#!/usr/bin/env bash
# Verify the daily job is actually able to run. Prints PASS/FAIL per check and
# exits non-zero if anything that would stop tonight's run is broken.
#
#     bash deploy/healthcheck.sh

APP_DIR="${APP_DIR:-/opt/redcast}"
PY="${APP_DIR}/.venv/bin/python"
fails=0

ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fails=$((fails + 1)); }
warn() { printf '  \033[33mWARN\033[0m  %s\n' "$1"; }

echo "redcast health check  $(date '+%F %T %Z')"
echo

echo "Runtime"
[[ -x "${PY}" ]] && ok "venv python" || bad "venv missing at ${PY}"
if [[ -x "${PY}" ]]; then
    missing=$("${PY}" - <<'EOF'
mods = {'requests':'requests','bs4':'beautifulsoup4','PIL':'pillow',
        'imageio_ffmpeg':'imageio-ffmpeg','googleapiclient':'google-api-python-client',
        'google.oauth2':'google-auth','boto3':'boto3'}
out = []
for m, pkg in mods.items():
    try: __import__(m)
    except ImportError: out.append(pkg)
print(' '.join(out))
EOF
)
    [[ -z "${missing}" ]] && ok "python dependencies" || bad "missing packages: ${missing}"
fi
# Ask the renderer itself rather than fc-list: fontconfig is not installed on a
# minimal server, and what matters is the path mp3_to_video actually resolves.
if [[ -x "${PY}" ]]; then
    font=$(cd "${APP_DIR}" && "${PY}" -c "import mp3_to_video as m; print(m.find_font_path() or '')" 2>/dev/null)
    [[ -n "${font}" ]] && ok "title-card font: ${font}" || bad "no usable TrueType font (apt-get install fonts-dejavu-core)"
fi

echo
echo "Credentials"
for f in service_account.json token.json client_secrets.json minio_config.json; do
    if [[ -f "${APP_DIR}/${f}" ]]; then
        perms=$(stat -c '%a %U' "${APP_DIR}/${f}")
        ok "${f} (${perms})"
    else
        bad "${f} missing"
    fi
done
[[ -f "${APP_DIR}/state.json" ]] && ok "state.json present" || warn "state.json absent (first run will start empty)"

# Load the credentials the way the daily job does. A token that parses but does
# not actually yield usable credentials looks fine on disk and fails only at
# 06:00 - which is exactly how a broken upload step went unnoticed once.
if [[ -x "${PY}" ]]; then
    yt=$(cd "${APP_DIR}" && sudo -u redcast "${PY}" - <<'EOF' 2>&1
import youtube_uploader as y
c = y.get_credentials()
print(("OK manage-scope" if y.has_manage_scope() else "OK upload-only") if c else "FAIL")
EOF
)
    case "${yt}" in
        *"OK manage-scope"*) ok "YouTube credentials load (playlists enabled)" ;;
        *"OK upload-only"*)  warn "YouTube credentials load, but no playlist scope" ;;
        *) bad "YouTube credentials do not load: ${yt##*$'
'}" ;;
    esac
fi

echo
echo "Services"
systemctl is-active --quiet minio && ok "minio.service running" || bad "minio.service not running"
curl -fsS http://127.0.0.1:9000/minio/health/live >/dev/null 2>&1 \
    && ok "MinIO API responding" || bad "MinIO API not responding on 127.0.0.1:9000"
systemctl is-enabled --quiet redcast.timer && ok "redcast.timer enabled" || bad "redcast.timer not enabled"
next=$(systemctl list-timers redcast.timer --no-pager 2>/dev/null | awk 'NR==2 {print $1, $2, $3}')
[[ -n "${next}" ]] && ok "next run: ${next}" || warn "timer has no scheduled run"

echo
echo "Application"
if [[ -x "${PY}" ]]; then
    if (cd "${APP_DIR}" && sudo -u redcast "${PY}" sol_storage.py check 2>&1 | sed 's/^/        /'); then
        ok "MinIO archive reachable by the app"
    else
        bad "app cannot reach MinIO"
    fi
    echo
    (cd "${APP_DIR}" && sudo -u redcast "${PY}" podcast.py status 2>&1 | tail -12 | sed 's/^/        /')
fi

echo
if [[ ${fails} -eq 0 ]]; then
    printf '\033[32mAll checks passed - the timer will run tonight.\033[0m\n'
else
    printf '\033[31m%d check(s) failed.\033[0m\n' "${fails}"
fi
exit "${fails}"
