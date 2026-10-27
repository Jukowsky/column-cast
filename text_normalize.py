"""
Normalises scraped Turkish prose before it is handed to the TTS engine.

Only the spoken text is touched. Titles, descriptions and the title card keep
their original punctuation - this is about what the synthesiser reads, not about
what a reader sees.

The main problem is the apostrophe. Turkish separates case suffixes from proper
nouns with one ("Cumhuriyet'in", "TKP'nin", "1920'de") and the corpus is full of
them: 306 letter-apostrophe-letter and 14 digit-apostrophe-letter occurrences in
the first eleven columns. Chirp 3 mishandles them, breaking the word in two.
Deleting the apostrophe restores exactly how the word is pronounced -
"Cumhuriyetin" - so this is a repair, not an approximation.

Notably absent from the corpus: apostrophes used as quotation marks. Every
single occurrence sits between two word characters, so joining is unambiguous.
"""

import os
import re
import unicodedata

import tr_readings

# Every apostrophe-like character the site actually emits, plus the usual
# lookalikes. U+2019 dominates (286 of 307); U+0027 accounts for the rest.
APOSTROPHES = "’'ʼ´‘ʹ՚＇"

# Zero-width and invisible characters. The corpus carries U+2060 WORD JOINER,
# which is silent on screen but can split a token for the synthesiser.
INVISIBLE = "​‌‍⁠﻿­"

_APOS_BETWEEN_WORDS = re.compile(rf"(?<=[^\W_])[{APOSTROPHES}](?=[^\W_])")
_APOS_ANY = re.compile(rf"[{APOSTROPHES}]")
_INVISIBLE = re.compile(f"[{INVISIBLE}]")
_QUOTES = re.compile("[“”«»„‟]")
_DASHES = re.compile(r"\s*[–—−]\s*")
_SPACES = re.compile("[     ]")


def normalize_for_tts(text, unknown=None, spelled=None):
    """Return `text` rewritten into what the engine should actually say.

    `spelled` is an engine-specific initialism table; None means the default.
    """
    if not text:
        return text

    # NFC first: a combining sequence can hide an apostrophe from the patterns.
    text = unicodedata.normalize("NFC", text)
    text = _INVISIBLE.sub("", text)
    text = _SPACES.sub(" ", text)

    # Before the apostrophes go. "TKP'nin" still has a word boundary after the
    # P, so the initialism table matches; strip the apostrophe first and it
    # becomes "TKPnin", which matches nothing.
    text = tr_readings.apply_all(text, unknown, spelled)

    # The whole point: join the proper noun to its suffix.
    text = _APOS_BETWEEN_WORDS.sub("", text)

    # Anything left is decorative (an opening or closing quote). It is silent
    # either way, so drop it rather than leave the engine to guess.
    text = _APOS_ANY.sub("", text)

    # Curly quotes are not pronounced but do influence phrasing; make them
    # ordinary. A parenthetical dash becomes a comma: rendering it as " - "
    # invites the engine to say "eksi", while a comma gives the pause the dash
    # was there for in the first place.
    text = _QUOTES.sub('"', text)
    text = _DASHES.sub(", ", text)
    # A dash next to other punctuation, or opening a line, leaves a comma with
    # nothing to separate: "dedi, , ama" or a line starting ", ama".
    text = re.sub(r",\s*([,.;:!?])", r"\1", text)
    text = re.sub(r"(?m)^,\s*", "", text)

    # Collapse whatever whitespace the substitutions left behind, but keep
    # paragraph breaks: they are what the chunker splits on.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def diff_report(original, normalized, limit=12):
    """Human-readable list of the tokens this changed, for spot-checking."""
    before = re.findall(rf"\S*[{APOSTROPHES}{INVISIBLE}]\S*", original)
    seen, rows = set(), []
    for token in before:
        if token in seen:
            continue
        seen.add(token)
        rows.append((token, normalize_for_tts(token)))
        if len(rows) >= limit:
            break
    return rows


def main():
    """CLI: preview a file, or rewrite a whole transcript directory in place."""
    import argparse
    import glob
    import sys

    parser = argparse.ArgumentParser(description="Normalise Turkish text for TTS")
    parser.add_argument('path', nargs='?', help='file to normalise (default: stdin)')
    # Preview only, deliberately. Writing the normalised text back over the
    # transcript is lossy - it destroys the original tokens, so a later
    # correction to the pronunciation tables can never reach them.
    parser.add_argument('--dir', metavar='GLOB',
                        help='preview what would change across a set of files, '
                             'e.g. --dir "articles/txt/*.txt"')
    args = parser.parse_args()

    if args.dir:
        files = sorted(glob.glob(args.dir))
        touched = total = 0
        for path in files:
            raw = open(path, encoding='utf-8').read()
            out = normalize_for_tts(raw)
            if out == raw:
                continue
            touched += 1
            changes = diff_report(raw, out, limit=4)
            total += len(re.findall(rf"[{APOSTROPHES}{INVISIBLE}]", raw))
            print(f"  {os.path.basename(path)[:58]:<60} "
                  + ", ".join(f"{a}->{b}" for a, b in changes[:2]))
        print(f"\n  {touched}/{len(files)} file(s) would change, {total} character(s) removed")
        return 0

    raw = open(args.path, encoding='utf-8').read() if args.path else sys.stdin.read()
    out = normalize_for_tts(raw)
    for a, b in diff_report(raw, out):
        print(f"  {a}  ->  {b}", file=sys.stderr)
    print(f"  ({len(raw)} -> {len(out)} characters)", file=sys.stderr)
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":

    import sys
    sys.exit(main())
