#!/usr/bin/env python3
"""Run a project Python script with Book Dragon's canonical environment."""

import subprocess
import sys
from pathlib import Path

from runtime import venv_python


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: launch.py <project-relative-script> [args...]")

    script = (PROJECT_ROOT / sys.argv[1]).resolve()
    try:
        script.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise SystemExit("Script path must stay within the project.") from exc

    if not script.is_file():
        raise SystemExit(f"Script not found: {sys.argv[1]}")

    try:
        python = venv_python()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    result = subprocess.run(
        [str(python), str(script), *sys.argv[2:]],
        cwd=PROJECT_ROOT,
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
