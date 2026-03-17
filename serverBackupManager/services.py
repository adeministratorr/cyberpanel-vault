import json
import os
import re
import shutil
import socket
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parseaddr
import fcntl
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
UI_STATE_DIR = Path(os.environ.get("CYBERPANEL_SERVER_BACKUP_UI_STATE_DIR", "/var/lib/cyberpanel-backup-ui"))
JOBS_DIR = UI_STATE_DIR / "jobs"
SETTINGS_FILE = UI_STATE_DIR / "settings.json"
SETTINGS_LOCK_FILE = UI_STATE_DIR / ".settings.lock"
BACKUP_SCRIPT = Path(os.environ.get("CYBERPANEL_SERVER_BACKUP_SCRIPT", "/usr/local/bin/cyberpanel_full_backup.sh"))
RESTORE_SCRIPT = Path(os.environ.get("CYBERPANEL_SERVER_RESTORE_SCRIPT", "/usr/local/bin/cyberpanel_restore.sh"))
JOB_RUNNER = BASE_DIR / "job_runner.py"
PRIVILEGED_JOB_RUNNER = Path(
    os.environ.get("CYBERPANEL_SERVER_BACKUP_PRIVILEGED_RUNNER", "/usr/local/bin/cyberpanel-vault-job-runner")
)
RUNNER_MODE = os.environ.get("CYBERPANEL_SERVER_BACKUP_RUNNER_MODE", "auto")
RCLONE_REMOTE = os.environ.get("RCLONE_REMOTE", "gdrive")
DRIVE_FOLDER = os.environ.get("DRIVE_FOLDER", "cyberpanel-backups")
HOST_FQDN = socket.getfqdn() or socket.gethostname()
HOST_SLUG = re.sub(r"[^A-Za-z0-9._-]+", "_", HOST_FQDN)
BACKUP_RE = re.compile(
    r"^backup__host-(?P<host>[A-Za-z0-9._-]+)"
    r"(?:__profile-(?P<profile>[A-Za-z0-9._-]+))?"
    r"__chain-(?P<chain>\d{8}T\d{6})"
    r"__type-(?P<kind>full|incremental)"
    r"__at-(?P<timestamp>\d{8}T\d{6})\.tar\.gz\.enc$"
)
JOB_ID_RE = re.compile(r"^\d{8}T\d{6}-[0-9a-f]{8}$")
ALLOWED_BACKUP_MODES = {"auto", "full", "incremental"}
ACTIVE_JOB_STATUSES = {"queued", "running"}
ALLOWED_RUNNER_MODES = {"auto", "direct", "sudo"}
DEFAULT_BACKUP_TIMEOUT_MINUTES = 120
MIN_BACKUP_TIMEOUT_MINUTES = 0
MAX_BACKUP_TIMEOUT_MINUTES = 1440
SCHEDULE_MANAGER = BASE_DIR / "schedule_manager.py"
DEFAULT_SCHEDULE_ENABLED = False
DEFAULT_SCHEDULE_HOUR = 3
DEFAULT_SCHEDULE_MINUTE = 0
DEFAULT_SCHEDULE_MODE = "auto"
DEFAULT_NOTIFICATION_ENABLED = False
DEFAULT_NOTIFICATION_EMAIL = str(os.environ.get("CYBERPANEL_SERVER_BACKUP_NOTIFY_EMAIL", "")).strip()
DEFAULT_NOTIFICATION_ON_SUCCESS = False
DEFAULT_NOTIFICATION_ON_FAILURE = True
BACKUP_COMPONENT_ORDER = ["databases", "site", "server", "email"]
BACKUP_COMPONENT_LABELS = {
    "databases": "Veritabanı",
    "site": "Site dosyaları",
    "server": "Sunucu ayarları",
    "email": "E-posta verileri",
}
BACKUP_COMPONENT_SLUGS = {
    "databases": "db",
    "site": "site",
    "server": "server",
    "email": "mail",
}
PROFILE_SLUG_TO_COMPONENT = {slug: component for component, slug in BACKUP_COMPONENT_SLUGS.items()}
DEFAULT_BACKUP_COMPONENTS = list(BACKUP_COMPONENT_ORDER)
UI_DIR_MODE = 0o2770
UI_FILE_MODE = 0o660
WEEKDAY_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")
WEEKDAY_LABELS = {
    "mon": "Pzt",
    "tue": "Sal",
    "wed": "Çar",
    "thu": "Per",
    "fri": "Cum",
    "sat": "Cmt",
    "sun": "Paz",
}
BACKUP_PROGRESS_STEPS = [
    ("Yedekleme basladi:", 8, "İş başlatıldı"),
    ("Veritabanlari yedekleniyor...", 18, "Veritabanı dökümü alınıyor"),
    ("Veritabanlari yedeklendi.", 28, "Veritabanı tamamlandı"),
    ("paketleme listesine eklendi.", 38, "Dosyalar hazırlanıyor"),
    ("Yedek paketi olusturuluyor...", 50, "Arşiv hazırlanıyor"),
    ("Paketleme tamamlandi:", 62, "Arşiv oluşturuldu"),
    ("Arsiv sifreleniyor...", 72, "Şifreleme yapılıyor"),
    ("Arsiv sifrelendi:", 80, "Şifreleme tamamlandı"),
    ("SHA256 ozeti olusturuldu.", 86, "Doğrulama özeti hazır"),
    ("Google Drive'a arsiv yukleniyor...", 92, "Google Drive'a yükleniyor"),
    ("Checksum yuklemesi tamamlandi.", 97, "Doğrulama özeti yüklendi"),
    ("Backup state guncellendi.", 99, "Durum kaydediliyor"),
    ("Yedekleme basariyla tamamlandi.", 100, "Tamamlandı"),
]
RESTORE_PROGRESS_STEPS = [
    ("Restore zinciri bulundu:", 12, "Zincir bulundu"),
    ("Indiriliyor:", 28, "Yedek dosyaları indiriliyor"),
    ("Checksum dogrulaniyor:", 42, "Bütünlük kontrolü yapılıyor"),
    ("Sifre cozuluyor:", 55, "Arşiv açılıyor"),
    ("Arsiv uygulaniyor:", 68, "Yedek arşivleri uygulanıyor"),
    ("Veritabani geri yukleniyor...", 78, "Veritabanı geri yükleniyor"),
    ("Veritabani geri yuklendi.", 84, "Veritabanı tamamlandı"),
    ("Dizin restore edildi:", 90, "Dosyalar geri yükleniyor"),
    ("Servisler yeniden baslatiliyor...", 96, "Servisler yeniden başlatılıyor"),
    ("Restore basariyla tamamlandi.", 100, "Tamamlandı"),
]


class ServiceError(RuntimeError):
    pass


def _parse_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _parse_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default

    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    return default


def _sanitize_timeout_minutes(value: Any, default: int) -> int:
    timeout_minutes = _parse_int(value, default)
    if timeout_minutes < MIN_BACKUP_TIMEOUT_MINUTES or timeout_minutes > MAX_BACKUP_TIMEOUT_MINUTES:
        return default
    return timeout_minutes


def _sanitize_schedule_hour(value: Any, default: int) -> int:
    hour = _parse_int(value, default)
    if hour < 0 or hour > 23:
        return default
    return hour


def _sanitize_schedule_minute(value: Any, default: int) -> int:
    minute = _parse_int(value, default)
    if minute < 0 or minute > 59:
        return default
    return minute


def _sanitize_schedule_weekdays(value: Any, default: list[str]) -> list[str]:
    if isinstance(value, str):
        candidates = [item.strip().lower() for item in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        candidates = [str(item).strip().lower() for item in value]
    else:
        candidates = list(default)

    seen: set[str] = set()
    weekdays: list[str] = []
    for day in WEEKDAY_ORDER:
        if day in candidates and day not in seen:
            weekdays.append(day)
            seen.add(day)

    return weekdays or list(default)


def _sanitize_schedule_mode(value: Any, default: str) -> str:
    mode = str(value).strip().lower() if value is not None else default
    if mode not in ALLOWED_BACKUP_MODES:
        return default
    return mode


def _sanitize_notification_email(value: Any, default: str) -> str:
    candidate = str(value).strip() if value is not None else str(default).strip()
    if not candidate:
        return ""

    _, parsed_address = parseaddr(candidate)
    return parsed_address.strip()


def _coerce_backup_components(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [item.strip().lower() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip().lower() for item in value if str(item).strip()]
    return None


def _sanitize_backup_components(value: Any, default: list[str]) -> list[str]:
    candidates = _coerce_backup_components(value)
    if candidates is None:
        candidates = list(default)

    if candidates == ["all"]:
        return list(DEFAULT_BACKUP_COMPONENTS)

    seen: set[str] = set()
    components: list[str] = []
    for component in BACKUP_COMPONENT_ORDER:
        if component in candidates and component not in seen:
            components.append(component)
            seen.add(component)

    return components or list(default)


def validate_backup_components(
    value: Any,
    *,
    default: list[str] | None = None,
    field_label: str = "Yedek bileşenleri",
) -> list[str]:
    candidates = _coerce_backup_components(value)
    if candidates is None:
        return list(default or DEFAULT_BACKUP_COMPONENTS)

    if candidates == ["all"]:
        return list(DEFAULT_BACKUP_COMPONENTS)

    if not candidates:
        raise ServiceError(f"{field_label} için en az bir seçim yapılmalıdır.")

    invalid_components = [item for item in candidates if item not in BACKUP_COMPONENT_ORDER]
    if invalid_components:
        raise ServiceError(f"{field_label} içinde geçersiz seçim var: {', '.join(invalid_components)}")

    sanitized = _sanitize_backup_components(candidates, default or DEFAULT_BACKUP_COMPONENTS)
    return sanitized


def summarize_backup_components(components: list[str] | Any, compact: bool = False) -> str:
    normalized = _sanitize_backup_components(components, DEFAULT_BACKUP_COMPONENTS)
    if normalized == DEFAULT_BACKUP_COMPONENTS:
        return "Tüm bileşenler"

    labels = [BACKUP_COMPONENT_LABELS[item] for item in normalized]
    if compact:
        return ", ".join(labels)
    return " + ".join(labels)


def backup_profile_key(components: list[str] | Any) -> str:
    normalized = _sanitize_backup_components(components, DEFAULT_BACKUP_COMPONENTS)
    if normalized == DEFAULT_BACKUP_COMPONENTS:
        return "all"
    return "-".join(BACKUP_COMPONENT_SLUGS[item] for item in normalized)


def components_from_profile_key(profile_key: str) -> list[str]:
    normalized = (profile_key or "").strip().lower()
    if not normalized or normalized in {"all", "legacy-all"}:
        return list(DEFAULT_BACKUP_COMPONENTS)

    slugs = [item for item in normalized.split("-") if item]
    components: list[str] = []
    seen: set[str] = set()
    for slug in slugs:
        component = PROFILE_SLUG_TO_COMPONENT.get(slug)
        if component and component not in seen:
            components.append(component)
            seen.add(component)

    return components or list(DEFAULT_BACKUP_COMPONENTS)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_file(job_id: str) -> Path:
    _validate_job_id(job_id)
    return JOBS_DIR / f"{job_id}.json"


def _log_file(job_id: str) -> Path:
    _validate_job_id(job_id)
    return JOBS_DIR / f"{job_id}.log"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.chmod(UI_FILE_MODE)
    tmp_path.replace(path)
    path.chmod(UI_FILE_MODE)


@contextmanager
def _locked_state_file(lock_path: Path):
    ensure_runtime_dirs()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        try:
            os.fchmod(lock_file.fileno(), UI_FILE_MODE)
        except PermissionError:
            pass
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def ensure_runtime_dirs() -> None:
    UI_STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        UI_STATE_DIR.chmod(UI_DIR_MODE)
    except PermissionError:
        pass
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        JOBS_DIR.chmod(UI_DIR_MODE)
    except PermissionError:
        pass


def _validate_job_id(job_id: str) -> None:
    if not JOB_ID_RE.match(job_id):
        raise ServiceError("İş kimliği geçerli değil.")


def _read_job_record(job_id: str) -> dict[str, Any]:
    path = _job_file(job_id)
    if not path.exists():
        raise ServiceError("İş kaydı bulunamadı.")
    return json.loads(path.read_text(encoding="utf-8"))


def _public_job_view(job: dict[str, Any]) -> dict[str, Any]:
    log_content = ""
    try:
        log_content = read_job_log(str(job.get("id", "")))
    except ServiceError:
        log_content = ""

    progress = _job_progress(job, log_content)
    return {
        "id": job.get("id", ""),
        "type": job.get("type", ""),
        "status": job.get("status", ""),
        "created_at": job.get("created_at", ""),
        "started_at": job.get("started_at", ""),
        "finished_at": job.get("finished_at", ""),
        "exit_code": job.get("exit_code"),
        "error": job.get("error", ""),
        "meta": job.get("meta", {}),
        "progress_percent": progress["percent"],
        "progress_label": progress["label"],
        "last_log_line": _last_log_line(log_content),
    }


def _last_log_line(log_content: str) -> str:
    if not log_content:
        return ""

    for line in reversed(log_content.splitlines()):
        clean = line.strip()
        if clean:
            return clean
    return ""


def _job_progress(job: dict[str, Any], log_content: str) -> dict[str, Any]:
    status = str(job.get("status", ""))
    job_type = str(job.get("type", ""))
    progress_steps = BACKUP_PROGRESS_STEPS if job_type == "backup" else RESTORE_PROGRESS_STEPS

    if status == "queued":
        return {"percent": 5, "label": "Sırada bekliyor"}

    percent = 10 if status == "running" else 100
    label = "Hazırlanıyor" if status == "running" else "Tamamlandı"

    for marker, step_percent, step_label in progress_steps:
        if marker in log_content:
            percent = step_percent
            label = step_label

    if status == "failed":
        if "timeout_exceeded=" in log_content:
            label = "Süre sınırında durdu"
        if percent >= 100:
            percent = 100
        elif percent < 15:
            percent = 15
        elif label != "Süre sınırında durdu":
            label = f"{label} aşamasında durdu"

    return {"percent": min(max(percent, 0), 100), "label": label}


def _settings_defaults() -> dict[str, Any]:
    return {
        "backup_timeout_minutes": _sanitize_timeout_minutes(
            os.environ.get("CYBERPANEL_SERVER_BACKUP_TIMEOUT_MINUTES"),
            DEFAULT_BACKUP_TIMEOUT_MINUTES,
        ),
        "backup_default_components": _sanitize_backup_components(
            os.environ.get("CYBERPANEL_SERVER_BACKUP_COMPONENTS"),
            DEFAULT_BACKUP_COMPONENTS,
        ),
        "backup_schedule_enabled": _parse_bool(
            os.environ.get("CYBERPANEL_SERVER_BACKUP_SCHEDULE_ENABLED"),
            DEFAULT_SCHEDULE_ENABLED,
        ),
        "backup_schedule_hour": _sanitize_schedule_hour(
            os.environ.get("CYBERPANEL_SERVER_BACKUP_SCHEDULE_HOUR"),
            DEFAULT_SCHEDULE_HOUR,
        ),
        "backup_schedule_minute": _sanitize_schedule_minute(
            os.environ.get("CYBERPANEL_SERVER_BACKUP_SCHEDULE_MINUTE"),
            DEFAULT_SCHEDULE_MINUTE,
        ),
        "backup_schedule_mode": _sanitize_schedule_mode(
            os.environ.get("CYBERPANEL_SERVER_BACKUP_SCHEDULE_MODE"),
            DEFAULT_SCHEDULE_MODE,
        ),
        "backup_schedule_components": _sanitize_backup_components(
            os.environ.get("CYBERPANEL_SERVER_BACKUP_SCHEDULE_COMPONENTS"),
            DEFAULT_BACKUP_COMPONENTS,
        ),
        "backup_schedule_weekdays": _sanitize_schedule_weekdays(
            os.environ.get("CYBERPANEL_SERVER_BACKUP_SCHEDULE_WEEKDAYS"),
            WEEKDAY_ORDER,
        ),
        "backup_notification_enabled": _parse_bool(
            os.environ.get("CYBERPANEL_SERVER_BACKUP_NOTIFY_ENABLED"),
            DEFAULT_NOTIFICATION_ENABLED,
        ),
        "backup_notification_email": _sanitize_notification_email(
            os.environ.get("CYBERPANEL_SERVER_BACKUP_NOTIFY_EMAIL"),
            DEFAULT_NOTIFICATION_EMAIL,
        ),
        "backup_notification_on_success": _parse_bool(
            os.environ.get("CYBERPANEL_SERVER_BACKUP_NOTIFY_ON_SUCCESS"),
            DEFAULT_NOTIFICATION_ON_SUCCESS,
        ),
        "backup_notification_on_failure": _parse_bool(
            os.environ.get("CYBERPANEL_SERVER_BACKUP_NOTIFY_ON_FAILURE"),
            DEFAULT_NOTIFICATION_ON_FAILURE,
        ),
    }


def load_ui_settings() -> dict[str, Any]:
    ensure_runtime_dirs()
    settings = _settings_defaults()

    if not SETTINGS_FILE.exists():
        return settings

    try:
        payload = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return settings

    if isinstance(payload, dict):
        settings["backup_timeout_minutes"] = _sanitize_timeout_minutes(
            payload.get("backup_timeout_minutes"),
            settings["backup_timeout_minutes"],
        )
        settings["backup_default_components"] = _sanitize_backup_components(
            payload.get("backup_default_components"),
            settings["backup_default_components"],
        )
        settings["backup_schedule_enabled"] = _parse_bool(
            payload.get("backup_schedule_enabled"),
            settings["backup_schedule_enabled"],
        )
        settings["backup_schedule_hour"] = _sanitize_schedule_hour(
            payload.get("backup_schedule_hour"),
            settings["backup_schedule_hour"],
        )
        settings["backup_schedule_minute"] = _sanitize_schedule_minute(
            payload.get("backup_schedule_minute"),
            settings["backup_schedule_minute"],
        )
        settings["backup_schedule_mode"] = _sanitize_schedule_mode(
            payload.get("backup_schedule_mode"),
            settings["backup_schedule_mode"],
        )
        settings["backup_schedule_components"] = _sanitize_backup_components(
            payload.get("backup_schedule_components"),
            settings["backup_schedule_components"],
        )
        settings["backup_schedule_weekdays"] = _sanitize_schedule_weekdays(
            payload.get("backup_schedule_weekdays"),
            settings["backup_schedule_weekdays"],
        )
        settings["backup_notification_enabled"] = _parse_bool(
            payload.get("backup_notification_enabled"),
            settings["backup_notification_enabled"],
        )
        settings["backup_notification_email"] = _sanitize_notification_email(
            payload.get("backup_notification_email"),
            settings["backup_notification_email"],
        )
        settings["backup_notification_on_success"] = _parse_bool(
            payload.get("backup_notification_on_success"),
            settings["backup_notification_on_success"],
        )
        settings["backup_notification_on_failure"] = _parse_bool(
            payload.get("backup_notification_on_failure"),
            settings["backup_notification_on_failure"],
        )

    return settings


def save_ui_settings(settings: dict[str, Any]) -> dict[str, Any]:
    with _locked_state_file(SETTINGS_LOCK_FILE):
        current = load_ui_settings()
        if "backup_timeout_minutes" in settings:
            current["backup_timeout_minutes"] = validate_backup_timeout_minutes(settings.get("backup_timeout_minutes"))
        if "backup_default_components" in settings:
            current["backup_default_components"] = validate_backup_components(
                settings.get("backup_default_components"),
                default=current["backup_default_components"],
                field_label="Varsayılan yedek bileşenleri",
            )
        if {
            "backup_schedule_enabled",
            "backup_schedule_hour",
            "backup_schedule_minute",
            "backup_schedule_mode",
            "backup_schedule_components",
            "backup_schedule_weekdays",
        } & set(settings.keys()):
            current.update(
                validate_backup_schedule_settings(
                    settings.get("backup_schedule_enabled", current["backup_schedule_enabled"]),
                    settings.get("backup_schedule_hour", current["backup_schedule_hour"]),
                    settings.get("backup_schedule_minute", current["backup_schedule_minute"]),
                    settings.get("backup_schedule_mode", current["backup_schedule_mode"]),
                    settings.get("backup_schedule_components", current["backup_schedule_components"]),
                    settings.get("backup_schedule_weekdays", current["backup_schedule_weekdays"]),
                )
            )
        if {
            "backup_notification_enabled",
            "backup_notification_email",
            "backup_notification_on_success",
            "backup_notification_on_failure",
        } & set(settings.keys()):
            current.update(
                validate_backup_notification_settings(
                    settings.get("backup_notification_enabled", current["backup_notification_enabled"]),
                    settings.get("backup_notification_email", current["backup_notification_email"]),
                    settings.get("backup_notification_on_success", current["backup_notification_on_success"]),
                    settings.get("backup_notification_on_failure", current["backup_notification_on_failure"]),
                )
            )
        _write_json(SETTINGS_FILE, current)
        return current


def validate_backup_timeout_minutes(value: Any) -> int:
    raw = str(value).strip() if value is not None else ""
    if raw == "":
        return load_ui_settings()["backup_timeout_minutes"]

    try:
        timeout_minutes = int(raw)
    except (TypeError, ValueError) as exc:
        raise ServiceError("Yedek süresi dakika cinsinden tam sayı olmalıdır.") from exc

    if timeout_minutes < MIN_BACKUP_TIMEOUT_MINUTES or timeout_minutes > MAX_BACKUP_TIMEOUT_MINUTES:
        raise ServiceError(
            f"Yedek süresi {MIN_BACKUP_TIMEOUT_MINUTES} ile {MAX_BACKUP_TIMEOUT_MINUTES} dakika arasında olmalıdır."
        )

    return timeout_minutes


def validate_backup_schedule_settings(
    enabled: Any,
    hour: Any,
    minute: Any,
    mode: Any,
    components: Any,
    weekdays: Any,
) -> dict[str, Any]:
    explicit_weekdays: list[str] | None
    if isinstance(weekdays, str):
        explicit_weekdays = [item.strip().lower() for item in weekdays.split(",") if item.strip()]
    elif isinstance(weekdays, (list, tuple, set)):
        explicit_weekdays = [str(item).strip().lower() for item in weekdays if str(item).strip()]
    else:
        explicit_weekdays = None

    schedule_enabled = _parse_bool(enabled, DEFAULT_SCHEDULE_ENABLED)
    schedule_hour = _sanitize_schedule_hour(hour, DEFAULT_SCHEDULE_HOUR)
    schedule_minute = _sanitize_schedule_minute(minute, DEFAULT_SCHEDULE_MINUTE)
    schedule_mode = _sanitize_schedule_mode(mode, DEFAULT_SCHEDULE_MODE)
    schedule_components = validate_backup_components(
        components,
        default=DEFAULT_BACKUP_COMPONENTS,
        field_label="Zamanlama bileşenleri",
    )
    schedule_weekdays = _sanitize_schedule_weekdays(weekdays, WEEKDAY_ORDER)

    raw_hour = str(hour).strip() if hour is not None else ""
    raw_minute = str(minute).strip() if minute is not None else ""
    raw_mode = str(mode).strip().lower() if mode is not None else ""

    if raw_hour and _parse_int(raw_hour, -1) != schedule_hour:
        raise ServiceError("Zamanlama saati 0 ile 23 arasında olmalıdır.")
    if raw_minute and _parse_int(raw_minute, -1) != schedule_minute:
        raise ServiceError("Zamanlama dakikası 0 ile 59 arasında olmalıdır.")
    if raw_mode and raw_mode != schedule_mode:
        raise ServiceError("Zamanlama modu geçerli değil.")
    if explicit_weekdays is not None and any(day not in WEEKDAY_ORDER for day in explicit_weekdays):
        raise ServiceError("Zamanlama günlerinden biri geçerli değil.")
    if schedule_enabled and explicit_weekdays is not None and not explicit_weekdays:
        raise ServiceError("Otomatik yedekleme için en az bir gün seçilmelidir.")

    return {
        "backup_schedule_enabled": schedule_enabled,
        "backup_schedule_hour": schedule_hour,
        "backup_schedule_minute": schedule_minute,
        "backup_schedule_mode": schedule_mode,
        "backup_schedule_components": schedule_components,
        "backup_schedule_weekdays": schedule_weekdays,
    }


def validate_backup_notification_settings(enabled: Any, email: Any, on_success: Any, on_failure: Any) -> dict[str, Any]:
    notification_enabled = _parse_bool(enabled, DEFAULT_NOTIFICATION_ENABLED)
    notification_email = _sanitize_notification_email(email, DEFAULT_NOTIFICATION_EMAIL)
    notify_on_success = _parse_bool(on_success, DEFAULT_NOTIFICATION_ON_SUCCESS)
    notify_on_failure = _parse_bool(on_failure, DEFAULT_NOTIFICATION_ON_FAILURE)
    raw_email = str(email).strip() if email is not None else ""

    if raw_email and not notification_email:
        raise ServiceError("Bildirim e-posta adresi geçerli değil.")
    if notification_email and not EMAIL_RE.match(notification_email):
        raise ServiceError("Bildirim e-posta adresi geçerli değil.")
    if notification_enabled and not notification_email:
        raise ServiceError("E-posta bildirimi açıkken alıcı adresi zorunludur.")
    if notification_enabled and not (notify_on_success or notify_on_failure):
        raise ServiceError("E-posta bildirimi için en az bir tetik seçilmelidir.")

    return {
        "backup_notification_enabled": notification_enabled,
        "backup_notification_email": notification_email,
        "backup_notification_on_success": notify_on_success,
        "backup_notification_on_failure": notify_on_failure,
    }


def summarize_backup_schedule(settings: dict[str, Any]) -> str:
    if not settings.get("backup_schedule_enabled"):
        return "Otomatik yedekleme kapalı."

    weekdays = settings.get("backup_schedule_weekdays") or []
    if weekdays == WEEKDAY_ORDER:
        day_label = "Her gün"
    else:
        day_label = ", ".join(WEEKDAY_LABELS.get(day, day) for day in weekdays)

    mode_labels = {
        "auto": "Otomatik",
        "full": "Tam",
        "incremental": "Artımlı",
    }
    mode_label = mode_labels.get(settings.get("backup_schedule_mode", DEFAULT_SCHEDULE_MODE), "Otomatik")
    components_label = summarize_backup_components(settings.get("backup_schedule_components", DEFAULT_BACKUP_COMPONENTS), compact=True)
    return (
        f"{day_label} {settings['backup_schedule_hour']:02d}:{settings['backup_schedule_minute']:02d} | "
        f"{mode_label} | {components_label}"
    )


def summarize_backup_notification_settings(settings: dict[str, Any]) -> str:
    if not settings.get("backup_notification_enabled"):
        return "E-posta bildirimi kapalı."

    event_labels: list[str] = []
    if settings.get("backup_notification_on_success"):
        event_labels.append("Başarı")
    if settings.get("backup_notification_on_failure"):
        event_labels.append("Hata")
    event_summary = " + ".join(event_labels) if event_labels else "Seçim yok"
    recipient = str(settings.get("backup_notification_email", "")).strip() or "tanımsız"
    return f"{recipient} | {event_summary}"


def _has_active_jobs() -> bool:
    ensure_runtime_dirs()

    for path in JOBS_DIR.glob("*.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        if job.get("status") in ACTIVE_JOB_STATUSES:
            return True

    return False


def _ensure_no_active_jobs() -> None:
    if _has_active_jobs():
        raise ServiceError("Bekleyen ya da çalışan bir yedekleme işlemi var. Yeni iş başlatmadan önce mevcut işi tamamlayın.")


def _spawn_job(job_path: Path) -> None:
    subprocess.Popen(
        _resolve_runner_command(job_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        cwd=str(BASE_DIR),
    )


def _resolve_runner_command(job_path: Path) -> list[str]:
    direct_command = [sys.executable, str(JOB_RUNNER), str(job_path)]

    if RUNNER_MODE not in ALLOWED_RUNNER_MODES:
        raise ServiceError(
            f"Geçersiz runner modu: {RUNNER_MODE}. Desteklenen değerler: auto, direct, sudo."
        )

    if RUNNER_MODE == "direct":
        return direct_command

    if RUNNER_MODE == "auto" and os.geteuid() == 0:
        return direct_command

    return _resolve_sudo_runner_command(job_path)


def _resolve_sudo_runner_command(job_path: Path) -> list[str]:
    if not PRIVILEGED_JOB_RUNNER.exists():
        raise ServiceError(
            "Root gerektiren işleri başlatmak için ayrı runner bulunamadı. "
            f"Beklenen yol: {PRIVILEGED_JOB_RUNNER}"
        )
    if not os.access(PRIVILEGED_JOB_RUNNER, os.X_OK):
        raise ServiceError(f"Ayrı runner çalıştırılabilir değil: {PRIVILEGED_JOB_RUNNER}")

    if not shutil.which("sudo"):
        raise ServiceError("sudo bulunamadı. Root runner başlatılamıyor.")

    try:
        result = subprocess.run(
            ["sudo", "-n", str(PRIVILEGED_JOB_RUNNER), "--check"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        raise ServiceError("Root runner kontrolü zaman aşımına uğradı.") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ServiceError(
            "Root runner kullanılamıyor. CyberPanel entegrasyon installer'ını çalıştırın "
            "ve sudoers ayarını doğrulayın."
            + (f" Ayrıntı: {detail}" if detail else "")
        )

    return ["sudo", "-n", str(PRIVILEGED_JOB_RUNNER), str(job_path)]


def _resolve_schedule_command(config_path: Path) -> list[str]:
    if RUNNER_MODE not in ALLOWED_RUNNER_MODES:
        raise ServiceError(
            f"Geçersiz runner modu: {RUNNER_MODE}. Desteklenen değerler: auto, direct, sudo."
        )

    if RUNNER_MODE == "direct" or (RUNNER_MODE == "auto" and os.geteuid() == 0):
        return [sys.executable, str(SCHEDULE_MANAGER), "apply", str(config_path)]

    if not PRIVILEGED_JOB_RUNNER.exists():
        raise ServiceError(
            "Zamanlama ayarını uygulamak için ayrı runner bulunamadı. "
            f"Beklenen yol: {PRIVILEGED_JOB_RUNNER}"
        )
    if not os.access(PRIVILEGED_JOB_RUNNER, os.X_OK):
        raise ServiceError(f"Ayrı runner çalıştırılabilir değil: {PRIVILEGED_JOB_RUNNER}")
    if not shutil.which("sudo"):
        raise ServiceError("sudo bulunamadı. Zamanlama ayarı uygulanamıyor.")

    try:
        result = subprocess.run(
            ["sudo", "-n", str(PRIVILEGED_JOB_RUNNER), "--check"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        raise ServiceError("Root runner kontrolü zaman aşımına uğradı.") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ServiceError(
            "Zamanlama ayarı uygulanamıyor. CyberPanel entegrasyon installer'ını çalıştırın "
            "ve sudoers ayarını doğrulayın."
            + (f" Ayrıntı: {detail}" if detail else "")
        )

    return ["sudo", "-n", str(PRIVILEGED_JOB_RUNNER), "--apply-schedule", str(config_path)]


def apply_backup_schedule(settings: dict[str, Any]) -> None:
    ensure_runtime_dirs()
    request_path = UI_STATE_DIR / f"schedule-request-{uuid.uuid4().hex}.json"
    _write_json(request_path, settings)

    try:
        result = subprocess.run(
            _resolve_schedule_command(request_path),
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(BASE_DIR),
        )
    except subprocess.TimeoutExpired as exc:
        raise ServiceError("Zamanlama ayarı uygulanırken zaman aşımı oluştu.") from exc
    finally:
        try:
            request_path.unlink()
        except FileNotFoundError:
            pass

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ServiceError(detail or "Zamanlama ayarı uygulanamadı.")


def create_job(job_type: str, command: list[str], env: dict[str, str] | None = None, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_runtime_dirs()

    job_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    job = {
        "id": job_id,
        "type": job_type,
        "status": "queued",
        "created_at": _now_iso(),
        "command": command,
        "env": env or {},
        "meta": meta or {},
        "log_path": str(_log_file(job_id)),
    }
    job_path = _job_file(job_id)
    _write_json(job_path, job)
    _spawn_job(job_path)
    return job


def _validate_script(path: Path, label: str) -> None:
    if not path.exists():
        raise ServiceError(f"{label} betiği bulunamadı: {path}")
    if not os.access(path, os.X_OK):
        raise ServiceError(f"{label} betiği çalıştırılabilir değil: {path}")


def start_backup_job(
    mode: str,
    timeout_minutes: Any = None,
    components: Any = None,
    *,
    persist_manual_defaults: bool = True,
) -> dict[str, Any]:
    if mode not in ALLOWED_BACKUP_MODES:
        raise ServiceError(f"Geçersiz yedekleme modu: {mode}")

    ui_settings = load_ui_settings()
    validated_timeout_minutes = validate_backup_timeout_minutes(timeout_minutes)
    validated_components = validate_backup_components(
        components,
        default=ui_settings["backup_default_components"],
    )
    _validate_script(BACKUP_SCRIPT, "Backup")
    _ensure_no_active_jobs()

    if persist_manual_defaults:
        save_ui_settings(
            {
                "backup_timeout_minutes": validated_timeout_minutes,
                "backup_default_components": validated_components,
            }
        )

    return create_job(
        job_type="backup",
        command=[str(BACKUP_SCRIPT)],
        env={
            "BACKUP_MODE": mode,
            "BACKUP_TIMEOUT_MINUTES": str(validated_timeout_minutes),
            "BACKUP_COMPONENTS": ",".join(validated_components),
        },
        meta={
            "mode": mode,
            "timeout_minutes": validated_timeout_minutes,
            "components": validated_components,
            "components_label": summarize_backup_components(validated_components, compact=True),
            "profile_key": backup_profile_key(validated_components),
            "notify_enabled": bool(ui_settings.get("backup_notification_enabled")),
            "notify_email": str(ui_settings.get("backup_notification_email", "")).strip(),
            "notify_on_success": bool(ui_settings.get("backup_notification_on_success")),
            "notify_on_failure": bool(ui_settings.get("backup_notification_on_failure")),
        },
    )


def update_backup_schedule(enabled: Any, hour: Any, minute: Any, mode: Any, components: Any, weekdays: Any) -> dict[str, Any]:
    current_settings = load_ui_settings()
    validated_schedule = validate_backup_schedule_settings(enabled, hour, minute, mode, components, weekdays)
    candidate_settings = {
        **current_settings,
        **validated_schedule,
    }
    apply_backup_schedule(candidate_settings)
    return save_ui_settings(validated_schedule)


def update_backup_notifications(enabled: Any, email: Any, on_success: Any, on_failure: Any) -> dict[str, Any]:
    validated_notifications = validate_backup_notification_settings(enabled, email, on_success, on_failure)
    return save_ui_settings(validated_notifications)


def start_restore_job(target_file: str, confirm_host: str, skip_db: bool, skip_files: bool, skip_configs: bool, skip_services: bool) -> dict[str, Any]:
    if not BACKUP_RE.match(target_file):
        raise ServiceError("Hedef yedek dosyası geçerli değil.")
    if confirm_host != HOST_FQDN:
        raise ServiceError(f"Onay için mevcut sunucunun FQDN değeri yazılmalıdır: {HOST_FQDN}")

    _validate_script(RESTORE_SCRIPT, "Restore")
    _ensure_no_active_jobs()

    command = [
        str(RESTORE_SCRIPT),
        "--target-file",
        target_file,
        "--confirm-host",
        confirm_host,
        "--apply",
    ]

    if skip_db:
        command.append("--skip-db")
    if skip_files:
        command.append("--skip-files")
    if skip_configs:
        command.append("--skip-configs")
    if skip_services:
        command.append("--skip-services")

    return create_job(
        job_type="restore",
        command=command,
        meta={
            "target_file": target_file,
            "confirm_host": confirm_host,
            "skip_db": skip_db,
            "skip_files": skip_files,
            "skip_configs": skip_configs,
            "skip_services": skip_services,
        },
    )


def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
    ensure_runtime_dirs()
    jobs: list[dict[str, Any]] = []

    for path in sorted(JOBS_DIR.glob("*.json"), reverse=True):
        try:
            jobs.append(_public_job_view(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError):
            continue

    jobs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return jobs[:limit]


def get_job(job_id: str) -> dict[str, Any]:
    return _public_job_view(_read_job_record(job_id))


def read_job_log(job_id: str, max_chars: int = 20000) -> str:
    log_path = _log_file(job_id)
    if not log_path.exists():
        return ""

    content = log_path.read_text(encoding="utf-8", errors="replace")
    if len(content) > max_chars:
        return content[-max_chars:]
    return content


def get_job_log_path(job_id: str) -> Path:
    log_path = _log_file(job_id)
    if not log_path.exists():
        raise ServiceError("İş günlüğü bulunamadı.")
    return log_path


def list_remote_backups() -> list[dict[str, Any]]:
    ensure_runtime_dirs()

    try:
        result = subprocess.run(
            ["rclone", "lsf", f"{RCLONE_REMOTE}:{DRIVE_FOLDER}", "--files-only"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise ServiceError("rclone bulunamadı.") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() or exc.stdout.strip() or "rclone lsf komutu başarısız oldu."
        raise ServiceError(stderr) from exc
    except subprocess.TimeoutExpired as exc:
        raise ServiceError("Uzak yedek listesi zaman aşımına uğradı.") from exc

    chains: dict[str, dict[str, Any]] = {}
    for line in result.stdout.splitlines():
        match = BACKUP_RE.match(line.strip())
        if not match:
            continue

        if match.group("host") != HOST_SLUG:
            continue

        chain_id = match.group("chain")
        profile_key = match.group("profile") or "legacy-all"
        grouping_key = f"{profile_key}:{chain_id}"
        chain_components = components_from_profile_key(profile_key)
        chain_entry = chains.setdefault(
            grouping_key,
            {
                "chain_id": chain_id,
                "profile_key": profile_key,
                "components": chain_components,
                "components_label": summarize_backup_components(chain_components, compact=True),
                "host": match.group("host"),
                "backups": [],
            },
        )
        chain_entry["backups"].append(
            {
                "file": line.strip(),
                "kind": match.group("kind"),
                "timestamp": match.group("timestamp"),
            }
        )

    for chain in chains.values():
        chain["backups"].sort(key=lambda item: item["timestamp"])
        chain["latest_timestamp"] = chain["backups"][-1]["timestamp"]
        chain["full_timestamp"] = chain["backups"][0]["timestamp"]
        chain["backup_count"] = len(chain["backups"])

    return sorted(chains.values(), key=lambda item: item["latest_timestamp"], reverse=True)


def latest_backup_summary(backups: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not backups:
        return None

    latest_chain = backups[0]
    latest_item = latest_chain["backups"][-1]
    return {
        "chain_id": latest_chain["chain_id"],
        "profile_key": latest_chain["profile_key"],
        "components": latest_chain["components"],
        "components_label": latest_chain["components_label"],
        "backup_count": latest_chain["backup_count"],
        "latest_file": latest_item["file"],
        "latest_timestamp": latest_item["timestamp"],
        "latest_kind": latest_item["kind"],
        "full_timestamp": latest_chain["full_timestamp"],
    }


def active_job_summary(jobs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for job in jobs:
        if job.get("status") in ACTIVE_JOB_STATUSES:
            return {
                "id": job.get("id", ""),
                "type": job.get("type", ""),
                "status": job.get("status", ""),
                "progress_percent": job.get("progress_percent", 0),
                "progress_label": job.get("progress_label", ""),
                "components_label": str((job.get("meta") or {}).get("components_label", "")),
            }
    return None


def dashboard_state() -> dict[str, Any]:
    jobs = list_jobs()
    ui_settings = load_ui_settings()

    try:
        backups = list_remote_backups()
        remote_error = ""
    except ServiceError as exc:
        backups = []
        remote_error = str(exc)

    return {
        "jobs": jobs,
        "backups": backups,
        "remote_error": remote_error,
        "latest_backup_summary": latest_backup_summary(backups),
        "active_job_summary": active_job_summary(jobs),
        "backup_settings": ui_settings,
        "backup_schedule_summary": summarize_backup_schedule(ui_settings),
        "backup_notification_summary": summarize_backup_notification_settings(ui_settings),
    }


def dashboard_context() -> dict[str, Any]:
    state = dashboard_state()

    return {
        "backups": state["backups"],
        "remote_error": state["remote_error"],
        "jobs": state["jobs"],
        "latest_backup_summary": state["latest_backup_summary"],
        "active_job_summary": state["active_job_summary"],
        "backup_settings": state["backup_settings"],
        "backup_schedule_summary": state["backup_schedule_summary"],
        "backup_notification_summary": state["backup_notification_summary"],
        "host_fqdn": HOST_FQDN,
        "host_slug": HOST_SLUG,
        "backup_script": str(BACKUP_SCRIPT),
        "restore_script": str(RESTORE_SCRIPT),
        "runner_mode": RUNNER_MODE,
        "privileged_runner": str(PRIVILEGED_JOB_RUNNER),
    }
