"""
Uploads rendered columns to YouTube with the Data API v3.

Chosen over the Zapier MCP route because this runs unattended: MCP needs an
agent session, and Zapier's `video` field is a file field that rejects a local
path outright ("Media type 'text/plain' is not supported"), so that route would
also need the MP4 published to a public URL first. This uploads the local file
directly, resumably, straight from cron.

Driven by state.json: only columns whose 'uploaded' stage is empty are sent, so
a column is never published twice.

First-time setup (once):
    1. console.cloud.google.com -> new project -> enable "YouTube Data API v3"
    2. Google Auth Platform -> Audience -> External, then either add the uploading
       Google account under "Test users", or better, click "Publish app".
       Testing mode revokes refresh tokens after 7 days, which breaks a daily
       cron every week; Production keeps them alive. Publishing needs no Google
       verification for personal use - you just click through the "unverified
       app" warning once (Advanced -> Go to <app> (unsafe)).
    3. Credentials -> Create OAuth client ID -> Desktop app -> download JSON
    4. Save it next to this file as client_secrets.json
    5. python youtube_uploader.py auth      (opens a browser once, writes token.json)

Daily use:
    python youtube_uploader.py upload                     # every pending column
    python youtube_uploader.py upload --privacy private
    python youtube_uploader.py upload --limit 3           # stay inside the quota
    python youtube_uploader.py upload --id 413293

Quota: each upload costs 1600 units of the default 10,000/day, so roughly six
videos a day before Google starts refusing. --limit exists for that reason.
"""

import argparse
import json
import os
import random
import sys
import time

import googleapiclient.errors
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

import sol_state
import youtube_meta

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly",
          # Creating playlists and adding episodes to them needs the broader
          # manage scope; youtube.upload alone returns 403 insufficientPermissions.
          # Adding it here does not upgrade an existing token - the user has to
          # run `auth` again in a browser for the new consent to take effect.
          "https://www.googleapis.com/auth/youtube"]

REAUTH_HINT = (
    "Playlist operations need a broader scope the current token lacks.\n"
    "  On your own machine:  python youtube_uploader.py auth\n"
    "  then copy token.json to the server. Uploads keep working meanwhile.")
CLIENT_SECRETS = "client_secrets.json"
TOKEN_FILE = "token.json"

# Which channel this install is allowed to publish to. A Google account can own
# several channels (brand accounts), and the OAuth flow silently picks whichever
# one was chosen in the consent screen - so a re-auth can start publishing to the
# wrong channel with no error at all. Pinning the id turns that into a refusal.
CHANNEL_FILE = "channel.json"

# Transient upload failures worth retrying; anything else is a real error.
RETRIABLE_STATUS = {500, 502, 503, 504}
MAX_RETRIES = 8


def granted_scopes():
    """The scopes the stored token actually holds - not the ones we now want.

    Refreshing with a scope the token was never granted fails outright
    ("invalid_scope"), which would take the uploads down too. Widening SCOPES
    must therefore degrade to "playlists don't work yet", never to "nothing
    works", so the refresh is always asked for what the user already consented
    to and the extra scope only arrives with the next interactive auth.
    """
    try:
        with open(TOKEN_FILE, encoding='utf-8') as f:
            stored = json.load(f).get('scopes')
        return stored or SCOPES
    except (OSError, json.JSONDecodeError):
        return SCOPES


def has_manage_scope():
    return "https://www.googleapis.com/auth/youtube" in granted_scopes()


def get_credentials(interactive=False, force_consent=False):
    """Load the stored token, or run the browser flow.

    `force_consent` exists because a still-valid token used to short-circuit the
    `auth` command entirely: it printed "Authorised as ..." without opening a
    browser, so a newly requested scope could never be granted. Re-authorising
    has to mean re-authorising.
    """
    creds = None
    if os.path.exists(TOKEN_FILE) and not force_consent:
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, granted_scopes())

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            # An app left in "Testing" on the OAuth consent screen has its
            # refresh tokens revoked after 7 days, which silently kills a cron
            # job every week. Publishing the app to Production fixes it for good.
            print(f"Refresh token rejected ({exc}).", file=sys.stderr)
            print("Re-authorise with: python youtube_uploader.py auth", file=sys.stderr)
            print("If this keeps happening weekly, the Google Auth Platform app is still "
                  "in Testing mode - publish it to Production so tokens stop expiring.",
                  file=sys.stderr)
            return None
    # `elif`, not `if`: a successful refresh above must return the credentials,
    # not fall through into this chain and hit the "not authorised" branch. That
    # regression silently stopped every upload once the access token expired,
    # while the rest of the pipeline carried on looking healthy.
    # force_consent needs no separate branch here - it leaves `creds` as None
    # above, so control reaches this point anyway.
    elif interactive or force_consent:
        if not os.path.exists(CLIENT_SECRETS):
            print(f"{CLIENT_SECRETS} not found - see the setup steps at the top of this file.",
                  file=sys.stderr)
            return None
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS, SCOPES)
        creds = flow.run_local_server(port=0, prompt='consent')
    else:
        print("Not authorised yet. Run: python youtube_uploader.py auth", file=sys.stderr)
        return None

    with open(TOKEN_FILE, 'w', encoding='utf-8') as f:
        f.write(creds.to_json())
    return creds


def upload_one(youtube, video_path, meta):
    """Resumable upload. Returns the new video id, or None on failure."""
    body = {
        'snippet': {
            'title': meta['title'],
            'description': meta['description'],
            'tags': meta['tags'],
            'categoryId': meta['category_id'],
            'defaultLanguage': meta['default_language'],
            'defaultAudioLanguage': meta['default_audio_language'],
        },
        'status': {
            'privacyStatus': meta['privacy_status'],
            'selfDeclaredMadeForKids': meta['made_for_kids'] == 'true',
        },
    }

    media = MediaFileUpload(video_path, chunksize=4 * 1024 * 1024, resumable=True,
                            mimetype='video/mp4')
    request = youtube.videos().insert(part='snippet,status', body=body, media_body=media)

    response = None
    attempt = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                print(f"        {int(status.progress() * 100):3d}%", end='\r')
        except googleapiclient.errors.HttpError as exc:
            if exc.resp.status not in RETRIABLE_STATUS:
                print(f"\n      ! HTTP {exc.resp.status}: {exc}")
                return None
            attempt += 1
            if attempt > MAX_RETRIES:
                print("\n      ! gave up after repeated 5xx responses")
                return None
            sleep = random.uniform(1, 2 ** attempt)
            print(f"\n      . HTTP {exc.resp.status}, retry {attempt} in {sleep:.1f}s")
            time.sleep(sleep)
        except (OSError, IOError) as exc:
            attempt += 1
            if attempt > MAX_RETRIES:
                print(f"\n      ! network error, gave up: {exc}")
                return None
            time.sleep(random.uniform(1, 2 ** attempt))

    return response.get('id')


def set_thumbnail(youtube, video_id, thumbnail_path):
    try:
        youtube.thumbnails().set(videoId=video_id,
                                 media_body=MediaFileUpload(thumbnail_path)).execute()
        return True
    except googleapiclient.errors.HttpError as exc:
        # Custom thumbnails need a verified channel; not fatal for the upload.
        print(f"      . thumbnail skipped: {exc.resp.status}")
        return False


def current_channel(youtube):
    """The channel the credentials actually publish to. Costs 1 quota unit."""
    items = youtube.channels().list(part='snippet', mine=True).execute().get('items', [])
    if not items:
        return None
    return {'id': items[0]['id'], 'title': items[0]['snippet']['title']}


def load_pinned_channel():
    if not os.path.exists(CHANNEL_FILE):
        return None
    with open(CHANNEL_FILE, encoding='utf-8') as f:
        return json.load(f)


def pin_channel(channel):
    with open(CHANNEL_FILE, 'w', encoding='utf-8') as f:
        json.dump(channel, f, ensure_ascii=False, indent=2)


def check_channel(youtube, allow_change=False):
    """Refuse to upload if the token points at a different channel than pinned."""
    actual = current_channel(youtube)
    if not actual:
        print("Could not read the authorised channel.", file=sys.stderr)
        return None

    pinned = load_pinned_channel()
    if pinned is None or allow_change:
        pin_channel(actual)
        if pinned and pinned['id'] != actual['id']:
            print(f"Channel changed: {pinned['title']} -> {actual['title']}")
        else:
            print(f"Channel pinned: {actual['title']} ({actual['id']})")
        return actual

    if pinned['id'] != actual['id']:
        print(f"REFUSING TO UPLOAD - wrong channel.\n"
              f"  pinned : {pinned['title']} ({pinned['id']})\n"
              f"  token  : {actual['title']} ({actual['id']})\n"
              f"If the change is intentional, re-run with --allow-channel-change.",
              file=sys.stderr)
        return None

    return actual


# One playlist for the whole series, rather than one per publication day. The
# old daily lists are left as they are; their episodes are added to the series
# playlist as well, so it holds the complete archive.
PLAYLIST_TITLE = "Köşe Yazıları"
PLAYLIST_KEY = "series"


def ensure_playlist(youtube, state, privacy):
    """The series playlist, created once and remembered.

    Checked against the channel as well as the ledger: if state.json were ever
    restored from an older backup, going by the ledger alone would create a
    second playlist with the same name.
    """
    cache = state.setdefault('playlists', {})
    if PLAYLIST_KEY in cache:
        return cache[PLAYLIST_KEY]

    title = PLAYLIST_TITLE
    existing = youtube.playlists().list(part='snippet', mine=True,
                                        maxResults=50).execute()
    for item in existing.get('items', []):
        if item['snippet']['title'] == title:
            cache[PLAYLIST_KEY] = item['id']
            print(f"      . playlist found: {title}")
            return item['id']

    # Creating playlists back to back trips YouTube's rate limiter (429), so
    # this backs off the same way the item insert does. If it never clears, the
    # day simply stays unrecorded and the next run picks it up.
    body = {'snippet': {'title': title,
                        'description': "haber.sol.org.tr'de yayımlanan köşe "
                                       "yazılarının Türkçe yapay ses ile "
                                       "seslendirilmiş halleri. Her gün yeni bölüm.",
                        'defaultLanguage': 'tr'},
            'status': {'privacyStatus': privacy}}
    for attempt in range(1, 4):
        try:
            created = youtube.playlists().insert(part='snippet,status', body=body).execute()
            break
        except googleapiclient.errors.HttpError as exc:
            # 409 is a moment's propagation delay and worth waiting out. 429 is
            # an account-wide limit measured in hours - it is re-raised for the
            # caller to abandon the whole pass rather than retried per item.
            if exc.resp.status != 409 or attempt == 3:
                raise
            wait = 3 * attempt
            print(f"      . playlist create HTTP 409, retrying in {wait}s")
            time.sleep(wait)
    cache[PLAYLIST_KEY] = created['id']
    print(f"      + playlist created: {title}")
    return created['id']


def insert_playlist_item(youtube, playlist_id, video_id, attempts=6):
    """Add one video, retrying the 409 a freshly created playlist returns.

    Two transient statuses, both seen in practice:
      409 - a playlist is not immediately writable after playlists.insert.
      429 - YouTube rate-limits playlist writes when a batch runs back to back.
    Both clear on their own, so they are waited out rather than reported.
    """
    for attempt in range(1, attempts + 1):
        try:
            return youtube.playlistItems().insert(
                part='snippet',
                body={'snippet': {'playlistId': playlist_id,
                                  'resourceId': {'kind': 'youtube#video',
                                                 'videoId': video_id}}}).execute()
        except googleapiclient.errors.HttpError as exc:
            if exc.resp.status not in (409, 429) or attempt == attempts:
                raise
            wait = 2 ** attempt
            print(f"      . HTTP {exc.resp.status}, retrying in {wait}s")
            time.sleep(wait)


def playlist_video_ids(youtube, playlist_id):
    ids, token = set(), None
    while True:
        page = youtube.playlistItems().list(part='contentDetails', playlistId=playlist_id,
                                            maxResults=50, pageToken=token).execute()
        ids.update(i['contentDetails']['videoId'] for i in page.get('items', []))
        token = page.get('nextPageToken')
        if not token:
            return ids


def sync_playlists(youtube, state, privacy):
    """Put every published column into the series playlist.

    A separate pass rather than a step inside the upload loop, so a column
    whose playlist insert failed is picked up on the next run instead of being
    stranded. Episodes filed in the old per-day playlists are added too; they
    stay in their daily list as well.
    """
    todo = [e for e in state['articles'].values()
            if e.get('stages', {}).get('uploaded')
            and e.get('youtube_video_id')
            and e.get('playlist_title') != PLAYLIST_TITLE]
    if not todo:
        return 0

    if not has_manage_scope():
        print("\nPlaylist: " + REAUTH_HINT)
        return 0

    print(f"\nPlaylist: {len(todo)} episode(s) to add")
    done = 0
    present = None
    for entry in sorted(todo, key=lambda e: e.get('published') or ''):
        try:
            playlist_id = ensure_playlist(youtube, state, privacy)
            # The ledger can lag the channel (an insert that landed but was
            # never recorded), so the playlist itself is asked once, and a
            # video already in it is recorded rather than added twice.
            if present is None:
                present = playlist_video_ids(youtube, playlist_id)
            if entry['youtube_video_id'] not in present:
                insert_playlist_item(youtube, playlist_id, entry['youtube_video_id'])
                present.add(entry['youtube_video_id'])
        except googleapiclient.errors.HttpError as exc:
            # These two fail identically for every remaining episode, so say it
            # once and stop instead of repeating the same error per item - and,
            # for the rate limit, instead of burning a minute of backoff each.
            if exc.resp.status == 403 and 'insufficient' in str(exc).lower():
                print("  ! " + REAUTH_HINT)
                return done
            if exc.resp.status == 429:
                print(f"  ! YouTube playlist quota exhausted (429). {len(todo) - done} episode(s) "
                      f"waiting; the next run adds them once the limit resets.")
                return done
            print(f"  ! {entry['id']} could not be added: HTTP {exc.resp.status}")
            continue
        entry['playlist_id'] = playlist_id
        entry['playlist_title'] = PLAYLIST_TITLE
        sol_state.save(state)
        done += 1
        print(f"  ok {entry['id']}  {PLAYLIST_TITLE}")
    return done


def free_local_media(entry, articles_dir):
    """Delete the mp3/mp4/png once YouTube holds the video.

    Only ever called after the ledger records a video id, so nothing that still
    needs uploading can be deleted. The .txt transcript stays - it is tiny, and
    it is the only local copy of what was actually spoken.
    """
    freed = 0
    for key in ('audio_file', 'video_file', 'thumbnail_file'):
        rel = entry.get(key)
        if not rel:
            continue
        path = os.path.join(articles_dir, rel)
        if os.path.exists(path):
            freed += os.path.getsize(path)
            os.remove(path)
    if freed:
        entry['media_cleaned'] = True
        print(f"      . {freed / (1024 * 1024):.1f} MB of local media deleted")


def cmd_auth(args):
    creds = get_credentials(interactive=True, force_consent=True)
    if not creds:
        return 1
    youtube = build('youtube', 'v3', credentials=creds)
    channel = current_channel(youtube)
    if not channel:
        print("Authorised, but no YouTube channel is attached to this account.", file=sys.stderr)
        return 1

    previous = load_pinned_channel()
    print(f"Authorised as: {channel['title']}  ({channel['id']})")
    if previous and previous['id'] != channel['id']:
        print(f"NOTE: this replaces the previously pinned channel '{previous['title']}'. "
              f"Uploads from now on go to '{channel['title']}'.")
    pin_channel(channel)
    print(f"Token saved to {TOKEN_FILE} - keep it out of version control.")

    missing = [s for s in SCOPES if s not in granted_scopes()]
    if missing:
        print("\nWARNING: these scope(s) were not granted:", file=sys.stderr)
        for s in missing:
            print("   " + s, file=sys.stderr)
        print("Tick all of them on the consent screen. If a scope never shows up there, "
              "add it under Console > Google Auth Platform > Data Access.", file=sys.stderr)
        return 1
    print("All scopes granted; playlist management enabled.")
    return 0


# Publishing happens on the server and nowhere else. install.sh drops this
# marker into /opt/redcast; a developer checkout does not have it. Without the
# check, running the pipeline on a laptop whose ledger is empty or stale would
# cheerfully re-upload the whole archive as duplicates.
PRODUCTION_MARKER = "PRODUCTION"


def is_production():
    return os.path.exists(PRODUCTION_MARKER)


def cmd_upload(args):
    if not args.dry_run and not is_production() and not args.allow_non_production:
        print("Refusing to upload: this is not the production host.\n"
              "  Publishing runs on the server (/opt/redcast), which carries a "
              "PRODUCTION marker file.\n"
              "  Uploading from a developer checkout risks duplicating the whole "
              "archive on the channel.\n"
              "  Deliberate exception: --allow-non-production",
              file=sys.stderr)
        return 1

    state = sol_state.load()
    todo = sol_state.pending(state, 'uploaded', requires='video')
    if args.id:
        todo = [e for e in todo if e['id'] == args.id]
    if args.limit:
        todo = todo[:args.limit]

    print("=" * 70)
    print(f"YouTube upload: {len(todo)} pending  (privacy: {args.privacy})")
    print("=" * 70)
    # An empty upload queue is not the end of the run: episodes published before
    # the playlist feature existed, or whose insert failed, still need placing.
    if not todo and (args.dry_run or args.no_playlist):
        print("  nothing to upload")
        return 0

    youtube = None
    if not args.dry_run:
        creds = get_credentials()
        if not creds:
            return 1
        youtube = build('youtube', 'v3', credentials=creds)
        if not check_channel(youtube, allow_change=args.allow_channel_change):
            return 1

    ok = 0
    for i, entry in enumerate(todo, 1):
        video_path = os.path.join(args.dir, entry.get('video_file', ''))
        if not entry.get('video_file') or not os.path.exists(video_path):
            print(f"[{i}/{len(todo)}] {entry['id']}  ! video missing")
            continue

        meta = youtube_meta.build(entry)
        meta['privacy_status'] = args.privacy
        print(f"[{i}/{len(todo)}] {meta['title'][:64]}")
        print(f"        {os.path.getsize(video_path) / (1024 * 1024):.1f} MB")

        if args.dry_run:
            print("        (dry run, not uploaded)")
            continue

        video_id = upload_one(youtube, video_path, meta)
        if not video_id:
            print("      ! upload failed, will be retried next run")
            continue

        # Record before the thumbnail: the video is live, and a thumbnail
        # failure must never cause a duplicate upload on the next run.
        entry['youtube_video_id'] = video_id
        entry['youtube_url'] = f"https://www.youtube.com/watch?v={video_id}"
        entry['youtube_privacy'] = args.privacy
        sol_state.mark(state, entry['id'], 'uploaded')
        sol_state.save(state)
        ok += 1
        print(f"      ok  {entry['youtube_url']}")

        thumb = os.path.join(args.dir, entry.get('thumbnail_file', ''))
        if entry.get('thumbnail_file') and os.path.exists(thumb):
            set_thumbnail(youtube, video_id, thumb)

        if args.cleanup:
            free_local_media(entry, args.dir)
            sol_state.save(state)

    if args.dry_run:
        print(f"\n{'=' * 70}\n  dry run: {len(todo)} would be uploaded\n{'=' * 70}")
        return 0

    if not args.no_playlist:
        sync_playlists(youtube, state, args.privacy)

    sol_state.export_index(state, args.dir)
    print(f"\n{'=' * 70}\n  {ok}/{len(todo)} uploaded\n{'=' * 70}")
    return 0 if ok == len(todo) else 1


def cmd_playlist(args):
    """File every published episode into the series playlist, uploading nothing."""
    state = sol_state.load()
    creds = get_credentials()
    if not creds:
        return 1
    youtube = build('youtube', 'v3', credentials=creds)
    if not check_channel(youtube, allow_change=False):
        return 1
    sync_playlists(youtube, state, args.privacy)
    left = [e for e in state['articles'].values()
            if e.get('youtube_video_id') and e.get('playlist_title') != PLAYLIST_TITLE]
    print(f"\n{len(left)} episode(s) still outside '{PLAYLIST_TITLE}'")
    return 0 if not left else 2


def main():
    parser = argparse.ArgumentParser(description="Upload rendered columns to YouTube")
    parser.add_argument('--dir', default='articles')
    sub = parser.add_subparsers(dest='cmd', required=True)

    sub.add_parser('auth', help='run the one-time OAuth flow and store token.json')

    up = sub.add_parser('upload', help='upload every column that is rendered but not yet published')
    up.add_argument('--privacy', default='private', choices=['private', 'unlisted', 'public'],
                    help='default private - flip to public once a run looks right')
    up.add_argument('--limit', type=int, help='stop after N uploads (quota is ~6/day)')
    up.add_argument('--id', metavar='ARTICLE_ID', help='upload only this article id')
    up.add_argument('--dry-run', action='store_true', help='list what would be uploaded')
    up.add_argument('--no-playlist', action='store_true',
                    help=f"skip adding published episodes to '{PLAYLIST_TITLE}'")
    up.add_argument('--allow-non-production', action='store_true',
                    help='publish from a machine without the PRODUCTION marker '
                         '(normally only the server may upload)')
    up.add_argument('--allow-channel-change', action='store_true',
                    help='accept that the token now points at a different channel '
                         'and re-pin it (guards against publishing to the wrong channel)')
    up.add_argument('--cleanup', action='store_true',
                    help='delete the local mp3/mp4/png after a successful upload '
                         '(the transcript and the ledger are kept)')

    pl = sub.add_parser('playlist', help=f"add every published episode to '{PLAYLIST_TITLE}' "
                                         f"without uploading anything")
    pl.add_argument('--privacy', default='public', choices=['private', 'unlisted', 'public'],
                    help='privacy of the playlist if it has to be created')

    args = parser.parse_args()
    return {'auth': cmd_auth, 'upload': cmd_upload, 'playlist': cmd_playlist}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
