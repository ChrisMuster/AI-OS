#!/usr/bin/env python3
"""Shared project-runtime discovery and hand-off helpers."""

import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


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
        return True
    except OSError:
        return False


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
