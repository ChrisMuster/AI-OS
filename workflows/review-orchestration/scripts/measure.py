#!/usr/bin/env python3
"""
measure.py - the measurement harness of review orchestration (plan 10.8, sub-stage A5).

It measures the one review step real runs use, through the same engine: the reviewer
role (10.4) on code and the plan reviewer role (10.3 step 4) on a design document. Two
default reviewers (section 11 item 3) are each one full measurement of 12 sessions, run
in this order: the code replay, the plan replay, the seven planted changes, then three
judge sessions on the other provider. One ``--measure`` covers both reviewers and stops
after the first reviewer's set so the user can look at it.

Entered only through the starter (plan section 3), in three forms, which the user adds
to ``start.py`` by hand:

  start.py --measure [--git-dir <dir>]     A fresh measurement. The starter refuses an
                                           uncommitted project, then runs this file from
                                           the live tree, which makes its preflight,
                                           frozen folder and record and hands over to
                                           the frozen folder's trusted copy.
  start.py --measure --resume <id>         Continue a paused or stopped measurement,
                                           from its trusted copy, only at the commit it
                                           started from.
  start.py --measure --score <id>          Score a finished measurement from the user's
                                           rulings, from its trusted copy, and write
                                           the final report.

There is no output argument (the record is ``runs/measure-<id>/``) and no
``--allow-paid``: a stop on paid usage is a usage stop like any other. Run directly,
without the starter's marker, every form prints the starter command and exits 2; the
trusted copy alone accepts the hidden ``--advance <id>`` the hand-over runs.

Exit codes: 0 when a measurement finishes or a score is written; 1 when it pauses or
stops; 2 on a usage error or a form not entered through the starter; 3 when a start,
resume or score is refused, or the trusted copy cannot set itself up.
"""

import argparse
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

REFUSED = 3
ROOT_ENV = "BOOK_DRAGON_ROOT"
FROZEN_PARENT = "book-dragon-orchestration"
_RUN_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
SCRIPT_NAME = "measure.py"


# ------------------------------------------------- run.py's layout, restated
#
# As in run.py, nothing from the project is imported before the gate and, in the
# trusted copy, before the tripwire is armed (plan 7.0 item 3, 7.8). A test checks the
# restated names agree with run.py's.


def trusted_frozen_folder(path=None):
    """The measurement's frozen folder when this file is its trusted copy, at
    ``<x>/book-dragon-orchestration/<id>/trusted/workflows/review-orchestration/
    scripts/measure.py``; otherwise None."""
    path = Path(path) if path is not None else Path(__file__).resolve()
    parts = path.parents
    try:
        layout = (path.name == SCRIPT_NAME and parts[0].name == "scripts"
                  and parts[1].name == "review-orchestration"
                  and parts[2].name == "workflows" and parts[3].name == "trusted"
                  and bool(_RUN_ID.match(parts[4].name))
                  and parts[5].name == FROZEN_PARENT)
    except IndexError:
        return None
    return parts[4] if layout else None


def arm_tripwire(frozen, root=None):
    """Load the frozen folder's tripwire by its path and install it (plan 7.8)."""
    path = Path(frozen) / "tripwire" / "sitecustomize.py"
    spec = importlib.util.spec_from_file_location("_bookdragon_tripwire", path)
    if spec is None or not path.is_file():
        raise RuntimeError(f"the tripwire {path.as_posix()} is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.install(root)


def with_tripwire(environ, frozen):
    """``PYTHONPATH`` with the frozen folder's tripwire folder first (plan 7.8)."""
    wire = str(Path(frozen) / "tripwire")
    rest = [part for part in (environ.get("PYTHONPATH") or "").split(os.pathsep)
            if part and part != wire]
    return os.pathsep.join([wire, *rest])


FIRE_LOG_ENV = "BOOK_DRAGON_FIRE_LOG"
RUNLOGS_PARENT = "book-dragon-runlogs"


def set_up_trusted(frozen, environ=None):
    """The trusted copy's first statements, as run.py's: arm the tripwire, put it first
    on ``PYTHONPATH``, set the fire log, and take the project root from ``hook.json``.
    Returns None, or the reason the copy cannot run."""
    environ = os.environ if environ is None else environ
    try:
        arm_tripwire(frozen)
    except Exception as exc:  # noqa: BLE001 - whatever failed, nothing else loads
        return f"the tripwire could not be armed ({exc})"
    environ["PYTHONPATH"] = with_tripwire(environ, frozen)
    environ[FIRE_LOG_ENV] = str(Path(frozen).parent.parent / RUNLOGS_PARENT
                                / Path(frozen).name / "fire-log.jsonl")
    try:
        config = json.loads((Path(frozen) / "hook.json").read_text(encoding="utf-8"))
        root = Path(config["project_root"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return f"the measurement's hook.json cannot be read ({type(exc).__name__})"
    if not root.is_dir() or not (root / "AGENTS.md").is_file():
        return f"hook.json names {root.as_posix()}, which is not a folder holding AGENTS.md"
    given = environ.get(ROOT_ENV)
    if given is not None and (not given or os.path.normcase(os.path.realpath(given))
                              != os.path.normcase(os.path.realpath(root))):
        return (f"{ROOT_ENV} is {given!r}, but this measurement's hook.json names "
                f"{root.as_posix()}")
    environ[ROOT_ENV] = str(root)
    return None


STARTER_PATH = "workflows/review-orchestration/scripts/start.py"
STARTER = "python " + STARTER_PATH
STARTER_MARKER = "BOOK_DRAGON_REVIEW_ORCHESTRATION_STARTER"
REEXEC_MARKER = "BOOK_DRAGON_REVIEW_ORCHESTRATION_REEXEC"
MEASURE = "--measure"
ADVANCE = "--advance"


def needs_starter(argv, environ=None, frozen=None):
    """The message to print when this file is run without the starter's marker; None
    when the marker is set, or for the trusted copy's own ``--advance``. ``--score``
    and every other form given directly are refused (the user's ruling on R64-2)."""
    environ = os.environ if environ is None else environ
    if environ.get(STARTER_MARKER) == "1":
        return None
    if frozen is not None and list(argv[:1]) == [ADVANCE]:
        return None
    shown = " ".join(argv) if argv else MEASURE
    return ("The measurement harness is run through the starter, which checks the code "
            f"it is about to run is committed: {STARTER} {shown}")


_FROZEN = trusted_frozen_folder()
if __name__ == "__main__":
    _message = needs_starter(sys.argv[1:], frozen=_FROZEN)
    if _message is not None:
        print(_message)
        sys.exit(2)
if _FROZEN is not None:
    sys.dont_write_bytecode = True
    _problem = set_up_trusted(_FROZEN)
    if _problem is not None:
        sys.stderr.write(f"REFUSED: the measurement's trusted copy cannot start: "
                         f"{_problem}\n")
        sys.exit(REFUSED)

_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_DIR))

import brief as brief_mod  # noqa: E402
import checks as checks_mod  # noqa: E402
import container  # noqa: E402
import limits  # noqa: E402
import loop  # noqa: E402
import runrecord  # noqa: E402
import settings as settings_mod  # noqa: E402
import stopreasons as sr  # noqa: E402
import worktree  # noqa: E402

PROJECT_ROOT = brief_mod.PROJECT_ROOT
LOG_PATH = brief_mod.LOG_PATH
WORKFLOW_DIR = _SCRIPTS_DIR.parent
FIXTURES_DIR = WORKFLOW_DIR / "measurement"
KEY_PATH = Path("memory") / "review_orchestration_answer_key.json"
PROGRESS = "progress.json"
PROPOSALS = "proposals.json"
RULINGS = "rulings.md"
REPORT = "report.md"
LOCK = "resume.lock"

# The check image the live check of 2026-11-03 recorded in the decision log (plan 10.8,
# "The check image"); a measurement refuses to start without it and never builds one.
IMAGE_TAG = "bookdragon-checks:211a000e7364"
# The personal repository 10.8 names, the default for --git-dir.
PERSONAL_REPO = "C:/BookDragon/personal-work.git"
# The fixed refs of the planted set (plan 10.8 item 2, "Starting state").
PLANTED_PUBLIC = "65d9c6249eea918afa548fa3c8c60541654c5b10"
PLANTED_PERSONAL = "58e97f604380d4a685fbf76b85d507c7c1812656"
RUNS_TREE = "workflows/review-orchestration/runs/"
# The ``base_blob`` of a planted replace not yet validated against the fixed public ref;
# the pre-session check refuses it by name.
UNVALIDATED_BLOB = "0" * 40
# The two files changed after the planted public ref; no planted patch may touch them.
CHANGED_SINCE_REF = ("workflows/review-orchestration/tests/test_brief.py",
                     "workflows/review-orchestration/tests/CONTEXT.md")

KINDS = ("logic-error", "missed-reference", "stale-document", "scope-breach",
         "metadata-drift", "wrong-test", "control")
CONTROL = "control"
REPLAYS = ("code", "plan")
JUDGES = ("judge-code", "judge-plan", "judge-planted")
# Codex's reviewer runs first, as plan 10.8 "Reviewers" puts Sol first.
REVIEWER_ORDER = (limits.CODEX, limits.CLAUDE)
MAX_ATTEMPTS = 2
TIMESTAMP_SLOT = "{{TIMESTAMP}}"
GIT_NAME = "Biblio measurement"
# A throwaway identity for the copies' baseline commits, never a person's: written in
# two parts only so the personal-data guard does not read it as an email address.
GIT_EMAIL = "@".join(("measurement", "book-dragon.invalid"))

# Statuses of a measurement and of one of its sessions.
RUNNING, PAUSED, STOPPED, FINISHED = "running", "paused", "stopped", "finished"
DONE, NOT_MEASURED, SKIPPED = "done", "not_measured", "skipped"
ERROR, VOID, OK = "error", "void", "ok"


class Refused(Exception):
    """A start, resume or score is refused; nothing was changed."""


class MeasureStop(Exception):
    """The measurement stops, resumably, for a reason that is not an item's failure (an
    untouched copy that fails its checks, say). ``reason`` names the stop."""

    def __init__(self, reason, detail):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class PatchError(Exception):
    """A planted patch does not apply as its fixture says."""


# ------------------------------------------------------------------- boundaries


def pinned_git(*args, hooks=None):
    """A git command for a copy's throwaway repository (process table row 14): no
    template, an empty hook path, identity and signing pinned on the command line, so
    the user's global configuration decides none of them."""
    hooks = hooks or (Path(tempfile.gettempdir()) / "book-dragon-measure-hooks")
    Path(hooks).mkdir(parents=True, exist_ok=True)
    return ["git", "-c", f"core.hooksPath={Path(hooks).as_posix()}",
            "-c", "init.templateDir=", "-c", f"user.name={GIT_NAME}",
            "-c", f"user.email={GIT_EMAIL}", "-c", "commit.gpgsign=false",
            "-c", "tag.gpgsign=false", "-c", "core.autocrlf=false",
            "-c", "core.symlinks=false", "--literal-pathspecs", *args]


class World:
    """Every boundary the harness crosses (git, Docker, usage readings, sessions, the
    checks, the clock), replaceable for tests as ``loop.Deps`` is."""

    def __init__(self, root=PROJECT_ROOT):
        self.root = Path(root)

    # git, read-only (row 14) -------------------------------------------------
    def _git(self, args, cwd=None, git_dir=None, text=True):
        command = ["git"] + ([f"--git-dir={git_dir}"] if git_dir else []) + list(args)
        try:
            done = subprocess.run(command, cwd=str(cwd or self.root), capture_output=True,
                                  timeout=600, env=checks_mod.host_env())
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Refused(f"git {' '.join(args)} could not run: {exc}") from exc
        if done.returncode != 0:
            raise Refused(f"git {' '.join(args)} failed: "
                          + done.stderr.decode("utf-8", "replace").strip())
        return done.stdout.decode("utf-8", "replace") if text else done.stdout

    def head(self):
        return self._git(["rev-parse", "HEAD"]).strip()

    def has_commit(self, ref, git_dir=None):
        try:
            self._git(["cat-file", "-e", f"{ref}^{{commit}}"], git_dir=git_dir)
        except Refused:
            return False
        return True

    def archive(self, ref, git_dir=None):
        return self._git(["archive", "--format=tar", ref], git_dir=git_dir, text=False)

    def show(self, ref, path, git_dir=None):
        return self._git(["show", f"{ref}:{path}"], git_dir=git_dir, text=False)

    def ignored(self, copy, relative):
        done = subprocess.run(["git", "check-ignore", "-q", "--", relative], cwd=str(copy),
                              capture_output=True, timeout=60, env=checks_mod.host_env())
        return done.returncode == 0

    # git in a copy's throwaway repositories (row 14's copy-only commands) ------
    def copy_git(self, cwd, *args, git_dir=None, work_tree=None):
        extra = ([f"--git-dir={git_dir}"] if git_dir else []) + \
                ([f"--work-tree={work_tree}"] if work_tree else [])
        done = subprocess.run(pinned_git(*extra, *args), cwd=str(cwd), capture_output=True,
                              timeout=600, env=checks_mod.host_env())
        if done.returncode != 0:
            raise MeasureStop("error", f"git {' '.join(args)} in a restored copy failed: "
                              + done.stderr.decode("utf-8", "replace").strip())
        return done.stdout.decode("utf-8", "replace")

    # Docker (row 5) -----------------------------------------------------------
    def docker_up(self):
        return container.ensure_running()

    def docker_down(self):
        container.stop_docker()

    def image_exists(self, tag):
        return container.image_exists(tag)

    # usage (rows 13 and 15) ---------------------------------------------------
    def read_codex(self, folder):
        return loop._read_codex_live(self.root, folder)

    def probe_claude(self, model, cwd, on_event):
        import asyncio
        import providers
        return asyncio.run(providers.probe_claude(model, cwd=cwd, on_event=on_event))

    # sessions (rows 21 and 22) ------------------------------------------------
    def session(self, settings, provider, role, cwd, usage, frozen_dir):
        import providers
        return providers.make_read_only(settings, provider, role, cwd=cwd, usage=usage,
                                        frozen_dir=frozen_dir)

    # checks (rows 4, 7 and 23) ------------------------------------------------
    def run_checks(self, copy, edit_paths, directories, commands, **kwargs):
        return checks_mod.run_all(copy, edit_paths, directories, commands, sys.executable,
                                  **kwargs)

    def dead_links(self, copy, trusted):
        code, out, err = checks_mod._trusted_run(copy, sys.executable, trusted,
                                                 "workflows/link-check",
                                                 ["--audit", "--no-log", "--json"])
        try:
            if code != 0:
                raise ValueError(f"exit {code}")
            dead = json.loads(out)["dead_links"]
            return [(d["file"], d["target"]) for d in dead]
        except (ValueError, KeyError, TypeError) as exc:
            raise MeasureStop("error", "the link check on a restored copy did not run: "
                              f"{exc}: {(out + err).strip()[-300:]}") from exc

    def category_a(self, copy):
        """The Category A list with the copy as the working folder, since the sync
        classification reads its plan and runs git from there."""
        before = os.getcwd()
        os.chdir(copy)
        try:
            return worktree.category_a(copy)
        finally:
            os.chdir(before)

    def start_ignored(self, copy):
        return container.start_ignored(copy)

    # the rest -----------------------------------------------------------------
    def now(self):
        return datetime.now().astimezone()

    def scratch(self, prefix):
        return Path(tempfile.mkdtemp(prefix=prefix))

    def remove(self, path):
        container.remove_copy(path)

    def hand_over(self, argv, env, cwd):
        return subprocess.run(argv, cwd=str(cwd), env=env).returncode


# --------------------------------------------------------------- the fixtures


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(f"{Path(path).name} cannot be read: {exc}") from exc


REPLAY_FIELDS = ("title", "public_ref", "personal_ref", "brief", "answer_labels",
                 "review_process", "original_could_run", "drift")


def load_replays(folder=FIXTURES_DIR):
    data = load_json(Path(folder) / "replays.json")
    rounds = data.get("rounds") if isinstance(data, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get("note"), str) or \
            not isinstance(rounds, dict) or set(rounds) != set(REPLAYS):
        raise Refused("replays.json must hold a note and the rounds code and plan")
    for name, entry in rounds.items():
        missing = [field for field in REPLAY_FIELDS if field not in entry]
        if missing:
            raise Refused(f"replays.json: round {name} lacks {', '.join(missing)}")
    code = rounds["code"]
    if len(code.get("new_files", [])) != 8 or len(code.get("required_plans", [])) != 2 \
            or not code.get("plan_slot"):
        raise Refused("replays.json: the code round needs eight new files, two plans "
                      "and its plan_slot text")
    plan = rounds["plan"]
    if not plan.get("plan_path") or not re.fullmatch(
            r"[0-9a-f]{64}", str(plan["brief"].get("sha256"))):
        raise Refused("replays.json: the plan round needs plan_path and its brief's "
                      "SHA-256")
    return data


def is_personal_name(path):
    """A file only the personal repository holds, as the fixtures may name one: every
    LOG.md is gitignored."""
    return Path(path).name == "LOG.md"


def check_fixture(kind, data):
    """Reasons a ``planted-<kind>.json`` is not in its shape; empty when it is."""
    problems = []
    if not isinstance(data, dict) or set(data) != {"brief", "definition", "patch",
                                                   "acceptance"}:
        return [f"planted-{kind}.json must hold exactly brief, definition, patch and "
                "acceptance"]
    if not isinstance(data["brief"], str) or not data["brief"].strip():
        problems.append(f"{kind}: the brief is empty")
    definition = data["definition"]
    if kind == CONTROL:
        if definition is not None:
            problems.append("control: the control has no defect, so no definition")
    elif (not isinstance(definition, dict)
          or set(definition) != {"file", "catch", "check"}
          or not isinstance(definition["file"], str) or not definition["file"]
          or not isinstance(definition["catch"], str) or not definition["catch"].strip()
          or not (definition["check"] is None or (
              isinstance(definition["check"], dict)
              and set(definition["check"]) == {"prefix", "file"}
              and isinstance(definition["check"]["file"], str)
              and str(definition["check"]["prefix"]).endswith(":")))):
        problems.append(f"{kind}: the definition must hold file, catch and check (null, "
                        "or a prefix ending in a colon and a file)")
    if not isinstance(data["acceptance"], list) or not all(
            isinstance(c, str) for c in data["acceptance"]):
        problems.append(f"{kind}: acceptance must be a list of commands")
    ops = data["patch"]
    if not isinstance(ops, list) or not ops:
        return problems + [f"{kind}: the patch is empty"]
    for number, op in enumerate(ops, 1):
        where = f"{kind} operation {number}"
        if not isinstance(op, dict) or op.get("op") not in ("replace", "new", "append"):
            problems.append(f"{where}: op must be replace, new or append")
            continue
        path = op.get("path")
        if not isinstance(path, str) or not path or path.startswith("/") or \
                ".." in path.split("/") or "\\" in path:
            problems.append(f"{where}: the path is not project-relative")
            continue
        if path.startswith(RUNS_TREE):
            problems.append(f"{where}: {path} is under {RUNS_TREE}")
        if path in CHANGED_SINCE_REF:
            problems.append(f"{where}: {path} changed after the planted refs")
        if is_personal_name(path) and op["op"] != "append":
            problems.append(f"{where}: a LOG.md is changed only by an append")
        if op["op"] == "replace":
            if not isinstance(op.get("search"), str) or not op["search"] or \
                    not isinstance(op.get("replace"), str):
                problems.append(f"{where}: a replace needs search and replace text")
            if not re.fullmatch(r"[0-9a-f]{40}", str(op.get("base_blob"))):
                problems.append(f"{where}: a replace needs its target's base_blob")
        elif not isinstance(op.get("text"), str) or not op["text"]:
            problems.append(f"{where}: a {op['op']} needs its text")
        if "defect" in op and not isinstance(op["defect"], str):
            problems.append(f"{where}: defect must be text")
    return problems


def load_planted(folder=FIXTURES_DIR):
    planted, problems = {}, []
    for kind in KINDS:
        data = load_json(Path(folder) / f"planted-{kind}.json")
        problems += check_fixture(kind, data)
        planted[kind] = data
    if problems:
        raise Refused("the planted fixtures are not in their shape: " + "; ".join(problems))
    return planted


def blob_id(data):
    """The git blob id of a file's bytes, as ``git hash-object`` gives it."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def apply_ops(read, ops, timestamp):
    """Apply a planted patch to file contents. ``read(path)`` gives a file's bytes or
    None. Returns ``{path: bytes}``, in the order to write them: every replace and new
    file first, then every append, so an appended LOG.md entry, stamped ``timestamp``,
    is never older than the file it records. A replace whose search text is not found
    exactly once, a new file that exists, an append to a missing file or a defect text
    absent from its result raises ``PatchError``."""
    out = {}

    def current(path):
        return out[path] if path in out else read(path)

    ordered = [op for op in ops if op["op"] != "append"] + \
              [op for op in ops if op["op"] == "append"]
    for op in ordered:
        path = op["path"]
        data = current(path)
        if op["op"] == "new":
            if data is not None:
                raise PatchError(f"{path} already exists, so it is not a new file")
            result = op["text"]
        elif data is None:
            raise PatchError(f"{path} is missing, so the {op['op']} cannot apply")
        else:
            text = data.decode("utf-8")
            if op["op"] == "replace":
                count = text.count(op["search"])
                if count != 1:
                    raise PatchError(f"{path}: the search text occurs {count} times, "
                                     "not once")
                result = text.replace(op["search"], op["replace"])
            else:
                separator = "" if not text or text.endswith("\n") else "\n"
                result = text + separator + op["text"].replace(TIMESTAMP_SLOT, timestamp)
        if op.get("defect") and op["defect"] not in result:
            raise PatchError(f"{path}: the result does not hold its defect text")
        out.pop(path, None)
        out[path] = result.encode("utf-8")
    return out


# ------------------------------------------------------------------ restores


def archive_files(data, label):
    """Every file member of a ``git archive`` tar, refusing a symbolic or hard link and
    a member that would leave the destination. Returns the file paths."""
    files = []
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive.getmembers():
            name = member.name.rstrip("/")
            if member.issym() or member.islnk():
                raise Refused(f"the {label} archive holds a link: {name}")
            parts = name.split("/")
            if name.startswith("/") or ".." in parts or re.match(r"^[A-Za-z]:", name):
                raise Refused(f"the {label} archive holds a path outside the copy: {name}")
            if member.isfile():
                files.append(name)
    return files


def _extract(data, dest, wanted):
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        members = [m for m in archive.getmembers() if m.name.rstrip("/") in wanted]
        archive.extractall(dest, members=members, filter="data")


class Restored:
    """A restored copy: ``path``, its public and personal file lists, the baseline
    commit, and for a planted copy the personal repository beside it."""

    def __init__(self, base, path, public, personal):
        self.base, self.path, self.public, self.personal = base, path, public, personal
        self.baseline = None
        self.personal_git = None
        self.personal_head = None


def restore(world, public_ref, personal_ref, git_dir, *, leave_out_runs=False,
            prefix="book-dragon-measure-"):
    """A restored copy in the system temp folder (plan 10.8, "Every restored copy"): the
    public archive, then the personal archive over it. Refuses a file path in both
    archives (folders both share are expected) and a link in either. ``runs/`` is left
    out of a planted copy. No worktree is made in either repository."""
    public_data = world.archive(public_ref)
    personal_data = world.archive(personal_ref, git_dir=git_dir)
    public = archive_files(public_data, "public")
    personal = archive_files(personal_data, "personal")
    if leave_out_runs:
        public = [p for p in public if not p.startswith(RUNS_TREE)]
        personal = [p for p in personal if not p.startswith(RUNS_TREE)]
    both = sorted(set(public) & set(personal))
    if both:
        raise Refused("a file path is in both archives: " + ", ".join(both[:10]))
    base = world.scratch(prefix)
    path = base / "copy"
    path.mkdir()
    _extract(public_data, path, set(public))
    _extract(personal_data, path, set(personal))
    return Restored(base, path, sorted(public), sorted(personal))


def _pathspec_file(base, name, paths):
    target = Path(base) / name
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("".join(f"{p}\n" for p in paths))
    return target


def baseline(world, restored, leave_out=()):
    """``git init`` in the copy and one baseline commit of its public files, those in
    ``leave_out`` apart, so its history holds that commit alone."""
    left = set(leave_out)
    keep = [p for p in restored.public if p not in left]
    listing = _pathspec_file(restored.base, "public-files.txt", keep)
    world.copy_git(restored.path, "init", "--template=", "-q", ".")
    world.copy_git(restored.path, "add", "-f", f"--pathspec-from-file={listing}")
    world.copy_git(restored.path, "commit", "-q", "--no-verify", "-m",
                   "Measurement baseline")
    restored.baseline = world.copy_git(restored.path, "rev-parse", "HEAD").strip()
    return restored.baseline


def personal_repository(world, restored):
    """A planted copy's second throwaway repository, its git folder beside the copy,
    holding the restored personal files as one baseline commit, so the reviewer's diff
    is ``worktree.review_diff`` with it as the personal start (the user's decision of
    2026-11-08)."""
    folder = restored.base / "personal"
    folder.mkdir()
    world.copy_git(restored.base, "init", "--template=", "-q", str(folder))
    git_dir = folder / ".git"
    listing = _pathspec_file(restored.base, "personal-files.txt", restored.personal)
    world.copy_git(restored.path, "add", "-f", f"--pathspec-from-file={listing}",
                   git_dir=git_dir, work_tree=restored.path)
    world.copy_git(restored.path, "commit", "-q", "--no-verify", "-m",
                   "Measurement personal baseline", git_dir=git_dir,
                   work_tree=restored.path)
    restored.personal_git = git_dir
    restored.personal_head = world.copy_git(restored.path, "rev-parse", "HEAD",
                                            git_dir=git_dir).strip()
    return git_dir


# -------------------------------------------------------------- the answer key


_HEADING = re.compile(r"^(#{1,6})[ \t]+\S")


def load_answer_key(root):
    """The answer key, read from the project folder (never the trusted copy): one JSON
    object with an ``entries`` list, each entry exactly ``label``, ``ref``, ``path``,
    ``section`` and ``text``, all non-empty text. Any other shape is refused."""
    data = load_json(Path(root) / KEY_PATH)
    fields = {"label", "ref", "path", "section", "text"}
    if not isinstance(data, dict) or set(data) != {"entries"} or \
            not isinstance(data["entries"], list) or not data["entries"]:
        raise Refused("the answer key must be one object holding an entries list")
    labels = set()
    for number, entry in enumerate(data["entries"], 1):
        if not isinstance(entry, dict) or set(entry) != fields or not all(
                isinstance(entry[f], str) and entry[f] for f in fields):
            raise Refused(f"answer key entry {number} must hold exactly label, ref, path, "
                          "section and text, each text")
        if entry["label"] in labels:
            raise Refused(f"answer key label {entry['label']} appears twice")
        labels.add(entry["label"])
    return data["entries"]


def sections_under(text, heading):
    """The bodies of every section whose heading line is exactly ``heading`` (a whole
    line, trailing space ignored), each ending at the next heading of the same or a
    higher level."""
    lines = text.splitlines()
    wanted = heading.rstrip()
    level = len(wanted) - len(wanted.lstrip("#"))
    bodies = []
    for index, line in enumerate(lines):
        if line.rstrip() != wanted:
            continue
        body = []
        for later in lines[index + 1:]:
            found = _HEADING.match(later)
            if found and len(found.group(1)) <= level:
                break
            body.append(later)
        bodies.append("\n".join(body))
    return bodies


def contains(text, heading, passage):
    """Containment (the user's decision of 2026-11-08): the packet holds the heading as
    a whole line, and the passage appears word for word in a section under it."""
    return any(passage in body for body in sections_under(text, heading))


def confirm_key(entries, show, git_dir):
    """Every key entry confirmed against its source. ``show(ref, path, git_dir)`` gives
    the packet's bytes. Returns the problems, empty when every entry is contained."""
    problems = []
    for entry in entries:
        try:
            text = show(entry["ref"], entry["path"], git_dir).decode("utf-8")
        except Exception as exc:  # noqa: BLE001 - every failure is the entry's problem
            problems.append(f"{entry['label']}: {entry['path']} at {entry['ref'][:12]} "
                            f"cannot be read ({exc})")
            continue
        if not sections_under(text, entry["section"]):
            problems.append(f"{entry['label']}: no line `{entry['section']}` in "
                            f"{entry['path']}")
        elif not contains(text, entry["section"], entry["text"]):
            problems.append(f"{entry['label']}: its text is not under `{entry['section']}`")
    return problems


# ---------------------------------------------------------------------- the scan


_TAIL_CHAR = re.compile(r"[a-z0-9._-]")
CLAUDE_TOOL_BLOCKS = ("ToolUseBlock", "ServerToolUseBlock")
# The input fields of each Codex tool item (``openai_codex`` ThreadItem types), in both
# spellings; output fields (``aggregated_output``, ``result``) are never read.
CODEX_INPUTS = {
    "commandExecution": ("command", "cwd", "command_actions", "commandActions"),
    "fileChange": ("changes",),
    "mcpToolCall": ("arguments",),
    "dynamicToolCall": ("arguments",),
    "imageView": ("path",),
    "webSearch": ("query", "action"),
}
# Codex tool items that hand work to another agent. The other agent's own tool calls are
# not in this session's record, so the scan cannot see what it read, and a session holding
# one is voided rather than scored (code review R7-1).
CODEX_DELEGATIONS = ("collabAgentToolCall",)


def normal(text):
    """A path as the scan compares it: lower case, every run of slashes of either kind
    one forward slash."""
    return re.sub(r"[\\/]+", "/", str(text)).lower()


def scan_targets(root, git_dir):
    """The two places a measured session must not read: the live project and the
    personal repository."""
    targets = []
    for path in (root, git_dir):
        if path:
            resolved = Path(path).resolve().as_posix() if Path(path).exists() else path
            targets.append(normal(resolved).rstrip("/"))
    return [t for t in targets if t]


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def tool_inputs(raw):
    """Every string in the tool-call inputs of a session's raw record, never its
    outputs: a Claude record's tool-use blocks' ``input``, and a Codex record's tool
    items' input fields."""
    if isinstance(raw, dict):
        if raw.get("_type") in CLAUDE_TOOL_BLOCKS:
            yield from _strings(raw.get("input"))
            return
        fields = CODEX_INPUTS.get(raw.get("type"))
        if fields is not None:
            for field in fields:
                yield from _strings(raw.get(field))
            return
        for value in raw.values():
            yield from tool_inputs(value)
    elif isinstance(raw, (list, tuple)):
        for item in raw:
            yield from tool_inputs(item)


def scan(raw, targets):
    """The tool-call inputs naming a path into one of ``targets``, ignoring case and
    taking either slash; a sibling folder sharing a prefix is not a hit."""
    hits = []
    for text in tool_inputs(raw):
        seen = normal(text)
        for target in targets:
            start = seen.find(target)
            while start != -1:
                end = start + len(target)
                if end == len(seen) or not _TAIL_CHAR.match(seen[end]):
                    hits.append(text if len(text) <= 300 else text[:297] + "...")
                    break
                start = seen.find(target, start + 1)
    return hits


def delegations(raw):
    """Every Codex tool item in a session's raw record that hands work to another agent,
    as ``"<type> <tool> <id>"``."""
    found = []
    if isinstance(raw, dict):
        if raw.get("type") in CODEX_DELEGATIONS:
            found.append(" ".join(str(part) for part in (
                raw.get("type"), raw.get("tool"), raw.get("id")) if part))
            return found
        for value in raw.values():
            found += delegations(value)
    elif isinstance(raw, (list, tuple)):
        for item in raw:
            found += delegations(item)
    return found


# ------------------------------------------------------------ the measurement


def reviewer_label(model):
    """An item label's reviewer part: the model name's last word of letters
    (``gpt-6-sol`` gives ``sol``, ``claude-opus-5-5`` gives ``opus``)."""
    words = [w for w in re.split(r"[^A-Za-z]+", model or "") if w]
    return (words[-1] if words else "reviewer").lower()


def other(provider):
    return limits.CLAUDE if provider == limits.CODEX else limits.CODEX


def check_settings(settings):
    """A measurement needs a reviewer, plan reviewer and judge entry for both
    providers; ordinary runs never ask for the judge."""
    for provider in settings_mod.PROVIDERS:
        for role in ("reviewer", "plan_reviewer", settings_mod.JUDGE):
            if role not in settings["models"].get(provider, {}):
                raise Refused(f"models.{provider}.{role} is required for a measurement; "
                              "add it to the settings file")


# What ``providers.probe_claude`` raises when its one request ends without a successful
# result: the shape a request refused or cut off at a usage limit takes. Its other
# failures (the model not offered, another model started, no init message) are not
# usage readings and stop the measurement as an error.
PROBE_UNREAD = "ended without a successful result"


def probe_failed_unread(exc):
    """Whether a probe failure is the request ending without a successful result."""
    return type(exc).__name__ == "ProviderError" and PROBE_UNREAD in str(exc)


def _now_text(world):
    return world.now().isoformat(timespec="seconds")


def log_entry(action, note, log_path=LOG_PATH):
    return loop._log(action, note, log_path)


class Measurement:
    """One measurement's record and its schedule."""

    def __init__(self, folder, progress, settings, *, root, frozen, world, fixtures,
                 key_entries, log_path=LOG_PATH):
        self.folder = Path(folder)
        self.progress = progress
        self.settings = settings
        self.root = Path(root)
        self.frozen = Path(frozen)
        self.world = world
        self.replays = fixtures["replays"]
        self.planted = fixtures["planted"]
        self.key = {entry["label"]: entry for entry in key_entries}
        self.log_path = log_path

    @property
    def id(self):
        return self.progress["id"]

    def save(self):
        runrecord.write_json_atomic(self.folder / PROGRESS, self.progress)

    def resume_command(self):
        return f"{STARTER} {MEASURE} --resume {self.id}"

    def reviewers(self):
        return self.progress["reviewers"]

    # ------------------------------------------------------------- schedule

    def run(self):
        """Take every session not yet finished, in order: for each reviewer the two
        replays, the seven planted changes and the three judges, pausing after the
        first reviewer's set once. Returns the exit code."""
        for index, reviewer in enumerate(self.reviewers()):
            for name in (*REPLAYS, *KINDS, *JUDGES):
                if not self.session(name, reviewer):
                    return 1
            if index == 0 and not self.progress.get("resumed_from_pause"):
                return self.pause(reviewer)
        return self.finish()

    def pause(self, reviewer):
        self.progress["status"] = PAUSED
        self.progress["paused_after"] = reviewer["label"]
        self.save()
        write_proposals(self)
        log_entry("completed", f"Measurement {self.id} paused after the "
                  f"{reviewer['label']} reviewer's set.", self.log_path)
        print(f"Measurement {self.id} paused after the {reviewer['label']} reviewer's "
              f"set. Its proposals and rulings are in {self.folder.as_posix()}. When "
              f"ready: {self.resume_command()}")
        return 1

    def finish(self):
        self.progress["status"] = FINISHED
        self.save()
        write_proposals(self)
        log_entry("completed", f"Measurement {self.id} finished: every session done or "
                  "not measured.", self.log_path)
        print(f"Measurement {self.id} finished. Rule on each line of "
              f"{(self.folder / RULINGS).as_posix()}, then: {STARTER} {MEASURE} "
              f"--score {self.id}")
        return 0

    def stop(self, reason, detail):
        """End this command, resumably. The harness never waits for a reset."""
        self.progress["status"] = STOPPED
        self.progress["stop"] = {"reason": reason, "detail": detail,
                                 "at": _now_text(self.world)}
        self.save()
        log_entry("failed" if reason == "error" else "completed",
                  f"Measurement {self.id} stopped ({reason}).", self.log_path)
        print(f"Measurement {self.id} stopped ({reason}): {detail}. Once the cause is "
              f"cleared: {self.resume_command()}")

    def failed_before_sessions(self, exc):
        """The trusted copy's checks failed before any session: record the stop and
        close the started entry with a failed one, for a fresh start and a resume alike
        (code review R7-2). The caller re-raises, so the refusal is still printed once."""
        detail = (str(exc) if isinstance(exc, Refused)
                  else f"{type(exc).__name__}: {exc}")
        self.progress["status"] = STOPPED
        self.progress["stop"] = {"reason": "error", "detail": detail,
                                 "at": _now_text(self.world)}
        self.save()
        log_entry("failed", f"Measurement {self.id} stopped before any session: {detail}",
                  self.log_path)

    # ------------------------------------------------------------- one session

    def entry(self, key):
        return self.progress.setdefault("sessions", {}).setdefault(
            key, {"status": None, "attempts": []})

    def session(self, name, reviewer):
        """Run one session to a finished state: done, not measured after its one rerun
        (an error or a void, in either order), or skipped (a judge with nothing to
        judge). Each attempt is saved as it finishes. Returns False when the measurement
        stopped."""
        key = f"{reviewer['label']}/{name}"
        entry = self.entry(key)
        while entry["status"] not in (DONE, NOT_MEASURED, SKIPPED):
            skipped = self.skip_reason(name, reviewer)
            if skipped is not None:
                entry.update(status=SKIPPED, cause=skipped)
                self.save()
                break
            provider = other(reviewer["provider"]) if name in JUDGES \
                else reviewer["provider"]
            try:
                stop = self.before_session(key, provider)
            except Exception as exc:  # noqa: BLE001 - the reading itself failed
                self.stop("error", f"{key}: the usage reading before the session failed: "
                          f"{type(exc).__name__}: {exc}")
                return False
            if stop is not None:
                self.stop("usage-limit", f"{key}: {stop}")
                return False
            usage = limits.ClaudeUsage()
            usage.paid_allowed = False
            usage.on_event = self.note_claude
            holder = {}
            number = len(entry["attempts"]) + 1
            try:
                result = self.take(name, reviewer, usage, holder)
                outcome, cause = OK, None
            except limits.UsageLimitError as exc:
                # A usage stop ends the measurement, not the item: the cut-off session
                # starts afresh on resume, and nothing counts as failed.
                self.save_raw(key, number, holder.get("raw"), suffix="-cut")
                self.stop("usage-limit", f"{key}: {exc}")
                return False
            except MeasureStop as exc:
                self.stop(exc.reason, f"{key}: {exc.detail}")
                return False
            except Exception as exc:  # noqa: BLE001 - an item's failure
                result, outcome, cause = None, ERROR, f"{type(exc).__name__}: {exc}"
            raw = holder.get("raw")
            self.save_raw(key, number, raw)
            hits = scan(raw, self.progress["scan_targets"]) if raw is not None else []
            handed = delegations(raw) if raw is not None else []
            if hits:
                outcome, cause = VOID, "read outside the copy: " + "; ".join(hits[:5])
            elif handed:
                outcome, cause = VOID, ("work handed to another agent, whose reads are not "
                                        "in the record: " + "; ".join(handed[:5]))
            entry["attempts"].append({"outcome": outcome, "cause": cause,
                                      "at": _now_text(self.world)})
            if outcome == OK:
                entry.update(status=DONE, result=result)
            elif len(entry["attempts"]) >= MAX_ATTEMPTS:
                entry.update(status=NOT_MEASURED,
                             causes=[a["cause"] for a in entry["attempts"]])
            self.save()
        return True

    def skip_reason(self, name, reviewer):
        """A judge whose material was not measured is not started."""
        label = reviewer["label"]
        sessions = self.progress.get("sessions", {})
        if name in ("judge-code", "judge-plan"):
            round_ = name.split("-", 1)[1]
            if sessions.get(f"{label}/{round_}", {}).get("status") != DONE:
                return f"the {round_} replay was not measured"
        if name == "judge-planted":
            if not [k for k in KINDS
                    if sessions.get(f"{label}/{k}", {}).get("status") == DONE]:
                return "no planted change was measured"
        return None

    def save_raw(self, key, number, raw, suffix=""):
        if raw is None:
            return
        loop._write_json(self.folder / "transcripts"
                         / f"{key.replace('/', '-')}-{number}{suffix}.json", raw)

    def take(self, name, reviewer, usage, holder):
        if name == "code":
            return self.replay_code(reviewer, usage, holder)
        if name == "plan":
            return self.replay_plan(reviewer, usage, holder)
        if name in KINDS:
            return self.planted_change(name, reviewer, usage, holder)
        return self.judge(name, reviewer, usage, holder)

    # ------------------------------------------------------------- usage

    def note_claude(self, reading):
        self.progress["claude_last_reading"] = loop._reading_record(
            reading, self.world.now().timestamp())
        self.save()

    def before_session(self, key, provider):
        """Fresh readings before every session (plan 10.8, 10.10). Returns None, or why
        the measurement stops before it. Codex is read live every time; a Claude session
        gets the probe unless the last reading is a known overage, and stops when the
        probe received no usable event (R65-1) or reports overage (``probe_on_overage``
        when it is new, R63-1)."""
        ceiling = self.settings["usage_ceiling_percent"]
        world = self.world
        codex = world.read_codex(str(self.frozen))
        record = {"session": key, "provider": provider, "codex": codex.summary()}
        self.progress.setdefault("readings", []).append(record)
        if provider == limits.CODEX:
            self.save()
            stop = limits.check_ceiling([codex], ceiling)
            if stop is not None:
                return f"{stop.reason} ({stop.reading.summary()})"
            start = self.progress.get("codex_start_credits")
            now = loop.credit_snapshot(codex)
            if now is None:
                return "the Codex credit snapshot is missing from the reading"
            unusable = loop.untrackable(start)
            if unusable is not None:
                return ("the Codex credit snapshot read at the start could not be "
                        f"tracked ({unusable})")
            differs = [k for k in ("hasCredits", "unlimited", "balance")
                       if now.get(k) != start.get(k)]
            if differs:
                return ("the Codex credit snapshot changed since the start (" +
                        ", ".join(f"{k} {start.get(k)!r} -> {now[k]!r}" for k in differs)
                        + ")")
            if loop._reached_type(codex) and now.get("hasCredits"):
                return "Codex reports its included allowance reached with credits next"
            return None
        last = loop._effective_last(self.progress, world.now().timestamp(),
                                    user_resume=bool(self.progress.get("plain_resume")))
        if loop._paid_overage(last):
            record["claude"] = last.summary()
            self.save()
            return f"Claude reports paid overage ({last.summary()}); no probe was sent"
        events = []
        try:
            probe = world.probe_claude(self.settings["usage_probe_model"],
                                       str(self.frozen), events.append)
        except Exception as exc:  # noqa: BLE001 - sorted below
            if not probe_failed_unread(exc):
                raise  # a probe that cannot run at all (a model not offered, say)
            # A probe ended without a successful result, as a request refused or cut
            # off at a usage limit does: a usage stop, never an item's failure (R65-1).
            self.progress["plain_resume"] = False
            seen = limits.ClaudeUsage()
            for event in events:
                seen.observe(event)
            reading = seen.reading()
            record["claude"] = reading.summary()
            record["usage_probe"] = {"failed": f"{type(exc).__name__}: {exc}",
                                     "probe_event": bool(events)}
            if events:
                self.progress["claude_last_reading"] = loop._reading_record(
                    reading, world.now().timestamp())
                if loop._paid_overage(reading):
                    record["usage_probe"]["probe_on_overage"] = True
                    self.progress.setdefault("probe_on_overage", []).append(
                        {"session": key, "reading": reading.summary(), "tokens": None,
                         "at": _now_text(world)})
            else:
                record["claude_unavailable"] = (
                    f"the probe ended without a successful result and no event: {exc}")
            self.save()
            return ("the Claude probe ended without a successful result"
                    + ("; Claude's usage is unavailable" if not events else
                       f" ({reading.summary()})") + f" ({exc})")
        self.progress["plain_resume"] = False
        reading = probe.reading
        record["claude"] = reading.summary()
        record["usage_probe"] = {"tokens": probe.tokens, "model": probe.model,
                                 "probe_event": bool(probe.events)}
        if probe.events:
            self.progress["claude_last_reading"] = loop._reading_record(
                reading, world.now().timestamp())
        if not probe.events or reading.source == limits.UNAVAILABLE:
            record["claude_unavailable"] = reading.reason or "no event"
            self.save()
            return ("the Claude probe received no usable event, so Claude's usage is "
                    f"unavailable ({reading.reason or 'no event'})")
        if loop._paid_overage(reading):
            record["usage_probe"]["probe_on_overage"] = True
            self.progress.setdefault("probe_on_overage", []).append(
                {"session": key, "reading": reading.summary(), "tokens": probe.tokens,
                 "at": _now_text(world)})
        self.save()
        stop = limits.check_ceiling([reading], ceiling)
        if stop is not None:
            return f"{stop.reason} ({reading.summary()})"
        return None

    # ------------------------------------------------------------- sessions

    def make(self, provider, role, cwd, usage):
        return self.world.session(self.settings, provider, role, cwd, usage,
                                  str(self.frozen))

    @staticmethod
    def _keep(holder):
        def called(record):
            holder["raw"] = record.raw
        return called

    def replay_code(self, reviewer, usage, holder):
        """The code round (plan 10.8 item 1): its eight files new in the diff and the
        changed-file list, its two plans the specification and required in
        ``files_reviewed``, no mechanical results and no prior findings."""
        spec = self.replays["rounds"]["code"]
        restored = restore(self.world, spec["public_ref"], spec["personal_ref"],
                           self.progress["git_dir"])
        try:
            baseline(self.world, restored, leave_out=spec["new_files"])
            brief_text = (restored.path / spec["brief"]["path"]).read_text(
                encoding="utf-8")
            diff = worktree.review_diff(restored.path, spec["new_files"],
                                        restored.baseline, category_a=set())
            plans = spec["required_plans"]
            session = self.make(reviewer["provider"], "reviewer", restored.path, usage)
            values = dict(
                round=1, brief=self.replays["note"] + "\n\n" + brief_text,
                plan=spec["plan_slot"], mechanical="None.", prior="None.",
                diff=diff or "(empty)",
                changed_files="\n".join(f"- {p}" for p in spec["new_files"]),
                check_written="None.",
                reviewer_tools=loop.reviewer_tools(reviewer["provider"]))
            record, findings = loop.review_step(
                session, values, ledger=sr.Ledger(), state=self.progress, save=self.save,
                changed_files=spec["new_files"], required=plans,
                after_call=self._keep(holder))
            return {"findings": findings, "summary": record.reply.get("summary"),
                    "tokens": record.tokens}
        finally:
            self.world.remove(restored.base)

    def replay_plan(self, reviewer, usage, holder):
        """The design-document round: the plan as restored in the plan slot, the brief
        from its personal ref, checked by SHA-256, and no prior findings."""
        spec = self.replays["rounds"]["plan"]
        git_dir = self.progress["git_dir"]
        brief_bytes = self.world.show(spec["brief"]["ref"], spec["brief"]["path"], git_dir)
        if hashlib.sha256(brief_bytes).hexdigest() != spec["brief"]["sha256"]:
            raise MeasureStop("error", "the plan round's brief does not have its recorded "
                              "SHA-256")
        restored = restore(self.world, spec["public_ref"], spec["personal_ref"], git_dir)
        try:
            baseline(self.world, restored)
            plan_text = (restored.path / spec["plan_path"]).read_text(encoding="utf-8")
            session = self.make(reviewer["provider"], "plan_reviewer", restored.path,
                                usage)
            values = dict(round=1, brief=self.replays["note"] + "\n\n"
                          + brief_bytes.decode("utf-8"), plan=plan_text, prior="None.")
            record, findings = loop.plan_review_step(
                session, values, ledger=sr.Ledger(), state=self.progress, save=self.save,
                after_call=self._keep(holder))
            return {"findings": findings, "summary": record.reply.get("summary"),
                    "tokens": record.tokens}
        finally:
            self.world.remove(restored.base)

    def planted_change(self, kind, reviewer, usage, holder):
        """One planted change in 10.8's order: placeholders, the checks on the untouched
        copy (any failure stops the measurement), the patch, the checks again, then one
        review step with their results, ``NO_PLAN`` and an empty prior slot."""
        fixture = self.planted[kind]
        restored = restore(self.world, PLANTED_PUBLIC, PLANTED_PERSONAL,
                           self.progress["git_dir"], leave_out_runs=True)
        try:
            baseline(self.world, restored)
            personal_repository(self.world, restored)
            copy = restored.path
            placeholders = self.placeholders(copy)
            text = fixture["brief"]
            edit_paths = brief_mod.brief_edit_paths(text)
            common = dict(run_id=f"measure-{self.id}-{reviewer['label']}-{kind}",
                          frozen_dir=str(self.frozen), public_head=restored.baseline,
                          image=IMAGE_TAG, floor=self.world.start_ignored(copy))
            rounds = self.folder / "rounds" / f"{reviewer['label']}-{kind}"
            found, untouched = self.world.run_checks(
                copy, edit_paths, [], fixture["acceptance"],
                round_dir=rounds / "untouched", **common)
            if found:
                raise MeasureStop("untouched-copy", f"{kind}: the untouched copy fails its "
                                  "checks, so a later failure could not be put down to "
                                  "the defect: " + "; ".join(f["title"] for f in found[:5]))
            category_a = self.world.category_a(copy)
            before = worktree.snapshot(copy, edit_paths, category_a)
            stamp = (self.world.now() + timedelta(seconds=1)).replace(microsecond=0)
            changed = apply_ops(lambda p: _read(copy / p), fixture["patch"],
                                stamp.isoformat(timespec="seconds"))
            for path, data in changed.items():
                (copy / path).parent.mkdir(parents=True, exist_ok=True)
                (copy / path).write_bytes(data)
            category_a = self.world.category_a(copy)
            after_patch = worktree.contents(copy, edit_paths, category_a)
            changed_files = worktree.changed(before, loop._hashes(after_patch))
            mechanical, mechanical_text = self.world.run_checks(
                copy, edit_paths, worktree.changed_dirs(changed_files),
                fixture["acceptance"], round_dir=rounds / "patched", **common)
            after_checks = worktree.contents(copy, edit_paths, category_a)
            written = worktree.delta(after_patch, after_checks, "the checks")
            diff = worktree.review_diff(copy, edit_paths, restored.baseline,
                                        restored.personal_head, restored.personal_git,
                                        category_a=category_a)
            session = self.make(reviewer["provider"], "reviewer", copy, usage)
            values = dict(round=1, brief=text, plan=loop.NO_PLAN,
                          mechanical=mechanical_text or "None.", prior="",
                          diff=diff or "(empty)",
                          changed_files="\n".join(f"- {p}" for p in changed_files)
                          or "None.", check_written=written or "None.",
                          reviewer_tools=loop.reviewer_tools(reviewer["provider"]))
            record, findings = loop.review_step(
                session, values, ledger=sr.Ledger(), state=self.progress, save=self.save,
                changed_files=changed_files, after_call=self._keep(holder))
            return {"findings": findings, "mechanical": mechanical,
                    "mechanical_text": mechanical_text, "placeholders": placeholders,
                    "changed_files": changed_files,
                    "summary": record.reply.get("summary"), "tokens": record.tokens}
        finally:
            self.world.remove(restored.base)

    def placeholders(self, copy):
        """The link check on the untouched copy (process table row 23): an empty
        placeholder for every dead link whose target git ignores, then the check again,
        which must be clean. A dead link to a tracked file gets none, so it stays dead
        and stops the measurement as an untouched-copy failure, named in the stop."""
        trusted = self.frozen / checks_mod.TRUSTED_DIR
        made = []
        for _file, target in self.world.dead_links(copy, trusted):
            relative = target + ".md"
            if self.world.ignored(copy, relative):
                path = copy / relative
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"")
                    made.append(relative)
        again = self.world.dead_links(copy, trusted)
        if again:
            raise MeasureStop("untouched-copy", "the untouched copy's link check is not "
                              "clean after its placeholders: " + "; ".join(
                                  f"[[{t}]] in {f}" for f, t in again[:5]))
        return made

    def judge(self, name, reviewer, usage, holder):
        """One judge session (plan 10.8 item 3) on the provider that did not produce the
        findings, read-only in its own fresh, empty temporary folder, not told which AI
        produced them."""
        material = judge_material(self, name, reviewer)
        folder = self.world.scratch("book-dragon-judge-")
        try:
            session = self.make(other(reviewer["provider"]), settings_mod.JUDGE, folder,
                                usage)
            record = session.review(material["prompt"], sr.JUDGE_SCHEMA)
            holder["raw"] = record.raw
            parsed = sr.parse_judge_reply(record.reply, material["items"],
                                          material["findings"], material["control"])
            return {"judgement": parsed, "tokens": record.tokens}
        finally:
            self.world.remove(folder)


def _read(path):
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        return None


# -------------------------------------------------------------- judge material


REPLAY_TEXT = dict(
    what="one round of a design or code review; the round's material is not shown, "
         "only what was expected of the review and what Reviewer A found",
    items_note="Each is a finding the round's original reviewer recorded, quoted as it "
               "was written, with the packet heading it sat under.",
    match_rule="",
    false_alarm_rule="This review had no control, so use `false alarm` for no finding.")
PLANTED_TEXT = dict(
    what="seven small changes, each against its own short brief: six hold one planted "
         "defect each, described by its item, and one, the control, holds none",
    items_note="Each describes one planted defect: its change, its file and what a "
               "finding must identify to catch it.",
    match_rule="A finding catches only an item of its own change: a finding's id begins "
               "with its change's name.",
    false_alarm_rule="Every finding made on the control is `false alarm`, whatever it "
                     "says, since the control holds no defect, and no other finding is.")


def _finding_text(ident, finding):
    return (f"- {ident} [{finding.get('severity')}] {finding.get('title')} "
            f"(file: {finding.get('file')})\n  Evidence: {finding.get('evidence')}\n"
            f"  Fix: {finding.get('fix')}")


def judge_material(measurement, name, reviewer):
    """The judge's prompt and the ids it must answer for. A planted change that was not
    measured is left out; a defect a mechanical check caught is credited to the check,
    so it is not put to the judge."""
    label = reviewer["label"]
    sessions = measurement.progress["sessions"]
    if name in ("judge-code", "judge-plan"):
        round_ = name.split("-", 1)[1]
        spec = measurement.replays["rounds"][round_]
        found = sessions[f"{label}/{round_}"]["result"]["findings"]
        items = {lab: None for lab in spec["answer_labels"]}
        findings = [f"N{n}" for n in range(1, len(found) + 1)]
        item_text = "\n".join(
            f"- {lab} (under `{measurement.key[lab]['section']}`):\n"
            + "\n".join("  " + line for line in measurement.key[lab]["text"].splitlines())
            for lab in spec["answer_labels"])
        finding_text = "\n".join(_finding_text(i, f) for i, f in zip(findings, found)) \
            or "None."
        prompt = loop.render("judge", items=item_text, findings=finding_text,
                             **REPLAY_TEXT)
        return {"prompt": prompt, "items": items, "findings": findings, "control": []}
    items, findings, control, item_lines, finding_lines = {}, [], [], [], []
    for kind in KINDS:
        entry = sessions.get(f"{label}/{kind}", {})
        if entry.get("status") != DONE:
            continue
        result = entry["result"]
        ids = [f"{kind}/N{n}" for n in range(1, len(result["findings"]) + 1)]
        findings += ids
        if kind == CONTROL:
            control += ids
        definition = measurement.planted[kind]["definition"]
        if definition is not None and not mechanical_catch(definition,
                                                           result["mechanical"]):
            items[kind] = ids
            item_lines.append(f"- {kind}: in `{definition['file']}`. To catch it a "
                              f"finding must identify: {definition['catch']}")
        finding_lines.append(f"### Change {kind}\nIts brief:\n"
                             + measurement.planted[kind]["brief"].strip() + "\nFindings:\n"
                             + ("\n".join(_finding_text(i, f) for i, f in
                                          zip(ids, result["findings"])) or "None."))
    prompt = loop.render("judge", items="\n".join(item_lines) or "None.",
                         findings="\n\n".join(finding_lines), **PLANTED_TEXT)
    return {"prompt": prompt, "items": items, "findings": findings, "control": control}


def mechanical_catch(definition, mechanical):
    """The named check on the named file (plan 10.8 item 3): a mechanical finding whose
    title begins with the definition's prefix and whose ``file`` is its file."""
    check = (definition or {}).get("check")
    if not check:
        return None
    for finding in mechanical or ():
        if (str(finding.get("title", "")).startswith(check["prefix"])
                and str(finding.get("file", "")).strip().rstrip("/")
                == check["file"].rstrip("/")):
            return finding
    return None


# ------------------------------------------------------------------ proposals


RULING_VALUES = {
    "recorded": ("caught", "missed"),
    "definition": ("caught", "missed"),
    "new": ("plausible", "not plausible"),
    "control": ("false alarm", "not a false alarm"),
    "unexpected": ("caught", "not a catch"),
}


def proposals(measurement):
    """Every proposal of the sessions finished so far, the mechanical catches, and the
    items not measured, which get no proposal."""
    out, not_measured, mechanical = [], [], []
    sessions = measurement.progress.get("sessions", {})
    for reviewer in measurement.reviewers():
        label = reviewer["label"]
        for round_ in REPLAYS:
            spec = measurement.replays["rounds"][round_]
            review = sessions.get(f"{label}/{round_}", {})
            judged = sessions.get(f"{label}/judge-{round_}", {})
            if review.get("status") == NOT_MEASURED or judged.get("status") in (
                    NOT_MEASURED, SKIPPED):
                cause = review.get("causes") or judged.get("causes") or \
                    [judged.get("cause")]
                not_measured += [{"item": f"{label}/{lab}", "cause": cause}
                                 for lab in spec["answer_labels"]]
                continue
            if judged.get("status") != DONE:
                continue
            verdict = judged["result"]["judgement"]
            for lab in spec["answer_labels"]:
                ruled = verdict["items"][lab]
                out.append({"item": f"{label}/{lab}", "kind": "recorded",
                            "reviewer": label, "round": round_,
                            "review_process": lab in spec["review_process"],
                            "proposal": ruled["verdict"], "match": ruled["match"],
                            "why": ruled["why"]})
            found = review["result"]["findings"]
            for ident, ruled in verdict["findings"].items():
                out.append({"item": f"{label}/{round_}/{ident}", "kind": "new",
                            "reviewer": label, "round": round_,
                            "proposal": ruled["verdict"], "why": ruled["why"],
                            "title": found[int(ident[1:]) - 1].get("title")})
        judged = sessions.get(f"{label}/judge-planted", {})
        for kind in KINDS:
            entry = sessions.get(f"{label}/{kind}", {})
            definition = measurement.planted[kind]["definition"]
            if entry.get("status") == NOT_MEASURED:
                not_measured.append({"item": f"{label}/{kind}",
                                     "cause": entry.get("causes")})
                continue
            if entry.get("status") != DONE:
                continue
            result = entry["result"]
            caught = mechanical_catch(definition, result["mechanical"])
            if caught is not None:
                mechanical.append({"item": f"{label}/{kind}", "check": caught["title"],
                                   "file": caught["file"]})
            if kind != CONTROL:
                unexpected = [f for f in result["mechanical"] if f is not caught]
                for n, finding in enumerate(unexpected, 1):
                    out.append({"item": f"{label}/{kind}/unexpected-{n}",
                                "kind": "unexpected", "reviewer": label, "change": kind,
                                "proposal": "unexpected", "title": finding.get("title"),
                                "file": finding.get("file")})
            if judged.get("status") in (NOT_MEASURED, SKIPPED):
                cause = judged.get("causes") or [judged.get("cause")]
                if definition is not None and caught is None:
                    not_measured.append({"item": f"{label}/{kind}", "cause": cause})
                not_measured += [{"item": f"{label}/{kind}/N{n}", "cause": cause}
                                 for n in range(1, len(result["findings"]) + 1)]
                continue
            if judged.get("status") != DONE:
                continue
            verdict = judged["result"]["judgement"]
            if definition is not None and caught is None:
                ruled = verdict["items"][kind]
                out.append({"item": f"{label}/{kind}", "kind": "definition",
                            "reviewer": label, "change": kind,
                            "proposal": ruled["verdict"], "match": ruled["match"],
                            "why": ruled["why"]})
            for n, finding in enumerate(result["findings"], 1):
                ident = f"{kind}/N{n}"
                if ident not in verdict["findings"]:
                    continue  # given as a match
                ruled = verdict["findings"][ident]
                out.append({"item": f"{label}/{ident}",
                            "kind": "control" if kind == CONTROL else "new",
                            "reviewer": label, "change": kind,
                            "proposal": ruled["verdict"], "why": ruled["why"],
                            "title": finding.get("title")})
    return {"proposals": out, "not_measured": not_measured, "mechanical": mechanical}


def ruling_line(proposal):
    """One rulings line, prefilled with the proposal (an ``unexpected`` one left for
    the user to rule), with the judge's match and reason, or the check's title and
    file, indented beneath."""
    ruling = "" if proposal["kind"] == "unexpected" else proposal["proposal"]
    lines = [f"- {proposal['item']} | proposal: {proposal['proposal']} | ruling: "
             f"{ruling} | note:"]
    for field in ("title", "file", "match", "why"):
        value = proposal.get(field)
        if value:
            lines.append(f"    {field}: " + " ".join(str(value).split()))
    return "\n".join(lines)


_LINE = re.compile(r"^- (\S+) \| proposal: ([^|]*?) \| ruling: ([^|]*?) \| note:(.*)$")
_ITEM = re.compile(r"^- (\S+) \|")


def write_proposals(measurement):
    """``proposals.json`` and the rulings file, written atomically. Lines already in the
    rulings file are kept exactly as they are, so a ruling the user edited at the pause
    survives the second reviewer's merge; only new items are added."""
    found = proposals(measurement)
    runrecord.write_json_atomic(measurement.folder / PROPOSALS, found)
    path = measurement.folder / RULINGS
    existing = path.read_text(encoding="utf-8") if path.exists() else (
        f"# Rulings for measurement {measurement.id}\n\n"
        "Change `ruling:` on any line you disagree with; text after `note:` is free. "
        "Indented lines are ignored by scoring.\n")
    have = {m.group(1) for m in map(_ITEM.match, existing.splitlines()) if m}
    new = [ruling_line(p) for p in found["proposals"] if p["item"] not in have]
    text = existing.rstrip("\n") + "\n" + ("\n" + "\n".join(new) + "\n" if new else "")
    runrecord.write_text_atomic(path, text)
    return found


# ---------------------------------------------------------------------- scoring


def read_rulings(text, found):
    """The user's rulings, by item. Raises ``Refused`` naming every unreadable line by
    number, every unruled, duplicate or unmatched item, and every ruling not allowed
    for its proposal."""
    by_item = {p["item"]: p for p in found["proposals"]}
    problems, rulings, mentioned = [], {}, set()
    for number, line in enumerate(text.splitlines(), 1):
        if not line.startswith("- "):
            continue
        match = _LINE.match(line)
        if match is None:
            problems.append(f"line {number} cannot be read")
            continue
        item, ruling = match.group(1), match.group(3).strip()
        if item not in by_item:
            problems.append(f"line {number}: {item} matches no proposal")
            continue
        mentioned.add(item)
        if item in rulings:
            problems.append(f"line {number}: {item} is ruled a second time")
            continue
        if not ruling:
            problems.append(f"line {number}: {item} has no ruling")
            continue
        allowed = RULING_VALUES[by_item[item]["kind"]]
        if ruling not in allowed:
            problems.append(f"line {number}: {item}'s ruling must be one of "
                            + ", ".join(allowed))
            continue
        rulings[item] = ruling
    missing = [item for item in by_item if item not in mentioned]
    if missing:
        problems.append("no ruling for: " + ", ".join(missing[:20]))
    if problems:
        raise Refused("the rulings cannot be scored: " + "; ".join(problems))
    return rulings


def control_failures(measurement):
    out = []
    for reviewer in measurement.reviewers():
        entry = measurement.progress["sessions"].get(f"{reviewer['label']}/{CONTROL}", {})
        if entry.get("status") == DONE and entry["result"]["mechanical"]:
            out.append(reviewer["label"])
    return out


def score(measurement):
    """Write the final report from the proposals and the user's rulings (plan 10.8, "The
    score"). Refuses until the measurement has finished, while the control has a check
    failure, and on any line the rulings file cannot give. Rerunnable; it writes only
    the report."""
    progress = measurement.progress
    if progress.get("status") != FINISHED:
        raise Refused(f"measurement {measurement.id} is not finished "
                      f"({progress.get('status')}); continue it first: "
                      f"{measurement.resume_command()}")
    failing = control_failures(measurement)
    if failing:
        raise Refused("the control has a check failure for " + ", ".join(failing)
                      + ", so the fixtures are wrong and no score counts")
    found = proposals(measurement)
    path = measurement.folder / RULINGS
    if not path.exists():
        raise Refused(f"there is no rulings file at {path.as_posix()}")
    rulings = read_rulings(path.read_text(encoding="utf-8"), found)
    text = report(measurement, found, rulings)
    runrecord.write_text_atomic(measurement.folder / REPORT, text)
    return text


def session_counts(progress):
    """How many sessions failed and how many were voided, counted apart."""
    failed = voided = 0
    for entry in progress.get("sessions", {}).values():
        for attempt in entry.get("attempts", []):
            failed += attempt["outcome"] == ERROR
            voided += attempt["outcome"] == VOID
    return failed, voided


def report(measurement, found, rulings):
    """The final report: every element of plan 10.8 item 4."""
    progress = measurement.progress
    props = found["proposals"]
    lines = [f"# Measurement {measurement.id}", "",
             f"Commit: {progress['commit']}. Reviewers, in order: " + ", ".join(
                 f"{r['label']} ({r['provider']}, {r['model']})"
                 for r in measurement.reviewers()) + ".", "", "## Replays", ""]
    for reviewer in measurement.reviewers():
        label = reviewer["label"]
        totals = {"caught": 0, "missed": 0, "plausible": 0, "not plausible": 0}
        for round_ in REPLAYS:
            spec = measurement.replays["rounds"][round_]
            counts = {"caught": 0, "missed": 0, "plausible": 0, "not plausible": 0}
            process = []
            for p in props:
                if p["reviewer"] != label or p.get("round") != round_:
                    continue
                ruling = rulings[p["item"]]
                if p["kind"] == "recorded" and p["review_process"]:
                    process.append(f"{p['item']} {ruling}")
                    continue
                counts[ruling] = counts.get(ruling, 0) + 1
            for key in totals:
                totals[key] += counts[key]
            lines += [f"- {label}, {spec['title']}: caught {counts['caught']}, missed "
                      f"{counts['missed']}; new findings plausible {counts['plausible']}, "
                      f"not plausible {counts['not plausible']}.",
                      f"  - What the original reviewer could run: "
                      f"{spec['original_could_run']}",
                      f"  - Recorded drift: {spec['drift']}",
                      "  - Review-process findings, reported apart: "
                      + ("; ".join(process) if process else "none scored")]
        lines.append(f"- {label}, both rounds: caught {totals['caught']}, missed "
                     f"{totals['missed']}; new findings plausible {totals['plausible']}, "
                     f"not plausible {totals['not plausible']}.")
    lines += ["", "## Planted set", "",
              "Each kind has one sample, so each line is a single reading, not a rate. "
              "The scope breach is measured without the intent check (plan 10.6), whose "
              "judgement also looks for unasked additions in a real run.", ""]
    mech = {m["item"]: m for m in found["mechanical"]}
    absent = {n["item"] for n in found["not_measured"]}
    for reviewer in measurement.reviewers():
        label = reviewer["label"]
        for kind in KINDS:
            item = f"{label}/{kind}"
            if kind == CONTROL:
                alarms = [p for p in props if p["reviewer"] == label and
                          p.get("change") == CONTROL and rulings[p["item"]] == "false alarm"]
                state = "not measured" if item in absent else \
                    f"{len(alarms)} false alarm(s)"
            elif item in mech:
                state = f"mechanical catch ({mech[item]['check']})"
            elif any(p["item"].startswith(item + "/unexpected-")
                     and rulings[p["item"]] == "caught" for p in props):
                state = "mechanical catch (an unexpected check failure ruled caught)"
            elif item in rulings:
                state = "reviewer catch" if rulings[item] == "caught" else "missed"
            else:
                state = "not measured"
            lines.append(f"- {item}: {state}")
    not_a_catch = [p["item"] for p in props if p["kind"] == "unexpected"
                   and rulings[p["item"]] == "not a catch"]
    lines += ["", "Unexpected check failures ruled not a catch: "
              + (", ".join(not_a_catch) or "none") + ".", "", "## Placeholders", ""]
    for key, entry in sorted(progress.get("sessions", {}).items()):
        made = (entry.get("result") or {}).get("placeholders")
        if made is not None:
            lines.append(f"- {key}: " + (", ".join(made) or "none"))
    failed, voided = session_counts(progress)
    lines += ["", "## Sessions", "",
              f"Failed sessions: {failed}. Voided sessions: {voided}.",
              "Paid probe requests (probe_on_overage): " + (
                  "; ".join(f"{p['session']}: {p['reading']}"
                            for p in progress.get("probe_on_overage", [])) or "none") + ".",
              "", "## Not measured", ""]
    lines += [f"- {n['item']}: " + "; ".join(str(c) for c in (n["cause"] or []))
              for n in found["not_measured"]] or ["None."]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------- the commands


def preflight(root, world, git_dir, fixtures_dir=FIXTURES_DIR):
    """Every check before anything is created: the settings, the answer key's shape
    and containment, the fixtures, and every ref. Returns what a measurement needs."""
    settings = settings_mod.load_settings()
    check_settings(settings)
    entries = load_answer_key(root)
    replays = load_replays(fixtures_dir)
    planted = load_planted(fixtures_dir)
    if not Path(git_dir).is_dir():
        raise Refused(f"the personal repository {git_dir} does not exist")
    wanted = {lab for r in replays["rounds"].values() for lab in r["answer_labels"]}
    missing = sorted(wanted - {e["label"] for e in entries})
    if missing:
        raise Refused("the answer key lacks " + ", ".join(missing))
    problems = confirm_key(entries, world.show, git_dir)
    if problems:
        raise Refused("the answer key does not match its sources: " + "; ".join(problems))
    refs = [(PLANTED_PUBLIC, None), (PLANTED_PERSONAL, git_dir),
            (replays["rounds"]["plan"]["brief"]["ref"], git_dir)]
    for spec in replays["rounds"].values():
        refs += [(spec["public_ref"], None), (spec["personal_ref"], git_dir)]
    absent = [ref[:12] for ref, where in refs if not world.has_commit(ref, where)]
    if absent:
        raise Refused("these commits cannot be read: " + ", ".join(absent))
    return {"settings": settings, "entries": entries, "replays": replays,
            "planted": planted}


def unvalidated(planted):
    """Every replace operation still holding the placeholder ``base_blob``, as
    ``"<kind>: <path>"``."""
    return [f"{kind}: {op['path']}" for kind, fixture in planted.items()
            for op in fixture["patch"]
            if op["op"] == "replace" and op.get("base_blob") == UNVALIDATED_BLOB]


def check_planted(world, git_dir, planted):
    """The pre-session check: one restore of the planted refs, every replace target's
    blob id equal to its ``base_blob``, every appended file present, and every patch
    applied to a throwaway copy of its files. Refuses on any mismatch, and first on any
    ``base_blob`` still the unvalidated placeholder, before any reading, probe or
    session."""
    placeholders = unvalidated(planted)
    if placeholders:
        raise Refused("a planted fixture has not been validated against the fixed "
                      f"public ref {PLANTED_PUBLIC[:12]}: its base_blob is still the "
                      "placeholder for " + ", ".join(sorted(set(placeholders)))
                      + "; write each target's blob id there (git rev-parse "
                      f"{PLANTED_PUBLIC[:12]}:<path>) and check the patch applies")
    restored = restore(world, PLANTED_PUBLIC, PLANTED_PERSONAL, git_dir,
                       leave_out_runs=True)
    try:
        public, personal = set(restored.public), set(restored.personal)
        for kind, fixture in planted.items():
            for op in fixture["patch"]:
                path = op["path"]
                if op["op"] == "replace":
                    data = _read(restored.path / path)
                    if path not in public:
                        raise Refused(f"{kind}: {path} is not a public file at the ref")
                    if data is None or blob_id(data) != op["base_blob"]:
                        raise Refused(f"{kind}: {path} is not the blob its fixture names "
                                      f"({op['base_blob'][:12]})")
                elif op["op"] == "new" and (path in public or path in personal):
                    raise Refused(f"{kind}: {path} already exists at the ref")
                elif op["op"] == "append" and path not in personal:
                    raise Refused(f"{kind}: {path}, the personal file it appends to, is "
                                  "missing at the personal ref")
            try:
                apply_ops(lambda p: _read(restored.path / p), fixture["patch"],
                          "2026-01-01T00:00:00+00:00")
            except (PatchError, UnicodeDecodeError) as exc:
                raise Refused(f"{kind}: its patch does not apply: {exc}") from exc
    finally:
        world.remove(restored.base)


def claim(folder):
    """Claim the measurement for this command (``resume.lock``, created exclusively)."""
    lock = Path(folder) / LOCK
    try:
        with open(lock, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{os.getpid()}\n")
    except FileExistsError as exc:
        raise Refused(f"{lock.as_posix()} exists: another command holds this "
                      "measurement, or one crashed; once none is running, delete the "
                      "file") from exc
    return lock


def release(lock):
    try:
        Path(lock).unlink()
    except OSError:
        pass


def start(root, world, git_dir, *, runs_dir=None, fixtures_dir=FIXTURES_DIR,
          log_path=LOG_PATH, temp_root=None, setup=None):
    """A fresh measurement, in the live tree the starter found committed: the preflight,
    the frozen folder, the record and ``progress.json``. Returns ``(id, frozen)``."""
    ready = preflight(root, world, git_dir, fixtures_dir)
    measure_id = runrecord.new_run_id()
    head = world.head()
    if setup is None:
        import frozen as frozen_mod
        setup = frozen_mod.setup
    folder = setup(root, measure_id, public_head=head, builder="none",
                   temp_root=temp_root)
    record = runrecord.create_measure(measure_id, runs_dir=runs_dir or runrecord.RUNS_DIR)
    settings = ready["settings"]
    reviewers = []
    for provider in REVIEWER_ORDER:
        model = settings_mod.model_for(settings, provider, "reviewer")["model"]
        reviewers.append({"label": reviewer_label(model), "provider": provider,
                          "model": model})
    progress = {"id": measure_id, "commit": head, "git_dir": str(git_dir),
                "frozen_dir": str(folder), "created": _now_text(world),
                "status": RUNNING, "stop": None, "reviewers": reviewers,
                "scan_targets": scan_targets(root, git_dir), "sessions": {},
                "codex_start_credits": None, "credits_read": False}
    runrecord.write_json_atomic(record / PROGRESS, progress)
    log_entry("started", f"Measurement {measure_id} started.", log_path)
    return measure_id, folder


def load_measurement(measure_id, root, world, *, runs_dir=None, fixtures_dir=FIXTURES_DIR,
                     log_path=LOG_PATH):
    folder = runrecord.measure_dir(measure_id, runs_dir=runs_dir or runrecord.RUNS_DIR)
    if not (folder / PROGRESS).is_file():
        raise Refused(f"there is no measurement {measure_id}")
    progress = load_json(folder / PROGRESS)
    fixtures = {"replays": load_replays(fixtures_dir), "planted": load_planted(fixtures_dir)}
    return Measurement(folder, progress, settings_mod.load_settings(), root=root,
                       frozen=progress["frozen_dir"], world=world, fixtures=fixtures,
                       key_entries=load_answer_key(root), log_path=log_path)


def advance(measurement, *, resumed):
    """The trusted copy's work: the image check before anything else, the answer key
    and the planted patches confirmed, the starting credit reading taken once (the one
    figure carried across a restart), then the sessions. Returns the exit code."""
    world, progress = measurement.world, measurement.progress
    started = False
    try:
        try:
            # Inside the logged checks, so Docker failing to start is closed with a failed
            # entry like any other failure before a session (code review R8-1).
            started = world.docker_up()
            if not world.image_exists(IMAGE_TAG):
                raise Refused(f"the check image {IMAGE_TAG} is missing; a measurement "
                              "never builds one: restore it, or record a new tag by hand")
            problems = confirm_key(list(measurement.key.values()), world.show,
                                   progress["git_dir"])
            if problems:
                raise Refused("the answer key does not match its sources: "
                              + "; ".join(problems))
            check_planted(world, progress["git_dir"], measurement.planted)
            if not progress.get("credits_read"):
                reading = world.read_codex(str(measurement.frozen))
                progress["codex_start_credits"] = loop.credit_snapshot(reading)
                progress["credits_read"] = True
        except Exception as exc:  # noqa: BLE001 - logged, then raised as before
            measurement.failed_before_sessions(exc)
            raise
        if resumed and progress.get("status") == PAUSED:
            progress["resumed_from_pause"] = True
        progress["status"] = RUNNING
        progress["stop"] = None
        measurement.save()
        try:
            return measurement.run()
        except Exception as exc:  # noqa: BLE001 - never leave it marked running
            measurement.stop("error", f"{type(exc).__name__}: {exc}")
            return 1
    finally:
        if started:
            world.docker_down()


def resume(measurement):
    """``--measure --resume``: only at the recorded commit, only a paused or stopped
    measurement."""
    progress = measurement.progress
    head = measurement.world.head()
    if head != progress["commit"]:
        raise Refused(f"measurement {measurement.id} started at commit "
                      f"{progress['commit']}, but the project is at {head}; return to "
                      f"{progress['commit']} to resume it, or start a new measurement")
    if progress.get("status") not in (PAUSED, STOPPED):
        raise Refused(f"measurement {measurement.id} is {progress.get('status')}; only a "
                      "paused or stopped measurement resumes")
    progress["plain_resume"] = True
    log_entry("started", f"Measurement {measurement.id} resumed.", measurement.log_path)
    return advance(measurement, resumed=True)


def hand_over(measure_id, root, world, frozen, *, runs_dir=None, environ=None,
              python=None, arm=None):
    """The fresh measurement's last act in the live tree: arm the tripwire, then run the
    frozen folder's trusted ``measure.py --advance <id>``. A child that ends leaving the
    measurement marked running is recorded as stopped ``error``."""
    environ = os.environ if environ is None else environ
    script = Path(frozen).joinpath("trusted", "workflows", "review-orchestration",
                                   "scripts", SCRIPT_NAME)
    env = {k: v for k, v in environ.items() if k != STARTER_MARKER}
    env.update({ROOT_ENV: str(root), REEXEC_MARKER: "1", "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": with_tripwire(environ, frozen)})
    try:
        (arm or arm_tripwire)(frozen, root)
        code = world.hand_over([str(python or sys.executable), str(script), ADVANCE,
                                measure_id], env, root)
        problem = f"the trusted copy exited {code} without ending its work"
    except Exception as exc:  # noqa: BLE001
        code, problem = 1, f"the trusted copy could not be started ({exc})"
    folder = runrecord.measure_dir(measure_id, runs_dir=runs_dir or runrecord.RUNS_DIR)
    progress = load_json(folder / PROGRESS)
    if progress.get("status") == RUNNING:
        progress.update(status=STOPPED, stop={"reason": "error", "detail": problem})
        runrecord.write_json_atomic(folder / PROGRESS, progress)
        print(f"Measurement {measure_id} stopped: {problem}. Once fixed: {STARTER} "
              f"{MEASURE} --resume {measure_id}")
        return code if code not in (None, 0) else 1
    return code


def main(argv=None, *, root=None, world=None, frozen=_FROZEN, runs_dir=None,
         fixtures_dir=FIXTURES_DIR, log_path=LOG_PATH, environ=None, handover=None):
    """``handover`` is what a fresh measurement is passed to; the command line passes
    ``hand_over``."""
    brief_mod._reconfigure_streams()
    environ = os.environ if environ is None else environ
    argv = list(sys.argv[1:] if argv is None else argv)
    message = needs_starter(argv, environ, frozen)
    if message is not None:
        print(message)
        return 2
    root = Path(root or PROJECT_ROOT)
    world = world or World(root)
    parser = argparse.ArgumentParser(prog="measure.py",
                                     description="The review-orchestration measurement.")
    parser.add_argument(MEASURE, action="store_true", help="a measurement")
    parser.add_argument("--resume", metavar="ID", help="continue a measurement")
    parser.add_argument("--score", metavar="ID", help="score a finished measurement")
    parser.add_argument("--git-dir", default=PERSONAL_REPO,
                        help="the personal repository (a fresh measurement only)")
    parser.add_argument(ADVANCE, metavar="ID", help=argparse.SUPPRESS)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    common = dict(runs_dir=runs_dir, fixtures_dir=fixtures_dir, log_path=log_path)
    try:
        if args.advance:
            if frozen is None or args.advance != Path(frozen).name or args.measure:
                print("--advance is the trusted copy's own mode")
                return 2
            measurement = load_measurement(args.advance, root, world, **common)
            lock = claim(measurement.folder)
            try:
                return advance(measurement, resumed=False)
            finally:
                release(lock)
        if not args.measure:
            print("the first argument must be --measure")
            return 2
        if args.resume and args.score:
            print("give --resume or --score, not both")
            return 2
        if args.resume or args.score:
            measure_id = args.resume or args.score
            if frozen is None or measure_id != Path(frozen).name:
                raise Refused(f"--resume and --score run only from measurement "
                              f"{measure_id}'s trusted copy, through the starter")
            measurement = load_measurement(measure_id, root, world, **common)
            lock = claim(measurement.folder)
            try:
                if args.resume:
                    return resume(measurement)
                score(measurement)
                print(f"Report written: {(measurement.folder / REPORT).as_posix()}")
                return 0
            finally:
                release(lock)
        if frozen is not None:
            raise Refused("a fresh measurement starts from the project, through the "
                          "starter, never from a trusted copy")
        measure_id, folder = start(root, world, args.git_dir, **common)
        if handover is None:
            return 0
        return handover(measure_id, root, world, folder, runs_dir=runs_dir)
    except Refused as exc:
        print(f"REFUSED: {exc}")
        return REFUSED


if __name__ == "__main__":
    sys.exit(main(handover=hand_over))
