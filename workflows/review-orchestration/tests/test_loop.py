#!/usr/bin/env python3
"""Hermetic tests for the build-and-review loop (loop.py) and its parts: the review
packet (packet.py), the brief reader (brief.read_brief), the working-tree records
(worktree.py) and the mechanical checks (checks.py).

No test makes a model call. Whole runs are driven through ``loop.Deps`` with a fake
builder session, a fake reviewer session, scripted usage readings and scripted
mechanical checks, in a temporary project that is a real git repository with a
second repository standing in for the personal one. What is under test is the loop:
the order of steps, the stop reasons, the usage and credit checks before each step,
the clean stop and resume, the records it writes, and what each role is shown.

Same three controls as the other suites:

    positive control  a run the plan expects to work. It must work.
    rejection control a condition the plan says ends or refuses the run. It must,
                      with the reason named.
    negative control  something that looks like such a condition but is not. It
                      must not end the run.

    python workflows/review-orchestration/tests/test_loop.py
"""

import asyncio
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

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
checks = _load("checks")
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


def git(root, *args, git_dir=None):
    command = ["git"]
    if git_dir:
        command += [f"--git-dir={git_dir}", "--work-tree=."]
    subprocess.run(command + list(args), cwd=str(root), check=True,
                   capture_output=True, encoding="utf-8")


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
    subprocess.run(["git", "init", "-q", "--bare", str(personal)], check=True,
                   capture_output=True)
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

    def reading(self):
        return self.harness.claude_usage or limits.Reading(
            limits.CLAUDE, limits.UNAVAILABLE, reason="no event")


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
        self.cat_a = set(CAT_A)
        self.frozen = None
        self.builder_refusals = []
        self.review_refusals = []
        self.during_review = None
        self.inventories = 0
        # A Codex builder's thread totals, one per turn, as the real one reports a
        # running total (the chunk (d) short plan, 11.3); 700 each when not given.
        self.codex_totals = []
        # What a fake Codex builder reports as its launch record (11.2), per launch.
        self.launch_records = []

    def start_check(self, root, python):
        """The start-of-run close-out check: clean unless a test says otherwise."""
        if self.start_check_writes:
            self.start_check_writes()
        return self.baseline

    def deps(self):
        return loop.Deps(make_builder=self.make_builder, make_reviewer=self.make_reviewer,
                         read_codex=self.read_codex, run_checks=self.run_checks,
                         baseline_checks=self.start_check,
                         category_a=lambda root: set(self.cat_a),
                         capture_problems=lambda root, git_dir: [],
                         inventory=self.inventory, claude_usage=lambda: self.usage,
                         sleep=self.slept.append, now=lambda: self.clock)

    def inventory(self, root, git_dir, category_a):
        self.inventories += 1
        return worktree.inventory(root, git_dir, category_a)

    def make_builder(self, settings, approver_, cwd, resume, run_dir, usage):
        self.given.append(("builder", run_dir, usage))
        builder = FakeBuilder(self, resume)
        self.builders.append(builder)
        return builder

    def make_reviewer(self, settings, cwd, usage):
        self.given.append(("reviewer", None, usage))
        return FakeReviewer(self)

    def read_codex(self, root):
        return self.readings.pop(0) if self.readings else reading()

    def run_checks(self, root, edit_paths, directories, commands, python=None):
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
            return providers.TurnRecord(
                name=name, final_text=text, session_id="s-1", model="gpt-6-sol",
                num_turns=None, is_error=False, usage={"total": {"totalTokens": total}},
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
                                      tokens=providers.codex_tokens(usage))


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


class RunTests(LoopCase):

    def test_positive_a_clean_review_ends_reviewed_clean_never_clean(self):
        self.h.builder_script = [edit(self.root, "done\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        self.assertEqual(run.state["step"], "review-R1")
        self.assertEqual([s["step"] for s in run.state["steps"]], ["build", "review-R1"])
        self.assertEqual(self.log_actions(), ["started", "completed"])
        folder = run.run_dir
        for name in ("brief.md", "settings.json", "state.json", "report.md", "report.json",
                     "start/heads.json", "start/diff.patch", "rounds/R1/diff.patch",
                     "rounds/R1/files.zip", "transcripts/build.json",
                     "transcripts/review-R1.json"):
            self.assertTrue((folder / name).is_file(), name)
        self.assertIn("+done", (folder / "rounds/R1/diff.patch").read_text(encoding="utf-8"))
        saved = json.loads((folder / "settings.json").read_text(encoding="utf-8"))
        self.assertIn("claude-agent-sdk", saved["sdk_versions"])
        self.assertEqual(run.state["live"]["claude_init_model"], MODEL)
        self.assertEqual(run.state["live"]["title_pattern"], "accepted")

    def test_positive_a_finding_fixed_then_a_clean_round(self):
        self.h.builder_script = [edit(self.root, "one\n"),
                                 lambda p: (edit(self.root, "two\n")(p),
                                            actions(("R1-1", "fixed"), ("R1-2", "fixed")))[1]]
        self.h.mechanical = [[{"title": "doc-sync: work: CONTEXT.md not updated",
                               "severity": "major", "file": "work", "evidence": "e",
                               "fix": "f"}], []]
        self.h.reviews = [[finding()], []]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        ledger = run.ledger.findings
        self.assertEqual(ledger["R1-1"]["source"], "mechanical")  # checks first
        self.assertEqual(ledger["R1-2"]["source"], "reviewer")
        text = (self.root / "memory" / "demo_review_packet.md").read_text(encoding="utf-8")
        self.assertIn("## Open\n\nNone.", text)
        self.assertIn("### R1-1 - doc-sync", text)
        fix_prompt = self.h.builders[0].prompts[1][1]
        self.assertIn("R1-1 (mechanical", fix_prompt)
        self.assertIn("R1-2 (reviewer", fix_prompt)

    def test_rejection_an_empty_first_pass_ends_no_change_made(self):
        self.h.builder_script = [reply("I changed nothing.")]
        run = self.start()
        self.assertEqual(self.reason(run), sr.NO_CHANGE_MADE)
        self.assertEqual(self.h.review_calls, [])

    def test_negative_rewriting_a_generated_file_is_not_a_change(self):
        (self.root / "work" / "out.generated").write_bytes(b"before\n")

        def regenerate(prompt):
            (self.root / "work" / "out.generated").write_bytes(b"after\n")
            return "ran a tool"
        self.h.builder_script = [regenerate]
        self.assertEqual(self.reason(self.start()), sr.NO_CHANGE_MADE)

    def test_negative_a_bytecode_cache_is_not_a_change(self):
        def cache(prompt):
            (self.root / "work" / "__pycache__").mkdir()
            (self.root / "work" / "__pycache__" / "a.cpython-313.pyc").write_bytes(b"x")
            return "ran the tests"
        self.h.builder_script = [cache]
        self.assertEqual(self.reason(self.start()), sr.NO_CHANGE_MADE)

    def test_rejection_declined_scope_ends_out_of_scope_at_once(self):
        self.h.builder_script = [edit(self.root, "x\n"),
                                 reply(actions(("R1-1", "declined-scope")))]
        self.h.reviews = [[finding()]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.OUT_OF_SCOPE)
        self.assertEqual(len(self.h.review_calls), 1)

    def test_rejection_a_reraise_of_a_declined_merits_finding_is_disagreement(self):
        self.h.builder_script = [edit(self.root, "x\n"),
                                 reply(actions(("R1-1", "declined-merits")))]
        self.h.reviews = [[finding()], [finding(reraises="R1-1")]]
        self.assertEqual(self.reason(self.start()), sr.DISAGREEMENT)

    def test_positive_a_merits_decline_not_raised_again_is_accepted(self):
        self.h.builder_script = [edit(self.root, "x\n"),
                                 reply(actions(("R1-1", "declined-merits")))]
        self.h.reviews = [[finding()], []]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        self.assertEqual(run.ledger.findings["R1-1"]["decline_accepted"], 2)
        text = (self.root / "memory" / "demo_review_packet.md").read_text(encoding="utf-8")
        self.assertIn("## Open\n\nNone.", text)  # the end reason and the packet agree
        self.assertIn("not raised again in round 2, so the decline stands", text)
        report = json.loads((run.run_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual((report["open"], report["addressed"]), ([], ["R1-1"]))

    def test_positive_a_decline_is_accepted_even_in_a_round_with_other_findings(self):
        self.h.builder_script = [edit(self.root, "x\n"),
                                 reply(actions(("R1-1", "declined-merits"))),
                                 reply(actions(("R2-1", "fixed")))]
        self.h.reviews = [[finding()], [finding(title="Another defect")], []]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        self.assertEqual(run.ledger.findings["R1-1"]["decline_accepted"], 2)

    def test_negative_a_decline_raised_again_is_never_accepted(self):
        self.h.builder_script = [edit(self.root, "x\n"),
                                 reply(actions(("R1-1", "declined-merits")))]
        self.h.reviews = [[finding()], [finding(reraises="R1-1")]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.DISAGREEMENT)
        self.assertNotIn("decline_accepted", run.ledger.findings["R1-1"])
        report = json.loads((run.run_dir / "report.json").read_text(encoding="utf-8"))
        self.assertIn("R1-1", report["open"])

    def test_rejection_a_malformed_builder_reply_ends_error(self):
        self.h.builder_script = [edit(self.root, "x\n"), reply("no action list")]
        self.h.reviews = [[finding()]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertEqual(self.log_actions()[-1], "failed")

    def test_rejection_a_label_in_a_reviewer_title_ends_error(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[finding(title="R1-1 again")]]
        self.assertEqual(self.reason(self.start()), sr.ERROR)

    def test_rejection_max_rounds_then_resume_with_more_rounds(self):
        self.h.builder_script = [edit(self.root, "x\n"), reply(actions(("R1-1", "fixed")))]
        self.h.reviews = [[finding()], []]
        run = self.start(cap=1)
        self.assertEqual(self.reason(run), sr.MAX_ROUNDS)
        again = self.resume(run, rounds=1)
        self.assertEqual(self.reason(again), sr.REVIEWED_CLEAN)
        self.assertEqual(again.state["round_cap"], 2)
        self.assertEqual([s["reason"] for s in again.state["stop_reasons"]],
                         [sr.MAX_ROUNDS, sr.REVIEWED_CLEAN])
        self.assertEqual(self.h.builders[-1].resume, "s-1")  # same builder session

    def test_rejection_a_reviewed_clean_run_cannot_be_resumed(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        again = loop.load(run.state["run_id"], root=self.root, deps=self.h.deps(),
                          log_path=self.log, runs_dir=self.runs)
        with self.assertRaises(loop.RunRefused):
            loop.resume(again)

    def test_negative_reviewer_is_shown_actions_but_never_the_builder_note(self):
        self.h.builder_script = [edit(self.root, "x\n"),
                                 reply(actions(("R1-1", "fixed")).replace(
                                     '"note": "n"', '"note": "SECRET-ACCOUNT"'))]
        self.h.reviews = [[finding()], []]
        self.start()
        second = self.h.review_calls[1][0]
        self.assertIn("R1-1 (reviewer) A defect: builder action fixed", second)
        self.assertNotIn("SECRET-ACCOUNT", second)
        self.assertNotIn("Changed work/a.txt", second)

    def test_positive_the_reviewer_is_given_the_brief_the_checks_and_the_diff(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        self.start()
        prompt = self.h.review_calls[0][0]
        self.assertIn("work/a.txt says done.", prompt)
        self.assertIn("checked work", prompt)
        self.assertIn("+x", prompt)

    def test_rejection_a_reviewer_that_skips_a_changed_file_ends_error(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        self.h.files_reviewed = []  # read nothing, yet claims clean
        run = self.start()
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertIn("work/a.txt", run.state["stop_reasons"][-1]["evidence"]["error"])

    def test_positive_every_changed_file_is_named_and_the_schema_requires_the_list(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        self.start()
        prompt, schema = self.h.review_calls[0]
        self.assertIn("THE CHANGED FILES", prompt)
        self.assertIn("- work/a.txt", prompt)
        self.assertIn("files_reviewed", schema["required"])

    def test_rejection_a_diff_too_large_for_the_prompt_is_never_cut(self):
        big = "line of text\n" * 40_000  # about 520,000 characters
        self.h.builder_script = [lambda p: ((self.root / "work" / "big.txt").write_bytes(
            big.encode("utf-8")), "wrote a big file")[1]]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        prompt = self.h.review_calls[0][0]
        self.assertIn("too large for this prompt", prompt)
        self.assertNotIn("line of text", prompt)  # no part of it is passed off as whole
        saved = (run.run_dir / "rounds/R1/diff.patch").read_text(encoding="utf-8")
        self.assertIn(f"rounds/R1/diff.patch", prompt)
        self.assertEqual(saved.count("line of text"), 40_000)  # the saved diff is whole

    def test_positive_the_checks_run_first_and_their_writes_are_named(self):
        def append_log():
            path = self.root / "work" / "a.txt"
            path.write_bytes(path.read_bytes() + b"check wrote this\n")
        self.h.check_writes = append_log
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        prompt = self.h.review_calls[0][0]
        written = prompt.split("FILES THE ORCHESTRATOR'S OWN CHECKS CHANGED")[1]
        written = written.split("THE DIFF")[0]
        # Exactly the check's line is named as the check's; the builder's own edit to
        # the same file is not (R5-2).
        self.assertIn("+check wrote this", written)
        self.assertNotIn("+x", written)
        self.assertEqual(run.state["check_writes"], ["rounds/R1/check-writes.patch"])
        # The reviewed diff and the archive both hold the state the checks left.
        diff = (run.run_dir / "rounds/R1/diff.patch").read_text(encoding="utf-8")
        self.assertIn("+check wrote this", diff)
        with zipfile.ZipFile(run.run_dir / "rounds/R1/files.zip") as bundle:
            self.assertIn(b"check wrote this", bundle.read("work/a.txt"))

    def test_positive_the_start_check_write_is_recorded_and_named(self):
        def append_log():
            path = self.root / "work" / "a.txt"
            path.write_bytes(path.read_bytes() + b"start check wrote\n")
        self.h.start_check_writes = append_log
        self.h.builder_script = [lambda p: ((self.root / "work" / "b.txt").write_bytes(
            b"builder\n"), "wrote b")[1]]
        self.h.reviews = [[]]
        run = self.start()
        self.assertTrue((run.run_dir / "start/check-writes.patch").is_file())
        written = self.h.review_calls[0][0].split(
            "FILES THE ORCHESTRATOR'S OWN CHECKS CHANGED")[1].split("THE DIFF")[0]
        self.assertIn("+start check wrote", written)
        self.assertNotIn("+builder", written)  # the builder's line is not the check's

    def test_rejection_a_failure_while_saving_a_failed_step_still_ends_the_run(self):
        # The classification reads at preflight, then breaks after the builder's turn,
        # as a builder editing the sync-architecture code mid-way could break it. The
        # build fails on it, and the partial diff, which reads it again, fails too: the
        # run must still end error, with its report and LOG.md entry, and resume (R7-1).
        calls = []

        def classification(root):
            calls.append(1)
            if len(calls) > 1 and not self.fixed:
                raise SyntaxError("invalid syntax (allowlist.py, line 1)")
            return CAT_A
        self.fixed = False
        deps = self.h.deps()
        deps.category_a = classification
        self.h.builder_script = [edit(self.root, "x\n")]
        run = loop.start("brief.md", "demo", str(self.personal), root=self.root,
                         deps=deps, log_path=self.log, runs_dir=self.runs,
                         settings=self.settings)
        asyncio.run(run.advance())
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertEqual(run.state["status"], sr.ENDED)
        self.assertIn("SyntaxError", run.state["stop_reasons"][-1]["evidence"]["error"])
        self.assertTrue((run.run_dir / "report.md").is_file())
        self.assertEqual(self.log_actions()[-1], "failed")
        patch = (run.run_dir / "partial" / "build.patch").read_text(encoding="utf-8")
        self.assertIn("could not be taken: SyntaxError", patch)
        # The diff's failure is noted in state.json too, not only in the patch (R8-1).
        self.assertTrue(any("build: the partial diff could not be taken: SyntaxError" in n
                            for n in run.state["interrupted_notes"]))
        # Fixed, the run resumes from its last completed step.
        self.fixed = True
        self.h.builder_script = [edit(self.root, "y\n")]
        self.h.reviews = [[]]
        again = loop.load(run.state["run_id"], root=self.root, deps=deps,
                          log_path=self.log, runs_dir=self.runs)
        loop.resume(again)
        asyncio.run(again.advance())
        self.assertEqual(self.reason(again), sr.REVIEWED_CLEAN)

    def test_rejection_two_distinct_failures_are_both_recorded(self):
        # The step fails for one reason (the reviewer) and the partial diff for another
        # (the classification): state.json holds both (R8-1).
        calls = []

        def classification(root):
            calls.append(1)
            # Reads 1 to 4: preflight, after the build, and the review's two (either side
            # of its checks). The fifth is the partial diff's, after the reviewer failed.
            if len(calls) > 4:
                raise OSError("classification unreadable")
            return CAT_A
        deps = self.h.deps()
        deps.category_a = classification
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [providers.ProviderError("Codex failed")]
        run = loop.start("brief.md", "demo", str(self.personal), root=self.root,
                         deps=deps, log_path=self.log, runs_dir=self.runs,
                         settings=self.settings)
        asyncio.run(run.advance())
        self.assertEqual(self.reason(run), sr.ERROR)
        error = run.state["stop_reasons"][-1]["evidence"]["error"]
        self.assertIn("Codex failed", error)  # the step's own failure
        self.assertNotIn("classification", error)
        self.assertTrue(any("review-R1: the partial diff could not be taken: OSError" in n
                            for n in run.state["interrupted_notes"]))

    def test_rejection_a_provider_failure_ends_error_and_resumes_at_that_step(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [providers.ProviderError("Codex failed"), []]
        run = self.start()
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertEqual(run.state["step"], "build")
        again = self.resume(run)
        self.assertEqual(self.reason(again), sr.REVIEWED_CLEAN)

    def test_rejection_codex_refusing_the_title_pattern_drops_it_and_goes_on(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [providers.ProviderError("invalid schema:\n  pattern not supported"),
                          []]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        self.assertTrue(run.state["live"]["title_pattern"].startswith("refused"))
        report = (run.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("- title_pattern: refused: invalid schema: pattern not supported\n",
                      report)
        second = self.h.review_calls[1][1]
        title = second["properties"]["findings"]["items"]["properties"]["title"]
        self.assertNotIn("pattern", title)
        self.assertIn("pattern", sr.REVIEW_SCHEMA["properties"]["findings"]["items"]
                      ["properties"]["title"])  # the shared schema is untouched


class UsageTests(LoopCase):

    def test_rejection_the_ceiling_stops_before_the_step(self):
        self.h.readings = [reading(30), reading(85)]
        run = self.start()
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(self.h.builders, [])  # no builder turn was made
        self.assertIn("ceiling", run.state["stop_reasons"][-1]["evidence"]["detail"])

    def test_rejection_the_ceiling_at_preflight_refuses_the_run(self):
        self.h.readings = [reading(90)]
        with self.assertRaises(loop.RunRefused):
            self.start()
        self.assertFalse(self.runs.exists())

    def test_rejection_claude_paid_overage_stops_before_the_next_step(self):
        # The reading arrives as the builder's session reports it, during the build.
        def build(prompt):
            self.h.claude_usage = limits.Reading(
                limits.CLAUDE, limits.LIMIT_REPORTED, percent=50, resets_at=5,
                window="five_hour", reason="using paid overage")
            return edit(self.root, "x\n")(prompt)
        self.h.builder_script = [build]
        run = self.start()
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(run.state["step"], "build")  # the build finished; review never ran
        self.assertIn("paid overage", run.state["stop_reasons"][-1]["evidence"]["detail"])

    def test_rejection_a_changed_credit_balance_stops_the_codex_step(self):
        changed = dict(CREDITS, balance="4.50")
        self.h.readings = [reading(), reading(), reading(credits=changed)]
        self.h.builder_script = [edit(self.root, "x\n")]
        run = self.start()
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        detail = run.state["stop_reasons"][-1]["evidence"]["detail"]
        self.assertIn("balance '5.00' -> '4.50'", detail)
        self.assertEqual(self.h.review_calls, [])

    def test_rejection_a_top_up_also_stops_it_and_a_resume_rebaselines(self):
        topped = dict(CREDITS, balance="25.00")
        self.h.readings = [reading(), reading(), reading(credits=topped),
                           reading(credits=topped), reading(credits=topped)]
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        again = self.resume(run)
        self.assertEqual(self.reason(again), sr.REVIEWED_CLEAN)
        self.assertEqual(again.state["credits"]["balance"], "25.00")

    def test_rejection_a_missing_credit_snapshot_stops_the_codex_step(self):
        self.h.readings = [reading(), reading(), reading(credits=None)]
        self.h.builder_script = [edit(self.root, "x\n")]
        run = self.start()
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertIn("missing", run.state["stop_reasons"][-1]["evidence"]["detail"])

    def test_negative_a_credit_change_does_not_stop_a_builder_step(self):
        changed = dict(CREDITS, balance="4.50")
        self.h.readings = [reading(), reading(credits=changed), reading()]
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        self.assertEqual(self.reason(self.start()), sr.REVIEWED_CLEAN)

    def test_rejection_allowance_reached_with_credits_next_stops_the_codex_step(self):
        self.h.readings = [reading(), reading(), reading(reached="rate_limit_reached")]
        self.h.builder_script = [edit(self.root, "x\n")]
        run = self.start()
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertIn("credits next", run.state["stop_reasons"][-1]["evidence"]["detail"])

    def test_rejection_untrackable_credits_refuse_the_run(self):
        for credits in (None, {"hasCredits": True, "unlimited": False}):
            self.h.readings = [reading(credits=credits)]
            with self.assertRaises(loop.RunRefused):
                self.start()

    def test_rejection_a_refusal_for_credits_names_what_codex_answered(self):
        """A reading with no credit snapshot is usually an unavailable one, and the
        refusal must carry its reason rather than only "no credits snapshot" (proof run
        2's resume from Codex was refused twice with nothing to go on)."""
        self.h.readings = [reading(credits=None, source="unavailable")]
        with self.assertRaises(loop.RunRefused) as caught:
            self.start()
        self.assertIn("Codex usage not reported: x", caught.exception.args[0][0])
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start(inject="build")
        self.h.readings = [reading(credits=None, source="unavailable")]
        with self.assertRaises(loop.RunRefused) as caught:
            loop.resume(run)
        self.assertIn("Codex usage not reported: x", caught.exception.args[0][0])
        self.assertIn("the run is unchanged", caught.exception.args[0][0])

    def test_negative_no_credits_and_not_unlimited_needs_no_balance(self):
        self.h.readings = [reading(credits={"hasCredits": False, "unlimited": False})] * 3
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        self.assertEqual(self.reason(self.start()), sr.REVIEWED_CLEAN)

    def test_rejection_a_failed_reading_before_a_step_ends_error_not_a_crash(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        calls = []

        def read(root):
            calls.append(1)
            if len(calls) == 3:
                raise RuntimeError("transport closed")
            return reading()
        deps = self.h.deps()
        deps.read_codex = read
        run = loop.start("brief.md", "demo", str(self.personal), root=self.root,
                         deps=deps, log_path=self.log, runs_dir=self.runs,
                         settings=self.settings)
        asyncio.run(run.advance())
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertIn("usage reading", run.state["stop_reasons"][-1]["evidence"]["error"])


class LimitStopTests(LoopCase):

    def test_positive_an_injected_builder_limit_stops_cleanly_and_resumes(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start(inject="build")
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(run.state["step"], "preflight")  # the last completed step
        self.assertTrue(run.state["inject"]["fired"])
        self.assertIn("injected", run.state["stop_reasons"][-1]["evidence"]["detail"])
        again = self.resume(run)
        self.assertEqual(self.reason(again), sr.REVIEWED_CLEAN)
        self.assertEqual(self.log_actions(), ["started", "completed", "started",
                                              "completed"])

    def test_positive_an_injected_review_limit_stops_cleanly_and_resumes(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start(inject="review-R1")
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(run.state["step"], "build")
        self.assertEqual(self.h.review_calls, [])
        again = self.resume(run)
        self.assertEqual(self.reason(again), sr.REVIEWED_CLEAN)
        self.assertEqual(len(self.h.review_calls), 1)

    def test_positive_a_builder_cut_off_mid_edit_resumes_in_its_session(self):
        def partial(prompt):
            (self.root / "work" / "a.txt").write_bytes(b"half\n")
            raise limits.UsageLimitError(limits.CLAUDE, "rate_limit", resets_at=None)
        self.h.builder_script = [partial, edit(self.root, "whole\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(run.state["builder_session"], "s-1")
        patch = (run.run_dir / "partial" / "build.patch").read_text(encoding="utf-8")
        self.assertIn("+half", patch)
        again = self.resume(run)
        self.assertEqual(self.reason(again), sr.REVIEWED_CLEAN)
        self.assertEqual(self.h.builders[-1].resume, "s-1")
        self.assertIn("interrupted", self.h.builders[-1].prompts[0][1])

    def test_positive_wait_sleeps_to_the_reset_and_resumes_itself(self):
        def limited(prompt):
            raise limits.UsageLimitError(limits.CLAUDE, "rate_limit",
                                         resets_at=self.h.clock + 600)
        self.h.builder_script = [limited, edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start(wait=True)
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        self.assertEqual(self.h.slept, [600 + loop.WAIT_MARGIN_SECONDS])

    def test_negative_wait_does_nothing_without_a_reset_time(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        run = self.start(inject="build", wait=True)
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(self.h.slept, [])
        report = (run.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("not reported", report)

    def test_rejection_an_injection_that_is_not_a_step_is_refused(self):
        with self.assertRaises(loop.RunRefused):
            self.start(inject="review")


class PreflightTests(LoopCase):

    def test_rejection_every_refusal_is_named_and_nothing_is_created(self):
        (self.root / "memory" / "demo_review_packet.md").write_bytes(b"old\n")
        deps = self.h.deps()
        deps.capture_problems = lambda root, git_dir: ["not captured"]
        with self.assertRaises(loop.RunRefused) as caught:
            loop.preflight("brief.md", "demo", str(self.personal), root=self.root,
                           deps=deps, settings=self.settings)
        text = " ".join(caught.exception.args[0])
        self.assertIn("already exists", text)
        self.assertIn("not captured", text)
        self.assertFalse(self.runs.exists())

    def test_rejection_an_unreadable_classification_refuses_the_run(self):
        deps = self.h.deps()

        def missing(root):
            raise worktree.GitError("the sync plan is missing")
        deps.category_a = missing
        with self.assertRaises(loop.RunRefused) as caught:
            loop.preflight("brief.md", "demo", str(self.personal), root=self.root,
                           deps=deps, settings=self.settings)
        self.assertIn("sync classification", " ".join(caught.exception.args[0]))

    def test_rejection_uncommitted_work_under_the_edit_paths_refuses_the_run(self):
        (self.root / "work" / "a.txt").write_bytes(b"work already here\n")
        (self.root / "work" / "new.txt").write_bytes(b"untracked\n")
        with self.assertRaises(loop.RunRefused) as caught:
            self.start()
        text = " ".join(caught.exception.args[0])
        self.assertIn("work/a.txt", text)
        self.assertIn("work/new.txt", text)
        self.assertIn("commit them first", text)
        self.assertFalse(self.runs.exists())

    def test_negative_env_and_uncommitted_work_elsewhere_do_not_refuse_the_run(self):
        (self.root / "work" / ".env").write_bytes(b"SECRET=x\n")  # never part of a run
        # Ignored, as a real one must be: a run does not start while a .env-named file
        # is neither tracked nor ignored (chunk (d), short plan 5.1 rule 3).
        with open(self.root / ".git" / "info" / "exclude", "ab") as handle:
            handle.write(b".env\n")
        (self.root / "brief.md").write_bytes(BRIEF.encode("utf-8") + b"\n")  # outside
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        self.assertEqual(self.reason(self.start()), sr.REVIEWED_CLEAN)

    def test_positive_the_clean_start_check_is_recorded(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(run.state["baseline"], self.h.baseline)

    def test_rejection_a_tree_whose_checks_are_not_clean_refuses_the_run(self):
        self.h.baseline = {"clean": False, "status": "fail",
                           "problems": ["close-out gate failed: tests: 1 failed"]}
        with self.assertRaises(loop.RunRefused) as caught:
            self.start()
        text = " ".join(caught.exception.args[0])
        self.assertIn("not clean", text)
        self.assertIn("close-out gate failed: tests", text)
        self.assertFalse(self.runs.exists())
        self.assertEqual(self.h.builders, [])

    def test_rejection_a_refused_resume_leaves_the_run_exactly_as_it_was(self):
        self.h.builder_script = [edit(self.root, "x\n"), reply(actions(("R1-1", "fixed")))]
        self.h.reviews = [[finding()]]
        run = self.start(cap=1)
        self.assertEqual(self.reason(run), sr.MAX_ROUNDS)
        before = {name: (run.run_dir / name).read_bytes()
                  for name in ("state.json", "report.md", "report.json")}
        log_before = self.log.read_bytes()
        for trouble in (reading(credits=None), RuntimeError("transport closed")):
            deps = self.h.deps()
            deps.read_codex = (lambda root, r=trouble: (_ for _ in ()).throw(r)) \
                if isinstance(trouble, Exception) else (lambda root, r=trouble: r)
            again = loop.load(run.state["run_id"], root=self.root, deps=deps,
                              log_path=self.log, runs_dir=self.runs)
            with self.assertRaises(loop.RunRefused):
                loop.resume(again, rounds=1)
            for name, content in before.items():
                self.assertEqual((run.run_dir / name).read_bytes(), content, name)
            self.assertEqual(self.log.read_bytes(), log_before)

    def test_rejection_an_unavailable_codex_reading_is_reported_as_the_plan_asks(self):
        no_windows = limits.Reading(limits.CODEX, limits.UNAVAILABLE,
                                    reason="the response carried neither usage window",
                                    raw={"rateLimits": {"credits": dict(CREDITS)}})
        self.h.readings = [no_windows, reading(), reading()]
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        report = json.loads((run.run_dir / "report.json").read_text(encoding="utf-8"))
        source = report["usage_source"]
        self.assertEqual(source["codex"], "unavailable")
        self.assertEqual(source["codex_reason"], "the response carried neither usage window")
        self.assertEqual(source["codex_raw"], no_windows.raw)
        first = (run.run_dir / "report.md").read_text(encoding="utf-8").splitlines()[2]
        self.assertTrue(first.startswith("Codex usage not reported: the response"))
        # It holds for the rest of the run: later readings are not trusted for the ceiling.
        self.assertTrue(all("Codex usage not reported" in r["codex"]
                            for r in run.state["readings"]))

    def test_negative_a_reported_codex_reading_is_named_as_reported(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        report = json.loads((run.run_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["usage_source"], {"codex": "reported"})
        self.assertNotIn("not reported", (run.run_dir / "report.md").read_text(
            encoding="utf-8").splitlines()[2])

    def test_rejection_a_bad_item_or_brief_is_refused(self):
        (self.root / "bad.md").write_bytes(b"## Goal\n")
        with self.assertRaises(loop.RunRefused) as caught:
            loop.preflight("bad.md", "Bad Item", str(self.personal), root=self.root,
                           deps=self.h.deps(), settings=self.settings)
        text = " ".join(caught.exception.args[0])
        self.assertIn("--item", text)
        self.assertIn("does not pass the check", text)

    def test_positive_a_dry_run_creates_nothing(self):
        with redirect_stdout(io.StringIO()):
            result = loop.start("brief.md", "demo", str(self.personal), root=self.root,
                                deps=self.h.deps(), log_path=self.log,
                                runs_dir=self.runs, dry_run=True, settings=self.settings)
        self.assertIsNone(result)
        self.assertFalse(self.runs.exists())
        self.assertFalse(self.log.exists())

    def test_rejection_a_dry_run_never_claims_the_check_it_did_not_run(self):
        # The tree's checks are not clean; a dry run cannot run them (the verifier
        # writes its own log), so it must say they were not checked (R6-1).
        self.h.baseline = {"clean": False, "status": "fail", "problems": ["x"]}
        called = []
        deps = self.h.deps()
        deps.baseline_checks = lambda root, python: called.append(1) or self.h.baseline
        err = io.StringIO()
        with redirect_stderr(err):
            loop.start("brief.md", "demo", str(self.personal), root=self.root, deps=deps,
                       log_path=self.log, runs_dir=self.runs, dry_run=True,
                       settings=self.settings)
        text = err.getvalue()
        self.assertEqual(called, [])  # the check that writes was not run
        self.assertIn("NOT checked: the close-out verifier's clean-start check", text)
        self.assertNotIn("preflight passed", text)
        self.assertFalse(self.runs.exists())

    def test_positive_the_capture_check_on_a_clean_repository(self):
        self.assertEqual(worktree.git(self.root, "status", "--short",
                                      "--untracked-files=no", git_dir=str(self.personal)),
                         "")

    def test_rejection_the_capture_check_sees_an_uncommitted_personal_file(self):
        (self.root / "memory" / "note.md").write_bytes(b"changed\n")
        problems = loop.capture_problems(self.root, str(self.personal))
        self.assertTrue(any("uncommitted" in p for p in problems))


class CommandLineTests(LoopCase):

    RUN = ("--run", "brief.md", "--builder", "claude")

    def call(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = run_py.main(list(argv), root=self.root, log_path=self.log,
                               deps=self.h.deps(), runs_dir=self.runs)
        return code, out.getvalue()

    def test_positive_run_exits_0_on_reviewed_clean(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        code, out = self.call(*self.RUN, "--item", "demo", "--git-dir",
                              str(self.personal))
        self.assertEqual(code, 0, out)
        self.assertIn("ended reviewed-clean", out)

    def test_rejection_a_refused_run_exits_3_and_other_ends_exit_1(self):
        self.h.readings = [reading(95)]
        code, out = self.call(*self.RUN, "--item", "demo", "--git-dir",
                              str(self.personal))
        self.assertEqual(code, 3)
        self.assertIn("REFUSED", out)
        self.h.builder_script = [reply("nothing")]
        code, _ = self.call(*self.RUN, "--item", "other", "--git-dir",
                            str(self.personal))
        self.assertEqual(code, 1)

    def test_rejection_stop_refuses_a_run_that_is_not_paused(self):
        self.h.builder_script = [reply("nothing")]
        self.call(*self.RUN, "--item", "demo", "--git-dir", str(self.personal))
        run_id = next(self.runs.iterdir()).name
        code, out = self.call("--stop", run_id)
        self.assertEqual(code, 3)
        self.assertIn("only a paused run", out)

    def usage_error(self, *argv):
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as caught:
            self.call(*argv)
        self.assertEqual(caught.exception.code, 2)
        return err.getvalue()

    def test_rejection_run_with_no_builder_is_a_usage_error(self):
        # The chunk (d) short plan, 11.1: no default; the AI asks, never picks.
        text = self.usage_error("--run", "brief.md", "--item", "demo", "--git-dir",
                                str(self.personal))
        self.assertIn("--run needs --builder claude or --builder codex", text)
        self.assertFalse(self.runs.exists() and any(self.runs.iterdir()))

    def test_rejection_a_builder_that_is_not_a_provider(self):
        text = self.usage_error("--run", "brief.md", "--builder", "gemini", "--item",
                                "demo", "--git-dir", str(self.personal))
        self.assertIn("invalid choice: 'gemini'", text)

    def test_rejection_builder_with_resume_stop_or_check(self):
        for argv in (("--resume", "20261004-000000-abcd"), ("--stop", "20261004-000000-abcd"),
                     ("--check-brief", "brief.md")):
            with self.subTest(mode=argv[0]):
                text = self.usage_error(*argv, "--builder", "codex")
                self.assertIn("--builder is given only with --run", text)

    def test_positive_the_named_builder_is_kept_in_the_record_for_a_resume(self):
        self.h.builder_script = [reply("nothing")]
        self.call(*self.RUN, "--item", "demo", "--git-dir", str(self.personal))
        run_dir = next(self.runs.iterdir())
        saved = json.loads((run_dir / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["roles"], {"builder": "claude", "reviewer": "codex"})
        again = loop.load(run_dir.name, root=self.root, deps=self.h.deps(),
                          log_path=self.log, runs_dir=self.runs)
        self.assertEqual(again.settings["roles"], saved["roles"])

    def test_rejection_preflight_with_no_builder_and_no_roles(self):
        bare = settings_mod.load_settings()
        with self.assertRaises(loop.RunRefused) as caught:
            loop.preflight("brief.md", "demo", str(self.personal), root=self.root,
                           deps=self.h.deps(), settings=bare)
        self.assertIn("no builder named", " ".join(caught.exception.args[0]))

    def test_positive_a_named_builder_overrides_nothing_else(self):
        ready = loop.preflight("brief.md", "demo", str(self.personal), root=self.root,
                               deps=self.h.deps(), settings=settings_mod.load_settings(),
                               builder="codex")
        self.assertEqual(ready["settings"]["roles"], {"builder": "codex", "reviewer": "claude"})
        self.assertEqual(ready["settings"]["models"], settings_mod.DEFAULT_MODELS)

    def bytes_of(self, run_dir):
        return ({p.name: p.read_bytes() for p in run_dir.iterdir() if p.is_file()},
                self.log.read_bytes())

    def test_positive_resume_dry_run_checks_and_changes_nothing(self):
        self.h.builder_script = [edit(self.root, "x\n"), reply(actions(("R1-1", "fixed")))]
        self.h.reviews = [[finding()]]
        settings = json.loads(json.dumps(self.settings))
        settings["round_caps"]["build_review"] = 1
        run = loop.start("brief.md", "demo", str(self.personal), root=self.root,
                         deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                         settings=settings)
        asyncio.run(run.advance())
        before = self.bytes_of(run.run_dir)
        err = io.StringIO()
        with redirect_stderr(err):
            code, out = self.call("--resume", run.state["run_id"], "--rounds", "1",
                                  "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("[DRY RUN] would resume run", err.getvalue())
        self.assertIn("next step fix-R1", err.getvalue())
        self.assertEqual(self.bytes_of(run.run_dir), before)
        self.assertEqual(len(self.h.builders), 1)  # no session was started

    def test_rejection_resume_dry_run_still_refuses_what_resume_refuses(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        code, out = self.call("--resume", run.state["run_id"], "--dry-run")
        self.assertEqual(code, 3)
        self.assertIn("cannot be resumed", out)

    def test_positive_stop_dry_run_changes_nothing(self):
        self.h.builder_script = [reply("nothing")]
        self.call(*self.RUN, "--item", "demo", "--git-dir", str(self.personal))
        run_dir = next(self.runs.iterdir())
        state = runrecord.read_state(run_dir)
        state.update(status="paused", pause="awaiting-user")
        runrecord.write_state(run_dir, state)
        before = self.bytes_of(run_dir)
        err = io.StringIO()
        with redirect_stderr(err):
            code, _ = self.call("--stop", run_dir.name, "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("[DRY RUN] would end run", err.getvalue())
        self.assertEqual(self.bytes_of(run_dir), before)

    def test_positive_stop_ends_a_paused_run_stopped_by_user(self):
        self.h.builder_script = [reply("nothing")]
        self.call(*self.RUN, "--item", "demo", "--git-dir", str(self.personal))
        run_dir = next(self.runs.iterdir())
        state = runrecord.read_state(run_dir)
        state.update(status="paused", pause="awaiting-user")
        runrecord.write_state(run_dir, state)
        code, out = self.call("--stop", run_dir.name)
        self.assertEqual(runrecord.read_state(run_dir)["stop_reasons"][-1]["reason"],
                         sr.STOPPED_BY_USER)
        self.assertEqual(code, 1)


class PriceAlarmTests(LoopCase):

    def test_positive_a_step_over_twice_the_median_is_flagged(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        first = self.start(item="one")
        steps = first.state["steps"]
        steps[0]["tokens"] = 10  # an earlier builder step far cheaper than 110
        runrecord.write_state(first.run_dir, first.state)
        self.reset_work()
        self.h.builder_script = [edit(self.root, "y\n")]
        self.h.reviews = [[]]
        second = self.start(item="two")
        flags = loop.price_alarm(second)
        self.assertEqual([(f["step"], f["role"]) for f in flags], [("build", "builder")])

    def test_negative_a_run_that_started_later_is_not_counted(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        other = self.start(item="one")
        other.state["steps"][0]["tokens"] = 10
        other.state["started_ns"] = 2 ** 62  # started after, whatever its ID
        runrecord.write_state(other.run_dir, other.state)
        self.reset_work()
        self.h.builder_script = [edit(self.root, "y\n")]
        self.h.reviews = [[]]
        self.assertEqual(loop.price_alarm(self.start(item="two")), [])

    def test_negative_runs_started_in_the_same_second_are_ordered_in_both_orders(self):
        # Two runs whose created times are the same second: only started_ns orders them.
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        a = self.start(item="one")
        self.reset_work()
        self.h.builder_script = [edit(self.root, "y\n")]
        self.h.reviews = [[]]
        b = self.start(item="two")
        for early, late in ((a, b), (b, a)):
            early.state["created"] = late.state["created"] = "2026-09-30T10:00:00+01:00"
            early.state["started_ns"], late.state["started_ns"] = 1_000, 2_000
            early.state["steps"][0]["tokens"] = 10
            late.state["steps"][0]["tokens"] = 110
            for run in (early, late):
                runrecord.write_state(run.run_dir, run.state)
            self.assertEqual([f["step"] for f in loop.price_alarm(late)], ["build"])
            self.assertEqual(loop.price_alarm(early), [])  # the later run never counts
        # Records from before started_ns, in the same second, cannot be ordered: neither
        # counts the other, rather than one being guessed earlier.
        for run in (a, b):
            run.state.pop("started_ns")
            run.state["steps"][0]["tokens"] = 10 if run is a else 110
            runrecord.write_state(run.run_dir, run.state)
        self.assertEqual(loop.price_alarm(b), [])
        self.assertEqual(loop.price_alarm(a), [])

    def test_negative_no_earlier_runs_means_no_alarm(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        self.assertEqual(loop.price_alarm(self.start()), [])

    def two_runs(self):
        """An earlier run whose build step is cheap, and a later one to judge."""
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        first = self.start(item="one")
        self.reset_work()
        self.h.builder_script = [edit(self.root, "y\n")]
        self.h.reviews = [[]]
        second = self.start(item="two")
        return first, second

    def test_negative_the_other_providers_steps_in_the_same_role_are_not_compared(self):
        # The chunk (d) short plan, 11.3: like with like, provider and role together.
        first, second = self.two_runs()
        first.state["steps"][0].update(tokens=10, provider="codex",
                                       thread_total_tokens=10)
        runrecord.write_state(first.run_dir, first.state)
        self.assertEqual(loop.price_alarm(second), [])  # second's builder is Claude
        first.state["steps"][0]["provider"] = "claude"
        runrecord.write_state(first.run_dir, first.state)
        self.assertEqual([f["step"] for f in loop.price_alarm(second)], ["build"])

    def test_negative_a_codex_builder_step_with_no_thread_total_is_not_compared(self):
        # R9-1: the first proof run's Codex builder steps hold the thread's running
        # total and no thread_total_tokens, so they cannot stand as a step's share.
        first, second = self.two_runs()
        shaped = {"tokens": 10, "provider": "codex"}  # as 20261003-035437-0ec4 records
        first.state["steps"][0].update(shaped)
        first.state["steps"][0].pop("thread_total_tokens", None)
        runrecord.write_state(first.run_dir, first.state)
        second.state["steps"][0].update(provider="codex", tokens=110,
                                        thread_total_tokens=110)
        self.assertEqual(loop.price_alarm(second), [])
        first.state["steps"][0]["thread_total_tokens"] = 10  # recorded under fix 3
        runrecord.write_state(first.run_dir, first.state)
        flags = loop.price_alarm(second)
        self.assertEqual([(f["step"], f["provider"]) for f in flags], [("build", "codex")])

    def test_negative_a_step_that_names_no_provider_is_not_compared(self):
        first, second = self.two_runs()
        first.state["steps"][0]["tokens"] = 10
        first.state["steps"][0].pop("provider")
        runrecord.write_state(first.run_dir, first.state)
        self.assertEqual(loop.price_alarm(second), [])


class RunRecordEscapingTests(LoopCase):
    """The chunk (d) short plan, 11.4: every JSON file of a run record is written with
    non-ASCII characters escaped, so a garbled tool output kept verbatim cannot fail the
    encoding guard, and reads back as the same values."""

    # Built with chr() so this source file stays ASCII: an em dash, the three characters
    # an em dash becomes when UTF-8 is read as Windows-1252, and a pound sign.
    GARBLED = ("em dash " + chr(0x2014) + ", garbled " + chr(0xE2) + chr(0x20AC)
               + chr(0x201D) + ", pound " + chr(0xA3))

    def test_positive_write_json_escapes_and_reads_back_equal(self):
        path = self.root / "out" / "x.json"
        value = {"text": self.GARBLED, "list": [self.GARBLED]}
        loop._write_json(path, value)
        raw = path.read_bytes()
        self.assertTrue(raw.isascii(), raw)
        self.assertEqual(json.loads(raw.decode("utf-8")), value)

    def test_positive_every_json_file_a_run_writes_is_ascii(self):
        write = edit(self.root, "x\n")

        def build(prompt):
            write(prompt)
            return "Changed work/a.txt. " + self.GARBLED
        self.h.builder_script = [build,
                                 reply(self.GARBLED + "\n" + actions(("R1-1", "fixed")))]
        self.h.reviews = [[finding(title="A defect in " + self.GARBLED)], []]
        run = self.start()
        files = [p for p in run.run_dir.rglob("*") if p.suffix in (".json", ".jsonl")]
        self.assertTrue(files)
        for path in files:
            with self.subTest(path=path.relative_to(run.run_dir).as_posix()):
                self.assertTrue(path.read_bytes().isascii())
        self.assertIn(self.GARBLED, json.dumps(runrecord.read_state(run.run_dir),
                                               ensure_ascii=False))


class PacketTests(unittest.TestCase):

    def ledger(self):
        ledger = sr.Ledger()
        ledger.add_round(1, [{"title": "check failed", "severity": "blocker",
                              "file": "x", "evidence": "e", "fix": "f"}],
                         [dict(finding(), title="bad\n## Open\n- R9-9 fake")])
        ledger.apply_actions({"R1-1": ("fixed", "done"),
                              "R1-2": ("declined-merits", "wrong")})
        return ledger

    def test_positive_the_handoff_reader_counts_the_packet(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        (root / "memory").mkdir()
        packet.write(root, "demo", "20260929-120000-abcd", self.ledger())
        sys.path.insert(0, str(PROJECT / "workflows" / "handoff" / "scripts"))
        import gather
        found = [p for p in gather.review_packets(root)
                 if p["path"].replace("\\", "/").endswith("demo_review_packet.md")]
        self.assertEqual(found[0]["open"], ["R1-2"])
        self.assertEqual(found[0]["addressed"], ["R1-1"])
        self.assertEqual(found[0]["labels_noncanonical"], [])

    def test_rejection_provider_text_cannot_break_the_structure(self):
        text = packet.render("demo", "20260929-120000-abcd", self.ledger())
        self.assertEqual(text.count("\n## Open"), 1)
        self.assertNotIn("\n- R9-9", text)
        self.assertEqual(packet.clean("- x"), "\\- x")
        self.assertEqual(packet.clean("# x"), "\\# x")
        self.assertEqual(packet.clean("a" + chr(0x2014) + "b"), "a - b")  # an em dash

    def test_positive_a_declined_finding_stays_open_with_the_builder_action(self):
        text = packet.render("demo", "20260929-120000-abcd", self.ledger())
        open_part = text.split("## Addressed")[0]
        self.assertIn("R1-2", open_part)
        self.assertIn("Builder: declined-merits. wrong", open_part)

    def test_positive_an_accepted_decline_moves_to_addressed(self):
        ledger = self.ledger()
        ledger.findings["R1-2"]["decline_accepted"] = 2
        text = packet.render("demo", "20260929-120000-abcd", ledger)
        open_part, addressed = text.split("## Addressed")
        self.assertNotIn("R1-2", open_part)
        self.assertIn("### R1-2", addressed)
        self.assertIn("Reviewer: not raised again in round 2", addressed)

    def test_negative_a_scope_decline_is_never_addressed(self):
        self.assertFalse(packet.is_addressed({"action": "declined-scope",
                                              "decline_accepted": 2}))


class BriefReaderTests(unittest.TestCase):

    def test_positive_edit_paths_and_acceptance_come_from_a_passing_brief(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        (root / "work").mkdir()
        (root / "b.md").write_bytes(BRIEF.encode("utf-8"))
        read = brief.read_brief("b.md", root)
        self.assertEqual(read["edit_paths"], ["work/"])
        self.assertEqual(read["acceptance"], ['python -c "print(1)"'])
        self.assertEqual(read["sections"]["Goal"], ["- work/a.txt says done."])

    def test_rejection_a_failing_brief_is_never_read(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        (Path(folder.name) / "b.md").write_bytes(b"## Goal\n")
        with self.assertRaises(ValueError):
            brief.read_brief("b.md", folder.name)


class WorktreeTests(unittest.TestCase):

    def setUp(self):
        self.root, self.personal = make_project(self)
        self.heads = {"public": worktree.git(self.root, "rev-parse", "HEAD").strip(),
                      "personal": worktree.git(self.root, "rev-parse", "HEAD",
                                               git_dir=str(self.personal)).strip()}

    def diff(self, paths):
        return worktree.review_diff(self.root, paths, self.heads["public"],
                                    self.heads["personal"], str(self.personal),
                                    category_a=CAT_A)

    def test_positive_tracked_new_and_ignored_changes_are_all_shown(self):
        (self.root / "work" / "a.txt").write_bytes(b"changed\n")
        (self.root / "work" / "new.txt").write_bytes(b"fresh\n")
        (self.root / "memory" / "note.md").write_bytes(b"personal edit\n")
        (self.root / "memory" / "other.md").write_bytes(b"new personal\n")
        text = self.diff(["work/", "memory/"])
        self.assertIn("+changed", text)
        self.assertIn("--- new file work/new.txt ---\nfresh", text)
        self.assertIn("+personal edit", text)  # against the personal commit
        self.assertIn("--- new file memory/other.md ---", text)

    def test_positive_a_deleted_personal_file_shows_as_a_deletion(self):
        (self.root / "memory" / "note.md").unlink()
        self.assertIn("deleted file", self.diff(["memory/"]))

    def test_negative_no_change_is_an_empty_diff_and_an_empty_change_list(self):
        self.assertEqual(self.diff(["work/"]), "")
        before = worktree.snapshot(self.root, ["work/"], CAT_A)
        self.assertEqual(worktree.changed(before, worktree.snapshot(self.root, ["work/"],
                                                                    CAT_A)), [])

    def test_rejection_env_files_never_reach_a_diff_a_snapshot_or_an_archive(self):
        (self.root / "work" / ".env").write_bytes(b"SECRET=untracked\n")
        (self.root / "work" / "sub").mkdir()
        (self.root / "work" / "sub" / ".ENV.local").write_bytes(b"SECRET=case\n")
        (self.root / "memory" / ".env").write_bytes(b"SECRET=ignored\n")
        (self.root / "work" / "a.txt").write_bytes(b"real change\n")
        text = self.diff(["work/", "memory/"])
        self.assertNotIn("SECRET", text)
        self.assertIn("+real change", text)  # the rest of the diff is still there
        self.assertEqual(worktree.list_files(self.root, ["work/", "memory/"], CAT_A),
                         ["memory/note.md", "work/a.txt"])
        folder = self.root / "runs" / "r"
        worktree.record_round(folder, 1, self.root, ["work/"], "", category_a=CAT_A)
        with zipfile.ZipFile(folder / "rounds/R1/files.zip") as bundle:
            self.assertEqual(bundle.namelist(), ["work/a.txt"])

    def test_rejection_a_tracked_env_file_is_kept_out_of_git_diffs(self):
        (self.root / "work" / ".env").write_bytes(b"SECRET=one\n")
        git(self.root, "add", "work/.env")
        git(self.root, "commit", "-qm", "tracked env")
        head = worktree.git(self.root, "rev-parse", "HEAD").strip()
        (self.root / "work" / ".env").write_bytes(b"SECRET=two\n")
        text = worktree.review_diff(self.root, ["work/"], head, category_a=CAT_A)
        self.assertNotIn("SECRET", text)
        folder = self.root / "runs" / "s"
        worktree.record_start(folder, self.root, ["work/"], str(self.personal))
        self.assertNotIn("SECRET", (folder / "start/diff.patch").read_text(encoding="utf-8"))

    def test_rejection_generated_ignored_files_are_never_work(self):
        # A generated file (Category C), there before the run and rewritten after.
        (self.root / "work" / "last-result.generated").write_bytes(b"old\n")
        before = worktree.snapshot(self.root, ["work/"], CAT_A)
        (self.root / "work" / "last-result.generated").write_bytes(b"rewritten\n")
        (self.root / "memory" / "scratch.md").write_bytes(b"not category A\n")
        self.assertEqual(worktree.changed(before, worktree.snapshot(self.root, ["work/"],
                                                                    CAT_A)), [])
        text = self.diff(["work/", "memory/"])
        self.assertNotIn("last-result.generated", text)
        self.assertNotIn("scratch.md", text)
        self.assertEqual(worktree.list_files(self.root, ["work/", "memory/"], CAT_A),
                         ["memory/note.md", "work/a.txt"])
        folder = self.root / "runs" / "g"
        worktree.record_round(folder, 1, self.root, ["work/"], "", category_a=CAT_A)
        with zipfile.ZipFile(folder / "rounds/R1/files.zip") as bundle:
            self.assertEqual(bundle.namelist(), ["work/a.txt"])

    def test_positive_a_new_category_a_file_is_work_and_reviewed(self):
        (self.root / "memory" / "new.md").write_bytes(b"a new memory file\n")
        self.assertIn("memory/new.md", worktree.list_files(self.root, ["memory/"], CAT_A))
        self.assertIn("--- new file memory/new.md ---", self.diff(["memory/"]))

    def test_rejection_a_missing_classification_is_an_error_not_a_guess(self):
        with self.assertRaises(worktree.GitError):
            worktree.category_a(self.root)  # the fixture has no sync plan

    def test_rejection_a_run_record_is_never_the_builder_work(self):
        workflow = self.root / "workflows" / "review-orchestration"
        (workflow / "runs" / "r1").mkdir(parents=True)
        (workflow / "runs" / "r1" / "state.json").write_bytes(b"{}\n")
        (workflow / "notes.md").write_bytes(b"workflow file\n")
        paths = ["workflows/review-orchestration/"]
        self.assertEqual(worktree.list_files(self.root, paths, CAT_A),
                         ["workflows/review-orchestration/notes.md"])
        text = self.diff(paths)
        self.assertIn("workflows/review-orchestration/notes.md", text)
        self.assertNotIn("state.json", text)

    def test_negative_caches_are_skipped(self):
        (self.root / "work" / "__pycache__").mkdir()
        (self.root / "work" / "__pycache__" / "a.pyc").write_bytes(b"x")
        self.assertEqual(worktree.list_files(self.root, ["work/"], CAT_A), ["work/a.txt"])

    def test_positive_the_round_record_holds_every_file_in_an_archive(self):
        folder = self.root / "runs" / "r"
        worktree.record_round(folder, 1, self.root, ["work/"], "the diff",
                              category_a=CAT_A)
        with zipfile.ZipFile(folder / "rounds/R1/files.zip") as bundle:
            self.assertEqual(bundle.namelist(), ["work/a.txt"])
            self.assertEqual(bundle.read("work/a.txt"), b"start\n")
        self.assertEqual((folder / "rounds/R1/diff.patch").read_text(encoding="utf-8"),
                         "the diff")
        # No live copy of the project is left for project tools to walk into.
        self.assertEqual(sorted(p.name for p in (folder / "rounds/R1").iterdir()),
                         sorted(["diff.patch", "files.zip"]))

    def test_positive_a_round_written_again_replaces_its_record(self):
        folder = self.root / "runs" / "r"
        worktree.record_round(folder, 1, self.root, ["work/"], "first", category_a=CAT_A)
        (self.root / "work" / "a.txt").write_bytes(b"later\n")
        worktree.record_round(folder, 1, self.root, ["work/"], "second", category_a=CAT_A)
        with zipfile.ZipFile(folder / "rounds/R1/files.zip") as bundle:
            self.assertEqual(bundle.read("work/a.txt"), b"later\n")
        self.assertEqual((folder / "rounds/R1/diff.patch").read_text(encoding="utf-8"),
                         "second")


class ChecksTests(unittest.TestCase):

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def script(self, relative, body):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body.encode("utf-8"))

    def test_positive_a_passing_acceptance_check_is_not_a_finding(self):
        found, lines = checks.acceptance(self.root, sys.executable, ['python -c "print(1)"'])
        self.assertEqual(found, [])
        self.assertIn("PASS", lines[0])

    def test_rejection_a_failing_acceptance_check_is_a_finding(self):
        found, _ = checks.acceptance(self.root, sys.executable,
                                     ['python -c "import sys; sys.exit(4)"'])
        self.assertEqual(found[0]["severity"], "blocker")
        self.assertIn("exit 4", found[0]["evidence"])

    def test_rejection_a_check_that_cannot_run_is_a_finding(self):
        found, _ = checks.close_out(self.root, sys.executable)
        self.assertEqual(found[0]["title"], "close-out verifier did not run")
        found, _ = checks.acceptance(self.root, sys.executable, ["no-such-program-xyz"])
        self.assertEqual(len(found), 1)

    def close_out_script(self, gates, degraded=()):
        self.script("workflows/close-out/scripts/run.py",
                    "import json\nprint(json.dumps({'status': 'fail', 'gates': "
                    + repr(gates) + ", 'degraded': " + repr(list(degraded)) + "}))\n")

    PASSED = [{"name": "tests", "passed": True, "detail": "ok", "files": []},
              {"name": "link audit", "passed": True, "detail": "0 dead link(s)"}]

    def test_positive_a_clean_tree_may_start(self):
        self.close_out_script(self.PASSED)
        start = checks.close_out_start(self.root, sys.executable)
        self.assertEqual((start["clean"], start["problems"]), (True, []))

    def test_rejection_a_failing_skipped_or_unreadable_check_at_the_start_is_not_clean(self):
        failing = [dict(self.PASSED[0], passed=False, detail="1 failed",
                        files=[{"file": "other/test_x.py", "passed": False}])]
        for gates, degraded in ((failing, ()),
                                (self.PASSED, ["structural audit: a guard skipped"])):
            self.close_out_script(gates, degraded)
            start = checks.close_out_start(self.root, sys.executable)
            self.assertFalse(start["clean"])
            self.assertTrue(start["problems"])
        self.script("workflows/close-out/scripts/run.py", "print('not json')\n")
        self.assertFalse(checks.close_out_start(self.root, sys.executable)["clean"])

    def test_rejection_every_failed_gate_during_a_run_is_a_finding(self):
        # The run started clean, so nothing failing now can predate it: the old
        # comparison of test names, audit entries and link counts is gone (R5-3).
        self.close_out_script([
            {"name": "tests", "passed": False, "detail": "1 failed",
             "files": [{"file": "other/test_x.py", "passed": False}]},
            {"name": "link audit", "passed": False, "detail": "2 dead link(s)"},
            {"name": "structural audit", "passed": False,
             "detail": "could not run audit: boom"}])
        found, _ = checks.close_out(self.root, sys.executable)
        self.assertEqual([f["title"] for f in found],
                         ["close-out gate failed: tests", "close-out gate failed: link audit",
                          "close-out gate failed: structural audit"])
        self.assertIn("other/test_x.py", found[0]["evidence"])

    def test_rejection_a_skipped_scope_is_a_finding_even_with_every_gate_passed(self):
        self.close_out_script(self.PASSED, ["structural audit: encoding guard skipped"])
        found, lines = checks.close_out(self.root, sys.executable)
        self.assertEqual([f["title"] for f in found], ["close-out skipped part of a check"])
        self.assertIn("encoding guard skipped", found[0]["evidence"])

    def test_rejection_a_doc_sync_scan_that_did_not_run_is_a_finding_anywhere(self):
        self.script("workflows/doc-sync-guard/scripts/run.py",
                    "import json\nprint(json.dumps({'findings': ["
                    "{'severity': 'WARN', 'message': 'scan skipped - git failed'},"
                    "{'severity': 'DEGRADED', 'message': 'inventory: could not load'}"
                    "]}))\n")
        found, _ = checks.doc_sync(self.root, sys.executable, ["work/"])
        self.assertEqual(len(found), 2)
        self.assertTrue(all(f["title"].startswith("doc-sync did not run in full")
                            for f in found))

    def test_positive_a_failed_close_out_gate_is_a_finding(self):
        self.script("workflows/close-out/scripts/run.py",
                    "import json\nprint(json.dumps({'status': 'fail', 'gates': ["
                    "{'name': 'tests', 'passed': False, 'detail': '1 failed'},"
                    "{'name': 'link audit', 'passed': True, 'detail': 'ok'}]}))\n")
        found, _ = checks.close_out(self.root, sys.executable)
        self.assertEqual([f["title"] for f in found], ["close-out gate failed: tests"])

    def test_negative_a_doc_sync_warn_outside_the_edit_paths_is_not_a_finding(self):
        self.script("workflows/doc-sync-guard/scripts/run.py",
                    "import json\nprint(json.dumps({'findings': ["
                    "{'severity': 'WARN', 'message': 'work/sub: CONTEXT.md not updated'},"
                    "{'severity': 'WARN', 'message': 'workshop: CONTEXT.md not updated'}"
                    "]}))\n")
        found, lines = checks.doc_sync(self.root, sys.executable, ["work/"])
        self.assertEqual([f["file"] for f in found], ["work/sub"])
        self.assertTrue(any("outside this run's edit paths" in line for line in lines))

    def test_rejection_an_audit_that_crashes_before_reporting_is_a_finding(self):
        self.script("workflows/audit/scripts/run.py",
                    "import sys\nsys.stderr.write('Traceback: boom')\nsys.exit(1)\n")
        found, lines = checks.audit(self.root, sys.executable, ["work"])
        self.assertEqual([f["title"] for f in found], ["targeted audit failed (exit 1)"])
        self.assertIn("boom", found[0]["evidence"])
        self.assertFalse(any("clean" in line for line in lines))

    def test_negative_a_clean_audit_exiting_0_is_not_a_finding(self):
        self.script("workflows/audit/scripts/run.py",
                    "print('**Failures:** 0\\n\\nAll structural checks passed.')\n")
        found, lines = checks.audit(self.root, sys.executable, ["work"])
        self.assertEqual(found, [])
        self.assertIn("clean", lines[-1])

    def test_positive_audit_failures_and_warnings_are_findings(self):
        self.script("workflows/audit/scripts/run.py",
                    "print('## Failures\\n\\n- `work` missing CONTEXT.md\\n\\n"
                    "## Warnings\\n\\n- `work` Revision History over cap\\n\\n"
                    "## Info\\n\\n- `work` fine')\n")
        found, _ = checks.audit(self.root, sys.executable, ["work"])
        self.assertEqual([f["severity"] for f in found], ["blocker", "major"])
        self.assertEqual(found[0]["file"], "work")


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


class ReversePairingTests(SwappedCase):
    """Short plan section 6: every row of the table of what the loop assumed, with the
    roles swapped. The same rows with the roles as they are today are the tests above
    and ``TodaysPairingTests`` below."""

    def test_positive_a_whole_run_records_by_provider_not_by_role(self):
        self.h.builder_script = [edit(self.root, "done\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        steps = run.state["steps"]
        self.assertEqual([(s["role"], s["provider"]) for s in steps],
                         [("builder", "codex"), ("reviewer", "claude")])
        # Tokens come from the session that knows its own usage shape.
        self.assertEqual([s["tokens"] for s in steps], [700, 450])
        self.assertIsNone(steps[0]["cost_usd_equivalent"])
        self.assertEqual(steps[1]["thread_id"], "claude-session")
        live = run.state["live"]
        self.assertEqual(live["codex_model"], "gpt-6-sol")
        self.assertEqual(live["claude_init_model"], MODEL)
        # The builder is given the run folder and the run's one Claude usage object;
        # so is every fresh Claude reviewer.
        self.assertEqual(self.h.given[0], ("builder", run.run_dir, self.h.usage))
        self.assertEqual(self.h.given[1], ("reviewer", None, self.h.usage))
        report = (run.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("## Check after every builder turn", report)
        self.assertIn("- build: ", report)
        self.assertIn("nothing found", report)

    def builder_steps(self, run):
        return [s for s in run.state["steps"] if s["role"] == "builder"]

    def test_positive_each_codex_builder_step_records_its_share(self):
        # The chunk (d) short plan, 11.3 and live check 9: the thread reports a running
        # total, and a second fix is measured from the first fix, not the build.
        self.h.codex_totals = [1000, 1600, 2500]
        self.h.builder_script = [edit(self.root, "x\n"), reply(actions(("R1-1", "fixed"))),
                                 reply(actions(("R2-1", "fixed")))]
        self.h.reviews = [[finding()], [finding(title="Another defect")], []]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        steps = self.builder_steps(run)
        self.assertEqual([s["step"] for s in steps], ["build", "fix-R1", "fix-R2"])
        self.assertEqual([s["tokens"] for s in steps], [1000, 600, 900])
        self.assertEqual([s["thread_total_tokens"] for s in steps], [1000, 1600, 2500])
        self.assertEqual({s["thread"] for s in steps}, {"s-1"})

    def test_positive_the_forced_stop_and_resume_of_live_check_3(self):
        # R8-1's repair: --inject-limit fix-R1 stops the run before the fix turn, after
        # the builder session is open; the resume opens a new session on the same
        # thread, whose launch record is stored with its step, and the share still holds.
        self.h.codex_totals = [1000, 1600]
        self.h.launch_records = [{"self_test": "passed", "thread": {"method": "start"}},
                                 {"self_test": "passed", "thread": {"method": "resume"}}]
        self.h.builder_script = [edit(self.root, "x\n"), reply(actions(("R1-1", "fixed")))]
        self.h.reviews = [[finding()], []]
        run = self.start(inject="fix-R1")
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(run.state["step"], "review-R1")
        self.assertEqual([s["step"] for s in self.builder_steps(run)], ["build"])
        self.assertTrue(self.h.builders[0].closed)
        again = self.resume(run)
        self.assertEqual(self.reason(again), sr.REVIEWED_CLEAN)
        self.assertEqual(self.h.builders[1].resume, "s-1")
        launches = again.state["live"]["codex_builder_launches"]
        self.assertEqual([(entry["step"], entry["thread"]["method"]) for entry in launches],
                         [("build", "start"), ("fix-R1", "resume")])
        self.assertEqual([s["tokens"] for s in self.builder_steps(again)], [1000, 600])

    def test_negative_a_session_with_no_launch_record_adds_nothing(self):
        self.h.builder_script = [edit(self.root, "done\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertNotIn("codex_builder_launches", run.state["live"])

    def test_positive_the_title_pattern_is_not_sent_to_a_claude_reviewer(self):
        self.h.builder_script = [edit(self.root, "done\n")]
        self.h.reviews = [[]]
        run = self.start()
        schema = self.h.review_calls[0][1]
        title = schema["properties"]["findings"]["items"]["properties"]["title"]
        self.assertNotIn("pattern", title)
        self.assertIn("files_reviewed", schema["required"])
        self.assertEqual(run.state["live"]["title_pattern"], "not sent")

    def test_rejection_the_ceiling_on_a_reading_from_an_earlier_reviewer_session(self):
        # The run holds one Claude usage object, so what round 1's reviewer session
        # reported is still there before the next step, with that session gone.
        def reported():
            self.h.claude_usage = limits.Reading(
                limits.CLAUDE, limits.REPORTED, percent=85, resets_at=5,
                window="five_hour")
        self.h.during_review = reported
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[finding()]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(run.state["step"], "review-R1")  # the fix pass never started
        self.assertEqual(len(self.h.builders[0].prompts), 1)
        stop = run.state["stop_reasons"][-1]["evidence"]
        self.assertEqual(stop["provider"], "claude")
        self.assertIn("ceiling", stop["detail"])

    def test_rejection_the_credit_rule_runs_before_a_codex_builder_step(self):
        changed = dict(CREDITS, balance="4.50")
        self.h.readings = [reading(), reading(credits=changed)]
        self.h.builder_script = [edit(self.root, "x\n")]
        run = self.start()
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(run.state["step"], "preflight")
        self.assertEqual(self.h.builders, [])
        self.assertIn("balance '5.00' -> '4.50'", self.evidence(run))

    def test_negative_the_credit_rule_does_not_run_before_a_claude_review_step(self):
        changed = dict(CREDITS, balance="4.50")
        self.h.readings = [reading(), reading(), reading(credits=changed)]
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        self.assertEqual(self.reason(self.start()), sr.REVIEWED_CLEAN)

    def test_positive_refusals_from_each_kind_of_session_reach_the_report(self):
        self.h.builder_refusals = [{"tool": "Bash", "detail": "curl example.com",
                                    "reason": "Refused by the orchestrator: no."}]
        self.h.review_refusals = [{"tool": "Write", "detail": "x.txt",
                                   "reason": "Refused by the orchestrator: the reviewer "
                                             "is read-only."}]
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual([(r["step"], r["tool"]) for r in run.state["refusals"]],
                         [("build", "Bash"), ("review-R1", "Write")])
        report = (run.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("## Refused calls", report)
        self.assertIn("- build: Bash curl example.com", report)
        self.assertIn("- review-R1: Write x.txt", report)

    def test_positive_refusals_are_kept_when_the_step_fails(self):
        self.h.builder_refusals = [{"tool": "webrun", "detail": "", "reason": "r"}]

        def fails(prompt):
            raise RuntimeError("the turn was ended")
        self.h.builder_script = [fails]
        run = self.start()
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertEqual([(r["step"], r["tool"]) for r in run.state["refusals"]],
                         [("build", "webrun")])

    def test_positive_a_failed_review_keeps_what_the_reviewer_was_refused(self):
        self.h.review_refusals = [{"tool": "Bash", "detail": "ls", "reason": "r"}]
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [providers.ProviderError("no structured reply")]
        run = self.start()
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertEqual([(r["step"], r["tool"]) for r in run.state["refusals"]],
                         [("review-R1", "Bash")])

    def test_positive_both_prompts_take_the_form_for_their_provider(self):
        self.h.builder_script = [edit(self.root, "x\n"),
                                 reply(actions(("R1-1", "fixed")))]
        self.h.reviews = [[finding()], []]
        self.assertEqual(self.reason(self.start()), sr.REVIEWED_CLEAN)
        build, fix = (prompt for _, prompt in self.h.builders[0].prompts)
        for text in (build, fix):
            self.assertIn("apply_patch", text)
            self.assertIn('Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz"', text)
            self.assertIn("Get-Content -LiteralPath PATH", text)
            # Nothing a Codex builder does not have.
            for absent in ("Biblio Tools", "get_timestamp", "append_log"):
                self.assertNotIn(absent, text)
            # Its own verification list: neither writing command is offered.
            self.assertIn("ai-style-guard|backlog-guard", text)
            self.assertNotIn("workflows/audit/scripts/run", text)
            self.assertNotIn("workflows/close-out/scripts/run", text)
            self.assertNotIn("$", text.replace("`$`", ""))
        review = self.h.review_calls[0][0]
        self.assertIn("you cannot run anything", review)
        self.assertIn("A file wildcard in a search is refused", review)
        self.assertNotIn("You may run read-only commands", review)

    def test_positive_the_lists_printed_are_the_frozen_folders(self):
        # The frozen copies judge the builder, so they are what its prompt shows.
        (self.h.frozen / "codex-verify-commands.txt").write_bytes(b"python frozen-verify\n")
        (self.h.frozen / "codex-read-commands.txt").write_bytes(b"frozen-read PATH\n")
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        self.start()
        build = self.h.builders[0].prompts[0][1]
        self.assertIn("python frozen-verify", build)
        self.assertIn("frozen-read PATH", build)
        # The project's own read list is not printed. The prompt's literal Get-Content
        # example (fix 5) names a real path; the list's line names the PATH token.
        self.assertNotIn("Get-Content -LiteralPath PATH", build)
        self.assertNotIn("rg( -n", build)

    def test_positive_an_injected_limit_at_a_codex_builder_step(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start(inject="build")
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(run.state["stop_reasons"][-1]["evidence"]["provider"], "codex")
        self.assertEqual(self.reason(self.resume(run)), sr.REVIEWED_CLEAN)

    def test_positive_an_injected_limit_at_a_claude_review_step(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start(inject="review-R1")
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(run.state["stop_reasons"][-1]["evidence"]["provider"], "claude")
        self.assertEqual(self.h.review_calls, [])
        self.assertEqual(self.reason(self.resume(run)), sr.REVIEWED_CLEAN)


class TodaysPairingTests(LoopCase):
    """The same rows of the table with Claude building and Codex reviewing."""

    def test_positive_tokens_models_and_sessions_by_provider(self):
        self.h.builder_script = [edit(self.root, "done\n")]
        self.h.reviews = [[]]
        run = self.start()
        steps = run.state["steps"]
        self.assertEqual([(s["role"], s["provider"]) for s in steps],
                         [("builder", "claude"), ("reviewer", "codex")])
        self.assertEqual([s["tokens"] for s in steps], [110, 1000])
        self.assertEqual(steps[0]["cost_usd_equivalent"], 0.01)
        self.assertEqual(steps[1]["thread_id"], "t")
        self.assertEqual(run.state["live"]["claude_init_model"], MODEL)
        self.assertNotIn("codex_model", run.state["live"])
        self.assertEqual(run.state["live"]["title_pattern"], "accepted")
        self.assertEqual(self.h.given[0], ("builder", run.run_dir, self.h.usage))
        self.assertEqual(self.h.given[1], ("reviewer", None, self.h.usage))

    def test_negative_the_check_after_every_turn_is_not_run_for_a_claude_builder(self):
        def stray(prompt):
            (self.root / "other.txt").write_bytes(b"outside the edit paths\n")
            return edit(self.root, "x\n")(prompt)
        self.h.builder_script = [stray]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        self.assertEqual(self.h.inventories, 0)
        self.assertNotIn("turn_checks", run.state)
        report = (run.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertNotIn("Check after every builder turn", report)

    def test_positive_both_prompts_take_the_form_for_their_provider(self):
        self.h.builder_script = [edit(self.root, "x\n"),
                                 reply(actions(("R1-1", "fixed")))]
        self.h.reviews = [[finding()], []]
        self.assertEqual(self.reason(self.start()), sr.REVIEWED_CLEAN)
        build, fix = (prompt for _, prompt in self.h.builders[0].prompts)
        self.assertIn("the Biblio Tools\n    `get_timestamp` or `append_log` tool", build)
        self.assertIn("workflows/audit/scripts/run", build)
        for text in (build, fix):
            # Nothing a Claude builder does not have.
            for absent in ("apply_patch", "Get-Date", "Get-Content"):
                self.assertNotIn(absent, text)
        review = self.h.review_calls[0][0]
        self.assertIn("You may run read-only commands, such as the tests.", review)
        self.assertNotIn("cannot run anything", review)


class TurnCheckTests(SwappedCase):
    """The check after every Codex builder turn (plan 10.4, chunk (d); short plan 4)."""

    def planted(self, action):
        """A run whose builder does ``action`` during its turn, as well as its work."""
        def turn(prompt):
            action()
            return edit(self.root, "x\n")(prompt)
        self.h.builder_script = [turn]
        self.h.reviews = [[]]
        return self.start()

    def refused(self, action, *named):
        run = self.planted(action)
        self.assertEqual(self.reason(run), sr.ERROR)
        text = self.evidence(run)
        self.assertIn("check after the builder's turn", text)
        for name in named:
            self.assertIn(name, text)
        self.assertEqual(self.h.review_calls, [], "no review of a turn that failed it")
        # The failed step's partial record is saved before the run ends.
        self.assertTrue((run.run_dir / "partial" / "build.patch").is_file())
        self.assertEqual(run.state["interrupted"], "build")
        self.assertTrue(run.state["turn_checks"][-1]["changes"])
        return run

    def personal_git(self, *args):
        git(self.root, "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args,
            git_dir=str(self.personal))

    def test_positive_a_turn_inside_the_edit_paths_passes(self):
        run = self.planted(lambda: None)
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        check, = run.state["turn_checks"]
        self.assertEqual((check["step"], check["changes"]), ("build", []))
        self.assertIsInstance(check["seconds"], float)
        self.assertEqual(self.h.inventories, 2, "one before the turn and one after")

    def test_rejection_a_write_to_a_tracked_file_outside_the_edit_paths(self):
        self.refused(lambda: (self.root / "brief.md").write_bytes(b"changed\n"),
                     "brief.md was changed outside the edit paths")

    def test_rejection_a_new_file_outside_the_edit_paths(self):
        self.refused(lambda: (self.root / "other.txt").write_bytes(b"new\n"),
                     "other.txt was added outside the edit paths")

    def test_rejection_a_new_category_a_file(self):
        self.refused(lambda: (self.root / "memory" / "new.md").write_bytes(b"new\n"),
                     "memory/new.md was added")

    def test_rejection_a_change_to_an_existing_category_a_file(self):
        self.refused(lambda: (self.root / "memory" / "note.md").write_bytes(b"other\n"),
                     "memory/note.md was changed")

    def test_rejection_a_deleted_tracked_file(self):
        self.refused((self.root / "brief.md").unlink, "brief.md was removed")

    def test_rejection_a_deleted_category_a_file(self):
        self.refused((self.root / "memory" / "note.md").unlink,
                     "memory/note.md was removed")

    def test_rejection_a_git_add_in_the_public_repository(self):
        def add():
            (self.root / "work" / "a.txt").write_bytes(b"staged\n")
            git(self.root, "add", "work/a.txt")
        self.refused(add, "the staged contents changed in the public repository")

    def test_rejection_a_new_branch_in_the_public_repository(self):
        self.refused(lambda: git(self.root, "branch", "sneaky"),
                     "a ref changed in the public repository")

    def test_rejection_a_git_add_in_the_personal_repository(self):
        def add():
            (self.root / "memory" / "note.md").write_bytes(b"staged\n")
            self.personal_git("add", "-f", "memory/note.md")
        self.refused(add, "the staged contents changed in the personal repository")

    def test_rejection_a_new_branch_in_the_personal_repository(self):
        self.refused(lambda: self.personal_git("branch", "sneaky"),
                     "a ref changed in the personal repository")

    def test_negative_a_change_inside_the_run_record_is_the_runs_own(self):
        # The session copies its hook's decisions into the run record during a turn.
        def record():
            folder, = self.runs.iterdir()
            (folder / "hook-decisions.jsonl").write_bytes(b"{}\n")
            self.h.cat_a.add(f"runs/{folder.name}/hook-decisions.jsonl")
        run = self.planted(record)
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        self.assertEqual(run.state["turn_checks"][0]["changes"], [])

    def test_rejection_any_other_change_inside_the_run_record(self):
        # Code review finding R3-1: only the decisions copy is the orchestrator's own;
        # a listed test that rewrote the run's saved roles must end the run.
        for name in ("settings.json", "state.json", "brief.md", "new.txt"):
            with self.subTest(name):
                self.setUp()

                def record(name=name):
                    folder, = self.runs.iterdir()
                    (folder / name).write_bytes(b"{}\n")
                    self.h.cat_a.add(f"runs/{folder.name}/{name}")
                self.refused(record, f"/{name} was ")

    def test_positive_the_exempt_record_file_is_the_one_the_session_copies(self):
        self.assertEqual(worktree.TURN_RECORD_WRITES, (codex_rules.DECISIONS_FILE,))

    def test_rejection_a_run_record_change_under_an_edit_path_that_covers_it(self):
        # Code review finding R5-1: an edit path over this workflow covers runs/ too,
        # and run records are judged before edit paths.
        root = self.root
        base = "workflows/review-orchestration"
        run_dir = root / base / "runs" / "20261004-000000-abcd"

        def inventory(files):
            return {"files": files, "git": {"public": {}, "personal": {}}}
        record = f"{base}/runs/20261004-000000-abcd"
        before = inventory({f"{record}/settings.json": "a", f"{record}/hook-decisions.jsonl": "a",
                            f"{base}/runs/20261003-000000-dcba/state.json": "a",
                            f"{base}/scripts/x.py": "a"})
        after = inventory({f"{record}/settings.json": "b", f"{record}/hook-decisions.jsonl": "b",
                           f"{base}/runs/20261003-000000-dcba/state.json": "b",
                           f"{base}/scripts/x.py": "b", f"{record}/new.txt": "b"})
        for edit_paths in ([f"{base}/"], [f"{base}/runs/"], ["workflows/"]):
            with self.subTest(edit_paths=edit_paths):
                problems = worktree.compare_inventory(before, after, edit_paths, run_dir,
                                                      root)
                expected = [f"{record}/new.txt was added in a run record",
                            f"{record}/settings.json was changed in a run record",
                            f"{base}/runs/20261003-000000-dcba/state.json was changed "
                            "in a run record"]
                if edit_paths == [f"{base}/runs/"]:
                    expected.append(f"{base}/scripts/x.py was changed outside the edit "
                                    "paths")
                self.assertEqual(sorted(problems), sorted(expected))

    def test_rejection_a_changed_added_or_removed_file_in_the_frozen_folder(self):
        for label in ("changed", "added", "removed"):
            with self.subTest(label):
                self.setUp()  # a fresh project and frozen folder for each case
                hook = self.h.frozen / "codex_hook.py"
                run = self.planted({"changed": lambda: hook.write_bytes(b"other\n"),
                                    "added": lambda: (self.h.frozen / "extra.py")
                                    .write_bytes(b"x\n"),
                                    "removed": hook.unlink}[label])
                self.assertEqual(self.reason(run), sr.ERROR)
                self.assertIn("the frozen hook folder changed during the turn",
                              self.evidence(run))
                self.assertIn({"changed": "codex_hook.py", "added": "extra.py",
                               "removed": "codex_hook.py"}[label], self.evidence(run))

    def test_negative_the_decisions_file_is_meant_to_change(self):
        decisions = self.h.frozen / "hook-decisions.jsonl"
        run = self.planted(lambda: decisions.write_bytes(b'{"decision": "allow"}\n'))
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)

    def test_negative_the_check_calls_file_is_meant_to_change(self):
        # The check helper writes it during a turn (isolation plan 6, item 5).
        calls = self.h.frozen / "check-calls.jsonl"
        run = self.planted(lambda: calls.write_bytes(b'{"allowed": true}\n'))
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)

    def test_rejection_another_frozen_file_changing_beside_the_calls_file(self):
        # Control for the test above: the calls file's exemption is that file only.
        def both():
            (self.h.frozen / "check-calls.jsonl").write_bytes(b"{}\n")
            (self.h.frozen / "start-ignored.txt").write_bytes(b".env\n")
        run = self.planted(both)
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertIn("start-ignored.txt", self.evidence(run))
        self.assertNotIn("check-calls.jsonl", self.evidence(run))

    def test_negative_a_bytecode_cache_in_the_frozen_folder_is_not_a_change(self):
        # Running the hook writes one; the rules it runs from are what is compared.
        def cache():
            (self.h.frozen / "__pycache__").mkdir()
            (self.h.frozen / "__pycache__" / "codex_rules.cpython-313.pyc").write_bytes(b"x")
        run = self.planted(cache)
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)

    def test_rejection_the_check_runs_when_the_turn_raised(self):
        def fails(prompt):
            (self.root / "other.txt").write_bytes(b"left behind\n")
            raise RuntimeError("the turn was ended")
        self.h.builder_script = [fails]
        run = self.start()
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertIn("other.txt was added outside the edit paths", self.evidence(run))
        self.assertEqual(self.h.inventories, 2)

    def test_negative_a_turn_that_raised_and_changed_nothing_keeps_its_own_error(self):
        def fails(prompt):
            raise RuntimeError("the turn was ended")
        self.h.builder_script = [fails]
        run = self.start()
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertIn("RuntimeError: the turn was ended", self.evidence(run))
        self.assertNotIn("check after the builder's turn", self.evidence(run))
        self.assertEqual(run.state["turn_checks"][0]["changes"], [])

    def test_positive_the_check_runs_after_a_fix_pass_too(self):
        def fix(prompt):
            (self.root / "other.txt").write_bytes(b"new\n")
            return actions(("R1-1", "fixed"))
        self.h.builder_script = [edit(self.root, "x\n"), fix]
        self.h.reviews = [[finding()]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertEqual([c["step"] for c in run.state["turn_checks"]],
                         ["build", "fix-R1"])
        self.assertIn("other.txt was added", self.evidence(run))


class EnvPreflightTests(LoopCase):
    """Short plan 5.1 rule 3: no run of either pairing starts while a .env-named file
    is neither tracked nor ignored."""

    def loose(self):
        (self.root / ".env").write_bytes(b"SECRET=1\n")
        (self.root / "sub").mkdir()
        (self.root / "sub" / ".env.local").write_bytes(b"SECRET=2\n")

    def test_rejection_an_untracked_unignored_env_file_each_named(self):
        self.loose()
        for swapped in (False, True):
            with self.subTest(swapped=swapped):
                self.h.swapped = swapped
                with self.assertRaises(loop.RunRefused) as caught:
                    self.start()
                text = " ".join(caught.exception.args[0])
                self.assertIn(".env is a `.env`-named file that git neither tracks",
                              text)
                self.assertIn("sub/.env.local is a `.env`-named file", text)
                self.assertIn("add it to .gitignore", text)
                self.assertFalse(self.runs.exists())

    def test_positive_the_run_starts_once_they_are_ignored(self):
        self.loose()
        with open(self.root / ".git" / "info" / "exclude", "ab") as handle:
            handle.write(b".env\n.env.*\n")
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        self.assertEqual(self.reason(self.start()), sr.REVIEWED_CLEAN)

    def test_negative_a_tracked_env_named_file_does_not_refuse_the_run(self):
        # A tracked file is already published, so it is not a secret; the read rules
        # still stop a read that names it.
        (self.root / ".env.example").write_bytes(b"KEY=placeholder\n")
        git(self.root, "add", ".env.example")
        git(self.root, "commit", "-qm", "example")
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        self.assertEqual(self.reason(self.start()), sr.REVIEWED_CLEAN)


class PromptTests(unittest.TestCase):

    def test_positive_every_prompt_renders_with_its_fields(self):
        loop.render("builder", edit_paths="a/", tool_rules="  - x", brief="B")
        text = loop.render("reviewer", round=1, brief="B", mechanical="M", prior="P",
                           diff="D", changed_files="- a", check_written="None.",
                           reviewer_tools="T")
        self.assertIn("round\n1.", text)
        fix = loop.render("fix", round=2, findings="F", tool_rules="")
        self.assertIn('"label": "R2-1"', fix)

    def test_rejection_a_missing_field_fails_loudly(self):
        with self.assertRaises(KeyError):
            loop.render("reviewer", round=1)

    def test_positive_the_builder_prompt_lists_the_verification_commands(self):
        rules = loop.tool_rules(limits.CLAUDE, approver.load_verify_commands())
        text = loop.render("builder", edit_paths="a/", tool_rules=rules, brief="B")
        self.assertIn("doc-sync-guard", text)
        self.assertIn("No git commands", text)
        self.assertNotIn("$", text.split("THE BRIEF")[0].replace("(?!-)", ""),
                         "no field is left unfilled")


if __name__ == "__main__":
    unittest.main()
