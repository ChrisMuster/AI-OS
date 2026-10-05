#!/usr/bin/env python3
"""
codex_hook.py - the hook Codex runs before every tool call of an orchestrated Codex
builder (Stage A2 chunk (d); short plan section 3.5).

Codex runs this once per tool call, from the run's frozen folder
(``<temp>/book-dragon-orchestration/<run-id>/``), with the run id as its one argument
and the call as a JSON object on stdin. It reads nothing outside that folder: the
brief's edit paths and the project root come from the folder's ``hook.json``, and the
two command lists sit beside it. It decides the call with ``codex_rules.decide``,
appends one line to the folder's ``hook-decisions.jsonl``, and then prints a refusal
for a ``deny`` or nothing for an ``allow``. The line is written before anything is
printed, so an ``allow`` with no line cannot happen.

It fails closed. A bad run id, a missing or unreadable ``hook.json``, stdin that is
not a JSON object, a ``session_id`` that differs from the one an earlier line of the
same file recorded, or any exception at all prints a refusal, and is recorded where
the file can be written. Standard library and this workflow's own modules only.
"""

import json
import sys
from pathlib import Path

FOLDER = Path(__file__).resolve().parent


def refusal(reason):
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                              "permissionDecision": "deny",
                                              "permissionDecisionReason": reason}})


def record(path, event, decision, reason, detail=""):
    """Append one decision line. Returns False if it could not be written. Non-ASCII
    characters are escaped, since the file is copied into the run record (the chunk
    (d) short plan, 11.4)."""
    event = event if isinstance(event, dict) else {}
    line = {"tool_use_id": event.get("tool_use_id"), "turn_id": event.get("turn_id"),
            "session_id": event.get("session_id"), "tool": event.get("tool_name"),
            "decision": decision, "reason": reason, "detail": detail}
    try:
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(line) + "\n")
    except OSError:
        return False
    return True


def judge(argv, raw, folder):
    """Decide one call. Returns ``(event, allowed, reason, detail)``."""
    import codex_rules
    from approver import Approver

    try:
        event = json.loads(raw)
    except ValueError:
        event = None
    if not isinstance(event, dict):
        return None, False, ("Refused by the orchestrator: the hook was not sent a "
                             "JSON object."), ""
    run_id = argv[0] if len(argv) == 1 else ""
    if not codex_rules.RUN_ID.match(run_id) or folder.name != run_id:
        return event, False, ("Refused by the orchestrator: the hook was not started "
                              "for this run."), ""
    try:
        config = json.loads((folder / codex_rules.HOOK_CONFIG).read_text(encoding="utf-8"))
        edit_paths = config["edit_paths"]
        root = config["project_root"]
    except (OSError, ValueError, KeyError, TypeError):
        return event, False, ("Refused by the orchestrator: the run's hook settings "
                              "cannot be read."), ""
    earlier = {line.get("session_id")
               for line in codex_rules.read_decisions(folder / codex_rules.DECISIONS_FILE)}
    earlier.discard(None)
    if earlier and event.get("session_id") not in earlier:
        return event, False, ("Refused by the orchestrator: this call comes from a "
                              "session other than the run's builder session."), ""
    approver = Approver(edit_paths,
                        codex_rules.load_verify_commands(folder / codex_rules.FROZEN_LISTS[0]),
                        root=root)
    pattern = codex_rules.load_read_pattern(folder / codex_rules.FROZEN_LISTS[1])
    tool, tool_input = event.get("tool_name"), event.get("tool_input")
    decision = codex_rules.decide(tool, tool_input, approver, pattern)
    return event, decision.allowed, decision.reason, codex_rules.detail(tool, tool_input)


def main(argv=None, stdin=None, folder=FOLDER):
    argv = sys.argv[1:] if argv is None else argv
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    event = None
    try:
        raw = (stdin or sys.stdin).read()
        event, allowed, reason, detail = judge(argv, raw, folder)
    except Exception as exc:  # noqa: BLE001 - whatever went wrong, the call is refused
        allowed, detail = False, ""
        reason = ("Refused by the orchestrator: the hook could not judge this call "
                  f"({type(exc).__name__}).")
    try:
        import codex_rules
        decisions = folder / codex_rules.DECISIONS_FILE
    except Exception:  # noqa: BLE001
        decisions = folder / "hook-decisions.jsonl"
    written = record(decisions, event, "allow" if allowed else "deny", reason, detail)
    if allowed and not written:
        allowed = False
        reason = ("Refused by the orchestrator: the hook could not record its decision "
                  "for this call.")
    if not allowed:
        print(refusal(reason))
    return 0


if __name__ == "__main__":
    sys.exit(main())
