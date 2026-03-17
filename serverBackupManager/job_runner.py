#!/usr/bin/env python3

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: job_runner.py <job_json_path>")

    os.umask(0o077)

    job_path = Path(sys.argv[1])
    job = json.loads(job_path.read_text(encoding="utf-8"))

    command = job["command"]
    env = os.environ.copy()
    env.update(job.get("env", {}))

    log_path = Path(job["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.chmod(0o700)
    log_path.touch(mode=0o600, exist_ok=True)
    log_path.chmod(0o600)

    job["status"] = "running"
    job["started_at"] = now_iso()
    write_json(job_path, job)

    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"[runner] started_at={job['started_at']}\n")
        log_file.write(f"[runner] command={' '.join(command)}\n")
        log_file.flush()

        process = subprocess.Popen(
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
        return_code = process.wait()

    job["finished_at"] = now_iso()
    job["exit_code"] = return_code
    job["status"] = "completed" if return_code == 0 else "failed"
    write_json(job_path, job)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
