#!/usr/bin/env python3
"""The shared fixtures of the build-and-review suites: the loaded modules, the fixture
project, the fake sessions, ``Harness``, ``LoopCase`` and ``SwappedCase``.

This is not a suite. Its name does not begin ``test_``, so the close-out verifier does
not run it on its own, and it holds no test. The loop suites (the ``test_loop*.py``
files), ``test_approval.py`` and the planning suites import it, so every suite takes its
fixtures from this one place (plan 10.3, the chunk list, the split of the old single
loop file before A3 chunk 2's tests were added).

No fixture makes a model call. Whole runs are driven through ``loop.Deps`` with a fake
builder session, a fake reviewer session, scripted usage readings and scripted
mechanical checks, in a temporary project that is a real git repository with a second
repository standing in for the personal one.

    import loop_fixtures            (the tests folder is on sys.path when a suite runs)
"""

import asyncio
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

WORKFLOW = Path(__file__).resolve().parent.parent
SCRIPTS = WORKFLOW / "scripts"
PROJECT = WORKFLOW.parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


brief = _load("brief")
limits = _load("limits")
settings_mod = _load("settings")
approver = _load("approver")
codex_rules = _load("codex_rules")
stopreasons = _load("stopreasons")
providers = _load("providers")
runrecord = _load("runrecord")
packet = _load("packet")
worktree = _load("worktree")
container = _load("container")  # checks imports container
checks = _load("checks")
frozen = _load("frozen")
loop = _load("loop")
run_py = _load("run")

sr = stopreasons
MODEL = "claude-opus-5-5"
# The fixture's Category A: its hand-written gitignored files. A gitignored file not
# named here (a *.generated file) is Category B or C, never part of a run's work.
CAT_A = {"memory/note.md", "memory/other.md", "memory/new.md"}
CREDITS ={"hasCredits": True, "unlimited": False, "balance": "5.00"}

BRIEF = """\
## Goal
- work/a.txt says done.

## Constraints
- Touch nothing else.

## Out of scope
- None.

## Acceptance checks
- python -c "print(1)"

## Edit paths
- work/ | Measure: git ls-files work

## Open questions
None

## Source
- `work/`
"""


_spec = importlib.util.spec_from_file_location(
    "fixture_git", Path(__file__).resolve().parent / "fixture_git.py")
fixture_git = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture_git)
FixtureGitError = fixture_git.FixtureGitError


def git(root, *args, git_dir=None):
    """Run a git command for a test's fixture, through the suites' one fixture runner
    (`fixture_git.py`: git's own text on a failure, code review R18-1; an object-write
    refusal retried, R22)."""
    command = ["git"]
    if git_dir:
        command += [f"--git-dir={git_dir}", "--work-tree=."]
    fixture_git.run_git(command + list(args), root, shown=f"git {' '.join(args)}")


def make_project(test):
    """A temporary project: a git repository with work/a.txt committed, a brief,
    memory/, and a second repository standing in for the personal one."""
    folder = tempfile.TemporaryDirectory()
    test.addCleanup(folder.cleanup)
    base = Path(folder.name).resolve()
    root = base / "project"
    (root / "work").mkdir(parents=True)
    (root / "memory").mkdir()
    (root / "work" / "a.txt").write_bytes(b"start\n")
    (root / ".gitignore").write_bytes(b"memory/\nruns/\n__pycache__/\n*.generated\n")
    (root / "memory" / "note.md").write_bytes(b"personal\n")
    (root / "brief.md").write_bytes(BRIEF.encode("utf-8"))
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "t")
    git(root, "add", ".gitignore", "work/a.txt", "brief.md")
    git(root, "commit", "-qm", "start")
    personal = base / "personal.git"
    git(base, "init", "-q", "--bare", str(personal))
    git(root, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "add", "-f",
        "memory/note.md", git_dir=str(personal))
    git(root, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qm",
        "capture", git_dir=str(personal))
    return root, personal


def reading(percent=30, credits=CREDITS, source="reported", reached=None, resets=None):
    snapshot = {}
    if credits is not None:
        snapshot["credits"] = dict(credits)
    if reached:
        snapshot["rateLimitReachedType"] = reached
    return limits.Reading(limits.CODEX, source, percent=percent, resets_at=resets,
                          window="secondary", raw={"rateLimits": snapshot},
                          reason=None if source == "reported" else "x")


def edit(root, text):
    """A builder action that writes work/a.txt and replies."""
    def act(prompt):
        (root / "work" / "a.txt").write_bytes(text.encode("utf-8"))
        return "Changed work/a.txt."
    return act


def reply(text):
    return lambda prompt: text


def actions(*pairs):
    body = json.dumps([{"label": label, "action": action, "note": "n"}
                       for label, action in pairs])
    return f"Done.\n```json\n{body}\n```"


def finding(title="A defect", reraises=""):
    return {"title": title, "severity": "major", "file": "work/a.txt",
            "evidence": "seen", "fix": "fix it", "reraises": reraises}


class FakeUsage:
    """Stands in for the run's one limits.ClaudeUsage: its reading is whatever the
    harness holds at that moment, so a session can set it mid-run as an event would."""

    def __init__(self, harness):
        self.harness = harness
        self.actual = limits.ClaudeUsage()

    def reading(self):
        return self.harness.claude_usage or limits.Reading(
            limits.CLAUDE, limits.UNAVAILABLE, reason="no event")

    def observe(self, event):
        self.actual.observe(event)
        self.harness.claude_usage = self.actual.reading()
        if self.on_event is not None:
            self.on_event(self.harness.claude_usage)


class Harness:
    """Scripts for both sessions, the readings and the checks, shared by every
    session object the loop creates."""

    def __init__(self, root):
        self.root = root
        self.builder_script = []
        self.reviews = []
        self.readings = []
        self.mechanical = []
        self.builders = []
        self.review_calls = []
        self.claude_usage = None
        self.probe_calls = []
        self.probe_readings = []
        self.probe_events = []
        self.probe_error = None
        self.slept = []
        self.clock = 1_000_000
        self.baseline = {"clean": True, "status": "pass", "problems": []}
        self.start_check_writes = None
        self.files_reviewed = None
        self.check_writes = None
        # Chunk (d): the reverse pairing. With ``swapped`` the fake builder is a Codex
        # one (it needs the check after every turn and has a frozen folder) and the
        # fake reviewer a Claude one.
        self.swapped = False
        self.usage = FakeUsage(self)
        self.given = []
        self.reviewer_frozen = []
        self.reading_folders = []
        self.review_launch = None  # a Codex reviewer's launch readings, if a test sets
        self.cat_a = set(CAT_A)
        self.frozen = None
        self.frozen_calls = []
        self.frozen_error = None
        self.frozen_parent = None
        self.on_frozen = None
        self.builder_refusals = []
        self.review_refusals = []
        self.during_review = None
        self.inventories = 0
        # A Codex builder's thread totals, one per turn, as the real one reports a
        # running total (the chunk (d) short plan, 11.3); 700 each when not given.
        self.codex_totals = []
        # A3 chunk 1: a fake Codex builder's whole usage per turn, where a test needs its
        # input, cached and output counts; {"total": {"totalTokens": <total>}} if not.
        self.codex_usages = []
        # What a fake Codex builder reports as its launch record (11.2), per launch.
        self.launch_records = []
        # Decision 39: what a fake Claude builder reports as each turn's callback
        # record, one per turn; None gives the fake no callback_record at all.
        self.callback_records = None
        # Orchestrator isolation S6b: a fake Docker and image step. No test starts or
        # stops the real Docker. ``docker_calls`` records every Docker action in order.
        self.docker_up = True
        self.docker_calls = []
        self.docker_start_error = None
        self.docker_stop_error = None
        self.docker_status_error = None
        self.constraints_missing = False
        self.reports_at_stop = []
        self.image_tag = "bookdragon-checks:0123456789ab"
        self.image_exists = True
        self.image_error = None
        self.image_calls = []
        self.start_check_calls = []
        self.removed = []
        self.check_calls = []
        self.check_error = None

    def start_check(self, root, python, read_only=False):
        """The start-of-run close-out check: clean unless a test says otherwise."""
        self.start_check_calls.append({"read_only": read_only})
        if self.start_check_writes:
            self.start_check_writes()
        return self.baseline

    def docker_running(self):
        self.docker_calls.append("status")
        if self.docker_status_error is not None:
            raise self.docker_status_error
        return self.docker_up

    def ensure_docker(self):
        self.docker_calls.append("status")
        if self.docker_up:
            return False
        self.docker_calls.append("start")
        if self.docker_start_error is not None:
            raise self.docker_start_error
        self.docker_up = True
        return True

    def stop_docker(self):
        self.docker_calls.append("stop")
        # Plan 7.3's order, report first (code review R30-3): whether a run's report
        # already existed at the moment Docker was stopped.
        self.reports_at_stop.append(bool(list(Path(self.root).glob("runs/*/report.md"))))
        if self.docker_stop_error is not None:
            raise self.docker_stop_error
        self.docker_up = False

    def prepare_image(self, root, python, work_dir, *, dry_run=False):
        """Stands in for container.prepare_image (its own tests are in
        test_container.py): writes the constraints file into the given folder, as the
        real one does, and records whether the run folder existed at that moment."""
        constraints = Path(work_dir) / "constraints.txt"
        constraints.write_bytes(b"pkg==1.0\n")
        self.image_calls.append({"work_dir": Path(work_dir), "dry_run": dry_run,
                                 "runs_existed": [p.name for p in Path(root).glob("runs/*")]})
        if self.image_error is not None:
            raise self.image_error
        if dry_run:
            self.docker_calls.append("status")
            if not self.docker_up:
                return {"tag": self.image_tag, "would": "unknown: Docker is not running"}
            self.docker_calls.append("inspect")
            return {"tag": self.image_tag, "would": "reuse" if self.image_exists else "build"}
        self.docker_calls.append("inspect")
        built = not self.image_exists
        if built:
            self.docker_calls.append("build")
            self.image_exists = True
        if self.constraints_missing:  # the copy after the run record will fail
            constraints = Path(work_dir) / "gone.txt"
        return {"tag": self.image_tag, "built": built, "constraints": constraints}

    def deps(self):
        return loop.Deps(make_builder=self.make_builder, make_reviewer=self.make_reviewer,
                         probe_claude=self.probe_claude,
                         read_codex=self.read_codex, run_checks=self.run_checks,
                         baseline_checks=self.start_check,
                         category_a=lambda root: set(self.cat_a),
                         capture_problems=lambda root, git_dir: [],
                         inventory=self.inventory, claude_usage=lambda: self.usage,
                         setup_frozen=self.setup_frozen,
                         docker_running=self.docker_running,
                         ensure_docker=self.ensure_docker, stop_docker=self.stop_docker,
                         prepare_image=self.prepare_image,
                         remove_frozen=lambda folder: self.removed.append(
                             ("frozen", Path(folder).as_posix())),
                         remove_copies=lambda run_id: self.removed.append(
                             ("copies", run_id)),
                         sleep=self.slept.append, now=lambda: self.clock)

    def probe_claude(self, model, cwd, on_event):
        self.probe_calls.append((model, cwd))
        events = self.probe_events.pop(0) if self.probe_events else []
        for event in events:
            on_event(event)
        if self.probe_error is not None:
            raise self.probe_error
        value = (self.probe_readings.pop(0) if self.probe_readings else
                 self.claude_usage or limits.Reading(
                     limits.CLAUDE, limits.UNAVAILABLE, reason="no event"))
        return SimpleNamespace(events=events, reading=value, tokens=1, usage={}, model=model)

    def setup_frozen(self, root, run_id, *, public_head, builder):
        """Stands in for frozen.setup (its own tests are in test_frozen.py): records
        what the loop asked for and gives the swapped builder's folder, or a fresh one
        beside the project. Since A3 the edit paths and the image tag are not setup's:
        the start writes them to approval.json straight after. A reused folder is
        given back as a fresh one would be, without an earlier run's approval.json."""
        self.frozen_calls.append({"root": root, "run_id": run_id, "public_head": public_head,
                                  "builder": builder})
        if self.frozen_error is not None:
            raise self.frozen_error
        if self.frozen is not None:
            (Path(self.frozen) / codex_rules.APPROVAL_FILE).unlink(missing_ok=True)
            return self.frozen
        folder = (self.frozen_parent or Path(root).parent / "frozen") / run_id
        folder.mkdir(parents=True)
        if self.on_frozen is not None:
            self.on_frozen(folder, root, run_id, builder, public_head)
        return folder

    def inventory(self, root, git_dir, category_a):
        self.inventories += 1
        return worktree.inventory(root, git_dir, category_a)

    def make_builder(self, settings, approver_, cwd, resume, run_dir, usage):
        self.given.append(("builder", run_dir, usage))
        builder = FakeBuilder(self, resume)
        self.builders.append(builder)
        return builder

    def make_reviewer(self, settings, cwd, usage, frozen_dir):
        self.given.append(("reviewer", None, usage))
        self.reviewer_frozen.append(frozen_dir)
        return FakeReviewer(self)

    def read_codex(self, root, folder=None):
        # Plan 7.6 item 4: which folder each usage reading was given (None before the
        # frozen folder exists).
        self.reading_folders.append(folder)
        return self.readings.pop(0) if self.readings else reading()

    def run_checks(self, root, edit_paths, directories, commands, python=None, *, run_id,
                   frozen_dir, public_head, image, round_dir):
        self.check_calls.append({"run_id": run_id, "frozen_dir": frozen_dir,
                                 "public_head": public_head, "image": image,
                                 "round_dir": Path(round_dir)})
        if self.check_error is not None:
            raise self.check_error
        if self.check_writes:
            self.check_writes()  # a check that writes a file, as close-out writes its log
        found = self.mechanical.pop(0) if self.mechanical else []
        return found, f"checked {', '.join(directories) or 'nothing'}"


class FakeBuilder:

    def __init__(self, harness, resume):
        self.harness = harness
        self.resume = resume
        self.session_id = resume
        self.prompts = []
        self.last_messages = []
        self.closed = False
        self.provider = limits.CODEX if harness.swapped else limits.CLAUDE
        self.needs_turn_check = harness.swapped
        self.frozen_dir = harness.frozen if harness.swapped else None
        self.refusals = []
        self.launch_record = None
        if harness.callback_records is not None:
            self.callback_record = lambda: harness.callback_records.pop(0)

    async def start(self):
        if self.harness.swapped and self.harness.launch_records:
            self.launch_record = self.harness.launch_records.pop(0)

    async def turn(self, name, prompt):
        self.prompts.append((name, prompt))
        self.last_messages = [{"_type": "SystemMessage", "data": {"session_id": "s-1"}}]
        try:
            text = self.harness.builder_script.pop(0)(prompt)
        except Exception:
            # A real Codex builder keeps what its hook refused when a turn fails.
            self.refusals = list(self.harness.builder_refusals)
            raise
        self.session_id = "s-1"
        if self.harness.swapped:
            total = self.harness.codex_totals.pop(0) if self.harness.codex_totals else 700
            usage = (self.harness.codex_usages.pop(0) if self.harness.codex_usages
                     else {"total": {"totalTokens": total}})
            return providers.TurnRecord(
                name=name, final_text=text, session_id="s-1", model="gpt-6-sol",
                num_turns=None, is_error=False, usage=usage,
                cost_usd_equivalent=None, wall_seconds=0.1, messages=[{"n": 1}],
                tokens=total, refusals=list(self.harness.builder_refusals))
        usage = {"input_tokens": 100, "output_tokens": 10}
        return providers.TurnRecord(name=name, final_text=text, session_id="s-1",
                                    model=MODEL, num_turns=1, is_error=False,
                                    usage=usage, cost_usd_equivalent=0.01,
                                    wall_seconds=0.1, messages=[{"n": 1}],
                                    tokens=providers.claude_tokens(usage))

    async def close(self):
        self.closed = True


class FakeReviewer:

    def __init__(self, harness):
        self.harness = harness
        self.provider = limits.CLAUDE if harness.swapped else limits.CODEX
        self.model = MODEL if harness.swapped else "gpt-6-sol"
        self.refusals = []

    def review(self, prompt, schema):
        self.harness.review_calls.append((prompt, schema))
        if self.harness.during_review:
            self.harness.during_review()
        answer = self.harness.reviews.pop(0)
        if isinstance(answer, Exception):
            self.refusals = list(self.harness.review_refusals)
            raise answer
        # A compliant reviewer lists every changed file the prompt names, unless the
        # test says otherwise.
        section = prompt.split("THE CHANGED FILES", 1)[1].split("FILES THE ORCHESTRATOR")[0]
        listed = [line[2:] for line in section.splitlines() if line.startswith("- ")]
        if self.harness.files_reviewed is not None:
            listed = self.harness.files_reviewed
        if self.harness.swapped:
            usage = {"input_tokens": 400, "output_tokens": 50}
            return providers.ReviewRecord(
                reply={"summary": "s", "findings": answer, "files_reviewed": listed},
                thread_id="claude-session", status="completed", usage=usage,
                wall_seconds=0.1, raw={"r": 1}, tokens=providers.claude_tokens(usage),
                model=MODEL, refusals=list(self.harness.review_refusals))
        usage = {"total": {"total_tokens": 1000}}
        return providers.ReviewRecord(reply={"summary": "s", "findings": answer,
                                             "files_reviewed": listed},
                                      thread_id="t", status="completed", usage=usage,
                                      wall_seconds=0.1, raw={"r": 1},
                                      tokens=providers.codex_tokens(usage),
                                      launch=self.harness.review_launch)


class LoopCase(unittest.TestCase):

    def setUp(self):
        self.root, self.personal = make_project(self)
        self.h = Harness(self.root)
        self.runs = self.root / "runs"
        self.log = self.root / "LOG.md"
        # A run's resolved settings carry its roles; these tests build with Claude unless
        # a test names the builder (the chunk (d) short plan, 11.1).
        self.settings = settings_mod.assign_roles(settings_mod.load_settings(), "claude")

    def start(self, cap=3, inject=None, wait=False, item="demo"):
        settings = json.loads(json.dumps(self.settings))
        settings["round_caps"]["build_review"] = cap
        run = loop.start("brief.md", item, str(self.personal), root=self.root,
                         deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                         inject=inject, wait=wait, settings=settings,
                         builder="codex" if self.h.swapped else "claude")
        asyncio.run(run.advance())
        return run

    def resume(self, run, rounds=None):
        again = loop.load(run.state["run_id"], root=self.root, deps=self.h.deps(),
                          log_path=self.log, runs_dir=self.runs)
        loop.resume(again, rounds=rounds)
        asyncio.run(again.advance())
        return again

    def reason(self, run):
        return run.state["stop_reasons"][-1]["reason"]

    def reset_work(self):
        """Put the edit paths back to the commit, as committing a run's work would,
        so the next run's preflight finds them clean."""
        git(self.root, "checkout", "--", "work")

    def log_actions(self):
        return [line.split("| Action: ")[1].split(" |")[0]
                for line in self.log.read_text(encoding="utf-8").splitlines()]


def make_trusted(folder, root, run_id, builder, public_head):
    """A frozen folder as frozen.setup makes it, with trusted/ copied from this working
    tree rather than git-archived (frozen.setup's own archive is tested in
    test_frozen.py): the orchestrator's scripts, config and prompts, and the sync
    classification's scripts."""
    # What git archive would leave out: caches, run records and the gitignored logs.
    skip = shutil.ignore_patterns("__pycache__", "runs", "LOG.md")
    workflow = Path(folder) / "trusted" / "workflows" / "review-orchestration"
    for sub in ("scripts", "config", "prompts"):
        shutil.copytree(WORKFLOW / sub, workflow / sub, ignore=skip)
    shutil.copytree(PROJECT / "workflows" / "sync-architecture" / "scripts",
                    Path(folder) / "trusted" / "workflows" / "sync-architecture" / "scripts",
                    ignore=skip)
    frozen.populate(folder, workflow / "scripts", workflow / "config",
                    frozen.hook_config(run_id, root, builder, public_head))


def loop_refused():
    return run_py.REFUSED


class SwappedCase(LoopCase):
    """Whole runs with Codex building and Claude reviewing (chunk (d))."""

    def setUp(self):
        super().setUp()
        self.h.swapped = True
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.h.frozen = Path(folder.name).resolve() / "frozen"
        self.h.frozen.mkdir()
        (self.h.frozen / "codex_hook.py").write_bytes(b"the frozen rules\n")
        (self.h.frozen / "hook-decisions.jsonl").write_bytes(b"")

    def evidence(self, run):
        return json.dumps(run.state["stop_reasons"][-1]["evidence"])
