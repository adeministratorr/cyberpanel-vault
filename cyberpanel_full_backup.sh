#!/usr/bin/env bash

# ============================================================
# CyberPanel Yedekleme Scripti
# - Haftalik 1 tam yedek, aradaki kosularda incremental dosya/config yedegi
# - Veritabani dump'i her kosuda tam alinir
# - Arsiv sifrelenir, rclone ile Google Drive'a yuklenir
# - Eski zincirler chain-aware sekilde temizlenir
# ============================================================

set -uo pipefail
umask 077

RCLONE_REMOTE="${RCLONE_REMOTE:-gdrive}"
DRIVE_FOLDER="${DRIVE_FOLDER:-cyberpanel-backups}"
MYSQL_PASSWORD_FILE="${MYSQL_PASSWORD_FILE:-/etc/cyberpanel/mysqlPassword}"
MYSQL_DUMP_MODE="${MYSQL_DUMP_MODE:-auto}"
BACKUP_MODE="${BACKUP_MODE:-auto}"
FULL_BACKUP_INTERVAL_DAYS="${FULL_BACKUP_INTERVAL_DAYS:-7}"
BACKUP_DIR="${BACKUP_DIR:-/root/backups}"
STATE_DIR="${STATE_DIR:-/var/lib/cyberpanel-backup}"
LOG_FILE="${LOG_FILE:-/var/log/cyberpanel_backup.log}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
LOCK_FILE="${LOCK_FILE:-/var/lock/cyberpanel_full_backup.lock}"
ENCRYPTION_PASSWORD_FILE="${ENCRYPTION_PASSWORD_FILE:-/root/.config/cyberpanel-backup/encryption.pass}"
OPENSSL_CIPHER="${OPENSSL_CIPHER:-aes-256-cbc}"
OPENSSL_PBKDF2_ITERATIONS="${OPENSSL_PBKDF2_ITERATIONS:-200000}"
CONSISTENCY_MODE="${CONSISTENCY_MODE:-service-freeze}"
CONSISTENCY_STRICT="${CONSISTENCY_STRICT:-1}"
QUIESCE_SERVICES="${QUIESCE_SERVICES:-lsws lscpd postfix dovecot crond cron pure-ftpd}"
PRE_BACKUP_HOOK="${PRE_BACKUP_HOOK:-}"
POST_BACKUP_HOOK="${POST_BACKUP_HOOK:-}"

RUN_TIMESTAMP="$(date +%Y%m%dT%H%M%S)"
RUN_HUMAN_TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S %Z')"
CURRENT_EPOCH="$(date +%s)"
HOSTNAME_FQDN="$(hostname -f 2>/dev/null || hostname)"
HOST_SLUG="$(printf '%s' "$HOSTNAME_FQDN" | tr -cs '[:alnum:]._-' '_')"
STAGING_ROOT_NAME="__cyberpanel_backup"
STATE_FILE="${STATE_DIR}/state.env"
SNAPSHOT_FILE="${STATE_DIR}/current_chain.snar"

BACKUP_TYPE=""
CHAIN_ID=""
CHAIN_STARTED_AT=""
BACKUP_NAME=""
BACKUP_PATH=""
STAGING_PATH=""
ARCHIVE_PATH=""
ENCRYPTED_ARCHIVE_PATH=""
UPLOAD_PATH=""
CHECKSUM_PATH=""
WORKING_SNAPSHOT_FILE=""
MYSQL_DEFAULTS_FILE=""
MYSQL_DUMP_MODE_EFFECTIVE=""
BACKUP_REASON=""

STATE_CURRENT_CHAIN_ID=""
STATE_LAST_FULL_EPOCH=""
STATE_LAST_FULL_TIMESTAMP=""

SECONDS=0
WARNINGS=0
FAILURES=0
CONSISTENCY_ACTIVE=0
declare -a ROOT_ITEMS=()
declare -a MYSQL_DUMP_ARGS=()
declare -a STOPPED_SERVICES=()

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
    WARNINGS=$((WARNINGS + 1))
    log_line "WARN" "$YELLOW" "$1"
}

step_error() {
    FAILURES=$((FAILURES + 1))
    log_line "ERROR" "$RED" "$1"
}

fatal() {
    log_line "ERROR" "$RED" "$1"
    exit 1
}

cleanup() {
    local exit_code=$?

    trap - EXIT INT TERM

    if [ "$CONSISTENCY_ACTIVE" -eq 1 ]; then
        thaw_writes || true
    fi

    if [ -n "${MYSQL_DEFAULTS_FILE}" ] && [ -f "${MYSQL_DEFAULTS_FILE}" ]; then
        rm -f "${MYSQL_DEFAULTS_FILE}"
    fi

    exit "$exit_code"
}

trap cleanup EXIT INT TERM

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        fatal "Bu script root olarak calistirilmalidir."
    fi
}

require_commands() {
    local cmd

    for cmd in flock gzip mktemp mysql mysqldump openssl rclone sha256sum tar; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            fatal "Gerekli komut bulunamadi: ${cmd}"
        fi
    done
}

require_positive_integer() {
    local name="$1"
    local value="$2"

    case "$value" in
        ''|*[!0-9]*)
            fatal "${name} pozitif tam sayi olmalidir. Deger: ${value}"
            ;;
        *)
            if [ "$value" -lt 1 ]; then
                fatal "${name} en az 1 olmalidir. Deger: ${value}"
            fi
            ;;
    esac
}

validate_settings() {
    require_positive_integer "FULL_BACKUP_INTERVAL_DAYS" "$FULL_BACKUP_INTERVAL_DAYS"
    require_positive_integer "RETENTION_DAYS" "$RETENTION_DAYS"
    require_positive_integer "OPENSSL_PBKDF2_ITERATIONS" "$OPENSSL_PBKDF2_ITERATIONS"
    case "$CONSISTENCY_MODE" in
        none|service-freeze)
            ;;
        *)
            fatal "Gecersiz CONSISTENCY_MODE degeri: ${CONSISTENCY_MODE}"
            ;;
    esac

    case "$CONSISTENCY_STRICT" in
        0|1)
            ;;
        *)
            fatal "CONSISTENCY_STRICT 0 veya 1 olmalidir. Deger: ${CONSISTENCY_STRICT}"
            ;;
    esac
}

acquire_lock() {
    mkdir -p "$(dirname "$LOCK_FILE")"
    exec 9>"$LOCK_FILE" || fatal "Kilit dosyasi olusturulamadi: ${LOCK_FILE}"

    if ! flock -n 9; then
        fatal "Baska bir yedekleme islemi zaten calisiyor."
    fi
}

prepare_base_paths() {
    mkdir -p "$BACKUP_DIR" "$STATE_DIR" "$(dirname "$LOG_FILE")"
    chmod 700 "$STATE_DIR" 2>/dev/null || true
    touch "$LOG_FILE" || fatal "Log dosyasi yazilamiyor: ${LOG_FILE}"
}

prepare_mysql_defaults() {
    local mysql_root_pass

    if [ ! -r "$MYSQL_PASSWORD_FILE" ]; then
        fatal "MySQL parola dosyasi okunamiyor: ${MYSQL_PASSWORD_FILE}"
    fi

    mysql_root_pass="$(<"$MYSQL_PASSWORD_FILE")"
    MYSQL_DEFAULTS_FILE="$(mktemp "${BACKUP_DIR}/mysql-backup.XXXXXX.cnf")" || fatal "Gecici MySQL ayar dosyasi olusturulamadi."

    chmod 600 "$MYSQL_DEFAULTS_FILE" || fatal "Gecici MySQL ayar dosyasi izinleri ayarlanamadi."
    printf '[client]\nuser=root\npassword=%s\n' "$mysql_root_pass" >"$MYSQL_DEFAULTS_FILE"
}

prepare_encryption() {
    if [ ! -r "$ENCRYPTION_PASSWORD_FILE" ]; then
        fatal "Sifreleme parola dosyasi okunamiyor: ${ENCRYPTION_PASSWORD_FILE}"
    fi
}

load_state() {
    if [ ! -r "$STATE_FILE" ]; then
        return
    fi

    while IFS='=' read -r key value; do
        case "$key" in
            CURRENT_CHAIN_ID) STATE_CURRENT_CHAIN_ID="$value" ;;
            LAST_FULL_EPOCH) STATE_LAST_FULL_EPOCH="$value" ;;
            LAST_FULL_TIMESTAMP) STATE_LAST_FULL_TIMESTAMP="$value" ;;
        esac
    done <"$STATE_FILE"
}

determine_backup_mode() {
    local age_since_full=0
    local state_valid=1

    case "$STATE_LAST_FULL_EPOCH" in
        ''|*[!0-9]*)
            state_valid=0
            ;;
    esac

    case "$BACKUP_MODE" in
        full)
            BACKUP_TYPE="full"
            BACKUP_REASON="BACKUP_MODE=full secildi"
            ;;
        incremental)
            if [ -z "$STATE_CURRENT_CHAIN_ID" ] || [ ! -f "$SNAPSHOT_FILE" ]; then
                fatal "BACKUP_MODE=incremental icin gecerli bir tam yedek zinciri bulunamadi."
            fi
            BACKUP_TYPE="incremental"
            BACKUP_REASON="BACKUP_MODE=incremental secildi"
            ;;
        auto)
            if [ -z "$STATE_CURRENT_CHAIN_ID" ] || [ "$state_valid" -eq 0 ] || [ ! -f "$SNAPSHOT_FILE" ]; then
                BACKUP_TYPE="full"
                BACKUP_REASON="Ilk kosu veya gecerli zincir bulunamadi"
            else
                age_since_full=$((CURRENT_EPOCH - STATE_LAST_FULL_EPOCH))
                if [ "$age_since_full" -ge $((FULL_BACKUP_INTERVAL_DAYS * 86400)) ]; then
                    BACKUP_TYPE="full"
                    BACKUP_REASON="Son tam yedek ${FULL_BACKUP_INTERVAL_DAYS} gunu asti"
                else
                    BACKUP_TYPE="incremental"
                    BACKUP_REASON="Tam yedek araliginda incremental devam edecek"
                fi
            fi
            ;;
        *)
            fatal "Gecersiz BACKUP_MODE degeri: ${BACKUP_MODE}"
            ;;
    esac

    if [ "$BACKUP_TYPE" = "full" ]; then
        CHAIN_ID="$RUN_TIMESTAMP"
        CHAIN_STARTED_AT="$RUN_TIMESTAMP"
    else
        CHAIN_ID="$STATE_CURRENT_CHAIN_ID"
        CHAIN_STARTED_AT="${STATE_LAST_FULL_TIMESTAMP:-$STATE_CURRENT_CHAIN_ID}"
    fi

    BACKUP_NAME="backup__host-${HOST_SLUG}__chain-${CHAIN_ID}__type-${BACKUP_TYPE}__at-${RUN_TIMESTAMP}"
}

initialize_backup_paths() {
    BACKUP_PATH="${BACKUP_DIR}/${BACKUP_NAME}.staging"
    STAGING_PATH="${BACKUP_PATH}/${STAGING_ROOT_NAME}"
    ARCHIVE_PATH="${BACKUP_DIR}/${BACKUP_NAME}.tar.gz"
    ENCRYPTED_ARCHIVE_PATH="${ARCHIVE_PATH}.enc"
    UPLOAD_PATH="${ENCRYPTED_ARCHIVE_PATH}"
    CHECKSUM_PATH="${UPLOAD_PATH}.sha256"
    WORKING_SNAPSHOT_FILE="${BACKUP_PATH}/current_chain.snar"

    mkdir -p "$STAGING_PATH/databases"
}

prepare_working_snapshot() {
    if [ "$BACKUP_TYPE" = "full" ]; then
        rm -f "$WORKING_SNAPSHOT_FILE"
        log "Tam yedek modu aktif. Yeni zincir baslatiliyor."
    else
        cp "$SNAPSHOT_FILE" "$WORKING_SNAPSHOT_FILE" || fatal "Incremental snapshot dosyasi hazirlanamadi."
        log "Incremental mod aktif. Zincir: ${CHAIN_ID}"
    fi
}

determine_mysqldump_mode() {
    local myisam_count

    MYSQL_DUMP_ARGS=(
        --all-databases
        --quick
        --routines
        --events
        --triggers
        --hex-blob
    )

    case "$MYSQL_DUMP_MODE" in
        auto)
            myisam_count="$(
                mysql \
                    --defaults-extra-file="$MYSQL_DEFAULTS_FILE" \
                    --batch \
                    --skip-column-names \
                    -e "SELECT COUNT(*) FROM information_schema.tables WHERE ENGINE = 'MyISAM';" \
                    2>>"$LOG_FILE"
            )" || fatal "Veritabani motor analizi yapilamadi."
            myisam_count="${myisam_count//[[:space:]]/}"

            if [ -z "$myisam_count" ]; then
                fatal "Veritabani motor analizi bos sonuc dondurdu."
            fi

            if [ "$myisam_count" -gt 0 ]; then
                MYSQL_DUMP_MODE_EFFECTIVE="lock-all-tables"
                MYSQL_DUMP_ARGS+=(--lock-all-tables)
                warn "MyISAM tablo algilandi (${myisam_count}); tutarli dump icin global lock kullanilacak."
            else
                MYSQL_DUMP_MODE_EFFECTIVE="single-transaction"
                MYSQL_DUMP_ARGS+=(--single-transaction --skip-lock-tables)
                log "Transactional veritabani dump modu kullanilacak."
            fi
            ;;
        single-transaction)
            MYSQL_DUMP_MODE_EFFECTIVE="single-transaction"
            MYSQL_DUMP_ARGS+=(--single-transaction --skip-lock-tables)
            log "MYSQL_DUMP_MODE=single-transaction secildi."
            ;;
        lock-all-tables)
            MYSQL_DUMP_MODE_EFFECTIVE="lock-all-tables"
            MYSQL_DUMP_ARGS+=(--lock-all-tables)
            warn "MYSQL_DUMP_MODE=lock-all-tables secildi; dump sirasinda global tablo kilidi alinacak."
            ;;
        *)
            fatal "Gecersiz MYSQL_DUMP_MODE degeri: ${MYSQL_DUMP_MODE}"
            ;;
    esac
}

service_exists() {
    local service_name="$1"
    local load_state

    load_state="$(systemctl show "$service_name" -p LoadState --value 2>/dev/null || true)"
    [ -n "$load_state" ] && [ "$load_state" != "not-found" ]
}

run_consistency_hook() {
    local stage="$1"
    local hook_cmd="$2"

    [ -n "$hook_cmd" ] || return 0

    log "Tutarlilik hook calisiyor (${stage})."
    if /bin/sh -c "$hook_cmd" >>"$LOG_FILE" 2>&1; then
        log "Tutarlilik hook tamamlandi (${stage})."
        return 0
    fi

    if [ "$CONSISTENCY_STRICT" = "1" ]; then
        fatal "Tutarlilik hook basarisiz oldu (${stage})."
    fi

    warn "Tutarlilik hook basarisiz oldu (${stage}), devam ediliyor."
    return 1
}

freeze_writes() {
    local service_name

    if [ "$CONSISTENCY_MODE" = "none" ]; then
        warn "CONSISTENCY_MODE=none. Veritabani ve dosya sistemi ayni ana sabitlenmeyecek."
        return 0
    fi

    if ! command -v systemctl >/dev/null 2>&1; then
        if [ "$CONSISTENCY_STRICT" = "1" ]; then
            fatal "Tutarlilik modu icin systemctl gereklidir."
        fi
        warn "systemctl bulunamadi. Tutarlilik icin servis dondurma atlandi."
        return 1
    fi

    run_consistency_hook "pre-freeze" "$PRE_BACKUP_HOOK" || true

    STOPPED_SERVICES=()
    log "Yazan servisler gecici olarak durduruluyor..."

    for service_name in $QUIESCE_SERVICES; do
        if ! service_exists "$service_name"; then
            continue
        fi

        if ! systemctl is-active --quiet "$service_name"; then
            continue
        fi

        if systemctl stop "$service_name" >>"$LOG_FILE" 2>&1; then
            STOPPED_SERVICES+=("$service_name")
            log "Servis durduruldu: ${service_name}"
        else
            if [ "$CONSISTENCY_STRICT" = "1" ]; then
                fatal "Servis durdurulamadi: ${service_name}"
            fi
            warn "Servis durdurulamadi: ${service_name}"
        fi
    done

    CONSISTENCY_ACTIVE=1
    log "Tutarlilik penceresi acildi. Veritabani ve dosyalar ayni ana sabitlenecek."
}

thaw_writes() {
    local idx
    local service_name
    local thaw_failed=0

    if [ "$CONSISTENCY_ACTIVE" -eq 0 ]; then
        return 0
    fi

    log "Durdurulan servisler yeniden baslatiliyor..."

    for ((idx=${#STOPPED_SERVICES[@]} - 1; idx>=0; idx--)); do
        service_name="${STOPPED_SERVICES[$idx]}"
        if systemctl start "$service_name" >>"$LOG_FILE" 2>&1; then
            log "Servis baslatildi: ${service_name}"
        else
            thaw_failed=1
            log_line "ERROR" "$RED" "Servis baslatilamadi: ${service_name}"
        fi
    done

    run_consistency_hook "post-thaw" "$POST_BACKUP_HOOK" || thaw_failed=1

    STOPPED_SERVICES=()
    CONSISTENCY_ACTIVE=0

    if [ "$thaw_failed" -ne 0 ] && [ "$CONSISTENCY_STRICT" = "1" ]; then
        return 1
    fi

    if [ "$thaw_failed" -ne 0 ]; then
        warn "Bazi servisler geri acilirken sorun olustu."
        return 1
    fi

    log "Tutarlilik penceresi kapatildi."
    return 0
}

write_metadata() {
    cat >"${STAGING_PATH}/backup_meta.txt" <<EOF
backup_name=${BACKUP_NAME}
backup_type=${BACKUP_TYPE}
backup_reason=${BACKUP_REASON}
chain_id=${CHAIN_ID}
chain_started_at=${CHAIN_STARTED_AT}
run_started_at=${RUN_HUMAN_TIMESTAMP}
host=${HOSTNAME_FQDN}
remote=${RCLONE_REMOTE}:${DRIVE_FOLDER}
retention_days=${RETENTION_DAYS}
full_backup_interval_days=${FULL_BACKUP_INTERVAL_DAYS}
archive_name=$(basename "$UPLOAD_PATH")
encryption=openssl-${OPENSSL_CIPHER}
mysql_dump_mode=${MYSQL_DUMP_MODE_EFFECTIVE}
mysql_backup_strategy=full_dump_every_run
consistency_mode=${CONSISTENCY_MODE}
consistency_strict=${CONSISTENCY_STRICT}
quiesce_services=${QUIESCE_SERVICES}
restore_requires_previous_chain=$( [ "$BACKUP_TYPE" = "incremental" ] && printf 'yes' || printf 'no' )
EOF
}

backup_databases() {
    local sql_file="${STAGING_PATH}/databases/all_databases.sql"

    log "Veritabanlari yedekleniyor..."
    if mysqldump \
        --defaults-extra-file="$MYSQL_DEFAULTS_FILE" \
        "${MYSQL_DUMP_ARGS[@]}" \
        >"$sql_file" 2>>"$LOG_FILE"; then
        if gzip -f "$sql_file" 2>>"$LOG_FILE"; then
            log "Veritabanlari yedeklendi."
        else
            rm -f "$sql_file"
            step_error "Veritabani dump dosyasi sikistirilamadi."
        fi
    else
        rm -f "$sql_file"
        step_error "Veritabani yedegi alinirken sorun olustu."
    fi
}

register_backup_paths() {
    local label="$1"
    local required="$2"
    shift 2

    local path
    local -a existing_paths=()
    local -a missing_paths=()

    for path in "$@"; do
        if [ -e "/$path" ]; then
            existing_paths+=("$path")
        else
            missing_paths+=("/$path")
        fi
    done

    if [ "$required" = "required" ] && [ "${#missing_paths[@]}" -gt 0 ]; then
        step_error "${label} icin zorunlu yollar eksik: ${missing_paths[*]}"
        return
    fi

    if [ "${#missing_paths[@]}" -gt 0 ] && [ "${#existing_paths[@]}" -gt 0 ]; then
        log "${label} icin bulunamayan yollar atlandi: ${missing_paths[*]}"
    fi

    if [ "${#existing_paths[@]}" -eq 0 ]; then
        if [ "$required" = "required" ]; then
            step_error "${label} yedeklenemedi, kaynak bulunamadi."
        else
            warn "${label} atlandi, kaynak bulunamadi."
        fi
        return
    fi

    ROOT_ITEMS+=("${existing_paths[@]}")
    log "${label} paketleme listesine eklendi."
}

abort_if_critical_failures() {
    if [ "$FAILURES" -gt 0 ]; then
        fatal "Kritik yedekleme adimlari basarisiz oldu. Paketleme ve yukleme durduruldu. Inceleme icin staging dizini korundu: ${BACKUP_PATH}"
    fi
}

package_backup() {
    log "Yedek paketi olusturuluyor..."

    if tar -czf "$ARCHIVE_PATH" \
        --listed-incremental="$WORKING_SNAPSHOT_FILE" \
        -C "$BACKUP_PATH" "$STAGING_ROOT_NAME" \
        -C / "${ROOT_ITEMS[@]}" \
        2>>"$LOG_FILE"; then
        log "Paketleme tamamlandi: $(basename "$ARCHIVE_PATH")"
    else
        fatal "Paketleme basarisiz oldu."
    fi
}

encrypt_archive() {
    log "Arsiv sifreleniyor..."

    if openssl enc "-${OPENSSL_CIPHER}" \
        -salt \
        -pbkdf2 \
        -iter "$OPENSSL_PBKDF2_ITERATIONS" \
        -in "$ARCHIVE_PATH" \
        -out "$UPLOAD_PATH" \
        -pass "file:${ENCRYPTION_PASSWORD_FILE}" \
        2>>"$LOG_FILE"; then
        rm -f "$ARCHIVE_PATH"
        log "Arsiv sifrelendi: $(basename "$UPLOAD_PATH")"
    else
        fatal "Arsiv sifrelenemedi. Yerel arsiv korundu: ${ARCHIVE_PATH}"
    fi
}

create_checksum() {
    if (
        cd "$(dirname "$UPLOAD_PATH")" &&
        sha256sum "$(basename "$UPLOAD_PATH")" >"$CHECKSUM_PATH"
    ) 2>>"$LOG_FILE"; then
        log "SHA256 ozeti olusturuldu."
    else
        fatal "SHA256 ozeti olusturulamadi."
    fi
}

upload_backup() {
    local -a rclone_opts=()

    if [ -t 1 ]; then
        rclone_opts+=(--progress)
    fi

    log "Google Drive'a arsiv yukleniyor..."
    if rclone copy "$UPLOAD_PATH" "${RCLONE_REMOTE}:${DRIVE_FOLDER}/" "${rclone_opts[@]}" 2>>"$LOG_FILE"; then
        log "Arsiv yuklemesi tamamlandi."
    else
        fatal "Google Drive arsiv yuklemesi basarisiz oldu. Yerel dosya korundu: ${UPLOAD_PATH}"
    fi

    log "Google Drive'a checksum yukleniyor..."
    if rclone copy "$CHECKSUM_PATH" "${RCLONE_REMOTE}:${DRIVE_FOLDER}/" "${rclone_opts[@]}" 2>>"$LOG_FILE"; then
        log "Checksum yuklemesi tamamlandi."
    else
        fatal "Checksum yuklemesi basarisiz oldu. Yerel dosyalar korundu."
    fi
}

save_state() {
    local state_tmp="${STATE_FILE}.tmp"
    local last_full_epoch_to_write="$STATE_LAST_FULL_EPOCH"
    local last_full_timestamp_to_write="$STATE_LAST_FULL_TIMESTAMP"

    if [ "$BACKUP_TYPE" = "full" ]; then
        last_full_epoch_to_write="$CURRENT_EPOCH"
        last_full_timestamp_to_write="$CHAIN_ID"
    fi

    if [ ! -f "$WORKING_SNAPSHOT_FILE" ]; then
        fatal "Guncel incremental snapshot dosyasi bulunamadi: ${WORKING_SNAPSHOT_FILE}"
    fi

    cp "$WORKING_SNAPSHOT_FILE" "$SNAPSHOT_FILE" || fatal "Kalici snapshot dosyasi guncellenemedi."
    chmod 600 "$SNAPSHOT_FILE" || fatal "Snapshot izinleri ayarlanamadi."

    cat >"$state_tmp" <<EOF
CURRENT_CHAIN_ID=${CHAIN_ID}
LAST_FULL_EPOCH=${last_full_epoch_to_write}
LAST_FULL_TIMESTAMP=${last_full_timestamp_to_write}
LAST_BACKUP_TYPE=${BACKUP_TYPE}
LAST_BACKUP_TIMESTAMP=${RUN_TIMESTAMP}
EOF

    chmod 600 "$state_tmp" || fatal "Durum dosyasi izinleri ayarlanamadi."
    mv "$state_tmp" "$STATE_FILE" || fatal "Durum dosyasi guncellenemedi."

    STATE_CURRENT_CHAIN_ID="$CHAIN_ID"
    STATE_LAST_FULL_EPOCH="$last_full_epoch_to_write"
    STATE_LAST_FULL_TIMESTAMP="$last_full_timestamp_to_write"

    log "Backup state guncellendi."
}

timestamp_to_epoch() {
    local ts="$1"

    case "$ts" in
        ????????T??????)
            date -d "${ts:0:4}-${ts:4:2}-${ts:6:2} ${ts:9:2}:${ts:11:2}:${ts:13:2}" +%s
            ;;
        *)
            return 1
            ;;
    esac
}

cleanup_remote() {
    local list_output
    local cutoff_epoch=$((CURRENT_EPOCH - (RETENTION_DAYS * 86400)))
    local file
    local rest
    local chain_id
    local chain_epoch
    local deleted_any=0
    local -A seen_chains=()

    log "${RETENTION_DAYS} gunden eski yedek zincirleri temizleniyor..."

    if ! list_output="$(rclone lsf "${RCLONE_REMOTE}:${DRIVE_FOLDER}" --files-only 2>>"$LOG_FILE")"; then
        warn "Remote listeleme basarisiz oldu; eski yedek temizleme atlandi."
        return
    fi

    while IFS= read -r file; do
        case "$file" in
            backup__host-${HOST_SLUG}__chain-*__type-*__at-*.tar.gz.enc|backup__host-${HOST_SLUG}__chain-*__type-*__at-*.tar.gz.enc.sha256)
                rest="${file#*__chain-}"
                chain_id="${rest%%__type-*}"
                if [ -n "$chain_id" ]; then
                    seen_chains["$chain_id"]=1
                fi
                ;;
        esac
    done <<<"$list_output"

    for chain_id in "${!seen_chains[@]}"; do
        if [ "$chain_id" = "$STATE_CURRENT_CHAIN_ID" ]; then
            continue
        fi

        if ! chain_epoch="$(timestamp_to_epoch "$chain_id" 2>>"$LOG_FILE")"; then
            warn "Remote chain tarihi parse edilemedi: ${chain_id}"
            continue
        fi

        if [ "$chain_epoch" -lt "$cutoff_epoch" ]; then
            if rclone delete "${RCLONE_REMOTE}:${DRIVE_FOLDER}" \
                --include "backup__host-${HOST_SLUG}__chain-${chain_id}__*" \
                2>>"$LOG_FILE"; then
                log "Eski zincir silindi: ${chain_id}"
                deleted_any=1
            else
                warn "Zincir silinemedi: ${chain_id}"
            fi
        fi
    done

    if [ "$deleted_any" -eq 0 ]; then
        log "Silinecek eski zincir bulunamadi."
    fi
}

cleanup_local_archive() {
    rm -f "$ARCHIVE_PATH" "$UPLOAD_PATH" "$CHECKSUM_PATH"
    rm -rf "$BACKUP_PATH"
    log "Yerel arsiv temizlendi."
}

main() {
    require_root
    acquire_lock
    require_commands
    validate_settings
    prepare_base_paths
    prepare_mysql_defaults
    prepare_encryption
    load_state
    determine_backup_mode
    initialize_backup_paths
    prepare_working_snapshot
    determine_mysqldump_mode

    log "=========================================="
    log "Yedekleme basladi: ${BACKUP_NAME}"
    log "Mod: ${BACKUP_TYPE} | Zincir: ${CHAIN_ID}"
    log "Neden: ${BACKUP_REASON}"
    log "=========================================="

    write_metadata
    freeze_writes
    backup_databases
    register_backup_paths "Site dosyalari (/home)" required "home"
    register_backup_paths "CyberPanel ayarlari" required "usr/local/CyberCP/CyberCP/settings.py" "etc/cyberpanel"
    register_backup_paths "OpenLiteSpeed ayarlari" required "usr/local/lsws/conf"
    register_backup_paths "Email verileri" optional "var/vmail"
    register_backup_paths "DNS ayarlari" optional "etc/powerdns"
    register_backup_paths "SSL sertifikalari" optional "etc/letsencrypt"
    register_backup_paths "Mail servis ayarlari" optional "etc/postfix" "etc/dovecot"
    register_backup_paths "Cron ayarlari" optional "etc/cron.d" "etc/cron.daily" "etc/cron.hourly" "etc/cron.weekly" "etc/cron.monthly" "var/spool/cron"
    register_backup_paths "Systemd unit ayarlari" optional "etc/systemd/system"
    register_backup_paths "Firewall ayarlari" optional "etc/firewalld" "etc/ufw"

    abort_if_critical_failures
    package_backup
    thaw_writes || fatal "Durdurulan servisler guvenli sekilde yeniden baslatilamadi."
    encrypt_archive
    create_checksum
    upload_backup
    save_state
    cleanup_remote
    cleanup_local_archive

    log "=========================================="
    log "Yedekleme basariyla tamamlandi."
    log "Mod: ${BACKUP_TYPE} | Zincir: ${CHAIN_ID}"
    log "Uyarilar: ${WARNINGS}"
    log "Sure: $((SECONDS / 60)) dakika $((SECONDS % 60)) saniye"
    log "=========================================="
}

main "$@"
