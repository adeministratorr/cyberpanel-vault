#!/usr/bin/env python3

import argparse
import sys

import services


def main() -> int:
    parser = argparse.ArgumentParser(description="CyberPanel Vault zamanlanmış yedek çalıştırıcısı")
    parser.add_argument(
        "--mode",
        choices=sorted(services.ALLOWED_BACKUP_MODES),
        default="auto",
        help="Çalıştırılacak yedekleme modu",
    )
    parser.add_argument(
        "--timeout-minutes",
        type=int,
        default=None,
        help="Dakika cinsinden üst süre sınırı. Verilmezse arayüzdeki kayıtlı değer kullanılır.",
    )
    args = parser.parse_args()

    timeout_minutes = args.timeout_minutes
    if timeout_minutes is None:
        timeout_minutes = services.load_ui_settings()["backup_timeout_minutes"]

    try:
        job = services.start_backup_job(args.mode, timeout_minutes)
    except services.ServiceError as exc:
        print(f"[schedule-runner] {exc}", file=sys.stderr)
        return 1

    timeout_label = "limitsiz" if timeout_minutes == 0 else f"{timeout_minutes} dakika"
    print(
        f"[schedule-runner] job_id={job['id']} mode={args.mode} timeout={timeout_label}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
