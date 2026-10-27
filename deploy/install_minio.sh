#!/usr/bin/env bash
# Install MinIO as the durable archive for the podcast pipeline.
#
#   sudo bash deploy/install_minio.sh
#
# Idempotent: re-running leaves existing data, credentials and objects alone.
#
# The S3 API is bound to 127.0.0.1 on purpose. The pipeline runs on this same
# host, so there is no reason to expose object storage to the internet - that
# removes the whole class of "public bucket" accidents and needs no firewall
# rule. To reach it from your laptop, tunnel instead:
#     ssh -L 9000:127.0.0.1:9000 root@<vps>

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/redcast}"
APP_USER="${APP_USER:-redcast}"
MINIO_USER="minio-user"
MINIO_DATA="/var/lib/minio/data"
MINIO_ADDR="127.0.0.1:9000"
MINIO_CONSOLE="127.0.0.1:9001"
BUCKET="redcast"
CRED_FILE="/etc/default/minio"

[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }

echo "==> MinIO binaries"
for bin in minio mc; do
    if [[ ! -x "/usr/local/bin/${bin}" ]]; then
        case "${bin}" in
            minio) url="https://dl.min.io/server/minio/release/linux-amd64/minio" ;;
            mc)    url="https://dl.min.io/client/mc/release/linux-amd64/mc" ;;
        esac
        echo "    downloading ${bin}"
        curl -fsSL "${url}" -o "/usr/local/bin/${bin}"
        chmod +x "/usr/local/bin/${bin}"
    else
        echo "    ${bin} already installed"
    fi
done

echo "==> Service user and data directory"
id -u "${MINIO_USER}" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin "${MINIO_USER}"
mkdir -p "${MINIO_DATA}"
chown -R "${MINIO_USER}:${MINIO_USER}" /var/lib/minio

echo "==> Root credentials"
if [[ ! -f "${CRED_FILE}" ]]; then
    ROOT_USER="redcast-root"
    ROOT_PASS="$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-32)"
    cat > "${CRED_FILE}" <<EOF
MINIO_ROOT_USER=${ROOT_USER}
MINIO_ROOT_PASSWORD=${ROOT_PASS}
MINIO_VOLUMES="${MINIO_DATA}"
MINIO_OPTS="--address ${MINIO_ADDR} --console-address ${MINIO_CONSOLE}"
EOF
    chmod 600 "${CRED_FILE}"
    echo "    generated new root credentials"
else
    echo "    keeping existing ${CRED_FILE}"
fi
# shellcheck disable=SC1090
source "${CRED_FILE}"

echo "==> systemd unit"
cat > /etc/systemd/system/minio.service <<EOF
[Unit]
Description=MinIO object storage (redcast archive)
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
User=${MINIO_USER}
Group=${MINIO_USER}
EnvironmentFile=${CRED_FILE}
ExecStart=/usr/local/bin/minio server \$MINIO_OPTS \$MINIO_VOLUMES
Restart=always
RestartSec=5s
LimitNOFILE=65536
TimeoutStopSec=30s

NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/minio
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now minio.service

echo "==> Waiting for MinIO to accept connections"
for i in $(seq 1 30); do
    if curl -fsS "http://${MINIO_ADDR}/minio/health/live" >/dev/null 2>&1; then
        echo "    up after ${i}s"; break
    fi
    [[ $i -eq 30 ]] && { echo "    MinIO did not come up"; journalctl -u minio -n 30 --no-pager; exit 1; }
    sleep 1
done

echo "==> Bucket and application credentials"
export MC_HOST_local="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@${MINIO_ADDR}"
mc mb --ignore-existing "local/${BUCKET}" >/dev/null
# Versioning keeps an old copy of state.json when it is overwritten - that file
# is the only record of what has already been published.
mc version enable "local/${BUCKET}" >/dev/null 2>&1 || true

APP_CONFIG="${APP_DIR}/minio_config.json"
if [[ ! -f "${APP_CONFIG}" ]]; then
    APP_KEY="redcast-app"
    APP_SECRET="$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-32)"

    # A key scoped to this one bucket, rather than the root account.
    POLICY=$(mktemp)
    cat > "${POLICY}" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow",
     "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
     "Resource": ["arn:aws:s3:::${BUCKET}"]},
    {"Effect": "Allow",
     "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
     "Resource": ["arn:aws:s3:::${BUCKET}/*"]}
  ]
}
EOF
    mc admin policy create local redcast-rw "${POLICY}" >/dev/null 2>&1 || true
    mc admin user add local "${APP_KEY}" "${APP_SECRET}" >/dev/null
    mc admin policy attach local redcast-rw --user "${APP_KEY}" >/dev/null 2>&1 || true
    rm -f "${POLICY}"

    cat > "${APP_CONFIG}" <<EOF
{
  "endpoint": "http://${MINIO_ADDR}",
  "access_key": "${APP_KEY}",
  "secret_key": "${APP_SECRET}",
  "bucket": "${BUCKET}",
  "secure": false
}
EOF
    chown "${APP_USER}:${APP_USER}" "${APP_CONFIG}"
    chmod 600 "${APP_CONFIG}"
    echo "    created scoped application key -> ${APP_CONFIG}"
else
    echo "    keeping existing ${APP_CONFIG}"
fi

echo
echo "MinIO ready."
echo "  API      http://${MINIO_ADDR}   (localhost only)"
echo "  bucket   ${BUCKET}  (versioning on)"
echo "  data     ${MINIO_DATA}"
echo "  root creds in ${CRED_FILE} (chmod 600)"
echo
echo "Browse from your laptop with a tunnel:"
echo "  ssh -L 9001:127.0.0.1:9001 root@<vps>   then open http://127.0.0.1:9001"
