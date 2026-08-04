#!/usr/bin/env python3
"""
run.py - memory-diff workflow entry point.

Shows what changed in `memory/` since the last session (best-practices umbrella
Bucket-1 child #5, the OpenClaw "Memory Diff" skill). Like the weekly-review and
handoff workflows there are no per-AI slash commands: this is a deterministic
reader plus a one-line AI summary, triggered by natural language documented in
AGENTS.md, so it works identically on every AGENTS-reading AI with no per-AI
adapter.

Modes:
  (default / --status)  Report the memory changes since the watermark (read-only;
                        does not advance it). Used by the AGENTS.md startup step
                        and by the "what changed in memory" trigger. Supports
                        --json, which also emits a `through` token for the ack
                        handshake. Exits non-zero on an anomaly (see below).
  --ack                 Advance the watermark to the newest memory/LOG.md entry so
                        the same changes are not surfaced again. Called at startup
                        after the diff has been shown. Honours --dry-run.
                        --through <token> makes it refuse to advance if the log
                        moved since --status; --force-baseline resets past an
                        anomaly.

Failure handling is loud, not silent. Only a genuine first run (no state file)
baselines quietly. A corrupt/unreadable state file, a missing/unreadable memory
log, or a stored watermark that has vanished from the log are all reported as
anomalies (a WARNING and a non-zero exit), and --ack refuses to advance so no
span of memory changes is skipped without the user seeing it.

Usage:
  python workflows/memory-diff/scripts/run.py
  python workflows/memory-diff/scripts/run.py --status --json
  python workflows/memory-diff/scripts/run.py --ack --through <token>
  python workflows/memory-diff/scripts/run.py --ack --dry-run
  python workflows/memory-diff/scripts/run.py --ack --force-baseline

Path overrides (used by the integration tests so they never touch real state):
  MEMORY_DIFF_MEMORY_LOG, MEMORY_DIFF_STATE, MEMORY_DIFF_WORKFLOW_LOG.
"""

import argparse
import json
import os
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
import diff  # noqa: E402
import state as state_mod  # noqa: E402

_MEMORY_LOG = Path(os.environ.get(
    "MEMORY_DIFF_MEMORY_LOG", _PROJECT_ROOT / config.MEMORY_LOG))
_STATE_PATH = Path(os.environ.get(
    "MEMORY_DIFF_STATE", _WORKFLOW_DIR / "state.json"))
_LOG_PATH = Path(os.environ.get(
    "MEMORY_DIFF_WORKFLOW_LOG", _WORKFLOW_DIR / "LOG.md"))

# Anomaly status codes (from a broken log or state) mapped to a human phrase. Any
# status in this table is a "cannot prove what changed" case: it is surfaced and
# never silently treated as a first-run baseline.
_ANOMALIES = {
    "log_missing": "the memory log is missing",
    "log_unreadable": "the memory log is unreadable",
    "state_corrupt": "the saved state is corrupt",
    "state_unreadable": "the saved state is unreadable",
    "state_malformed": "the saved state is malformed",
    state_mod.WATERMARK_MISSING: "the saved watermark is no longer in the memory log",
}


def _is_anomaly(status):
    return status in _ANOMALIES


def _timestamp():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _append_log(action, note):
    line = f"[{_timestamp()}] | Actor: Biblio | Action: {action} | Note: {note}\n"
    with open(_LOG_PATH, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)


def _compute():
    """Return a result dict describing the current diff state.

    Keys:
        status   - state.FIRST_RUN / state.OK, or an anomaly code (see _ANOMALIES).
        new      - entry lines after the watermark (only populated when OK).
        entries  - all memory-log entry lines (empty if the log could not be read).
        latest   - the newest entry line, or None.
        through  - token of `latest` for the ack handshake, or None.
        detail   - human detail for an anomaly, else "".
        state    - the loaded state dict, or None if it could not be loaded.
    """
    result = {"status": None, "new": [], "entries": [], "latest": None,
              "through": None, "detail": "", "state": None}
    try:
        entries = diff.read_log_entries(_MEMORY_LOG)
    except diff.LogError as exc:
        result["status"] = f"log_{exc.reason}"
        result["detail"] = exc.detail
        return result
    result["entries"] = entries
    result["latest"] = state_mod.latest_line(entries)
    result["through"] = state_mod.line_token(result["latest"])
    try:
        st = state_mod.load_state(_STATE_PATH)
    except state_mod.StateError as exc:
        result["status"] = f"state_{exc.reason}"
        result["detail"] = exc.detail
        return result
    result["state"] = st
    status, new = state_mod.new_entries(entries, st)
    result["status"] = status
    result["new"] = new
    return result


def cmd_status(args):
    r = _compute()
    status = r["status"]
    anomaly = _is_anomaly(status)

    if args.json:
        groups = diff.categorise(r["new"])
        payload = {
            "status": status,
            "anomaly": anomaly,
            "has_changes": bool(r["new"]),
            "baseline": status == state_mod.FIRST_RUN,
            "count": len(r["new"]),
            "through": r["through"],
            "groups": {
                name: [
                    {"date": e["ts"][:10], "action": e["action"], "note": e["note"]}
                    for e in groups[name]
                ]
                for name in diff.CATEGORIES if groups[name]
            },
        }
        if r["detail"]:
            payload["detail"] = r["detail"]
        print(json.dumps(payload, indent=2))
        return 2 if anomaly else 0

    if anomaly:
        print(f"WARNING: memory-diff could not compute a reliable delta - "
              f"{_ANOMALIES[status]}.")
        if r["detail"]:
            print(f"  {r['detail']}")
        print("  Nothing has been acknowledged, so no memory changes are being "
              "skipped. Check memory/LOG.md and workflows/memory-diff/state.json; "
              "once satisfied, reset with --ack --force-baseline.")
        return 2

    if not r["new"]:
        if status == state_mod.FIRST_RUN:
            print("Memory-diff not yet initialised (baseline will be set on --ack).")
        else:
            print("No memory changes since last session.")
        return 0

    # Newest first for the summary; trim the printed list to the soft cap.
    ordered = list(reversed(r["new"]))
    hidden = 0
    if config.MAX_ENTRIES and len(ordered) > config.MAX_ENTRIES:
        hidden = len(ordered) - config.MAX_ENTRIES
        ordered = ordered[:config.MAX_ENTRIES]

    groups = diff.categorise(ordered)
    print(f"{len(r['new'])} memory change(s) since last session:")
    for name in diff.CATEGORIES:
        items = groups[name]
        if not items:
            continue
        print(f"  {name}:")
        for entry in items:
            print(f"    - [{entry['ts'][:10]}] {entry['note']}")
    if hidden:
        print(f"  ... and {hidden} older change(s) not shown.")
    return 0


def cmd_ack(args):
    r = _compute()
    status = r["status"]

    # Anomaly: refuse to advance unless the operator explicitly forces a reset,
    # so a broken log/state cannot silently swallow a span of memory changes.
    if _is_anomaly(status):
        reason = _ANOMALIES[status]
        if args.force_baseline:
            latest = r["latest"]
            if latest is None:
                print(f"Cannot force-baseline: {reason}, and there is no memory "
                      "log entry to baseline to.")
                return 2
            if args.dry_run:
                print(f"[DRY RUN] Would force-baseline past the anomaly ({reason}).")
                print("[DRY RUN] No state written, no log entry.")
                return 0
            # State may be unloadable, so start from a clean dict deliberately.
            state_mod.save_state(_STATE_PATH, {"seen_line": latest})
            print(f"Memory-diff force-baselined past the anomaly ({reason}).")
            return 0
        print(f"Refusing to acknowledge: {reason}.")
        if r["detail"]:
            print(f"  {r['detail']}")
        print("  Nothing advanced, so no memory changes are being skipped. "
              "Re-run with --ack --force-baseline once you have checked "
              "memory/LOG.md and state.json.")
        return 2

    latest = r["latest"]
    if latest is None:
        print("No memory/LOG.md entries; nothing to acknowledge.")
        return 0

    if status == state_mod.OK and not r["new"]:
        print("Memory-diff watermark already current.")
        return 0

    # Finding 4: refuse if the log grew a different newest entry since --status
    # computed `through`, so a change appended between status and ack is surfaced
    # next time rather than silently acknowledged here.
    if args.through and args.through != r["through"]:
        print("Refusing to acknowledge: memory/LOG.md changed since the status "
              "check computed the delta.")
        print("  The newer change(s) will be surfaced on the next check rather "
              "than skipped. Re-run the status check.")
        return 2

    if args.dry_run:
        target = "baseline" if status == state_mod.FIRST_RUN else f"{len(r['new'])} new change(s)"
        print(f"[DRY RUN] Would advance the watermark to the latest entry ({target}).")
        print("[DRY RUN] No state written, no log entry.")
        return 0

    st = dict(r["state"] or {})
    st["seen_line"] = latest
    state_mod.save_state(_STATE_PATH, st)

    if status == state_mod.FIRST_RUN:
        # First run: no surfaced changes, so no log entry.
        print("Memory-diff baseline established.")
    else:
        _append_log("completed",
                    f"Memory-diff surfaced and acknowledged {len(r['new'])} memory "
                    f"change(s) at startup.")
        print(f"Acknowledged {len(r['new'])} memory change(s).")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Memory diff: report what changed in memory/ since the last "
                    "session, and advance the watermark once it has been shown.")
    parser.add_argument("--status", action="store_true",
                        help="Report memory changes since the watermark (default; read-only).")
    parser.add_argument("--ack", action="store_true",
                        help="Advance the watermark to the latest memory/LOG.md entry.")
    parser.add_argument("--json", action="store_true",
                        help="With --status, emit machine-readable JSON (includes the ack token).")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --ack, preview without writing anything.")
    parser.add_argument("--through", metavar="TOKEN", default=None,
                        help="With --ack, only advance if the log's newest entry still "
                             "matches this token from --status --json (race guard).")
    parser.add_argument("--force-baseline", action="store_true",
                        help="With --ack, reset the watermark past an anomaly "
                             "(corrupt state, missing log, or a lost watermark).")
    args = parser.parse_args()

    if args.ack:
        return cmd_ack(args)
    return cmd_status(args)


if __name__ == "__main__":
    raise SystemExit(main())
