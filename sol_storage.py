"""
Archives each column's audio, transcript and metadata to MinIO (S3-compatible).

MinIO is the durable copy. Local media is only a working cache that `podcast.py
prune` clears after the retention window, so the archive is what makes that
deletion safe - prune refuses to touch anything this module has not stored yet.

Bucket layout:
    audio/<filename>.mp3
    text/<filename>.txt
    metadata/<article_id>.json
    compare/ema/<filename>.mp3   EMA Lightning copy of an ElevenLabs column,
                                 for comparison only (ema_compare.py)

Configuration, in order of precedence:
    environment  MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY,
                 MINIO_BUCKET, MINIO_SECURE
    file         minio_config.json  {"endpoint": "...", "access_key": "...",
                                     "secret_key": "...", "bucket": "redcast"}

Usage:
    python sol_storage.py sync           # archive everything not yet archived
    python sol_storage.py sync --force   # re-upload even if already archived
    python sol_storage.py check          # connectivity + bucket + object count
"""

import argparse
import json
import os
import sys
from datetime import datetime

import sol_state

CONFIG_FILE = "minio_config.json"
DEFAULT_BUCKET = "redcast"

CONTENT_TYPES = {'.mp3': 'audio/mpeg', '.txt': 'text/plain; charset=utf-8',
                 '.json': 'application/json', '.mp4': 'video/mp4', '.png': 'image/png'}


def load_config():
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding='utf-8-sig') as f:
            cfg = json.load(f)

    endpoint = os.environ.get('MINIO_ENDPOINT') or cfg.get('endpoint')
    access = os.environ.get('MINIO_ACCESS_KEY') or cfg.get('access_key')
    secret = os.environ.get('MINIO_SECRET_KEY') or cfg.get('secret_key')
    bucket = os.environ.get('MINIO_BUCKET') or cfg.get('bucket') or DEFAULT_BUCKET
    secure = os.environ.get('MINIO_SECURE', str(cfg.get('secure', False))).lower() in ('1', 'true', 'yes')

    if not (endpoint and access and secret):
        return None
    if not endpoint.startswith(('http://', 'https://')):
        endpoint = ('https://' if secure else 'http://') + endpoint
    return {'endpoint': endpoint, 'access_key': access, 'secret_key': secret,
            'bucket': bucket}


def client(cfg):
    import boto3
    from botocore.config import Config

    return boto3.client(
        's3',
        endpoint_url=cfg['endpoint'],
        aws_access_key_id=cfg['access_key'],
        aws_secret_access_key=cfg['secret_key'],
        # MinIO ignores regions but botocore insists on one being set.
        region_name='us-east-1',
        config=Config(signature_version='s3v4', retries={'max_attempts': 5, 'mode': 'standard'}),
    )


def ensure_bucket(s3, bucket):
    from botocore.exceptions import ClientError
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError:
        s3.create_bucket(Bucket=bucket)


def put_file(s3, bucket, local_path, key):
    ext = os.path.splitext(local_path)[1].lower()
    s3.upload_file(local_path, bucket, key,
                   ExtraArgs={'ContentType': CONTENT_TYPES.get(ext, 'application/octet-stream')})
    return os.path.getsize(local_path)


def build_metadata(entry):
    """The record that outlives the media files."""
    return {
        'article_id': entry.get('id'),
        'title': entry.get('title'),
        'author': entry.get('author'),
        'spot': entry.get('spot'),
        'published': entry.get('published'),
        'source_url': entry.get('url'),
        'word_count': entry.get('word_count'),
        'filename': entry.get('filename'),
        'tts_engine': entry.get('tts_engine'),
        'tts_voice': entry.get('tts_voice'),
        'tts_billed_chars': entry.get('tts_billed_chars'),
        'youtube_video_id': entry.get('youtube_video_id'),
        'youtube_url': entry.get('youtube_url'),
        'previous_youtube': entry.get('previous_youtube'),
        'stages': entry.get('stages'),
        'objects': {
            'audio': f"audio/{entry['filename']}.mp3" if entry.get('audio_file') else None,
            'text': f"text/{entry['filename']}.txt" if entry.get('txt_file') else None,
            'ema_audio': entry.get('ema_audio_file'),
        },
        'ema_seconds': entry.get('ema_seconds'),
        'archived_at': datetime.now().isoformat(timespec='seconds'),
    }


def cmd_sync(args):
    cfg = load_config()
    if not cfg:
        print(f"MinIO not configured. Create {CONFIG_FILE} or set MINIO_ENDPOINT / "
              f"MINIO_ACCESS_KEY / MINIO_SECRET_KEY.", file=sys.stderr)
        return 1

    s3 = client(cfg)
    ensure_bucket(s3, cfg['bucket'])
    state = sol_state.load()

    archived = refreshed = ema_archived = 0
    total_bytes = 0
    for entry in sorted(state['articles'].values(), key=lambda e: e.get('published') or ''):
        if not entry.get('stages', {}).get('tts'):
            continue                                # nothing to archive yet

        already = entry.get('stages', {}).get('stored') and not args.force
        if not already:
            uploaded_any = False
            for local_rel, prefix, ext in (
                    (entry.get('audio_file'), 'audio', '.mp3'),
                    (entry.get('txt_file'), 'text', '.txt')):
                if not local_rel:
                    continue
                path = os.path.join(args.dir, local_rel)
                if not os.path.exists(path):
                    continue
                key = f"{prefix}/{entry['filename']}{ext}"
                total_bytes += put_file(s3, cfg['bucket'], path, key)
                uploaded_any = True
            if uploaded_any:
                sol_state.mark(state, entry['id'], 'stored')
                archived += 1
                print(f"  archived  {entry['id']}  {entry.get('title', '')[:46]}")

        # Its own stage, not part of 'stored': the EMA copy is made after the
        # column is voiced, often on a later night while the backlog drains.
        ema_rel = entry.get('ema_audio_file')
        if ema_rel and (args.force or not entry.get('stages', {}).get('ema_stored')):
            path = os.path.join(args.dir, ema_rel)
            if os.path.exists(path):
                total_bytes += put_file(s3, cfg['bucket'], path, ema_rel)
                sol_state.mark(state, entry['id'], 'ema_stored')
                ema_archived += 1

        # Metadata is tiny and changes after upload, so it is always refreshed.
        s3.put_object(Bucket=cfg['bucket'], Key=f"metadata/{entry['id']}.json",
                      Body=json.dumps(build_metadata(entry), ensure_ascii=False,
                                      indent=2).encode('utf-8'),
                      ContentType='application/json')
        refreshed += 1

    sol_state.save(state)

    # The ledger itself belongs in the archive too - losing it means re-uploading
    # every column to YouTube.
    if os.path.exists(sol_state.STATE_FILE):
        put_file(s3, cfg['bucket'], sol_state.STATE_FILE, 'state/state.json')

    print(f"\n  {archived} newly archived, {ema_archived} EMA copies, {refreshed} metadata refreshed, "
          f"{total_bytes / (1024 * 1024):.1f} MB sent")
    print(f"  bucket: {cfg['bucket']} @ {cfg['endpoint']}")
    return 0


def cmd_check(args):
    cfg = load_config()
    if not cfg:
        print("MinIO not configured.", file=sys.stderr)
        return 1
    try:
        s3 = client(cfg)
        ensure_bucket(s3, cfg['bucket'])
        counts, size = {}, 0
        token = None
        while True:
            kwargs = {'Bucket': cfg['bucket'], 'MaxKeys': 1000}
            if token:
                kwargs['ContinuationToken'] = token
            page = s3.list_objects_v2(**kwargs)
            for obj in page.get('Contents', []):
                counts[obj['Key'].split('/')[0]] = counts.get(obj['Key'].split('/')[0], 0) + 1
                size += obj['Size']
            if not page.get('IsTruncated'):
                break
            token = page.get('NextContinuationToken')
    except Exception as exc:
        print(f"MinIO unreachable: {exc}", file=sys.stderr)
        return 1

    print(f"MinIO OK  {cfg['endpoint']}  bucket={cfg['bucket']}")
    for prefix in sorted(counts):
        print(f"  {prefix:<10} {counts[prefix]:>4} objects")
    print(f"  total      {size / (1024 * 1024):.1f} MB")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Archive columns to MinIO")
    parser.add_argument('--dir', default='articles')
    sub = parser.add_subparsers(dest='cmd', required=True)
    sync = sub.add_parser('sync', help='archive audio, transcripts and metadata')
    sync.add_argument('--force', action='store_true', help='re-upload already archived items')
    sub.add_parser('check', help='verify connectivity and show what is stored')
    args = parser.parse_args()
    return {'sync': cmd_sync, 'check': cmd_check}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
