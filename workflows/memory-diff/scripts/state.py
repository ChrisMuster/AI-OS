#!/usr/bin/env python3
"""
Content watermark for the memory-diff workflow.

The point of a memory diff is that each session shows only what changed in
`memory/` since the last time it was surfaced. `memory/LOG.md` is strictly
append-only (an AGENTS.md rule), so the reliable boundary is a *content*
watermark: we store the raw text of the last log entry line already shown, and
"new" means every entry line that appears after it in the file.

This deliberately avoids a timestamp watermark. The memory log carries entries in
several timestamp shapes (`+01:00`, `+0100`, and legacy `T00:00:00` with no
zone), so lexical timestamp comparison is unreliable and a date-only window would
re-show same-day entries when several sessions run in one day. Matching on the
exact entry line sidesteps all of that.

Failure handling is deliberately loud. Only a genuine first run (no state file
stored yet) silently establishes a baseline. Every other odd case is surfaced
rather than swallowed:
  - a state file that exists but is corrupt/unreadable raises `StateError`; and
  - a stored watermark that is no longer present in the log resolves to the
    WATERMARK_MISSING outcome (the caller cannot prove what changed, so it must
    warn rather than silently re-baseline).

State (JSON, gitignored - machine-local):
    seen_line : the raw text of the last memory/LOG.md entry surfaced, or absent.

This module is pure except for load_state / save_state; the delta logic takes its
inputs explicitly so it is trivially testable.
"""

import hashlib
import json
from pathlib import Path

# Outcomes of new_entries. FIRST_RUN and OK are normal; WATERMARK_MISSING is an
# anomaly the caller must surface (it means "cannot prove what changed", which is
# not the same as "nothing changed").
FIRST_RUN = "first_run"
OK = "ok"
WATERMARK_MISSING = "watermark_missing"


class StateError(Exception):
    """Raised when state.json exists but cannot be read or parsed.

    Distinct from a missing state file (a genuine first run): a corrupt or
    unreadable state file is an anomaly that must be surfaced, never treated as
    first run. `reason` is a short machine code (corrupt / unreadable / malformed).
    """

    def __init__(self, reason, detail):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def load_state(path) -> dict:
    """Return the state dict, or {} if the file does not exist (first run).

    Raises StateError if the file exists but is broken in any way that would let
    it be mistaken for a first run and drop a diff span:
      - corrupt/unreadable JSON;
      - not a JSON object;
      - a JSON object with no usable `seen_line` (missing, non-string, or an
        empty/whitespace-only string).

    The distinction this preserves is the load-bearing one: a *missing* file is a
    genuine first run (return {}), while a *present-but-invalid* file is an
    anomaly to surface loudly, never a silent re-baseline. Extra keys are
    tolerated as long as `seen_line` is valid.
    """
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        with open(p, encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, ValueError) as exc:
        raise StateError("corrupt", f"state file is corrupt: {exc}") from exc
    except OSError as exc:
        raise StateError("unreadable", f"state file is unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise StateError("malformed", "state file is not a JSON object")
    seen = data.get("seen_line")
    if seen is None:
        raise StateError("malformed", "state file has no seen_line watermark")
    if not isinstance(seen, str):
        raise StateError("malformed", "state file seen_line is not a string")
    if not seen.strip():
        raise StateError("malformed", "state file seen_line is empty")
    return data


def save_state(path, state: dict) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
        handle.write("\n")


def latest_line(entries):
    """Return the newest entry line (last in file order), or None if empty."""
    return entries[-1] if entries else None


def line_token(line):
    """Return a short, stable token identifying an entry line.

    Used by the status/ack handshake: --status reports the token of the entry it
    would advance the watermark to, and --ack refuses to advance if the log has
    grown a different newest entry since (the log moved between the two calls).
    Returns None for a missing line.
    """
    if line is None:
        return None
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def new_entries(entries, state):
    """Return (status, new) for the entries after the watermark.

    Args:
        entries: raw memory/LOG.md entry-line strings, oldest-first (file order).
        state: prior state dict (reads seen_line).

    Returns:
        status = FIRST_RUN         no watermark stored yet; caller may baseline.
                 OK                watermark found; `new` is the entries after it.
                 WATERMARK_MISSING a watermark is stored but not present in the
                                   log; `new` is empty and the caller must warn
                                   rather than silently re-baseline.
        new    = the entry lines after the watermark, in file order (empty unless
                 status is OK with changes).
    """
    seen = state.get("seen_line")
    if not seen:
        return FIRST_RUN, []
    for i in range(len(entries) - 1, -1, -1):  # last occurrence wins
        if entries[i] == seen:
            return OK, entries[i + 1:]
    return WATERMARK_MISSING, []
