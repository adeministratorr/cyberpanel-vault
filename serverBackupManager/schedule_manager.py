#!/usr/bin/env python3

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
import shlex
import sys
from pathlib import Path

import services


APP_DIR = Path(__file__).resolve().parent
UI_STATE_DIR = Path(os.environ.get("CYBERPANEL_SERVER_BACKUP_UI_STATE_DIR", "/var/lib/cyberpanel-backup-ui"))
CRON_FILE = Path(os.environ.get("CYBERPANEL_SERVER_BACKUP_CRON_FILE", "/etc/cron.d/cyberpanel-vault"))
CRON_LOCK_FILE = UI_STATE_DIR / ".schedule.lock"
SCHEDULE_RUNNER = APP_DIR / "schedule_runner.py"
CRON_WEEKDAY_MAP = {
    "mon": "1",
    "tue": "2",
    "wed": "3",
    "thu": "4",
    "fri": "5",
    "sat": "6",
    "sun": "0",
}


class ScheduleManagerError(RuntimeError):
    pass


def require_root() -> None:
    if os.geteuid() != 0:
        raise ScheduleManagerError("Zamanlama yöneticisi root olarak çalışmalıdır.")


@contextmanager
def cron_lock():
    UI_STATE_DIR.mkdir(parents=True, exist_ok=True)
    with CRON_LOCK_FILE.open("a+", encoding="utf-8") as lock_file:
        os.fchmod(lock_file.fileno(), services.UI_FILE_MODE)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def validate_config_path(path_value: str) -> Path:
    candidate = Path(path_value).resolve(strict=True)
    ui_root = UI_STATE_DIR.resolve()

    try:
        candidate.relative_to(ui_root)
    except ValueError as exc:
        raise ScheduleManagerError(f"Zamanlama dosyası izin verilen klasörde değil: {candidate}") from exc

    return candidate


def load_candidate_settings(config_path: Path) -> dict:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScheduleManagerError(f"Zamanlama dosyası okunamadı: {config_path}") from exc

    validated_schedule = services.validate_backup_schedule_settings(
        payload.get("backup_schedule_enabled"),
        payload.get("backup_schedule_hour"),
        payload.get("backup_schedule_minute"),
        payload.get("backup_schedule_mode"),
        payload.get("backup_schedule_components"),
        payload.get("backup_schedule_weekdays"),
    )

    merged = services.load_ui_settings()
    merged.update(validated_schedule)
    return merged


def cron_weekday_expression(weekdays: list[str]) -> str:
    if weekdays == services.WEEKDAY_ORDER:
        return "*"

    ordered = [CRON_WEEKDAY_MAP[day] for day in services.WEEKDAY_ORDER if day in weekdays]
    if not ordered:
        raise ScheduleManagerError("Cron için en az bir gün gereklidir.")
    return ",".join(ordered)


def render_cron(settings: dict) -> str:
    if not SCHEDULE_RUNNER.exists():
        raise ScheduleManagerError(f"schedule_runner.py bulunamadı: {SCHEDULE_RUNNER}")

    dow = cron_weekday_expression(settings["backup_schedule_weekdays"])
    hour = settings["backup_schedule_hour"]
    minute = settings["backup_schedule_minute"]
    mode = shlex.quote(settings["backup_schedule_mode"])
    components = shlex.quote(",".join(settings["backup_schedule_components"]))
    python_bin = shlex.quote("python3")
    schedule_runner = shlex.quote(str(SCHEDULE_RUNNER))
    command = (
        f"{python_bin} {schedule_runner} --mode {mode} --components {components} "
        ">>/var/log/cyberpanel_backup.log 2>&1"
    )

    return (
        "# Managed by CyberPanel Vault\n"
        "SHELL=/bin/bash\n"
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
        f"{minute} {hour} * * {dow} root {command}\n"
    )


def apply_schedule(config_path_value: str) -> int:
    require_root()
    config_path = validate_config_path(config_path_value)
    settings = load_candidate_settings(config_path)

    with cron_lock():
        if not settings["backup_schedule_enabled"]:
            try:
                CRON_FILE.unlink()
            except FileNotFoundError:
                pass
            print("Otomatik yedekleme kapatıldı.")
            return 0

        cron_content = render_cron(settings)
        CRON_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_path = CRON_FILE.with_suffix(".tmp")
        temp_path.write_text(cron_content, encoding="utf-8")
        temp_path.chmod(0o644)
        temp_path.replace(CRON_FILE)
        CRON_FILE.chmod(0o644)
        print(f"Zamanlama uygulandı: {services.summarize_backup_schedule(settings)}")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="CyberPanel Vault zamanlama yöneticisi")
    subparsers = parser.add_subparsers(dest="command", required=True)

    apply_parser = subparsers.add_parser("apply", help="Aday zamanlama dosyasını uygula")
    apply_parser.add_argument("config_path", help="UI state dizinindeki JSON dosyası")

    args = parser.parse_args()

    try:
        if args.command == "apply":
            return apply_schedule(args.config_path)
    except (ScheduleManagerError, services.ServiceError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
