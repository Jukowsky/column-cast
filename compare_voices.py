"""
Render the same Turkish passage with several voices, for A/B listening.

Turkish stress is the thing to judge, so the default passage is loaded with the
cases that usually break: suffix-stacked words where stress must move to the
final syllable, the separate-written question particle, proper nouns, an
initialism, a year, and the i/ı - g/ğ distinctions.

    python compare_voices.py                     # WaveNet + a few Chirp 3
    python compare_voices.py --all-chirp         # every Chirp 3 voice too
    python compare_voices.py --text "kendi cumleniz"
    python compare_voices.py --eleven-initialisms TKP AKP --yes

The last one spends ElevenLabs credit: each initialism is rendered twice in
one carrier sentence, once as written ("TKP") and once in its SPELLED form
("Te Ka Pe"). Whichever sounds right goes into eleven_prep.ELEVEN_SPELLED -
None to keep the letters, a string to override the spelling.
"""

import argparse
import base64
import os
import sys

import requests

import tts_converter as tc

OUT_DIR = "articles/voice_test"

SAMPLE = (
    "Komünizm Cumhuriyet'in neresindeydi? "
    "Geleneğimizin çekiştirilmesine izin vereceğimiz zannedilmesin. "
    "Mustafa Suphi ve Şefik Hüsnü'nün adları anıldı; TKP'nin 1920'deki kuruluşu "
    "bugün hâlâ tartışılıyor. "
    "Bu yönelimin eski dönemlerden esin kaynakları da yok değildir. "
    "Sıkışıklığı aşabilecek miyiz, yoksa aynı yerde mi sayacağız?"
)

# Language-specific voices first: these are the ones likely to get Turkish
# stress right, and the only ones that accept SSML for manual corrections.
WAVENET = [f"tr-TR-Wavenet-{s}" for s in "ABCDE"]
STANDARD = [f"tr-TR-Standard-{s}" for s in "ABCDE"]
CHIRP_PICKS = ["Despina", "Kore", "Charon", "Aoede", "Leda"]


def synth(voice, text, auth, path):
    headers, params = auth
    body = {
        'input': {'text': text},
        'voice': {'languageCode': tc.LANGUAGE_CODE, 'name': voice},
        'audioConfig': {'audioEncoding': 'LINEAR16'},
    }
    response = requests.post(tc.CHIRP_ENDPOINT, headers=headers, params=params,
                             json=body, timeout=120)
    if response.status_code != 200:
        message = response.json().get('error', {}).get('message', response.text[:200])
        print(f"  {voice:<30} ERROR {response.status_code}: {message[:90]}")
        return False

    wav = base64.b64decode(response.json()['audioContent'])
    with open(path, 'wb') as f:
        f.write(wav)
    return True


ELEVEN_CARRIER = "Bu konuda {} ile ilgili yeni bir açıklama yapıldı."


def eleven_synth(text, path):
    """One short ElevenLabs render with the production voice and settings."""
    body = {"text": text, "model_id": tc.ELEVEN_MODEL,
            "voice_settings": tc.ELEVEN_SETTINGS, "seed": tc.ELEVEN_SEED}
    response = requests.post(f"{tc.ELEVEN_ENDPOINT}/{tc.ELEVEN_VOICE}",
                             params={"output_format": "mp3_44100_128"},
                             headers={"xi-api-key": tc.elevenlabs_key(),
                                      "accept": "audio/mpeg"},
                             json=body, timeout=120)
    if response.status_code != 200:
        print(f"  ERROR {response.status_code}: {response.text[:120]}")
        return False
    with open(path, 'wb') as f:
        f.write(response.content)
    return True


def eleven_initialisms(tokens, confirmed):
    """Render each initialism as written and as spelled, for an ear decision."""
    from tr_readings import SPELLED

    tokens = tokens or sorted(SPELLED)
    unknown = [t for t in tokens if t not in SPELLED]
    if unknown:
        print(f"not in the SPELLED table: {', '.join(unknown)}")
        return 1
    if not tc.elevenlabs_key():
        print("no ElevenLabs key")
        return 1

    renders = [(t, form, ELEVEN_CARRIER.format(text))
               for t in tokens for form, text in (('harf', t), ('spelled', SPELLED[t]))]
    cost = sum(len(text) for *_, text in renders)
    print(f"{len(tokens)} initialisms x 2 forms = {len(renders)} renders, ~{cost:,} ElevenLabs characters")
    if not confirmed:
        print("This spends credit. Add --yes to confirm.")
        return 0

    out = os.path.join(OUT_DIR, "eleven_initialisms")
    os.makedirs(out, exist_ok=True)
    for token, form, text in renders:
        path = os.path.join(out, f"{token}_{form}.mp3")
        if eleven_synth(text, path):
            print(f"  {token:<6} {form:<8} {text}")
    print(f"\noutput: {out}/ - put the preferred form in eleven_prep.ELEVEN_SPELLED.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="A/B Turkish TTS voices")
    parser.add_argument('--text', default=SAMPLE)
    parser.add_argument('--all-chirp', action='store_true',
                        help='render all 30 Chirp 3 voices, not just five')
    parser.add_argument('--only', nargs='*', help='render just these voice names')
    parser.add_argument('--eleven-initialisms', nargs='*', metavar='TOKEN',
                        help='A/B initialisms on ElevenLabs: as written vs spelled '
                             '(no tokens = the whole SPELLED table)')
    parser.add_argument('--yes', action='store_true',
                        help='actually spend the ElevenLabs credit')
    args = parser.parse_args()

    if args.eleven_initialisms is not None:
        return eleven_initialisms(args.eleven_initialisms, args.yes)

    auth = tc.auth_headers_and_params()
    if auth == (None, None):
        return 1

    if args.only:
        voices = args.only
    else:
        chirp = tc.list_all_chirp_names() if args.all_chirp else \
            [f"tr-TR-Chirp3-HD-{n}" for n in CHIRP_PICKS]
        voices = WAVENET + STANDARD + chirp

    os.makedirs(OUT_DIR, exist_ok=True)
    chars = len(args.text)
    print(f"{len(voices)} voices x {chars} characters = {len(voices) * chars:,} characters")
    print(f"output: {OUT_DIR}/\n")

    ok = 0
    for voice in voices:
        path = os.path.join(OUT_DIR, f"{voice}.wav")
        if synth(voice, args.text, auth, path):
            print(f"  {voice:<30} {os.path.getsize(path) / 1024:6.0f} KB")
            ok += 1

    print(f"\n{ok}/{len(voices)} rendered. Listen, pick one, then run:")
    print("  python tts_converter.py --force <id> --voice <voice-name>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
