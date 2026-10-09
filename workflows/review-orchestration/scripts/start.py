#!/usr/bin/env python3
"""
start.py - the starter: every command that runs the review orchestrator from the
project goes through here (orchestrator isolation plan 7.0 items 2 and 3, stage S6a).

  start.py --run <brief> ...        Refuses unless the whole project is committed
  start.py --run <brief> --dry-run  (``git status --porcelain --untracked-files=all``
                                    prints nothing), then runs the project's run.py.
  start.py --check-brief <brief>    Refuses unless the two folders the brief checker
                                    loads are committed, then runs run.py.
  start.py --resume <run-id> ...    No git check: a paused run is expected to have the
  start.py --stop <run-id> ...      builder's uncommitted edits. Refuses unless the run
                                    id has the run-record form, its frozen folder holds
                                    hook.json and the trusted run.py, and hook.json's
                                    project_root is this project; then runs that
                                    trusted run.py, which loads nothing from the live
                                    tree. Everything after the run id is passed on
                                    unchanged, and the trusted run.py alone decides
                                    which options are valid.

A start or brief check also refuses a ``BOOK_DRAGON_ROOT`` naming any project but this
one, since run.py would work on the project it names, which nothing here checked; for a
resume or stop the trusted run.py refuses one that disagrees with its hook.json.

The mode must be the first argument. run.py runs under the project's ``.venv``
interpreter, and a refusal names what to fix and exits 3, having run nothing. No
builder may change this file (the brief checker, the approver and the Codex rules all
refuse it), and it imports nothing from the project: standard library only.

    python workflows/review-orchestration/scripts/start.py --run <brief> --builder claude|codex --item <item> --git-dir <dir>
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REFUSED = 3
ROOT = Path(__file__).resolve().parents[3]
RUN_PY = "workflows/review-orchestration/scripts/run.py"
FROZEN_PARENT = "book-dragon-orchestration"
TRUSTED_RUN_PY = ("trusted", "workflows", "review-orchestration", "scripts", "run.py")
# The run-record form (runrecord.RUN_ID), restated: the starter imports nothing.
RUN_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
# The only code the brief checker loads (plan 7.0 item 2).
BRIEF_FOLDERS = ("workflows/review-orchestration", "workflows/sync-architecture/scripts")
STARTER_MARKER = "BOOK_DRAGON_REVIEW_ORCHESTRATION_STARTER"
ROOT_ENV = "BOOK_DRAGON_ROOT"
USAGE = ("usage: start.py --run <brief> ... | --check-brief <brief> | "
         "--resume <run-id> ... | --stop <run-id> ...")


class Refused(Exception):
    """The starter refuses; the message says why and what to do."""


def venv_python(root=ROOT):
    """The project .venv interpreter for this platform."""
    if os.name == "nt":
        return Path(root) / ".venv" / "Scripts" / "python.exe"
    return Path(root) / ".venv" / "bin" / "python"


def uncommitted(root, folders=()):
    """What ``git status --porcelain --untracked-files=all`` prints, over the whole
    project or the given folders, as lines. Raises Refused if git cannot answer."""
    command = ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"]
    if folders:
        command += ["--", *folders]
    try:
        done = subprocess.run(command, capture_output=True, encoding="utf-8",
                              errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Refused(f"git status could not run: {exc}") from exc
    if done.returncode != 0:
        raise Refused(f"git status failed: {done.stderr.strip() or done.returncode}")
    return [line for line in done.stdout.splitlines() if line.strip()]


def check_committed(root, folders=()):
    dirty = uncommitted(root, folders)
    if dirty:
        where = "the project" if not folders else " and ".join(folders)
        shown = "; ".join(line.strip() for line in dirty[:20])
        more = f" (and {len(dirty) - 20} more)" if len(dirty) > 20 else ""
        raise Refused(f"{where} has uncommitted changes: {shown}{more}. Commit or "
                      "discard them, then start again: a run runs only committed code")


def trusted_target(run_id, root=ROOT, temp_root=None):
    """The trusted run.py for a resume or stop, after every check plan 7.0 item 3
    names. Raises Refused naming the first that fails."""
    if not RUN_ID.match(run_id or ""):
        raise Refused(f"{run_id!r} is not a run id (YYYYMMDD-HHMMSS-xxxx)")
    frozen = Path(temp_root or tempfile.gettempdir()) / FROZEN_PARENT / run_id
    hook = frozen / "hook.json"
    trusted = frozen.joinpath(*TRUSTED_RUN_PY)
    if not hook.is_file() or not trusted.is_file():
        raise Refused(f"run {run_id} has no frozen folder holding hook.json and its "
                      f"trusted run.py ({frozen.as_posix()}); a run whose frozen folder "
                      "is gone cannot be resumed or stopped, only started again")
    try:
        project = json.loads(hook.read_text(encoding="utf-8"))["project_root"]
        same = (os.path.normcase(os.path.realpath(project))
                == os.path.normcase(os.path.realpath(root)))
    except (OSError, ValueError, KeyError, TypeError):
        raise Refused(f"run {run_id}'s hook.json cannot be read") from None
    if not same:
        raise Refused(f"run {run_id} belongs to another project ({project})")
    return trusted


def check_root_override(root, environ):
    """Refuse a BOOK_DRAGON_ROOT naming a project other than the starter's own (code
    review R22-1): run.py would work on that project, which the starter's committed-
    tree check never looked at. A value naming this project is allowed."""
    given = environ.get(ROOT_ENV)
    if given is None:
        return
    same = bool(given) and (os.path.normcase(os.path.realpath(given))
                            == os.path.normcase(os.path.realpath(root)))
    if not same:
        raise Refused(f"{ROOT_ENV} is set to {given!r}, but the starter checked "
                      f"{Path(root).as_posix()}; unset it, or run the starter of the "
                      "project it names")


def command_for(argv, root=ROOT, temp_root=None, python=None, environ=None):
    """The command the starter runs for ``argv``, after its checks. Raises Refused."""
    if not argv:
        raise Refused(USAGE)
    environ = os.environ if environ is None else environ
    mode = argv[0]
    python = str(python or venv_python(root))
    if mode in ("--resume", "--stop"):
        if len(argv) < 2:
            raise Refused(f"{mode} needs a run id. {USAGE}")
        trusted = trusted_target(argv[1], root, temp_root)
        return [python, str(trusted), *argv]
    if mode == "--run":
        check_root_override(root, environ)
        check_committed(root)
    elif mode == "--check-brief":
        check_root_override(root, environ)
        check_committed(root, BRIEF_FOLDERS)
    else:
        raise Refused(f"the first argument must be the mode. {USAGE}")
    return [python, str(Path(root) / RUN_PY), *argv]


def main(argv=None, *, root=ROOT, temp_root=None, python=None, runner=subprocess.run,
         environ=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    environ = os.environ if environ is None else environ
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    try:
        command = command_for(argv, root, temp_root, python, environ)
        if not Path(command[0]).exists():
            raise Refused(f"the project interpreter {command[0]} is missing; run "
                          "python workflows/biblio-tools/scripts/setup.py first")
    except Refused as exc:
        print(f"REFUSED: {exc}")
        return REFUSED
    env = dict(environ, **{STARTER_MARKER: "1"})
    if argv[0] in ("--resume", "--stop"):
        # The trusted copy writes no bytecode cache in its frozen folder (code review
        # R20-1); it also turns that off itself, so this is a second layer.
        env["PYTHONDONTWRITEBYTECODE"] = "1"
    return runner(command, cwd=str(root), env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
