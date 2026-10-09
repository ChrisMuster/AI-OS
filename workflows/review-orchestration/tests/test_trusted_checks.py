#!/usr/bin/env python3
"""Tests for orchestrator isolation stage S3 in the review orchestrator: the
project-root override (plan 4.2, 7.0), keeping the clean-copy marker off every
host check (4.3), and the trusted host copies with their readers (4.4).

The trusted copies are exercised for real against a fixture project: a temporary
git repository holding a copy of the four host checks' workflow folders as they
are in this working tree, plus a small documented folder to check. Copies are
written from its commit, so a change planted in its live scripts afterwards must
not run. The readers are also tested with fake outputs, one rule per test. No test
writes into the real project.

    python workflows/review-orchestration/tests/test_trusted_checks.py
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
checks = _load("checks")

FAKE_NAME = "Zedwick Quornby"
MARKER = "BOOK_DRAGON_CLEAN_COPY"


_spec = importlib.util.spec_from_file_location(
    "fixture_git", Path(__file__).resolve().parent / "fixture_git.py")
fixture_git = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture_git)
FixtureGitError = fixture_git.FixtureGitError


def git(cwd, *args):
    """Run a git command for a test's fixture and return the finished process,
    through the suites' one fixture runner (`fixture_git.py`)."""
    return fixture_git.run_git(["git", *args], cwd, shown=f"git {' '.join(args)}")


def without(*names):
    return {k: v for k, v in os.environ.items() if k.upper() not in names}


# ---------------------------------------------------------------------------
# Root override
# ---------------------------------------------------------------------------
PROBE = """
import json, sys
sys.path.insert(0, sys.argv[1])
import brief, runrecord, settings, codex_rules, approver, loop
print(json.dumps({
    "root": str(brief.PROJECT_ROOT), "log": str(brief.LOG_PATH),
    "runs": str(runrecord.RUNS_DIR), "loop_root": str(loop.PROJECT_ROOT),
    "loop_log": str(loop.LOG_PATH), "approver_root": str(approver.PROJECT_ROOT),
    "settings": str(settings.SETTINGS_PATH), "prompts": str(loop.PROMPTS_DIR),
    "verify": str(approver.VERIFY_COMMANDS_PATH),
    "codex_read": str(codex_rules.READ_COMMANDS_PATH)}))
"""


class RootOverrideTests(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.fixture = self.tmp / "project"
        self.fixture.mkdir()
        (self.fixture / "AGENTS.md").write_bytes(b"# fixture\n")

    def probe(self, env):
        # Run from a folder that is not the project, so a root taken from the
        # working folder cannot pass for the project's own (the R8-1 sweep).
        return subprocess.run([sys.executable, "-c", PROBE, str(SCRIPTS)], env=env,
                              cwd=str(self.tmp), capture_output=True, text=True,
                              encoding="utf-8", timeout=120)

    def test_positive_run_data_follows_the_root_and_code_stays_beside_itself(self):
        done = self.probe(dict(os.environ, BOOK_DRAGON_ROOT=str(self.fixture)))
        self.assertEqual(done.returncode, 0, done.stderr)
        got = {k: Path(v) for k, v in json.loads(done.stdout).items()}
        root = self.fixture.resolve()
        here = root / "workflows" / "review-orchestration"
        self.assertEqual(got["root"], root)
        self.assertEqual(got["loop_root"], root)
        self.assertEqual(got["approver_root"], root)
        self.assertEqual(got["log"], here / "LOG.md")
        self.assertEqual(got["loop_log"], here / "LOG.md")
        self.assertEqual(got["runs"], here / "runs")
        self.assertEqual(got["settings"], WORKFLOW / "config" / "settings.json")
        self.assertEqual(got["prompts"], WORKFLOW / "prompts")
        self.assertEqual(got["verify"], WORKFLOW / "config" / "verify-commands.txt")
        self.assertEqual(got["codex_read"], WORKFLOW / "config" / "codex-read-commands.txt")

    def test_negative_unset_everything_is_the_projects_own(self):
        done = self.probe(without("BOOK_DRAGON_ROOT"))
        self.assertEqual(done.returncode, 0, done.stderr)
        got = {k: Path(v) for k, v in json.loads(done.stdout).items()}
        self.assertEqual(got["root"], PROJECT)
        self.assertEqual(got["runs"], WORKFLOW / "runs")
        self.assertEqual(got["log"], WORKFLOW / "LOG.md")

    def test_rejection_a_bad_value_exits_2(self):
        (self.tmp / "no-agents").mkdir()
        for value in (str(self.tmp / "missing"), str(self.tmp / "no-agents"), ""):
            with self.subTest(value=value):
                done = self.probe(dict(os.environ, BOOK_DRAGON_ROOT=value))
                self.assertEqual(done.returncode, 2)
                self.assertIn("BOOK_DRAGON_ROOT", done.stderr)
                self.assertEqual(done.stdout, "")


# ---------------------------------------------------------------------------
# The marker never reaches a host check
# ---------------------------------------------------------------------------
SHOW_MARKER = "import os; print(os.environ.get('BOOK_DRAGON_CLEAN_COPY', 'absent'))"


class MarkerIsolationTests(unittest.TestCase):

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {MARKER: "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_negative_control_a_plain_child_inherits_the_marker(self):
        # Without the stripping the marker does reach a child, so the test below
        # can fail.
        done = subprocess.run([sys.executable, "-c", SHOW_MARKER], capture_output=True,
                              text=True, encoding="utf-8")
        self.assertEqual(done.stdout.strip(), "1")

    def test_positive_a_host_check_is_started_without_the_marker(self):
        code, out, _err = checks._run(PROJECT, [sys.executable, "-c", SHOW_MARKER])
        self.assertEqual((code, out.strip()), (0, "absent"))

    def test_positive_the_start_check_is_started_without_the_marker(self):
        with mock.patch.object(checks.subprocess, "run") as spy:
            spy.return_value = mock.Mock(returncode=0, stdout="{}", stderr="")
            checks.close_out_start(PROJECT, sys.executable)
        self.assertNotIn(MARKER, spy.call_args.kwargs["env"])

    def test_positive_a_trusted_check_gets_the_root_and_no_bytecode_or_marker(self):
        env = checks.trusted_env(Path("/fixture/root"))
        self.assertNotIn(MARKER, env)
        self.assertEqual(env["BOOK_DRAGON_ROOT"], str(Path("/fixture/root")))
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")


# ---------------------------------------------------------------------------
# Trusted copies, for real
# ---------------------------------------------------------------------------
FIXTURE_CONTEXT = b"""# Checked

**Last modified:** 2026-10-07

## Purpose
A folder for the trusted checks to look at.

See [[workflows/no-such-workflow/CONTEXT]] and [[journal/entries/2026-10]].
"""
# The audit report's separator is an em dash, built with chr() here.
MISSING_LOG = f"audit: `workflows/checked/` {chr(0x2014)} Missing LOG.md"
SIDE_EFFECT = "\nfrom pathlib import Path as _P; _P(r'{path}').write_text('fired')\n"


def build_fixture(root):
    """A git repository holding this working tree's copy of the four host checks'
    folders (tracked and new files, none ignored), committed, plus a documented
    folder with no LOG.md, a dead link and a link to an ignored file, and a
    made-up name in a gitignored USER.md written into one committable file."""
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--",
         *checks.TRUSTED_WORKFLOWS],
        cwd=str(PROJECT), check=True, capture_output=True, encoding="utf-8").stdout
    for rel in filter(None, listed.split("\0")):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT / rel, target)
    (root / "AGENTS.md").write_bytes(b"# fixture\n")
    (root / ".gitignore").write_bytes(b"**/LOG.md\nUSER.md\njournal/\n__pycache__/\n")
    (root / "workflows" / "checked").mkdir(parents=True)
    (root / "workflows" / "checked" / "CONTEXT.md").write_bytes(FIXTURE_CONTEXT)
    (root / "notes.txt").write_bytes(f"Written by {FAKE_NAME}.\n".encode())
    (root / "USER.md").write_bytes(f"**Name:** {FAKE_NAME}\n".encode())
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "Test")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "fixture", "--no-verify")


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}


class TrustedCopiesTests(unittest.TestCase):
    """One fixture for the class: building it copies four workflow folders and
    makes a commit, and every test only reads it."""

    @classmethod
    def setUpClass(cls):
        cls.holder = tempfile.TemporaryDirectory()
        tmp = Path(cls.holder.name)
        cls.root = tmp / "project"
        cls.root.mkdir()
        build_fixture(cls.root)
        cls.fired = tmp / "side-effect-fired"
        live = cls.root / "workflows" / "audit" / "scripts" / "run.py"
        with open(live, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(SIDE_EFFECT.format(path=cls.fired))
        cls.trusted = tmp / "frozen" / "trusted"
        checks.write_trusted_copies(cls.root, "HEAD", cls.trusted)
        cls.round_dir = tmp / "rounds" / "R1"
        cls.before = tree(cls.root)
        cls.trusted_before = tree(cls.trusted)
        with mock.patch.dict(os.environ, {MARKER: "1"}):
            cls.findings, cls.text = checks.trusted_host_checks(
                cls.root, sys.executable, cls.trusted, ["workflows/checked/"],
                ["workflows/checked"], cls.round_dir)
        cls.after = tree(cls.root)
        cls.trusted_after = tree(cls.trusted)

    @classmethod
    def tearDownClass(cls):
        cls.holder.cleanup()

    def titles(self):
        return [(f["severity"], f["title"]) for f in self.findings]

    def test_positive_the_copies_are_the_commits_not_the_live_tree(self):
        copied = (self.trusted / "workflows" / "audit" / "scripts" / "run.py").read_text(
            encoding="utf-8")
        committed = git(self.root, "show", "HEAD:workflows/audit/scripts/run.py").stdout
        self.assertEqual(copied, committed)
        self.assertNotIn("side-effect-fired", copied)
        for workflow in checks.TRUSTED_WORKFLOWS:
            self.assertTrue((self.trusted / workflow / "scripts" / "run.py").is_file())

    def test_positive_a_side_effect_planted_in_the_live_script_never_fires(self):
        self.assertFalse(self.fired.exists())

    def test_positive_the_fixture_is_the_project_checked(self):
        titles = [t for _, t in self.titles()]
        self.assertIn(MISSING_LOG, titles)
        self.assertIn("dead link: [[workflows/no-such-workflow/CONTEXT]] in "
                      "workflows/checked/CONTEXT.md", titles)
        self.assertIn("personal data: a personal name in notes.txt", titles)

    def test_positive_the_marker_in_the_orchestrator_did_not_reach_the_checks(self):
        # Run with the marker in the orchestrator's environment, yet a missing
        # LOG.md is a FAIL (blocker) and a link to an ignored file is dead.
        self.assertIn(("blocker", MISSING_LOG), self.titles())
        self.assertIn(("major", "dead link: [[journal/entries/2026-10]] in "
                       "workflows/checked/CONTEXT.md"), self.titles())

    def test_positive_the_checks_wrote_nothing_in_the_project_or_the_copies(self):
        # Every file in both, byte for byte (code review R7-2: the copies were
        # checked for bytecode folders only). No write is allowed in either.
        self.assertEqual(self.after, self.before)
        self.assertGreater(len(self.trusted_before), 0)
        self.assertEqual(self.trusted_after, self.trusted_before)
        self.assertEqual([p for p in self.trusted.rglob("__pycache__")], [])

    def test_positive_the_matched_text_reaches_only_the_round_file(self):
        self.assertNotIn(FAKE_NAME, json.dumps(self.findings))
        self.assertNotIn(FAKE_NAME, self.text)
        self.assertIn(FAKE_NAME.split()[0],
                      (self.round_dir / "personal-data.json").read_text(encoding="utf-8"))

    def test_rejection_an_existing_copy_is_never_overwritten(self):
        # Refused, and the copy left exactly as it was (the R7-2 sweep: a refusal
        # raised only after writing would otherwise pass). One copied file is first
        # made to differ from the commit, so an overwrite would show.
        copied = self.trusted / "workflows" / "audit" / "scripts" / "run.py"
        original = copied.read_bytes()
        self.addCleanup(copied.write_bytes, original)
        copied.write_bytes(original + b"# differs from the commit\n")
        before = tree(self.trusted)
        with self.assertRaises(checks.TrustedCopyError):
            checks.write_trusted_copies(self.root, "HEAD", self.trusted)
        self.assertEqual(tree(self.trusted), before)

    def test_rejection_a_commit_that_does_not_exist_writes_nothing(self):
        dest = Path(self.holder.name) / "bad-head"
        with self.assertRaises(checks.TrustedCopyError):
            checks.write_trusted_copies(self.root, "no-such-commit", dest)
        self.assertFalse(dest.exists())

    def test_rejection_a_commit_without_a_workflow_is_refused(self):
        dest = Path(self.holder.name) / "missing-workflow"
        with self.assertRaises(checks.TrustedCopyError):
            checks.write_trusted_copies(self.root, "HEAD", dest,
                                        workflows=("workflows/audit", "workflows/checked"))


# ---------------------------------------------------------------------------
# What each trusted check is asked to run
# ---------------------------------------------------------------------------
class TrustedArgvTests(unittest.TestCase):

    def run_spy(self, call):
        with mock.patch.object(checks, "_run", return_value=(0, "{}", "")) as spy:
            call()
        argv = spy.call_args.args[1]
        return [Path(argv[1]).as_posix(), *argv[2:]], spy.call_args.kwargs["env"]

    def test_positive_each_check_runs_its_trusted_copy_with_the_plan_arguments(self):
        trusted, root = Path("T"), Path("R")
        cases = {
            "audit": (lambda: checks.audit(root, "py", ["a", "b"], trusted=trusted),
                      ["T/workflows/audit/scripts/run.py", "--context", "a", "b",
                       "--read-only"]),
            "doc-sync": (lambda: checks.doc_sync(root, "py", [], trusted=trusted),
                         ["T/workflows/doc-sync-guard/scripts/run.py", "--json"]),
            "link": (lambda: checks.link_check(root, "py", trusted),
                     ["T/workflows/link-check/scripts/run.py", "--audit", "--no-log",
                      "--json"]),
        }
        for name, (call, expected) in cases.items():
            with self.subTest(check=name):
                argv, env = self.run_spy(call)
                self.assertEqual(argv, expected)
                self.assertEqual(env["BOOK_DRAGON_ROOT"], "R")

    def test_negative_without_trusted_the_live_scripts_run_as_before(self):
        with mock.patch.object(checks, "_run", return_value=(0, "{}", "")) as spy:
            checks.audit(Path("R"), "py", ["a"])
        self.assertEqual(spy.call_args.args[1],
                         ["py", "workflows/audit/scripts/run.py", "--context", "a"])
        self.assertNotIn("env", spy.call_args.kwargs)


# ---------------------------------------------------------------------------
# The personal-data reader, with fake output
# ---------------------------------------------------------------------------
SECRET = "the-matched-text-" + "7731"


class PersonalDataReaderTests(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.round_dir = Path(tmp.name) / "R2"

    def read(self, findings=None, raw=None, code=None, scanned=True):
        payload = {"findings": findings}
        if scanned is not None:
            payload["scanned"] = scanned
        out = raw if raw is not None else json.dumps(payload)
        if code is None:  # as the real guard exits: 1 with a FAIL, else 0
            code = 1 if any(f.get("severity") == "FAIL" for f in findings or []) else 0
        with mock.patch.object(checks, "_trusted_run", return_value=(code, out, "")):
            return checks.personal_data(Path("R"), "py", Path("T"), self.round_dir)

    def hit(self, severity="FAIL", file="docs/a.md", kind="email"):
        item = {"severity": severity, "label": "personal-data",
                "message": f"{file}: found {SECRET}"}
        if file is not None:
            item["file"] = file
        if kind is not None:
            item["kind"] = kind
        return item

    def test_positive_a_fail_is_a_blocker_naming_file_and_kind_only(self):
        found, lines = self.read([self.hit()])
        self.assertEqual([(f["severity"], f["title"], f["file"]) for f in found],
                         [("blocker", "personal data: an email address in docs/a.md",
                           "docs/a.md")])
        self.assertNotIn(SECRET, json.dumps(found) + "\n".join(lines))

    def test_positive_a_warn_is_major(self):
        found, _ = self.read([self.hit("WARN", kind="denylisted_term")])
        self.assertEqual([(f["severity"], f["title"]) for f in found],
                         [("major", "personal data: a denylisted term in docs/a.md")])

    def test_positive_every_kind_is_named_in_words(self):
        words = {"email": "an email address", "home_path": "a personal home path",
                 "os_username": "the OS username", "personal_name": "a personal name",
                 "denylisted_term": "a denylisted term"}
        for kind, said in words.items():
            with self.subTest(kind=kind):
                found, _ = self.read([self.hit(kind=kind)])
                self.assertEqual(found[0]["title"], f"personal data: {said} in docs/a.md")

    def test_rejection_a_hit_missing_its_file_or_kind_is_a_blocker_naming_only_the_check(self):
        for hit in (self.hit(file=None), self.hit(kind=None), self.hit(kind="other"),
                    self.hit(file="")):
            with self.subTest(hit=hit):
                found, _ = self.read([hit])
                self.assertEqual([(f["severity"], f["title"], f["file"]) for f in found],
                                 [("blocker", "personal-data check reported a hit it "
                                   "did not place", "(personal-data)")])
                self.assertNotIn(SECRET, json.dumps(found))

    def test_negative_a_run_level_info_note_is_not_a_finding(self):
        found, lines = self.read([{"severity": "INFO", "label": "personal-data",
                                   "message": "USER.md not found"}])
        self.assertEqual((found, lines), ([], ["personal-data: clean"]))

    def test_rejection_an_unreadable_result_is_a_blocker_without_the_output(self):
        found, _ = self.read(raw=f"Traceback ... {SECRET}", code=1)
        self.assertEqual([(f["severity"], f["title"]) for f in found],
                         [("blocker", "personal-data check did not run")])
        self.assertNotIn(SECRET, json.dumps(found))

    def test_positive_the_round_file_holds_the_whole_output(self):
        self.read([self.hit()])
        saved = json.loads((self.round_dir / "personal-data.json").read_text(
            encoding="utf-8"))
        self.assertIn(SECRET, saved["stdout"])
        self.assertEqual(saved["exit_code"], 1)

    def test_rejection_an_exit_that_does_not_match_the_result_is_a_blocker(self):
        # R7-1: a guard that printed valid JSON but did not finish as it reports.
        cases = (([], 1), ([], 2), ([self.hit()], 0), ([self.hit()], 2),
                 ([self.hit("WARN", kind="denylisted_term")], 1))
        for findings, code in cases:
            with self.subTest(findings=len(findings), code=code):
                found, lines = self.read(findings, code=code)
                self.assertEqual([(f["severity"], f["title"]) for f in found],
                                 [("blocker", "personal-data check did not run")])
                self.assertIn(f"exited {code}", found[0]["evidence"])
                self.assertNotIn(SECRET, json.dumps(found) + "\n".join(lines))

    def test_rejection_a_scan_not_reported_complete_is_a_blocker(self):
        # R9-1: git could not list the files, so the guard scanned none and said
        # so in a note, exiting 0 with no finding.
        skipped = {"severity": "INFO", "label": "personal-data",
                   "message": f"scan skipped - {SECRET}"}
        for scanned in (False, None, "true", 1):
            with self.subTest(scanned=scanned):
                found, lines = self.read([skipped], code=0, scanned=scanned)
                self.assertEqual([(f["severity"], f["title"], f["file"]) for f in found],
                                 [("blocker", "personal-data check did not run",
                                   "(personal-data)")])
                self.assertNotIn(SECRET, json.dumps(found) + "\n".join(lines))

    def test_positive_the_two_exits_the_guard_gives_are_read(self):
        self.assertEqual(self.read([], code=0), ([], ["personal-data: clean"]))
        found, _ = self.read([self.hit("WARN", kind="denylisted_term")], code=0)
        self.assertEqual([f["severity"] for f in found], ["major"])
        found, _ = self.read([self.hit()], code=1)
        self.assertEqual([f["severity"] for f in found], ["blocker"])


# ---------------------------------------------------------------------------
# The link reader, with fake output
# ---------------------------------------------------------------------------
class LinkReaderTests(unittest.TestCase):

    def read(self, raw, code=0):
        with mock.patch.object(checks, "_trusted_run", return_value=(code, raw, "")):
            return checks.link_check(Path("R"), "py", Path("T"))

    def test_rejection_a_valid_result_with_a_failing_exit_is_a_blocker(self):
        # R7-1: the link check exits 0 whenever it finishes.
        for code in (1, 2, None):
            with self.subTest(code=code):
                found, _ = self.read('{"dead_links": []}', code=code)
                self.assertEqual([(f["severity"], f["title"]) for f in found],
                                 [("blocker", "link check did not run")])
                self.assertIn(f"exit {code}", found[0]["evidence"])

    def test_positive_each_dead_link_is_a_major_finding(self):
        found, lines = self.read(json.dumps({"dead_links": [
            {"file": "workflows/a/CONTEXT.md", "target": "workflows/b/CONTEXT"}]}))
        self.assertEqual([(f["severity"], f["title"], f["file"]) for f in found],
                         [("major", "dead link: [[workflows/b/CONTEXT]] in "
                           "workflows/a/CONTEXT.md", "workflows/a/CONTEXT.md")])
        self.assertEqual(lines, ["link check: dead [[workflows/b/CONTEXT]] in "
                                 "workflows/a/CONTEXT.md"])

    def test_negative_no_dead_link_is_clean(self):
        self.assertEqual(self.read('{"dead_links": []}'), ([], ["link check: clean"]))

    def test_rejection_a_check_that_did_not_run_is_a_blocker(self):
        for raw in ("not json", '{"other": []}', '{"dead_links": [{"file": "a"}]}',
                    '{"dead_links": "x"}'):
            with self.subTest(raw=raw):
                found, _ = self.read(raw)
                self.assertEqual([(f["severity"], f["title"]) for f in found],
                                 [("blocker", "link check did not run")])


class DocSyncExitTests(unittest.TestCase):
    """R7-1 applied to the doc-sync reader, live or trusted: the guard exits 0
    whenever it finishes (no --strict), so a valid result with any other exit is
    a check that did not finish."""

    def read(self, code):
        with mock.patch.object(checks, "_run", return_value=(code, '{"findings": []}', "")):
            return checks.doc_sync(Path("R"), "py", ["e/"])

    def test_negative_control_exit_0_with_no_findings_is_clean(self):
        self.assertEqual(self.read(0), ([], ["doc-sync: clean"]))

    def test_rejection_a_valid_result_with_a_failing_exit_is_a_blocker(self):
        for code in (1, 2, None):
            with self.subTest(code=code):
                found, _ = self.read(code)
                self.assertEqual([(f["severity"], f["title"]) for f in found],
                                 [("blocker", "doc-sync did not run")])
                self.assertIn(f"exit {code}", found[0]["evidence"])


class TrustedHostChecksTests(unittest.TestCase):

    def test_positive_the_four_run_in_the_plans_order_from_the_trusted_folder(self):
        order = []

        def fake(name):
            def call(*args, **kwargs):
                order.append((name, kwargs.get("trusted", args[2] if name in
                                                ("personal_data", "link_check")
                                                else None)))
                return [{"title": name}], [name]
            return call

        with mock.patch.object(checks, "audit", fake("audit")), \
                mock.patch.object(checks, "doc_sync", fake("doc_sync")), \
                mock.patch.object(checks, "personal_data", fake("personal_data")), \
                mock.patch.object(checks, "link_check", fake("link_check")):
            found, text = checks.trusted_host_checks(Path("R"), "py", Path("T"),
                                                     ["e/"], ["e"], Path("D"))
        self.assertEqual(order, [("audit", Path("T")), ("doc_sync", Path("T")),
                                 ("personal_data", Path("T")), ("link_check", Path("T"))])
        self.assertEqual([f["title"] for f in found],
                         ["audit", "doc_sync", "personal_data", "link_check"])
        self.assertEqual(text, "audit\ndoc_sync\npersonal_data\nlink_check")


class FixtureHelperTests(unittest.TestCase):
    """The suite's own fixture helper (code review R21-1)."""

    def test_rejection_a_failing_git_command_names_gits_own_error(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        with self.assertRaises(FixtureGitError) as caught:
            git(Path(folder.name), "add", "-A")
        self.assertIn("`git add -A` exited 128", str(caught.exception))
        self.assertIn("not a git repository", str(caught.exception).lower())


if __name__ == "__main__":
    unittest.main()
