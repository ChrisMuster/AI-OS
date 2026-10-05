#!/usr/bin/env python3
"""
worktree.py - what a run changed in its edit paths, and the records of it
(plan section 10.4, "Saved before the run, recorded during it, never committed by it").

A run never commits. So what it changed is measured against two commits recorded at
its start: the public repository's ``HEAD`` for files git tracks or would track, and
the personal repository's ``HEAD`` for the gitignored files the personal repository
holds. Every diff a reviewer is shown therefore holds only the run's changes. The
git commands here all read; none writes to either repository.

  snapshot / changed   a SHA-256 per file under the edit paths, and the files that
                       differ between two snapshots. Used for the empty-diff stop
                       and for the directories the targeted audit checks.
  review_diff          the diff a reviewer is shown.
  record_start         ``runs/<run-id>/start/``: both commits and ``git diff HEAD``
                       of the edit paths, with any untracked file under them in full.
  record_round         ``runs/<run-id>/rounds/R<n>/``: every file under the edit paths
                       as that reviewer received it, in ``files.zip``, and the diff it
                       saw.

Three things are never part of the work and are skipped everywhere, in snapshots,
diffs and round archives alike:

  - Python bytecode caches (``__pycache__`` folders, ``.pyc`` files), which running a
    test rewrites.
  - ``.env`` and ``.env.*`` files, compared without case as Windows compares names, so
    a secret under a folder edit path never reaches a reviewer's prompt or a run record
    (plan section 3; review finding R1-2). The approver already refuses the builder
    any edit to one.
  - This workflow's ``runs/`` folder, so a run whose edit paths cover the workflow does
    not count its own record as the builder's work (review finding R1-3).

And a gitignored file is part of the work only when the sync classification puts it
in Category A, the hand-written files the personal repository captures: a memory file,
a LOG.md, a plan. Everything else gitignored under an edit path is Category B or C,
bulk data, caches and generated output such as the close-out verifier's own result
file, which a builder does not author and a run's checks rewrite (review finding R3-1,
the user's option A). The classification is the one shared implementation,
``workflows/sync-architecture/scripts/allowlist.py`` (``select(read_specs())["final"]``
with no ``git_dir``), the same call plan 10.4 names for chunk (d)'s inventory, so a new
Category A file the builder creates is recognised by its pattern and reviewed, and a
run that cannot read the classification does not start. Every function that lists
files takes it as ``category_a``, a set of project-relative paths; ``category_a(root)``
computes it.
"""

import difflib
import hashlib
import importlib.util
import json
import os
import subprocess
import zipfile
from pathlib import Path

SKIP_DIRS = {"__pycache__", ".git"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
RUNS_PREFIX = "workflows/review-orchestration/runs/"
# Pathspecs that keep the same files out of git's own diffs of tracked files.
EXCLUDE_SPECS = [":(exclude,icase,glob)**/.env", ":(exclude,icase,glob)**/.env.*",
                 ":(exclude,glob)**/__pycache__/**", ":(exclude)" + RUNS_PREFIX.rstrip("/")]


class GitError(Exception):
    """A git read failed."""


def git(root, *args, git_dir=None):
    """Run one read-only git command in the project root and return its stdout."""
    command = ["git"]
    if git_dir is not None:
        command += [f"--git-dir={git_dir}", "--work-tree=."]
    command += list(args)
    result = subprocess.run(command, cwd=str(root), capture_output=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise GitError(f"{' '.join(command[1:])} failed: {result.stderr.strip()}")
    return result.stdout


def _skipped(relative):
    parts = relative.split("/")
    folded = [part.lower().rstrip(". ") for part in parts]
    return (any(part in SKIP_DIRS for part in parts)
            or Path(relative).suffix.lower() in SKIP_SUFFIXES
            or any(part == ".env" or part.startswith(".env.") for part in folded)
            or (relative + "/").lower().startswith(RUNS_PREFIX))


def category_a(root):
    """The Category A selection, as a set of project-relative paths.

    Calls the sync-architecture workflow's one implementation. Run from the project
    root, as the workflow's scripts are. Raises GitError when the classification
    cannot be read, so a run never guesses which gitignored files are work.
    """
    path = Path(root) / "workflows" / "sync-architecture" / "scripts" / "allowlist.py"
    spec = importlib.util.spec_from_file_location("_sync_allowlist", path)
    if spec is None or not path.is_file():
        raise GitError(f"the sync classification cannot be read: {path.name} is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    specs = module.read_specs()
    if specs is None:
        raise GitError("the sync classification cannot be read: its plan or its "
                       "allowlist block is missing")
    return set(module.select(specs)["final"])


def _ignored(root, edit_paths):
    """Gitignored files under the edit paths git does not track, as a set."""
    specs = _pathspecs(edit_paths)
    if not specs:
        return set()
    return set(_lines(git(root, "ls-files", "--others", "--ignored",
                          "--exclude-standard", "--", *specs)))


def list_files(root, edit_paths, category_a):
    """Every file under the edit paths that is part of the work, project-relative
    with forward slashes: the skipped kinds apart, and a gitignored file only if it is
    in ``category_a``."""
    root = Path(root)
    ignored = _ignored(root, edit_paths)
    found = set()
    for edit in edit_paths:
        target = root / edit.rstrip("/")
        if target.is_file():
            candidates = [target]
        elif target.is_dir():
            candidates = [p for p in target.rglob("*") if p.is_file()]
        else:
            candidates = []
        for path in candidates:
            relative = path.relative_to(root).as_posix()
            if _skipped(relative):
                continue
            if relative in ignored and relative not in category_a:
                continue
            found.add(relative)
    return sorted(found)


def snapshot(root, edit_paths, category_a):
    """``{path: sha256}`` for every file under the edit paths that is part of the work."""
    root = Path(root)
    return {relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
            for relative in list_files(root, edit_paths, category_a)}


def contents(root, edit_paths, category_a):
    """``{path: bytes}`` for every file under the edit paths that is part of the work."""
    root = Path(root)
    return {relative: (root / relative).read_bytes()
            for relative in list_files(root, edit_paths, category_a)}


def delta(before, after, label):
    """What changed between two ``contents`` readings, as a unified diff.

    Used for the orchestrator's own checks: the reading either side of a check gives
    exactly the lines the check wrote, so the reviewer is told those lines, not whole
    files, are the check's (review finding R5-2). ``label`` names the check run.
    """
    parts = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        try:
            old_text = (old or b"").decode("utf-8").splitlines(keepends=True)
            new_text = (new or b"").decode("utf-8").splitlines(keepends=True)
        except UnicodeDecodeError:
            parts.append(f"--- {path} (binary, changed by {label}) ---\n")
            continue
        parts.append("".join(difflib.unified_diff(
            old_text, new_text, fromfile=f"{path} (before {label})",
            tofile=f"{path} (after {label})")))
    return "".join(part if part.endswith("\n") else part + "\n" for part in parts if part)


def changed(before, after):
    """Files added, removed or changed between two snapshots, sorted."""
    return sorted(path for path in set(before) | set(after)
                  if before.get(path) != after.get(path))


def changed_dirs(paths):
    """The directories holding the given files, sorted, without duplicates."""
    return sorted({(Path(path).parent.as_posix() or ".") for path in paths})


def _pathspecs(edit_paths):
    return [edit.rstrip("/") for edit in edit_paths]


def _lines(text):
    return [line for line in text.splitlines() if line.strip()]


def _new_file_block(root, relative):
    data = (Path(root) / relative).read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return f"--- new file {relative} (binary, {len(data)} bytes) ---\n"
    # Never cut: a diff is the record of what was reviewed, and a part-diff lets a
    # review pass without seeing the rest (review finding R4-1). A diff too large for
    # the reviewer's prompt is handled by the loop, not by shortening it here.
    return f"--- new file {relative} ---\n{text}\n"


def uncommitted(root, edit_paths):
    """Files under the edit paths with changes git does not hold: modified, staged,
    deleted or untracked, ignored files and the skipped kinds apart.

    A run refuses to start while this is not empty (review finding R2-2, the user's
    option A): a round's diff of tracked files is taken against the start commit, so
    work already there would be shown to the reviewer as the run's. Gitignored files
    need no such check: the Category A ones are committed to the personal repository by
    the capture the preflight insists on, and the rest are never part of the work
    (R3-1).
    """
    specs = _pathspecs(edit_paths)
    if not specs:
        return []
    out = git(root, "status", "--porcelain", "--untracked-files=all", "--", *specs,
              *EXCLUDE_SPECS)
    paths = [line[3:].strip().strip('"') for line in out.splitlines() if line.strip()]
    return [path for path in paths if not _skipped(path.split(" -> ")[-1])]


def review_diff(root, edit_paths, public_head, personal_head=None, git_dir=None, *,
                category_a):
    """The run's changes under the edit paths, as a reviewer is shown them.

    Tracked files: ``git diff <public start> -- <paths>``. New files git would track:
    in full. Gitignored files, Category A only: ``git diff <personal start>`` in the
    personal repository for the ones it held at the start (so a change or a deletion
    shows as a diff), and in full for a new one.
    """
    specs = _pathspecs(edit_paths)
    if not specs:
        return ""
    parts = [git(root, "diff", public_head, "--", *specs, *EXCLUDE_SPECS)]
    for relative in _lines(git(root, "ls-files", "--others", "--exclude-standard", "--",
                               *specs)):
        if not _skipped(relative):
            parts.append(_new_file_block(root, relative))
    ignored = [relative for relative in _lines(git(root, "ls-files", "--others",
                                                   "--ignored", "--exclude-standard",
                                                   "--", *specs))
               if not _skipped(relative) and relative in category_a]
    if git_dir is not None and personal_head is not None:
        public = set(_lines(git(root, "ls-files", "--", *specs)))
        held = [relative for relative in _lines(git(root, "ls-tree", "-r", "--name-only",
                                                    personal_head, "--", *specs,
                                                    git_dir=git_dir))
                if relative not in public and not _skipped(relative)]
        if held:
            parts.append(git(root, "diff", personal_head, "--", *held, git_dir=git_dir))
        held_set = set(held)
        ignored = [relative for relative in ignored if relative not in held_set]
    for relative in ignored:
        parts.append(_new_file_block(root, relative))
    return "".join(part if part.endswith("\n") or not part else part + "\n"
                   for part in parts)


# ------------------------------------------------- the check after every turn


def _git_state(root, git_dir=None):
    """One repository's own state: the staged contents, every ref, and where HEAD
    points. The raw index file is not hashed, since ``git status`` rewrites it to
    refresh timestamps."""
    state = {"index": git(root, "ls-files", "-s", git_dir=git_dir),
             "refs": git(root, "for-each-ref", "--format=%(refname) %(objectname)",
                         git_dir=git_dir)}
    for name, args in (("head_ref", ("symbolic-ref", "-q", "HEAD")),
                       ("head", ("rev-parse", "HEAD"))):
        try:
            state[name] = git(root, *args, git_dir=git_dir).strip()
        except GitError:  # a detached HEAD has no symbolic ref; an empty one no commit
            state[name] = None
    return state


def inventory(root, git_dir, category_a):
    """What the check after every Codex builder turn compares (plan 10.4, chunk (d)).

    ``files``: a SHA-256 for every file the public repository tracks, every new file
    it does not ignore, and the whole Category A selection. ``git``: both
    repositories' own state, so a ``git add``, a commit, a branch change or a stash is
    seen. Category B and C paths (bulk data, caches, generated output) are out of
    scope, as the plan states.
    """
    root = Path(root)
    # -z: names come back whole, never quoted, whatever characters they hold.
    paths = set(git(root, "ls-files", "-z").split("\0"))
    paths |= set(git(root, "ls-files", "-z", "--others", "--exclude-standard").split("\0"))
    paths |= set(category_a)
    paths.discard("")
    files = {}
    for relative in sorted(paths):
        target = root / relative
        if target.is_file():
            files[relative] = hashlib.sha256(target.read_bytes()).hexdigest()
    return {"files": files,
            "git": {"public": _git_state(root),
                    "personal": _git_state(root, git_dir=git_dir)}}


def _inside(relative, prefixes):
    folded = relative.lower()
    return any(folded == prefix or folded.startswith(prefix + "/") for prefix in prefixes)


# The run-record files the orchestrator itself writes during a Codex builder turn: the
# session copies its hook's decisions file (``codex_rules.DECISIONS_FILE``) into the
# record after each turn. Only these are exempt from the check after the turn; any other
# change to the record, such as the run's saved settings, is reported (code review
# finding R3-1).
TURN_RECORD_WRITES = ("hook-decisions.jsonl",)


def compare_inventory(before, after, edit_paths, run_dir, root=None):
    """Every change between two inventories that a builder turn may not make, as a
    list of lines naming it (empty when there is none).

    A path added, removed or with a changed hash is reported unless it lies inside the
    brief's edit paths. Run records are judged first, whatever the edit paths say: inside
    the folder that holds them, only the current run's ``TURN_RECORD_WRITES`` may change,
    so an edit path that covers this workflow does not cover its records (code review
    finding R5-1, re-raising R3-1). Any change at all in either repository's git state is
    reported: section 3 bans git side effects, so none is ever in scope.
    """
    allowed = [edit.rstrip("/").lower() for edit in edit_paths]
    exact, records = set(), None
    if root is not None:
        try:
            record = (Path(run_dir).resolve().relative_to(Path(root).resolve())
                      .as_posix().lower())
            exact = {f"{record}/{name}" for name in TURN_RECORD_WRITES}
            records = record.rpartition("/")[0] or None
        except ValueError:  # a run record kept outside the project is not in the inventory
            pass
    problems = []
    old, new = before["files"], after["files"]
    for relative in sorted(set(old) | set(new)):
        if old.get(relative) == new.get(relative):
            continue
        what = ("added" if relative not in old
                else "removed" if relative not in new else "changed")
        if records is not None and _inside(relative, [records]):
            if relative.lower() not in exact:
                problems.append(f"{relative} was {what} in a run record")
            continue
        if _inside(relative, allowed):
            continue
        problems.append(f"{relative} was {what} outside the edit paths")
    for repository in ("public", "personal"):
        then, now = before["git"][repository], after["git"][repository]
        for name, label in (("index", "the staged contents"), ("refs", "a ref"),
                            ("head_ref", "the branch HEAD points to"),
                            ("head", "the HEAD commit")):
            if then.get(name) != now.get(name):
                problems.append(f"{label} changed in the {repository} repository")
    return problems


def record_start(run_dir, root, edit_paths, git_dir):
    """Write ``start/``: both commits, and ``git diff HEAD`` of the edit paths with
    any untracked file under them in full. Returns the heads."""
    start = Path(run_dir) / "start"
    start.mkdir(parents=True, exist_ok=True)
    heads = {"public_head": git(root, "rev-parse", "HEAD").strip(),
             "personal_head": git(root, "rev-parse", "HEAD", git_dir=git_dir).strip()}
    specs = _pathspecs(edit_paths)
    diff = git(root, "diff", "HEAD", "--", *specs, *EXCLUDE_SPECS) if specs else ""
    for relative in _lines(git(root, "ls-files", "--others", "--exclude-standard", "--",
                               *specs)) if specs else []:
        if not _skipped(relative):
            diff += _new_file_block(root, relative)
    (start / "heads.json").write_text(json.dumps(heads, indent=2) + "\n",
                                      encoding="utf-8", newline="\n")
    (start / "diff.patch").write_text(diff, encoding="utf-8", newline="\n")
    return heads


def record_round(run_dir, round_no, root, edit_paths, diff, *, category_a):
    """Write ``rounds/R<n>/``: ``files.zip``, every file under the edit paths as the
    reviewer received it, and ``diff.patch``, the diff it was shown.

    The files go into an archive rather than a folder tree. A copied tree under the
    project is live content to every project tool that walks the tree: on the first
    proof run the close-out verifier found the copied tests and ran them, and the
    bytecode they left made the round's record impossible to rewrite. An archive is
    walked by nothing. A round written again (a resumed step) replaces both files.
    """
    folder = Path(run_dir) / "rounds" / f"R{round_no}"
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / "files.zip"
    temporary = folder / "files.zip.tmp"
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for relative in list_files(root, edit_paths, category_a):
            bundle.write(Path(root) / relative, arcname=relative)
    os.replace(temporary, archive)
    (folder / "diff.patch").write_text(diff, encoding="utf-8", newline="\n")
    return folder
