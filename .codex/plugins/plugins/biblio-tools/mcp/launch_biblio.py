#!/usr/bin/env python3
"""Launch the project Biblio Tools MCP server from the Codex plugin."""

import os
import runpy
import sys
from pathlib import Path


def _is_project_root(path: Path) -> bool:
    return (
        (path / "AGENTS.md").is_file()
        and (path / "workflows" / "biblio-tools" / "scripts" / "server.py").is_file()
    )


def _candidate_roots() -> list[Path]:
    candidates: list[Path] = []
    for name in [
        "BIBLIO_PROJECT_ROOT",
        "CODEX_PROJECT_ROOT",
        "CODEX_WORKSPACE",
        "CODEX_WORKSPACE_ROOT",
        "WORKSPACE_FOLDER",
        "PWD",
    ]:
        value = os.environ.get(name)
        if value:
            candidates.append(Path(value))

    cwd = Path.cwd()
    candidates.extend([cwd, *cwd.parents])

    for name in ["USERPROFILE", "HOME"]:
        value = os.environ.get(name)
        if not value:
            continue
        home = Path(value)
        candidates.extend(
            [
                home / "Desktop" / "AI-Work" / "AI-OS",
                home / "Desktop" / "AI-OS",
                home / "AI-Work" / "AI-OS",
                home / "AI-OS",
            ]
        )

    return candidates


def _project_root() -> Path:
    for candidate in _candidate_roots():
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if _is_project_root(resolved):
            return resolved

    raise SystemExit("Could not locate the Book Dragon project root for Biblio Tools.")


def main() -> None:
    """Run the project's launch.py in this process, so Codex gets the same chain as
    every other AI: launch.py watches this process's parent (Codex) and passes its
    own PID to server.py, which exits if launch.py dies. Exec-ing server.py directly
    did neither, and on Windows `os.execv` starts a new process whose parent is the
    venv launcher stub, which outlives Codex (cross-review R2-1, 2026-11-24)."""
    project_root = _project_root()
    scripts = project_root / "workflows" / "biblio-tools" / "scripts"
    launcher = scripts / "launch.py"
    if not launcher.is_file() or not (scripts / "server.py").is_file():
        raise SystemExit("Biblio Tools launch.py or server.py was not found from the Codex plugin.")

    os.chdir(project_root)
    sys.path.insert(0, str(scripts))
    sys.argv = [str(launcher), "workflows/biblio-tools/scripts/server.py"]
    runpy.run_path(str(launcher), run_name="__main__")


if __name__ == "__main__":
    main()
