#!/usr/bin/env python3
"""
run.py - review-orchestration workflow entry point.

Stage A1 ships one mode. Later sub-stages add the run modes.

Modes:
  --check-brief <brief>  Check a run brief. Calls the checker in brief.py and
                         returns its result unchanged: the same PASS or FAIL lines
                         on stdout, the same exit code, the same LOG.md entries.
                         Honours --dry-run, which prints the LOG.md entries to
                         stderr instead of writing them.

Usage:
  python workflows/review-orchestration/scripts/run.py --check-brief <brief>
  python workflows/review-orchestration/scripts/run.py --check-brief <brief> --dry-run
"""

import argparse
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_DIR))

import brief  # noqa: E402


def main(argv=None, *, root=brief.PROJECT_ROOT, log_path=brief.LOG_PATH):
    brief._reconfigure_streams()
    parser = argparse.ArgumentParser(
        prog="run.py", description="Review-orchestration workflow entry point.")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check-brief", metavar="BRIEF",
                       help="check a run brief (project-relative path)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the LOG.md entries to stderr instead of writing them")
    args = parser.parse_args(argv)
    return brief.run_check(args.check_brief, root=root, log_path=log_path,
                           dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
