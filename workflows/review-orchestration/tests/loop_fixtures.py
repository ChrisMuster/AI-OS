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
import re
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
planning = _load("planning")

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


def plan_reply(summary="Write done into the work file.", **changes):
    """A planner's reply that passes the schema and the identity check. Its prose uses
    the common words read and write as English, which is no tool form."""
    reply = {"summary": summary,
             "steps": [{"action": "Write the new text into work/a.txt.",
                        "files": ["work/a.txt"],
                        "verification": "Read it back and compare."}],
             "risks": ["None worth naming."], "assumptions": ["The brief is complete."],
             "open_questions": [], "confidence": "high"}
    reply.update(changes)
    return reply


def synthesis_reply(**changes):
    merged = {k: v for k, v in plan_reply(summary="The merged plan.").items()
              if k != "open_questions"}
    reply = {"agreements": ["Both change work/a.txt."], "disagreements": ["Wording."],
             "resolutions": [{"disagreement": "Wording.", "resolution": "Use done.",
                              "reason": "The brief says done."}],
             "retained_risks": ["None."], "merged_plan": merged}
    reply.update(changes)
    return reply


def open_labels_in(prompt):
    """The finding labels a revision prompt lists as open, in order."""
    section = prompt.split("THE OPEN FINDINGS", 1)[-1]
    return re.findall(r"^(R[0-9]+-[0-9]+) ", section, re.MULTILINE)


def revision_reply(action="fixed", **changes):
    """A synthesiser's revision: the synthesis, plus an action for every open finding
    its prompt lists. Returned as a function of the prompt, as the harness takes it."""
    def answer(prompt):
        reply = synthesis_reply(**changes)
        reply["actions"] = [{"label": label, "action": action, "note": "n"}
                            for label in open_labels_in(prompt)]
        return reply
    return answer


def callback_stub(role):
    """A Claude read-only session's callback record as the fakes report it (the A3
    proof run's live checks): one allowed Read, its id naming the session's role."""
    call = {"tool": "Read", "id": f"{role}-call"}
    return {"judged": [{**call, "decision": "allow"}], "tool_uses": [dict(call)],
            "not_judged": [], "rejected_before_hook": [], "not_in_transcript": [],
            "duplicate_ids": [], "undecided": []}


class FakePlanningSession:
    """A read-only planning session (planner, synthesiser or plan reviewer): records its
    prompt and schema in ``harness.planning_calls`` and answers from the harness's
    scripts. A plan reviewer answers ``harness.plan_reviews`` in turn (a list of
    findings each, an empty list when the script is spent, so a review is clean unless a
    test says otherwise); a synthesiser asked for a revision (its schema holds
    ``actions``) answers ``harness.revisions`` in turn, by default every open finding
    ``fixed``. With ``harness.claude_callbacks`` a Claude session also has a callback
    record, ``callback_stub(role)``, returned with its review and held when it fails."""

    def __init__(self, harness, provider, role):
        self.harness, self.provider, self.role = harness, provider, role
        self.model = f"{provider}-{role}-model"
        self.refusals = []
        self.callbacks = (callback_stub(role) if harness.claude_callbacks
                          and provider == limits.CLAUDE else None)
        if self.callbacks is not None:
            self.callback_record = lambda: self.callbacks

    def review(self, prompt, schema):
        h = self.harness
        h.planning_calls.append({"role": self.role, "provider": self.provider,
                                 "prompt": prompt, "schema": schema})
        if self.role == "planner":
            # A plan that names no provider, so the identity check passes it.
            answer = h.planner_replies.get(self.provider) or plan_reply(
                "Plan from one planner." if self.provider == "claude"
                else "Plan from the other planner.")
        elif self.role == "plan_reviewer":
            script = h.plan_reviews.pop(0) if h.plan_reviews else []
            answer = (script if isinstance(script, BaseException)
                      else {"summary": "s", "findings": script})
        elif "actions" in schema.get("properties", {}):
            script = h.revisions.pop(0) if h.revisions else revision_reply()
            answer = script(prompt) if callable(script) else script
        else:
            answer = h.synthesis or synthesis_reply()
        if isinstance(answer, BaseException):
            raise answer
        usage = ({"input_tokens": 10, "output_tokens": 5} if self.provider == "claude"
                 else {"total": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}})
        return providers.ReviewRecord(
            reply=json.loads(json.dumps(answer)),
            thread_id=f"{self.role}-session-{self.provider}-0001", status="completed",
            usage=usage, wall_seconds=0.1, raw={"r": self.provider}, tokens=15,
            model=self.model, callbacks=self.callbacks)


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
        # The A3 proof run's live checks: with this set, every fake Claude read-only
        # session (planner, synthesiser, plan reviewer and reviewer) has a callback
        # record, ``callback_stub`` of its role; unset, none has one, as before.
        self.claude_callbacks = False
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
        # A3 chunk 3: the planning half's fake sessions (``FakePlanningSession``). Each
        # planner gives a clean plan unless ``planner_replies`` names one; the synthesis
        # is ``synthesis`` or a clean one; ``plan_reviews`` and ``revisions`` are
        # scripts taken in turn.
        self.planning_calls = []
        self.planner_replies = {}
        self.synthesis = None
        self.plan_reviews = []
        self.revisions = []
        self.uncaptured = set()

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
                         make_planner=lambda settings, provider, cwd, usage, frozen_dir:
                         FakePlanningSession(self, provider, "planner"),
                         make_synthesiser=lambda settings, cwd, usage, frozen_dir:
                         FakePlanningSession(self, settings["roles"]["builder"],
                                             "synthesiser"),
                         make_plan_reviewer=lambda settings, cwd, usage, frozen_dir:
                         FakePlanningSession(self, settings["roles"]["reviewer"],
                                             "plan_reviewer"),
                         choose_order=lambda items: list(items),
                         uncaptured_category_a=lambda root, git_dir: set(self.uncaptured),
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
        self.callbacks = (callback_stub("reviewer") if harness.claude_callbacks
                          and harness.swapped else None)
        if self.callbacks is not None:
            self.callback_record = lambda: self.callbacks

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
                model=MODEL, refusals=list(self.harness.review_refusals),
                callbacks=self.callbacks)
        usage = {"total": {"total_tokens": 1000}}
        return providers.ReviewRecord(reply={"summary": "s", "findings": answer,
                                             "files_reviewed": listed},
                                      thread_id="t", status="completed", usage=usage,
                                      wall_seconds=0.1, raw={"r": 1},
                                      tokens=providers.codex_tokens(usage),
                                      launch=self.harness.review_launch)


def start_built(brief_arg, item, git_dir, *, allow_paid=False, **kwargs):
    """``loop.start``, then the run taken to its build as ``--approve`` leaves it: the
    build-and-review suites test the build from there.

    Since A3 chunk 3 a start plans first and builds only after approval. These suites
    test the build loop, so the planning half is recorded as done, with a clean plan
    review that used no label (``label_rounds`` 0, so the build's labels start at R1,
    as for a run with no planning half), and the run is approved by the real
    ``loop.approve``, which makes Docker, the image, the run's writes in the project and
    ``approval.json``, as the interim start used to. So that a suite's scripted readings
    and Docker calls line up as they did when the start did all of this itself, the
    approval is given the start's own Codex reading again rather than the next scripted
    one, and the start's Docker status query is left out of ``docker_calls``. The whole
    path (start, plan, review, pause, approve, build) is tested with no such stand-in in
    ``test_plan_review.py`` and ``test_plan_wiring.py``. Returns the Run, or None for a
    dry run."""
    deps = kwargs.get("deps")
    real = deps.read_codex
    harness = getattr(real, "__self__", None)
    seen = []

    def capturing(root, folder=None):
        value = real(root, folder)
        seen.append(value)
        return value
    calls = len(harness.docker_calls) if isinstance(harness, Harness) else None
    deps.read_codex = capturing
    try:
        run = loop.start(brief_arg, item, git_dir, allow_paid=allow_paid, **kwargs)
    finally:
        deps.read_codex = real
    if run is None:
        return None
    if calls is not None:
        del harness.docker_calls[calls:]
    return approve_planned(run, seen[-1], allow_paid=allow_paid)


def approve_planned(run, reading, *, allow_paid=False):
    """A started run's planning half recorded as done with a clean plan review, the run
    paused for approval, then approved with ``reading`` as approval's Codex reading."""
    pause_planned(run, allow_paid=allow_paid)
    real = run.deps.read_codex
    run.deps.read_codex = lambda root, folder=None: reading
    try:
        with redirect_stderr(io.StringIO()):
            loop.approve(run, allow_paid=allow_paid)
    finally:
        run.deps.read_codex = real
    return run


def pause_planned(run, *, allow_paid=False):
    """A started run's planning half recorded as done with a clean plan review that
    used no label, and the run paused ``awaiting-plan-approval``, as the planning half
    leaves it."""
    state = run.state
    if allow_paid:
        # The start's permission was for the planners, which this stand-in does not
        # run; the approval below permits the build instead.
        state.pop("paid_usage_pending", None)
        state["paid_usage"] = [p for p in state.get("paid_usage", [])
                               if p.get("step") != "planners"]
    state["planning"].update({
        "completed": list(planning.STEPS), "reviewed": True,
        "plans": {"claude": plan_reply("A."), "codex": plan_reply("B.")},
        "labels": {"Plan 1": "claude", "Plan 2": "codex"},
        "synthesis": synthesis_reply()})
    sr.enter_pause(state, sr.AWAITING_PLAN_APPROVAL)
    run.save()
    return run


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
        run = start_built("brief.md", item, str(self.personal), root=self.root,
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
