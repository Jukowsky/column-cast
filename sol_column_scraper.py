"""
soL column scraper.

Reads https://haber.sol.org.tr/kose-yazilari (columns only - no plain news) and
saves any column it has not seen before as a .txt file, registering it in
state.json. Columns already in the ledger are skipped without being fetched, so
a daily run only pays for what is genuinely new.

Plain HTTP, no browser: the listing and the article bodies are both server
rendered, so requests + BeautifulSoup get everything Playwright did. That keeps
the deployment down to a handful of pure-Python packages, which matters on a
small VPS.

Usage:
    python sol_column_scraper.py                 # newest 10 columns (page 1)
    python sol_column_scraper.py --limit 4       # at most 4 new columns
    python sol_column_scraper.py --pages 3       # look three listing pages deep
    python sol_column_scraper.py --today         # only columns published today
"""

import argparse
import os
import re
import sys
import time
from datetime import date

import requests
from bs4 import BeautifulSoup

import sol_state

BASE = "https://haber.sol.org.tr"
LISTING = BASE + "/kose-yazilari"

# The site serves a near-empty shell to non-browser user agents, so this is not
# cosmetic - without it the listing comes back with zero article links.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

COLUMN_RE = re.compile(r'^https?://haber\.sol\.org\.tr/yazarlar/[^/]+/.+-\d+$')

REQUEST_TIMEOUT = 30
POLITE_DELAY = 1.0      # seconds between requests; we are a guest on their server


HEADERS = {
    'User-Agent': UA,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'tr-TR,tr;q=0.9,en;q=0.8',
    'Upgrade-Insecure-Requests': '1',
}

# Cloudflare fronts the site and blocks plain `requests` with a 403 - not for
# the IP or the headers, but for the TLS handshake: the fingerprint does not
# look like a browser. curl_cffi replays Chrome's actual TLS/HTTP2 fingerprint
# and gets a 200 from the same machine. Verified on the VPS 2026-08-29: plain
# requests 403, curl_cffi impersonating Chrome 200.
IMPERSONATE = "chrome124"

try:
    from curl_cffi import requests as impersonating_requests
except ImportError:                                   # pragma: no cover
    impersonating_requests = None


def make_session():
    if impersonating_requests is not None:
        session = impersonating_requests.Session(impersonate=IMPERSONATE)
        session.headers.update(HEADERS)
        return session

    print("  ! curl_cffi missing, using plain requests - Cloudflare may answer 403")
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def fetch(session, url, retries=3):
    """GET with a short backoff - a daily cron should survive a blip."""
    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT)
            if response.status_code == 200:
                return response.text
            if response.status_code == 403:
                raise RuntimeError(
                    f"403 from {url} - Cloudflare refused the request. If this is a "
                    f"server, check that curl_cffi is installed (impersonation is "
                    f"what gets past it).")
            if 400 <= response.status_code < 500:
                raise RuntimeError(f"HTTP {response.status_code} for {url}")
            last = f"HTTP {response.status_code}"
        except RuntimeError:
            raise
        except Exception as exc:                      # transport-level failure
            last = exc.__class__.__name__

        if attempt == retries:
            raise RuntimeError(f"{url} failed after {retries} attempts ({last})")
        wait = 2 ** attempt
        print(f"       . {last}, retry {attempt} in {wait}s")
        time.sleep(wait)
    return None


def clean(text):
    return re.sub(r'\s+', ' ', text or '').strip()


def parse_published(text):
    """'Yayin Tarihi: 28.08.2026 , 21:41 ...' -> '2026-08-28T21:41:00'."""
    match = re.search(r'Yay[ıi]n Tarihi:\s*(\d{2})\.(\d{2})\.(\d{4})\s*,\s*(\d{2}):(\d{2})', text)
    if not match:
        return None
    day, month, year, hour, minute = match.groups()
    return f"{year}-{month}-{day}T{hour}:{minute}:00"


TR_MONTHS = {'Ocak': 1, 'Şubat': 2, 'Mart': 3, 'Nisan': 4, 'Mayıs': 5, 'Haziran': 6,
             'Temmuz': 7, 'Ağustos': 8, 'Eylül': 9, 'Ekim': 10, 'Kasım': 11, 'Aralık': 12}
_LISTING_DATE = re.compile(r'(\d{1,2})\s+(' + '|'.join(TR_MONTHS) + r')\s+(\d{4})')


def collect_column_urls(session, pages=1):
    """Return (url, listing_date) pairs from the listing, newest first.

    The listing date is the one that matters. sol posts a column late in the
    evening but dates it for the following day: the article page shows
    "Yayın Tarihi: 29.08.2026 , 21:44" while the listing - and the site's own
    presentation of the piece - calls it 30 Ağustos. Taking the article
    timestamp put every column, and every daily playlist, one day early.
    """
    pairs = []
    seen = set()
    for n in range(1, pages + 1):
        url = LISTING if n == 1 else f"{LISTING}?page={n}"
        print(f"  listing: {url}")
        soup = BeautifulSoup(fetch(session, url), 'html.parser')
        time.sleep(POLITE_DELAY)

        for item in soup.select('div.item'):
            link = item.select_one('a[href]')
            if not link:
                continue
            href = (link.get('href') or '').split('?')[0].rstrip('/')
            if href.startswith('/'):
                href = BASE + href
            if not COLUMN_RE.match(href) or href in seen:
                continue
            seen.add(href)
            match = _LISTING_DATE.search(item.get_text())
            day = (f"{match.group(3)}-{TR_MONTHS[match.group(2)]:02d}-{int(match.group(1)):02d}"
                   if match else None)
            pairs.append((href, day))
    return pairs


def extract_column(session, url):
    """Pull one column's author, title, spot, publish date and body."""
    soup = BeautifulSoup(fetch(session, url), 'html.parser')
    time.sleep(POLITE_DELAY)

    article = soup.select_one('div.article--yazar') or soup.select_one('div.article')
    if not article:
        return None

    title_el = article.select_one('h1.node-title') or article.select_one('h1')
    body_el = article.select_one('div.node-body')
    if not title_el or not body_el:
        return None

    paragraphs = [clean(p.get_text()) for p in body_el.find_all('p')]
    paragraphs = [p for p in paragraphs if len(p) > 30]
    if not paragraphs:
        return None

    # div.reporter repeats the name (image alt, then the label); collapse to one.
    reporter = article.select_one('div.reporter')
    names = [clean(t) for t in reporter.stripped_strings] if reporter else []
    author = names[0] if names else ''

    date_text = ''
    for p in article.find_all('p'):
        if 'Tarihi' in p.get_text():
            date_text = clean(p.get_text())
            break

    spot_el = article.select_one('div.node-spot')
    full_text = '\n\n'.join(paragraphs)
    return {
        'id': sol_state.article_id(url),
        'url': url,
        'author': author,
        'author_slug': sol_state.author_slug(url),
        'title_slug': sol_state.title_slug(url),
        'title': clean(title_el.get_text()),
        'spot': clean(spot_el.get_text()) if spot_el else '',
        'published': parse_published(date_text),
        'published_text': date_text,
        'word_count': len(full_text.split()),
        'full_text': full_text,
    }


def write_txt(entry, full_text, articles_dir):
    """Write the TTS input: spoken prose only, no URLs or separator rules.

    Stored verbatim. Normalisation happens at synthesis time instead, because
    it is lossy: once "TKP" has been rewritten to its spoken form the original
    token is gone, and a later correction to the pronunciation table can never
    be applied to it. Keeping the source intact means every transcript can be
    re-voiced with improved rules.

    To see what will actually be spoken:
        python text_normalize.py articles/txt/<file>.txt
    """
    txt_dir = os.path.join(articles_dir, 'txt')
    os.makedirs(txt_dir, exist_ok=True)
    path = os.path.join(txt_dir, entry['filename'] + '.txt')

    title = entry['title']
    parts = [title if title.endswith(('.', '!', '?', '…')) else title + '.']
    if entry.get('author'):
        # Written with the proper Turkish letters: this line is spoken, and the
        # engine pronounces "kose yazisi" as the ASCII string it sees.
        parts.append(entry['author'] + " imzalı köşe yazısı.")
    if entry.get('spot'):
        parts.append(entry['spot'])
    parts.append(full_text)

    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n\n'.join(parts))
    return f"txt/{entry['filename']}.txt"


def within_window(entry, args):
    """Is this column recent enough to process on this run?"""
    if args.since_days is None and not args.today:
        return True

    published = (entry.get('published') or '')[:10]
    if not published:
        return False                      # undated: only taken on an unfiltered run

    days = 0 if args.today and args.since_days is None else (args.since_days or 0)
    try:
        age = (date.today() - date.fromisoformat(published)).days
    except ValueError:
        return False
    return 0 <= age <= days


def main():
    parser = argparse.ArgumentParser(description="Scrape new sol.org.tr columns")
    parser.add_argument('--dir', default='articles')
    parser.add_argument('--limit', type=int, default=10, help='max NEW columns to fetch (default 10)')
    parser.add_argument('--pages', type=int, default=1, help='listing pages to scan (default 1)')
    parser.add_argument('--refetch', action='store_true',
                        help='re-download the transcript of columns already in the '
                             'ledger, overwriting the .txt but keeping every stage '
                             'flag - use after the extraction rules change')
    parser.add_argument('--today', action='store_true', help='keep only columns published today')
    parser.add_argument('--since-days', type=int, metavar='N',
                        help='keep columns published within the last N days '
                             '(0 = today only). Use this rather than --today for a '
                             'morning run: at 06:00 the current day has no columns yet, '
                             'so --today would silently process nothing.')
    args = parser.parse_args()

    state = sol_state.load()
    print("=" * 70)
    print("soL column scraper")
    print(f"  ledger: {len(state['articles'])} column(s) already known")
    print("=" * 70)

    added = skipped = refreshed = 0
    session = make_session()

    urls = collect_column_urls(session, args.pages)
    print(f"  found {len(urls)} column link(s)\n")

    for url, listing_day in urls:
        if added >= args.limit:
            break
        aid = sol_state.article_id(url)
        if not aid:
            continue
        if sol_state.known(state, aid) and not args.refetch:
            skipped += 1
            continue
        # --refetch is a refresh, not a crawl: it must not enlarge the ledger.
        # Counting a refreshed column as "added" once pulled nine unrelated
        # back-catalogue pieces into the pipeline.
        if args.refetch and not sol_state.known(state, aid):
            skipped += 1
            continue

        print(f"  NEW  {url}")
        try:
            entry = extract_column(session, url)
        except Exception as exc:
            print(f"       ! fetch failed: {exc}")
            continue
        if not entry:
            print("       ! no body found, skipped")
            continue

        if not within_window(entry, args):
            print(f"       - out of window ({(entry.get('published') or 'unknown')[:10]}), skipped")
            continue

        # The listing date is authoritative; the article timestamp is kept for
        # reference because it is the only record of when the piece went up.
        entry['posted_at'] = entry['published']
        if listing_day:
            entry['published'] = listing_day + 'T00:00:00'

        full_text = entry.pop('full_text')
        # Never recompute the basename for a column already in the ledger: it
        # encodes the date, and changing it would orphan the audio and video
        # files the rest of the pipeline has already produced.
        known_entry = state['articles'].get(aid)
        entry['filename'] = (known_entry or {}).get('filename') or sol_state.base_filename(entry)
        entry['txt_file'] = write_txt(entry, full_text, args.dir)

        if sol_state.known(state, aid):
            # Refetch: refresh the text, keep everything the pipeline has done.
            state['articles'][aid].update(
                {k: v for k, v in entry.items() if k != 'stages'})
            refreshed += 1
        else:
            entry['stages'] = {}
            state['articles'][aid] = entry
            sol_state.mark(state, aid, 'scraped')
            added += 1
        sol_state.save(state)
        print(f"       + {entry['author']} | {entry['word_count']} words -> {entry['txt_file']}")

    sol_state.export_index(state, args.dir)
    print("\n" + "=" * 70)
    summary = f"  {added} new"
    if refreshed:
        summary += f", {refreshed} refreshed"
    print(f"{summary}, {skipped} skipped, {len(state['articles'])} column(s) in the ledger")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
