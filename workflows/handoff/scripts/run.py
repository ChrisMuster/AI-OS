#!/usr/bin/env python3
"""
run.py - handoff workflow entry point.

Clean session-to-session transitions (best-practices umbrella Bucket-1 child #4).
There are no per-AI slash commands: like the weekly-review flywheel, this is a
deterministic gather script plus an AI write step, triggered by natural language
documented in AGENTS.md, so it works identically on every AGENTS-reading AI.

Modes:
  (default / --gather)  Assemble and print the briefing packet (read-only). The
                        AI reads it and writes HANDOVER.md at the project root.
  --status              Report whether an unread handoff exists (read-only). Used
                        by the AGENTS.md startup recovery step; supports --json.
  --seen                Mark the current HANDOVER.md as seen: advance the
                        watermark to its timestamp so it is not surfaced again.
                        Called at startup after the handoff has been shown, or
                        when the user picks it up. Honours --dry-run.

Usage:
  python workflows/handoff/scripts/run.py
  python workflows/handoff/scripts/run.py --status
  python workflows/handoff/scripts/run.py --status --json
  python workflows/handoff/scripts/run.py --seen
  python workflows/handoff/scripts/run.py --seen --dry-run
"""

import argparse
import json
import sys
from datetime import date, datetime, timezone
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
_LOG_PATH = _WORKFLOW_DIR / "LOG.md"
_HANDOFF_PATH = _PROJECT_ROOT / config.HANDOFF_FILENAME
_SESSION_DATA = _PROJECT_ROOT / "workflows" / "session-search" / "data"


def _now():
    return datetime.now(timezone.utc).astimezone()


def _timestamp():
    return _now().isoformat(timespec="seconds")


def _append_log(action, note):
    line = f"[{_timestamp()}] | Actor: Biblio | Action: {action} | Note: {note}\n"
    with open(_LOG_PATH, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)


def cmd_gather(args):
    today = date.today()
    status = gather.git_status(_PROJECT_ROOT)
    packet = gather.build_packet(
        timestamp=_timestamp(),
        branch=gather.current_branch(_PROJECT_ROOT),
        status=status,
        diffstat=gather.git_diff_stat(_PROJECT_ROOT),
        commits=gather.recent_commits(_PROJECT_ROOT, config.RECENT_COMMITS),
        dir_logs=gather.changed_dir_logs(_PROJECT_ROOT, status, config.LOG_TAIL),
        backlog=gather.active_backlog(_PROJECT_ROOT),
        sessions=gather.recent_sessions(
            _SESSION_DATA, today, config.SESSION_LOOKBACK_DAYS),
        doc_sync=gather.doc_sync_drift(_PROJECT_ROOT),
        review=gather.review_packets(_PROJECT_ROOT),
    )
    print(packet)
    print("---")
    print(f"Write the handoff to: {config.HANDOFF_FILENAME} (project root, "
          "overwrite any existing one).")
    print("It is surfaced automatically at the next session's startup.")
    return 0


def cmd_status(args):
    handoff_ts = state_mod.read_handoff_timestamp(_HANDOFF_PATH)
    st = state_mod.load_state(_STATE_PATH)
    unread = state_mod.is_unread(handoff_ts, st)

    if args.json:
        print(json.dumps({
            "unread": unread,
            "handoff_timestamp": handoff_ts or None,
            "seen_handoff": st.get("seen_handoff"),
            "handoff_path": config.HANDOFF_FILENAME,
        }, indent=2))
        return 0

    if unread:
        print(f"An unread handoff exists ({config.HANDOFF_FILENAME}, "
              f"created {handoff_ts}). Read it and open with an informed greeting, "
              f"then run --seen to acknowledge it.")
    elif handoff_ts:
        print(f"Handoff present but already seen ({handoff_ts}).")
    else:
        print("No handoff present.")
    return 0


def cmd_seen(args):
    handoff_ts = state_mod.read_handoff_timestamp(_HANDOFF_PATH)
    if not handoff_ts:
        print(f"No handoff at {config.HANDOFF_FILENAME} to mark as seen.",
              file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"[DRY RUN] Would set seen_handoff={handoff_ts}.")
        print("[DRY RUN] No state written, no log entry.")
        return 0

    st = state_mod.load_state(_STATE_PATH)
    st["seen_handoff"] = handoff_ts
    state_mod.save_state(_STATE_PATH, st)
    _append_log("completed",
                f"Handoff acknowledged at startup (seen_handoff={handoff_ts}).")
    print(f"Marked handoff seen (watermark = {handoff_ts}).")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Handoff: gather the session state packet, report whether an "
                    "unread handoff exists, and mark one as seen.")
    parser.add_argument("--status", action="store_true",
                        help="Report whether an unread handoff exists (read-only).")
    parser.add_argument("--gather", action="store_true",
                        help="Assemble and print the briefing packet (default; read-only).")
    parser.add_argument("--seen", action="store_true",
                        help="Advance the watermark so the current handoff is not surfaced again.")
    parser.add_argument("--json", action="store_true",
                        help="With --status, emit machine-readable JSON.")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --seen, preview without writing anything.")
    args = parser.parse_args()

    if args.status:
        return cmd_status(args)
    if args.seen:
        return cmd_seen(args)
    return cmd_gather(args)


if __name__ == "__main__":
    raise SystemExit(main())
