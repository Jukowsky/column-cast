---
name: publish-podcast
description: Runs and troubleshoots the daily soL column podcast - scrape, Chirp 3 TTS, MP4 render, YouTube upload, MinIO archive - and reports what landed. Use when asked to publish the podcast, upload today's columns, run the daily job, check pipeline status, or diagnose why a run failed. Turkish triggers - podcast yayınla, youtube'a yükle, günlük yayını yap, bekleyen yüklemeleri gönder, podcast durumu, arşiv kontrol.
---

# soL Column Podcast

`haber.sol.org.tr/kose-yazilari` → Chirp 3 TTS → title-card MP4 → YouTube → MinIO archive.

The production server is a **Hostinger VPS** (address in the `deploy/target` file), the app lives in `/opt/redcast`, the service user is `redcast`, and it runs every morning at **06:00 Europe/Istanbul** (`redcast.timer`).

**Always run commands with the venv and from inside `/opt/redcast`:**
`cd /opt/redcast && sudo -u redcast .venv/bin/python <script>`
Locally use `.venv/Scripts/python.exe`. Never use the base conda environment — its protobuf version conflicts with tensorflow.

## Fixed rules

**The same column is never published twice.** `state.json` is the single source of truth; each column is keyed by the permanent numeric id at the end of its URL and has five stages: `scraped / tts / video / uploaded / stored`. Every script skips its own stage when it's done. Don't derive the to-do list yourself — use `podcast.py status` or `youtube_uploader.py upload --dry-run`.

**Two engines, a monthly budget.** `--engine auto`: the Irem voice is used
until the month's ElevenLabs budget (`ELEVENLABS_MONTHLY_BUDGET`, 103,000
characters ≈ 14 columns) is used up, with **at most 1 column a day**
(`ELEVENLABS_MAX_PER_DAY`); after that, Google Chirp 3. The decision is made
per column before the call, so the budget can't be exceeded. Without a key
everything falls back to Google without an error.
`podcast.py cost` shows the budget status.

**Re-narrating costs money.** Chirp 3 is billed per character. Use `--force` only when it's really needed.

**The only copy is never deleted.** `podcast.py prune` deletes a file only if it exists on YouTube **and** in MinIO.

## Daily job

```bash
cd /opt/redcast && sudo systemctl start redcast.service
journalctl -u redcast.service -f
tail -f /opt/redcast/logs/pipeline.log
```

The command the timer runs:

```
run_pipeline.py --since-days 2 --engine auto --upload --privacy public --upload-limit 6 --retain-days 30
```

Stages: scrape → TTS → MP4 → website → YouTube → MinIO archive → clean up local media older than 30 days.

## Before uploading

Uploads are hard to undo. Show what will go out and get confirmation:

```bash
sudo -u redcast .venv/bin/python youtube_uploader.py upload --dry-run
```

If the user said "upload all of them", the confirmation covers the whole batch.

## Channel guard

`channel.json` pins the identity of the target channel. If the token points at a different channel, the upload is **refused** — one Google account can own several channels and the OAuth screen can silently pick the wrong one. If the change is intentional, use `--allow-channel-change`.

Switching channels requires `youtube_uploader.py auth`; it opens a browser, so **the user must run it on their own machine** — it can't be completed in a headless session.

## Playlist and title

A single playlist: **`Köşe Yazıları`**. The old daily lists still exist, but new
episodes don't go there.

Title: `Yazar - Yazi basligi - 3 Ekim 2026` (author - column title - date). The
column title gets 60 characters; anything longer is shortened with `...`. The
constants are in `youtube_meta.py` (`MAX_COLUMN_TITLE`), the playlist name in
`youtube_uploader.py` (`PLAYLIST_TITLE`).

## Quota

An upload costs 1600 units out of 10,000 a day → **6 videos a day**. The measured publishing rate is ~2 a day, so no backlog is expected. `quotaExceeded` is a limit, not an error; the rest wait in the ledger and go out automatically the next day.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `audio missing` | The ledger thinks the file exists. `podcast.py reconcile` — resets the stage of the missing output, and it is regenerated on the next run. It doesn't touch files that were pruned on purpose (`media_cleaned`). |
| No new columns coming in | The site's DOM may have changed. Selectors: `div.article--yazar`, `h1.node-title`, `div.node-body`. The `UA` constant in `sol_column_scraper.py` is required — without it the site returns an empty shell. |
| TTS `HTTP 401 API keys are not supported` | Cloud TTS doesn't accept API keys. `/opt/redcast/service_account.json` is required (role: Cloud Text-to-Speech User). |
| TTS `HTTP 400 exceeds 5000 bytes` | Chunking is broken; lower `MAX_REQUEST_BYTES` in `tts_converter.py`. |
| The same column is back in the upload list | An upload was interrupted. Search YouTube; if it's there, `podcast.py mark-uploaded <id> <videoId>`. |
| `auth` needed again every week | The OAuth app is in Testing mode; Google Auth Platform → Audience → Publish app. |
| MinIO unreachable | `systemctl status minio`; `sol_storage.py check`. The API listens only on `127.0.0.1:9000`; use an SSH tunnel for outside access. |
| Words are read split in two | An apostrophe is left in the text (`Cumhuriyet'in`). Clean it with `text_normalize.py --dir "articles/txt/*.txt"`; the scraper and tts_converter already do this automatically. |
| TTS `sentences that are too long` | A sentence exceeded ~880 characters, either in the source or after normalization. `tr_readings.split_long_sentences` splits at 600; this happens if the rule is disabled or an abbreviation expansion swallowed the sentence-ending period. |
| Nothing gets published because of one column | Shouldn't happen: steps exit with 2 on partial failure and the pipeline carries on. If you see exit 1, the step couldn't run at all (credentials, configuration). |
| Font error | `apt-get install -y fonts-dejavu-core` |

## Deployment

After a code change, one command from the user's machine:

```bash
bash deploy/push.sh
```

At the end `healthcheck.sh` runs and verifies everything is up. `state.json`, secrets and the archive are never overwritten.
