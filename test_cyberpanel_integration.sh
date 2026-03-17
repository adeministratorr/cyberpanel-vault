#!/usr/bin/env bash

set -uo pipefail
umask 022

CYBERPANEL_ROOT="${CYBERPANEL_ROOT:-/usr/local/CyberCP}"
DJANGO_PROJECT_DIR="${DJANGO_PROJECT_DIR:-${CYBERPANEL_ROOT}/CyberCP}"
APP_TARGET_DIR="${APP_TARGET_DIR:-${CYBERPANEL_ROOT}/serverBackupManager}"
SETTINGS_FILE="${SETTINGS_FILE:-${DJANGO_PROJECT_DIR}/settings.py}"
URLS_FILE="${URLS_FILE:-${DJANGO_PROJECT_DIR}/urls.py}"
BACKUP_SCRIPT="${BACKUP_SCRIPT:-/usr/local/bin/cyberpanel_full_backup.sh}"
RESTORE_SCRIPT="${RESTORE_SCRIPT:-/usr/local/bin/cyberpanel_restore.sh}"
RUNNER_SCRIPT="${RUNNER_SCRIPT:-/usr/local/bin/cyberpanel-vault-job-runner}"
STATE_DIR="${STATE_DIR:-/var/lib/cyberpanel-backup-ui}"
STATE_JOBS_DIR="${STATE_JOBS_DIR:-${STATE_DIR}/jobs}"
SUDOERS_FILE="${SUDOERS_FILE:-/etc/sudoers.d/cyberpanel-vault}"
RCLONE_REMOTE="${RCLONE_REMOTE:-gdrive}"
RCLONE_CONFIG_FILE="${RCLONE_CONFIG:-/root/.config/rclone/rclone.conf}"
ENCRYPTION_PASSWORD_FILE="${ENCRYPTION_PASSWORD_FILE:-/root/.config/cyberpanel-backup/encryption.pass}"
WEB_USER="${WEB_USER:-}"
PANEL_URL=""

CHECKS=0
FAILURES=0
WARNINGS=0

if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    NC='\033[0m'
else
    RED=''
    GREEN=''
    YELLOW=''
    NC=''
fi

usage() {
    cat <<'EOF'
Kullanim:
  bash test_cyberpanel_integration.sh [opsiyonlar]

Opsiyonlar:
  --panel-url <url>   Panel uzerindeki server backup manager adresini kontrol eder
  --web-user <user>   CyberPanel web surecisi kullanicisini acikca belirtir
  --help              Bu yardimi gosterir
EOF
}

log() {
    printf '%b[INFO]%b %s\n' "$GREEN" "$NC" "$1"
}

pass() {
    CHECKS=$((CHECKS + 1))
    printf '%b[OK]%b %s\n' "$GREEN" "$NC" "$1"
}

warn() {
    CHECKS=$((CHECKS + 1))
    WARNINGS=$((WARNINGS + 1))
    printf '%b[WARN]%b %s\n' "$YELLOW" "$NC" "$1"
}

fail() {
    CHECKS=$((CHECKS + 1))
    FAILURES=$((FAILURES + 1))
    printf '%b[FAIL]%b %s\n' "$RED" "$NC" "$1"
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --panel-url)
                [ "$#" -ge 2 ] || {
                    echo "--panel-url icin deger gerekli." >&2
                    exit 1
                }
                PANEL_URL="$2"
                shift 2
                ;;
            --web-user)
                [ "$#" -ge 2 ] || {
                    echo "--web-user icin deger gerekli." >&2
                    exit 1
                }
                WEB_USER="$2"
                shift 2
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            *)
                echo "Bilinmeyen parametre: $1" >&2
                usage >&2
                exit 1
                ;;
        esac
    done
}

require_root() {
    [ "$(id -u)" -eq 0 ] || {
        echo "Bu test scripti root olarak calistirilmalidir." >&2
        exit 1
    }
}

discover_web_user() {
    local detected=""

    if [ -n "$WEB_USER" ]; then
        return
    fi

    if [ -r "$SUDOERS_FILE" ]; then
        detected="$(awk '
            /^[[:space:]]*#/ { next }
            /^[[:space:]]*Defaults:/ { next }
            /^[[:space:]]*$/ { next }
            { split($1, a, ":"); print a[1]; exit }
        ' "$SUDOERS_FILE")"
    fi

    if [ -z "$detected" ] && id cyberpanel >/dev/null 2>&1; then
        detected="cyberpanel"
    fi

    WEB_USER="$detected"
}

check_command() {
    local cmd="$1"
    if command -v "$cmd" >/dev/null 2>&1; then
        pass "Komut bulundu: ${cmd}"
    else
        fail "Komut bulunamadi: ${cmd}"
    fi
}

check_file() {
    local path="$1"
    local label="$2"

    if [ -f "$path" ]; then
        pass "${label} bulundu: ${path}"
    else
        fail "${label} bulunamadi: ${path}"
    fi
}

check_executable() {
    local path="$1"
    local label="$2"

    if [ -x "$path" ]; then
        pass "${label} calistirilabilir: ${path}"
    else
        fail "${label} calistirilabilir degil: ${path}"
    fi
}

check_directory() {
    local path="$1"
    local label="$2"

    if [ -d "$path" ]; then
        pass "${label} bulundu: ${path}"
    else
        fail "${label} bulunamadi: ${path}"
    fi
}

check_contains() {
    local file="$1"
    local pattern="$2"
    local label="$3"

    if [ ! -f "$file" ]; then
        fail "${label} kontrol edilemedi; dosya bulunamadi: ${file}"
        return
    fi

    if grep -Fq "$pattern" "$file"; then
        pass "${label}"
    else
        fail "${label}"
    fi
}

check_sudoers() {
    check_file "$SUDOERS_FILE" "sudoers dosyasi"

    if [ ! -f "$SUDOERS_FILE" ]; then
        return
    fi

    if visudo -cf "$SUDOERS_FILE" >/dev/null 2>&1; then
        pass "sudoers dosyasi gecerli"
    else
        fail "sudoers dosyasi gecersiz"
    fi

    if [ -n "$WEB_USER" ] && \
        grep -Fq "${WEB_USER} ALL=(root) NOPASSWD: ${RUNNER_SCRIPT} --check" "$SUDOERS_FILE" && \
        grep -Fq "${WEB_USER} ALL=(root) NOPASSWD: ${RUNNER_SCRIPT} ${STATE_JOBS_DIR}/*.json" "$SUDOERS_FILE" && \
        grep -Fq "${WEB_USER} ALL=(root) NOPASSWD: ${RUNNER_SCRIPT} --apply-schedule ${STATE_DIR}/schedule-request-*.json" "$SUDOERS_FILE"; then
        pass "Web kullanicisi icin sudoers kurallari bulundu: ${WEB_USER}"
    elif [ -n "$WEB_USER" ]; then
        fail "Web kullanicisi icin sudoers kurallari eksik: ${WEB_USER}"
    else
        warn "Web kullanicisi tespit edilemedi; sudoers kullanici kontrolu atlandi."
    fi
}

check_runner_as_root() {
    if CYBERPANEL_SERVER_BACKUP_APP_DIR="$APP_TARGET_DIR" "$RUNNER_SCRIPT" --check >/dev/null 2>&1; then
        pass "Root runner dogrulamasi basarili"
    else
        fail "Root runner dogrulamasi basarisiz"
    fi
}

check_runner_as_web_user() {
    if [ -z "$WEB_USER" ]; then
        warn "Web kullanicisi bilinmedigi icin sudo testi atlandi."
        return
    fi

    if ! id "$WEB_USER" >/dev/null 2>&1; then
        fail "Web kullanicisi bulunamadi: ${WEB_USER}"
        return
    fi

    if sudo -u "$WEB_USER" sudo -n "$RUNNER_SCRIPT" --check >/dev/null 2>&1; then
        pass "Web kullanicisi root runner'i sifresiz calistirabiliyor: ${WEB_USER}"
    else
        fail "Web kullanicisi root runner'i calistiramiyor: ${WEB_USER}"
    fi
}

check_state_permissions() {
    if [ -z "$WEB_USER" ]; then
        warn "Web kullanicisi bilinmedigi icin state izin kontrolu atlandi."
        return
    fi

    if sudo -u "$WEB_USER" test -r "$STATE_DIR" && sudo -u "$WEB_USER" test -w "$STATE_DIR"; then
        pass "Web kullanicisi state klasorunu okuyup yazabiliyor: ${STATE_DIR}"
    else
        fail "Web kullanicisi state klasorune erisemiyor: ${STATE_DIR}"
    fi

    if sudo -u "$WEB_USER" test -r "$STATE_JOBS_DIR" && sudo -u "$WEB_USER" test -w "$STATE_JOBS_DIR"; then
        pass "Web kullanicisi jobs klasorunu okuyup yazabiliyor: ${STATE_JOBS_DIR}"
    else
        fail "Web kullanicisi jobs klasorune erisemiyor: ${STATE_JOBS_DIR}"
    fi
}

check_rclone_remote() {
    if rclone lsd "${RCLONE_REMOTE}:" >/dev/null 2>&1; then
        pass "rclone remote erisilebilir: ${RCLONE_REMOTE}"
    else
        fail "rclone remote erisilemiyor: ${RCLONE_REMOTE}"
    fi
}

check_notification_mailer() {
    local settings_path="${STATE_DIR}/settings.json"

    if [ ! -f "$settings_path" ]; then
        return
    fi

    if grep -Fq '"backup_notification_enabled": true' "$settings_path"; then
        if grep -Fq '"backup_notification_use_admin": true' "$settings_path"; then
            pass "E-posta bildirimi CyberPanel admin adresini kullanacak sekilde ayarli"
        else
            warn "E-posta bildirimi acik; ozel alici adresinin gecerli oldugunu panelden kontrol edin"
        fi
    fi
}

check_private_root_file() {
    local path="$1"
    local label="$2"
    local stat_output
    local owner_uid
    local mode
    local mode_decimal

    if [ ! -f "$path" ]; then
        fail "${label} bulunamadi: ${path}"
        return
    fi

    stat_output="$(stat -c '%u %a' "$path" 2>/dev/null || true)"
    if [ -z "$stat_output" ]; then
        warn "${label} izinleri okunamadi: ${path}"
        return
    fi

    owner_uid="${stat_output%% *}"
    mode="${stat_output##* }"
    mode_decimal=$((8#${mode}))

    if [ "$owner_uid" -ne 0 ]; then
        fail "${label} root sahipliginde degil: ${path}"
        return
    fi

    if [ $((mode_decimal & 077)) -ne 0 ]; then
        fail "${label} grup/dunya erisimine acik: ${path}"
        return
    fi

    pass "${label} izinleri guvenli: ${path}"
}

check_python_syntax() {
    if python3 -m py_compile \
        "${APP_TARGET_DIR}/job_runner.py" \
        "${APP_TARGET_DIR}/schedule_manager.py" \
        "${APP_TARGET_DIR}/schedule_runner.py" \
        "${APP_TARGET_DIR}/services.py" \
        "${APP_TARGET_DIR}/views.py" \
        "${APP_TARGET_DIR}/urls.py" >/dev/null 2>&1; then
        pass "Python dosyalari sentaks kontrolunu gecti"
    else
        fail "Python dosyalari sentaks kontrolunu gecemedi"
    fi
}

check_script_syntax() {
    if bash -n "$BACKUP_SCRIPT" "$RESTORE_SCRIPT" "$RUNNER_SCRIPT" >/dev/null 2>&1; then
        pass "Shell scriptler sentaks kontrolunu gecti"
    else
        fail "Shell scriptler sentaks kontrolunu gecemedi"
    fi
}

check_panel_url() {
    local http_code

    if [ -z "$PANEL_URL" ]; then
        warn "Panel URL verilmedigi icin HTTP kontrolu atlandi."
        return
    fi

    if ! command -v curl >/dev/null 2>&1; then
        warn "curl bulunamadigi icin panel URL kontrolu atlandi."
        return
    fi

    http_code="$(curl -k -sS -L -o /dev/null -w '%{http_code}' "$PANEL_URL" 2>/dev/null || true)"

    case "$http_code" in
        200|301|302|303)
            pass "Panel URL erisilebilir: ${PANEL_URL} (HTTP ${http_code})"
            ;;
        401|403)
            warn "Panel URL yetki istiyor ama rota ayakta: ${PANEL_URL} (HTTP ${http_code})"
            ;;
        404)
            fail "Panel URL bulunamadi: ${PANEL_URL} (HTTP 404)"
            ;;
        5??)
            fail "Panel URL sunucu hatasi dondurdu: ${PANEL_URL} (HTTP ${http_code})"
            ;;
        *)
            warn "Panel URL icin beklenmeyen yanit alindi: ${PANEL_URL} (HTTP ${http_code:-yok})"
            ;;
    esac
}

print_summary() {
    printf '\n'
    log "Toplam kontrol: ${CHECKS}"
    log "Uyari: ${WARNINGS}"
    if [ "$FAILURES" -eq 0 ]; then
        log "Kritik hata bulunmadi."
    else
        log "Toplam kritik hata: ${FAILURES}"
    fi
}

main() {
    parse_args "$@"
    require_root
    discover_web_user

    log "CyberPanel entegrasyon kontrolu basliyor..."
    [ -n "$WEB_USER" ] && log "Tespit edilen web kullanicisi: ${WEB_USER}"

    check_command python3
    check_command sudo
    check_command visudo
    check_command rclone
    check_private_root_file "$RCLONE_CONFIG_FILE" "rclone config dosyasi"
    check_private_root_file "$ENCRYPTION_PASSWORD_FILE" "sifreleme parola dosyasi"
    check_file "$SETTINGS_FILE" "settings.py"
    check_file "$URLS_FILE" "urls.py"
    check_directory "$APP_TARGET_DIR" "serverBackupManager uygulama klasoru"
    check_file "${APP_TARGET_DIR}/job_runner.py" "job_runner.py"
    check_file "${APP_TARGET_DIR}/schedule_manager.py" "schedule_manager.py"
    check_file "${APP_TARGET_DIR}/schedule_runner.py" "schedule_runner.py"
    check_file "${APP_TARGET_DIR}/services.py" "services.py"
    check_file "${APP_TARGET_DIR}/views.py" "views.py"
    check_file "${APP_TARGET_DIR}/urls.py" "uygulama urls.py"
    check_executable "$BACKUP_SCRIPT" "Backup script"
    check_executable "$RESTORE_SCRIPT" "Restore script"
    check_executable "$RUNNER_SCRIPT" "Root runner"
    check_directory "$STATE_DIR" "UI state klasoru"
    check_directory "$STATE_JOBS_DIR" "UI jobs klasoru"
    check_contains "$SETTINGS_FILE" "serverBackupManager.apps.ServerBackupManagerConfig" "INSTALLED_APPS icinde serverBackupManager kaydi var"
    check_contains "$URLS_FILE" "include(\"serverBackupManager.urls\")" "Ana urls.py icinde serverBackupManager route'u var"
    check_sudoers
    check_runner_as_root
    check_runner_as_web_user
    check_state_permissions
    check_rclone_remote
    check_notification_mailer
    check_python_syntax
    check_script_syntax
    check_panel_url
    print_summary

    [ "$FAILURES" -eq 0 ]
}

main "$@"
