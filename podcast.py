"""
Inspection and manual-repair surface for the pipeline ledger.

Uploading is youtube_uploader.py's job; this is for looking at state.json and
for the one case the uploader cannot fix itself - an upload that reached YouTube
but died before the ledger was written, which would otherwise be published twice.

    python podcast.py status                       # one line per column, all stages
    python podcast.py pending                      # JSON view of what awaits upload
    python podcast.py mark-uploaded 413293 dQw4w9  # record an upload the script missed
"""

import argparse
import json
import os
import sys
from datetime import datetime

import sol_state
import youtube_meta


def cmd_status(args):
    state = sol_state.load()
    rows = sorted(state['articles'].values(),
                  key=lambda e: (e.get('published') or ''), reverse=True)
    if not rows:
        print("ledger empty - run: python sol_column_scraper.py")
        return 0

    print(f"{'id':<8} {'scr':<4}{'tts':<4}{'vid':<4}{'yt':<4}{'arc':<4} {'date':<11} author / title")
    print("-" * 100)
    for e in rows:
        flags = "".join("  X " if e.get('stages', {}).get(s) else "  . "
                        for s in sol_state.STAGES)
        published = (e.get('published') or '')[:10]
        print(f"{e['id']:<8} {flags} {published:<11} {e.get('author', '')} / {e.get('title', '')[:45]}")

    counts = {s: sum(1 for e in rows if e.get('stages', {}).get(s)) for s in sol_state.STAGES}
    print("-" * 100)
    print(f"total {len(rows)} | " + " | ".join(f"{s}: {counts[s]}" for s in sol_state.STAGES))
    return 0


def cmd_pending(args):
    """Emit the columns that are rendered but not yet on YouTube."""
    state = sol_state.load()
    todo = sol_state.pending(state, 'uploaded', requires='video')

    items = []
    for entry in todo:
        video_path = os.path.abspath(os.path.join(args.dir, entry['video_file']))
        if not os.path.exists(video_path):
            continue
        item = {
            'article_id': entry['id'],
            'source_url': entry.get('url'),
            'video_path': video_path,
            'thumbnail_path': os.path.abspath(os.path.join(args.dir, entry['thumbnail_file']))
            if entry.get('thumbnail_file') else None,
            'video_size_mb': round(os.path.getsize(video_path) / (1024 * 1024), 1),
        }
        item.update(youtube_meta.build(entry))
        items.append(item)

    print(json.dumps(items, ensure_ascii=False, indent=2))
    return 0


def cmd_mark_uploaded(args):
    state = sol_state.load()
    entry = state['articles'].get(args.article_id)
    if not entry:
        print(f"unknown article id: {args.article_id}", file=sys.stderr)
        return 1

    entry['youtube_video_id'] = args.video_id
    entry['youtube_url'] = f"https://www.youtube.com/watch?v={args.video_id}"
    sol_state.mark(state, args.article_id, 'uploaded')
    sol_state.save(state)
    sol_state.export_index(state, args.dir)
    print(f"{args.article_id} -> {entry['youtube_url']}")
    return 0


MEDIA_KEYS = ('audio_file', 'video_file', 'thumbnail_file')


def _media_age_days(entry):
    """Days since the audio was produced, or None if that is unknown."""
    stamp = (entry.get('stages', {}).get('tts')
             or entry.get('stages', {}).get('uploaded')
             or entry.get('published'))
    if not stamp:
        return None
    try:
        return (datetime.now() - datetime.fromisoformat(stamp)).total_seconds() / 86400
    except ValueError:
        return None


def cmd_prune(args):
    """Delete local media older than --days, but only once YouTube holds it.

    The retention window is the whole point of keeping the files at all: they
    stay on disk for a month so they can be re-checked or re-published without
    paying for TTS again, then go. Anything not yet uploaded is never touched,
    however old it is - deleting that would silently drop a column.
    """
    state = sol_state.load()
    freed = kept = skipped = 0
    removed_rows = []

    for entry in state['articles'].values():
        if entry.get('media_cleaned'):
            continue
        if not any(entry.get(k) for k in MEDIA_KEYS):
            continue

        # Two conditions, both required: YouTube has the video, and MinIO has
        # the audio. Deleting media that exists nowhere else would be the one
        # unrecoverable mistake this pipeline can make.
        if not entry.get('stages', {}).get('uploaded'):
            skipped += 1
            continue
        if not entry.get('stages', {}).get('stored'):
            skipped += 1
            continue

        age = _media_age_days(entry)
        if age is None or age < args.days:
            kept += 1
            continue

        size = 0
        for key in MEDIA_KEYS:
            rel = entry.get(key)
            if not rel:
                continue
            path = os.path.join(args.dir, rel)
            if os.path.exists(path):
                size += os.path.getsize(path)
                if not args.dry_run:
                    os.remove(path)
        if size:
            removed_rows.append((entry['id'], age, size, entry.get('title', '')[:44]))
            freed += size
            if not args.dry_run:
                entry['media_cleaned'] = True

    verb = "to delete" if args.dry_run else "deleted"
    print(f"retention window: {args.days} days")
    for aid, age, size, title in sorted(removed_rows, key=lambda r: -r[1]):
        print(f"  {aid}  {age:5.1f} days  {size / (1024 * 1024):6.1f} MB  {title}")
    print(f"\n  {len(removed_rows)} column(s) {verb}, {freed / (1024 * 1024):.1f} MB")
    print(f"  {kept} column(s) kept within the window, {skipped} not yet uploaded/archived (left alone)")

    if not args.dry_run and removed_rows:
        sol_state.save(state)
        sol_state.export_index(state, args.dir)
    return 0


# Google Cloud TTS list price and monthly free allowance, per 1M characters.
# Check the console before trusting these for a real invoice - they are here to
# show the trend and the free-tier headroom, not to replace billing.
TTS_PRICE_PER_M = {'chirp3': 30.0, 'wavenet': 16.0, 'standard': 4.0}
TTS_FREE_CHARS = 1_000_000
YOUTUBE_UNITS_PER_UPLOAD = 1600
YOUTUBE_DAILY_QUOTA = 10_000


def cmd_cost(args):
    """Characters billed and YouTube quota spent, grouped by month.

    Every synthesis records tts_billed_chars, so this is what was actually sent
    to Google, not an estimate from the text on disk - which would understate
    the bill whenever a column was re-voiced.
    """
    state = sol_state.load()
    months = {}
    for entry in state['articles'].values():
        stamp = entry.get('stages', {}).get('tts') or entry.get('published') or ''
        month = stamp[:7] or 'unknown'
        row = months.setdefault(month, {'chars': 0, 'columns': 0, 'uploads': 0, 'voices': set()})
        chars = entry.get('tts_billed_chars')
        if chars:
            row['chars'] += chars
            row['columns'] += 1
        if entry.get('tts_voice'):
            row['voices'].add(entry['tts_voice'].split('-')[-1])
        if entry.get('stages', {}).get('uploaded'):
            row['uploads'] += 1

    price = TTS_PRICE_PER_M.get(args.engine, TTS_PRICE_PER_M['chirp3'])
    print(f"TTS: {args.engine} @ ${price:.0f}/1M characters, {TTS_FREE_CHARS:,} free per month\n")
    print(f"{'month':<10}{'cols':>6}{'chars':>12}{'billable':>12}{'est. $':>11}"
          f"{'uploads':>9}{'YT units':>10}  voices")
    print("-" * 88)

    total_chars = total_cost = total_uploads = 0
    for month in sorted(months):
        row = months[month]
        billable = max(0, row['chars'] - TTS_FREE_CHARS)
        cost = billable / 1e6 * price
        total_chars += row['chars']
        total_cost += cost
        total_uploads += row['uploads']
        print(f"{month:<10}{row['columns']:>6}{row['chars']:>12,}{billable:>12,}"
              f"{cost:>11.2f}{row['uploads']:>9}{row['uploads'] * YOUTUBE_UNITS_PER_UPLOAD:>10,}"
              f"  {', '.join(sorted(row['voices'])) or '-'}")

    print("-" * 88)
    print(f"{'total':<10}{'':>6}{total_chars:>12,}{'':>12}{total_cost:>11.2f}"
          f"{total_uploads:>9}{total_uploads * YOUTUBE_UNITS_PER_UPLOAD:>10,}")

    import tts_converter
    el = tts_converter.spent_this_month(state, 'elevenlabs')
    cap = tts_converter.ELEVENLABS_MONTHLY_BUDGET
    print(f"\nElevenLabs budget (this month): {el:,} / {cap:,} characters "
          f"(%{el / cap * 100:.0f}), {max(0, cap - el):,} left")
    balance = tts_converter.elevenlabs_balance()
    if balance:
        print(f"  account itself  : {balance[0]:,} / {balance[1]:,} credits left - this decides")
    else:
        print("  account unreadable (key lacks user_read?) - the ledger above decides")
    print(f"  once the budget is spent, remaining columns are voiced with {tts_converter.FALLBACK_ENGINE}")

    current = months.get(datetime.now().strftime('%Y-%m'), {'chars': 0, 'uploads': 0})
    used = current['chars']
    print(f"\nThis month ({datetime.now():%Y-%m}):")
    print(f"  free tier       : {used:,} / {TTS_FREE_CHARS:,}  (%{used / TTS_FREE_CHARS * 100:.0f})")
    remaining = max(0, TTS_FREE_CHARS - used)
    print(f"  remaining       : {remaining:,} characters"
          + (f"  (~{remaining // 8000} columns)" if remaining else "  - now billable"))
    print(f"\nYouTube daily quota: {YOUTUBE_UNITS_PER_UPLOAD:,} units per upload, "
          f"{YOUTUBE_DAILY_QUOTA:,} per day -> {YOUTUBE_DAILY_QUOTA // YOUTUBE_UNITS_PER_UPLOAD} videos a day")
    print("\nActual bill: console.cloud.google.com > Billing > Reports "
          "(service: Cloud Text-to-Speech API)")
    return 0


def cmd_reconcile(args):
    """Clear stage flags whose output file has gone missing.

    The ledger claims a column has audio; the disk disagrees. That happens when
    the ledger is copied to a new machine without the media, or when a file is
    deleted by hand. Without this the pipeline skips the TTS step and then dies
    at the video step with a missing input.

    Deliberately narrow: only `tts` and `video` are backed by local files.
    `uploaded` lives on YouTube and `stored` lives in MinIO, so neither is ever
    cleared here. Nor is anything the retention prune removed on purpose -
    re-synthesising those would mean paying for the same text twice a month.
    """
    state = sol_state.load()
    cleared = 0
    for entry in state['articles'].values():
        if entry.get('media_cleaned'):
            continue                    # pruned on purpose, not lost

        for stage, key in (('tts', 'audio_file'), ('video', 'video_file')):
            if not entry.get('stages', {}).get(stage):
                continue
            rel = entry.get(key)
            if rel and os.path.exists(os.path.join(args.dir, rel)):
                continue
            sol_state.mark(state, entry['id'], stage, None)
            cleared += 1
            print(f"  {entry['id']}  {stage} cleared (file missing)  {entry.get('title', '')[:40]}")

    if cleared:
        sol_state.save(state)
        sol_state.export_index(state, args.dir)
    counts = {s: sum(1 for e in state['articles'].values() if e.get('stages', {}).get(s))
              for s in sol_state.STAGES}
    print(f"\n  {cleared} stage(s) cleared | " + " | ".join(f"{s}: {counts[s]}" for s in sol_state.STAGES))
    return 0


def cmd_reset_upload(args):
    """Clear the 'uploaded' stamp so a column is published again.

    Only legitimate use is a deliberate re-publish - the usual one being a change
    of target channel. The old YouTube id is kept under `previous_youtube` so the
    stale video can still be found and deleted by hand.
    """
    state = sol_state.load()
    targets = (list(state['articles'].values()) if args.all
               else [state['articles'][i] for i in args.article_ids if i in state['articles']])
    if not targets:
        print("no matching articles", file=sys.stderr)
        return 1

    regenerate = 0
    for entry in targets:
        if not entry.get('stages', {}).get('uploaded'):
            continue
        entry.setdefault('previous_youtube', []).append({
            'video_id': entry.get('youtube_video_id'),
            'url': entry.get('youtube_url'),
        })
        entry.pop('youtube_video_id', None)
        entry.pop('youtube_url', None)
        sol_state.mark(state, entry['id'], 'uploaded', None)

        # If --cleanup already deleted the media, the mp3/mp4 have to be rebuilt
        # from the transcript before there is anything to upload again.
        if entry.pop('media_cleaned', None):
            sol_state.mark(state, entry['id'], 'tts', None)
            sol_state.mark(state, entry['id'], 'video', None)
            regenerate += 1

        print(f"  {entry['id']}  {entry.get('title', '')[:50]}")

    sol_state.save(state)
    sol_state.export_index(state, args.dir)
    pending = len(sol_state.pending(state, 'uploaded', requires='video'))
    print(f"\nreset done. {pending} column(s) now awaiting upload"
          + (f", {regenerate} need TTS+MP4 rebuilt first" if regenerate else ""))
    print("The old videos are NOT deleted from YouTube - remove them by hand if you want them gone.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="SOL podcast pipeline control")
    parser.add_argument('--dir', default='articles')
    sub = parser.add_subparsers(dest='cmd', required=True)

    sub.add_parser('status', help='show every column and which stages it has cleared')
    sub.add_parser('pending', help='JSON worklist of columns awaiting YouTube upload')

    marker = sub.add_parser('mark-uploaded', help='record a completed YouTube upload')
    marker.add_argument('article_id')
    marker.add_argument('video_id')

    prune = sub.add_parser('prune',
                           help='delete local media older than N days that is already on YouTube')
    prune.add_argument('--days', type=int, default=30, help='retention window (default 30)')
    prune.add_argument('--dry-run', action='store_true', help='list what would be deleted')

    cost = sub.add_parser('cost', help='characters billed and quota spent, by month')
    cost.add_argument('--engine', default='chirp3', choices=['chirp3', 'wavenet', 'standard'])

    sub.add_parser('reconcile',
                   help='clear stage flags whose local output file is missing')

    reset = sub.add_parser('reset-upload',
                           help='clear the uploaded stamp so columns publish again '
                                '(use after switching target channel)')
    reset.add_argument('article_ids', nargs='*')
    reset.add_argument('--all', action='store_true', help='reset every uploaded column')

    args = parser.parse_args()
    return {'status': cmd_status, 'pending': cmd_pending,
            'mark-uploaded': cmd_mark_uploaded,
            'prune': cmd_prune,
            'reconcile': cmd_reconcile,
            'cost': cmd_cost,
            'reset-upload': cmd_reset_upload}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
