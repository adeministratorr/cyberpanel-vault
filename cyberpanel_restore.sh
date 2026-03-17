#!/usr/bin/env bash

# ============================================================
# CyberPanel Zincirli Yedek Restore Scripti
# - Belirli bir full/incremental hedefe kadar zinciri indirir
# - Arsivleri dogrular, sifresini cozer ve sirali uygular
# - Veritabani, dosyalar ve host-level config'leri geri yukler
# ============================================================

set -euo pipefail
umask 077

RCLONE_REMOTE="${RCLONE_REMOTE:-gdrive}"
DRIVE_FOLDER="${DRIVE_FOLDER:-cyberpanel-backups}"
MYSQL_PASSWORD_FILE="${MYSQL_PASSWORD_FILE:-/etc/cyberpanel/mysqlPassword}"
RESTORE_WORKDIR="${RESTORE_WORKDIR:-/root/restore-workdir}"
LOG_FILE="${LOG_FILE:-/var/log/cyberpanel_restore.log}"
LOCK_FILE="${LOCK_FILE:-/var/lock/cyberpanel_restore.lock}"
ENCRYPTION_PASSWORD_FILE="${ENCRYPTION_PASSWORD_FILE:-/root/.config/cyberpanel-backup/encryption.pass}"
OPENSSL_CIPHER="${OPENSSL_CIPHER:-aes-256-cbc}"
OPENSSL_PBKDF2_ITERATIONS="${OPENSSL_PBKDF2_ITERATIONS:-200000}"
ALLOW_CROSS_HOST_RESTORE="${ALLOW_CROSS_HOST_RESTORE:-0}"

TARGET_FILE=""
CONFIRM_HOST=""
KEEP_WORKDIR=0
APPLY_RESTORE=0
RESTORE_DB=1
RESTORE_FILES=1
RESTORE_CONFIGS=1
RESTART_SERVICES=1

RUN_ID="$(date +%Y%m%dT%H%M%S)"
HOSTNAME_FQDN="$(hostname -f 2>/dev/null || hostname)"
HOST_SLUG="$(printf '%s' "$HOSTNAME_FQDN" | tr -cs '[:alnum:]._-' '_')"
WORKDIR=""
DOWNLOAD_DIR=""
DECRYPT_DIR=""
RESTORE_ROOT=""
CHAIN_ID=""
TARGET_TIMESTAMP=""
TARGET_TYPE=""
TARGET_HOST_SLUG=""
TARGET_PROFILE_KEY=""

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

log_line() {
    local level="$1"
    local color="$2"
    local message="$3"
    local line

    line="[$(date '+%Y-%m-%d %H:%M:%S')] [${level}] ${message}"
    printf '%s\n' "$line" >>"$LOG_FILE"
    printf '%b%s%b\n' "$color" "$line" "$NC"
}

log() {
    log_line "INFO" "$GREEN" "$1"
}

warn() {
    log_line "WARN" "$YELLOW" "$1"
}

fatal() {
    log_line "ERROR" "$RED" "$1"
    exit 1
}

usage() {
    cat <<'EOF'
Kullanim:
  cyberpanel_restore.sh --target-file <backup_file> --confirm-host <fqdn> --apply [opsiyonlar]

Opsiyonlar:
  --target-file <name>   Restore edilecek remote backup dosya adi
  --confirm-host <fqdn>  Canli geri yukleme icin mevcut host adini tekrar iste
  --apply                Geri yuklemeyi uygular. Verilmezse sadece planlama yapar
  --skip-db              Veritabanini geri yukleme
  --skip-files           /home verilerini geri yukleme
  --skip-configs         Host-level config'leri geri yukleme
  --skip-services        Restore sonrasi servis restart etme
  --keep-workdir         Gecici restore dizinini koru
  --help                 Bu yardimi goster
EOF
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --target-file)
                [ "$#" -ge 2 ] || fatal "--target-file degeri eksik."
                TARGET_FILE="$2"
                shift 2
                ;;
            --confirm-host)
                [ "$#" -ge 2 ] || fatal "--confirm-host degeri eksik."
                CONFIRM_HOST="$2"
                shift 2
                ;;
            --apply)
                APPLY_RESTORE=1
                shift
                ;;
            --skip-db)
                RESTORE_DB=0
                shift
                ;;
            --skip-files)
                RESTORE_FILES=0
                shift
                ;;
            --skip-configs)
                RESTORE_CONFIGS=0
                shift
                ;;
            --skip-services)
                RESTART_SERVICES=0
                shift
                ;;
            --keep-workdir)
                KEEP_WORKDIR=1
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

    [ -n "$TARGET_FILE" ] || fatal "--target-file zorunludur."
}

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        fatal "Bu script root olarak calistirilmalidir."
    fi
}

require_commands() {
    local cmd

    for cmd in flock gunzip mktemp mysql openssl rclone rsync sha256sum tar; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            fatal "Gerekli komut bulunamadi: ${cmd}"
        fi
    done
}

acquire_lock() {
    mkdir -p "$(dirname "$LOCK_FILE")"
    exec 9>"$LOCK_FILE" || fatal "Kilit dosyasi olusturulamadi: ${LOCK_FILE}"

    if ! flock -n 9; then
        fatal "Baska bir restore islemi zaten calisiyor."
    fi
}

prepare_env() {
    mkdir -p "$RESTORE_WORKDIR" "$(dirname "$LOG_FILE")"
    touch "$LOG_FILE" || fatal "Log dosyasi yazilamiyor: ${LOG_FILE}"

    [ -r "$MYSQL_PASSWORD_FILE" ] || fatal "MySQL parola dosyasi okunamiyor: ${MYSQL_PASSWORD_FILE}"
    [ -r "$ENCRYPTION_PASSWORD_FILE" ] || fatal "Sifreleme parola dosyasi okunamiyor: ${ENCRYPTION_PASSWORD_FILE}"

    WORKDIR="$(mktemp -d "${RESTORE_WORKDIR}/restore.${RUN_ID}.XXXXXX")" || fatal "Restore calisma dizini olusturulamadi."
    DOWNLOAD_DIR="${WORKDIR}/downloads"
    DECRYPT_DIR="${WORKDIR}/decrypted"
    RESTORE_ROOT="${WORKDIR}/restore_root"
    mkdir -p "$DOWNLOAD_DIR" "$DECRYPT_DIR" "$RESTORE_ROOT"
}

parse_target_file() {
    local pattern='^backup__host-([[:alnum:]_.-]+)(?:__profile-([[:alnum:]_.-]+))?__chain-([0-9]{8}T[0-9]{6})__type-(full|incremental)__at-([0-9]{8}T[0-9]{6})\.tar\.gz\.enc$'

    if [[ ! "$TARGET_FILE" =~ $pattern ]]; then
        fatal "Desteklenmeyen backup dosya adi formati: ${TARGET_FILE}"
    fi

    TARGET_HOST_SLUG="${BASH_REMATCH[1]}"
    TARGET_PROFILE_KEY="${BASH_REMATCH[2]:-legacy-all}"
    CHAIN_ID="${BASH_REMATCH[3]}"
    TARGET_TYPE="${BASH_REMATCH[4]}"
    TARGET_TIMESTAMP="${BASH_REMATCH[5]}"

    if [ "$ALLOW_CROSS_HOST_RESTORE" != "1" ] && [ "$TARGET_HOST_SLUG" != "$HOST_SLUG" ]; then
        fatal "Cross-host restore kapali. Hedef host=${TARGET_HOST_SLUG}, mevcut host=${HOST_SLUG}"
    fi
}

check_confirmation() {
    if [ "$APPLY_RESTORE" -ne 1 ]; then
        log "Apply bayragi verilmedi. Zincir dogrulamasi yapilacak, canli geri yukleme uygulanmayacak."
        return
    fi

    if [ "$CONFIRM_HOST" != "$HOSTNAME_FQDN" ]; then
        fatal "--confirm-host mevcut host FQDN degeriyle ayni olmalidir: ${HOSTNAME_FQDN}"
    fi
}

list_chain_files() {
    local remote_listing
    local pattern='^backup__host-([[:alnum:]_.-]+)(?:__profile-([[:alnum:]_.-]+))?__chain-([0-9]{8}T[0-9]{6})__type-(full|incremental)__at-([0-9]{8}T[0-9]{6})\.tar\.gz\.enc$'
    local line
    local file_host
    local file_profile
    local file_chain
    local file_type
    local file_ts
    local sorted_ts
    local sorted_type
    local sorted_file
    local -a chain_rows=()

    CHAIN_FILES=()
    CHAIN_TYPES=()

    remote_listing="$(rclone lsf "${RCLONE_REMOTE}:${DRIVE_FOLDER}" --files-only 2>>"$LOG_FILE")" || fatal "Remote backup listesi okunamadi."

    while IFS= read -r line; do
        [[ "$line" =~ $pattern ]] || continue

        file_host="${BASH_REMATCH[1]}"
        file_profile="${BASH_REMATCH[2]:-legacy-all}"
        file_chain="${BASH_REMATCH[3]}"
        file_type="${BASH_REMATCH[4]}"
        file_ts="${BASH_REMATCH[5]}"

        [ "$file_host" = "$TARGET_HOST_SLUG" ] || continue
        [ "$file_profile" = "$TARGET_PROFILE_KEY" ] || continue
        [ "$file_chain" = "$CHAIN_ID" ] || continue
        [[ "$file_ts" > "$TARGET_TIMESTAMP" ]] && continue

        chain_rows+=("${file_ts}|${file_type}|${line}")
    done <<<"$remote_listing"

    [ "${#chain_rows[@]}" -gt 0 ] || fatal "Secilen hedef icin zincir dosyasi bulunamadi."

    while IFS='|' read -r sorted_ts sorted_type sorted_file; do
        [ -n "$sorted_ts" ] || continue
        CHAIN_FILES+=("$sorted_file")
        CHAIN_TYPES+=("$sorted_type")
    done < <(printf '%s\n' "${chain_rows[@]}" | sort)

    [ "${CHAIN_TYPES[0]}" = "full" ] || fatal "Zincirin ilk elemani full backup degil."

    log "Restore zinciri bulundu: ${#CHAIN_FILES[@]} parca"
}

download_chain() {
    local file
    local remote_path
    local checksum_remote
    local local_path
    local checksum_local

    for file in "${CHAIN_FILES[@]}"; do
        remote_path="${RCLONE_REMOTE}:${DRIVE_FOLDER}/${file}"
        checksum_remote="${remote_path}.sha256"
        local_path="${DOWNLOAD_DIR}/${file}"
        checksum_local="${local_path}.sha256"

        log "Indiriliyor: ${file}"
        rclone copyto "$remote_path" "$local_path" 2>>"$LOG_FILE" || fatal "Backup indirilemedi: ${file}"
        rclone copyto "$checksum_remote" "$checksum_local" 2>>"$LOG_FILE" || fatal "Checksum indirilemedi: ${file}.sha256"
    done
}

verify_chain() {
    local file

    for file in "${CHAIN_FILES[@]}"; do
        log "Checksum dogrulaniyor: ${file}"
        (
            cd "$DOWNLOAD_DIR" &&
            sha256sum -c "$(basename "${file}.sha256")"
        ) >>"$LOG_FILE" 2>&1 || fatal "Checksum dogrulamasi basarisiz: ${file}"
    done
}

decrypt_chain() {
    local file
    local input_file
    local output_file

    for file in "${CHAIN_FILES[@]}"; do
        input_file="${DOWNLOAD_DIR}/${file}"
        output_file="${DECRYPT_DIR}/${file%.enc}"

        log "Sifre cozuluyor: ${file}"
        openssl enc "-${OPENSSL_CIPHER}" -d \
            -pbkdf2 \
            -iter "$OPENSSL_PBKDF2_ITERATIONS" \
            -in "$input_file" \
            -out "$output_file" \
            -pass "file:${ENCRYPTION_PASSWORD_FILE}" \
            2>>"$LOG_FILE" || fatal "Sifre cozme basarisiz: ${file}"
    done
}

extract_chain() {
    local file
    local archive

    for file in "${CHAIN_FILES[@]}"; do
        archive="${DECRYPT_DIR}/${file%.enc}"
        log "Arsiv uygulaniyor: $(basename "$archive")"
        tar -xzf "$archive" \
            --listed-incremental=/dev/null \
            -C "$RESTORE_ROOT" \
            2>>"$LOG_FILE" || fatal "Arsiv cikarilamadi: $(basename "$archive")"
    done
}

mysql_defaults_file() {
    local defaults_file
    local mysql_root_pass

    mysql_root_pass="$(<"$MYSQL_PASSWORD_FILE")"
    defaults_file="${WORKDIR}/mysql.cnf"
    printf '[client]\nuser=root\npassword=%s\n' "$mysql_root_pass" >"$defaults_file"
    chmod 600 "$defaults_file"
    printf '%s\n' "$defaults_file"
}

restore_dir() {
    local source_dir="$1"
    local target_dir="$2"

    if [ -d "$source_dir" ]; then
        mkdir -p "$target_dir"
        rsync -aH --delete "$source_dir/" "$target_dir/" >>"$LOG_FILE" 2>&1 || fatal "Dizin restore basarisiz: ${target_dir}"
        log "Dizin restore edildi: ${target_dir}"
    else
        log "Dizin atlandi: ${target_dir}"
    fi
}

restore_file() {
    local source_file="$1"
    local target_file="$2"

    if [ -f "$source_file" ]; then
        mkdir -p "$(dirname "$target_file")"
        cp -a "$source_file" "$target_file" || fatal "Dosya restore basarisiz: ${target_file}"
        log "Dosya restore edildi: ${target_file}"
    else
        log "Dosya atlandi: ${target_file}"
    fi
}

apply_restore() {
    local mysql_cnf
    local db_dump="${RESTORE_ROOT}/__cyberpanel_backup/databases/all_databases.sql.gz"

    if [ "$RESTORE_DB" -eq 1 ]; then
        [ -f "$db_dump" ] || fatal "Veritabani dump dosyasi bulunamadi: ${db_dump}"
        mysql_cnf="$(mysql_defaults_file)"
        log "Veritabani geri yukleniyor..."
        gunzip -c "$db_dump" | mysql --defaults-extra-file="$mysql_cnf" 2>>"$LOG_FILE" || fatal "Veritabani restore basarisiz oldu."
        log "Veritabani geri yuklendi."
    fi

    if [ "$RESTORE_FILES" -eq 1 ]; then
        restore_dir "${RESTORE_ROOT}/home" "/home"
        restore_dir "${RESTORE_ROOT}/var/vmail" "/var/vmail"
    fi

    if [ "$RESTORE_CONFIGS" -eq 1 ]; then
        restore_file "${RESTORE_ROOT}/usr/local/CyberCP/CyberCP/settings.py" "/usr/local/CyberCP/CyberCP/settings.py"
        restore_dir "${RESTORE_ROOT}/usr/local/lsws/conf" "/usr/local/lsws/conf"
        restore_dir "${RESTORE_ROOT}/etc/cyberpanel" "/etc/cyberpanel"
        restore_dir "${RESTORE_ROOT}/etc/powerdns" "/etc/powerdns"
        restore_dir "${RESTORE_ROOT}/etc/letsencrypt" "/etc/letsencrypt"
        restore_dir "${RESTORE_ROOT}/etc/postfix" "/etc/postfix"
        restore_dir "${RESTORE_ROOT}/etc/dovecot" "/etc/dovecot"
        restore_dir "${RESTORE_ROOT}/etc/cron.d" "/etc/cron.d"
        restore_dir "${RESTORE_ROOT}/etc/cron.daily" "/etc/cron.daily"
        restore_dir "${RESTORE_ROOT}/etc/cron.hourly" "/etc/cron.hourly"
        restore_dir "${RESTORE_ROOT}/etc/cron.weekly" "/etc/cron.weekly"
        restore_dir "${RESTORE_ROOT}/etc/cron.monthly" "/etc/cron.monthly"
        restore_dir "${RESTORE_ROOT}/var/spool/cron" "/var/spool/cron"
        restore_dir "${RESTORE_ROOT}/etc/systemd/system" "/etc/systemd/system"
        restore_dir "${RESTORE_ROOT}/etc/firewalld" "/etc/firewalld"
        restore_dir "${RESTORE_ROOT}/etc/ufw" "/etc/ufw"
    fi
}

restart_service_if_exists() {
    local service_name="$1"

    command -v systemctl >/dev/null 2>&1 || return 0

    if systemctl cat "$service_name" >/dev/null 2>&1; then
        if systemctl restart "$service_name" >>"$LOG_FILE" 2>&1; then
            log "Servis yeniden baslatildi: ${service_name}"
        else
            warn "Servis yeniden baslatilamadi: ${service_name}"
        fi
    fi
}

restart_services() {
    [ "$RESTART_SERVICES" -eq 1 ] || return 0

    log "Servisler yeniden baslatiliyor..."
    restart_service_if_exists lsws
    restart_service_if_exists lscpd
    restart_service_if_exists mysql
    restart_service_if_exists mariadb
    restart_service_if_exists pdns
    restart_service_if_exists postfix
    restart_service_if_exists dovecot
    restart_service_if_exists firewalld
    restart_service_if_exists ufw
}

cleanup_workdir() {
    if [ "$KEEP_WORKDIR" -eq 1 ]; then
        log "Calisma dizini korundu: ${WORKDIR}"
    else
        rm -rf "$WORKDIR"
        log "Calisma dizini temizlendi."
    fi
}

main() {
    parse_args "$@"
    require_root
    require_commands
    acquire_lock
    prepare_env
    parse_target_file
    check_confirmation
    list_chain_files
    log "Hedef backup: ${TARGET_FILE}"
    log "Profil: ${TARGET_PROFILE_KEY} | Zincir: ${CHAIN_ID} | Son tip: ${TARGET_TYPE}"

    if [ "$APPLY_RESTORE" -ne 1 ]; then
        log "Dry-run tamamlandi. Uygulamak icin --apply kullanin."
        cleanup_workdir
        exit 0
    fi

    download_chain
    verify_chain
    decrypt_chain
    extract_chain
    apply_restore
    restart_services
    cleanup_workdir

    log "Restore basariyla tamamlandi."
}

main "$@"
