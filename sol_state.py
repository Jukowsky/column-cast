"""
Shared state ledger for the soL column podcast pipeline.

Every article the pipeline has ever seen is recorded in state.json, keyed by the
numeric id that haber.sol.org.tr puts at the end of every column URL
(.../yazarlar/aydemir-guler/komunizm-cumhuriyetin-neresindeydi-413293 -> "413293").

That id is what makes the pipeline idempotent: TTS, video and YouTube upload each
check their own stage flag and skip anything already done, so re-running the
pipeline never re-synthesises or re-uploads a column.
"""

import json
import os
import re
from datetime import datetime

STATE_FILE = "state.json"
STAGES = ("scraped", "tts", "video", "uploaded", "stored")


def article_id(url):
    """Pull the trailing numeric id out of a sol article URL."""
    match = re.search(r'-(\d+)/?$', url.split('?')[0].rstrip('/'))
    return match.group(1) if match else None


def author_slug(url):
    """/yazarlar/<author-slug>/<title-slug>-<id> -> '<author-slug>'."""
    match = re.search(r'/yazarlar/([^/]+)/', url)
    return match.group(1) if match else 'unknown'


def title_slug(url):
    match = re.search(r'/yazarlar/[^/]+/(.+?)-\d+/?$', url.split('?')[0])
    return match.group(1) if match else 'column'


def load(path=STATE_FILE):
    if not os.path.exists(path):
        return {"version": 1, "articles": {}}
    with open(path, 'r', encoding='utf-8') as f:
        state = json.load(f)
    state.setdefault("version", 1)
    state.setdefault("articles", {})
    return state


def save(state, path=STATE_FILE):
    """Write atomically so an interrupted run can't leave a truncated ledger."""
    tmp = path + ".tmp"
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def known(state, aid):
    return aid in state["articles"]


def done(state, aid, stage):
    """True if this article has already cleared the given stage."""
    entry = state["articles"].get(aid)
    return bool(entry and entry.get("stages", {}).get(stage))


def mark(state, aid, stage, value=True):
    entry = state["articles"].setdefault(aid, {"id": aid, "stages": {}})
    entry.setdefault("stages", {})[stage] = datetime.now().isoformat(timespec='seconds') if value is True else value


def pending(state, stage, requires=None):
    """Articles that have cleared `requires` but not `stage`, oldest first."""
    out = []
    for aid, entry in state["articles"].items():
        if done(state, aid, stage):
            continue
        if requires and not done(state, aid, requires):
            continue
        out.append(entry)
    return sorted(out, key=lambda e: (e.get("published") or "", e.get("id", "")))


def base_filename(entry):
    """Stable, timestamp-free basename so re-runs overwrite instead of duplicating."""
    date = (entry.get("published") or "")[:10].replace("-", "") or "00000000"
    return f"{date}_{entry['author_slug']}_{entry['title_slug']}-{entry['id']}"


def export_index(state, articles_dir='articles'):
    """Emit the legacy articles/index.json that generate_website.py consumes."""
    rows = []
    for i, entry in enumerate(sorted(state["articles"].values(),
                                     key=lambda e: (e.get("published") or ""), reverse=True), 1):
        # With --cleanup the mp3/mp4 are deleted once YouTube holds them, so the
        # page must link to YouTube instead of rendering a dead audio player.
        cleaned = entry.get('media_cleaned')
        rows.append({
            'id': i,
            'article_id': entry['id'],
            'filename': entry.get('filename', ''),
            'title': entry.get('title', ''),
            'author': entry.get('author', ''),
            'date': entry.get('published_text', entry.get('published', '')),
            'url': entry.get('url', ''),
            'word_count': entry.get('word_count', 0),
            'txt_file': entry.get('txt_file', ''),
            'audio_file': '' if cleaned else entry.get('audio_file', ''),
            'audio_generated': bool(entry.get('stages', {}).get('tts')) and not cleaned,
            'video_file': '' if cleaned else entry.get('video_file', ''),
            'youtube_url': entry.get('youtube_url', ''),
        })
    os.makedirs(articles_dir, exist_ok=True)
    with open(os.path.join(articles_dir, 'index.json'), 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    return rows
