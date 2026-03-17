#!/usr/bin/env bash

set -euo pipefail
umask 022

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

WEB_USER="${WEB_USER:-cyberpanel}"
CYBERPANEL_ROOT="${CYBERPANEL_ROOT:-/usr/local/CyberCP}"
URL_PATH="${URL_PATH:-server-backup/}"
SECRET_FILE="${SECRET_FILE:-/root/.config/cyberpanel-backup/encryption.pass}"
RCLONE_CONFIG_FILE="${RCLONE_CONFIG_FILE:-/root/.config/rclone/rclone.conf}"
SHELL_ONLY=0
SKIP_VERIFY=0
REGENERATE_SECRET=0
PANEL_URL="${PANEL_URL:-}"

BACKUP_SCRIPT_SOURCE="${SCRIPT_DIR}/cyberpanel_full_backup.sh"
RESTORE_SCRIPT_SOURCE="${SCRIPT_DIR}/cyberpanel_restore.sh"
RUNNER_SOURCE="${SCRIPT_DIR}/cyberpanel_vault_job_runner.sh"
BACKUP_SCRIPT_TARGET="/usr/local/bin/cyberpanel_full_backup.sh"
RESTORE_SCRIPT_TARGET="/usr/local/bin/cyberpanel_restore.sh"
RUNNER_TARGET="/usr/local/bin/cyberpanel-vault-job-runner"
BACKUP_STATE_DIR="/var/lib/cyberpanel-backup"
UI_STATE_DIR="/var/lib/cyberpanel-backup-ui"
BACKUP_DIR="/root/backups"

SECRET_CREATED=0

usage() {
    cat <<'EOF'
Kullanim:
  bash install.sh [opsiyonlar]

Opsiyonlar:
  --web-user <user>          CyberPanel web sureci kullanicisi. Varsayilan: cyberpanel
  --cyberpanel-root <path>   CyberPanel kurulum kok dizini. Varsayilan: /usr/local/CyberCP
  --url-path <path>          Panel yolu. Varsayilan: server-backup/
  --panel-url <url>          Dogrulama icin panel adresi
  --secret-file <path>       Sifreleme parola dosyasi. Varsayilan: /root/.config/cyberpanel-backup/encryption.pass
  --rclone-config <path>     rclone config dosyasi. Varsayilan: /root/.config/rclone/rclone.conf
  --shell-only               Sadece shell scriptlerini kur, UI entegrasyonunu atla
  --skip-verify              Kurulum sonu dogrulamayi atla
  --regenerate-secret        Sifreleme parolasini yeni bir degerle yeniden olustur
  --help                     Bu yardimi goster
EOF
}

log() {
    printf '[INFO] %s\n' "$1"
}

warn() {
    printf '[WARN] %s\n' "$1" >&2
}

fatal() {
    printf '[ERROR] %s\n' "$1" >&2
    exit 1
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --web-user)
                [ "$#" -ge 2 ] || fatal "--web-user icin deger gerekli."
                WEB_USER="$2"
                shift 2
                ;;
            --cyberpanel-root)
                [ "$#" -ge 2 ] || fatal "--cyberpanel-root icin deger gerekli."
                CYBERPANEL_ROOT="$2"
                shift 2
                ;;
            --url-path)
                [ "$#" -ge 2 ] || fatal "--url-path icin deger gerekli."
                URL_PATH="$2"
                shift 2
                ;;
            --panel-url)
                [ "$#" -ge 2 ] || fatal "--panel-url icin deger gerekli."
                PANEL_URL="$2"
                shift 2
                ;;
            --secret-file)
                [ "$#" -ge 2 ] || fatal "--secret-file icin deger gerekli."
                SECRET_FILE="$2"
                shift 2
                ;;
            --rclone-config)
                [ "$#" -ge 2 ] || fatal "--rclone-config icin deger gerekli."
                RCLONE_CONFIG_FILE="$2"
                shift 2
                ;;
            --shell-only)
                SHELL_ONLY=1
                shift
                ;;
            --skip-verify)
                SKIP_VERIFY=1
                shift
                ;;
            --regenerate-secret)
                REGENERATE_SECRET=1
                shift
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            *)
                fatal "Bilinmeyen parametre: $1"
                ;;
        esac
    done
}

require_root() {
    [ "$(id -u)" -eq 0 ] || fatal "Bu script root olarak calistirilmalidir."
}

require_repo_files() {
    [ -f "$BACKUP_SCRIPT_SOURCE" ] || fatal "Backup script bulunamadi: ${BACKUP_SCRIPT_SOURCE}"
    [ -f "$RESTORE_SCRIPT_SOURCE" ] || fatal "Restore script bulunamadi: ${RESTORE_SCRIPT_SOURCE}"
    [ -f "$RUNNER_SOURCE" ] || fatal "Root runner bulunamadi: ${RUNNER_SOURCE}"
    [ -f "${SCRIPT_DIR}/install_cyberpanel_integration.sh" ] || fatal "install_cyberpanel_integration.sh bulunamadi."
}

require_commands() {
    local cmd
    local -a required_commands=(chmod dirname install mkdir openssl)

    if [ "$SKIP_VERIFY" -eq 0 ]; then
        required_commands+=(bash)
    fi

    for cmd in "${required_commands[@]}"; do
        command -v "$cmd" >/dev/null 2>&1 || fatal "Gerekli komut bulunamadi: ${cmd}"
    done
}

prepare_directories() {
    log "Calisma klasorleri hazirlaniyor..."
    mkdir -p "$(dirname "$SECRET_FILE")" "$BACKUP_STATE_DIR" "$BACKUP_DIR"
    chmod 700 "$(dirname "$SECRET_FILE")" "$BACKUP_STATE_DIR" "$BACKUP_DIR"

    if [ "$SHELL_ONLY" -eq 0 ]; then
        mkdir -p "$UI_STATE_DIR"
    fi
}

prepare_secret() {
    if [ "$REGENERATE_SECRET" -eq 1 ] || [ ! -f "$SECRET_FILE" ]; then
        log "Sifreleme parolasi olusturuluyor..."
        umask 077
        openssl rand -base64 48 | tr -d '\n' >"$SECRET_FILE"
        printf '\n' >>"$SECRET_FILE"
        chmod 600 "$SECRET_FILE"
        SECRET_CREATED=1
        return
    fi

    chmod 600 "$SECRET_FILE"
}

normalize_rclone_permissions() {
    if [ -f "$RCLONE_CONFIG_FILE" ]; then
        chmod 600 "$RCLONE_CONFIG_FILE"
        log "rclone config izinleri sikilastirildi: ${RCLONE_CONFIG_FILE}"
    else
        warn "rclone config dosyasi bulunamadi: ${RCLONE_CONFIG_FILE}"
        warn "Google Drive ayari icin daha sonra 'rclone config' calistirmaniz gerekir."
    fi
}

install_shell_scripts() {
    log "Backup ve restore scriptleri kuruluyor..."
    install -m 750 "$BACKUP_SCRIPT_SOURCE" "$BACKUP_SCRIPT_TARGET"
    install -m 750 "$RESTORE_SCRIPT_SOURCE" "$RESTORE_SCRIPT_TARGET"
    install -m 750 "$RUNNER_SOURCE" "$RUNNER_TARGET"
}

install_ui_integration() {
    log "CyberPanel arayuz entegrasyonu kuruluyor..."
    WEB_USER="$WEB_USER" \
    CYBERPANEL_ROOT="$CYBERPANEL_ROOT" \
    URL_PATH="$URL_PATH" \
    bash "${SCRIPT_DIR}/install_cyberpanel_integration.sh"
}

run_verification() {
    [ "$SKIP_VERIFY" -eq 0 ] || return 0
    [ "$SHELL_ONLY" -eq 0 ] || return 0

    log "Kurulum dogrulamasi calistiriliyor..."
    if [ -n "$PANEL_URL" ]; then
        bash "${SCRIPT_DIR}/test_cyberpanel_integration.sh" --web-user "$WEB_USER" --panel-url "$PANEL_URL"
    else
        bash "${SCRIPT_DIR}/test_cyberpanel_integration.sh" --web-user "$WEB_USER"
    fi
}

print_summary() {
    printf '\n'
    log "Kurulum tamamlandi."
    printf '\n'
    printf 'Kurulan yollar:\n'
    printf -- '- %s\n' "$BACKUP_SCRIPT_TARGET"
    printf -- '- %s\n' "$RESTORE_SCRIPT_TARGET"
    if [ "$SHELL_ONLY" -eq 0 ]; then
        printf -- '- %s\n' "$RUNNER_TARGET"
        printf -- '- Panel yolu: /%s\n' "$URL_PATH"
    fi
    printf '\n'
    printf 'Sifreleme dosyasi: %s\n' "$SECRET_FILE"
    if [ "$SECRET_CREATED" -eq 1 ]; then
        printf 'Not: Yeni sifreleme parolasi bu kurulumda olusturuldu.\n'
    fi
    printf '\n'
    printf 'Sonraki adimlar:\n'
    printf '1. CyberPanel icinde Server Mail ayarini kontrol edin.\n'
    printf '2. Admin hesabinin e-posta adresinin dogru oldugunu kontrol edin.\n'
    if [ ! -f "$RCLONE_CONFIG_FILE" ]; then
        printf '3. Sunucuda rclone config calistirip Google Drive remote olusturun.\n'
    else
        printf '3. Google Drive baglantisini "rclone lsd gdrive:" ile test edin.\n'
    fi
    if [ "$SHELL_ONLY" -eq 0 ]; then
        printf '4. CyberPanel web surecini yeniden baslatin ve /%s yolunu acin.\n' "$URL_PATH"
        printf '5. Panelden ilk tam yedegi baslatin.\n'
    else
        printf '4. Ilk tam yedegi su komutla alin: BACKUP_MODE=full %s\n' "$BACKUP_SCRIPT_TARGET"
    fi
}

main() {
    parse_args "$@"
    require_root
    require_repo_files
    require_commands
    prepare_directories
    prepare_secret
    normalize_rclone_permissions
    install_shell_scripts

    if [ "$SHELL_ONLY" -eq 0 ]; then
        install_ui_integration
    fi

    run_verification
    print_summary
}

main "$@"
