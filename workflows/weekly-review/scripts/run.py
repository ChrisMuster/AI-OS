#!/usr/bin/env python3
"""
run.py - weekly-review flywheel entry point.

The deterministic half of the cron + memory flywheel (best-practices umbrella
Bucket-1 child #3). There is no autonomous cron: no scheduling mechanism on the
Claude Code CLI/IDE surface can wake an LLM AND touch local files (see the
memory note reference_claude_scheduling_surfaces). Instead this script prepares
the review, and a session-startup staleness gate (AGENTS.md) surfaces "a review
is due" so the AI writes it when the user next sits down.

Modes:
  (default / --gather)  Assemble and print the briefing packet (read-only). The
                        AI reads it, writes reviews/<label>.md, then runs --record.
  --status              Report whether a review is due and warn about empty
                        journal days in the window (read-only). Used by the
                        AGENTS.md startup gate; supports --json.
  --record              After a review file has been written, advance the
                        coverage watermark, refresh the backfill list, stamp the
                        staleness marker, and log. Honours --dry-run.

Usage:
  python workflows/weekly-review/scripts/run.py
  python workflows/weekly-review/scripts/run.py --status
  python workflows/weekly-review/scripts/run.py --status --json
  python workflows/weekly-review/scripts/run.py --record
  python workflows/weekly-review/scripts/run.py --record --dry-run
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_WORKFLOW_DIR = _SCRIPTS_DIR.parent
_PROJECT_ROOT = _WORKFLOW_DIR.parent.parent

sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import config  # noqa: E402
import gather  # noqa: E402
import state as state_mod  # noqa: E402

_STATE_PATH = _WORKFLOW_DIR / "state.json"
_LAST_RUN_PATH = _WORKFLOW_DIR / ".last-run"
_LOG_PATH = _WORKFLOW_DIR / "LOG.md"
_REVIEWS_DIR = _PROJECT_ROOT / "reviews"
_JOURNAL_ROOT = _PROJECT_ROOT / "journal"
_SESSION_DATA = _PROJECT_ROOT / "workflows" / "session-search" / "data"


def _now():
    return datetime.now(timezone.utc).astimezone()


def _timestamp():
    return _now().isoformat(timespec="seconds")


def _append_log(action, note):
    line = f"[{_timestamp()}] | Actor: Biblio | Action: {action} | Note: {note}\n"
    with open(_LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(line)


def _today(args):
    if args.today:
        return state_mod.parse_date(args.today)
    return _now().date()


def _staleness(now):
    """Return (is_due, last_run_date_str_or_None, age_days_or_None)."""
    if not _LAST_RUN_PATH.is_file():
        return True, None, None
    try:
        raw = _LAST_RUN_PATH.read_text(encoding="utf-8").strip()
        last = datetime.fromisoformat(raw)
    except (OSError, ValueError):
        return True, None, None
    if last.tzinfo is None:
        last = last.replace(tzinfo=now.tzinfo)
    age = (now - last).days
    return age >= config.STALENESS_DAYS, last.date().isoformat(), age


def _resolve(today):
    """Compute window, journal coverage, and empty-in-window days for `today`."""
    st = state_mod.load_state(_STATE_PATH)
    start, end = state_mod.compute_window(
        st, today, config.WINDOW_DAYS, config.MAX_WINDOW_DAYS)
    has_content = gather.make_journal_checker(_JOURNAL_ROOT)
    # `end` is the run day. It is never counted in its own review (the day is not
    # finished), so it is excluded from the coverage and from the empty-day warning.
    included, new_pending = state_mod.resolve_journal_days(
        st, start, end, has_content, config.CARRY_FORWARD_DAYS, run_day=end)
    empty_in_window = [
        d.isoformat() for d in state_mod.daterange(start, end)
        if d != end and not has_content(d)
    ]
    return st, start, end, included, new_pending, empty_in_window


def cmd_status(args):
    now = _now()
    today = _today(args)
    is_due, last_run, age = _staleness(now)
    _st, start, end, _included, _new_pending, empty_in_window = _resolve(today)
    label = gather.iso_week_label(end)

    if args.json:
        print(json.dumps({
            "due": is_due,
            "last_run": last_run,
            "age_days": age,
            "label": label,
            "window": {"start": start.isoformat(), "end": end.isoformat()},
            "empty_journal_days": empty_in_window,
        }, indent=2))
        return 0

    if is_due:
        when = f"last: {last_run}" if last_run else "never run"
        print(f"A weekly review is due ({when}). Proposed: {label} "
              f"({start.isoformat()} to {end.isoformat()}).")
        if empty_in_window:
            days = ", ".join(empty_in_window)
            print(f"Note: no journal entry yet for {days} - you may want to fill "
                  f"those in first so the review captures them (they will be picked "
                  f"up automatically on a later review if left for now).")
    else:
        print(f"No weekly review due (last: {last_run}, {age} day(s) ago).")
    return 0


def cmd_gather(args):
    today = _today(args)
    _st, start, end, included, _new_pending, empty_in_window = _resolve(today)
    label = gather.iso_week_label(end)

    packet = gather.build_packet(
        label=label, start=start, end=end,
        included_days=included, empty_days=empty_in_window,
        journal=gather.journal_entries(_JOURNAL_ROOT, included),
        logs=gather.log_entries(_PROJECT_ROOT, start, end),
        commits=gather.git_commits(_PROJECT_ROOT, start, end),
        memory=gather.memory_changes(_PROJECT_ROOT, start, end),
        sessions=gather.session_summary(_SESSION_DATA, start, end),
        reviews=gather.prior_reviews(_REVIEWS_DIR, label, config.PRIOR_REVIEWS),
    )
    print(packet)
    print(f"\n---\nWrite the review to: reviews/{label}.md")
    print("Then record completion: "
          "python workflows/weekly-review/scripts/run.py --record")
    return 0


def cmd_record(args):
    today = _today(args)
    st, start, end, _included, new_pending, _empty = _resolve(today)
    label = gather.iso_week_label(end)
    review_file = _REVIEWS_DIR / f"{label}.md"

    if not review_file.is_file() or not review_file.read_text(encoding="utf-8").strip():
        print(f"No review found at reviews/{label}.md (or it is empty). Write the "
              f"review before recording completion.", file=sys.stderr)
        return 1

    new_state = {
        "last_reviewed_through": end.isoformat(),
        "pending_days": new_pending,
        "last_run": _timestamp(),
    }

    if args.dry_run:
        print(f"[DRY RUN] Would record review {label}.")
        print(f"[DRY RUN] Would set last_reviewed_through={end.isoformat()}, "
              f"pending_days={new_pending}.")
        print("[DRY RUN] No state written, no last-run stamp, no log entry.")
        return 0

    state_mod.save_state(_STATE_PATH, new_state)
    with open(_LAST_RUN_PATH, "w", encoding="utf-8") as handle:
        handle.write(new_state["last_run"] + "\n")
    _append_log("completed",
                f"Recorded weekly review {label} (covered through {end.isoformat()}; "
                f"{len(new_pending)} pending journal day(s)).")
    print(f"Recorded review {label}. Coverage watermark advanced to {end.isoformat()}.")
    if new_pending:
        print(f"Pending (empty) journal days carried forward: {', '.join(new_pending)}.")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Weekly-review flywheel: gather the week's signal, report "
                    "staleness, and record completion.")
    parser.add_argument("--status", action="store_true",
                        help="Report whether a review is due and warn about empty "
                             "journal days (read-only).")
    parser.add_argument("--gather", action="store_true",
                        help="Assemble and print the briefing packet (default; read-only).")
    parser.add_argument("--record", action="store_true",
                        help="Record a written review: advance the watermark and stamp staleness.")
    parser.add_argument("--json", action="store_true",
                        help="With --status, emit machine-readable JSON.")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --record, preview without writing anything.")
    parser.add_argument("--today", metavar="YYYY-MM-DD",
                        help="Override today's date (testing / manual backfill runs).")
    args = parser.parse_args()

    if args.status:
        return cmd_status(args)
    if args.record:
        return cmd_record(args)
    return cmd_gather(args)


if __name__ == "__main__":
    raise SystemExit(main())
