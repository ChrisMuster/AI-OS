#!/usr/bin/env python3
"""
run.py - review-orchestration workflow entry point.

Modes:
  --check-brief <brief>  Check a run brief. Calls the checker in brief.py and
                         returns its result unchanged: the same PASS or FAIL lines
                         on stdout, the same exit code, the same LOG.md entries.
  --run <brief>          Start a build-and-review run from a brief that passes the
                         check (loop.py). Needs --builder claude|codex, the provider
                         the user named to build (the other reviews; there is no
                         default, and an AI asked to start a run with no builder named
                         asks the user), --item, the name of the review packet
                         (memory/<item>_review_packet.md, which must not exist yet),
                         and --git-dir, the personal repository the run's preflight
                         checks is captured. --wait sleeps through a usage limit that
                         reported its reset time and resumes by itself.
                         --inject-limit <step> is test-only: that step raises a usage
                         limit at its provider boundary, once, to prove a clean stop
                         and resume.
  --resume <run-id>      Continue an ended run whose stop reason allows it
                         (max-rounds, usage-limit, error) from its last completed
                         step. --rounds N allows N more review rounds. The run keeps
                         the roles it started with, so --builder is refused here.
  --stop <run-id>        End a paused run.

--dry-run: with --check-brief, prints the LOG.md entries to stderr instead of writing
them; with --run, makes every read-only preflight check and says what it would do,
creating nothing and calling no model; with --resume and --stop, makes every check
that mode makes and says what it would do, changing nothing.

Where the code runs from (orchestrator isolation plan 7.0, stage S6a). A brief check,
a start, a resume and a stop are entered through the starter, ``start.py``, which
checks the code about to run is committed; run directly from the project for any of
the four, this script prints the starter command instead, before it imports anything
from the project. A start makes its preflight and run record
here, in the live tree, which the starter has just proved is the committed code; once
the run's frozen folder exists it arms the tripwire and hands the run to the frozen
folder's trusted copy of this script (``--advance``, a mode only that copy accepts),
which takes every step. Resume and stop go straight to the trusted copy. The trusted
copy, before importing anything else, arms the tripwire and takes the project root
from its frozen folder's ``hook.json``, refusing a set ``BOOK_DRAGON_ROOT`` that
disagrees with it.

Exit codes: 0 when the check passes or a run ends reviewed-clean or clean; 1 when a
check fails or a run ends any other way; 2 on a usage error, or a mode not entered
through the starter; 3 when a run is refused before it starts or
resumes, or the trusted copy cannot set itself up.

Usage (each through the starter, which runs this script):
  python workflows/review-orchestration/scripts/start.py --check-brief <brief>
  python workflows/review-orchestration/scripts/start.py --run <brief> --builder claude|codex --item <item> --git-dir <dir>
  python workflows/review-orchestration/scripts/start.py --resume <run-id> [--rounds N]
  python workflows/review-orchestration/scripts/start.py --stop <run-id>
"""

import argparse
import asyncio
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REFUSED = 3
ROOT_ENV = "BOOK_DRAGON_ROOT"
FROZEN_PARENT = "book-dragon-orchestration"
_RUN_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")


def trusted_frozen_folder(path=None):
    """The run's frozen folder when this file is a run's trusted copy, at
    ``<x>/book-dragon-orchestration/<run-id>/trusted/workflows/review-orchestration/
    scripts/run.py``; otherwise None."""
    path = Path(path) if path is not None else Path(__file__).resolve()
    parts = path.parents
    try:
        layout = (path.name == "run.py" and parts[0].name == "scripts"
                  and parts[1].name == "review-orchestration"
                  and parts[2].name == "workflows" and parts[3].name == "trusted"
                  and bool(_RUN_ID.match(parts[4].name))
                  and parts[5].name == FROZEN_PARENT)
    except IndexError:
        return None
    return parts[4] if layout else None


def arm_tripwire(frozen, root=None):
    """Load the frozen folder's tripwire by its path and install it (plan 7.8).
    Raises on any failure, naming the file."""
    path = Path(frozen) / "tripwire" / "sitecustomize.py"
    spec = importlib.util.spec_from_file_location("_bookdragon_tripwire", path)
    if spec is None or not path.is_file():
        raise RuntimeError(f"the tripwire {path.as_posix()} is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.install(root)


def set_up_trusted(frozen, environ=None):
    """The trusted copy's first statements (plan 7.0 item 3 and 7.8): arm the tripwire,
    then take the project root from ``hook.json`` and set ``BOOK_DRAGON_ROOT`` to it.
    Returns None, or the reason the copy cannot run."""
    environ = os.environ if environ is None else environ
    try:
        arm_tripwire(frozen)
    except Exception as exc:  # noqa: BLE001 - whatever failed, nothing else loads
        return f"the tripwire could not be armed ({exc})"
    try:
        config = json.loads((Path(frozen) / "hook.json").read_text(encoding="utf-8"))
        root = Path(config["project_root"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return f"the run's hook.json cannot be read ({type(exc).__name__})"
    if not root.is_dir() or not (root / "AGENTS.md").is_file():
        return f"hook.json names {root.as_posix()}, which is not a folder holding AGENTS.md"
    given = environ.get(ROOT_ENV)
    if given is not None and (not given or os.path.normcase(os.path.realpath(given))
                              != os.path.normcase(os.path.realpath(root))):
        return (f"{ROOT_ENV} is {given!r}, but this run's hook.json names "
                f"{root.as_posix()}")
    environ[ROOT_ENV] = str(root)
    return None


# The starter (brief.STARTER_PATH, restated: nothing from the project may be imported
# before the gate below; a test checks the two agree), and the marker it sets so this
# script knows it was entered through it.
STARTER_PATH = "workflows/review-orchestration/scripts/start.py"
STARTER = "python " + STARTER_PATH
STARTER_MARKER = "BOOK_DRAGON_REVIEW_ORCHESTRATION_STARTER"
STARTER_MODES = ("--check-brief", "--run", "--resume", "--stop")


def needs_starter(argv, environ=None, frozen=None):
    """The message to print when this script is run straight from the project for a
    brief check, a start, a resume or a stop rather than through the starter (plan 7.0
    item 2); None otherwise, and always None for a run's trusted copy (``frozen``)."""
    environ = os.environ if environ is None else environ
    if frozen is not None or environ.get(STARTER_MARKER) == "1":
        return None
    if not any(arg in STARTER_MODES or arg.split("=", 1)[0] in STARTER_MODES
               for arg in argv):
        return None
    return ("The review orchestrator is run through the starter, which checks the "
            f"code it is about to run is committed: {STARTER} " + " ".join(argv))


_FROZEN = trusted_frozen_folder()
# The gate comes before any project import (code review R18-2): run straight from the
# project, this script loads nothing of it, so a builder's edit to a module it imports
# never runs on the way to the redirect.
if __name__ == "__main__" and _FROZEN is None:
    _message = needs_starter(sys.argv[1:])
    if _message is not None:
        print(_message)
        sys.exit(2)
if _FROZEN is not None:
    # Nothing writes in the frozen folder after setup but the two record files (plan
    # 7.0 item 3), so the trusted copy writes no bytecode cache, for the tripwire it
    # loads or any module it imports (code review R20-1). Started by hand too.
    sys.dont_write_bytecode = True
    _problem = set_up_trusted(_FROZEN)
    if _problem is not None:
        sys.stderr.write(f"REFUSED: the run's trusted copy cannot start: {_problem}\n")
        sys.exit(REFUSED)

_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_DIR))

import brief  # noqa: E402

# Set on the child so the project interpreter never re-runs itself again.
REEXEC_MARKER = "BOOK_DRAGON_REVIEW_ORCHESTRATION_REEXEC"


def venv_python(root=brief.PROJECT_ROOT):
    """The project .venv interpreter for this platform (it may not exist)."""
    if os.name == "nt":
        return Path(root) / ".venv" / "Scripts" / "python.exe"
    return Path(root) / ".venv" / "bin" / "python"


def reexec_under_venv(argv, *, target=None, executable=None, environ=None,
                      runner=subprocess.run):
    """Re-run this script under the project .venv interpreter when the current one is
    another, and return the child's exit code; return None to carry on here.

    The documented command is ``python workflows/review-orchestration/scripts/run.py``,
    and which ``python`` that finds depends on the shell: a Codex session's PowerShell
    found the system interpreter, which lacks the two SDKs, so the run was refused. The
    SDKs live in the project .venv, as the close-out verifier's packages do, so this
    does what its ``reexec_under_venv`` does. Everything the run starts later uses
    ``sys.executable``, so it follows. With no .venv, or if the re-run cannot start,
    the current interpreter carries on and the SDK check says what is missing.
    """
    environ = os.environ if environ is None else environ
    if environ.get(REEXEC_MARKER) == "1":
        return None
    target = Path(target) if target is not None else venv_python()
    executable = executable or sys.executable
    try:
        if not target.exists() or target.resolve() == Path(executable).resolve():
            return None
        completed = runner([str(target), str(Path(__file__).resolve()), *argv],
                           env=dict(environ, **{REEXEC_MARKER: "1"}))
    except OSError:
        return None
    return completed.returncode


def hand_over(run, *, root=brief.PROJECT_ROOT, wait=False, environ=None,
              runner=subprocess.run, python=None, arm=None):
    """A start's last act in the live tree (plan 7.0 item 3): arm the tripwire, then
    run the frozen folder's trusted copy of this script with ``--advance <run-id>``,
    which takes every step, and return its exit code. The live tree is the committed
    code up to here; from here on nothing of it is executed.

    A hand-over that fails leaves no run stranded (code review R19-3): if the tripwire
    cannot be armed, the trusted copy cannot be started, or it exits without ending
    the run, the run is ended ``error`` here, naming why, so its report and log entry
    exist and the starter can resume it once the cause is fixed. An interrupt
    (Ctrl+C reaches both processes) is left to the trusted copy."""
    import frozen as frozen_mod
    import loop

    environ = os.environ if environ is None else environ
    arm = arm or arm_tripwire
    folder = Path(run.state["frozen_dir"])
    problem, code = None, None
    try:
        arm(folder, root)
    except Exception as exc:  # noqa: BLE001 - any failure is the run's error
        problem = f"the tripwire could not be armed before the hand-over ({exc})"
    if problem is None:
        python = python or venv_python(root)
        if not Path(python).exists():
            python = sys.executable
        argv = [str(python), str(frozen_mod.trusted_run_py(folder)), "--advance",
                run.state["run_id"], *(["--wait"] if wait else [])]
        env = {k: v for k, v in environ.items() if k != STARTER_MARKER}
        env.update({ROOT_ENV: str(root), REEXEC_MARKER: "1",
                    "PYTHONDONTWRITEBYTECODE": "1"})
        try:
            code = runner(argv, cwd=str(root), env=env).returncode
        except Exception as exc:  # noqa: BLE001
            problem = (f"the run's trusted copy could not be started "
                       f"({type(exc).__name__}: {exc})")
    again = loop.load(run.state["run_id"], root=run.root, deps=run.deps,
                      log_path=run.log_path, runs_dir=run.run_dir.parent)
    if again.state.get("status") != loop.sr.RUNNING:
        return code
    reason = problem or f"the run's trusted copy exited {code} without ending the run"
    again.end(loop.sr.ERROR, {"hand_over": reason})
    print(f"Run {run.state['run_id']} ended error: {reason}. Fix the cause, then resume: "
          f"{STARTER} --resume {run.state['run_id']}")
    return code if code not in (None, 0) else 1


def _outcome(run):
    import loop

    state = run.state
    if state["status"] != "ended":
        print(f"Run {state['run_id']} is {state['status']} ({state.get('pause')}). "
              + loop.pause_commands(state["run_id"]))
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
         runs_dir=None, frozen=_FROZEN, handover=None):
    """``frozen`` is the run's frozen folder when this is its trusted copy. ``handover``
    is what a started run is passed to instead of being advanced here: the command
    line passes ``hand_over``, so a start from the live tree hands the run to the
    trusted copy; the tests leave it out and advance in-process."""
    brief._reconfigure_streams()
    parser = argparse.ArgumentParser(
        prog="run.py", description="Review-orchestration workflow entry point.")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check-brief", metavar="BRIEF",
                       help="check a run brief (project-relative path)")
    modes.add_argument("--run", metavar="BRIEF", help="start a run from a brief")
    modes.add_argument("--resume", metavar="RUN_ID", help="continue an ended run")
    modes.add_argument("--stop", metavar="RUN_ID", help="end a paused run")
    # The trusted copy's own mode: take a started run's steps (plan 7.0 item 3).
    modes.add_argument("--advance", metavar="RUN_ID", help=argparse.SUPPRESS)
    parser.add_argument("--builder", choices=("claude", "codex"),
                        help="the provider that builds; the other reviews (with --run)")
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

    if args.builder and not args.run:
        parser.error("--builder is given only with --run: a resumed or stopped run "
                     "keeps the roles it started with")
    if args.check_brief:
        return brief.run_check(args.check_brief, root=root, log_path=log_path,
                               dry_run=args.dry_run)
    if args.advance and frozen is None:
        parser.error("--advance is used only by a run's trusted copy")
    if args.run and frozen is not None:
        parser.error("a run is started through the starter, never from a trusted copy")
    run_id = args.advance or args.resume or args.stop
    if frozen is not None and run_id != frozen.name:
        print(f"REFUSED: this trusted copy belongs to run {frozen.name}, not {run_id}")
        return REFUSED

    import loop  # the run modes need the SDK-facing modules; the check does not
    import runrecord
    runs_dir = runs_dir or runrecord.RUNS_DIR
    common = {"root": root, "deps": deps, "log_path": log_path, "runs_dir": runs_dir}
    try:
        if args.advance:
            run = loop.load(args.advance, wait=args.wait, **common)
        elif args.run:
            if not args.builder:
                parser.error("--run needs --builder claude or --builder codex: name the "
                             "provider that builds, and the other reviews")
            if not args.item or not args.git_dir:
                parser.error("--run needs --item and --git-dir")
            run = loop.start(args.run, args.item, args.git_dir, inject=args.inject_limit,
                             dry_run=args.dry_run, wait=args.wait, builder=args.builder,
                             **common)
            if run is None:
                return 0
            if handover is not None:
                return handover(run, root=root, wait=args.wait)
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
    code = reexec_under_venv(sys.argv[1:])
    sys.exit(main(handover=hand_over) if code is None else code)
