#!/usr/bin/env python3
"""Pure LOG.md timestamp parsing for the doc-sync guard.

`LOG.md` files are gitignored, so their freshness cannot be checked by a content
diff. Instead the guard reads the file from disk and compares the newest entry's
timestamp against a batch reference time computed from file mtimes. This module
does the pure part: turn LOG.md text into the newest entry's `datetime`, and
decide "is the log behind the batch" given already-resolved numbers. No
filesystem access lives here, so it is unit-tested with fixtures.
"""

import re
from datetime import datetime

# A LOG.md entry line: "[<timestamp>] | Actor: ... | Action: ... | Note: ...".
_LOG_ENTRY_RE = re.compile(r"^\s*\[([^\]]+)\]\s*\|\s*Actor:", re.IGNORECASE)


def _parse_ts(raw):
    """Parse one LOG timestamp string into a datetime, or None.

    Handles the canonical ISO-8601-with-offset form the project writes today
    (2026-07-06T20:52:20+01:00) and the legacy date-only form ([2026-05-27])
    that appears in older logs.
    """
    raw = raw.strip()
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        pass
    # Legacy date-only prefix, e.g. "2026-05-27".
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            return datetime.fromisoformat(raw + "T00:00:00")
        except ValueError:
            return None
    return None


def newest_log_datetime(text):
    """Return the datetime of the newest parseable LOG entry, or None.

    Entries are appended newest-at-the-bottom, so the last parseable entry line
    is the newest. A file with no parseable entry yields None (a finding
    upstream, never a silent pass).
    """
    newest = None
    for line in text.splitlines():
        m = _LOG_ENTRY_RE.match(line)
        if not m:
            continue
        dt = _parse_ts(m.group(1))
        if dt is not None:
            newest = dt
    return newest


def log_is_behind(log_epoch, reference_epoch, tolerance=2.0):
    """True when the newest LOG entry pre-dates the batch reference time.

    ``log_epoch`` and ``reference_epoch`` are POSIX timestamps (seconds).
    ``tolerance`` absorbs sub-second filesystem/clock granularity, since the
    correct order is source -> CONTEXT -> LOG and the three can land in the same
    second. Behind by more than the tolerance means the change was not logged.
    """
    if log_epoch is None:
        return True
    return log_epoch < reference_epoch - tolerance
