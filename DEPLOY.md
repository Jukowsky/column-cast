# Deploying as a daily job

The pipeline runs without a browser (plain HTTP + `requests`), and its only
binary dependency is the static ffmpeg bundled inside `imageio-ffmpeg`. All you
need is a Linux box with Python 3.10+.

## What runs on Hostinger and what doesn't

| Plan | Status |
|---|---|
| **VPS (KVM)** | ✅ The supported setup. Ubuntu + root; the script below installs everything. The smallest KVM plan is enough — the job takes ~5-10 minutes a day and uses a ~250 MB venv plus a few hundred MB of media. |
| Shared / Cloud hosting | ❌ Does not work. There is no persistent Python runtime, binary packages can't be installed with pip, and long-running processes get killed. |

If you're on a shared plan, your options are: upgrade to a VPS, or use a
serverless scheduler (a nightly GitHub Actions cron) — the code is the same
either way.

## Setup - one command

From your own machine, at the project root (Git Bash):

```bash
bash deploy/push.sh root@<vps>
```

The script, in order:

1. Generates an SSH key if you don't have one and installs it on the server (this is the last password prompt)
2. Packs the code (excluding secrets and media) and unpacks it on the server
3. `install.sh` - packages, the `redcast` service user, venv, systemd timer, logrotate
4. `install_minio.sh` - MinIO server, bucket, scoped access key
5. Uploads the texts (`articles/txt`) - the server produces the audio and video from these itself
6. Uploads the secrets and `state.json`, and locks down their permissions
7. `podcast.py reconcile` - matches the ledger against the files that actually exist on the server
8. `healthcheck.sh` - verifies everything is up

Idempotent: run it again whenever the code changes. The ledger, secrets and
archived media are never overwritten.

## MinIO archive

Every column's audio, text and metadata are written to MinIO. Local media is
only a working copy; it is deleted once the retention window passes, and the
permanent copy lives in MinIO.

```
audio/<file>.mp3         narration
text/<file>.txt          text fed to TTS
metadata/<id>.json       title, author, date, source, voice, YouTube link
state/state.json         the ledger itself
```

The API is bound **only to 127.0.0.1:9000** - nothing besides the pipeline on
this machine needs to reach it, so an accidentally public bucket is impossible.
To look at it from your own computer, open a tunnel:

```bash
ssh -L 9001:127.0.0.1:9001 root@<vps>
# then open http://127.0.0.1:9001 in a browser
```

**Versioning is enabled** on the bucket - when `state.json` is overwritten, the
old copy is kept. The root credentials are in `/etc/default/minio` (chmod 600);
the application uses a separate key that is authorized only for the `redcast`
bucket (`/opt/redcast/minio_config.json`).

To check the state of the archive:

```bash
cd /opt/redcast && sudo -u redcast .venv/bin/python sol_storage.py check
```

## The one manual step: authorization

OAuth opens a browser, and the server has none. So you generate the token on
your own machine and copy it up:

```bash
# On your own computer
python youtube_uploader.py auth

# To the server
scp token.json client_secrets.json root@<vps>:/opt/redcast/
ssh root@<vps> 'chown redcast:redcast /opt/redcast/{token,client_secrets}.json && \
                chmod 600 /opt/redcast/{token,client_secrets}.json'
```

> **Your Google Auth Platform app must be in Production.** In Testing mode Google
> revokes the refresh token after 7 days; the timer silently stops publishing and
> you only find out when you notice there are no new videos on the channel.

## Playlist and video title

All episodes go into a single playlist: **`Köşe Yazıları`**. (Previously a
separate list was created for each day; those lists still exist and their
contents were not moved - each episode carries its own `playlist_id`, so it is
not added again.)

The video title has this form:

```
Aydemir Güler - Komünizm Cumhuriyetin neresindeydi? - 29 Ağustos 2026
```

The author comes first: it is the most distinctive and most searched-for token
on the channel. In the middle is the column's own title, i.e. the topic
keywords. The date goes last; it makes every episode unique and signals
freshness, but doesn't take up the first words.

The column title gets a budget of **60 characters**; anything longer is
shortened with `...`, and the cut lands on a word boundary. The arithmetic:
author name at most 19, longest date 15, two separators 6 characters - 40 in
total, which leaves exactly 60 of YouTube's 100-character limit. The median
title in the corpus is 32, so most episodes are never shortened.

Turning the playlist into a YouTube **podcast** is done by hand in Studio
(Content > Playlists > menu > Set as podcast) - the Data API has no equivalent.

## Privacy setting — decide after setup

`deploy/redcast.service` currently ships with **`--privacy public`**, so videos
go live immediately. If you want to review them first, switch to `private`:

```bash
sudo sed -i 's/--privacy public/--privacy private/' /etc/systemd/system/redcast.service
sudo systemctl daemon-reload
```

## Google TTS credentials

Narration uses Google Cloud TTS **Chirp 3: HD**. Note: this API **does not accept API keys** (`401 - API keys are not supported by this API`); it requires a service account.

1. Console > IAM & Admin > Service Accounts > **Create service account**
2. Role: **Cloud Text-to-Speech User**
3. After creating it > Keys > Add key > Create new key > **JSON** > download
4. Make sure the **Text-to-Speech API** is enabled in the project

Put the file on the server:

```bash
scp service_account.json root@<vps>:/opt/redcast/
ssh root@<vps> 'chown redcast:redcast /opt/redcast/service_account.json &&                 chmod 600 /opt/redcast/service_account.json'
```

The code looks for it as `service_account.json` or `gcp_service_account.json` and mints the bearer token itself; no secret is ever written on the command line.

To list the available Turkish voices:

```bash
cd /opt/redcast && sudo -u redcast .venv/bin/python tts_converter.py --list-voices
```

The default voice is `tr-TR-Chirp3-HD-Despina`. To change it, add `--voice <name>` to the service file.

## Two engines, a monthly budget

Narration is split **automatically** between two engines (`--engine auto`):

1. Every column goes to ElevenLabs until the month's **ElevenLabs budget** is used up (voice: Irem)
2. Once the budget runs out, the remaining columns fall back to **Google Chirp 3** - the job doesn't stop and no extra charge is incurred

**At most 1 column a day** goes to ElevenLabs (`ELEVENLABS_MAX_PER_DAY`), even
when budget is available. Most days produce 2-4 columns; without this cap, a
single morning run could spend a week's worth of budget.

The two caps work together, and both are checked before the call. The budget
is a single constant in `tts_converter.py`: **`ELEVENLABS_MONTHLY_BUDGET = 103_000`**
characters. The number comes from the plan, not from past usage: Creator grants
121,000 credits, Multilingual v2 spends one credit per character, and a 15%
margin is kept. The measured average column is 7,299 characters, so this comes
to **~14 columns a month**.

The decision is made per column, **before** the call; the budget is a ceiling
that cannot be exceeded, not one that is noticed after the fact. A column is
never split across two engines.

The counter is kept by the calendar day/month on which the column was narrated
(not its publication date) - billing follows usage. It resets on its own when
the month rolls over.

**Result:** 1 column a day comes to ~219,000 characters a month, while the
budget is 103,000. So ElevenLabs runs out in the first ~14 days of the month,
and the remaining days are all Google. If you'd rather spread it over the whole
month, keep `ELEVENLABS_MAX_PER_DAY` at 1 but run it every other day - or move
to the Pro plan.

**Without a key**, all columns quietly go to Google; the system doesn't raise an error.

To see the status:

```bash
cd /opt/redcast && sudo -u redcast .venv/bin/python podcast.py cost
```

Put the key at the project root as `elevenlabs_key.txt`; `deploy/push.sh`
carries it to the server and `.gitignore` keeps it out of the repo.

To check whether the key is valid and which permissions it has, run
`python check_key.py` - it never prints the key. A key with only the
text-to-speech permission is enough; if the quota can't be read, the budget is
still counted from our own ledger, but you'll need to keep an eye on the
ElevenLabs quota in the dashboard.

## EMA Lightning comparison copy

Every column that goes to ElevenLabs is also narrated with **EMA Lightning**
as the **very last step** of the pipeline (`ema_compare.py`). This copy is
**not published**: YouTube gets the ElevenLabs audio, and the EMA audio is kept
only for comparison.

- Local: `articles/compare/ema/<file>.mp3`
- MinIO: `compare/ema/<file>.mp3` (the real audio lives under `audio/`, so they don't mix)
- Ledger: `ema_audio_file`, `ema_seconds` (generation time), `ema_audio_seconds`

EMA is free and runs offline (~6x real time on 8 cores, ~1-2 minutes per
column; an estimated ~10 minutes on a single-core VPS). On the first run, all
past ElevenLabs columns will have piled up; each run processes at most 2 of
them and the rest wait for later nights. The step runs last: if the service's
45-minute limit is exceeded, only this step is cut off, and publishing and
archiving have already finished. The EMA copy goes to MinIO during the next
night's archive step. `podcast.py prune` does not touch these files.

The step is non-critical: if it fails, publishing carries on. If
`ema-lightning` is not installed, the step is skipped. To turn it off, use
`run_pipeline.py --skip-ema`.

The model weights (~35 MB) are downloaded from Hugging Face on the first run
and kept under `/opt/redcast/.cache/huggingface` - the service runs with
`ProtectHome`, so it can't write to the home directory. `install.sh` installs
the CPU build of torch; the Linux build from PyPI ships with CUDA and is
several GB.

To regenerate a column by hand:

```bash
cd /opt/redcast && sudo -u redcast HF_HOME=/opt/redcast/.cache/huggingface .venv/bin/python ema_compare.py --force <article_id>
```

## What the daily run executes

```
run_pipeline.py --since-days 2 --engine auto --upload --privacy public --upload-limit 6 --retain-days 30
```

- `--since-days 2` — columns published in the last two days. **Not `--today`:** the job runs at 06:00, and at that hour the day has no columns yet; a "today only" filter would come back empty every morning. The two-day window covers yesterday's columns (complete by 06:00) plus a night's margin; the ledger already prevents duplicate processing.
- `--upload-limit 6` — the YouTube daily quota is 10,000 units and an upload costs 1600 → at most 6 a day
- `--retain-days 30` — audio and video files are **kept on disk for 1 month** after publishing, then deleted. At the measured rate (~2 columns/day) the steady state is ~690 MB. Text files and `state.json` are never deleted.

The retention window exists so you can republish or audit without paying for TTS again. No file that hasn't reached YouTube yet is ever deleted, however old it is.

To see what would be deleted:

```bash
cd /opt/redcast && sudo -u redcast .venv/bin/python podcast.py prune --days 30 --dry-run
```

Every stage checks `state.json`, so re-running is harmless: no column is
narrated twice or uploaded twice. Anything held back by the quota continues
automatically the next day.

## Operations

```bash
systemctl list-timers redcast.timer      # next run
sudo systemctl start redcast.service     # run once now
journalctl -u redcast.service -n 50      # last run
tail -f /opt/redcast/logs/pipeline.log
sudo -u redcast /opt/redcast/.venv/bin/python /opt/redcast/podcast.py status
```

Logs rotate weekly and are kept for 8 weeks (`/etc/logrotate.d/redcast`).

## Updating the code

```bash
cd ~/newsTTS_Sol && git pull
sudo bash deploy/install.sh      # idempotent; doesn't touch state or secrets
```

## Text normalization

Turkish separates proper nouns from their suffixes with an apostrophe
(`Cumhuriyet'in`, `TKP'nin`, `1920'de`), and Chirp 3 reads these by splitting
the word in two. `text_normalize.py` joins them before narration —
`Cumhuriyetin` — which is how the word is actually pronounced anyway, so it's a
fix, not an approximation.

It is applied in two places: when the scraper writes the text (the `.txt` on
disk is exactly the text that will be read) and when `tts_converter` reads it
(so that a hand-edited or older text doesn't reach the engine with apostrophes).
The function is idempotent; applying it twice is harmless.

Besides apostrophes, [tr_readings.py](tr_readings.py) also fixes the following
— all of them measured cases, not assumptions:

| Rule | Example |
|---|---|
| Abbreviations | `TKP` → Te Ka Pe, `ABD` → A Be De (`NATO`, `DİSK` stay as words) |
| Number + suffix | `1920'de` → bin dokuz yüz yirmide |
| Decimal / thousands | `2,5'i` → iki virgül beşi, `1.000'e` → bine |
| Ordinals | `104. madde` → yüz dördüncü madde |
| Roman numerals | `II.` → İkinci |
| Percent | `%50` → yüzde 50 |
| Slash | `akıllı/deli` → akıllı ya da deli |
| Abbreviated words | `vb.` → ve benzeri |
| Ellipsis, repeated punctuation | `…` `?!?` → collapsed to one |

The abbreviation table lives in the `SPELLED` (read letter by letter) and
`AS_WORD` (read as a word) dictionaries. When a new abbreviation shows up, it is
**not** guessed automatically — spelling out an acronym that is read as a word
makes things worse. Instead, unknown ones are reported and added to the table
by hand.

Invisible characters (such as U+2060), curly quotes and long dashes are cleaned
up in the same step. The title, description and video card are **not
affected** — punctuation stays as-is there.

To fix existing texts in bulk:

```bash
cd /opt/redcast && sudo -u redcast .venv/bin/python text_normalize.py     --dir "articles/txt/*.txt" --dry-run     # see what would change first
```

## Date trap: article page vs index

soL publishes columns late in the evening and dates them to the **next day**.
While the article page says `Yayın Tarihi: 29.08.2026 , 21:44`, the site's own
index shows the same column as **30 Ağustos**. The index date is the correct
one; the stamp on the article page is the actual posting time.

That's why `published` is taken **from the index page**, not from the article
page. The actual posting time is stored in the `posted_at` field. For columns
published in the morning the two dates match, so the rule is not "add one
day" - it's whatever the index says.

Using the wrong field shifts every daily playlist name back by one day.

## Time zone trap

The server's system clock is **UTC**. systemd timers evaluate `OnCalendar`
against the system clock; `Environment=TZ` in the service file only affects the
process, **not** the schedule. That's why the time zone is written explicitly
on the `OnCalendar` line:

```
OnCalendar=*-*-* 06:00:00 Europe/Istanbul
```

Without it the job would run on UTC — a three-hour shift. `systemctl list-timers`
shows UTC; for 06:00 Istanbul, **03:00 UTC** is the correct value.

## Local copy is code, server is data

The whole application runs on the server. The local folder is only a **code
working copy** - none of the generated files live there:

| | Where |
|---|---|
| Code | local + `/opt/redcast` |
| Text, audio, video | only `/opt/redcast/articles` |
| Archive (audio, text, metadata) | only MinIO |
| `state.json` (ledger) | **server only** |
| Secrets | both (needed locally to re-authorize with a browser) |

Two mechanisms enforce this:

- **The `PRODUCTION` marker.** `install.sh` drops it into `/opt/redcast`.
  `youtube_uploader.py` **refuses** to upload if this file is missing, so a run
  started locally by mistake can't re-upload the whole archive to the channel.
- **`push.sh` doesn't overwrite the ledger.** If `state.json` exists on the
  server, it is left alone; it is seeded from the local copy only if there is
  none at all. Pushing an old local ledger up would mean re-uploading columns
  that were already published.

## What to back up

`/opt/redcast/state.json` — the only record of which columns have been
published. If it's lost, the system re-narrates everything and **uploads
duplicates to YouTube**. `articles/txt/` is small too; back it up alongside.

```bash
sudo tar czf redcast-backup-$(date +%F).tar.gz \
    -C /opt/redcast state.json articles/txt
```

## Troubleshooting

| Symptom | Cause |
|---|---|
| `RuntimeError: No usable TrueType font` | `sudo apt-get install -y fonts-dejavu-core` |
| `No Google TTS API key` | `/opt/redcast/google_tts_key.txt` is missing or the `redcast` user can't read it |
| `HTTP 400 ... exceeds 5000 bytes` from TTS | Chunking has been disabled; lower `MAX_REQUEST_BYTES` in `tts_converter.py` |
| `HTTP 403` from TTS | The key isn't restricted to the Text-to-Speech API, or the API isn't enabled in the project |
| `auth` needed again every week | The OAuth app is still in Testing mode → move it to Production |
| `quotaExceeded` | The 6-videos-a-day limit. No action needed; it continues the next day. |
| Scrape `403 Forbidden` | Cloudflare rejects non-browser TLS fingerprints. `curl_cffi` must be installed — `requests` alone **always** gets a 403 from the server. Adding headers doesn't help and the IP isn't the problem; it's the TLS handshake. |
| Scrape finds 0 links | The site's DOM has changed or the `User-Agent` header got dropped. The `UA` constant in `sol_column_scraper.py` is required. |
| Timer doesn't run | `systemctl status redcast.timer`, then `journalctl -u redcast.service` |
| `audio missing` error | The ledger thinks the file exists. Run `podcast.py reconcile` — it resets the stage of the missing output, and it is regenerated on the next run. |
| MinIO is down | `systemctl status minio`, `journalctl -u minio -n 50`. Data is in `/var/lib/minio/data`. |
| Archive looks empty | `sol_storage.py check`. Credentials are in `/opt/redcast/minio_config.json` and the `redcast` user must be able to read them. |
