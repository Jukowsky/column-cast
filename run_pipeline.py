"""
Daily pipeline: scrape columns -> TTS -> MP4 -> website -> YouTube.

ElevenLabs columns are also voiced with EMA Lightning (ema_compare.py) into
articles/compare/ema/ - archived, never published - so the two can be compared.

Every stage is idempotent against state.json, so this is safe to run on a timer:
a column is never re-voiced, re-rendered or re-published.

Uploading is opt-in via --upload, because it costs YouTube quota (1600 units per
video out of 10,000/day) and publishes to a real channel. Without it the script
just reports what is waiting.

Usage:
    python run_pipeline.py
    python run_pipeline.py --today                        # only columns published today
    python run_pipeline.py --today --upload               # full daily job (private)
    python run_pipeline.py --today --upload --privacy public --retain-days 30
    python run_pipeline.py --limit 3 --skip-website
"""

import argparse
import subprocess
import sys

import sol_state


# A step reports one of three outcomes. The middle one exists because a single
# unusable column must not cost the day its publication: exit 2 means some items
# failed while others succeeded, and those failures are already recorded in the
# ledger for the next run to retry.
OK, PARTIAL, FAILED = 0, 2, 1


def run(step, command):
    print("\n" + "=" * 70)
    print(f">> {step}")
    print("=" * 70)
    code = subprocess.run([sys.executable] + command).returncode
    if code == OK:
        return OK
    if code == PARTIAL:
        print(f"\n!  {step}: some items failed, the rest succeeded - continuing")
        return PARTIAL
    print(f"\n!! {step} FAILED (exit {code})")
    return FAILED


def check_dependencies():
    required = {
        'requests': 'pip install requests',
        'curl_cffi': 'pip install curl_cffi',
        'bs4': 'pip install beautifulsoup4',
        'edge_tts': 'pip install edge-tts',
        'PIL': 'pip install pillow',
        'imageio_ffmpeg': 'pip install imageio-ffmpeg',
        'googleapiclient': 'pip install google-api-python-client google-auth-oauthlib',
    }
    missing = []
    for module, install in required.items():
        try:
            __import__(module)
        except ImportError:
            missing.append((module, install))

    if missing:
        print("Missing dependencies:")
        for module, install in missing:
            print(f"   {module:<16} {install}")
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description="soL column podcast pipeline")
    parser.add_argument('--limit', type=int, default=10, help='max NEW columns per run')
    parser.add_argument('--pages', type=int, default=1, help='listing pages to scan')
    parser.add_argument('--today', action='store_true', help='only columns published today')
    parser.add_argument('--since-days', type=int, metavar='N',
                        help='only columns published in the last N days')
    parser.add_argument('--engine', default='auto',
                        choices=['auto', 'chirp3', 'elevenlabs', 'edge-tts', 'gtts', 'pyttsx3'],
                        help="'auto' spends the ElevenLabs monthly budget first, "
                             "then falls back to Google")
    parser.add_argument('--skip-website', action='store_true')
    parser.add_argument('--skip-ema', action='store_true',
                        help='do not render the EMA Lightning comparison copy')
    parser.add_argument('--upload', action='store_true', help="publish pending columns to YouTube")
    parser.add_argument('--privacy', default='private', choices=['private', 'unlisted', 'public'])
    parser.add_argument('--upload-limit', type=int, default=6,
                        help='max uploads per run; the default keeps one run inside the daily quota')
    parser.add_argument('--skip-archive', action='store_true',
                        help='do not push audio/text/metadata to MinIO')
    parser.add_argument('--retain-days', type=int, default=0,
                        help='keep local mp3/mp4 for N days after upload, then delete '
                             '(0 = keep forever)')
    args = parser.parse_args()

    if not check_dependencies():
        return 1

    scrape = ['sol_column_scraper.py', '--limit', str(args.limit), '--pages', str(args.pages)]
    if args.since_days is not None:
        scrape += ['--since-days', str(args.since_days)]
    elif args.today:
        scrape.append('--today')

    # (name, command, critical). A failed upload must not stop the archive from
    # running: quota limits and channel mismatches are expected, recoverable
    # conditions, and the columns still need to reach MinIO tonight.
    steps = [
        ("Column scrape", scrape, True),
        ("Turkish TTS", ['tts_converter.py', '--engine', args.engine], True),
        ("MP4 render", ['mp3_to_video.py'], True),
    ]
    if not args.skip_website:
        steps.append(("Website", ['generate_website.py'], False))
    if args.upload:
        steps.append(("YouTube upload", ['youtube_uploader.py', 'upload',
                                         '--privacy', args.privacy,
                                         '--limit', str(args.upload_limit)], False))
    if not args.skip_archive:
        steps.append(("MinIO archive", ['sol_storage.py', 'sync'], True))
    if args.retain_days > 0:
        steps.append((f"Media cleanup (>{args.retain_days} days)",
                      ['podcast.py', 'prune', '--days', str(args.retain_days)], False))
    # Last, and not critical: the copy is never published. On the one-core
    # server a column takes minutes, and if the run hits systemd's timeout it
    # should cut this off, not the upload. The next night's sync archives it.
    if not args.skip_ema:
        steps.append(("EMA comparison copy", ['ema_compare.py'], False))

    failed, partial = [], []
    for i, (step, command, critical) in enumerate(steps, 1):
        outcome = run(f"{i}/{len(steps)}  {step}", command)
        if outcome == OK:
            continue
        if outcome == PARTIAL:
            # Never fatal, even for a critical step: what succeeded should go
            # on to be rendered, published and archived.
            partial.append(step)
            continue
        if critical:
            print(f"\n!! critical step failed, stopping: {step}")
            return 1
        failed.append(step)
        print("   (not critical, continuing)")

    state = sol_state.load()
    waiting = sol_state.pending(state, 'uploaded', requires='video')

    print("\n" + "=" * 70)
    print("PIPELINE OK")
    print("=" * 70)
    print(f"  ledger: {len(state['articles'])} column(s)")
    print(f"  awaiting YouTube upload: {len(waiting)}")
    for entry in waiting:
        print(f"     - {entry['id']}  {entry.get('title', '')[:55]}")
    if partial:
        print(f"  PARTIALLY COMPLETED: {', '.join(partial)} "
              f"(failed items are retried on the next run)")
    if failed:
        print(f"  FAILED STEPS: {', '.join(failed)}")
    if waiting and not args.upload:
        print("\n  To upload:   python run_pipeline.py --upload")
        print("  List only:   python youtube_uploader.py upload --dry-run")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
