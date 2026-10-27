"""
ElevenLabs-specific text preparation, layered on top of the shared Turkish rules.

The shared layer (text_normalize + tr_readings) decides *what* is said and is
the same for every engine. This one decides *how it is delivered* to
ElevenLabs, which differs from Chirp 3 in ways that matter:

- It accepts <break time="..."/> tags (multilingual v2, turbo, flash; not v3),
  so paragraph and header pauses can be set explicitly instead of hoped for.
- Each request is generated independently. Tone and pace re-settle at every
  chunk boundary unless the neighbouring text is passed as context, and long
  generations drift - so chunks are smaller than the API allows.
- A line without closing punctuation is read with a rising, unfinished
  intonation, and a chunk that ends mid-sentence can end in an artefact.
- Initialisms tuned by ear for Chirp ("Te Ka Pe") may want a different form.

    python eleven_prep.py articles/txt/<file>.txt     # show the chunks it would send
"""

import re

import tr_readings
from text_normalize import normalize_for_tts

# Initialisms ElevenLabs should say differently from Chirp 3. Same format as
# tr_readings.SPELLED; a value of None passes the token through so the engine
# reads the letters itself. Empty until an A/B listen says otherwise:
#     python compare_voices.py --eleven-initialisms
ELEVEN_SPELLED = {
    # Studio listen, 2026-10-05: read correctly from the letters, and better
    # than the spelled forms tuned for Chirp.
    'TKP': None,
    'TBMM': None,
    'KKTC': None,
}

# A passed-through initialism keeps its apostrophe ("TKP'nin"): that is the form
# the listen above approved. The shared layer deletes apostrophes (a Chirp fix),
# so it is swapped for this private-use placeholder first and restored after.
_KEEP_APOSTROPHE = ''

# Smaller than the API's limit on purpose: multilingual v2 drifts in pace and
# accent over a long generation. Paragraph-sized requests stay steadier, and the
# context fields below hide the seams between them.
CHUNK_CHARS = 2000

# Context passed alongside each chunk (previous_text / next_text).
CONTEXT_CHARS = 300

# Pauses, in seconds. The first blocks of a transcript are the title, the byline
# line and the spot (see sol_column_scraper.write_txt): a longer pause after them
# separates the heading from the column the way a presenter would.
HEADER_BLOCKS = 2
HEADER_BREAK = 1.0
PARAGRAPH_BREAK = 0.5

# ElevenLabs warns that many break tags in one generation destabilise it
# (speed-ups, artefacts). Past this, paragraphs are joined by a blank line only.
MAX_BREAKS_PER_CHUNK = 4

# Models that take <break> tags. v3 does not; it would read them out.
_NO_BREAK_MODELS = ('eleven_v3',)
# Models that accept language_code. multilingual v2 rejects it.
LANGUAGE_CODE_MODELS = {'eleven_turbo_v2_5', 'eleven_flash_v2_5', 'eleven_v3'}

_CLOSERS = '"\')»”’]'
_TAG = re.compile(r'<break[^>]*/>')
_SENTENCE = re.compile(r'[^.!?…]+[.!?…]+["»”’)\]]*\s*|[^.!?…]+$')


def supports_breaks(model):
    return not model.startswith(_NO_BREAK_MODELS)


def close_line(line):
    """End a line with punctuation so it is read as finished, not as a question
    hanging in the air. Headings and the byline are the usual offenders."""
    line = line.rstrip()
    body = line.rstrip(_CLOSERS)
    if not body or body[-1] in '.!?…:;':
        return line
    if body[-1] == ',':
        return body[:-1] + '.' + line[len(body):]
    return line + '.'


def prepare(raw):
    """Raw transcript -> the text ElevenLabs should say, paragraphs kept apart."""
    kept = [t for t, v in ELEVEN_SPELLED.items() if v is None]
    if kept:
        raw = re.sub(rf"\b({'|'.join(map(re.escape, kept))})[’'ʼ](?=\w)",
                     lambda m: m.group(1) + _KEEP_APOSTROPHE, raw)
    text = normalize_for_tts(raw, spelled={**tr_readings.SPELLED, **ELEVEN_SPELLED})
    text = text.replace(_KEEP_APOSTROPHE, "'")
    # Angle brackets are parsed as markup; only our own break tags may use them.
    text = text.replace('<', ' ').replace('>', ' ')
    paragraphs = []
    for block in text.split('\n\n'):
        lines = [close_line(l) for l in block.split('\n') if l.strip()]
        if lines:
            paragraphs.append('\n'.join(lines))
    return '\n\n'.join(paragraphs)


def _split_paragraph(paragraph, limit):
    """A paragraph over the limit, cut at sentence ends (words as a last resort)."""
    out, current = [], ''
    for sentence in (s.strip() for s in _SENTENCE.findall(paragraph)):
        if not sentence:
            continue
        pieces = [sentence]
        if len(sentence) > limit:            # only on malformed input
            pieces, word_run = [], ''
            for word in sentence.split(' '):
                if word_run and len(word_run) + 1 + len(word) > limit:
                    pieces.append(word_run)
                    word_run = word
                else:
                    word_run = f"{word_run} {word}".strip()
            pieces.append(word_run)
        for piece in pieces:
            if current and len(current) + 1 + len(piece) <= limit:
                current += ' ' + piece
            else:
                if current:
                    out.append(current)
                current = piece
    if current:
        out.append(current)
    return out


def chunk(prepared, limit=CHUNK_CHARS, breaks=True):
    """Split prepared text into requests, each ending on a sentence boundary,
    with break tags between paragraphs inside a request."""
    units = []                       # (text, index of the paragraph it opens, or None)
    for i, paragraph in enumerate(p for p in prepared.split('\n\n') if p.strip()):
        pieces = [paragraph] if len(paragraph) <= limit else _split_paragraph(paragraph, limit)
        units.extend((piece, i if j == 0 else None) for j, piece in enumerate(pieces))

    chunks, current, used = [], '', 0
    for piece, opens in units:
        if not current:
            current, used = piece, 0
            continue
        took_break = False
        if opens is None:
            joiner = ' '
        elif breaks and used < MAX_BREAKS_PER_CHUNK:
            pause = HEADER_BREAK if opens <= HEADER_BLOCKS else PARAGRAPH_BREAK
            joiner = f'\n<break time="{pause:.1f}s" />\n'
            took_break = True
        else:
            joiner = '\n\n'
        candidate = current + joiner + piece
        if len(candidate) <= limit:
            current = candidate
            used += took_break
        else:
            chunks.append(current)
            current, used = piece, 0
    if current:
        chunks.append(current)
    return chunks


def strip_tags(text):
    return re.sub(r'\s+', ' ', _TAG.sub(' ', text)).strip()


def context(chunks, i, size=CONTEXT_CHARS):
    """previous_text / next_text for chunk i: plain text, cut at a word."""
    before = strip_tags(chunks[i - 1])[-size:] if i > 0 else ''
    after = strip_tags(chunks[i + 1])[:size] if i + 1 < len(chunks) else ''
    if before and ' ' in before:
        before = before.split(' ', 1)[1]
    if after and ' ' in after:
        after = after.rsplit(' ', 1)[0]
    return before, after


def billed_chars(prepared, model):
    """Characters the requests will contain - break tags included, since they
    are part of the text ElevenLabs counts."""
    return sum(len(c) for c in chunk(prepared, breaks=supports_breaks(model)))


def main():
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else None
    raw = open(path, encoding='utf-8').read() if path else sys.stdin.read()
    chunks = chunk(prepare(raw))
    for i, c in enumerate(chunks, 1):
        print(f"--- chunk {i}/{len(chunks)} ({len(c):,} characters)", file=sys.stderr)
        print(c)
    print(f"--- total {sum(map(len, chunks)):,} characters ({len(raw):,} raw)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
