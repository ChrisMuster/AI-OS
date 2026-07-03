#!/usr/bin/env python3
"""
run.py - trigger-phrase registry entry point.

Reads the single source of truth (config/triggers.yaml) and prints the
triggerable actions grouped by category, so "give me a list of triggers" gives
the same answer on every AI. Read-only: it renders the registry, nothing else.

Modes:
  --list                Print the registry grouped by category (default).
  --category NAME       Restrict the listing to one category.
  --json                Emit the registry as JSON.

Usage:
  python workflows/triggers/scripts/run.py --list
  python workflows/triggers/scripts/run.py --list --category handoff
  python workflows/triggers/scripts/run.py --json
"""

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_WORKFLOW_DIR = _SCRIPTS_DIR.parent
_PROJECT_ROOT = _WORKFLOW_DIR.parent.parent
_RUNTIME_SCRIPTS = _PROJECT_ROOT / "workflows" / "biblio-tools" / "scripts"
_REGISTRY_PATH = _WORKFLOW_DIR / "config" / "triggers.yaml"

sys.path.insert(0, str(_RUNTIME_SCRIPTS))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


def load_registry(path=_REGISTRY_PATH):
    """Return the list of category dicts, or [] if the file is missing/empty."""
    import yaml  # noqa: E402

    p = Path(path)
    if not p.is_file():
        return []
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return []
    categories = data.get("categories") or []
    return [c for c in categories if isinstance(c, dict) and c.get("name")]


def filter_categories(categories, name):
    """Return only the category matching `name` (case-insensitive), or all."""
    if not name:
        return categories
    wanted = name.strip().lower()
    return [c for c in categories if str(c.get("name", "")).lower() == wanted]


def render(categories):
    """Return the human-readable grouped listing as a string."""
    if not categories:
        return "No triggers found in the registry.\n"
    lines = ["# Book Dragon trigger phrases", ""]
    for cat in categories:
        lines.append(f"## {cat.get('name', '(unnamed)')}")
        if cat.get("summary"):
            lines.append(cat["summary"])
        if cat.get("runs"):
            lines.append(f"Runs: {cat['runs']}")
        phrases = cat.get("phrases") or []
        if phrases:
            lines.append("Say:")
            for phrase in phrases:
                lines.append(f'  - "{phrase}"')
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main():
    parser = argparse.ArgumentParser(
        description="List Book Dragon's triggerable actions and their phrases.")
    parser.add_argument("--list", action="store_true",
                        help="Print the registry grouped by category (default).")
    parser.add_argument("--category", metavar="NAME",
                        help="Restrict the listing to one category.")
    parser.add_argument("--json", action="store_true",
                        help="Emit the registry as JSON.")
    args = parser.parse_args()

    # Hand off to the canonical .venv so PyYAML is available even when the
    # caller used the system `python` from AGENTS.md or a trigger phrase.
    from runtime import ensure_project_runtime  # noqa: E402
    ensure_project_runtime()

    categories = filter_categories(load_registry(), args.category)

    if args.json:
        print(json.dumps({"categories": categories}, indent=2))
        return 0

    print(render(categories))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
