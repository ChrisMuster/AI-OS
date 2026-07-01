#!/usr/bin/env python3
"""run.py - Book Dragon deterministic rule-enforcement hook entry point.

One program, dispatched by `--ai <id>`, called by each AI's native hook. It
reads the firing tool event (stdin JSON), routes it through that AI's adapter
into a normalised Context, runs the rules for the event category, and emits the
AI's block contract on a block.

Modes:
  --ai <id>     evaluate a PreToolUse event read from stdin (the hook path)
  --reinject    print the SessionStart rule reminder (Group C re-injection)
  --precommit   run personal-data-guard as the git pre-commit gate (rule B3 L2)

Design notes:
  - Fail mode: a detected violation -> block; ANY hook/tool failure (unparseable
    event, a rule crash, a missing adapter) -> allow + fire-log. A tooling bug
    must never freeze the agent, and the git pre-commit + audit are the durable
    backstop. No rule fails closed on a hook error.
  - It is infrastructure, not a logged workflow run: blocks and trial-rule warns
    go to a gitignored fire-log, never to LOG.md (which records workflow
    changes, not per-fire activity).
  - Kept stdlib-only and light at import time; the personal-data guard is loaded
    lazily by the B3 rule only when a write is actually checked.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# UTF-8 I/O so output never mojibakes when piped on a cp1252 Windows console.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))  # so `import core / rules / adapters` works

from core import Decision, format_block  # noqa: E402
import adapters  # noqa: E402
from rules import rules_for  # noqa: E402

WORKFLOW_DIR = SCRIPT_DIR.parent
# Script-relative fallback root: scripts -> rule-hooks -> workflows -> project.
SCRIPT_RELATIVE_ROOT = SCRIPT_DIR.parent.parent.parent
FIRE_LOG = WORKFLOW_DIR / "fire-log.jsonl"

REMINDER = """[Book Dragon - rule reminders re-injected at session start]
- Permission gate: present your plan and wait for an explicit "go ahead" before
  building, creating or changing files, or running anything with side effects.
- If a tool is blocked or triggers a permission prompt, STOP. Do not route
  around it - treat the block as correct and your approach as the thing to fix.
- Use the prescribed Book Dragon tools (Biblio-tools MCP first when available),
  not ad-hoc substitutes."""


# ---------------------------------------------------------------------------
# Fire-log (infrastructure record, gitignored)
# ---------------------------------------------------------------------------
def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def fire_log(record):
    """Append one JSON record to the fire-log. Never raises."""
    try:
        entry = {"ts": _now(), **record}
        with open(FIRE_LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Runtime project-root resolution (Task 2)
# ---------------------------------------------------------------------------
def _git_toplevel():
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(SCRIPT_RELATIVE_ROOT), capture_output=True,
            encoding="utf-8", timeout=5,
        )
        if result.returncode == 0:
            top = result.stdout.strip()
            if top:
                return Path(top)
    except Exception:
        pass
    return None


def _has_signature(path):
    """A provable project root carries AGENTS.md and a .git entry."""
    path = Path(path)
    return (path / "AGENTS.md").exists() and (path / ".git").exists()


def _same_path(a, b):
    try:
        return Path(a).resolve() == Path(b).resolve()
    except Exception:
        return False


def resolve_project_root(ai_id, event):
    """Resolve the project root: AI channel -> git -> script-relative, with a
    read-only signature test adjudicating when the channel and git disagree."""
    adapter = adapters.get(ai_id)
    channel = None
    if adapter is not None:
        try:
            channel = adapter.root_from_event(event)
        except Exception:
            channel = None
    channel = Path(channel) if channel else None
    git_root = _git_toplevel()

    # Agreement (or only one present) -> trust it without the signature test.
    if channel and git_root and _same_path(channel, git_root):
        return channel
    if channel and not git_root and channel.is_dir():
        return channel
    if git_root and not channel:
        return git_root

    # Disagreement: let the signature test decide, not a guess.
    for candidate in (channel, git_root, SCRIPT_RELATIVE_ROOT):
        if candidate and Path(candidate).is_dir() and _has_signature(candidate):
            return Path(candidate)
    for candidate in (channel, git_root, SCRIPT_RELATIVE_ROOT):
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    return SCRIPT_RELATIVE_ROOT


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def evaluate(ctx):
    """Run the category's rules. Return (block_decision|None, [warns]).

    A crashing rule is logged and skipped (allow), never propagated - one bad
    rule must not freeze the agent or mask the others.
    """
    warns = []
    for check in rules_for(ctx.category):
        try:
            decision = check(ctx)
        except Exception as exc:
            fire_log({"event": "rule_error", "ai": ctx.ai_id,
                      "rule": getattr(check, "__name__", "?"), "error": repr(exc)})
            continue
        if decision is None:
            continue
        if decision.action == Decision.BLOCK:
            return decision, warns
        if decision.action == Decision.WARN:
            warns.append(decision)
    return None, warns


def run_ai(ai_id, raw_text):
    """Evaluate one firing event for an AI. Returns a process exit code."""
    adapter = adapters.get(ai_id)
    if adapter is None:
        fire_log({"event": "unknown_ai", "ai": ai_id})
        return 0  # no adapter -> no enforcement, never block

    try:
        event = json.loads(raw_text) if raw_text and raw_text.strip() else {}
    except Exception as exc:
        fire_log({"event": "parse_error", "ai": ai_id, "error": repr(exc)})
        return 0  # cannot read the event -> allow

    try:
        project_root = resolve_project_root(ai_id, event)
        ctx = adapter.parse_event(event, project_root)
    except Exception as exc:
        fire_log({"event": "adapter_error", "ai": ai_id, "error": repr(exc)})
        return 0
    if ctx is None:
        return 0  # not a guarded tool

    block, warns = evaluate(ctx)
    for warn in warns:
        fire_log({"event": "warn", "ai": ai_id, "rule": warn.rule,
                  "tool": ctx.tool_name, "reason": warn.reason})
    if block is not None:
        fire_log({"event": "block", "ai": ai_id, "rule": block.rule,
                  "tool": ctx.tool_name,
                  "file": ctx.file_path, "command": ctx.command})
        try:
            return adapter.emit_block(block, ctx)
        except Exception as exc:
            fire_log({"event": "emit_error", "ai": ai_id, "error": repr(exc)})
            return 0
    return 0


# ---------------------------------------------------------------------------
# Git pre-commit mode (rule B3 layer 2)
# ---------------------------------------------------------------------------
def run_precommit():
    """Run personal-data-guard as the commit gate.

    Block (exit 1) on a real personal-data FAIL; warn and ALLOW (exit 0) if the
    guard itself crashes, so a guard bug never locks the user out of committing.
    """
    root = _git_toplevel() or SCRIPT_RELATIVE_ROOT
    guard_path = root / "workflows" / "personal-data-guard" / "scripts" / "run.py"
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("pdg_guard", str(guard_path))
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        findings = guard.run_check(root)
    except Exception as exc:
        sys.stderr.write(
            f"[rule-hooks] WARNING: personal-data-guard could not run "
            f"({exc!r}); allowing the commit. Review manually.\n"
        )
        return 0

    fails = [f for f in findings if f[0] == "FAIL"]
    if not fails:
        return 0
    detail = "\n".join(f"  - {f[2]}" for f in fails)
    sys.stderr.write(format_block(
        "Commit blocked - personal data in committable files.",
        "personal-data-guard found the following in tracked or staged files:\n"
        + detail,
        "Remove the personal data or move it into a gitignored file, then "
        "commit again. Human override (use sparingly): git commit --no-verify.",
    ) + "\n")
    return 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Book Dragon deterministic rule-enforcement hook.")
    parser.add_argument("--ai", help="AI id whose hook is firing (e.g. claude, codex)")
    parser.add_argument("--reinject", action="store_true",
                        help="Print the SessionStart rule reminder and exit.")
    parser.add_argument("--precommit", action="store_true",
                        help="Run the git pre-commit personal-data gate.")
    args = parser.parse_args()

    try:
        if args.reinject:
            print(REMINDER)
            return 0
        if args.precommit:
            return run_precommit()
        if args.ai:
            raw = ""
            try:
                if not sys.stdin.isatty():
                    raw = sys.stdin.read()
            except Exception:
                raw = ""
            return run_ai(args.ai, raw)
    except Exception as exc:
        # Last-resort fail-safe: the hook must never crash the agent.
        fire_log({"event": "fatal", "error": repr(exc)})
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
