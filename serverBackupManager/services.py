import json
import os
import re
import socket
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
UI_STATE_DIR = Path(os.environ.get("CYBERPANEL_SERVER_BACKUP_UI_STATE_DIR", "/var/lib/cyberpanel-backup-ui"))
JOBS_DIR = UI_STATE_DIR / "jobs"
BACKUP_SCRIPT = Path(os.environ.get("CYBERPANEL_SERVER_BACKUP_SCRIPT", "/usr/local/bin/cyberpanel_full_backup.sh"))
RESTORE_SCRIPT = Path(os.environ.get("CYBERPANEL_SERVER_RESTORE_SCRIPT", "/usr/local/bin/cyberpanel_restore.sh"))
JOB_RUNNER = BASE_DIR / "job_runner.py"
RCLONE_REMOTE = os.environ.get("RCLONE_REMOTE", "gdrive")
DRIVE_FOLDER = os.environ.get("DRIVE_FOLDER", "cyberpanel-backups")
HOST_FQDN = socket.getfqdn() or socket.gethostname()
HOST_SLUG = re.sub(r"[^A-Za-z0-9._-]+", "_", HOST_FQDN)
BACKUP_RE = re.compile(
    r"^backup__host-(?P<host>[A-Za-z0-9._-]+)__chain-(?P<chain>\d{8}T\d{6})__type-(?P<kind>full|incremental)__at-(?P<timestamp>\d{8}T\d{6})\.tar\.gz\.enc$"
)
JOB_ID_RE = re.compile(r"^\d{8}T\d{6}-[0-9a-f]{8}$")
ALLOWED_BACKUP_MODES = {"auto", "full", "incremental"}
ACTIVE_JOB_STATUSES = {"queued", "running"}


class ServiceError(RuntimeError):
    pass


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
    tmp_path.chmod(0o600)
    tmp_path.replace(path)
    path.chmod(0o600)


def ensure_runtime_dirs() -> None:
    UI_STATE_DIR.mkdir(parents=True, exist_ok=True)
    UI_STATE_DIR.chmod(0o700)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    JOBS_DIR.chmod(0o700)


def _validate_job_id(job_id: str) -> None:
    if not JOB_ID_RE.match(job_id):
        raise ServiceError("İş kimliği geçerli değil.")


def _read_job_record(job_id: str) -> dict[str, Any]:
    path = _job_file(job_id)
    if not path.exists():
        raise ServiceError("İş kaydı bulunamadı.")
    return json.loads(path.read_text(encoding="utf-8"))


def _public_job_view(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job.get("id", ""),
        "type": job.get("type", ""),
        "status": job.get("status", ""),
        "created_at": job.get("created_at", ""),
        "started_at": job.get("started_at", ""),
        "finished_at": job.get("finished_at", ""),
        "exit_code": job.get("exit_code"),
        "meta": job.get("meta", {}),
    }


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
        [sys.executable, str(JOB_RUNNER), str(job_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        cwd=str(BASE_DIR),
    )


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


def start_backup_job(mode: str) -> dict[str, Any]:
    if mode not in ALLOWED_BACKUP_MODES:
        raise ServiceError(f"Geçersiz yedekleme modu: {mode}")

    _validate_script(BACKUP_SCRIPT, "Backup")
    _ensure_no_active_jobs()
    return create_job(
        job_type="backup",
        command=[str(BACKUP_SCRIPT)],
        env={"BACKUP_MODE": mode},
        meta={"mode": mode},
    )


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
        chain_entry = chains.setdefault(
            chain_id,
            {
                "chain_id": chain_id,
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


def dashboard_context() -> dict[str, Any]:
    try:
        backups = list_remote_backups()
        remote_error = ""
    except ServiceError as exc:
        backups = []
        remote_error = str(exc)

    return {
        "backups": backups,
        "remote_error": remote_error,
        "jobs": list_jobs(),
        "host_fqdn": HOST_FQDN,
        "host_slug": HOST_SLUG,
        "backup_script": str(BACKUP_SCRIPT),
        "restore_script": str(RESTORE_SCRIPT),
    }
