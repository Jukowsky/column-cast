"""
MP3 -> MP4 converter for YouTube upload.

Creates a static title-card video from a column's audio file. YouTube does not
accept bare audio, so each MP3 is muxed with a generated 1280x720 title card
into an H.264/AAC MP4.

Driven by state.json: only columns whose 'video' stage is empty are rendered.

Usage:
    python mp3_to_video.py                # render every voiced column that lacks a video
    python mp3_to_video.py --force 413293 # re-render one article id
"""

import argparse
import os
import re
import subprocess
import sys
import textwrap

from imageio_ffmpeg import get_ffmpeg_exe
from PIL import Image, ImageDraw, ImageFont

import sol_state

WIDTH, HEIGHT = 1280, 720
BG = (18, 22, 30)
ACCENT = (198, 40, 40)
FG = (240, 240, 240)
MUTED = (150, 158, 170)

# Windows first (dev box), then the usual Linux server fonts. All of these carry
# the Turkish glyphs (ı İ ş ğ ç ö ü); PIL's built-in fallback does not, and is a
# fixed-size bitmap that ignores `size`, so a title card drawn with it is
# unusable - hence the hard failure below rather than a silent downgrade.
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]

FONT_MISSING_HINT = ("No usable TrueType font found. On Debian/Ubuntu install one with:\n"
                     "    sudo apt-get install -y fonts-dejavu-core")


def find_font_path():
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def load_font(size):
    path = find_font_path()
    if not path:
        raise RuntimeError(FONT_MISSING_HINT)
    return ImageFont.truetype(path, size)


def make_title_card(title, byline, output_path):
    """Render a 1280x720 title card PNG for the column."""
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # Accent bar down the left edge
    draw.rectangle([0, 0, 12, HEIGHT], fill=ACCENT)
    draw.text((70, 80), "soL KÖŞE YAZILARI  •  SESLİ", font=load_font(28), fill=ACCENT)

    # Wrap the title, shrinking the font until it fits the available height
    for size, width_chars in ((58, 34), (48, 42), (40, 50), (34, 60)):
        font = load_font(size)
        lines = textwrap.wrap(title, width=width_chars)
        if len(lines) * (size + 16) <= 340:
            break

    y = 180
    for line in lines:
        draw.text((70, y), line, font=font, fill=FG)
        y += size + 16

    draw.text((70, HEIGHT - 110), byline, font=load_font(26), fill=MUTED)
    img.save(output_path)
    return output_path


def probe_duration(audio_path):
    """Return the audio's duration in seconds, or None if it can't be read."""
    result = subprocess.run(
        [get_ffmpeg_exe(), "-i", audio_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    match = re.search(r"Duration: (\d+):(\d\d):(\d\d\.\d+)", result.stderr)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def convert(audio_path, image_path, output_path):
    """Mux a still image and an MP3 into an MP4 that YouTube accepts.

    The image input loops forever, so `-shortest` alone overshoots the audio by
    however many frames x264 holds in its lookahead queue. Pin the output length
    to the probed audio duration instead.
    """
    cmd = [
        get_ffmpeg_exe(),
        "-y",
        "-loop", "1", "-framerate", "2", "-i", image_path,
        "-i", audio_path,
        "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
        "-vf", f"scale={WIDTH}:{HEIGHT}",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart",
    ]
    duration = probe_duration(audio_path)
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd.append(output_path)
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        print(result.stderr[-2000:])
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description="Convert column audio to YouTube-ready MP4")
    parser.add_argument("--dir", default="articles")
    parser.add_argument("--force", metavar="ARTICLE_ID", help="re-render this article id")
    args = parser.parse_args()

    state = sol_state.load()
    if args.force:
        sol_state.mark(state, args.force, 'video', None)

    todo = sol_state.pending(state, 'video', requires='tts')
    video_dir = os.path.join(args.dir, "video")
    os.makedirs(video_dir, exist_ok=True)

    print("=" * 70)
    print(f"MP4 render: {len(todo)} pending")
    print("=" * 70)

    ok = 0
    for entry in todo:
        audio_path = os.path.join(args.dir, entry.get("audio_file", ""))
        if not entry.get("audio_file") or not os.path.exists(audio_path):
            print(f"  ! audio missing for {entry['id']}")
            continue

        print(f"  {entry['id']}  {entry['title'][:55]}")
        image_rel = f"video/{entry['filename']}.png"
        byline = entry.get("author") or "soL Haber"
        make_title_card(entry["title"], byline, os.path.join(args.dir, image_rel))

        video_rel = f"video/{entry['filename']}.mp4"
        if convert(audio_path, os.path.join(args.dir, image_rel), os.path.join(args.dir, video_rel)):
            size_mb = os.path.getsize(os.path.join(args.dir, video_rel)) / (1024 * 1024)
            entry["video_file"] = video_rel
            entry["thumbnail_file"] = image_rel
            sol_state.mark(state, entry["id"], "video")
            sol_state.save(state)
            ok += 1
            print(f"      ok  {video_rel} ({size_mb:.1f} MB)")
        else:
            print("      ! ffmpeg failed")

    sol_state.export_index(state, args.dir)
    print(f"\n{'=' * 70}\n  {ok}/{len(todo)} rendered\n{'=' * 70}")
    # 2 signals "some items failed"; the pipeline carries on with the rest.
    return 0 if ok == len(todo) else 2


if __name__ == "__main__":
    sys.exit(main())
