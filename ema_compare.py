"""
Voice the ElevenLabs columns a second time with EMA Lightning, for comparison.

EMA Lightning (huggingface.co/canberkkkkkk/ema-lightning) is a small Turkish-only
model that runs offline on the CPU, at roughly six times real time. Before it
could replace a paid voice it has to be heard on the same columns, so every
column ElevenLabs narrates is rendered again here from the same transcript.

The copy is never published: it lands in articles/compare/ema/ and, through
sol_storage.py, under compare/ema/ in MinIO - away from audio/, which is what
YouTube and the site are built from. Prune does not touch it either.

The model reads its own normalizer's output, which spells "MHP'nin" as "me he pe
kesme ne i ne"; the shared Turkish rules fix that, so it gets the same text
Chirp 3 does.

Without the ema-lightning package this step reports that and exits 0, so a
server that has not installed it yet keeps publishing.

Usage:
    python ema_compare.py                 # render what is waiting
    python ema_compare.py --limit 1
    python ema_compare.py --force 123456  # render this column again
"""

import argparse
import os
import subprocess
import sys
import tempfile
import time

import sol_state
from text_normalize import normalize_for_tts

COMPARE_DIR = "compare/ema"

# Same output as the published MP3s, so the two are compared on voice, not on
# encoding.
SAMPLE_RATE = 24000
MP3_BITRATE = "128k"

# Fixed, so rendering a column again gives the same audio.
EMA_SEED = 20261021

# A first run finds the whole ElevenLabs backlog waiting. The pipeline runs
# under a 45-minute systemd timeout on a one-core VPS, where a column can take
# around ten minutes. Two a night drains the backlog while ElevenLabs adds one
# a day; the step runs last, so a timeout cuts it off and nothing else.
DEFAULT_LIMIT = 2


def waiting(state):
    """ElevenLabs columns without an EMA copy, oldest first."""
    return [e for e in sol_state.pending(state, 'ema', requires='tts')
            if e.get('tts_engine') == 'elevenlabs']


def encode_mp3(wav_path, mp3_path):
    from imageio_ffmpeg import get_ffmpeg_exe

    result = subprocess.run(
        [get_ffmpeg_exe(), "-y", "-i", wav_path,
         "-c:a", "libmp3lame", "-b:a", MP3_BITRATE, "-ar", str(SAMPLE_RATE), "-ac", "1",
         mp3_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        print(result.stderr[-1500:])
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description='EMA Lightning copies of the ElevenLabs columns')
    parser.add_argument('--dir', default='articles')
    parser.add_argument('--limit', type=int, default=DEFAULT_LIMIT,
                        help='max columns per run (0 = all)')
    parser.add_argument('--force', metavar='ARTICLE_ID',
                        help='render this article id again even though it is already done')
    args = parser.parse_args()

    state = sol_state.load()
    if args.force:
        sol_state.mark(state, args.force, 'ema', None)

    todo = waiting(state)
    print("=" * 70)
    print("EMA Lightning comparison copy")
    print(f"  {len(todo)} ElevenLabs column(s) waiting")
    if args.limit and len(todo) > args.limit:
        print(f"  {args.limit} this run, the rest on later runs")
        todo = todo[:args.limit]
    print("=" * 70)
    if not todo:
        return 0

    try:
        from ema_lightning import EMA
    except ImportError:
        print("  ema-lightning not installed, skipping (see DEPLOY.md)")
        return 0

    started = time.perf_counter()
    tts = EMA()
    print(f"  model {time.perf_counter() - started:.1f} s to load")

    out_dir = os.path.join(args.dir, COMPARE_DIR)
    os.makedirs(out_dir, exist_ok=True)
    ok = 0
    for i, entry in enumerate(todo, 1):
        print(f"\n[{i}/{len(todo)}] {entry['title'][:60]}")
        txt_path = os.path.join(args.dir, entry.get('txt_file') or '')
        if not entry.get('txt_file') or not os.path.exists(txt_path):
            print(f"      ! text missing: {txt_path}")
            continue
        with open(txt_path, 'r', encoding='utf-8') as f:
            text = normalize_for_tts(f.read())

        rel = f"{COMPARE_DIR}/{entry['filename']}.mp3"
        path = os.path.join(args.dir, rel)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                wav = os.path.join(tmp, 'ema.wav')
                t = time.perf_counter()
                speech = tts.say(text, seed=EMA_SEED, sample_rate=SAMPLE_RATE, path=wav)
                took = time.perf_counter() - t
                if not encode_mp3(wav, path):
                    print("      ! mp3 encoding failed")
                    continue
        except Exception as exc:
            print(f"      ! EMA error: {exc}")
            continue

        entry['ema_audio_file'] = rel
        entry['ema_seconds'] = round(took, 1)
        entry['ema_audio_seconds'] = round(speech.duration, 1)
        sol_state.mark(state, entry['id'], 'ema')
        sol_state.save(state)
        ok += 1
        print(f"      ok  {rel}  {speech.duration:.0f}s of audio in {took:.0f}s "
              f"({speech.duration / took:.1f}x)")

    print(f"\n{'=' * 70}\n  {ok}/{len(todo)} EMA copies\n{'=' * 70}")
    return 0 if ok == len(todo) else 2


if __name__ == "__main__":
    sys.exit(main())
