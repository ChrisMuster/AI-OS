#!/usr/bin/env python3
"""
run.py - review-orchestration workflow entry point.

Modes:
  --check-brief <brief>  Check a run brief. Calls the checker in brief.py and
                         returns its result unchanged: the same PASS or FAIL lines
                         on stdout, the same exit code, the same LOG.md entries.
  --run <brief>          Start a build-and-review run from a brief that passes the
                         check (loop.py). Needs --item, the name of the review packet
                         (memory/<item>_review_packet.md, which must not exist yet),
                         and --git-dir, the personal repository the run's preflight
                         checks is captured. --wait sleeps through a usage limit that
                         reported its reset time and resumes by itself.
                         --inject-limit <step> is test-only: that step raises a usage
                         limit at its provider boundary, once, to prove a clean stop
                         and resume.
  --resume <run-id>      Continue an ended run whose stop reason allows it
                         (max-rounds, usage-limit, error) from its last completed
                         step. --rounds N allows N more review rounds.
  --stop <run-id>        End a paused run.

--dry-run: with --check-brief, prints the LOG.md entries to stderr instead of writing
them; with --run, makes every read-only preflight check and says what it would do,
creating nothing and calling no model; with --resume and --stop, makes every check
that mode makes and says what it would do, changing nothing.

Exit codes: 0 when the check passes or a run ends reviewed-clean or clean; 1 when a
check fails or a run ends any other way; 2 on a usage error; 3 when a run is refused
before it starts or resumes.

Usage:
  python workflows/review-orchestration/scripts/run.py --check-brief <brief>
  python workflows/review-orchestration/scripts/run.py --run <brief> --item <item> --git-dir <dir>
  python workflows/review-orchestration/scripts/run.py --resume <run-id> [--rounds N]
  python workflows/review-orchestration/scripts/run.py --stop <run-id>
"""

import argparse
import asyncio
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_DIR))

import brief  # noqa: E402

REFUSED = 3


def _outcome(run):
    state = run.state
    if state["status"] != "ended":
        print(f"Run {state['run_id']} is {state['status']} ({state.get('pause')}).")
        return 1
    reason = state["stop_reasons"][-1]["reason"]
    print(f"Run {state['run_id']} ended {reason}. Report: "
          f"workflows/review-orchestration/runs/{state['run_id']}/report.md")
    return 0 if reason in ("reviewed-clean", "clean") else 1


def _refused(exc):
    for problem in exc.args[0] if exc.args and isinstance(exc.args[0], list) else [exc]:
        print(f"REFUSED: {problem}")
    return REFUSED


def main(argv=None, *, root=brief.PROJECT_ROOT, log_path=brief.LOG_PATH, deps=None,
         runs_dir=None):
    brief._reconfigure_streams()
    parser = argparse.ArgumentParser(
        prog="run.py", description="Review-orchestration workflow entry point.")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check-brief", metavar="BRIEF",
                       help="check a run brief (project-relative path)")
    modes.add_argument("--run", metavar="BRIEF", help="start a run from a brief")
    modes.add_argument("--resume", metavar="RUN_ID", help="continue an ended run")
    modes.add_argument("--stop", metavar="RUN_ID", help="end a paused run")
    parser.add_argument("--item", help="the review packet's item name (with --run)")
    parser.add_argument("--git-dir", help="the personal repository (with --run)")
    parser.add_argument("--rounds", type=int, help="more review rounds (with --resume)")
    parser.add_argument("--wait", action="store_true",
                        help="sleep through a usage limit with a reset time, then resume")
    parser.add_argument("--inject-limit", metavar="STEP",
                        help="test only: raise a usage limit at this step, once")
    parser.add_argument("--dry-run", action="store_true",
                        help="check and say what would happen, changing nothing")
    args = parser.parse_args(argv)

    if args.check_brief:
        return brief.run_check(args.check_brief, root=root, log_path=log_path,
                               dry_run=args.dry_run)

    import loop  # the run modes need the SDK-facing modules; the check does not
    import runrecord
    runs_dir = runs_dir or runrecord.RUNS_DIR
    common = {"root": root, "deps": deps, "log_path": log_path, "runs_dir": runs_dir}
    try:
        if args.run:
            if not args.item or not args.git_dir:
                parser.error("--run needs --item and --git-dir")
            run = loop.start(args.run, args.item, args.git_dir, inject=args.inject_limit,
                             dry_run=args.dry_run, wait=args.wait, **common)
            if run is None:
                return 0
        elif args.resume:
            if args.rounds is not None and args.rounds < 1:
                parser.error("--rounds must be at least 1")
            run = loop.load(args.resume, wait=args.wait, **common)
            loop.resume(run, rounds=args.rounds, dry_run=args.dry_run)
            if args.dry_run:
                return 0
        else:
            run = loop.load(args.stop, **common)
            loop.stop(run, dry_run=args.dry_run)
            if args.dry_run:
                return 0
            return _outcome(run)
        asyncio.run(run.advance())
    except loop.RunRefused as exc:
        return _refused(exc)
    return _outcome(run)


if __name__ == "__main__":
    sys.exit(main())
