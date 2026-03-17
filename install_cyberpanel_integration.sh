#!/usr/bin/env bash

set -euo pipefail
umask 022

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CYBERPANEL_ROOT="${CYBERPANEL_ROOT:-/usr/local/CyberCP}"
DJANGO_PROJECT_DIR="${DJANGO_PROJECT_DIR:-${CYBERPANEL_ROOT}/CyberCP}"
APP_SOURCE_DIR="${SCRIPT_DIR}/serverBackupManager"
APP_TARGET_DIR="${CYBERPANEL_ROOT}/serverBackupManager"
SETTINGS_FILE="${DJANGO_PROJECT_DIR}/settings.py"
URLS_FILE="${DJANGO_PROJECT_DIR}/urls.py"
BACKUP_SCRIPT_SOURCE="${SCRIPT_DIR}/cyberpanel_full_backup.sh"
RESTORE_SCRIPT_SOURCE="${SCRIPT_DIR}/cyberpanel_restore.sh"
RUNNER_SOURCE="${SCRIPT_DIR}/cyberpanel_vault_job_runner.sh"
BACKUP_SCRIPT_TARGET="/usr/local/bin/cyberpanel_full_backup.sh"
RESTORE_SCRIPT_TARGET="/usr/local/bin/cyberpanel_restore.sh"
RUNNER_TARGET="/usr/local/bin/cyberpanel-vault-job-runner"
STATE_DIR="/var/lib/cyberpanel-backup-ui"
STATE_JOBS_DIR="${STATE_DIR}/jobs"
SUDOERS_FILE="/etc/sudoers.d/cyberpanel-vault"
WEB_USER="${WEB_USER:-cyberpanel}"
URL_PATH="${URL_PATH:-server-backup/}"
BACKUP_SUFFIX=".$(date +%Y%m%d%H%M%S).cyberpanel-vault.bak"

log() {
    printf '[INFO] %s\n' "$1"
}

fatal() {
    printf '[ERROR] %s\n' "$1" >&2
    exit 1
}

require_root() {
    [ "$(id -u)" -eq 0 ] || fatal "Bu installer root olarak calistirilmalidir."
}

require_web_user() {
    id "$WEB_USER" >/dev/null 2>&1 || fatal "WEB_USER bulunamadi: ${WEB_USER}"
}

require_files() {
    [ -d "$APP_SOURCE_DIR" ] || fatal "serverBackupManager klasoru bulunamadi: ${APP_SOURCE_DIR}"
    [ -f "$SETTINGS_FILE" ] || fatal "settings.py bulunamadi: ${SETTINGS_FILE}"
    [ -f "$URLS_FILE" ] || fatal "urls.py bulunamadi: ${URLS_FILE}"
    [ -f "$BACKUP_SCRIPT_SOURCE" ] || fatal "Backup script bulunamadi: ${BACKUP_SCRIPT_SOURCE}"
    [ -f "$RESTORE_SCRIPT_SOURCE" ] || fatal "Restore script bulunamadi: ${RESTORE_SCRIPT_SOURCE}"
    [ -f "$RUNNER_SOURCE" ] || fatal "Root runner bulunamadi: ${RUNNER_SOURCE}"
}

backup_file() {
    local source_file="$1"
    local backup_file="${source_file}${BACKUP_SUFFIX}"

    cp -a "$source_file" "$backup_file"
    log "Yedek alindi: ${backup_file}"
}

copy_application() {
    log "Django uygulamasi kopyalaniyor..."
    rm -rf "$APP_TARGET_DIR"
    cp -a "$APP_SOURCE_DIR" "$APP_TARGET_DIR"
    find "$APP_TARGET_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} +
}

install_scripts() {
    log "Backup ve restore scriptleri kuruluyor..."
    install -m 750 "$BACKUP_SCRIPT_SOURCE" "$BACKUP_SCRIPT_TARGET"
    install -m 750 "$RESTORE_SCRIPT_SOURCE" "$RESTORE_SCRIPT_TARGET"
    install -m 750 "$RUNNER_SOURCE" "$RUNNER_TARGET"
}

prepare_state_dirs() {
    log "State klasorleri olusturuluyor..."
    mkdir -p "$STATE_JOBS_DIR"
    chown root:"$WEB_USER" "$STATE_DIR" "$STATE_JOBS_DIR"
    chmod 2770 "$STATE_DIR" "$STATE_JOBS_DIR"
    find "$STATE_DIR" -type d -exec chmod 2770 {} +
    find "$STATE_DIR" -type f -exec chmod 660 {} +
    chgrp -R "$WEB_USER" "$STATE_DIR"
}

patch_settings() {
    if grep -Fq "serverBackupManager.apps.ServerBackupManagerConfig" "$SETTINGS_FILE"; then
        log "INSTALLED_APPS girdisi zaten var."
        return
    fi

    log "settings.py guncelleniyor..."
    backup_file "$SETTINGS_FILE"
    local tmp_file
    tmp_file="$(mktemp)"

    awk '
        BEGIN { in_apps = 0; inserted = 0 }
        {
            if ($0 ~ /^INSTALLED_APPS[[:space:]]*=[[:space:]]*\[/) {
                in_apps = 1
            }

            if (in_apps && !inserted && $0 ~ /^[[:space:]]*\]/) {
                print "    '\''serverBackupManager.apps.ServerBackupManagerConfig'\'',"
                inserted = 1
            }

            print
        }
        END {
            if (!inserted) {
                exit 1
            }
        }
    ' "$SETTINGS_FILE" >"$tmp_file" || fatal "settings.py otomatik guncellenemedi."

    mv "$tmp_file" "$SETTINGS_FILE"
}

patch_urls_import() {
    if grep -Eq '^from django.urls import .*include' "$URLS_FILE"; then
        return
    fi

    if grep -Eq '^from django.urls import ' "$URLS_FILE"; then
        sed -i.bak 's/^from django.urls import /from django.urls import include, /' "$URLS_FILE"
        rm -f "${URLS_FILE}.bak"
        return
    fi

    fatal "urls.py icinde 'from django.urls import ...' satiri bulunamadi."
}

patch_urls() {
    if grep -Fq 'include("serverBackupManager.urls")' "$URLS_FILE"; then
        log "urls.py icindeki route zaten var."
        return
    fi

    backup_file "$URLS_FILE"
    patch_urls_import

    log "urls.py guncelleniyor..."
    local tmp_file
    tmp_file="$(mktemp)"

    awk -v route_path="$URL_PATH" '
        BEGIN { in_patterns = 0; inserted = 0 }
        {
            if ($0 ~ /^urlpatterns[[:space:]]*=[[:space:]]*\[/) {
                in_patterns = 1
            }

            if (in_patterns && !inserted && $0 ~ /^[[:space:]]*\]/) {
                printf "    path(\"%s\", include(\"serverBackupManager.urls\")),\n", route_path
                inserted = 1
            }

            print
        }
        END {
            if (!inserted) {
                exit 1
            }
        }
    ' "$URLS_FILE" >"$tmp_file" || fatal "urls.py otomatik guncellenemedi."

    mv "$tmp_file" "$URLS_FILE"
}

install_sudoers() {
    log "sudoers kurali yaziliyor..."
    cat >"$SUDOERS_FILE" <<EOF
Defaults:${WEB_USER} !requiretty
${WEB_USER} ALL=(root) NOPASSWD: ${RUNNER_TARGET} --check
${WEB_USER} ALL=(root) NOPASSWD: ${RUNNER_TARGET} ${STATE_JOBS_DIR}/*.json
${WEB_USER} ALL=(root) NOPASSWD: ${RUNNER_TARGET} --apply-schedule ${STATE_DIR}/schedule-request-*.json
EOF
    chmod 440 "$SUDOERS_FILE"
    visudo -cf "$SUDOERS_FILE" >/dev/null || fatal "sudoers dosyasi gecersiz: ${SUDOERS_FILE}"
}

print_summary() {
    cat <<EOF

Kurulum tamamlandi.

Kontrol etmeniz gerekenler:
1. CyberPanel web surecisi ${WEB_USER} kullanicisi ile calisiyor mu?
2. CyberPanel ayarlari yeniden yukecek sekilde web surecisini yeniden baslattiniz mi?
3. Panelde su adrese gidebiliyor musunuz?
   /${URL_PATH}

Kullanilan yollar:
- Django root: ${CYBERPANEL_ROOT}
- settings.py: ${SETTINGS_FILE}
- urls.py: ${URLS_FILE}
- backup script: ${BACKUP_SCRIPT_TARGET}
- restore script: ${RESTORE_SCRIPT_TARGET}
- root runner: ${RUNNER_TARGET}
- state dir: ${STATE_DIR}

EOF
}

main() {
    require_root
    require_web_user
    require_files
    copy_application
    install_scripts
    prepare_state_dirs
    patch_settings
    patch_urls
    install_sudoers
    print_summary
}

main "$@"
