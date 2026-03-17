#!/usr/bin/env python3

import json
import os
import re
import signal
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
UI_DIR_MODE = 0o2770
UI_FILE_MODE = 0o660
BACKUP_RE = re.compile(
    r"^backup__host-([A-Za-z0-9._-]+)(?:__profile-([A-Za-z0-9._-]+))?__chain-(\d{8}T\d{6})__type-(full|incremental)__at-(\d{8}T\d{6})\.tar\.gz\.enc$"
)
JOB_ID_RE = re.compile(r"^\d{8}T\d{6}-[0-9a-f]{8}$")
ALLOWED_BACKUP_MODES = {"auto", "full", "incremental"}
BACKUP_COMPONENT_ORDER = ["databases", "site", "server", "email"]
DEFAULT_BACKUP_TIMEOUT_MINUTES = 120
MIN_BACKUP_TIMEOUT_MINUTES = 0
MAX_BACKUP_TIMEOUT_MINUTES = 1440
TIMEOUT_GRACE_SECONDS = 30
CYBERPANEL_DJANGO_ROOT = Path(
    os.environ.get("CYBERPANEL_SERVER_BACKUP_DJANGO_ROOT", str(Path(__file__).resolve().parent.parent))
)
CYBERPANEL_DJANGO_SETTINGS_MODULE = os.environ.get(
    "CYBERPANEL_SERVER_BACKUP_DJANGO_SETTINGS_MODULE",
    "CyberCP.settings",
)
DEFAULT_NOTIFY_FROM = os.environ.get("CYBERPANEL_SERVER_BACKUP_NOTIFY_FROM", f"root@{HOST_FQDN}")
DEFAULT_NOTIFY_SUBJECT_PREFIX = os.environ.get("CYBERPANEL_SERVER_BACKUP_NOTIFY_SUBJECT_PREFIX", "[CyberPanel Vault]")


class JobRunnerError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.chmod(UI_FILE_MODE)
    tmp_path.replace(path)
    path.chmod(UI_FILE_MODE)


def parse_timeout_minutes(value: object) -> int:
    try:
        timeout_minutes = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise JobRunnerError("Yedek süresi dakika cinsinden tam sayı olmalıdır.") from exc

    if timeout_minutes < MIN_BACKUP_TIMEOUT_MINUTES or timeout_minutes > MAX_BACKUP_TIMEOUT_MINUTES:
        raise JobRunnerError(
            f"Yedek süresi {MIN_BACKUP_TIMEOUT_MINUTES} ile {MAX_BACKUP_TIMEOUT_MINUTES} dakika arasında olmalıdır."
        )

    return timeout_minutes


def parse_backup_components(value: object) -> list[str]:
    if value is None:
        return list(BACKUP_COMPONENT_ORDER)

    if isinstance(value, (list, tuple, set)):
        candidates = [str(item).strip().lower() for item in value if str(item).strip()]
    else:
        raw = str(value).strip().lower()
        if not raw or raw == "all":
            return list(BACKUP_COMPONENT_ORDER)
        candidates = [item.strip() for item in raw.split(",") if item.strip()]

    if not candidates:
        raise JobRunnerError("Yedek bileşenleri boş bırakılamaz.")

    invalid_components = [item for item in candidates if item not in BACKUP_COMPONENT_ORDER]
    if invalid_components:
        raise JobRunnerError(f"Geçersiz yedek bileşenleri: {', '.join(invalid_components)}")

    components: list[str] = []
    for component in BACKUP_COMPONENT_ORDER:
        if component in candidates:
            components.append(component)
    if not components:
        raise JobRunnerError("Yedek bileşenleri boş bırakılamaz.")
    return components


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


def resolve_log_path(job: dict) -> Path | None:
    raw_log_path = str(job.get("log_path", "")).strip()
    if not raw_log_path:
        return None

    resolved_log_path = Path(raw_log_path).resolve()
    if resolved_log_path.parent != JOBS_DIR.resolve():
        return None
    return resolved_log_path


def load_job(job_path: Path) -> dict:
    return json.loads(job_path.read_text(encoding="utf-8"))


def build_job_command(job: dict) -> tuple[list[str], dict[str, str], int | None]:
    job_type = job.get("type")
    meta = job.get("meta") or {}
    raw_job_env = job.get("env") or {}
    base_env = {**os.environ.copy(), **{str(key): str(value) for key, value in raw_job_env.items()}}

    if job_type == "backup":
        mode = meta.get("mode", "")
        if mode not in ALLOWED_BACKUP_MODES:
            raise JobRunnerError(f"Geçersiz backup modu: {mode}")
        timeout_minutes = parse_timeout_minutes(meta.get("timeout_minutes", DEFAULT_BACKUP_TIMEOUT_MINUTES))
        components = parse_backup_components(meta.get("components") or raw_job_env.get("BACKUP_COMPONENTS"))
        timeout_seconds = timeout_minutes * 60 if timeout_minutes > 0 else None
        return [
            str(BACKUP_SCRIPT)
        ], {
            **base_env,
            "BACKUP_MODE": mode,
            "BACKUP_TIMEOUT_MINUTES": str(timeout_minutes),
            "BACKUP_COMPONENTS": ",".join(components),
        }, timeout_seconds

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

        return command, base_env, None

    raise JobRunnerError(f"Desteklenmeyen iş türü: {job_type}")


def _notification_requested(job: dict) -> bool:
    if job.get("type") != "backup":
        return False

    meta = job.get("meta") or {}
    if not meta.get("notify_enabled"):
        return False
    if not str(meta.get("notify_email", "")).strip():
        return False

    status = str(job.get("status", ""))
    if status == "completed":
        return bool(meta.get("notify_on_success"))
    if status == "failed":
        return bool(meta.get("notify_on_failure"))
    return False


def _load_cyberpanel_mail_stack():
    django_root = str(CYBERPANEL_DJANGO_ROOT)
    if django_root not in sys.path:
        sys.path.insert(0, django_root)

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", CYBERPANEL_DJANGO_SETTINGS_MODULE)

    try:
        import django
        django.setup()
        from django.conf import settings as django_settings
        from django.contrib.auth import get_user_model
        from django.core.mail import send_mail
    except Exception as exc:  # noqa: BLE001
        raise JobRunnerError(f"CyberPanel mail altyapısı yüklenemedi: {exc}") from exc

    return send_mail, get_user_model(), django_settings


def _resolve_notification_recipient(job: dict, user_model) -> str:
    meta = job.get("meta") or {}
    if not meta.get("notify_use_admin"):
        return str(meta.get("notify_email", "")).strip()

    admin_candidate = (
        user_model.objects.filter(is_superuser=True)
        .exclude(email__isnull=True)
        .exclude(email__exact="")
        .filter(username__iexact="admin")
        .order_by("id")
        .first()
    )
    if admin_candidate is None:
        admin_candidate = (
            user_model.objects.filter(is_superuser=True)
            .exclude(email__isnull=True)
            .exclude(email__exact="")
            .order_by("id")
            .first()
        )
    return str(getattr(admin_candidate, "email", "") or "").strip()


def _append_notification_log(log_path: Path | None, line: str) -> None:
    if log_path is None:
        return

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch(mode=UI_FILE_MODE, exist_ok=True)
        log_path.chmod(UI_FILE_MODE)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")
    except OSError:
        pass


def _notification_subject(job: dict) -> str:
    status = str(job.get("status", ""))
    meta = job.get("meta") or {}
    status_label = "başarılı" if status == "completed" else "hatalı"
    mode = str(meta.get("mode", "")).strip() or "manual"
    components = str(meta.get("components_label", "")).strip() or "Tüm bileşenler"
    return f"{DEFAULT_NOTIFY_SUBJECT_PREFIX} {HOST_FQDN} yedek {status_label} ({mode} | {components})"


def _notification_body(job: dict, log_path: Path | None) -> str:
    meta = job.get("meta") or {}
    timeout_label = ""
    if "timeout_minutes" in meta:
        timeout_label = "limitsiz" if meta.get("timeout_minutes") == 0 else f"{meta.get('timeout_minutes')} dakika"

    log_tail = ""
    if log_path and log_path.exists():
        try:
            log_tail = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            log_tail = ""
        if len(log_tail) > 8000:
            log_tail = log_tail[-8000:]

    lines = [
        "CyberPanel Vault yedek işi tamamlandı.",
        "",
        f"Sunucu: {HOST_FQDN}",
        f"İş kimliği: {job.get('id', '')}",
        f"Durum: {job.get('status', '')}",
        f"Mod: {meta.get('mode', '')}",
        f"Kapsam: {meta.get('components_label', '')}",
        f"Süre sınırı: {timeout_label}",
        f"Oluşturuldu: {job.get('created_at', '')}",
        f"Başladı: {job.get('started_at', '')}",
        f"Bitti: {job.get('finished_at', '')}",
        f"Çıkış kodu: {job.get('exit_code', '')}",
    ]
    if job.get("error"):
        lines.append(f"Hata: {job.get('error', '')}")
    if log_path is not None:
        lines.append(f"Log dosyası: {log_path}")
    if log_tail:
        lines.extend(["", "Son log satırları:", "", log_tail])
    return "\n".join(lines)


def send_job_notification(job: dict) -> None:
    if not _notification_requested(job):
        return

    log_path = resolve_log_path(job)
    try:
        send_mail, user_model, django_settings = _load_cyberpanel_mail_stack()
    except JobRunnerError as exc:
        _append_notification_log(log_path, f"[notify] cyberpanel_mail_unavailable={exc}")
        return

    recipient = _resolve_notification_recipient(job, user_model)
    if not recipient:
        _append_notification_log(log_path, "[notify] recipient_missing=1")
        return

    from_email = (
        str(getattr(django_settings, "DEFAULT_FROM_EMAIL", "") or "").strip()
        or str(getattr(django_settings, "SERVER_EMAIL", "") or "").strip()
        or DEFAULT_NOTIFY_FROM
    )

    try:
        result = send_mail(
            _notification_subject(job),
            _notification_body(job, log_path),
            from_email,
            [recipient],
            fail_silently=False,
        )
    except Exception as exc:  # noqa: BLE001
        _append_notification_log(log_path, f"[notify] send_failed={exc}")
        return

    if int(result or 0) < 1:
        _append_notification_log(log_path, "[notify] send_failed=no_message_sent")
        return

    _append_notification_log(log_path, f"[notify] sent_to={recipient}")


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
    send_job_notification(job)


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
        command, env, timeout_seconds = build_job_command(job)
    except JobRunnerError as exc:
        mark_job_failed(job_path, job, str(exc))
        raise SystemExit(str(exc))

    log_path = resolve_log_path(job)
    if log_path is None:
        raise SystemExit(f"Log dosyası izin verilen klasörde değil: {log_path}")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.chmod(UI_DIR_MODE)
    log_path.touch(mode=UI_FILE_MODE, exist_ok=True)
    log_path.chmod(UI_FILE_MODE)

    job["status"] = "running"
    job["started_at"] = now_iso()
    write_json(job_path, job)

    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"[runner] started_at={job['started_at']}\n")
        log_file.write(f"[runner] type={job.get('type', '')}\n")
        log_file.write(f"[runner] command={' '.join(command)}\n")
        if timeout_seconds is not None:
            log_file.write(f"[runner] timeout_seconds={timeout_seconds}\n")
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

        timed_out = False

        try:
            if timeout_seconds is None:
                return_code = process.wait()
            else:
                return_code = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            log_file.write(f"[runner] timeout_exceeded={timeout_seconds}\n")
            log_file.write("[runner] sending_signal=SIGTERM\n")
            log_file.flush()

            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

            try:
                return_code = process.wait(timeout=TIMEOUT_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                log_file.write("[runner] sending_signal=SIGKILL\n")
                log_file.flush()
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                return_code = process.wait()

        if timed_out:
            return_code = 124

    job["finished_at"] = now_iso()
    job["exit_code"] = return_code
    job["status"] = "completed" if return_code == 0 else "failed"
    if return_code == 124:
        job["error"] = "Yedekleme süresi sınırı aşıldı."
    write_json(job_path, job)
    send_job_notification(job)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
