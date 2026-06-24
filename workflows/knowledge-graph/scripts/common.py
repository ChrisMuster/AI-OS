"""
common.py — Shared constants and helpers for the knowledge-graph indexer.

Standard library only. Kept deliberately small so parser.py (pure parsing)
and builder.py (filesystem-aware) can share constants without a circular import.
"""

from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent

# ---------------------------------------------------------------------------
# Walk configuration (mirrors the audit workflow's proven approach)
# ---------------------------------------------------------------------------
# Directories never traversed at all.
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".claude", ".obsidian"}

# Directories that become nodes themselves but whose contents are not recursed
# into (immutable source data, not Book Dragon directories).
NO_RECURSE_DIRS = {"raw", "data"}

# ---------------------------------------------------------------------------
# Relationship resolution
# ---------------------------------------------------------------------------
# Root-level .md files treated as graph nodes and as link/dependency targets.
ROOT_LINK_FILES = {
    "AGENT-SETUP.md",
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    "README.md",
    "USER.md",
    "SOUL.md",
}
# The same set without the .md extension, used to recognise [[AGENTS]]-style links.
ROOT_LINK_STEMS = {name[:-3] for name in ROOT_LINK_FILES}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def now_ts() -> str:
    """Current time as ISO 8601 with timezone offset, e.g. 2026-06-19T21:15:45+01:00."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def rel(path, root: Path = PROJECT_ROOT) -> str:
    """Return ``path`` relative to ``root`` using forward slashes."""
    try:
        return str(Path(path).relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def read_text_safe(path: Path) -> str:
    """Read a file as UTF-8, never raising. Returns "" on any failure."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""


def top_level_dir_names(root: Path) -> set:
    """Names of the immediate project subdirectories, used to recognise that a
    backtick token like ``workflows/audit/`` is a project-root-relative path."""
    names = set()
    try:
        for p in root.iterdir():
            if p.is_dir() and p.name not in SKIP_DIRS and not p.name.startswith("."):
                names.add(p.name)
    except OSError:
        pass
    return names
