"""
TTS Converter - turns scraped columns into Turkish audio.

Driven by state.json: only columns whose 'tts' stage is empty are synthesised,
so re-running never re-sends an already-voiced column to the engine. That also
matters for money now - with a paid engine, a needless re-render is a re-charge.
Use --force to deliberately re-voice one.

Engines:
    chirp3    Google Cloud TTS, Chirp 3: HD voices (paid, best quality)
    edge-tts  Microsoft Edge read-aloud endpoint (free, unofficial, no SLA)
    gtts      Google Translate TTS (free, unofficial)
    pyttsx3   local system voices (offline, poor quality)

Usage:
    python tts_converter.py                              # default engine
    python tts_converter.py --engine chirp3
    python tts_converter.py --engine chirp3 --voice tr-TR-Chirp3-HD-Charon
    python tts_converter.py --list-voices                # what Chirp 3 offers in tr-TR
    python tts_converter.py --force 413293
"""

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime

import eleven_prep
import sol_state
from text_normalize import normalize_for_tts

DEFAULT_ENGINE = 'auto'

# ---------------------------------------------------------------- Chirp 3: HD

CHIRP_ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"
CHIRP_VOICES_ENDPOINT = "https://texttospeech.googleapis.com/v1/voices"
LANGUAGE_CODE = "tr-TR"
CHIRP_VOICE = "tr-TR-Chirp3-HD-Charon"

# Two voices in rotation, assigned per columnist rather than per column.
# Alternating within an author would make the same byline sound like two
# different people from one day to the next; keeping it fixed per author gives
# each columnist a recognisable voice while still varying across the feed.
VOICE_POOL = ["tr-TR-Chirp3-HD-Charon", "tr-TR-Chirp3-HD-Kore"]


def pick_voice(entry):
    """Stable choice: the same column always gets the same voice.

    Hashed with md5 rather than hash(), whose string seed changes every process
    - that would hand a re-voiced column a different speaker than the one
    already published.
    """
    key = entry.get('author_slug') or entry.get('id') or ''
    digest = hashlib.md5(key.encode('utf-8')).hexdigest()
    return VOICE_POOL[int(digest, 16) % len(VOICE_POOL)]

# The API rejects anything over 5000 bytes per request, and Turkish text is
# ~11% larger in UTF-8 than its character count, so the budget is set in bytes
# with headroom rather than in characters.
MAX_REQUEST_BYTES = 4500

KEY_ENV = "GOOGLE_TTS_API_KEY"
KEY_FILES = ("api_key.json", "google_tts_key.txt")

# Cloud TTS refuses plain API keys outright:
#   401 "API keys are not supported by this API. Expected OAuth2 access token
#        or other authentication credentials that assert a principal."
# so the supported path is a service account JSON, exchanged for a bearer token.
SERVICE_ACCOUNT_FILES = ("service_account.json", "gcp_service_account.json", "api_key.json")
CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

_TOKEN_CACHE = {}


def service_account_file():
    for path in SERVICE_ACCOUNT_FILES:
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding='utf-8-sig') as f:
                if json.load(f).get('type') == 'service_account':
                    return path
        except (json.JSONDecodeError, OSError):
            continue
    return None


def access_token():
    """Bearer token from the service account, refreshed as needed."""
    path = service_account_file()
    if not path:
        return None

    creds = _TOKEN_CACHE.get('creds')
    if creds is None:
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(
            path, scopes=[CLOUD_SCOPE])
        _TOKEN_CACHE['creds'] = creds

    if not creds.valid:
        from google.auth.transport.requests import Request
        creds.refresh(Request())
    return creds.token


def auth_headers_and_params():
    """Return (headers, params) for a Cloud TTS REST call, or (None, None).

    Prefers the service account. Falls back to an API key only so that a
    misconfigured setup produces Google's own error rather than silence.
    """
    token = access_token()
    if token:
        return {'Authorization': f'Bearer {token}'}, {}

    key = api_key(quiet=True)
    if key:
        return {}, {'key': key}

    print("No Google Cloud credentials found.\n"
          "  Cloud Text-to-Speech does NOT accept API keys - it needs a service account.\n"
          "  Console > IAM & Admin > Service Accounts > Create, grant it the\n"
          "  'Cloud Text-to-Speech User' role, then Keys > Add key > JSON, and save\n"
          f"  the file here as one of: {', '.join(SERVICE_ACCOUNT_FILES[:2])}",
          file=sys.stderr)
    return None, None


SERVICE_ACCOUNT_HELP = "\n".join((
    "Cloud Text-to-Speech does not accept API keys - it needs a service account.",
    "  1. Console > IAM & Admin > Service Accounts > Create service account",
    "  2. Grant it the role 'Cloud Text-to-Speech User'",
    "  3. Open it > Keys > Add key > Create new key > JSON > download",
    "  4. Save that file next to this script as service_account.json",
    "  (also make sure the Text-to-Speech API is enabled on the project)",
))


def explain_auth_failure(status, body):
    """Turn Google's 401/403 into the one instruction that actually fixes it."""
    if status in (401, 403) and 'API keys are not supported' in body:
        print("\n! " + SERVICE_ACCOUNT_HELP, file=sys.stderr)
        return True
    return False


def _find_key(value):
    """Pull an API key out of whatever shape the credentials file happens to be.

    Google hands the key out in several wrappers (a bare string, a console
    export with an Api_Key_Details block, a flat {"api_key": ...}), so the
    lookup is by key name at any depth rather than by a fixed path.
    """
    if isinstance(value, str):
        candidate = value.strip()
        return candidate or None
    if isinstance(value, dict):
        for name, inner in value.items():
            if name.lower().replace('-', '_') in ('api_key', 'apikey', 'key') \
                    and isinstance(inner, str) and inner.strip():
                return inner.strip()
        for inner in value.values():
            found = _find_key(inner) if isinstance(inner, (dict, list)) else None
            if found:
                return found
    if isinstance(value, list):
        for inner in value:
            found = _find_key(inner)
            if found:
                return found
    return None


def api_key(quiet=False):
    """Read the key from the environment or a local file - never from argv.

    Keeping it out of the command line keeps it out of `ps`, the shell history
    and the systemd unit.
    """
    key = os.environ.get(KEY_ENV, '').strip()
    if key:
        return key

    for path in KEY_FILES:
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8-sig') as f:
            raw = f.read().strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return raw          # plain one-line key file
        if isinstance(parsed, dict) and parsed.get('type') == 'service_account':
            continue        # handled by access_token(), not here
        found = _find_key(parsed)
        if found:
            return found

    if not quiet:
        print(f"No Google TTS API key. Put it in {' or '.join(KEY_FILES)}, "
              f"or set ${KEY_ENV}.", file=sys.stderr)
    return None


def split_for_api(text, budget=MAX_REQUEST_BYTES, measure=None):
    """Split text into UTF-8 chunks under `budget` bytes, on natural boundaries.

    Prefers paragraph breaks, falls back to sentence ends, and only as a last
    resort splits mid-sentence at a space - a break in the middle of a clause is
    audible as a swallowed pause, so it is worth trying hard to avoid.
    """
    # Chirp 3 limits a request by UTF-8 bytes; ElevenLabs counts characters.
    # Same splitting logic, different ruler.
    size = measure or (lambda s: len(s.encode('utf-8')))

    def pack(pieces, joiner):
        out, current = [], ''
        for piece in pieces:
            candidate = piece if not current else current + joiner + piece
            if size(candidate) <= budget:
                current = candidate
            else:
                if current:
                    out.append(current)
                current = piece
        if current:
            out.append(current)
        return out

    def hard_split(s):
        """Last resort for a token with no split point - cut on byte boundaries.

        Only reachable on malformed input (an unbroken run longer than the
        budget), but the function must never hand the API an oversized chunk,
        so the guarantee is enforced here rather than assumed.
        """
        if measure is not None:            # character budget: a plain slice
            return [s[i:i + budget] for i in range(0, len(s), budget)]
        raw = s.encode('utf-8')
        out = []
        while raw:
            piece, raw = raw[:budget], raw[budget:]
            # Don't cut a multi-byte character in half.
            while piece and raw and (raw[0] & 0xC0) == 0x80:
                raw = piece[-1:] + raw
                piece = piece[:-1]
            out.append(piece.decode('utf-8'))
        return out

    chunks = []
    for block in pack([p for p in text.split('\n\n') if p.strip()], '\n\n'):
        if size(block) <= budget:
            chunks.append(block)
            continue
        # Too big as one paragraph - break into sentences, keeping the terminator.
        sentences = re.findall(r'[^.!?…]+[.!?…]+["»”’)\]]*\s*|[^.!?…]+$', block)
        for part in pack([s.strip() for s in sentences if s.strip()], ' '):
            if size(part) <= budget:
                chunks.append(part)
                continue
            for word_chunk in pack(part.split(' '), ' '):
                if size(word_chunk) <= budget:
                    chunks.append(word_chunk)
                else:
                    chunks.extend(hard_split(word_chunk))
    return chunks


def chirp_synthesize_chunk(text, auth, voice, rate):
    """One synthesize call. Returns MP3 bytes, or None."""
    import requests

    headers, params = auth

    body = {
        'input': {'text': text},
        'voice': {'languageCode': LANGUAGE_CODE, 'name': voice},
        # LINEAR16, not MP3: Google's MP3 output is a fixed 32 kb/s mono, which
        # throws away most of what an HD voice is worth. Ask for uncompressed
        # PCM and do the single encode ourselves, at a bitrate we choose.
        'audioConfig': {'audioEncoding': 'LINEAR16'},
    }
    if rate != 1.0:
        body['audioConfig']['speakingRate'] = rate

    for attempt in range(1, 5):
        try:
            response = requests.post(CHIRP_ENDPOINT, headers=headers, params=params,
                                     json=body, timeout=120)
        except requests.RequestException as exc:
            print(f"        . network error ({exc.__class__.__name__}), retry {attempt}")
            time.sleep(2 ** attempt)
            continue

        if response.status_code == 200:
            return base64.b64decode(response.json()['audioContent'])

        # 429/5xx are worth waiting out; 4xx means the request itself is wrong.
        if response.status_code == 429 or response.status_code >= 500:
            print(f"        . HTTP {response.status_code}, retry {attempt}")
            time.sleep(2 ** attempt)
            continue

        detail = response.json().get('error', {}).get('message', response.text[:300])
        print(f"      ! HTTP {response.status_code}: {detail}")
        explain_auth_failure(response.status_code, response.text)
        return None

    print("      ! gave up after repeated transient failures")
    return None


MP3_BITRATE = "128k"


def concat_and_encode(parts, output_path):
    """Join the WAV chunks and encode to MP3 once, over the whole programme.

    Encoding after the join rather than per chunk keeps the single lossy step
    single - re-encoding each piece would stack artefacts at every seam.
    """
    from imageio_ffmpeg import get_ffmpeg_exe

    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for i, blob in enumerate(parts):
            path = os.path.join(tmp, f"part{i:03d}.wav")
            with open(path, 'wb') as f:
                f.write(blob)
            paths.append(path)

        listing = os.path.join(tmp, "parts.txt")
        with open(listing, 'w', encoding='utf-8') as f:
            for path in paths:
                f.write(f"file '{path.replace(chr(92), '/')}'\n")

        result = subprocess.run(
            [get_ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0", "-i", listing,
             "-c:a", "libmp3lame", "-b:a", MP3_BITRATE, "-ar", "24000", "-ac", "1",
             output_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            print(result.stderr[-1500:])
            return False
    return True


def convert_with_chirp3(text, output_path, voice=CHIRP_VOICE, rate=1.0):
    auth = auth_headers_and_params()
    if auth == (None, None):
        return False

    chunks = split_for_api(text)
    total = sum(len(c.encode('utf-8')) for c in chunks)
    print(f"      {len(chunks)} chunk(s), {total:,} bytes, voice: {voice}")

    parts = []
    for i, chunk in enumerate(chunks, 1):
        print(f"        chunk {i}/{len(chunks)} ({len(chunk.encode('utf-8')):,} bytes)")
        audio = chirp_synthesize_chunk(chunk, auth, voice, rate)
        if audio is None:
            return False
        parts.append(audio)

    return concat_and_encode(parts, output_path)


def list_chirp_voices():
    import requests

    headers, params = auth_headers_and_params()
    if headers is None:
        return 1
    response = requests.get(CHIRP_VOICES_ENDPOINT, headers=headers,
                            params={**params, 'languageCode': LANGUAGE_CODE}, timeout=60)
    if response.status_code != 200:
        message = response.json().get('error', {}).get('message', response.text[:300])
        print(f"HTTP {response.status_code}: {message}", file=sys.stderr)
        explain_auth_failure(response.status_code, response.text)
        return 1

    voices = response.json().get('voices', [])
    chirp = sorted(v['name'] for v in voices if 'Chirp3-HD' in v['name'])
    other = sorted(v['name'] for v in voices if 'Chirp3-HD' not in v['name'])
    print(f"Chirp 3: HD ({LANGUAGE_CODE}) - {len(chirp)} voices")
    for name in chirp:
        print("   ", name)
    print(f"\nOther {LANGUAGE_CODE} voices: {len(other)}")
    return 0


# ----------------------------------------------------------------- ElevenLabs

ELEVEN_ENDPOINT = "https://api.elevenlabs.io/v1/text-to-speech"
ELEVEN_KEY_FILES = ("elevenlabs_key.txt", "elevenlabs.json")
ELEVEN_KEY_ENV = "ELEVENLABS_API_KEY"

ELEVEN_VOICE = "uvU9jrgGLWNPeNA4NgNT"        # Irem
ELEVEN_MODEL = "eleven_multilingual_v2"      # quality over latency; this is a batch job

# Chunk size, break tags and context live in eleven_prep. Fixed seed: the same
# text re-rendered gives the same delivery, so a re-voice changes only what the
# text changed.
ELEVEN_SEED = 20261005
# ElevenLabs' own normalisation is English-centric and knows no Turkish
# suffixes, so the shared layer expands everything it can first. "auto" leaves
# the engine a safety net for what is left (bare years and counts).
ELEVEN_TEXT_NORMALIZATION = "auto"

# Steady news narration rather than performance: high stability keeps the tone
# even across an eight-minute column, and low style stops it drifting into
# dramatisation paragraph by paragraph.
ELEVEN_SETTINGS = {"stability": 0.5, "similarity_boost": 0.75, "style": 0.0,
                   "use_speaker_boost": True}


def elevenlabs_key():
    key = os.environ.get(ELEVEN_KEY_ENV, '').strip()
    if key:
        return key
    for path in ELEVEN_KEY_FILES:
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8-sig') as f:
            raw = f.read().strip()
        if not raw:
            continue
        try:
            return _find_key(json.loads(raw)) or raw
        except json.JSONDecodeError:
            return raw
    return None


def convert_with_elevenlabs(text, output_path, voice=None, rate=1.0, **_):
    """`text` is eleven_prep.prepare() output, not the shared normalisation."""
    import requests

    key = elevenlabs_key()
    if not key:
        print(f"      ! no ElevenLabs key ({' / '.join(ELEVEN_KEY_FILES)})")
        return False

    voice_id = voice or ELEVEN_VOICE
    chunks = eleven_prep.chunk(text, breaks=eleven_prep.supports_breaks(ELEVEN_MODEL))
    print(f"      {len(chunks)} chunk(s), {sum(map(len, chunks)):,} characters, voice: {voice_id}")

    parts, request_ids = [], []
    for i, chunk in enumerate(chunks):
        print(f"        chunk {i + 1}/{len(chunks)} ({len(chunk):,} characters)")
        before, after = eleven_prep.context(chunks, i)
        body = {"text": chunk, "model_id": ELEVEN_MODEL,
                "voice_settings": ELEVEN_SETTINGS,
                "seed": ELEVEN_SEED,
                "apply_text_normalization": ELEVEN_TEXT_NORMALIZATION}
        # Continuity across chunks. The request ids of what was already
        # generated condition this one on the actual audio; when sent, they
        # take precedence over previous_text, which covers the first retry
        # case where no id came back.
        if before:
            body["previous_text"] = before
        if after:
            body["next_text"] = after
        if request_ids:
            body["previous_request_ids"] = request_ids[-3:]
        if ELEVEN_MODEL in eleven_prep.LANGUAGE_CODE_MODELS:
            body["language_code"] = "tr"
        audio = None
        for attempt in range(1, 5):
            try:
                response = requests.post(
                    f"{ELEVEN_ENDPOINT}/{voice_id}",
                    params={"output_format": "mp3_44100_128"},
                    headers={"xi-api-key": key, "accept": "audio/mpeg"},
                    json=body, timeout=180)
            except requests.RequestException as exc:
                print(f"        . {exc.__class__.__name__}, retrying in {2 ** attempt}s")
                time.sleep(2 ** attempt)
                continue
            if response.status_code == 200:
                audio = response.content
                if response.headers.get('request-id'):
                    request_ids.append(response.headers['request-id'])
                break
            if response.status_code == 429 or response.status_code >= 500:
                print(f"        . HTTP {response.status_code}, retrying in {2 ** attempt}s")
                time.sleep(2 ** attempt)
                continue
            print(f"      ! HTTP {response.status_code}: {response.text[:200]}")
            return False
        if audio is None:
            print("      ! transient errors persisted, gave up")
            return False
        parts.append(audio)

    return concat_mp3_parts(parts, output_path)


def concat_mp3_parts(parts, output_path):
    """Join MP3 chunks. Already encoded by the API, so no re-encode here."""
    from imageio_ffmpeg import get_ffmpeg_exe

    if len(parts) == 1:
        with open(output_path, 'wb') as f:
            f.write(parts[0])
        return True

    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for i, blob in enumerate(parts):
            path = os.path.join(tmp, f"part{i:03d}.mp3")
            with open(path, 'wb') as f:
                f.write(blob)
            paths.append(path)
        listing = os.path.join(tmp, "parts.txt")
        with open(listing, 'w', encoding='utf-8') as f:
            for path in paths:
                f.write(f"file '{path.replace(chr(92), '/')}'\n")
        result = subprocess.run(
            [get_ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0", "-i", listing,
             "-c", "copy", output_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            print(result.stderr[-1500:])
            return False
    return True


# --------------------------------------------------------------- free engines

def convert_with_edge_tts(text, output_path, **_):
    try:
        import asyncio
        import edge_tts

        async def run():
            await edge_tts.Communicate(text, "tr-TR-EmelNeural").save(output_path)

        asyncio.run(run())
        return True
    except Exception as exc:
        print(f"      ! Edge TTS error: {exc}")
        return False


def convert_with_gtts(text, output_path, **_):
    try:
        from gtts import gTTS
        gTTS(text=text, lang='tr', slow=False).save(output_path)
        return True
    except Exception as exc:
        print(f"      ! gTTS error: {exc}")
        return False


def convert_with_pyttsx3(text, output_path, **_):
    try:
        import pyttsx3
        engine = pyttsx3.init()
        for voice in engine.getProperty('voices'):
            if 'turkish' in voice.name.lower() or 'tr' in (voice.languages or []):
                engine.setProperty('voice', voice.id)
                break
        engine.setProperty('rate', 150)
        engine.save_to_file(text, output_path)
        engine.runAndWait()
        return True
    except Exception as exc:
        print(f"      ! pyttsx3 error: {exc}")
        return False


ENGINES = {
    'chirp3': convert_with_chirp3,
    'elevenlabs': convert_with_elevenlabs,
    'edge-tts': convert_with_edge_tts,
    'gtts': convert_with_gtts,
    'pyttsx3': convert_with_pyttsx3,
}

# ------------------------------------------------------------- monthly budget

# Characters a calendar month may send to ElevenLabs. Set from the plan, not
# from past usage: Creator allows 121,000 credits and Multilingual v2 spends
# one per character, so this is that ceiling with 15% held back. September's
# actual output was 419,492 characters, so roughly a quarter of the month's
# columns get the premium voice and the rest fall back to Google - which is
# free, and at that share sits near 20% of its own 1M allowance.
#
# Raise this if the plan changes; nothing else needs touching.
ELEVENLABS_MONTHLY_BUDGET = 103_000

# At most one column a day gets the premium voice, whatever the budget says.
# Two to four columns are published most days, so without this a single run
# could spend a week's worth of budget in one morning.
ELEVENLABS_MAX_PER_DAY = 1

FALLBACK_ENGINE = 'chirp3'

# Share of the plan's credits never spent by the pipeline, so manual tests and
# a re-voice still have room. Applied to the live balance when it is readable.
ELEVENLABS_RESERVE_SHARE = 0.15


def elevenlabs_balance():
    """(remaining, limit) credits as ElevenLabs itself reports them, or None.

    The account is the truth; our ledger is only a fallback. In October 2026
    the ledger counted 100,926 characters for the cycle while the account
    showed 45,940 used, and the premium voice stopped for days with 85,060
    credits still left. Reading it needs the key's `user_read` permission.
    """
    import requests

    key = elevenlabs_key()
    if not key:
        return None
    try:
        r = requests.get("https://api.elevenlabs.io/v1/user/subscription",
                         headers={"xi-api-key": key}, timeout=30)
        if r.status_code != 200:
            return None
        sub = r.json()
        limit, used = sub["character_limit"], sub["character_count"]
    except Exception:
        return None
    return max(0, limit - used), limit


def spent_this_month(state, engine='elevenlabs', now=None):
    """Characters already billed to `engine` in the current calendar month.

    Keyed on when the audio was produced, not when the column was published:
    the bill follows usage, and a backlog voiced today is charged to today.
    """
    month = (now or datetime.now()).strftime('%Y-%m')
    return sum(e.get('tts_billed_chars') or 0
               for e in state['articles'].values()
               if e.get('tts_engine') == engine
               and (e.get('stages', {}).get('tts') or '')[:7] == month)


def used_today(state, engine='elevenlabs', now=None):
    """How many columns already went to `engine` today."""
    day = (now or datetime.now()).strftime('%Y-%m-%d')
    return sum(1 for e in state['articles'].values()
               if e.get('tts_engine') == engine
               and (e.get('stages', {}).get('tts') or '')[:10] == day)


def choose_engine(state, chars, budget=ELEVENLABS_MONTHLY_BUDGET, now=None,
                  attempted=(), balance=None):
    """Premium voice while both limits allow it, otherwise the free one.

    Two ceilings, both checked before the call rather than noticed after it:
    one column a day, and the month's character budget. A column is never
    split across engines - it is wholly one or the other.

    Returns the engine and a short reason, so the log says why a column got
    the voice it did.

    `chars` is what ElevenLabs would bill for this column - its own prepared
    text, break tags included (eleven_prep.billed_chars), not the shared one.

    `balance` is elevenlabs_balance(): when given, the account's own remaining
    credits less the reserve decide; otherwise the monthly ledger does.

    With no ElevenLabs key configured this degrades to the free engine rather
    than failing: otherwise every column would be chosen for a service that
    cannot answer, fail, and be retried the same way the next night.
    """
    if not elevenlabs_key():
        return FALLBACK_ENGINE, 'no ElevenLabs key'

    # `attempted` carries calls made in this run that produced nothing: a
    # failure may still have been billed for the chunks that went through, so
    # it counts against the day just as a success does.
    today = used_today(state, 'elevenlabs', now)
    today = max(today, len(set(attempted)))
    if today >= ELEVENLABS_MAX_PER_DAY:
        return FALLBACK_ENGINE, f"today's ElevenLabs share used up ({today}/{ELEVENLABS_MAX_PER_DAY})"

    if balance:
        remaining, limit = balance
        usable = remaining - int(limit * ELEVENLABS_RESERVE_SHARE)
        if chars > usable:
            return FALLBACK_ENGINE, (f'{remaining:,} credits left on the account, '
                                     f'%{ELEVENLABS_RESERVE_SHARE * 100:.0f} held in reserve')
        return 'elevenlabs', f'{remaining - chars:,} credits will remain on the account'

    spent = spent_this_month(state, 'elevenlabs', now)
    if spent + chars > budget:
        return FALLBACK_ENGINE, f'monthly budget used up ({spent:,}/{budget:,})'

    remaining = budget - spent - chars
    return 'elevenlabs', f'{remaining:,} characters left in the budget'


def main():
    parser = argparse.ArgumentParser(description='Convert scraped columns to Turkish audio')
    parser.add_argument('--engine', default=DEFAULT_ENGINE,
                        choices=['auto'] + list(ENGINES),
                        help="'auto' spends the month's ElevenLabs budget first, "
                             'then falls back to Google for the rest')
    parser.add_argument('--voice', default=None,
                        help='force one Chirp 3 voice for every column; '
                             'omit to rotate per author across ' + ', '.join(
                                 v.split('-')[-1] for v in VOICE_POOL))
    parser.add_argument('--rate', type=float, default=1.0, help='speaking rate, 0.25-2.0')
    parser.add_argument('--dir', default='articles')
    parser.add_argument('--force', metavar='ARTICLE_ID',
                        help='re-voice this article id even though it is already done')
    parser.add_argument('--list-voices', action='store_true',
                        help='list the Chirp 3 voices available for tr-TR and exit')
    args = parser.parse_args()

    if args.list_voices:
        return list_chirp_voices()

    state = sol_state.load()
    if args.force:
        sol_state.mark(state, args.force, 'tts', None)

    todo = sol_state.pending(state, 'tts', requires='scraped')
    os.makedirs(os.path.join(args.dir, 'audio'), exist_ok=True)

    print("=" * 70)
    voicing = args.voice or ('by author: ' + ', '.join(
        v.split('-')[-1] for v in VOICE_POOL))
    print(f"TTS ({args.engine}, {voicing})")
    balance = None
    if args.engine == 'auto':
        balance = elevenlabs_balance()
        if balance:
            remaining, limit = balance
            print(f"  ElevenLabs account: {remaining:,} / {limit:,} credits left "
                  f"(%{ELEVENLABS_RESERVE_SHARE * 100:.0f} held in reserve)")
        else:
            used = spent_this_month(state, 'elevenlabs')
            print("  ElevenLabs account unreadable (key lacks user_read?), using the ledger")
            print(f"  ElevenLabs budget : {used:,} / {ELEVENLABS_MONTHLY_BUDGET:,} characters "
                  f"(%{used / ELEVENLABS_MONTHLY_BUDGET * 100:.0f}), "
                  f"{max(0, ELEVENLABS_MONTHLY_BUDGET - used):,} left")
        print(f"  used today       : {used_today(state, 'elevenlabs')}"
              f"/{ELEVENLABS_MAX_PER_DAY} columns")
    print(f"  {len(todo)} column(s) to voice, "
          f"{len(state['articles']) - len(todo)} already done")
    print("=" * 70)
    if not todo:
        return 0

    if args.engine in ('chirp3', 'auto') and auth_headers_and_params() == (None, None):
        return 1
    ok = 0
    premium_tried = []          # ids this run sent to ElevenLabs, failures included
    for i, entry in enumerate(todo, 1):
        print(f"\n[{i}/{len(todo)}] {entry['title'][:60]}")
        txt_path = os.path.join(args.dir, entry['txt_file'])
        if not os.path.exists(txt_path):
            print(f"      ! text missing: {txt_path}")
            continue

        with open(txt_path, 'r', encoding='utf-8') as f:
            raw = f.read()
        # Transcripts are stored verbatim; each engine gets its own spoken
        # form. ElevenLabs takes break tags and its own initialism table,
        # everything else the shared normalisation.
        spoken = {'elevenlabs': eleven_prep.prepare(raw)}
        shared = normalize_for_tts(raw)

        def text_for(engine):
            return spoken.get(engine, shared)

        def billed(engine):
            if engine == 'elevenlabs':
                return eleven_prep.billed_chars(spoken[engine], ELEVEN_MODEL)
            return len(shared)

        # Decided per column, after preparation, so the budget is measured
        # against exactly the characters the engine will be billed for.
        if args.engine == 'auto':
            engine, reason = choose_engine(state, billed('elevenlabs'),
                                           attempted=premium_tried, balance=balance)
            print(f"      {engine} - {reason}")
        else:
            engine = args.engine

        audio_rel = f"audio/{entry['filename']}.mp3"
        audio_path = os.path.join(args.dir, audio_rel)

        # The premium engine failing must not cost the column its narration:
        # the free one is right there and working. One attempt only - a failed
        # call may still have been billed for the chunks that went through,
        # and retrying it for every column would burn a day's credit on an
        # outage.
        attempts = [engine]
        if args.engine == 'auto' and engine == 'elevenlabs':
            attempts.append(FALLBACK_ENGINE)

        produced = None
        for candidate in attempts:
            if candidate == 'elevenlabs':
                premium_tried.append(entry['id'])
            voice = (args.voice if args.engine != 'auto' else None) or (
                ELEVEN_VOICE if candidate == 'elevenlabs' else pick_voice(entry))
            if (ENGINES[candidate](text_for(candidate), audio_path, voice=voice,
                                   rate=args.rate)
                    and os.path.exists(audio_path) and os.path.getsize(audio_path) > 0):
                produced = (candidate, voice)
                break
            if candidate != attempts[-1]:
                print(f"      ! {candidate} failed, trying {attempts[-1]}")

        if produced:
            engine, voice = produced
            entry['audio_file'] = audio_rel
            # The engine that actually ran, not what was asked for: under
            # 'auto' these differ, and the budget is counted from this field.
            entry['tts_engine'] = engine
            entry['tts_voice'] = voice
            entry['tts_billed_chars'] = billed(engine)
            sol_state.mark(state, entry['id'], 'tts')
            sol_state.save(state)
            ok += 1
            print(f"      ok  {audio_rel} ({os.path.getsize(audio_path) / 1024:.0f} KB)")
        else:
            print("      ! failed")

    sol_state.export_index(state, args.dir)
    print(f"\n{'=' * 70}\n  {ok}/{len(todo)} voiced\n{'=' * 70}")
    # Exit 2, not 1: some columns were narrated and the rest stay unmarked for
    # the next run. Reporting total failure here halted the whole pipeline - no
    # video, no upload, no archive - for two days, because one article had a
    # sentence the engine refused.
    return 0 if ok == len(todo) else 2


if __name__ == "__main__":
    sys.exit(main())


def list_all_chirp_names():
    """Every tr-TR Chirp 3 voice name, straight from the API."""
    import requests

    headers, params = auth_headers_and_params()
    if headers is None:
        return []
    response = requests.get(CHIRP_VOICES_ENDPOINT, headers=headers,
                            params={**params, 'languageCode': LANGUAGE_CODE}, timeout=60)
    if response.status_code != 200:
        return []
    return sorted(v['name'] for v in response.json().get('voices', [])
                  if 'Chirp3-HD' in v['name'])
