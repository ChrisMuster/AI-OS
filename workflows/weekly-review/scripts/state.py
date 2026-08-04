#!/usr/bin/env python3
"""
Coverage state for the weekly-review workflow.

The review does NOT use a naive rolling 7-day window for the journal. A naive
window would permanently miss any day the user backfills after the fact: if a
review runs while 28 June is blank and the user writes it up on 5 July, a plain
"last 7 days" window on the following run never looks at 28 June again.

Instead the workflow tracks a coverage watermark plus a list of days that were
empty when last reviewed, and each run includes any previously-empty day that
now has content, even if it falls outside the current window. That turns
"the next review might catch it" into "the next review will catch it".

State (JSON, gitignored - it is machine/personal coverage state):
    last_reviewed_through : ISO date; the newest day already covered by a review.
    pending_days          : ISO dates that were empty at review time and are
                            still within the carry-forward horizon.
    last_run              : ISO timestamp of the last recorded review (staleness).

This module is pure except for load_state / save_state; the window and backfill
logic take their inputs explicitly so they are trivially testable.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path


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
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(state, handle, indent=2)
        handle.write("\n")


def parse_date(value: str):
    """Parse an ISO YYYY-MM-DD string into a date, raising ValueError if invalid."""
    return datetime.strptime(value, "%Y-%m-%d").date()


def daterange(start, end):
    """Yield each date from start to end inclusive."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def compute_window(state: dict, end, window_days: int, max_window_days: int):
    """Return (start, end) dates for the review window.

    Covers everything since the watermark (last_reviewed_through + 1 day), so a
    skipped week is not silently dropped, capped at max_window_days. With no
    watermark (first ever run) it falls back to a window_days look-back.
    """
    last = state.get("last_reviewed_through")
    if last:
        try:
            start = parse_date(last) + timedelta(days=1)
        except ValueError:
            start = end - timedelta(days=window_days - 1)
        if start > end:
            start = end  # already reviewed through today; a thin/empty span
    else:
        start = end - timedelta(days=window_days - 1)

    earliest = end - timedelta(days=max_window_days - 1)
    if start < earliest:
        start = earliest
    return start, end


def resolve_journal_days(state: dict, start, end, has_content, carry_forward_days: int,
                         run_day=None):
    """Work out which journal days a review should cover.

    Args:
        state: prior state dict (reads pending_days).
        start, end: window bounds (date objects).
        has_content: callable(date) -> bool, True if that day's journal has text.
        carry_forward_days: horizon after which a still-empty day is dropped.
        run_day: the day the review is being run (usually == end). It is never
            counted in its own review - the day is not finished, so its journal is
            incomplete - and is always deferred to a later review, even if it
            already has partial content. This prevents both a false "fill today
            first" nag and the loss of an entry written later the same day.

    Returns:
        (included, new_pending): sorted lists of ISO date strings.
        included     = window days with content + carried pending days now filled,
                       excluding the run day.
        new_pending  = window days still empty + the run day + still-empty carried
                       days within horizon.
    """
    window_dates = list(daterange(start, end))
    window_set = {d.isoformat() for d in window_dates}

    included = []
    new_pending = []

    for d in window_dates:
        if run_day is not None and d == run_day:
            continue  # the run day is handled below, never counted in this review
        if has_content(d):
            included.append(d.isoformat())
        else:
            new_pending.append(d.isoformat())

    # The run day is always deferred: today is not finished, so its journal is
    # carried to a later review (where it is a past, completed day).
    if run_day is not None and start <= run_day <= end:
        new_pending.append(run_day.isoformat())

    horizon_start = end - timedelta(days=carry_forward_days)
    for iso in state.get("pending_days", []) or []:
        if iso in window_set:
            continue  # already resolved in the window loop above
        try:
            d = parse_date(iso)
        except (ValueError, TypeError):
            continue
        if d < horizon_start:
            continue  # past the carry-forward horizon; stop tracking it
        if has_content(d):
            included.append(iso)     # backfilled since last review - pick it up now
        else:
            new_pending.append(iso)  # still empty, still worth carrying

    return sorted(set(included)), sorted(set(new_pending))
