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
    parser.add_argument(
        "--components",
        default=None,
        help="Virgülle ayrılmış yedek bileşenleri. Verilmezse kayıtlı zamanlama bileşenleri kullanılır.",
    )
    args = parser.parse_args()

    settings = services.load_ui_settings()
    timeout_minutes = args.timeout_minutes
    if timeout_minutes is None:
        timeout_minutes = settings["backup_timeout_minutes"]

    components = args.components
    if components is None:
        components = settings["backup_schedule_components"]

    try:
        job = services.start_backup_job(args.mode, timeout_minutes, components, persist_manual_defaults=False)
    except services.ServiceError as exc:
        print(f"[schedule-runner] {exc}", file=sys.stderr)
        return 1

    timeout_label = "limitsiz" if timeout_minutes == 0 else f"{timeout_minutes} dakika"
    print(
        f"[schedule-runner] job_id={job['id']} mode={args.mode} components={job['meta'].get('profile_key', 'all')} timeout={timeout_label}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
