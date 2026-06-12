#!/usr/bin/env python3
"""Shared project-runtime discovery and hand-off helpers."""

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
