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

from brief import PROJECT_ROOT

# Run records are run data, so they live under the project root, which follows
# BOOK_DRAGON_ROOT (brief.PROJECT_ROOT), never beside a copy of this module.
RUNS_DIR = PROJECT_ROOT / "workflows" / "review-orchestration" / "runs"
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
    replace, so a reader never sees half a file. Non-ASCII characters are escaped, as
    in every JSON file of a run record (the chunk (d) short plan, 11.4)."""
    run_dir = Path(run_dir)
    target = run_dir / STATE_FILE
    temporary = run_dir / f"{STATE_FILE}.tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(state, handle, indent=2)
        handle.write("\n")
    os.replace(temporary, target)


def read_state(run_dir):
    """Read a run's ``state.json``."""
    with open(Path(run_dir) / STATE_FILE, encoding="utf-8") as handle:
        return json.load(handle)


# A measurement's record (plan 10.8, A5): ``runs/measure-<id>/``, beside the run records,
# so the folder's gitignore and personal capture cover it; its id has the run-id form.
MEASURE_PREFIX = "measure-"


def measure_dir(measure_id, *, runs_dir=RUNS_DIR):
    """Where a measurement's record is: ``runs/measure-<id>/``. Refuses an id not in
    the run-id form."""
    if not RUN_ID.match(measure_id or ""):
        raise ValueError(f"not a measurement id: {measure_id!r} "
                         "(expected YYYYMMDD-HHMMSS-xxxx)")
    return Path(runs_dir) / f"{MEASURE_PREFIX}{measure_id}"


def create_measure(measure_id, *, runs_dir=RUNS_DIR):
    """Create ``runs/measure-<id>/``; a folder already there is refused, as a run's is,
    so a measurement record is never reused or overwritten. Returns its path."""
    folder = measure_dir(measure_id, runs_dir=runs_dir)
    if folder.exists():
        raise FileExistsError(f"measurement record already exists: {folder.name}")
    folder.parent.mkdir(parents=True, exist_ok=True)
    folder.mkdir()
    return folder


def write_json_atomic(path, data):
    """Write a JSON file atomically, as ``write_state`` writes ``state.json``: a
    temporary file beside it, then a replace, so a reader never sees half a file and a
    crash leaves the last whole version. Non-ASCII characters are escaped."""
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def write_text_atomic(path, text):
    """Write a text file atomically (LF line endings), as ``write_json_atomic``."""
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(temporary, path)


# The intent check's start snapshot (plan 10.6, "What a changed path is"): written once
# when the run is created, before the first planner, and never rewritten. The run removes
# it at a stop reason a resume cannot continue, and the personal repository never takes it.
START_SNAPSHOT_FILE = "start-snapshot.json"


class SnapshotError(Exception):
    """``start-snapshot.json`` is missing, unreadable or not in its shape."""


def write_start_snapshot(run_dir, snapshot):
    """Write ``start-snapshot.json`` once. A file already there raises
    ``FileExistsError``: the snapshot is never rewritten (an approval's scope rebuild and
    an amendment leave it as the run's creation wrote it)."""
    with open(Path(run_dir) / START_SNAPSHOT_FILE, "x", encoding="utf-8",
              newline="\n") as handle:
        json.dump(snapshot, handle)
        handle.write("\n")


def read_start_snapshot(run_dir):
    """Read and check ``start-snapshot.json``: an object holding ``files``, each path
    mapped to ``{"sha256": text, "size": int}``, and ``existence``, a list of paths.
    Anything else raises ``SnapshotError``, so a check never reads a damaged snapshot as
    a run that changed nothing."""
    path = Path(run_dir) / START_SNAPSHOT_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SnapshotError(f"{START_SNAPSHOT_FILE} cannot be read: {exc}") from exc
    files = data.get("files") if isinstance(data, dict) else None
    existence = data.get("existence") if isinstance(data, dict) else None
    if not isinstance(files, dict) or not isinstance(existence, list):
        raise SnapshotError(f"{START_SNAPSHOT_FILE} does not hold files and existence")
    for name, entry in files.items():
        if (not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str)
                or not isinstance(entry.get("size"), int)
                or isinstance(entry.get("size"), bool)):
            raise SnapshotError(f"{START_SNAPSHOT_FILE}: the entry for {name!r} is not "
                                "a hash and a size")
    if not all(isinstance(name, str) for name in existence):
        raise SnapshotError(f"{START_SNAPSHOT_FILE}: the existence listing holds a "
                            "value that is not a path")
    return data
