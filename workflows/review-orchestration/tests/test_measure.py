#!/usr/bin/env python3
"""Hermetic tests for the measurement harness (plan 10.8, A5): measure.py, the shared
review steps it calls in loop.py, the judge's reply in stopreasons.py, the measurement
record in runrecord.py, the ignore floor in checks.py, the judge's settings, the judge
prompt and the shipped fixtures in measurement/.

No test starts Docker, a model session, a Claude probe or a Codex app server, and none
reads the live project or the personal repository except the shipped-key test, which
skips, saying why, where the key or the personal repository is absent. Every provider,
Docker and usage boundary is a fake (``FakeWorld``); git runs only in temporary
repositories a test builds. Same three controls as the other suites: positive (what the
plan expects works), rejection (what it rules out is refused, named) and negative
(what looks wrong but is not).

    python workflows/review-orchestration/tests/test_measure.py
"""

import ast
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

TESTS = Path(__file__).resolve().parent
WORKFLOW = TESTS.parent
SCRIPTS = WORKFLOW / "scripts"
FIXTURES = WORKFLOW / "measurement"
sys.path.insert(0, str(SCRIPTS))

import brief as brief_mod  # noqa: E402
import checks as checks_mod  # noqa: E402
import limits  # noqa: E402
import loop  # noqa: E402
import measure  # noqa: E402
import planning  # noqa: E402
import providers  # noqa: E402
import runrecord  # noqa: E402
import settings as settings_mod  # noqa: E402
import stopreasons as sr  # noqa: E402
import worktree  # noqa: E402

HAVE_GIT = shutil.which("git") is not None
NEEDS_GIT = unittest.skipUnless(HAVE_GIT, "git is not installed")
ID = "20261109-120000-ab12"
CREDITS = {"hasCredits": False, "unlimited": False, "balance": "0"}
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def tar_of(files, links=()):
    """A ``git archive``-shaped tar: ``files`` maps paths to text or bytes; ``links``
    holds ``(name, kind)`` pairs, kind ``sym`` or ``hard``."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for path, data in files.items():
            data = data.encode("utf-8") if isinstance(data, str) else data
            info = tarfile.TarInfo(path)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        for name, kind in links:
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE if kind == "sym" else tarfile.LNKTYPE
            info.linkname = "elsewhere"
            archive.addfile(info)
    return buffer.getvalue()


def brief_text(goal="A small change.", edit="work/"):
    return (f"# Run brief\n\n## Goal\n- {goal}\n\n## Constraints\n- None beyond the "
            "goal.\n\n## Out of scope\n- Everything else.\n\n## Acceptance checks\n"
            "- python -m py_compile work/run.py\n\n## Edit paths\n"
            f"- {edit} | Measure: git ls-files {edit}\n\n## Open questions\nNone\n\n"
            "## Source\n- `work/run.py`\n")


BASE_RUN = "VALUE = 1\n"
PUBLIC = {".gitignore": "LOG.md\n*-PLAN.md\n*-BRIEF.md\n", "AGENTS.md": "rules\n",
          "work/run.py": BASE_RUN, "work/CONTEXT.md": "# Work\n"}
PERSONAL = {"work/LOG.md": "# Work - Log\n"}


def planted_fixture(kind, value):
    definition = None
    if kind != measure.CONTROL:
        check = ({"prefix": "doc-sync:", "file": "work"} if kind == "metadata-drift"
                 else None)
        definition = {"file": "work/run.py", "catch": f"the {kind} defect",
                      "check": check}
    return {"brief": brief_text(f"{kind} change"), "definition": definition,
            "patch": [{"op": "replace", "path": "work/run.py", "search": BASE_RUN,
                       "replace": f"VALUE = {value}\n",
                       "base_blob": measure.blob_id(BASE_RUN.encode())},
                      {"op": "append", "path": "work/LOG.md",
                       "text": "[{{TIMESTAMP}}] | Actor: Biblio | Action: modified | "
                               "Note: changed.\n"}],
            "acceptance": ["python -m py_compile work/run.py"]}


def planted_set():
    return {kind: planted_fixture(kind, n + 2) for n, kind in enumerate(measure.KINDS)}


CODE_FILES = [f"s/f{n}.py" for n in range(1, 9)]
BRIEF_BYTES = b"The plan round's brief.\n"
SECTION = "## Addressed - plan readiness"
KEY = [
    {"label": "C1", "ref": "k1", "path": "p.md", "section": SECTION, "text": "C one."},
    {"label": "P1", "ref": "k1", "path": "p.md", "section": "## Addressed - process",
     "text": "P one."},
    {"label": "R1-1", "ref": "k2", "path": "q.md", "section": SECTION, "text": "R one."},
]


def replays_fixture():
    return {"note": "A FIXED NOTE.", "rounds": {
        "code": {"title": "the code round", "public_ref": "pc", "personal_ref": "pcp",
                 "brief": {"path": "CODE-BRIEF.md"}, "new_files": CODE_FILES,
                 "required_plans": ["A-PLAN.md", "B-PLAN.md"],
                 "plan_slot": "Read `A-PLAN.md` and `B-PLAN.md` as the plan.",
                 "answer_labels": ["C1", "P1"], "review_process": ["P1"],
                 "original_could_run": "the tests", "drift": "none"},
        "plan": {"title": "the plan round", "public_ref": "pp", "personal_ref": "ppp",
                 "brief": {"ref": "bref", "path": "PLAN-ROUND-BRIEF.md",
                           "sha256": hashlib.sha256(BRIEF_BYTES).hexdigest()},
                 "plan_path": "THE-PLAN.md", "answer_labels": ["R1-1"],
                 "review_process": [], "original_could_run": "the live project",
                 "drift": "two documents edited"}}}


def archives():
    code_public = {".gitignore": "*-PLAN.md\n*-BRIEF.md\n", "AGENTS.md": "rules\n",
                   **{path: f"# {path}\n" for path in CODE_FILES}}
    return {
        (measure.PLANTED_PUBLIC, False): tar_of(PUBLIC),
        (measure.PLANTED_PERSONAL, True): tar_of(PERSONAL),
        ("pc", False): tar_of(code_public),
        ("pcp", True): tar_of({"CODE-BRIEF.md": "Review the code.\n",
                               "A-PLAN.md": "plan a\n", "B-PLAN.md": "plan b\n"}),
        ("pp", False): tar_of({".gitignore": "*-PLAN.md\n", "AGENTS.md": "rules\n"}),
        ("ppp", True): tar_of({"THE-PLAN.md": "# The plan\n"}),
    }


def finding(title="A defect", file="work/run.py"):
    return {"title": title, "severity": "major", "file": file, "evidence": "e",
            "fix": "f", "reraises": ""}


def record(reply, raw=None):
    return SimpleNamespace(reply=reply, raw=raw if raw is not None else [], tokens=3,
                           refusals=[], model="m", callbacks=None, launch=None,
                           usage=None, wall_seconds=0.1, thread_id="t")


def section(prompt, start, end):
    text = prompt.split(start, 1)[1]
    return text.split(end, 1)[0] if end in text else text


def judge_reply(prompt):
    """A judge reply naming every item and finding the prompt holds: each item missed,
    each control finding a false alarm, every other finding plausible."""
    items = re.findall(r"^- (\S+?)(?::| \(under)", section(prompt, "THE ITEMS\n",
                                                            "THE FINDINGS"), re.M)
    found = re.findall(r"^- (\S+) \[", section(prompt, "THE FINDINGS\n", "\0"), re.M)
    return {"items": [{"item": i, "verdict": "missed", "match": "", "why": "w"}
                      for i in items],
            "findings": [{"finding": f, "verdict": "false alarm"
                          if f.startswith("control/") else "plausible", "why": "w"}
                         for f in found]}


class FakeSession:
    def __init__(self, world, provider, role, cwd):
        self.world, self.provider, self.role, self.cwd = world, provider, role, cwd
        self.model = "m"

    def review(self, prompt, schema):
        self.world.prompts.append((self.role, prompt))
        behaviour = self.world.behaviours.pop(0) if self.world.behaviours else None
        raw = [{"_type": "ToolUseBlock", "id": "1", "name": "Read",
                "input": {"file_path": str(Path(self.cwd) / "x.md")}}]
        if behaviour == "error":
            raise providers.ProviderError("the session failed")
        if behaviour == "void":
            raw = [{"_type": "ToolUseBlock", "id": "1", "name": "Read",
                    "input": {"file_path": str(self.world.live / "AGENTS.md")}}]
        if behaviour == "delegate":
            raw = raw + [{"type": "collabAgentToolCall", "tool": "spawnAgent", "id": "c1",
                          "prompt": "read the plan", "status": "completed"}]
        if behaviour == "limit":
            raise limits.UsageLimitError(self.provider, "weekly limit")
        if self.role == settings_mod.JUDGE:
            return record(judge_reply(prompt), raw)
        reply = {"summary": "s", "findings": [finding()]}
        if self.role == "reviewer":
            listed = re.findall(r"^- (\S+)$", section(prompt, "THE CHANGED FILES",
                                                      "FILES THE ORCHESTRATOR"), re.M)
            listed += re.findall(r"`([^`]+-PLAN\.md)`", prompt)
            reply["files_reviewed"] = listed
        return record(reply, raw)


class FakeWorld(measure.World):
    """Every boundary but git in the test's own temporary repositories."""

    def __init__(self, base):
        super().__init__(base / "live")
        self.base = Path(base)
        self.live = self.base / "live"
        self.live.mkdir(parents=True, exist_ok=True)
        self.archives = archives()
        self.shown = {("bref", "PLAN-ROUND-BRIEF.md"): BRIEF_BYTES,
                      ("k1", "p.md"): (f"{SECTION}\n- C one.\n## Addressed - process\n"
                                       "- P one.\n").encode(),
                      ("k2", "q.md"): f"{SECTION}\n- R one.\n".encode()}
        self.calls, self.prompts, self.behaviours = [], [], []
        self.image = True
        self.head_value = "c0"
        self.codex = limits.Reading(limits.CODEX, limits.REPORTED, percent=10.0,
                                    raw={"rateLimits": {"credits": dict(CREDITS)}})
        self.probe = SimpleNamespace(events=["event"], tokens=5, model="probe",
                                     reading=limits.Reading(limits.CLAUDE, limits.REPORTED,
                                                            percent=10.0))
        self.checks = {}
        self.dead = []

    def archive(self, ref, git_dir=None):
        self.calls.append(("archive", ref))
        return self.archives[(ref, git_dir is not None)]

    def show(self, ref, path, git_dir=None):
        return self.shown[(ref, path)]

    def head(self):
        return self.head_value

    def has_commit(self, ref, git_dir=None):
        return True

    def docker_up(self):
        self.calls.append("docker_up")
        return False

    def image_exists(self, tag):
        self.calls.append(("image", tag))
        return self.image

    def read_codex(self, folder):
        self.calls.append("read_codex")
        return self.codex

    def probe_claude(self, model, cwd, on_event):
        self.calls.append("probe")
        return self.probe

    def session(self, settings, provider, role, cwd, usage, frozen_dir):
        self.calls.append(("session", provider, role))
        return FakeSession(self, provider, role, cwd)

    def run_checks(self, copy, edit_paths, directories, commands, **kwargs):
        where = Path(kwargs["round_dir"])
        self.calls.append(("checks", where.name, where.parent.name))
        return self.checks.get((where.parent.name, where.name), ([], "checks: clean"))

    def dead_links(self, copy, trusted):
        self.calls.append("dead_links")
        return self.dead.pop(0) if self.dead else []

    def category_a(self, copy):
        return set()

    def start_ignored(self, copy):
        return []

    def now(self):
        return NOW

    def scratch(self, prefix):
        return Path(tempfile.mkdtemp(prefix=prefix, dir=self.base))


class Base(unittest.TestCase):

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name).resolve()
        self.world = FakeWorld(self.base)
        self.frozen = self.base / "frozen"
        (self.frozen / "trusted").mkdir(parents=True)

    def measurement(self, planted=None, replays=None, progress=None):
        folder = self.base / "runs" / f"measure-{ID}"
        folder.mkdir(parents=True, exist_ok=True)
        git_dir = self.base / "personal.git"
        progress = progress or {
            "id": ID, "commit": "c0", "git_dir": str(git_dir),
            "frozen_dir": str(self.frozen), "status": measure.RUNNING, "stop": None,
            "reviewers": [{"label": "sol", "provider": "codex", "model": "gpt-6-sol"},
                          {"label": "opus", "provider": "claude",
                           "model": "claude-opus-5-5"}],
            "scan_targets": measure.scan_targets(self.world.live, git_dir),
            "sessions": {}, "codex_start_credits": dict(CREDITS), "credits_read": True}
        return measure.Measurement(
            folder, progress, settings_mod.load_settings(), root=self.world.live,
            frozen=self.frozen, world=self.world,
            fixtures={"replays": replays or replays_fixture(),
                      "planted": planted or planted_set()},
            key_entries=KEY, log_path=self.base / "LOG.md")


# ------------------------------------------------- the process table (plan step 2)


class ProcessTableTests(unittest.TestCase):

    def table(self):
        text = (SCRIPTS / "CONTEXT.md").read_text(encoding="utf-8")
        return {int(m.group(1)): m.group(0) for m in
                re.finditer(r"^\| (\d+) \|.*$", text, re.M)}

    def test_positive_rows_20_to_23_and_row_14_copy_commands(self):
        rows = self.table()
        for number in (20, 21, 22, 23):
            self.assertIn(number, rows)
        self.assertIn("measure.py", rows[20])
        self.assertIn("restored copy", rows[21])
        self.assertIn("empty temporary folder", rows[22])
        self.assertIn("placeholders", rows[23])
        for word in ("`init`", "`add`", "`commit`", "empty hook path", "no template"):
            self.assertIn(word, rows[14])

    def test_positive_every_process_measure_starts_maps_to_a_row(self):
        # Every World method that starts a process, or calls the module that does,
        # and the row it is under.
        rows = {"_git": 14, "ignored": 14, "copy_git": 14, "hand_over": 20,
                "docker_up": 5, "docker_down": 5, "image_exists": 5, "read_codex": 13,
                "probe_claude": 15, "session": 21, "run_checks": 7, "dead_links": 23,
                "category_a": 14, "start_ignored": 14, "remove": 14}
        tree = ast.parse((SCRIPTS / "measure.py").read_text(encoding="utf-8"))
        world = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                     and n.name == "World")
        starters = set()
        for node in world.body:
            if isinstance(node, ast.FunctionDef):
                text = ast.unparse(node)
                if re.search(r"subprocess\.run|container\.|checks_mod\.|loop\._read|"
                             r"providers\.|worktree\.category_a", text):
                    starters.add(node.name)
        self.assertTrue(starters, "the World methods were not found")
        self.assertEqual(starters - set(rows), set())
        self.assertTrue(set(rows.values()) <= set(self.table()))
        outside = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)
                   and "subprocess.run" in ast.unparse(n)]
        self.assertEqual(outside, [])


# ------------------------------------------------------ the judge's settings (3)


class JudgeSettingsTests(unittest.TestCase):

    def write(self, data):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "settings.json"
        path.write_text(json.dumps(data), encoding="utf-8", newline="\n")
        return settings_mod.load_settings(path)

    def test_positive_the_judge_defaults_are_each_reviewers_model_at_high(self):
        loaded = settings_mod.load_settings()
        for provider in ("claude", "codex"):
            judge = settings_mod.model_for(loaded, provider, "judge")
            self.assertEqual(judge["effort"], "high")
            self.assertEqual(judge["model"],
                             settings_mod.model_for(loaded, provider, "reviewer")["model"])
        self.assertEqual(loaded["models"], settings_mod.DEFAULT_MODELS)

    def test_positive_an_overridden_judge_model_is_kept(self):
        loaded = self.write({"models": {"codex": {"judge": {"model": "gpt-6-astra",
                                                            "effort": "high"}}}})
        self.assertEqual(loaded["models"]["codex"]["judge"]["model"], "gpt-6-astra")

    def test_rejection_an_invalid_judge_effort(self):
        with self.assertRaisesRegex(settings_mod.SettingsError,
                                    r"models\.claude\.judge\.effort"):
            self.write({"models": {"claude": {"judge": {"model": "x", "effort": "ultra"}}}})

    def test_rejection_a_measurement_without_a_judge_entry(self):
        loaded = settings_mod.load_settings()
        del loaded["models"]["claude"]["judge"]
        with self.assertRaisesRegex(measure.Refused, r"models\.claude\.judge"):
            measure.check_settings(loaded)

    def test_negative_an_ordinary_run_does_not_ask_for_the_judge(self):
        loaded = settings_mod.load_settings()
        for provider in ("claude", "codex"):
            del loaded["models"][provider]["judge"]
        for builder in ("claude", "codex"):
            settings_mod.assign_roles(loaded, builder)


# ----------------------------------------------------- the shared steps (4)


class Session:
    def __init__(self, provider, replies):
        self.provider, self.replies, self.calls = provider, list(replies), []

    def review(self, prompt, schema):
        self.calls.append((prompt, schema))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return record(reply)


def review_values(**extra):
    values = dict(round=1, brief="B", plan=loop.NO_PLAN, mechanical="None.", prior="",
                  diff="(empty)", changed_files="- a.py", check_written="None.",
                  reviewer_tools="T")
    values.update(extra)
    return values


class SharedStepTests(unittest.TestCase):

    def run_step(self, session, *, required=(), state=None, order=None):
        order = [] if order is None else order
        state = {} if state is None else state
        return loop.review_step(
            session, review_values(), ledger=sr.Ledger(), state=state,
            save=lambda: order.append("save"), changed_files=["a.py"], required=required,
            before_call=lambda: order.append("before"),
            after_call=lambda r: order.append("after"),
            on_failure=lambda: order.append("failure")), order, state

    def test_positive_the_three_callers_reach_the_shared_functions(self):
        tree = ast.parse((SCRIPTS / "loop.py").read_text(encoding="utf-8"))
        run = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Run")
        review = next(n for n in run.body if isinstance(n, ast.FunctionDef)
                      and n.name == "review")
        self.assertIn("review_step(", ast.unparse(review))
        tree = ast.parse((SCRIPTS / "planning.py").read_text(encoding="utf-8"))
        rounds = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                      and n.name == "_review_round")
        self.assertIn("loop.plan_review_step", ast.unparse(rounds))
        source = (SCRIPTS / "measure.py").read_text(encoding="utf-8")
        self.assertIn("loop.review_step(", source)
        self.assertIn("loop.plan_review_step(", source)

    def test_positive_the_order_of_the_callbacks(self):
        session = Session(limits.CODEX, [{"summary": "s", "findings": [finding()],
                                          "files_reviewed": ["a.py"]}])
        (_record, findings), order, state = self.run_step(session)
        self.assertEqual(order, ["before", "after"])
        self.assertEqual(len(findings), 1)
        prompt, schema = session.calls[0]
        self.assertIn("files_reviewed", schema["required"])
        self.assertIn("pattern", schema["properties"]["findings"]["items"]["properties"][
            "title"])
        self.assertEqual(state["live"]["title_pattern"], "accepted")
        self.assertIn("None: this run has no approved plan.", prompt)

    def test_rejection_a_missing_required_file_is_named(self):
        session = Session(limits.CODEX, [{"summary": "s", "findings": [],
                                          "files_reviewed": ["a.py", "A-PLAN.md"]}])
        with self.assertRaisesRegex(sr.ReplyError, "required files.*B-PLAN.md"):
            self.run_step(session, required=["A-PLAN.md", "B-PLAN.md"])

    def test_rejection_a_parse_error_comes_after_the_after_call(self):
        session = Session(limits.CODEX, [{"summary": "s", "findings": [{"bad": 1}],
                                          "files_reviewed": ["a.py"]}])
        order = []
        with self.assertRaises(sr.ReplyError):
            self.run_step(session, order=order)
        self.assertEqual(order, ["before", "after"])

    def test_rejection_a_failed_call_runs_on_failure_only(self):
        session = Session(limits.CODEX, [providers.ProviderError("down")])
        order = []
        with self.assertRaises(providers.ProviderError):
            self.run_step(session, order=order)
        self.assertEqual(order, ["before", "failure"])

    def test_positive_the_title_pattern_rule(self):
        claude = Session(limits.CLAUDE, [{"summary": "s", "findings": [],
                                          "files_reviewed": ["a.py"]}])
        _, _, state = self.run_step(claude)
        self.assertEqual(state["live"]["title_pattern"], "not sent")
        self.assertNotIn("pattern", claude.calls[0][1]["properties"]["findings"]["items"][
            "properties"]["title"])
        codex = Session(limits.CODEX, [providers.ProviderError("bad pattern"),
                                       {"summary": "s", "findings": [],
                                        "files_reviewed": ["a.py"]}])
        _, order, state = self.run_step(codex)
        self.assertTrue(state["live"]["title_pattern"].startswith("refused"))
        self.assertIn("save", order)

    def test_positive_the_plan_review_step_never_records_not_sent(self):
        session = Session(limits.CLAUDE, [{"summary": "s", "findings": [finding()]}])
        state = {}
        _, findings = loop.plan_review_step(
            session, dict(round=1, brief="B", plan="P", prior="None."),
            ledger=sr.Ledger(), state=state, save=lambda: None)
        self.assertEqual(len(findings), 1)
        self.assertNotIn("title_pattern", state.get("live", {}))
        self.assertNotIn("files_reviewed", session.calls[0][1]["required"])
        self.assertIs(planning.plan_review_schema, loop.plan_review_schema)

    def test_positive_no_plan_is_the_loops_text(self):
        self.assertEqual(loop.Run.approved_plan(SimpleNamespace(state={})), loop.NO_PLAN)
        self.assertEqual(loop.NO_PLAN, "None: this run has no approved plan.")


# -------------------------------------------------- the supporting parts (5)


class JudgeReplyTests(unittest.TestCase):

    def parse(self, reply, items=None, findings=("N1", "N2"), control=()):
        return sr.parse_judge_reply(reply, items or {"R1-1": None}, findings, control)

    def good(self):
        return {"items": [{"item": "R1-1", "verdict": "caught", "match": "N1: the quote",
                           "why": "same fault"}],
                "findings": [{"finding": "N2", "verdict": "plausible", "why": "w"}]}

    def test_positive_a_good_reply(self):
        parsed = self.parse(self.good())
        self.assertEqual(parsed["matched"], {"N1": "R1-1"})
        self.assertEqual(list(parsed["findings"]), ["N2"])

    def test_rejection_each_malformed_reply(self):
        missed = {"item": "R1-1", "verdict": "missed", "match": "", "why": "w"}
        cases = [
            ({"items": []}, "exactly: items, findings"),
            ({"items": [], "findings": []}, "leaves out item"),
            ({"items": [{"item": "R1-1", "verdict": "caught", "match": "the quote",
                         "why": "w"}], "findings": []}, "not written"),
            ({"items": [missed],
              "findings": [{"finding": "N1", "verdict": "plausible", "why": "w"}]},
             "leaves out finding"),
            ({"items": [{"item": "R1-1", "verdict": "caught", "match": "N1: q",
                         "why": "w"}],
              "findings": [{"finding": "N1", "verdict": "plausible", "why": "w"},
                           {"finding": "N2", "verdict": "plausible", "why": "w"}]},
             "not a new finding left unmatched"),
            ({"items": [dict(missed, item="R9")], "findings": []}, "not given"),
            ({"items": [dict(missed, verdict="maybe")], "findings": []}, "verdict"),
            ({"items": [missed],
              "findings": [{"finding": "N1", "verdict": "false alarm", "why": "w"},
                           {"finding": "N2", "verdict": "plausible", "why": "w"}]},
             "control is a false alarm"),
        ]
        for reply, reason in cases:
            with self.subTest(reason):
                with self.assertRaisesRegex(sr.ReplyError, reason):
                    self.parse(reply)

    def test_rejection_a_control_finding_not_marked_a_false_alarm(self):
        reply = {"items": [{"item": "a", "verdict": "missed", "match": "", "why": "w"}],
                 "findings": [{"finding": "control/N1", "verdict": "plausible",
                               "why": "w"}]}
        with self.assertRaisesRegex(sr.ReplyError, "control is a false alarm"):
            self.parse(reply, {"a": []}, ["control/N1"], ["control/N1"])

    def test_rejection_a_match_from_another_change(self):
        reply = {"items": [{"item": "a", "verdict": "caught", "match": "b/N1: q",
                            "why": "w"}], "findings": []}
        with self.assertRaisesRegex(sr.ReplyError, "not a finding it could match"):
            self.parse(reply, {"a": ["a/N1"]}, ["b/N1"])


class RecordAndFloorTests(unittest.TestCase):

    def test_positive_the_measurement_folder_and_a_clash(self):
        with tempfile.TemporaryDirectory() as folder:
            made = runrecord.create_measure(ID, runs_dir=folder)
            self.assertEqual(made.name, f"measure-{ID}")
            with self.assertRaisesRegex(FileExistsError, "already exists"):
                runrecord.create_measure(ID, runs_dir=folder)
            with self.assertRaisesRegex(ValueError, "not a measurement id"):
                runrecord.measure_dir("../x", runs_dir=folder)
            runrecord.write_json_atomic(made / "p.json", {"a": "\u00e9"})
            self.assertEqual(json.loads((made / "p.json").read_text(encoding="utf-8")),
                             {"a": "\u00e9"})
            self.assertFalse((made / "p.json.tmp").exists())

    def floor_run(self, floor):
        seen = {}
        container = checks_mod.container
        saved = {name: getattr(container, name) for name in
                 ("read_start_ignored", "next_copy", "make_copy", "run_in_container",
                  "remove_copy")}
        container.read_start_ignored = lambda path: seen.setdefault("read", ["x/"])
        container.next_copy = lambda run_id: Path("copy")
        container.make_copy = lambda root, dest, head, used: seen.setdefault("floor",
                                                                             used)
        container.run_in_container = lambda dest, argv, image: (
            0, json.dumps({"status": "pass", "gates": []}), "")
        container.remove_copy = lambda dest: None
        try:
            checks_mod.container_checks("root", [], run_id="r", frozen_dir="f",
                                        public_head="h", image="i", floor=floor)
        finally:
            for name, value in saved.items():
                setattr(container, name, value)
        return seen

    def test_positive_the_ignore_floor_default_and_override(self):
        self.assertEqual(self.floor_run(None), {"read": ["x/"], "floor": ["x/"]})
        self.assertEqual(self.floor_run(["LOG.md"]), {"floor": ["LOG.md"]})


# ------------------------------------------------------- the judge prompt (6)


def load_shipped_planted():
    return measure.load_planted(FIXTURES)


class JudgePromptTests(Base):

    def finished(self, m):
        for reviewer in m.reviewers():
            label = reviewer["label"]
            for name in ("code", "plan", *measure.KINDS):
                result = {"findings": [finding()], "mechanical": [], "placeholders": []}
                m.progress["sessions"][f"{label}/{name}"] = {"status": measure.DONE,
                                                             "attempts": [],
                                                             "result": result}
        return m

    def test_positive_both_prompts_render_and_name_no_producer(self):
        m = self.finished(self.measurement(planted=load_shipped_planted()))
        settings = settings_mod.load_settings()
        rules = planning.identity_rules(planning.load_identifiers(),
                                        planning.model_names(settings))
        for name in ("judge-code", "judge-plan", "judge-planted"):
            with self.subTest(name):
                material = measure.judge_material(m, name, m.reviewers()[0])
                prompt = material["prompt"]
                self.assertNotIn("$", prompt)
                for word in ("sol", "opus"):
                    self.assertIsNone(re.search(rf"(?<!\w){word}(?!\w)", prompt,
                                                re.IGNORECASE), word)
                hits = planning.check_identity(prompt, rules, planning.KnownPaths([]))
                self.assertEqual([h["text"] for h in hits], [])
        planted = measure.judge_material(m, "judge-planted", m.reviewers()[0])
        self.assertIn("Every finding made on the control is `false alarm`",
                      planted["prompt"])
        self.assertEqual(planted["control"], ["control/N1"])
        self.assertIn("WHAT COUNTS", planted["prompt"])

    def test_positive_a_mechanically_caught_defect_is_not_put_to_the_judge(self):
        m = self.finished(self.measurement())
        entry = m.progress["sessions"]["sol/metadata-drift"]
        entry["result"]["mechanical"] = [{"title": "doc-sync: work: CONTEXT.md not "
                                          "updated", "file": "work"}]
        material = measure.judge_material(m, "judge-planted", m.reviewers()[0])
        self.assertNotIn("metadata-drift", material["items"])
        self.assertIn("logic-error", material["items"])


# ---------------------------------------------------------- the fixtures (7)


class FixtureTests(unittest.TestCase):

    def test_positive_a_flat_folder_with_exactly_the_expected_files(self):
        names = {p.name for p in FIXTURES.iterdir()} - {"__pycache__"}
        self.assertFalse([p for p in FIXTURES.iterdir() if p.is_dir()
                          and p.name != "__pycache__"])
        expected = {"CONTEXT.md", "LOG.md", "replays.json",
                    *(f"planted-{k}.json" for k in measure.KINDS)}
        # LOG.md is gitignored, so a clean copy holds the rest only.
        self.assertTrue(names - {"LOG.md"} == expected - {"LOG.md"}, names)

    @NEEDS_GIT
    def test_positive_no_fixture_name_matches_a_design_time_pattern(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            subprocess.run(["git", "init", "-q", str(repo)], check=True,
                           capture_output=True)
            (repo / ".gitignore").write_text(
                "*-PLAN.md\n*-BRIEF.md\nHANDOVER.md\nPROPOSAL.md\nROADMAP.md\n",
                encoding="utf-8", newline="\n")
            names = ["replays.json", *(f"planted-{k}.json" for k in measure.KINDS)]
            for name in names:
                (repo / name).write_text("{}", encoding="utf-8", newline="\n")
            done = subprocess.run(["git", "check-ignore", "--", *names], cwd=repo,
                                  capture_output=True, encoding="utf-8")
            self.assertEqual(done.stdout.strip(), "")
            self.assertEqual(done.returncode, 1, "check-ignore ignores none of them")
            (repo / "SOME-PLAN.md").write_text("x", encoding="utf-8", newline="\n")
            control = subprocess.run(["git", "check-ignore", "-q", "SOME-PLAN.md"],
                                     cwd=repo, capture_output=True)
            self.assertEqual(control.returncode, 0, "the positive control is ignored")

    def test_positive_seven_kinds_in_shape(self):
        planted = load_shipped_planted()
        self.assertEqual(set(planted), set(measure.KINDS))
        self.assertIsNone(planted["control"]["definition"])
        for kind, data in planted.items():
            self.assertEqual(measure.check_fixture(kind, data), [], kind)
        drift = planted["metadata-drift"]["definition"]["check"]
        self.assertEqual(drift, {"prefix": "doc-sync:",
                                 "file": "workflows/weather/scripts"})

    def test_positive_every_replace_has_a_blob_and_targets_a_public_file(self):
        for kind, data in load_shipped_planted().items():
            for op in data["patch"]:
                with self.subTest(kind=kind, path=op["path"]):
                    if op["op"] == "replace":
                        # A real blob id, or the unvalidated placeholder, which the
                        # harness's pre-session check refuses by name.
                        self.assertRegex(op["base_blob"], r"^[0-9a-f]{40}$")
                    if op["op"] in ("replace", "new"):
                        self.assertFalse(measure.is_personal_name(op["path"]))
                    if op["path"].endswith("LOG.md"):
                        self.assertEqual(op["op"], "append")
                        self.assertIn(measure.TIMESTAMP_SLOT, op["text"])
                    self.assertNotIn(op["path"], measure.CHANGED_SINCE_REF)
                    self.assertFalse(op["path"].startswith(measure.RUNS_TREE))

    def test_positive_each_brief_passes_the_checker_and_its_readers(self):
        for kind, data in load_shipped_planted().items():
            with self.subTest(kind):
                with tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    sources = re.findall(r"^- `([^`]+)`$", data["brief"], re.M)
                    self.assertTrue(sources)
                    for path in sources:
                        (root / path).parent.mkdir(parents=True, exist_ok=True)
                        (root / path).write_text("x", encoding="utf-8", newline="\n")
                    headings, problems = brief_mod.check_text(data["brief"], root)
                    self.assertEqual((headings, problems), ([], []))
                self.assertTrue(brief_mod.brief_edit_paths(data["brief"]))
                self.assertEqual(brief_mod.brief_acceptance(data["brief"]),
                                 data["acceptance"])
                self.assertTrue(brief_mod.intent_lines(data["brief"]))

    def test_positive_every_patched_path_is_inside_its_edit_paths(self):
        for kind, data in load_shipped_planted().items():
            edit_paths = brief_mod.brief_edit_paths(data["brief"])
            for op in data["patch"]:
                with self.subTest(kind=kind, path=op["path"]):
                    self.assertTrue(worktree.under_edit_paths(op["path"], edit_paths))

    def test_positive_each_patch_applies_to_its_own_expected_context(self):
        for kind, data in load_shipped_planted().items():
            with self.subTest(kind):
                files = {}
                for op in data["patch"]:
                    if op["op"] == "replace":
                        files[op["path"]] = files.get(op["path"], "") + \
                            "\n# context\n" + op["search"]
                    elif op["op"] == "append":
                        files.setdefault(op["path"], "# Log\n")
                changed = measure.apply_ops(
                    lambda p: files[p].encode() if p in files else None, data["patch"],
                    "2026-11-09T12:00:00+00:00")
                appended = [p for p in changed if p.endswith("LOG.md")]
                for path in appended:
                    self.assertIn("[2026-11-09T12:00:00+00:00]", changed[path].decode())
                self.assertEqual(appended, list(changed)[len(changed) - len(appended):],
                                 "every append is written last")

    def test_positive_the_replays_fixture_has_every_field(self):
        data = measure.load_replays(FIXTURES)
        code, plan = data["rounds"]["code"], data["rounds"]["plan"]
        self.assertEqual(len(code["new_files"]), 8)
        self.assertEqual(code["required_plans"], ["SYNC-ARCHITECTURE-PLAN.md",
                                                  "SHADOW-REPOSITORY-PLAN.md"])
        self.assertEqual(plan["brief"]["sha256"], "5388c1fd76a81cc9859e61353ef9983fd6c4"
                                                  "d84305690356f6da5eebe92b94f5")
        self.assertEqual(code["answer_labels"], ["C1", "P1"])
        self.assertEqual(len(plan["answer_labels"]), 11)
        self.assertEqual(plan["review_process"], ["R1-11"])
        for field in measure.REPLAY_FIELDS:
            self.assertIn(field, code)
            self.assertIn(field, plan)

    def test_rejection_a_fixture_out_of_shape(self):
        data = planted_fixture("logic-error", 2)
        cases = [
            (dict(data, definition=None), "definition must hold"),
            (dict(data, patch=[{"op": "replace", "path": "work/LOG.md", "search": "a",
                                "replace": "b", "base_blob": "0" * 40}]),
             "only by an append"),
            (dict(data, patch=[{"op": "new", "path": measure.CHANGED_SINCE_REF[0],
                                "text": "x"}]), "changed after"),
            (dict(data, patch=[{"op": "new", "path": measure.RUNS_TREE + "x",
                                "text": "x"}]), "is under"),
            (dict(data, patch=[{"op": "replace", "path": "a", "search": "a",
                                "replace": "b"}]), "base_blob"),
        ]
        for fixture, reason in cases:
            with self.subTest(reason):
                self.assertTrue(any(reason in p for p in
                                    measure.check_fixture("logic-error", fixture)))
        self.assertTrue(measure.check_fixture("control", planted_fixture("logic-error",
                                                                         2)))


class PreSessionCheckTests(Base):

    def test_positive_a_matching_set_passes(self):
        measure.check_planted(self.world, "g", planted_set())

    def test_rejection_a_blob_that_differs_and_a_patch_that_does_not_apply(self):
        wrong_blob = planted_set()
        wrong_blob["logic-error"]["patch"][0]["base_blob"] = "1" * 40
        with self.assertRaisesRegex(measure.Refused, "logic-error: work/run.py is not "
                                                     "the blob"):
            measure.check_planted(self.world, "g", wrong_blob)
        no_match = planted_set()
        no_match["control"]["patch"][0]["search"] = "VALUE = 9\n"
        with self.assertRaisesRegex(measure.Refused, "control: its patch does not apply"):
            measure.check_planted(self.world, "g", no_match)
        missing = planted_set()
        missing["wrong-test"]["patch"][1]["path"] = "work/NOPE/LOG.md"
        with self.assertRaisesRegex(measure.Refused, "missing at the personal ref"):
            measure.check_planted(self.world, "g", missing)
        self.assertNotIn("read_codex", self.world.calls)

    def test_rejection_an_unvalidated_placeholder_blob_is_named(self):
        placeholder = planted_set()
        placeholder["control"]["patch"][0]["base_blob"] = measure.UNVALIDATED_BLOB
        with self.assertRaisesRegex(measure.Refused, "has not been validated against "
                                                     "the fixed public ref"):
            measure.check_planted(self.world, "g", placeholder)
        self.assertEqual(measure.unvalidated(placeholder),
                         ["control: work/run.py"])
        self.assertEqual(measure.unvalidated(planted_set()), [])


# ---------------------------------------------------------- entry and state (8)


class EntryTests(Base):

    def test_positive_the_marker_gate(self):
        self.assertIsNone(measure.needs_starter(["--measure"], {measure.STARTER_MARKER:
                                                                "1"}))
        message = measure.needs_starter(["--measure", "--score", ID], {})
        self.assertIn(f"{measure.STARTER} --measure --score {ID}", message)
        self.assertIsNone(measure.needs_starter([measure.ADVANCE, ID], {},
                                                frozen=Path("x") / ID))
        self.assertIsNotNone(measure.needs_starter(["--measure", "--score", ID], {},
                                                   frozen=Path("x") / ID))
        self.assertEqual(measure.main(["--measure", "--score", ID], environ={},
                                      world=self.world, frozen=None), 2)

    def test_rejection_the_live_copys_refusals(self):
        env = {measure.STARTER_MARKER: "1"}
        for argv in (["--measure", "--resume", ID], ["--measure", "--score", ID]):
            with self.subTest(argv):
                self.assertEqual(measure.main(argv, environ=env, world=self.world,
                                              frozen=None), measure.REFUSED)
        for argv in (["--measure", "--allow-paid"], ["--measure", "--output", "x"],
                     [measure.ADVANCE, ID], ["--resume", ID]):
            with self.subTest(argv):
                self.assertEqual(measure.main(argv, environ=env, world=self.world,
                                              frozen=None), 2)
        self.assertEqual(self.world.calls, [])

    def test_positive_import_has_no_side_effects(self):
        with tempfile.TemporaryDirectory() as folder:
            code = ("import sys; sys.path.insert(0, sys.argv[1]); import measure; "
                    "print(measure._FROZEN)")
            done = subprocess.run([sys.executable, "-c", code, str(SCRIPTS)], cwd=folder,
                                  capture_output=True, encoding="utf-8", timeout=120,
                                  env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(done.stdout.strip(), "None")
            self.assertEqual(os.listdir(folder), [])

    def test_positive_the_layout_agrees_with_run_py(self):
        spec = importlib.util.spec_from_file_location("_run_layout", SCRIPTS / "run.py")
        run = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(run)
        for name in ("REFUSED", "ROOT_ENV", "FROZEN_PARENT", "STARTER_PATH",
                     "STARTER_MARKER", "FIRE_LOG_ENV", "RUNLOGS_PARENT", "REEXEC_MARKER"):
            self.assertEqual(getattr(measure, name), getattr(run, name), name)
        self.assertEqual(measure.STARTER_PATH, brief_mod.STARTER_PATH)
        folder = Path("C:/t") / measure.FROZEN_PARENT / ID
        script = folder / "trusted" / "workflows" / "review-orchestration" / "scripts"
        self.assertEqual(measure.trusted_frozen_folder(script / "measure.py"), folder)
        self.assertEqual(run.trusted_frozen_folder(script / "run.py"), folder)
        self.assertIsNone(measure.trusted_frozen_folder(script / "run.py"))
        self.assertIsNone(measure.trusted_frozen_folder(SCRIPTS / "measure.py"))

    def test_rejection_a_missing_image_starts_nothing(self):
        self.world.image = False
        m = self.measurement()
        with self.assertRaisesRegex(measure.Refused, "bookdragon-checks:211a000e7364 is "
                                                     "missing"):
            measure.advance(m, resumed=False)
        self.assertFalse([c for c in self.world.calls if c in ("read_codex", "probe")
                          or (isinstance(c, tuple) and c[0] in ("session", "archive"))])

    def test_rejection_a_wrong_blob_refuses_before_any_reading(self):
        planted = planted_set()
        planted["scope-breach"]["patch"][0]["base_blob"] = "2" * 40
        m = self.measurement(planted=planted)
        with self.assertRaisesRegex(measure.Refused, "scope-breach"):
            measure.advance(m, resumed=False)
        self.assertNotIn("read_codex", self.world.calls)
        self.assertNotIn("probe", self.world.calls)

    def log_lines(self):
        path = self.base / "LOG.md"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def test_positive_a_refusal_before_any_session_is_logged_and_recorded(self):
        # Code review R7-2: a fresh start's trusted copy and a resume each end the run
        # they logged as started with a failed entry, and the record says stopped.
        for how in ("fresh", "resume"):
            with self.subTest(how=how):
                self.setUp()
                self.world.image = False
                m = self.measurement()
                with self.assertRaises(measure.Refused):
                    if how == "fresh":
                        measure.advance(m, resumed=False)
                    else:
                        m.progress["status"] = measure.PAUSED
                        measure.resume(m)
                saved = measure.load_json(m.folder / measure.PROGRESS)
                self.assertEqual(saved["status"], measure.STOPPED)
                self.assertIn("is missing", saved["stop"]["detail"])
                lines = self.log_lines()
                self.assertIn("Action: failed", lines[-1])
                self.assertIn("stopped before any session", lines[-1])
                if how == "resume":
                    self.assertIn("Action: started", lines[-2])

    def test_positive_docker_failing_to_start_is_logged_and_stops_nothing(self):
        # Code review R8-1: Docker's start is one of the checks before any session.
        for how in ("fresh", "resume"):
            with self.subTest(how=how):
                self.setUp()

                def broken():
                    self.world.calls.append("docker_up")
                    raise RuntimeError("Docker could not start")
                self.world.docker_up = broken
                self.world.docker_down = lambda: self.world.calls.append("docker_down")
                m = self.measurement()
                with self.assertRaisesRegex(RuntimeError, "could not start"):
                    if how == "fresh":
                        measure.advance(m, resumed=False)
                    else:
                        m.progress["status"] = measure.PAUSED
                        measure.resume(m)
                self.assertEqual(m.progress["status"], measure.STOPPED)
                lines = self.log_lines()
                self.assertIn("Action: failed", lines[-1])
                self.assertIn("Docker could not start", lines[-1])
                if how == "resume":
                    self.assertIn("Action: started", lines[-2])
                self.assertNotIn("docker_down", self.world.calls,
                                 "nothing was started to stop")

    def test_positive_a_failed_credit_reading_before_any_session_is_logged(self):
        def broken(frozen):
            raise RuntimeError("no reading")
        self.world.read_codex = broken
        m = self.measurement()
        m.progress["credits_read"] = False  # the first start, before its reading
        with self.assertRaisesRegex(RuntimeError, "no reading"):
            measure.advance(m, resumed=False)
        self.assertEqual(m.progress["status"], measure.STOPPED)
        self.assertIn("Action: failed", self.log_lines()[-1])
        self.assertIn("RuntimeError: no reading", self.log_lines()[-1])

    def test_rejection_a_resume_at_another_commit(self):
        m = self.measurement()
        m.progress["status"] = measure.PAUSED
        self.world.head_value = "c9"
        with self.assertRaisesRegex(measure.Refused, "started at commit c0, but the "
                                                     "project is at c9; return to c0"):
            measure.resume(m)
        self.assertEqual(self.world.calls, [])

    def test_rejection_a_second_claim(self):
        folder = self.base / "claim"
        folder.mkdir()
        lock = measure.claim(folder)
        with self.assertRaisesRegex(measure.Refused, "resume.lock exists"):
            measure.claim(folder)
        measure.release(lock)
        measure.release(measure.claim(folder))


class AnswerKeyTests(unittest.TestCase):

    def write_key(self, data):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / measure.KEY_PATH
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(data), encoding="utf-8", newline="\n")
        return Path(folder.name)

    def test_rejection_every_other_shape(self):
        entry = dict(KEY[0])
        cases = [[entry], {"entries": []}, {"entries": [entry], "extra": 1},
                 {"entries": [dict(entry, extra="x")]},
                 {"entries": [{k: v for k, v in entry.items() if k != "text"}]},
                 {"entries": [dict(entry, text="")]},
                 {"entries": [dict(entry, label=3)]}, {"entries": [entry, entry]}]
        for data in cases:
            with self.subTest(data=data):
                with self.assertRaises(measure.Refused):
                    measure.load_answer_key(self.write_key(data))
        self.assertEqual(measure.load_answer_key(self.write_key({"entries": KEY})), KEY)

    def test_positive_the_whole_line_heading_and_where_a_substring_lands(self):
        longer = "## Addressed - plan readiness, the replay set and the identity check"
        packet = (f"# Packet\n{longer}\n- First finding.\n### A sub-heading\n- Still "
                  f"first.\n## Open\n- Open one.\n{SECTION}\n- The later finding.\n"
                  "## Addressed - review process\n- Process one.\n")
        entry = {"label": "R1-2", "ref": "r", "path": "p", "section": SECTION,
                 "text": "- The later finding."}
        self.assertEqual(measure.confirm_key([entry], lambda *a: packet.encode(), "g"),
                         [])
        # A substring match lands on the first, longer heading, whose section does not
        # hold the entry.
        lines = packet.splitlines()
        wrong = next(i for i, line in enumerate(lines) if SECTION in line)
        self.assertEqual(lines[wrong], longer)
        self.assertNotIn(entry["text"], "\n".join(lines[wrong:wrong + 4]))
        # A deeper heading stays inside the section; a same-level one ends it.
        self.assertTrue(measure.contains(packet, longer, "- Still first."))
        self.assertFalse(measure.contains(packet, longer, "- Open one."))
        self.assertFalse(measure.contains(packet, SECTION, "- Process one."))
        bad = dict(entry, text="- First finding.")
        self.assertTrue(measure.confirm_key([bad], lambda *a: packet.encode(), "g"))
        missing = dict(entry, section="## Addressed - nothing")
        self.assertIn("no line", measure.confirm_key([missing],
                                                     lambda *a: packet.encode(), "g")[0])

    def test_positive_the_shipped_key_against_its_sources(self):
        root = WORKFLOW.parent.parent
        key = root / measure.KEY_PATH
        git_dir = Path(measure.PERSONAL_REPO)
        if not HAVE_GIT or not key.is_file() or not git_dir.is_dir():
            reason = ("git is not installed" if not HAVE_GIT else
                      "the answer key is absent" if not key.is_file() else
                      f"the personal repository {git_dir.as_posix()} is absent")
            print(f"\nskipped: the shipped-key test: {reason}")
            self.skipTest(reason)
        entries = measure.load_answer_key(root)
        world = measure.World(root)
        self.assertEqual(measure.confirm_key(entries, world.show, str(git_dir)), [])
        self.assertEqual({e["label"] for e in entries},
                         {"C1", "P1", *(f"R1-{n}" for n in range(1, 12))})


# ---------------------------------------------------------- restores (9)


class RestoreTests(Base):

    def test_rejection_paths_in_both_archives_links_and_escapes(self):
        cases = [
            ({"a.md": "x"}, {"a.md": "y"}, (), "in both archives: a.md"),
            ({"a.md": "x"}, {"b.md": "y"}, (("l.md", "sym"),), "holds a link: l.md"),
            ({"a.md": "x"}, {"b.md": "y"}, (("h.md", "hard"),), "holds a link: h.md"),
            ({"../out.md": "x"}, {"b.md": "y"}, (), "outside the copy"),
        ]
        for public, personal, links, reason in cases:
            with self.subTest(reason):
                self.world.archives[("x", False)] = tar_of(public)
                self.world.archives[("y", True)] = tar_of(personal, links)
                with self.assertRaisesRegex(measure.Refused, re.escape(reason)):
                    measure.restore(self.world, "x", "y", "g")

    def test_positive_shared_folders_are_allowed_and_runs_left_out(self):
        self.world.archives[("x", False)] = tar_of(
            {"w/a.md": "x", measure.RUNS_TREE + "r/state.json": "{}"})
        self.world.archives[("y", True)] = tar_of({"w/LOG.md": "y",
                                                   measure.RUNS_TREE + "r/b.md": "z"})
        restored = measure.restore(self.world, "x", "y", "g", leave_out_runs=True)
        self.assertEqual(restored.public, ["w/a.md"])
        self.assertEqual(restored.personal, ["w/LOG.md"])
        self.assertFalse((restored.path / measure.RUNS_TREE).exists())
        self.assertTrue(str(restored.path).startswith(str(self.base)))

    @NEEDS_GIT
    def test_positive_the_baseline_leaves_the_eight_files_new(self):
        spec = replays_fixture()["rounds"]["code"]
        restored = measure.restore(self.world, "pc", "pcp", "g")
        measure.baseline(self.world, restored, leave_out=spec["new_files"])
        log = subprocess.run(["git", "log", "--oneline"], cwd=restored.path,
                             capture_output=True, encoding="utf-8")
        self.assertEqual(len(log.stdout.strip().splitlines()), 1)
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"],
                                cwd=restored.path, capture_output=True, encoding="utf-8")
        new = sorted(line[3:] for line in status.stdout.splitlines())
        self.assertEqual(new, sorted(spec["new_files"]))
        diff = worktree.review_diff(restored.path, spec["new_files"], restored.baseline,
                                    category_a=set())
        for path in spec["new_files"]:
            self.assertIn(f"--- new file {path} ---", diff)

    @NEEDS_GIT
    def test_positive_a_planted_copy_has_its_personal_repository(self):
        restored = measure.restore(self.world, measure.PLANTED_PUBLIC,
                                   measure.PLANTED_PERSONAL, "g", leave_out_runs=True)
        measure.baseline(self.world, restored)
        git_dir = measure.personal_repository(self.world, restored)
        self.assertEqual(Path(git_dir).parent.parent, restored.base)
        held = worktree.git(restored.path, "ls-tree", "-r", "--name-only",
                            restored.personal_head, git_dir=git_dir)
        self.assertEqual(held.split(), ["work/LOG.md"])
        tracked = worktree.git(restored.path, "ls-files")
        self.assertNotIn("work/LOG.md", tracked)


# ------------------------------------------------------- planted changes (10)


class PlantedChangeTests(Base):

    def run_change(self, kind="logic-error"):
        m = self.measurement()
        spied = []
        real = worktree.review_diff

        def spy(root, edit_paths, public_head, personal_head=None, git_dir=None, **kw):
            spied.append((Path(root), list(edit_paths), personal_head, git_dir))
            return real(root, edit_paths, public_head, personal_head, git_dir, **kw)

        worktree.review_diff = spy
        try:
            ok = m.session(kind, m.reviewers()[0])
        finally:
            worktree.review_diff = real
        return m, ok, spied

    @NEEDS_GIT
    def test_positive_the_order_slots_and_diff(self):
        m, ok, spied = self.run_change()
        self.assertTrue(ok)
        entry = m.progress["sessions"]["sol/logic-error"]
        self.assertEqual(entry["status"], measure.DONE, entry)
        order = [c if isinstance(c, str) else c[:2] for c in self.world.calls
                 if c in ("dead_links", "read_codex") or (isinstance(c, tuple)
                                                          and c[0] in ("checks", "session"))]
        self.assertEqual(order, ["read_codex", "dead_links", "dead_links",
                                 ("checks", "untouched"), ("checks", "patched"),
                                 ("session", "codex")])
        _role, prompt = self.world.prompts[-1]
        self.assertIn("None: this run has no approved plan.", prompt)
        self.assertIn("PRIOR FINDINGS, WITH THE BUILDER'S ACTION ON EACH\n\n", prompt)
        self.assertIn("VALUE = 2", prompt)  # the patched line, in the diff
        self.assertIn("Action: modified", prompt)  # the LOG.md append, personal side
        (_root, edit_paths, personal_head, git_dir), = spied
        self.assertEqual(edit_paths, ["work/"])
        self.assertTrue(personal_head and git_dir)
        self.assertEqual(entry["result"]["changed_files"], ["work/run.py"])

    @NEEDS_GIT
    def test_rejection_an_untouched_copy_that_fails_stops_the_measurement(self):
        self.world.checks[("sol-logic-error", "untouched")] = (
            [{"title": "audit: broken", "file": "x"}], "audit: broken")
        m, ok, _ = self.run_change()
        self.assertFalse(ok)
        self.assertEqual(m.progress["status"], measure.STOPPED)
        self.assertEqual(m.progress["stop"]["reason"], "untouched-copy")
        self.assertNotIn(("checks", "patched", "sol-logic-error"), self.world.calls)
        self.assertEqual(m.progress["sessions"]["sol/logic-error"]["attempts"], [])

    @NEEDS_GIT
    def test_positive_a_placeholder_for_an_ignored_target_only(self):
        self.world.dead = [[("work/CONTEXT.md", "work/lost/LOG")], []]
        m, ok, _ = self.run_change("control")
        self.assertTrue(ok)
        self.assertEqual(m.progress["sessions"]["sol/control"]["result"]["placeholders"],
                         ["work/lost/LOG.md"])

    @NEEDS_GIT
    def test_rejection_a_dead_link_to_a_tracked_file_gets_no_placeholder(self):
        dead = [("work/CONTEXT.md", "work/GONE")]
        self.world.dead = [dead, dead]
        m, ok, _ = self.run_change()
        self.assertFalse(ok)
        self.assertEqual(m.progress["stop"]["reason"], "untouched-copy")
        self.assertIn("[[work/GONE]] in work/CONTEXT.md", m.progress["stop"]["detail"])

    def test_positive_a_catch_the_unexpected_lines_and_the_control(self):
        m = self.measurement()
        drift = {"title": "doc-sync: work: CONTEXT.md not updated", "file": "work"}
        other = {"title": "audit: something", "file": "work/x.md"}
        for kind in measure.KINDS:
            mechanical = {"metadata-drift": [drift, other], "logic-error": [other],
                          "control": [other]}.get(kind, [])
            m.progress["sessions"][f"sol/{kind}"] = {
                "status": measure.DONE, "attempts": [],
                "result": {"findings": [], "mechanical": mechanical,
                           "placeholders": []}}
        found = measure.proposals(m)
        self.assertEqual(found["mechanical"], [{"item": "sol/metadata-drift",
                                                "check": drift["title"], "file": "work"}])
        unexpected = sorted(p["item"] for p in found["proposals"]
                            if p["kind"] == "unexpected")
        self.assertEqual(unexpected, ["sol/logic-error/unexpected-1",
                                      "sol/metadata-drift/unexpected-1"])
        line = measure.ruling_line(next(p for p in found["proposals"]
                                        if p["kind"] == "unexpected"))
        self.assertTrue(line.startswith("- sol/logic-error/unexpected-1 | proposal: "
                                        "unexpected | ruling:  | note:"))
        self.assertIn("    title: audit: something", line)
        self.assertEqual(measure.control_failures(m), ["sol"])


# --------------------------------------- the schedule, usage and the scan (11)


class ScheduleTests(Base):

    @NEEDS_GIT
    def test_positive_the_full_schedule_pauses_then_finishes(self):
        m = self.measurement()
        self.assertEqual(measure.advance(m, resumed=False), 1)
        self.assertEqual(m.progress["status"], measure.PAUSED, m.progress.get("stop"))
        sol = [k for k in m.progress["sessions"] if k.startswith("sol/")]
        self.assertEqual(len(sol), 12)
        self.assertFalse([k for k in m.progress["sessions"] if k.startswith("opus/")])
        rulings = (m.folder / measure.RULINGS).read_text(encoding="utf-8")
        self.assertIn("- sol/C1 | proposal: missed | ruling: missed | note:", rulings)
        with self.assertRaisesRegex(measure.Refused, "not finished.*--measure --resume "
                                    + ID):
            measure.score(m)
        # The user changes a ruling at the pause; the second set's merge keeps it.
        edited = rulings.replace("- sol/C1 | proposal: missed | ruling: missed | note:",
                                 "- sol/C1 | proposal: missed | ruling: caught | note: "
                                 "mine")
        (m.folder / measure.RULINGS).write_text(edited, encoding="utf-8", newline="\n")
        again = measure.load_json(m.folder / measure.PROGRESS)
        m2 = self.measurement(progress=again)
        self.assertEqual(measure.resume(m2), 0)
        self.assertEqual(m2.progress["status"], measure.FINISHED)
        self.assertEqual(len(m2.progress["sessions"]), 24)
        final = (m.folder / measure.RULINGS).read_text(encoding="utf-8")
        self.assertIn("- sol/C1 | proposal: missed | ruling: caught | note: mine", final)
        self.assertIn("- opus/C1 | proposal: missed | ruling: missed | note:", final)
        roles = [c[1:] for c in self.world.calls if isinstance(c, tuple)
                 and c[0] == "session"]
        self.assertEqual(roles[:2], [("codex", "reviewer"), ("codex", "plan_reviewer")])
        self.assertEqual(roles[9:12], [("claude", "judge")] * 3)
        self.assertEqual(roles[21:24], [("codex", "judge")] * 3)
        code_prompt = self.world.prompts[0][1]
        self.assertIn("A FIXED NOTE.\n\nReview the code.", code_prompt)
        self.assertIn("`A-PLAN.md` and `B-PLAN.md`", code_prompt)
        plan_prompt = self.world.prompts[1][1]
        self.assertIn("# The plan", plan_prompt)
        self.assertIn("A FIXED NOTE.\n\nThe plan round's brief.", plan_prompt)
        text = measure.score(m2)
        self.assertIn("sol, the code round: caught 1, missed 0", text)
        self.assertTrue((m.folder / measure.REPORT).is_file())

    @NEEDS_GIT
    def test_rejection_a_code_reply_leaving_out_a_plan_is_an_error(self):
        m = self.measurement()
        world = self.world

        class Leaving(FakeSession):
            def review(inner, prompt, schema):
                result = FakeSession.review(inner, prompt, schema)
                result.reply["files_reviewed"] = [p for p in result.reply[
                    "files_reviewed"] if p != "B-PLAN.md"]
                return result

        world.session = lambda settings, provider, role, cwd, usage, frozen: Leaving(
            world, provider, role, cwd)
        m.session("code", m.reviewers()[0])
        entry = m.progress["sessions"]["sol/code"]
        self.assertEqual(entry["status"], measure.NOT_MEASURED)
        self.assertIn("B-PLAN.md", entry["causes"][0])

    def test_rejection_the_plan_rounds_brief_hash(self):
        self.world.shown[("bref", "PLAN-ROUND-BRIEF.md")] = b"changed"
        m = self.measurement()
        self.assertFalse(m.session("plan", m.reviewers()[0]))
        self.assertIn("SHA-256", m.progress["stop"]["detail"])


class UsageTests(Base):

    def stop_for(self, provider, change=None):
        m = self.measurement()
        if change:
            change(m)
        return m, m.before_session("sol/x", provider)

    def test_positive_fresh_readings_and_no_stop(self):
        _, stop = self.stop_for(limits.CODEX)
        self.assertIsNone(stop)
        _, stop = self.stop_for(limits.CLAUDE)
        self.assertIsNone(stop)
        self.assertEqual(self.world.calls.count("read_codex"), 2)
        self.assertEqual(self.world.calls.count("probe"), 1)

    def test_rejection_each_usage_case_is_a_stop(self):
        overage = limits.Reading(limits.CLAUDE, limits.LIMIT_REPORTED, percent=50.0,
                                 reason="using paid overage")
        cases = [
            ("codex ceiling", limits.CODEX, lambda m: setattr(
                self.world, "codex", limits.Reading(limits.CODEX, limits.REPORTED,
                                                    percent=95.0,
                                                    raw=self.world.codex.raw)),
             "ceiling"),
            ("credits changed", limits.CODEX, lambda m: m.progress.update(
                codex_start_credits=dict(CREDITS, balance="5")),
             "changed since the start"),
            ("untrackable start", limits.CODEX, lambda m: m.progress.update(
                codex_start_credits=None), "could not be tracked"),
            ("no probe event", limits.CLAUDE, lambda m: setattr(
                self.world, "probe", SimpleNamespace(
                    events=[], tokens=1, model="p",
                    reading=limits.Reading(limits.CLAUDE, limits.UNAVAILABLE,
                                           reason="no rate-limit event received yet"))),
             "no usable event"),
            ("overage", limits.CLAUDE, lambda m: setattr(
                self.world, "probe", SimpleNamespace(events=["e"], tokens=1, model="p",
                                                     reading=overage)), "paid overage"),
            ("known overage", limits.CLAUDE, lambda m: m.progress.update(
                claude_last_reading=loop._reading_record(overage, 0)),
             "no probe was sent"),
        ]
        for label, provider, change, reason in cases:
            with self.subTest(label):
                self.setUp()
                m, stop = self.stop_for(provider, change)
                self.assertIn(reason, stop or "")
                if label == "no usable event":
                    self.assertIn("claude_unavailable", m.progress["readings"][-1])
                if label == "overage":
                    self.assertEqual(len(m.progress["probe_on_overage"]), 1)
                if label == "known overage":
                    self.assertNotIn("probe", self.world.calls)

    def real_probe(self, messages):
        """The world's probe made by the real ``providers.probe_claude`` over a fake
        client that yields ``messages``, so its failure has the real shape."""
        try:
            from claude_agent_sdk import ResultMessage, SystemMessage
        except ImportError:
            self.skipTest("claude-agent-sdk is not installed")
        import asyncio

        class Client:
            def __init__(self, options):
                self.options = options

            async def connect(self):
                pass

            async def query(self, prompt):
                pass

            async def receive_response(self):
                for message in messages(SystemMessage, ResultMessage):
                    yield message

            async def disconnect(self):
                pass

        def probe(model, cwd, on_event):
            self.world.calls.append("probe")
            return asyncio.run(providers.probe_claude(model, cwd=cwd,
                                                      client_factory=Client,
                                                      on_event=on_event))
        self.world.probe_claude = probe

    def test_rejection_a_probe_ending_without_a_result_is_a_usage_stop(self):
        def failed(system, result):
            return [system(subtype="init", data={"model": "claude-haiku-5-5"}),
                    result(subtype="error_during_execution", duration_ms=1,
                           duration_api_ms=1, is_error=True, num_turns=1,
                           session_id="s", usage={"input_tokens": 1}, result="")]
        self.real_probe(failed)
        m, stop = self.stop_for(limits.CLAUDE)
        self.assertIn("ended without a successful result", stop)
        self.assertIn("unavailable", stop)
        record = m.progress["readings"][-1]
        self.assertIn("no event", record["claude_unavailable"])
        self.assertIn("ProviderError", record["usage_probe"]["failed"])
        self.assertNotIn("claude_last_reading", m.progress)
        # Through a session: a usage stop, not an item's failure.
        m.progress["sessions"]["sol/code"] = {"status": measure.DONE, "attempts": [],
                                              "result": {"findings": []}}
        self.assertFalse(m.session("judge-code", m.reviewers()[0]))
        self.assertEqual(m.progress["stop"]["reason"], "usage-limit")
        self.assertEqual(m.progress["sessions"]["sol/judge-code"]["attempts"], [])

    def test_negative_a_probe_on_another_model_is_an_error_not_a_usage_stop(self):
        def other_model(system, result):
            return [system(subtype="init", data={"model": "another-model"})]
        self.real_probe(other_model)
        m = self.measurement()
        with self.assertRaisesRegex(providers.ProviderError, "started on"):
            m.before_session("sol/x", limits.CLAUDE)
        m.progress["sessions"]["sol/code"] = {"status": measure.DONE, "attempts": [],
                                              "result": {"findings": []}}
        self.assertFalse(m.session("judge-code", m.reviewers()[0]))
        self.assertEqual(m.progress["stop"]["reason"], "error")

    def test_positive_a_usage_stop_is_not_an_items_failure(self):
        self.world.probe = SimpleNamespace(events=[], tokens=1, model="p",
                                           reading=limits.Reading(limits.CLAUDE,
                                                                  limits.UNAVAILABLE,
                                                                  reason="none"))
        m = self.measurement()
        m.progress["sessions"]["opus/code"] = {"status": measure.DONE, "attempts": [],
                                               "result": {"findings": []}}
        # The opus reviewer's judges run on the other provider, so take the first
        # reviewer's judge, which runs on Claude.
        m.progress["sessions"]["sol/code"] = {"status": measure.DONE, "attempts": [],
                                              "result": {"findings": []}}
        self.assertFalse(m.session("judge-code", m.reviewers()[0]))
        self.assertEqual(m.progress["status"], measure.STOPPED)
        self.assertEqual(m.progress["stop"]["reason"], "usage-limit")
        entry = m.progress["sessions"]["sol/judge-code"]
        self.assertEqual(entry["attempts"], [])
        self.assertNotEqual(entry["status"], measure.NOT_MEASURED)

    @NEEDS_GIT
    def test_positive_a_limit_mid_session_stops_and_the_resume_starts_afresh(self):
        m = self.measurement()
        self.world.behaviours = ["limit"]
        self.assertFalse(m.session("plan", m.reviewers()[0]))
        self.assertEqual(m.progress["stop"]["reason"], "usage-limit")
        self.assertEqual(m.progress["sessions"]["sol/plan"]["attempts"], [])
        self.assertTrue(m.session("plan", m.reviewers()[0]))
        self.assertEqual(m.progress["sessions"]["sol/plan"]["status"], measure.DONE)


class ScanTests(unittest.TestCase):

    TARGETS = ["d:/projects/ai-os", "c:/bookdragon/personal-work.git"]

    def test_positive_case_slashes_and_nesting(self):
        raw = [{"_type": "AssistantMessage", "content": [
            {"_type": "ToolUseBlock", "id": "1", "name": "Grep",
             "input": {"path": "D:\\Projects\\AI-OS\\memory", "nested": [
                 {"deep": "see C:/BookDragon/personal-work.git/HEAD"}]}}]}]
        self.assertEqual(len(measure.scan(raw, self.TARGETS)), 2)

    def test_negative_output_only_and_sibling_prefixes(self):
        raw = [{"_type": "UserMessage", "content": [
            {"_type": "ToolResultBlock", "content": "D:/Projects/AI-OS/AGENTS.md"}]},
               {"_type": "ToolUseBlock", "input": {"path": "D:/Projects/AI-OS-copy/a.md"}},
               {"type": "commandExecution", "command": "ls", "cwd": "E:/copy",
                "aggregated_output": "D:/Projects/AI-OS/AGENTS.md"}]
        self.assertEqual(measure.scan(raw, self.TARGETS), [])

    def test_positive_a_claude_read_by_absolute_path_is_in_its_record(self):
        try:
            from claude_agent_sdk import (AssistantMessage, ResultMessage, SystemMessage,
                                          ToolUseBlock)
        except ImportError:
            self.skipTest("claude-agent-sdk is not installed")

        class Client:
            def __init__(self, options):
                self.options = options

            async def connect(self):
                pass

            async def query(self, prompt):
                pass

            async def receive_response(self):
                yield SystemMessage(subtype="init", data={"model": "m"})
                yield AssistantMessage(content=[ToolUseBlock(
                    id="u1", name="Read",
                    input={"file_path": "D:\\Projects\\AI-OS\\A.md"})], model="m")
                yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                                    is_error=False, num_turns=1, session_id="s",
                                    usage={"input_tokens": 1}, result="r",
                                    structured_output={"summary": "s", "findings": []})

            async def disconnect(self):
                pass

        session = providers.ClaudeReviewer("m", "high", cwd="E:/copy",
                                           client_factory=Client)
        result = session.review("p", {"type": "object"})
        self.assertEqual(len(measure.scan(result.raw, self.TARGETS)), 1)

    def test_positive_a_codex_read_by_absolute_path_is_in_its_record(self):
        try:
            from openai_codex._run import _collect_turn_result
            from openai_codex.generated.v2_all import (ItemCompletedNotification,
                                                       TurnCompletedNotification)
            from openai_codex.models import Notification
        except ImportError:
            self.skipTest("openai-codex is not installed")

        def item(payload):
            return Notification(method="item/completed",
                                payload=ItemCompletedNotification.model_validate(
                                    {"completedAtMs": 1, "threadId": "t", "turnId": "u",
                                     "item": payload}))

        events = [
            item({"type": "commandExecution", "id": "c1",
                  "command": "Get-Content D:/Projects/AI-OS/AGENTS.md", "cwd": "E:/copy",
                  "commandActions": [], "status": "completed",
                  "aggregatedOutput": "C:/BookDragon/personal-work.git"}),
            Notification(method="turn/completed",
                         payload=TurnCompletedNotification.model_validate(
                             {"threadId": "t", "turn": {"id": "u", "items": [],
                                                        "status": "completed"}}))]
        # The reviewer's record is made exactly so: the SDK's collector, then jsonable.
        raw = providers.jsonable(_collect_turn_result(iter(events), turn_id="u"))
        self.assertEqual(measure.scan(raw, self.TARGETS),
                         ["Get-Content D:/Projects/AI-OS/AGENTS.md"])

    def test_positive_a_codex_delegation_is_found_though_the_scan_cannot_see_its_reads(self):
        # Code review R7-1: another agent's reads are not in this record, so the item
        # itself is what a measured session is voided on.
        try:
            from openai_codex._run import _collect_turn_result
            from openai_codex.generated.v2_all import (ItemCompletedNotification,
                                                       TurnCompletedNotification)
            from openai_codex.models import Notification
        except ImportError:
            self.skipTest("openai-codex is not installed")
        events = [
            Notification(method="item/completed",
                         payload=ItemCompletedNotification.model_validate(
                             {"completedAtMs": 1, "threadId": "t", "turnId": "u",
                              "item": {"type": "collabAgentToolCall", "id": "c9",
                                       "tool": "spawnAgent", "status": "completed",
                                       "senderThreadId": "t", "receiverThreadIds": ["t2"],
                                       "agentsStates": {}, "prompt": "read the plan"}})),
            Notification(method="turn/completed",
                         payload=TurnCompletedNotification.model_validate(
                             {"threadId": "t", "turn": {"id": "u", "items": [],
                                                        "status": "completed"}}))]
        raw = providers.jsonable(_collect_turn_result(iter(events), turn_id="u"))
        self.assertEqual(measure.scan(raw, self.TARGETS), [])
        self.assertEqual(measure.delegations(raw), ["collabAgentToolCall spawnAgent c9"])

    def test_negative_an_ordinary_record_holds_no_delegation(self):
        raw = [{"type": "commandExecution", "command": "ls", "cwd": "E:/copy"},
               {"_type": "ToolUseBlock", "input": {"path": "E:/copy/a.md"}}]
        self.assertEqual(measure.delegations(raw), [])


class FailureTests(Base):

    @NEEDS_GIT
    def test_positive_a_session_that_hands_work_to_another_agent_is_voided(self):
        # Code review R7-1: a delegation voids the attempt like a read outside the copy.
        m = self.measurement()
        self.world.behaviours = ["delegate"]
        self.assertTrue(m.session("logic-error", m.reviewers()[0]))
        attempt = m.progress["sessions"]["sol/logic-error"]["attempts"][0]
        self.assertEqual(attempt["outcome"], measure.VOID)
        self.assertIn("handed to another agent", attempt["cause"])
        self.assertEqual(m.progress["sessions"]["sol/logic-error"]["status"], measure.DONE)

    @NEEDS_GIT
    def test_positive_error_then_void_and_void_then_error(self):
        for first, second in (("error", "void"), ("void", "error")):
            for name in ("logic-error", "plan"):
                with self.subTest(first=first, name=name):
                    self.setUp()
                    m = self.measurement()
                    self.world.behaviours = [first, second]
                    self.assertTrue(m.session(name, m.reviewers()[0]))
                    entry = m.progress["sessions"][f"sol/{name}"]
                    self.assertEqual(entry["status"], measure.NOT_MEASURED)
                    self.assertEqual([a["outcome"] for a in entry["attempts"]],
                                     [first, second])
                    saved = measure.load_json(m.folder / measure.PROGRESS)
                    self.assertEqual(saved["sessions"][f"sol/{name}"]["status"],
                                     measure.NOT_MEASURED)
                    again = self.measurement(progress=saved)
                    before = len(self.world.prompts)
                    self.assertTrue(again.session(name, again.reviewers()[0]))
                    self.assertEqual(len(self.world.prompts), before)

    @NEEDS_GIT
    def test_positive_not_measured_items_leave_the_judges_and_the_score(self):
        m = self.measurement()
        reviewer = m.reviewers()[0]
        self.world.behaviours = ["error", "void"]
        m.session("plan", reviewer)
        self.world.behaviours = ["void", "error"]
        m.session("logic-error", reviewer)
        for name in ("code", *measure.KINDS[1:]):
            m.session(name, reviewer)
        before = len(self.world.prompts)
        m.session("judge-plan", reviewer)
        self.assertEqual(len(self.world.prompts), before, "no judge for the plan round")
        self.assertEqual(m.progress["sessions"]["sol/judge-plan"]["status"],
                         measure.SKIPPED)
        m.session("judge-code", reviewer)
        m.session("judge-planted", reviewer)
        planted_prompt = self.world.prompts[-1][1]
        self.assertNotIn("### Change logic-error", planted_prompt)
        found = measure.proposals(m)
        items = {p["item"] for p in found["proposals"]}
        self.assertNotIn("sol/R1-1", items)
        self.assertNotIn("sol/logic-error", items)
        absent = {n["item"] for n in found["not_measured"]}
        self.assertTrue({"sol/R1-1", "sol/logic-error"} <= absent)
        self.assertEqual(measure.session_counts(m.progress), (2, 2))

    @NEEDS_GIT
    def test_positive_a_judge_not_measured_marks_its_items(self):
        m = self.measurement()
        reviewer = m.reviewers()[0]
        m.session("code", reviewer)
        self.world.behaviours = ["error", "error"]
        m.session("judge-code", reviewer)
        found = measure.proposals(m)
        absent = {n["item"] for n in found["not_measured"]}
        self.assertTrue({"sol/C1", "sol/P1"} <= absent)
        self.assertFalse([p for p in found["proposals"] if p["item"] == "sol/C1"])


# ------------------------------------------------------- scoring and the report (12)


class ScoringTests(Base):

    def finished(self):
        m = self.measurement()
        m.progress["reviewers"] = m.progress["reviewers"][:1]
        sessions = m.progress["sessions"]
        sessions["sol/code"] = {"status": measure.DONE, "attempts": [],
                                "result": {"findings": [finding("one"), finding("two")]}}
        sessions["sol/judge-code"] = {"status": measure.DONE, "attempts": [], "result": {
            "judgement": {"items": {
                "C1": {"item": "C1", "verdict": "caught", "match": "N1: one", "why": "w"},
                "P1": {"item": "P1", "verdict": "missed", "match": "", "why": "w"}},
                "findings": {"N2": {"finding": "N2", "verdict": "plausible", "why": "w"}},
                "matched": {"N1": "C1"}}}}
        sessions["sol/plan"] = {"status": measure.NOT_MEASURED, "attempts": [
            {"outcome": "error", "cause": "x"}, {"outcome": "void", "cause": "y"}],
            "causes": ["x", "y"]}
        sessions["sol/judge-plan"] = {"status": measure.SKIPPED,
                                      "cause": "the plan replay was not measured"}
        drift = {"title": "doc-sync: work: CONTEXT.md not updated", "file": "work"}
        for kind in measure.KINDS:
            mechanical = ([drift] if kind == "metadata-drift" else
                          [{"title": "audit: odd", "file": "q"}] if kind == "wrong-test"
                          else [])
            sessions[f"sol/{kind}"] = {"status": measure.DONE, "attempts": [], "result": {
                "findings": [finding(f"{kind} finding")], "placeholders": ["a/LOG.md"],
                "mechanical": mechanical}}
        sessions["sol/judge-planted"] = {"status": measure.DONE, "attempts": [], "result": {
            "judgement": {
                "items": {k: {"item": k, "verdict": "missed", "match": "", "why": "w"}
                          for k in measure.KINDS if k not in ("control",
                                                              "metadata-drift")},
                "findings": {f"{k}/N1": {"finding": f"{k}/N1", "why": "w",
                                         "verdict": "false alarm" if k == "control"
                                         else "plausible"} for k in measure.KINDS},
                "matched": {}}}}
        m.progress["status"] = measure.FINISHED
        m.progress["probe_on_overage"] = [{"session": "sol/x", "reading": "R", "tokens": 1,
                                           "at": "t"}]
        measure.write_proposals(m)
        return m

    def rulings(self, m):
        return (m.folder / measure.RULINGS).read_text(encoding="utf-8")

    def set_rulings(self, m, text):
        (m.folder / measure.RULINGS).write_text(text, encoding="utf-8", newline="\n")

    def test_positive_the_report_holds_every_element(self):
        m = self.finished()
        text = self.rulings(m).replace("proposal: unexpected | ruling:  |",
                                       "proposal: unexpected | ruling: not a catch |")
        self.set_rulings(m, text)
        report = measure.score(m)
        for wanted in ("sol, the code round: caught 1, missed 0",
                       "Review-process findings, reported apart: sol/P1 missed",
                       "What the original reviewer could run: the tests",
                       "Recorded drift: two documents edited",
                       "Each kind has one sample",
                       "The scope breach is measured without the intent check",
                       "sol/metadata-drift: mechanical catch (doc-sync:",
                       "sol/logic-error: missed", "sol/control: 1 false alarm(s)",
                       "Unexpected check failures ruled not a catch: "
                       "sol/wrong-test/unexpected-1",
                       "sol/logic-error: a/LOG.md",
                       "Failed sessions: 1. Voided sessions: 1.",
                       "probe_on_overage): sol/x: R", "## Not measured",
                       "sol/R1-1: x; y"):
            self.assertIn(wanted, report)
        self.assertNotIn("- sol/R1-1 |", self.rulings(m))

    def test_rejection_each_unreadable_or_unruled_line(self):
        m = self.finished()
        base = self.rulings(m).replace("proposal: unexpected | ruling:  |",
                                       "proposal: unexpected | ruling: caught |")
        c1 = "- sol/C1 | proposal: caught | ruling: caught | note:"
        cases = [
            (base.replace(c1, "- sol/C1 ruling caught"), "cannot be read"),
            (base.replace(c1, "- sol/C1 | proposal: caught | ruling:  | note:"),
             "sol/C1 has no ruling"),
            (base + c1 + "\n", "a second time"),
            (base + "- sol/R1-1 | proposal: caught | ruling: caught | note:\n",
             "sol/R1-1 matches no proposal"),
            (base.replace(c1 + "\n", ""), "no ruling for: sol/C1"),
            (base.replace(c1, "- sol/C1 | proposal: caught | ruling: maybe | note:"),
             "must be one of"),
        ]
        for text, reason in cases:
            with self.subTest(reason):
                self.set_rulings(m, text)
                with self.assertRaisesRegex(measure.Refused, reason):
                    measure.score(m)
                self.assertEqual(self.rulings(m), text)
                self.assertFalse((m.folder / measure.REPORT).exists())
        lines = base.splitlines()
        number = next(i for i, line in enumerate(lines, 1) if line.startswith(c1))
        lines[number - 1] = "- sol/C1 | broken"
        self.set_rulings(m, "\n".join(lines) + "\n")
        with self.assertRaisesRegex(measure.Refused, f"line {number} cannot be read"):
            measure.score(m)

    def test_positive_a_rerun_after_a_changed_ruling(self):
        m = self.finished()
        text = self.rulings(m).replace("proposal: unexpected | ruling:  |",
                                       "proposal: unexpected | ruling: caught |")
        self.set_rulings(m, text)
        first = measure.score(m)
        self.assertIn("sol/wrong-test: mechanical catch (an unexpected", first)
        self.set_rulings(m, text.replace("- sol/C1 | proposal: caught | ruling: caught",
                                         "- sol/C1 | proposal: caught | ruling: missed"))
        second = measure.score(m)
        self.assertIn("sol, the code round: caught 0, missed 1", second)

    def test_rejection_while_the_control_has_a_check_failure(self):
        m = self.finished()
        m.progress["sessions"]["sol/control"]["result"]["mechanical"] = [
            {"title": "audit: x", "file": "y"}]
        with self.assertRaisesRegex(measure.Refused, "control has a check failure"):
            measure.score(m)

    def test_rejection_a_stopped_measurement(self):
        m = self.finished()
        m.progress["status"] = measure.STOPPED
        with self.assertRaisesRegex(measure.Refused, "--measure --resume " + ID):
            measure.score(m)


if __name__ == "__main__":
    unittest.main()
