#!/usr/bin/env bash

set -euo pipefail
umask 077

APP_DIR="${CYBERPANEL_SERVER_BACKUP_APP_DIR:-/usr/local/CyberCP/serverBackupManager}"
JOB_RUNNER="${APP_DIR}/job_runner.py"
SCHEDULE_MANAGER="${APP_DIR}/schedule_manager.py"

usage() {
    cat <<'EOF'
Kullanim:
  cyberpanel-vault-job-runner --check
  cyberpanel-vault-job-runner --apply-schedule <schedule_json_path>
  cyberpanel-vault-job-runner <job_json_path>
EOF
}

if [ "${1:-}" = "--check" ]; then
    [ "$(id -u)" -eq 0 ] || {
        echo "Bu kontrol root olarak calismalidir." >&2
        exit 1
    }
    command -v python3 >/dev/null 2>&1 || {
        echo "python3 bulunamadi." >&2
        exit 1
    }
    [ -f "$JOB_RUNNER" ] || {
        echo "job_runner.py bulunamadi: ${JOB_RUNNER}" >&2
        exit 1
    }
    [ -r "$JOB_RUNNER" ] || {
        echo "job_runner.py okunamiyor: ${JOB_RUNNER}" >&2
        exit 1
    }
    [ -f "$SCHEDULE_MANAGER" ] || {
        echo "schedule_manager.py bulunamadi: ${SCHEDULE_MANAGER}" >&2
        exit 1
    }
    [ -r "$SCHEDULE_MANAGER" ] || {
        echo "schedule_manager.py okunamiyor: ${SCHEDULE_MANAGER}" >&2
        exit 1
    }
    exit 0
fi

if [ "${1:-}" = "--apply-schedule" ]; then
    [ "$#" -eq 2 ] || {
        usage >&2
        exit 1
    }

    [ "$(id -u)" -eq 0 ] || {
        echo "Bu komut root olarak calismalidir." >&2
        exit 1
    }

    command -v python3 >/dev/null 2>&1 || {
        echo "python3 bulunamadi." >&2
        exit 1
    }

    [ -f "$SCHEDULE_MANAGER" ] || {
        echo "schedule_manager.py bulunamadi: ${SCHEDULE_MANAGER}" >&2
        exit 1
    }

    exec python3 "$SCHEDULE_MANAGER" apply "$2"
fi

[ "$#" -eq 1 ] || {
    usage >&2
    exit 1
}

[ "$(id -u)" -eq 0 ] || {
    echo "Bu komut root olarak calismalidir." >&2
    exit 1
}

command -v python3 >/dev/null 2>&1 || {
    echo "python3 bulunamadi." >&2
    exit 1
}

[ -f "$JOB_RUNNER" ] || {
    echo "job_runner.py bulunamadi: ${JOB_RUNNER}" >&2
    exit 1
}

exec python3 "$JOB_RUNNER" "$1"
