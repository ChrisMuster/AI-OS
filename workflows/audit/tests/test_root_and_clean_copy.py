#!/usr/bin/env python3
"""Tests for the audit's project-root override and clean-copy mode (orchestrator
isolation S3, plan 4.2 and 4.3).

``BOOK_DRAGON_ROOT`` names the project a copy of this script audits: every project
path follows it, and a value that is not an existing folder holding AGENTS.md exits
2. ``BOOK_DRAGON_CLEAN_COPY=1`` (only ``1``) makes a missing LOG.md an INFO and
leaves the full audit's personal-data and doc-sync hooks to the host, each saying
so. Fixture projects live in temporary folders; the real LOG.md files are never
touched (the subprocess runs that write, write into the fixture).

    python workflows/audit/tests/test_root_and_clean_copy.py
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
SCRIPT = TESTS.parent / "scripts" / "run.py"
REAL_ROOT = TESTS.parents[2]

sys.path.insert(0, str(SCRIPT.parent))
import run  # noqa: E402

ADVISORY_CHECKS = ("run_encoding_check", "run_personal_data_check",
                   "run_ai_style_check", "run_doc_sync_check",
                   "run_skill_hardening_check")
HOST_INFO = {
    ("INFO", "personal-data", "personal-data check runs on the host, not in the clean copy"),
    ("INFO", "doc-sync", "doc-sync check runs on the host, not in the clean copy"),
}


def load_fresh(env):
    """Import run.py afresh under ``env``: the root is read at import."""
    spec = importlib.util.spec_from_file_location("audit_run_root_probe", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, env):
        spec.loader.exec_module(module)
    return module


def without(*names):
    """os.environ with ``names`` removed, for a subprocess."""
    return {k: v for k, v in os.environ.items() if k.upper() not in names}


class FixtureCase(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.fixture = self.tmp / "project"
        (self.fixture / "workflows" / "only-in-fixture").mkdir(parents=True)
        (self.fixture / "AGENTS.md").write_bytes(b"# fixture\n")
        (self.fixture / "workflows" / "only-in-fixture" / "CONTEXT.md").write_bytes(
            b"# Only in the fixture\n")

    def audit(self, root, *args):
        env = dict(without("BOOK_DRAGON_CLEAN_COPY"), BOOK_DRAGON_ROOT=root)
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=env,
                              capture_output=True, text=True, encoding="utf-8",
                              timeout=120)


class RootOverrideTests(FixtureCase):

    def test_negative_unset_the_root_is_three_folders_above_the_script(self):
        # A script location that is not the working folder, so a root taken from
        # the working folder cannot pass (the R8-1 sweep).
        script_dir = self.tmp / "workflows" / "audit" / "scripts"
        with mock.patch.dict(os.environ):
            os.environ.pop("BOOK_DRAGON_ROOT", None)
            self.assertEqual(run.project_root(script_dir), self.tmp)
            self.assertEqual(run.project_root(), REAL_ROOT)

    def test_positive_every_project_path_follows_a_set_root(self):
        module = load_fresh({"BOOK_DRAGON_ROOT": str(self.fixture)})
        root = self.fixture.resolve()
        self.assertEqual(module.PROJECT_ROOT, root)
        self.assertEqual(module.WORKFLOW_LOG, root / "workflows" / "audit" / "LOG.md")
        self.assertEqual(module.ROOT_LOG, root / "LOG.md")
        self.assertEqual(module.KG_RUN_PY,
                         root / "workflows" / "knowledge-graph" / "scripts" / "run.py")

    def test_positive_the_set_root_is_the_one_audited(self):
        # The fixture's folder exists nowhere in the real project, so a report on
        # it can only come from auditing the fixture.
        done = self.audit(str(self.fixture), "--context", "workflows/only-in-fixture",
                          "--read-only")
        self.assertEqual(done.returncode, 0, done.stderr)
        # The report's separator is an em dash, built with chr() here.
        self.assertIn(f"`workflows/only-in-fixture/` {chr(0x2014)} Missing LOG.md",
                      done.stdout)

    def test_positive_the_logs_written_are_the_set_roots(self):
        (self.fixture / "workflows" / "audit").mkdir()
        done = self.audit(str(self.fixture), "--context", "workflows/only-in-fixture")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Running targeted CONTEXT.md maintenance audit for: "
                      "workflows/only-in-fixture.",
                      (self.fixture / "workflows" / "audit" / "LOG.md")
                      .read_text(encoding="utf-8"))
        self.assertIn("audit workflow ran.",
                      (self.fixture / "LOG.md").read_text(encoding="utf-8"))

    def test_rejection_a_bad_value_exits_2_before_auditing(self):
        no_agents = self.tmp / "no-agents"
        no_agents.mkdir()
        for value in (str(self.tmp / "missing"), str(no_agents), ""):
            with self.subTest(value=value):
                done = self.audit(value, "--context", "workflows/only-in-fixture",
                                  "--read-only")
                self.assertEqual(done.returncode, 2)
                self.assertIn("BOOK_DRAGON_ROOT", done.stderr)
                self.assertEqual(done.stdout, "")


class CleanCopyLogTests(FixtureCase):

    def findings(self, marker):
        directory = self.fixture / "workflows" / "only-in-fixture"
        env = {} if marker is None else {"BOOK_DRAGON_CLEAN_COPY": marker}
        with mock.patch.dict(os.environ, env):
            if marker is None:
                os.environ.pop("BOOK_DRAGON_CLEAN_COPY", None)
            return [(sev, msg) for sev, _, msg in run.audit_directory(directory)
                    if "LOG.md" in msg]

    def test_positive_with_the_marker_a_missing_log_is_info(self):
        self.assertEqual(self.findings("1"),
                         [("INFO", "LOG.md is checked on the host, not in the clean copy")])

    def test_rejection_without_the_marker_a_missing_log_fails(self):
        self.assertEqual(self.findings(None), [("FAIL", "Missing LOG.md")])

    def test_rejection_only_1_is_the_marker(self):
        for value in ("0", "true", "", "yes"):
            with self.subTest(value=value):
                self.assertEqual(self.findings(value), [("FAIL", "Missing LOG.md")])

    def test_negative_a_present_log_is_silent_either_way(self):
        (self.fixture / "workflows" / "only-in-fixture" / "LOG.md").write_bytes(b"")
        self.assertEqual(self.findings("1"), [])
        self.assertEqual(self.findings(None), [])


class CleanCopyContextTests(unittest.TestCase):
    """Decision 22: in the clean copy a missing CONTEXT.md that git ignores (a
    personal folder's) is INFO; one git does not ignore, or cannot answer for, is
    still a FAIL. The fixture is a git repository ignoring ``personal/*`` with a
    tracked ``personal/.gitkeep``, as the real project ignores ``wikis/*``."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.tmp / "project"
        for name in ("personal", "plain"):
            (self.root / name).mkdir(parents=True)
            (self.root / name / "LOG.md").write_bytes(b"")
        (self.root / ".gitignore").write_bytes(b"personal/*\n!personal/.gitkeep\n")
        (self.root / "personal" / ".gitkeep").write_bytes(b"")

    def git_init(self):
        subprocess.run(["git", "init", "-q"], cwd=str(self.root), check=True,
                       capture_output=True)

    def context_findings(self, name, marker):
        patcher = mock.patch.object(run, "PROJECT_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        with mock.patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": str(self.tmp)}):
            os.environ.pop("BOOK_DRAGON_CLEAN_COPY", None)
            if marker is not None:
                os.environ["BOOK_DRAGON_CLEAN_COPY"] = marker
            return [(sev, msg) for sev, _, msg in run.audit_directory(self.root / name)
                    if "CONTEXT.md" in msg]

    def test_positive_with_the_marker_an_ignored_context_is_info(self):
        self.git_init()
        self.assertEqual(self.context_findings("personal", "1"),
                         [("INFO", "CONTEXT.md is checked on the host, not in the clean copy")])

    def test_rejection_with_the_marker_an_unignored_context_still_fails(self):
        self.git_init()
        self.assertEqual(self.context_findings("plain", "1"),
                         [("FAIL", "Missing CONTEXT.md")])

    def test_rejection_without_the_marker_an_ignored_context_fails(self):
        self.git_init()
        for marker in (None, "true"):
            with self.subTest(marker=marker):
                self.assertEqual(self.context_findings("personal", marker),
                                 [("FAIL", "Missing CONTEXT.md")])

    def test_rejection_a_git_that_cannot_answer_leaves_it_a_fail(self):
        # No repository: git check-ignore errors rather than answering.
        self.assertEqual(self.context_findings("personal", "1"),
                         [("FAIL", "Missing CONTEXT.md")])


class CleanCopyHookTests(unittest.TestCase):
    """The full audit leaves personal-data and doc-sync to the host only with the
    marker, says so for each, and still runs every other check once."""

    def run_audit(self, marker):
        env = {} if marker is None else {"BOOK_DRAGON_CLEAN_COPY": marker}
        stubs = {name: mock.patch.object(run, name, return_value=[])
                 for name in ("collect_dirs", "check_python_scripts",
                              "run_graph_validation", *ADVISORY_CHECKS)}
        with mock.patch.dict(os.environ, env):
            if marker is None:
                os.environ.pop("BOOK_DRAGON_CLEAN_COPY", None)
            mocks = {name: patcher.start() for name, patcher in stubs.items()}
            try:
                findings, _ = run.run_audit(with_graph=True)
            finally:
                for patcher in stubs.values():
                    patcher.stop()
        return set(findings), {n: mocks[n].call_count for n in ADVISORY_CHECKS}

    def test_positive_with_the_marker_both_hooks_are_left_to_the_host(self):
        findings, calls = self.run_audit("1")
        self.assertEqual(findings & HOST_INFO, HOST_INFO)
        self.assertEqual(calls, {"run_encoding_check": 1, "run_personal_data_check": 0,
                                 "run_ai_style_check": 1, "run_doc_sync_check": 0,
                                 "run_skill_hardening_check": 1})

    def test_rejection_without_the_marker_both_hooks_run(self):
        findings, calls = self.run_audit(None)
        self.assertEqual(findings & HOST_INFO, set())
        self.assertEqual(calls, dict.fromkeys(ADVISORY_CHECKS, 1))

    def test_rejection_another_value_is_not_the_marker(self):
        findings, calls = self.run_audit("true")
        self.assertEqual(findings & HOST_INFO, set())
        self.assertEqual(calls, dict.fromkeys(ADVISORY_CHECKS, 1))


if __name__ == "__main__":
    unittest.main()
