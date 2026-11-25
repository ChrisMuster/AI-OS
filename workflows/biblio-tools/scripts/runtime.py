#!/usr/bin/env python3
"""Shared project-runtime discovery and hand-off helpers."""

import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# launch.py's own process ID, set for the script it starts, so that script can exit
# when launch.py dies (server.py's watchdog reads it).
LAUNCHER_PID_ENV = "BOOK_DRAGON_LAUNCHER_PID"


def venv_python() -> Path:
    """Return the platform-appropriate project virtual-environment Python."""
    candidates = [
        PROJECT_ROOT / ".venv" / "Scripts" / "python.exe",
        PROJECT_ROOT / ".venv" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError(
        "Book Dragon's project runtime is not set up. Run: "
        "python workflows/biblio-tools/scripts/setup.py"
    )


def process_is_alive(pid: int) -> bool:
    """Return True when *pid* appears to be a running process."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            handle = ctypes.windll.kernel32.OpenProcess(
                0x1000,  # PROCESS_QUERY_LIMITED_INFORMATION
                False,
                pid,
            )
            if not handle:
                return False
            try:
                exit_code = wintypes.DWORD()
                if not ctypes.windll.kernel32.GetExitCodeProcess(
                    handle, ctypes.byref(exit_code)
                ):
                    return False
                return exit_code.value == 259  # STILL_ACTIVE
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return False

    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return not _is_zombie(pid)


# Where Linux publishes process state; a module name so a test can point it elsewhere.
_PROC = Path("/proc")


def _is_zombie(pid: int) -> bool:
    """Whether *pid* has exited but not been reaped (POSIX only).

    ``os.kill(pid, 0)`` succeeds for such a zombie, so without this a launcher that
    was killed, and that its own parent has not yet waited for, reads as alive, and
    server.py's watchdog keeps the server running (cross-review R1-1, 2026-11-24).
    Linux is read from ``/proc/<pid>/stat``; where there is no ``/proc`` (macOS),
    ``ps`` is asked. If neither can answer, the process is taken as not a zombie,
    which is what the ``os.kill`` probe alone reported before."""
    if _PROC.is_dir():
        try:
            text = (_PROC / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return True  # gone between the two probes
        except OSError:
            return False
        # The state follows the command name, which is in parentheses and may itself
        # contain spaces or parentheses, so read from the last ")".
        return text[text.rfind(")") + 2:][:1] in ("Z", "X")
    try:
        result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                                capture_output=True, text=True, encoding="utf-8",
                                timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return True  # ps lists no such process
    return result.stdout.strip().startswith("Z")


def ensure_project_runtime() -> None:
    """Re-execute the current script inside the canonical project runtime."""
    python = venv_python()
    try:
        current = Path(sys.executable).resolve()
        target = python.resolve()
    except OSError:
        current = Path(sys.executable)
        target = python

    if current == target:
        return

    result = subprocess.run(
        [str(python), *sys.argv],
        cwd=PROJECT_ROOT,
    )
    raise SystemExit(result.returncode)
