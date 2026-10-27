# sol-tts

A daily podcast pipeline. It reads the opinion columns published at
[haber.sol.org.tr/kose-yazilari](https://haber.sol.org.tr/kose-yazilari),
narrates them in Turkish with ElevenLabs and Google Cloud TTS, renders each one as a video,
publishes it to YouTube in a playlist for that day, and archives the audio and
transcript to MinIO.

It runs unattended, once every morning.

```
scrape → normalise → TTS → MP4 → YouTube → MinIO → prune → EMA comparison copy
```

Every column keeps its author's byline, the publication date, a note that the
narration is synthetic, and a link back to the original article.

---

## How it works

| Stage | Script | What it does |
|---|---|---|
| scrape | `sol_column_scraper.py` | Reads the column index, fetches anything new, stores the transcript verbatim |
| TTS | `tts_converter.py` | ElevenLabs while the monthly budget lasts, then Google Chirp 3: HD (two Turkish voices in rotation) |
| render | `mp3_to_video.py` | Muxes the audio with a generated title card into an H.264/AAC MP4 |
| publish | `youtube_uploader.py` | Resumable upload, then adds the episode to that day's playlist |
| archive | `sol_storage.py` | Audio, transcript and metadata to MinIO (S3-compatible) |
| prune | `podcast.py prune` | Deletes local media after the retention window |
| compare | `ema_compare.py` | Re-voices ElevenLabs columns with EMA Lightning, offline; never published |
| orchestration | `run_pipeline.py` | Runs the stages in order |

Supporting modules: `sol_state.py` (the ledger), `text_normalize.py` and
`tr_readings.py` (Turkish text preparation), `youtube_meta.py` (titles and
descriptions), `podcast.py` (inspection and manual repair).

`scraper.py`, `scrape_sol_fulltext.py` and `generate_website.py` predate the
podcast and target the news feed rather than the columns. Only the website
generator is still wired in, as a by-product.

---

## The ledger

`state.json` is the single source of truth, keyed by the numeric id at the end
of every column URL (`.../turkiyenin-rejimi-nedir-413294` → `413294`). Each
entry carries five stage stamps:

```
scraped → tts → video → uploaded → stored
```

Every stage skips what it has already done, so **re-running is always safe**:
nothing is narrated twice (which would cost money), and nothing is published
twice (which cannot be undone quietly). Filenames deliberately contain no run
timestamp, so a repeated run overwrites rather than accumulates.

The ledger belongs to the server and is not in this repository. A checkout with
a stale copy would re-upload the whole archive.

---

## Turkish text preparation

Chirp 3 accepts no SSML, so every pronunciation fix has to be a plain-text
rewrite. Each rule below was added because the pattern was measured in the
corpus, not because it might occur.

| Problem | Example | Rewritten as |
|---|---|---|
| Apostrophe before a case suffix | `Cumhuriyet'in` | `Cumhuriyetin` |
| Initialisms | `TKP`, `ABD` | `Te Ka Pe`, `A Be De` |
| Numbers carrying a suffix | `1920'de` | `bin dokuz yüz yirmide` |
| Ordinals | `104. madde` | `yüz dördüncü madde` |
| Roman numerals | `II.` | `İkinci` |
| Percent, decimals, thousands | `%50`, `2,5'i`, `1.000'e` | `yüzde 50`, `iki virgül beşi`, `bine` |
| Invisible characters, curly quotes, dashes | U+2060, `…`, `—` | removed or simplified |
| Clock times | `14.30'da` | `on dört otuzda` |
| Ranges (ascending, no leading zero) | `2019-2023`, `10–15 Ekim` | `2019 ila 2023`, `10 ila 15 Ekim` |
| Bare separators | `1.250.000`, `2,5 milyar` | `bir milyon iki yüz elli bin`, `iki buçuk milyar` |
| Currency | `500 TL'lik`, `$5`, `10 milyon €` | `500 liralık`, `5 dolar`, `10 milyon avro` |
| Fractions (denominator ≤ 10) | `3/4'ü`, `1/2 asır` | `dörtte üçü`, `yarım asır` |
| Reference marks | `Bkz. s. 45`, `M.Ö.` | `Bakınız sayfa 45`, `Milattan önce` |
| Links and handles | `https://sol.org.tr/...`, `@x`, `&` | `sol nokta org nokta tr`, `x`, `ve` |

The rows above `Clock times` were measured in the corpus; the rest were added
from known Turkish conventions and are covered by `tests/test_readings.py`
(`python -m unittest discover tests`).

### ElevenLabs layer

`eleven_prep.py` runs the shared rules and then prepares the text for how
ElevenLabs delivers it, which differs from Chirp 3:

- every line ends in punctuation, so headings are not read as unfinished;
- `<break>` pauses between paragraphs, longer after the title and byline,
  at most four per request (more destabilises the model; v3 gets none);
- requests of about 2,000 characters, cut on sentences, each sent with
  `previous_text`, `next_text` and `previous_request_ids` so tone carries over
  the seams, plus a fixed `seed`;
- its own initialism table, `ELEVEN_SPELLED`, empty until an A/B listen
  (`python compare_voices.py --eleven-initialisms --yes`) says otherwise.

The monthly budget counts what ElevenLabs bills - its prepared text, break
tags included. `python eleven_prep.py <txt>` shows the requests it would send.

Two details worth knowing:

- **Turkish separators are the reverse of English.** The dot groups thousands
  and the comma marks the decimal, so `1.000` is *bin*, not *bir virgül sıfır*.
- **Unknown initialisms are left alone and reported, never guessed.** Spelling
  out an acronym that is read as a word (`NATO`, `DİSK`) makes it worse, and the
  letters alone do not tell you which kind you have. New ones are added to the
  table by hand.

Normalisation happens at synthesis time, not at scrape time. Rewriting the
stored transcript would destroy the original tokens and no later correction to
the tables could ever reach them.

---

## EMA Lightning comparison

[EMA Lightning](https://huggingface.co/canberkkkkkk/ema-lightning) is a small
Turkish-only TTS model that runs offline on the CPU, at roughly six times real
time. It costs nothing per character, so it is a candidate to replace the paid
voice - but only once it has been heard on the same material.

`ema_compare.py` therefore narrates every column that went to ElevenLabs a
second time, from the same transcript and through the same Turkish rules as
Chirp 3. The copy is **never published**:

- Local: `articles/compare/ema/<file>.mp3`
- MinIO: `compare/ema/<file>.mp3`, apart from `audio/`, which feeds YouTube
- Ledger: `ema_audio_file`, `ema_seconds` (generation time), `ema_audio_seconds`

It runs as the last, non-critical step of the daily job, at most two columns a
night, so a slow single-core server can only ever cut off the comparison, never
the upload or the archive. Without the `ema-lightning` package the step reports
that and exits cleanly; `run_pipeline.py --skip-ema` turns it off. Prune leaves
the copies alone. Server setup (CPU-only torch, model cache) is in `DEPLOY.md`.

---

## Dates

The date of a column comes from the **index page**, not the article page. sol
posts columns late in the evening and dates them for the following day: the
article shows `Yayın Tarihi: 29.08.2026 , 21:44` while the index calls the same
piece 30 Ağustos. Taking the article timestamp puts every column, and every
daily playlist, one day early.

The index date goes in `published`; the actual posting stamp is kept as
`posted_at`. This is not a blanket +1 — a column posted at 10:03 has the same
date in both places.

---

## Setup

Requires Python 3.10+ on Linux. No browser and no system packages beyond fonts:
the site is server-rendered, and `imageio-ffmpeg` ships its own ffmpeg binary.

```bash
pip install -r requirements.txt
```

### Credentials

| File | For | Notes |
|---|---|---|
| `service_account.json` | Google Cloud TTS | Role: *Cloud Text-to-Speech User*. **The API rejects plain API keys** — it needs a service account. |
| `client_secrets.json` + `token.json` | YouTube Data API v3 | OAuth desktop client. Run `python youtube_uploader.py auth` once, in a browser. |
| `minio_config.json` | MinIO archive | Written by `deploy/install_minio.sh`. |

All are gitignored. Publish the Google Auth Platform app to **Production**:
in Testing mode Google revokes refresh tokens after seven days, which stops a
daily job silently.

Playlist management needs the broad `auth/youtube` scope. `youtube.upload`
alone returns 403; widening the scope requires a fresh interactive consent.

### Deployment

```bash
bash deploy/push.sh user@host        # or put the host in deploy/target
```

Ships the code, installs the venv and systemd units, sets up MinIO, reconciles
the ledger against the files actually present, and runs a health check. It is
idempotent, and never overwrites the ledger, the credentials or the archive.

The daily unit runs:

```
run_pipeline.py --since-days 2 --upload --privacy public --upload-limit 6 --retain-days 30
```

`--since-days 2` rather than "today": the job runs at 06:00, when the current
day has no columns yet, so a today-only filter would process nothing every
morning. The two-day window covers yesterday plus a night of slack, and the
ledger prevents any double processing.

---

## Operating

```bash
python podcast.py status        # every column and the stages it has cleared
python podcast.py cost          # characters billed and quota spent, by month
python podcast.py reconcile     # clear stage flags whose output file is missing
python sol_storage.py check     # archive connectivity and object counts
python ema_compare.py --force <id>  # re-render one column's EMA comparison copy
bash deploy/healthcheck.sh      # everything the 06:00 run depends on
```

### Limits worth knowing

- **YouTube upload quota**: 1600 units per video out of 10,000 a day, so six
  videos. The measured publication rate is about two a day.
- **YouTube playlist creation** is rate-limited over hours, not seconds.
  Deleting and recreating playlists exhausts it; the run leaves the day
  unrecorded and the next one picks it up.
- **TTS request size** is 5,000 **bytes**, and Turkish is ~11% larger in UTF-8
  than its character count. Text is split on paragraph, then sentence, then word
  boundaries, and the pieces are concatenated before a single MP3 encode.
- **TTS billing** is per character. `state.json` records `tts_billed_chars` per
  column, which is what `podcast.py cost` reports.

### Safeguards

- Uploads refuse to run without a `PRODUCTION` marker file, so a developer
  checkout cannot duplicate the channel.
- The target channel id is pinned in `channel.json`; a token pointing elsewhere
  is refused rather than published to.
- Local media is deleted only when the video is on YouTube **and** the audio is
  in MinIO.

---

## Licence and attribution

The columns belong to their authors. This pipeline only
narrates them: it does not alter, abridge or comment on the text, and every
episode credits the author and links to the original. Takedown requests are
honoured.
