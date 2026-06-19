#!/usr/bin/env python3
"""Run a project Python script with Book Dragon's canonical environment."""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from runtime import process_is_alive, venv_python


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _stop_child(child_proc: subprocess.Popen) -> None:
    """Terminate the child process without raising during shutdown races."""
    if child_proc.poll() is not None:
        return
    try:
        child_proc.terminate()
        child_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child_proc.kill()
    except OSError:
        pass


def _parent_watchdog(child_proc):
    """Kill the child process if our parent (the AI session) dies."""
    parent_pid = os.getppid()
    while True:
        time.sleep(10)
        if child_proc.poll() is not None:
            return
        if not process_is_alive(parent_pid):
            _stop_child(child_proc)
            os._exit(0)


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

    child = subprocess.Popen(
        [str(python), str(script), *sys.argv[2:]],
        cwd=PROJECT_ROOT,
    )
    threading.Thread(target=_parent_watchdog, args=(child,), daemon=True).start()
    raise SystemExit(child.wait())


if __name__ == "__main__":
    main()
