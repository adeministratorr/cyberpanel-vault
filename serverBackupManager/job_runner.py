#!/usr/bin/env python3

import json
import os
import re
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


UI_STATE_DIR = Path(os.environ.get("CYBERPANEL_SERVER_BACKUP_UI_STATE_DIR", "/var/lib/cyberpanel-backup-ui"))
JOBS_DIR = UI_STATE_DIR / "jobs"
BACKUP_SCRIPT = Path(os.environ.get("CYBERPANEL_SERVER_BACKUP_SCRIPT", "/usr/local/bin/cyberpanel_full_backup.sh"))
RESTORE_SCRIPT = Path(os.environ.get("CYBERPANEL_SERVER_RESTORE_SCRIPT", "/usr/local/bin/cyberpanel_restore.sh"))
HOST_FQDN = socket.getfqdn() or socket.gethostname()
BACKUP_RE = re.compile(
    r"^backup__host-([A-Za-z0-9._-]+)__chain-(\d{8}T\d{6})__type-(full|incremental)__at-(\d{8}T\d{6})\.tar\.gz\.enc$"
)
JOB_ID_RE = re.compile(r"^\d{8}T\d{6}-[0-9a-f]{8}$")
ALLOWED_BACKUP_MODES = {"auto", "full", "incremental"}


class JobRunnerError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.chmod(0o600)
    tmp_path.replace(path)
    path.chmod(0o600)


def validate_job_path(job_path: Path) -> Path:
    try:
        resolved_job_path = job_path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise JobRunnerError(f"İş dosyası bulunamadı: {job_path}") from exc

    resolved_jobs_dir = JOBS_DIR.resolve()
    if resolved_job_path.parent != resolved_jobs_dir:
        raise JobRunnerError(f"İş dosyası izin verilen klasörde değil: {resolved_job_path}")
    if not JOB_ID_RE.match(resolved_job_path.stem):
        raise JobRunnerError(f"İş kimliği geçerli değil: {resolved_job_path.stem}")

    return resolved_job_path


def load_job(job_path: Path) -> dict:
    return json.loads(job_path.read_text(encoding="utf-8"))


def build_job_command(job: dict) -> tuple[list[str], dict[str, str]]:
    job_type = job.get("type")
    meta = job.get("meta") or {}
    base_env = os.environ.copy()

    if job_type == "backup":
        mode = meta.get("mode", "")
        if mode not in ALLOWED_BACKUP_MODES:
            raise JobRunnerError(f"Geçersiz backup modu: {mode}")
        return [str(BACKUP_SCRIPT)], {**base_env, "BACKUP_MODE": mode}

    if job_type == "restore":
        target_file = str(meta.get("target_file", ""))
        confirm_host = str(meta.get("confirm_host", ""))
        if not BACKUP_RE.match(target_file):
            raise JobRunnerError(f"Geçersiz restore hedefi: {target_file}")
        if confirm_host != HOST_FQDN:
            raise JobRunnerError(
                f"Restore onayı başarısız. Beklenen FQDN: {HOST_FQDN}, verilen: {confirm_host}"
            )

        command = [
            str(RESTORE_SCRIPT),
            "--target-file",
            target_file,
            "--confirm-host",
            confirm_host,
            "--apply",
        ]

        if meta.get("skip_db"):
            command.append("--skip-db")
        if meta.get("skip_files"):
            command.append("--skip-files")
        if meta.get("skip_configs"):
            command.append("--skip-configs")
        if meta.get("skip_services"):
            command.append("--skip-services")

        return command, base_env

    raise JobRunnerError(f"Desteklenmeyen iş türü: {job_type}")


def ensure_script_ready(path: Path, label: str) -> None:
    if not path.exists():
        raise JobRunnerError(f"{label} betiği bulunamadı: {path}")
    if not os.access(path, os.X_OK):
        raise JobRunnerError(f"{label} betiği çalıştırılabilir değil: {path}")


def mark_job_failed(job_path: Path, job: dict, message: str) -> None:
    job["finished_at"] = now_iso()
    job["exit_code"] = 1
    job["status"] = "failed"
    job["error"] = message
    write_json(job_path, job)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: job_runner.py <job_json_path>")

    os.umask(0o077)

    try:
        job_path = validate_job_path(Path(sys.argv[1]))
        job = load_job(job_path)
    except (JobRunnerError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc))

    try:
        if job.get("type") == "backup":
            ensure_script_ready(BACKUP_SCRIPT, "Backup")
        elif job.get("type") == "restore":
            ensure_script_ready(RESTORE_SCRIPT, "Restore")
        command, env = build_job_command(job)
    except JobRunnerError as exc:
        mark_job_failed(job_path, job, str(exc))
        raise SystemExit(str(exc))

    log_path = Path(job["log_path"]).resolve()
    if log_path.parent != JOBS_DIR.resolve():
        raise SystemExit(f"Log dosyası izin verilen klasörde değil: {log_path}")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.chmod(0o700)
    log_path.touch(mode=0o600, exist_ok=True)
    log_path.chmod(0o600)

    job["status"] = "running"
    job["started_at"] = now_iso()
    write_json(job_path, job)

    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"[runner] started_at={job['started_at']}\n")
        log_file.write(f"[runner] type={job.get('type', '')}\n")
        log_file.write(f"[runner] command={' '.join(command)}\n")
        log_file.flush()

        try:
            process = subprocess.Popen(
                command,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            )
        except OSError as exc:
            log_file.write(f"[runner] start_failed={exc}\n")
            log_file.flush()
            mark_job_failed(job_path, job, str(exc))
            return 1

        return_code = process.wait()

    job["finished_at"] = now_iso()
    job["exit_code"] = return_code
    job["status"] = "completed" if return_code == 0 else "failed"
    write_json(job_path, job)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
