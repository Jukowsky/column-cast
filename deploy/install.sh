#!/usr/bin/env bash
# Provision the soL column podcast job on a fresh Ubuntu/Debian VPS.
#
#   sudo bash deploy/install.sh
#
# Idempotent: safe to re-run after a code update.

set -euo pipefail

APP_USER="${APP_USER:-redcast}"
APP_DIR="${APP_DIR:-/opt/redcast}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo." >&2
    exit 1
fi

echo "==> System packages"
apt-get update -qq
# fonts-dejavu-core carries the Turkish glyphs the title card needs; without it
# the render aborts rather than drawing tofu boxes.
apt-get install -y -qq python3 python3-venv python3-pip fonts-dejavu-core ca-certificates rsync

echo "==> Service user: ${APP_USER}"
if ! id -u "${APP_USER}" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "/home/${APP_USER}" --shell /usr/sbin/nologin "${APP_USER}"
fi

echo "==> Application directory: ${APP_DIR}"
mkdir -p "${APP_DIR}"
# Copy the code but never clobber runtime state or secrets already on the box.
# --delete keeps the deployed tree honest (a file removed from the repo goes
# away here too), but everything the running system owns must be protected from
# it. Without these excludes, running install.sh on its own - which DEPLOY.md
# tells you to do for a code update - deletes the credentials and the logs.
rsync -a --delete \
    --exclude '.venv/' \
    --exclude '.cache/' \
    --exclude '__pycache__/' \
    --exclude 'articles/' \
    --exclude 'logs/' \
    --exclude 'state.json' \
    --exclude 'token.json' \
    --exclude 'client_secrets.json' \
    --exclude 'service_account.json' \
    --exclude 'api_key.json' \
    --exclude 'google_tts_key.txt' \
    --exclude 'minio_config.json' \
    --exclude 'channel.json' \
    --exclude 'PRODUCTION' \
    "${REPO_DIR}/" "${APP_DIR}/"
mkdir -p "${APP_DIR}/articles" "${APP_DIR}/logs"
# Marks this checkout as the one allowed to publish to YouTube. A developer
# copy has no such file, so an accidental local run cannot duplicate the channel.
[[ -f "${APP_DIR}/PRODUCTION" ]] || date -Iseconds > "${APP_DIR}/PRODUCTION"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

echo "==> Python environment"
if [[ ! -d "${APP_DIR}/.venv" ]]; then
    sudo -u "${APP_USER}" python3 -m venv "${APP_DIR}/.venv"
fi
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install --quiet --upgrade pip
# CPU-only torch for ema-lightning, before requirements.txt pulls it in: from
# PyPI, Linux gets the CUDA build and several GB of NVIDIA libraries.
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install --quiet torch \
    --index-url https://download.pytorch.org/whl/cpu
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install --quiet -r "${APP_DIR}/requirements.txt"

echo "==> systemd units"
install -m 644 "${REPO_DIR}/deploy/redcast.service" /etc/systemd/system/redcast.service
install -m 644 "${REPO_DIR}/deploy/redcast.timer"   /etc/systemd/system/redcast.timer
systemctl daemon-reload
systemctl enable --now redcast.timer

echo "==> Log rotation"
cat > /etc/logrotate.d/redcast <<EOF
${APP_DIR}/logs/*.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}
EOF

cat <<EOF

Installed.

Still to do by hand (the OAuth step needs a browser, so it cannot run here):
  1. On your laptop:  python youtube_uploader.py auth
  2. Copy the result up:
       scp token.json client_secrets.json root@<vps>:${APP_DIR}/
       chown ${APP_USER}:${APP_USER} ${APP_DIR}/token.json ${APP_DIR}/client_secrets.json
       chmod 600 ${APP_DIR}/token.json ${APP_DIR}/client_secrets.json
  3. Make sure the Google Auth Platform app is published to Production, or the
     refresh token dies after 7 days and the timer silently stops publishing.

Useful:
  systemctl list-timers redcast.timer     # when it next fires
  systemctl start redcast.service         # run once, now
  journalctl -u redcast.service -n 50     # last run's output
  tail -f ${APP_DIR}/logs/pipeline.log
EOF
