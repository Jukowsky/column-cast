"""
Builds the YouTube title / description / tags for one column.

Kept separate from the uploader so the metadata can be reviewed (and unit
tested) without touching the YouTube account.
"""

import re

TITLE_LIMIT = 100          # YouTube hard limit
DESCRIPTION_LIMIT = 5000   # YouTube hard limit

# Title shape: "<Author> - <Column title> - <D Month YYYY>".
#
# The author leads because that is what people search by name, and it is the
# most distinctive token on the channel; the column title carries the topical
# keywords; the date closes it, which keeps every episode unique and signals
# freshness without competing for the first words.
#
# Sized against the corpus: author is at most 19 characters, the longest date
# renders as 15 ("30 Ağustos 2026"), and two separators take 6. That leaves
# exactly 60 for the title inside YouTube's 100-character limit - and the
# median column title is 32, so most are never shortened at all.
MAX_COLUMN_TITLE = 60
ELLIPSIS = "..."

TR_MONTHS = ['', 'Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz',
             'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık']

BASE_TAGS = ["soL", "köşe yazısı", "sesli makale", "Türkiye", "gündem", "podcast"]


def format_date(published):
    """'2026-09-10T00:00:00' -> '10 Eylül 2026'."""
    if not published:
        return ''
    year, month, day = published[:4], published[5:7], published[8:10]
    try:
        return f"{int(day)} {TR_MONTHS[int(month)]} {year}"
    except (ValueError, IndexError):
        return ''


def shorten(text, limit):
    """Trim to `limit`, cutting at a word boundary so the ellipsis reads well."""
    if len(text) <= limit:
        return text
    cut = text[:limit - len(ELLIPSIS)].rstrip()
    space = cut.rfind(' ')
    # Only honour the word boundary if it does not throw away half the title.
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(' ,;:–-') + ELLIPSIS


def _slug_words(slug):
    return [w for w in re.split(r'[-_]', slug or '') if len(w) > 3]


def build_title(entry):
    """'Aydemir Güler - Komünizm Cumhuriyetin neresindeydi? - 29 Ağustos 2026'."""
    title = entry.get('title', '').strip()
    author = entry.get('author', '').strip()
    date = format_date(entry.get('published'))

    parts = [p for p in (author, None, date) if p is not None]
    fixed = sum(len(p) for p in parts if p) + 3 * (len([p for p in parts if p]))

    # The cap is fixed at 60 for predictability, but never at the cost of
    # overrunning YouTube's limit if an author name or date is unusually long.
    budget = min(MAX_COLUMN_TITLE, TITLE_LIMIT - fixed)
    headline = shorten(title, max(budget, 20))

    return ' - '.join(p for p in (author, headline, date) if p)


def build_description(entry):
    parts = []
    if entry.get('spot'):
        parts.append(entry['spot'])

    byline = []
    if entry.get('author'):
        byline.append(f"Yazar: {entry['author']}")
    if entry.get('published'):
        day, month, year = entry['published'][8:10], entry['published'][5:7], entry['published'][:4]
        byline.append(f"Yayın tarihi: {day}.{month}.{year}")
    if byline:
        parts.append("\n".join(byline))

    parts.append(
        "Bu kayıt, haber.sol.org.tr'de yayımlanan köşe yazısının yapay ses (TTS) ile "
        "seslendirilmiş halidir. Yazının tüm hakları yazarına aittir."
    )
    if entry.get('url'):
        parts.append(f"Yazının tamamı: {entry['url']}")

    return "\n\n".join(parts)[:DESCRIPTION_LIMIT]


def build_tags(entry):
    tags = list(BASE_TAGS)
    if entry.get('author'):
        tags.insert(0, entry['author'])
    tags += _slug_words(entry.get('title_slug'))[:6]

    seen, out = set(), []
    for tag in tags:
        key = tag.lower()
        if key not in seen:
            seen.add(key)
            out.append(tag)
    return out[:15]


def build(entry):
    return {
        'title': build_title(entry),
        'description': build_description(entry),
        'tags': build_tags(entry),
        'category_id': '25',            # News & Politics
        'privacy_status': 'public',
        'language_code': 'tr',
        'default_language': 'tr',
        'default_audio_language': 'tr',
        'made_for_kids': 'false',
    }
