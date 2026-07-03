#!/usr/bin/env python3
"""
Seen-watermark for the handoff workflow.

The point of a handoff is that the *next* session picks it up. So a handoff, once
written, must be surfaced at the next startup - but only once. A naive "read
HANDOVER.md if it exists" would re-surface the same handoff every session until a
new one overwrote it, nagging about work that was already resumed days ago.

Instead we store a watermark: the timestamp of the handoff that was last shown.
Startup surfaces a handoff only when its own timestamp differs from the watermark
(i.e. it is a handoff the user has not been shown yet), then advances the
watermark so it is not shown again. Each handoff carries a fresh timestamp, so a
new handoff always reads as unread and a consumed one never does.

State (JSON, gitignored - machine-local):
    seen_handoff : the timestamp of the handoff last surfaced at startup, or absent.

This module is pure except for load_state / save_state; the freshness logic takes
its inputs explicitly so it is trivially testable.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

# The "**Created:** <timestamp>" line a handoff document carries near the top.
_CREATED = re.compile(r"^\*\*Created:\*\*\s*(.+?)\s*$")


def load_state(path) -> dict:
    """Return the state dict, or {} if the file is missing or unreadable."""
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        with open(p, encoding="utf-8") as handle:
            return json.load(handle) or {}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def save_state(path, state: dict) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
        handle.write("\n")


def read_handoff_timestamp(handoff_path) -> str:
    """Return a handoff's identifying timestamp, or "" if there is no handoff.

    Prefers the document's own "**Created:**" line (stable across reads); falls
    back to the file's modification time so a hand-written or malformed handoff
    still gets a usable identity.
    """
    p = Path(handoff_path)
    if not p.is_file():
        return ""
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        match = _CREATED.match(line.strip())
        if match:
            return match.group(1).strip()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return ""
    return datetime.fromtimestamp(mtime, timezone.utc).astimezone().isoformat(
        timespec="seconds")


def is_unread(handoff_ts: str, state: dict) -> bool:
    """True if there is a handoff whose timestamp the user has not been shown.

    Empty handoff_ts means no handoff exists, so nothing to surface. Otherwise a
    handoff is unread when its timestamp differs from the stored watermark.
    """
    if not handoff_ts:
        return False
    return handoff_ts != state.get("seen_handoff")
