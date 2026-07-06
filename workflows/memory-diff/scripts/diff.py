#!/usr/bin/env python3
"""
Deterministic reader/categoriser for the memory-diff workflow.

This is the ~90% (code) half of the 90-10 split: it parses `memory/LOG.md` into
structured entries and groups them by what happened, so the AI only has to fold a
one-line summary into the greeting (the ~10%). It writes nothing and makes no
judgement.

Categorisation reads the Action field the log already carries, so there is no
fragile parsing of the free-text note:
    created  -> Added
    modified -> Updated
    archived -> Archived
Any other action (the format permits started/completed/failed) buckets into Other
so nothing is silently dropped.

Scope note: true deletion of a memory file has no standard log action - by the
append-only rule a removal leaves a `modified` or `archived` note - so removals
surface as Archived/Updated rather than a dedicated "Removed" group. Detecting a
hard-deleted file (e.g. by diffing MEMORY.md) is a deliberate follow-up, not part
of this first version.
"""

import re
from pathlib import Path

# A standard LOG.md entry line:
#   [TIMESTAMP] | Actor: X | Action: Y | Note: Z
_ENTRY = re.compile(
    r"^\[(?P<ts>[^\]]+)\]\s*\|\s*Actor:\s*(?P<actor>.+?)\s*\|\s*"
    r"Action:\s*(?P<action>\w+)\s*\|\s*Note:\s*(?P<note>.*)$"
)

# Action -> display category. Order here is the display order used downstream.
_CATEGORY = {
    "created": "Added",
    "modified": "Updated",
    "archived": "Archived",
}

# The display groups, in the order a summary should present them.
CATEGORIES = ("Added", "Updated", "Archived", "Other")


class LogError(Exception):
    """Raised when memory/LOG.md is missing or unreadable.

    The memory log is this workflow's sole signal source and is created by
    first-run initialisation, so its absence is an anomaly to surface, not an
    empty diff to swallow. `reason` is a short machine code (missing / unreadable).
    """

    def __init__(self, reason, detail):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def read_log_entries(log_path):
    """Return raw entry-line strings from a LOG.md, in file order (oldest first).

    Only lines matching the standard entry format are kept; the header line and
    blank lines are skipped. Raises LogError if the log is missing or unreadable,
    so a lost data source is reported rather than mistaken for "no changes".
    """
    p = Path(log_path)
    if not p.is_file():
        raise LogError("missing", f"memory log not found: {p}")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise LogError("unreadable", f"memory log is unreadable: {exc}") from exc
    entries = []
    for line in text.splitlines():
        stripped = line.strip()
        if _ENTRY.match(stripped):
            entries.append(stripped)
    return entries


def parse_entry(line):
    """Return {ts, actor, action, note} for an entry line, or None if it is not one."""
    match = _ENTRY.match(line.strip())
    if not match:
        return None
    return {key: match.group(key).strip()
            for key in ("ts", "actor", "action", "note")}


def categorise(entries):
    """Group parsed entry lines by display category, preserving input order.

    Returns {category: [parsed, ...]} for every category in CATEGORIES (empty
    lists included), where each parsed value is the dict from parse_entry.
    """
    groups = {name: [] for name in CATEGORIES}
    for line in entries:
        parsed = parse_entry(line)
        if not parsed:
            continue
        category = _CATEGORY.get(parsed["action"].lower(), "Other")
        groups[category].append(parsed)
    return groups
