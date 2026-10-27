#!/usr/bin/env bash
# One command to put the whole thing on the server and verify it.
#
# Run from the project root on your own machine (Git Bash):
#     bash deploy/push.sh root@your-server
#
# The host can also live in deploy/target (gitignored) so it need not be typed
# every time - and so a public repository carries no server address.
#
# Idempotent. Safe to re-run after any code change - it never overwrites the
# ledger, the credentials or the archived media.

set -euo pipefail

TARGET="${1:-}"
if [[ -z "${TARGET}" && -f "$(dirname "${BASH_SOURCE[0]}")/target" ]]; then
    TARGET="$(tr -d '[:space:]' < "$(dirname "${BASH_SOURCE[0]}")/target")"
fi
if [[ -z "${TARGET}" ]]; then
    echo "usage: bash deploy/push.sh user@host   (or put it in deploy/target)" >&2
    exit 1
fi
APP_DIR="/opt/redcast"
STAGE_DIR="~/newsTTS_Sol"
# Build the archive outside the tree: writing it into the directory being
# read makes tar warn "file changed as we read it" and exit 1, which under
# set -e killed the whole deploy.
ARCHIVE="$(mktemp -d)/redcast-code.tar.gz"

cd "$(dirname "${BASH_SOURCE[0]}")/.."

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

# ---------------------------------------------------------------- ssh key
# Without a key every scp below asks for the password again. Set one up once.
if [[ ! -f ~/.ssh/id_ed25519 ]]; then
    say "Creating an SSH key (no passphrase)"
    ssh-keygen -t ed25519 -C redcast -f ~/.ssh/id_ed25519 -N "" >/dev/null
fi
if ! ssh -o BatchMode=yes -o ConnectTimeout=8 "${TARGET}" true 2>/dev/null; then
    say "Installing your public key on ${TARGET} (last password prompt)"
    ssh-copy-id -i ~/.ssh/id_ed25519.pub "${TARGET}" 2>/dev/null || \
    cat ~/.ssh/id_ed25519.pub | ssh "${TARGET}" \
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
fi
ssh -o BatchMode=yes "${TARGET}" true || { echo "SSH key auth still failing." >&2; exit 1; }

# ---------------------------------------------------------------- code
say "Packing code (secrets and media excluded)"
tar czf "${ARCHIVE}" \
    --exclude=.venv --exclude=.cache --exclude=articles --exclude=__pycache__ --exclude=.git \
    --exclude=logs --exclude='*.tar.gz' \
    --exclude=token.json --exclude=client_secrets.json \
    --exclude=service_account.json --exclude=api_key.json \
    --exclude=google_tts_key.txt --exclude=minio_config.json \
    --exclude=credentials.json --exclude=PRODUCTION \
    .
printf '    %s (%s bytes)\n' "${ARCHIVE}" "$(wc -c < "${ARCHIVE}")"

say "Uploading and unpacking"
scp -q "${ARCHIVE}" "${TARGET}:/root/redcast-code.tar.gz"
ssh "${TARGET}" "mkdir -p ${STAGE_DIR} && tar xzf /root/redcast-code.tar.gz -C ${STAGE_DIR} && rm -f /root/redcast-code.tar.gz"

say "Running installers (app, then MinIO)"
ssh "${TARGET}" "cd ${STAGE_DIR} && bash deploy/install.sh && bash deploy/install_minio.sh"

# ---------------------------------------------------------------- secrets
# Transcripts are the source of truth and are tiny. Audio is shipped too when
# it already exists locally: it was paid for per character, so re-synthesising
# it on the server would be paying twice for the same text. Video is not
# shipped - rendering it from the audio is free CPU.
say "Copying transcripts and any audio already produced"
ssh "${TARGET}" "mkdir -p ${APP_DIR}/articles/txt ${APP_DIR}/articles/audio"
if compgen -G "articles/txt/*.txt" >/dev/null; then
    scp -q articles/txt/*.txt "${TARGET}:${APP_DIR}/articles/txt/"
    printf '    %s transcripts\n' "$(ls articles/txt/*.txt | wc -l)"
else
    echo "    no transcripts yet"
fi
if compgen -G "articles/audio/*.mp3" >/dev/null; then
    scp -q articles/audio/*.mp3 "${TARGET}:${APP_DIR}/articles/audio/"
    printf '    %s audio files\n' "$(ls articles/audio/*.mp3 | wc -l)"
fi

say "Copying credentials"
for f in service_account.json token.json client_secrets.json channel.json elevenlabs_key.txt; do
    if [[ -f "${f}" ]]; then
        scp -q "${f}" "${TARGET}:${APP_DIR}/"
        printf '    sent %s\n' "${f}"
    else
        printf '    absent locally, leaving the server copy alone: %s\n' "${f}"
    fi
done

# The ledger is the one file the server owns. It records what has already been
# published; pushing an older local copy over it would make the pipeline
# re-upload columns that are already on the channel. Seed it only if the server
# has none.
if ssh "${TARGET}" "test -f ${APP_DIR}/state.json"; then
    printf '    state.json already on the server - NOT overwritten (server is authoritative)\n'
elif [[ -f state.json ]]; then
    scp -q state.json "${TARGET}:${APP_DIR}/"
    printf '    state.json seeded from local (server had none)\n'
else
    printf '    no ledger anywhere yet - the first run will create one\n'
fi
ssh "${TARGET}" "cd ${APP_DIR} && chown redcast:redcast *.json 2>/dev/null; \
                 chmod 600 service_account.json token.json client_secrets.json elevenlabs_key.txt 2>/dev/null; true"

say "Reconciling the ledger against files actually present"
ssh "${TARGET}" "cd ${APP_DIR} && sudo -u redcast .venv/bin/python podcast.py reconcile"

say "Health check"
ssh "${TARGET}" "cd ${STAGE_DIR} && bash deploy/healthcheck.sh"

rm -rf "$(dirname "${ARCHIVE}")"
