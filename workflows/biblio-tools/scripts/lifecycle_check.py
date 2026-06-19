#!/usr/bin/env python3
"""Check process-lifecycle safeguards for Book Dragon helper processes."""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from runtime import process_is_alive, venv_python

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent


def _run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def check_mcp_smoke() -> tuple[bool, str]:
    try:
        python = str(venv_python())
    except RuntimeError:
        return False, "Project .venv Python not found."
    result = _run([
        python,
        "workflows/biblio-tools/scripts/mcp_smoke.py",
        "--command",
        "python",
        "--arg",
        "workflows/biblio-tools/scripts/launch.py",
        "--arg",
        "workflows/biblio-tools/scripts/server.py",
        "--cwd",
        str(PROJECT_ROOT),
    ])
    if result.returncode != 0:
        return False, result.stderr.strip() or result.stdout.strip()
    return True, "MCP handshake succeeded through launch.py."


def check_parent_death_cleanup() -> tuple[bool, str]:
    code = r"""
import subprocess
import sys

child = subprocess.Popen(
    [
        sys.executable,
        "workflows/biblio-tools/scripts/launch.py",
        "workflows/biblio-tools/scripts/server.py",
    ],
    stdin=subprocess.PIPE,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
print(child.pid, flush=True)
"""
    parent = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = parent.communicate(timeout=10)
    if parent.returncode != 0:
        return False, stderr.strip() or "Parent process failed."

    pid = int(stdout.strip().splitlines()[-1])
    time.sleep(1)
    if not process_is_alive(pid):
        return False, "Launcher exited before the watchdog window."
    # Poll with a timeout rather than a fixed sleep — the watchdog polls
    # every 10s, so 30s gives it at least two cycles even on a loaded machine.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if not process_is_alive(pid):
            return True, "Launcher cleaned itself up after parent death."
        time.sleep(2)
    return False, f"Launcher process {pid} survived parent death after 30s."


def check_reader_stale_pid_safety() -> tuple[bool, str]:
    pid_file = PROJECT_ROOT / "workflows/reddit-collector/.reader_server.pid"
    original = pid_file.read_text(encoding="utf-8") if pid_file.exists() else None
    payload = {
        "pid": os.getpid(),
        "port": 65534,
        "subreddit": "hfy",
        "started_at": "2000-01-01T00:00:00+00:00",
        "token": "not-a-real-reader",
    }
    try:
        pid_file.write_text(json.dumps(payload), encoding="utf-8")
        result = _run([
            sys.executable,
            "workflows/reddit-collector/scripts/build_reader.py",
            "--stop",
        ])
        if result.returncode != 0:
            return False, result.stderr.strip() or result.stdout.strip()
        if pid_file.exists():
            return False, "Stale reader PID metadata was not removed."
        return True, "Reader stale-PID safety check passed."
    finally:
        if original is not None:
            pid_file.write_text(original, encoding="utf-8")
        elif pid_file.exists():
            pid_file.unlink()



def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    checks = [
        ("MCP smoke", check_mcp_smoke),
        ("Parent-death cleanup", check_parent_death_cleanup),
        ("Reader stale-PID safety", check_reader_stale_pid_safety),
    ]
    failures = 0
    for label, fn in checks:
        ok, detail = fn()
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {label} - {detail}")
        if not ok:
            failures += 1
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
