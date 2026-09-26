#!/usr/bin/env python3
"""
runrecord.py - creates a run's record folder and keeps its state file.

Every run keeps its record in ``workflows/review-orchestration/runs/<run-id>/``
(plan section 10.7). The folder is gitignored and classified for the personal
repository, so a capture after a run picks up the whole record. This module makes
the run ID, creates the folder with its first file, ``state.json``, and writes that
file again after every completed step, atomically, so a stop loses at most the step
in progress.

A run ID is the local start time and four random hex digits,
``YYYYMMDD-HHMMSS-xxxx``, so IDs sort by start time and two runs started in the same
second do not collide. Creating a run whose folder already exists is refused: a run
record is never reused or overwritten.
"""

import json
import os
import re
import secrets
import sys
from datetime import datetime
from pathlib import Path

_WORKFLOW_DIR = Path(__file__).resolve().parent.parent
RUNS_DIR = _WORKFLOW_DIR / "runs"
STATE_FILE = "state.json"

RUN_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")


def new_run_id(now=None, token=None):
    """A fresh run ID. ``now`` and ``token`` are for tests."""
    now = now or datetime.now().astimezone()
    token = token if token is not None else secrets.token_hex(2)
    return f"{now:%Y%m%d-%H%M%S}-{token}"


def initial_state(run_id, now=None):
    """The state a new run starts in: running, at its first step, nothing recorded."""
    now = now or datetime.now().astimezone()
    return {
        "run_id": run_id,
        "created": now.isoformat(timespec="seconds"),
        "status": "running",
        "step": "created",
        "pause": None,
        "stop_reasons": [],
        "rounds": [],
    }


def create_run(run_id=None, *, runs_dir=RUNS_DIR, now=None, dry_run=False):
    """Create ``runs/<run-id>/`` with its ``state.json``. Returns the folder's path.

    Refuses an ID not in the ``YYYYMMDD-HHMMSS-xxxx`` form and a folder that already
    exists. ``dry_run`` prints what would be created and writes nothing.
    """
    run_id = run_id or new_run_id(now)
    if not RUN_ID.match(run_id):
        raise ValueError(f"not a run ID: {run_id!r} (expected YYYYMMDD-HHMMSS-xxxx)")
    run_dir = Path(runs_dir) / run_id
    if run_dir.exists():
        raise FileExistsError(f"run record already exists: {run_id}")
    state = initial_state(run_id, now)
    if dry_run:
        print(f"[DRY RUN] would create {run_dir.name}/ with {STATE_FILE}", file=sys.stderr)
        return run_dir
    run_dir.mkdir(parents=True, exist_ok=False)
    write_state(run_dir, state)
    return run_dir


def write_state(run_dir, state):
    """Write ``state.json`` atomically: a temporary file in the same folder, then a
    replace, so a reader never sees half a file."""
    run_dir = Path(run_dir)
    target = run_dir / STATE_FILE
    temporary = run_dir / f"{STATE_FILE}.tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temporary, target)


def read_state(run_dir):
    """Read a run's ``state.json``."""
    with open(Path(run_dir) / STATE_FILE, encoding="utf-8") as handle:
        return json.load(handle)
