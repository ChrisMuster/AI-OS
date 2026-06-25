#!/usr/bin/env python3
"""Launch the project Biblio Tools MCP server from the Codex plugin."""

import os
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


def _python_for(project_root: Path) -> Path:
    candidates = [
        project_root / ".venv" / "Scripts" / "python.exe",
        project_root / ".venv" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return Path(sys.executable)


def main() -> None:
    project_root = _project_root()
    server = project_root / "workflows" / "biblio-tools" / "scripts" / "server.py"
    if not server.is_file():
        raise SystemExit("Biblio Tools server.py was not found from the Codex plugin.")

    python = _python_for(project_root)
    os.chdir(project_root)
    os.execv(str(python), [str(python), str(server)])


if __name__ == "__main__":
    main()
