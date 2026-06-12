#!/usr/bin/env python3
"""
scheduler.py — Background session archive scheduler.

Runs archive.py --all every hour in a loop. PID-file-guarded to prevent
duplicate instances. Auto-terminates after a configurable inactivity period
(default: 4 hours) with no new sessions archived.

Started at session startup by non-Claude AIs (Claude uses its own MCP
scheduled task). Safe to run manually — exits immediately if another
instance is already running.

Usage:
    python scheduler.py              # Start scheduler (foreground)
    python scheduler.py --status     # Check if scheduler is running
    python scheduler.py --stop       # Stop a running scheduler
    python scheduler.py --interval 1800  # Custom interval (seconds)
    python scheduler.py --timeout 7200   # Custom inactivity timeout (seconds)
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
WORKFLOW_DIR = SCRIPT_DIR.parent
DATA_DIR = WORKFLOW_DIR / "data"
PID_FILE = DATA_DIR / "scheduler.pid"
ARCHIVE_SCRIPT = SCRIPT_DIR / "archive.py"

DEFAULT_INTERVAL = 3600  # 1 hour
DEFAULT_TIMEOUT = 14400  # 4 hours


def _log(msg: str) -> None:
    """Log to stderr with timestamp."""
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    print(f"[{ts}] scheduler: {msg}", file=sys.stderr)


def _read_pid() -> int | None:
    """Read PID from the PID file. Returns None if missing or invalid."""
    try:
        text = PID_FILE.read_text(encoding="utf-8").strip()
        return int(text)
    except (FileNotFoundError, ValueError):
        return None


def _is_process_alive(pid: int) -> bool:
    """Check whether a process with the given PID is still running."""
    if sys.platform == "win32":
        # Query the process directly. tasklist can be blocked by app sandboxes.
        try:
            import ctypes
            from ctypes import wintypes

            process = ctypes.windll.kernel32.OpenProcess(
                0x1000,  # PROCESS_QUERY_LIMITED_INFORMATION
                False,
                pid,
            )
            if not process:
                return False
            try:
                exit_code = wintypes.DWORD()
                if not ctypes.windll.kernel32.GetExitCodeProcess(
                    process, ctypes.byref(exit_code)
                ):
                    return False
                return exit_code.value == 259  # STILL_ACTIVE
            finally:
                ctypes.windll.kernel32.CloseHandle(process)
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _write_pid() -> None:
    """Write current PID to the PID file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")


def _remove_pid() -> None:
    """Remove the PID file."""
    try:
        PID_FILE.unlink()
    except FileNotFoundError:
        pass


def is_running() -> tuple[bool, int | None]:
    """Check if a scheduler instance is already running. Returns (alive, pid)."""
    pid = _read_pid()
    if pid is None:
        return False, None
    if _is_process_alive(pid):
        return True, pid
    # Stale PID file — process no longer exists
    _remove_pid()
    return False, None


def status() -> None:
    """Print scheduler status and exit."""
    alive, pid = is_running()
    if alive:
        print(f"Scheduler is running (PID {pid}).")
    else:
        print("Scheduler is not running.")


def stop() -> None:
    """Stop a running scheduler instance."""
    alive, pid = is_running()
    if not alive:
        print("Scheduler is not running.")
        return

    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True, timeout=10,
            )
        except Exception as e:
            print(f"Failed to stop scheduler (PID {pid}): {e}", file=sys.stderr)
            return
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as e:
            print(f"Failed to stop scheduler (PID {pid}): {e}", file=sys.stderr)
            return

    _remove_pid()
    print(f"Scheduler stopped (PID {pid}).")


def _run_archive() -> int:
    """Run archive.py --all and return the count of messages archived."""
    try:
        result = subprocess.run(
            [sys.executable, str(ARCHIVE_SCRIPT), "--all"],
            cwd=str(WORKFLOW_DIR.parent.parent),  # project root
            capture_output=True, text=True, timeout=300,
        )
        # Parse the "Done. N message(s) archived." line
        for line in result.stdout.splitlines():
            if "message(s) archived" in line:
                try:
                    return int(line.split()[1])
                except (IndexError, ValueError):
                    pass
        return 0
    except Exception as e:
        _log(f"archive.py failed: {e}")
        return 0


def run(interval: int = DEFAULT_INTERVAL, timeout: int = DEFAULT_TIMEOUT) -> None:
    """Main scheduler loop."""
    alive, pid = is_running()
    if alive:
        _log(f"Another instance is already running (PID {pid}). Exiting.")
        sys.exit(0)

    _write_pid()
    _log(f"Started (PID {os.getpid()}, interval={interval}s, timeout={timeout}s).")

    # Clean shutdown on SIGTERM / SIGINT
    def _shutdown(signum, frame):
        _log("Received shutdown signal.")
        _remove_pid()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    last_activity = time.monotonic()

    try:
        while True:
            count = _run_archive()
            if count > 0:
                _log(f"Archived {count} message(s).")
                last_activity = time.monotonic()
            else:
                idle_hours = (time.monotonic() - last_activity) / 3600
                if (time.monotonic() - last_activity) >= timeout:
                    _log(f"No new sessions for {idle_hours:.1f} hours. Auto-terminating.")
                    break

            time.sleep(interval)
    finally:
        _remove_pid()
        _log("Stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Background session archive scheduler",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Check if the scheduler is running",
    )
    parser.add_argument(
        "--stop", action="store_true",
        help="Stop a running scheduler instance",
    )
    parser.add_argument(
        "--interval", type=int, default=DEFAULT_INTERVAL,
        help=f"Seconds between archive runs (default: {DEFAULT_INTERVAL})",
    )
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT,
        help=f"Seconds of inactivity before auto-termination (default: {DEFAULT_TIMEOUT})",
    )
    args = parser.parse_args()

    if args.status:
        status()
    elif args.stop:
        stop()
    else:
        run(interval=args.interval, timeout=args.timeout)


if __name__ == "__main__":
    main()
