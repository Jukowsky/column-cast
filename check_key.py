"""Is the stored ElevenLabs credential usable? Never prints the key.

Checks each capability separately: a key can be scoped to synthesis alone, in
which case the quota is unreadable but the pipeline still works.
"""
import sys
import requests
import tts_converter as tc

key = tc.elevenlabs_key()
if not key:
    print("no key found"); sys.exit(1)
print(f"length {len(key)}, starts with 'sk_': {key.startswith('sk_')}")
if not key.startswith('sk_'):
    print("\n! This looks like an API key ID, not the key itself.")
    print("  ElevenLabs > Settings > API Keys > Rotate / Create")
    sys.exit(1)

H = {"xi-api-key": key}
ok = True


def show(label, r, body=None):
    if r.status_code == 200:
        print(f"  {label:<22} OK")
        return r.json() if body is None else body(r)
    detail = ''
    try:
        detail = r.json().get("detail", {}).get("message", "")[:90]
    except Exception:
        detail = r.text[:90]
    print(f"  {label:<22} HTTP {r.status_code}  {detail}")
    return None


print("\npermissions:")
sub = show("quota read", requests.get(
    "https://api.elevenlabs.io/v1/user/subscription", headers=H, timeout=60))

voice = show("voice access", requests.get(
    f"https://api.elevenlabs.io/v1/voices/{tc.ELEVEN_VOICE}", headers=H, timeout=60))

models = show("model list", requests.get(
    "https://api.elevenlabs.io/v1/models", headers=H, timeout=60))

# The one that actually matters. Three characters, so the cost is nil.
r = requests.post(f"{tc.ELEVEN_ENDPOINT}/{tc.ELEVEN_VOICE}",
                  params={"output_format": "mp3_44100_128"},
                  headers={**H, "accept": "audio/mpeg"},
                  json={"text": "Merhaba.", "model_id": tc.ELEVEN_MODEL,
                        "voice_settings": tc.ELEVEN_SETTINGS}, timeout=120)
if r.status_code == 200:
    print(f"  {'SYNTHESIS':<22} OK  ({len(r.content):,} bytes of audio produced)")
else:
    ok = False
    try:
        msg = r.json().get("detail", {}).get("message", "")[:160]
    except Exception:
        msg = r.text[:160]
    print(f"  {'SYNTHESIS':<22} HTTP {r.status_code}  {msg}")

if sub:
    used, cap = sub.get("character_count", 0), sub.get("character_limit", 0)
    print(f"\nplan : {sub.get('tier')}")
    print(f"quota: {used:,} / {cap:,} characters" + (f" (%{used / cap * 100:.0f})" if cap else ""))
    if cap and tc.ELEVENLABS_MONTHLY_BUDGET > cap:
        print("! budget exceeds the plan quota - lower ELEVENLABS_MONTHLY_BUDGET")
else:
    print("\nquota unreadable (the key lacks the user_read permission).")
    print("Not a problem: the budget is counted from our own ledger; to check it,")
    print("look at the ElevenLabs dashboard.")

if voice:
    print(f"\nvoice: {voice.get('name')}  ({voice.get('category')})")

if models:
    for m in models:
        if m.get("model_id") == tc.ELEVEN_MODEL:
            langs = [l.get("language_id") for l in m.get("languages", [])]
            print(f"model: {m.get('name')} | turkish: {'tr' in langs} | "
                  f"request limit: {m.get('maximum_text_length_per_request')} characters")

sys.exit(0 if ok else 1)
