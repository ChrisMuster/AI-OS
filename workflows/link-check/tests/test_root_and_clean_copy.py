#!/usr/bin/env python3
"""Tests for link-check's project-root override and clean-copy mode (orchestrator
isolation S3, plan 4.2 and 4.3).

The fixture is a temporary git repository whose ``.gitignore`` ignores
``USER.md``. Its one CONTEXT.md, in a folder that exists nowhere in the real
project, links to three targets: a file that exists, ``USER`` (absent and ignored)
and ``workflows/no-such-workflow/CONTEXT`` (absent and not ignored). With
``BOOK_DRAGON_CLEAN_COPY=1`` (only ``1``) the ignored one is "checked on the host"
and not dead; the other stays dead. The log paths are temporary files.

    python workflows/link-check/tests/test_root_and_clean_copy.py
"""

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run.py"


def load(name, env=None):
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    with mock.patch.dict(os.environ, env or {}):
        spec.loader.exec_module(module)
    return module


run = load("link_check_run_root")

BODY = ("# X\n\nSee [[workflows/live/CONTEXT]], [[USER]] and "
        "[[workflows/no-such-workflow/CONTEXT]].\n")
CONTEXT_REL = "workflows/x-only-in-fixture/CONTEXT.md"
SEED = b"[2026-01-01T00:00:00+00:00] | Actor: Biblio | Action: created | Note: seed\n"


def without(*names):
    return {k: v for k, v in os.environ.items() if k.upper() not in names}


class Fixture(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.tmp / "project"
        self.make_project(self.root, git=True)
        for name, value in (("WORKFLOW_LOG", self.tmp / "workflow-LOG.md"),
                            ("ROOT_LOG", self.tmp / "root-LOG.md")):
            Path(value).write_bytes(SEED)
            patcher = mock.patch.object(run, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_project(self, root, git):
        (root / "workflows" / "x-only-in-fixture").mkdir(parents=True)
        (root / "workflows" / "live").mkdir()
        (root / "workflows" / "live" / "CONTEXT.md").write_bytes(b"# Live\n")
        (root / "AGENTS.md").write_bytes(b"# fixture\n")
        (root / ".gitignore").write_bytes(b"USER.md\n")
        (root / CONTEXT_REL).write_bytes(BODY.encode("utf-8"))
        if git:
            subprocess.run(["git", "init", "-q"], cwd=str(root), check=True,
                           capture_output=True)

    def point_at(self, root):
        context = root / CONTEXT_REL
        for name, value in (("PROJECT_ROOT", root),
                            ("collect_context_files", lambda: [context])):
            patcher = mock.patch.object(run, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def marker(self, value):
        """os.environ with the marker set to ``value``, or removed for None."""
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("BOOK_DRAGON_CLEAN_COPY", None)
        if value is not None:
            os.environ["BOOK_DRAGON_CLEAN_COPY"] = value

    def statuses(self):
        return {target: status for _, target, status in run.audit_links()}

    def main(self, *args):
        out = io.StringIO()
        with mock.patch.object(run.sys, "argv", ["run.py", *args]), \
                redirect_stdout(out), redirect_stderr(io.StringIO()):
            run.main()
        return out.getvalue()


class CleanCopyTests(Fixture):

    def test_positive_with_the_marker_an_absent_ignored_target_is_checked_on_the_host(self):
        self.point_at(self.root)
        self.marker("1")
        self.assertEqual(self.statuses(), {
            "workflows/live/CONTEXT": "ok", "USER": "host",
            "workflows/no-such-workflow/CONTEXT": "dead"})

    def test_rejection_without_the_marker_both_absent_targets_are_dead(self):
        self.point_at(self.root)
        self.marker(None)
        self.assertEqual(self.statuses(), {
            "workflows/live/CONTEXT": "ok", "USER": "dead",
            "workflows/no-such-workflow/CONTEXT": "dead"})

    def test_rejection_only_1_is_the_marker(self):
        self.point_at(self.root)
        for value in ("0", "true", ""):
            with self.subTest(value=value):
                self.marker(value)
                self.assertEqual(self.statuses()["USER"], "dead")

    def test_rejection_a_git_that_cannot_answer_leaves_the_link_dead(self):
        bare = self.tmp / "no-git"
        self.make_project(bare, git=False)
        self.point_at(bare)
        self.marker("1")
        with mock.patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": str(self.tmp)}):
            self.assertEqual(self.statuses()["USER"], "dead")

    def test_positive_the_close_out_gate_counts_only_the_unignored_target(self):
        self.point_at(self.root)
        self.marker("1")
        report, dead_count, _ = run.run_audit_mode(fix=False, dry_run=False)
        self.assertEqual(dead_count, 1)
        self.assertIn("**Checked on the host:** 1", report)
        self.assertIn("## Checked on the host", report)
        self.assertIn("- `workflows/x-only-in-fixture/CONTEXT.md` - `[[USER]]`", report)

    def test_negative_without_the_marker_the_report_has_no_host_section(self):
        self.point_at(self.root)
        self.marker(None)
        report, dead_count, _ = run.run_audit_mode(fix=False, dry_run=False)
        self.assertEqual(dead_count, 2)
        self.assertNotIn("Checked on the host", report)

    def test_positive_json_lists_only_the_dead_target(self):
        self.point_at(self.root)
        self.marker("1")
        self.assertEqual(json.loads(self.main("--audit", "--json", "--no-log")),
                         {"dead_links": [
                             {"file": "workflows/x-only-in-fixture/CONTEXT.md",
                              "target": "workflows/no-such-workflow/CONTEXT"}]})


class RootOverrideTests(Fixture):

    def check(self, root, *args):
        env = dict(without("BOOK_DRAGON_CLEAN_COPY"), BOOK_DRAGON_ROOT=root)
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=env,
                              capture_output=True, text=True, encoding="utf-8",
                              timeout=120)

    def test_positive_the_set_root_is_the_one_checked(self):
        done = self.check(str(self.root), "--audit", "--json", "--no-log")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {"dead_links": [
            {"file": "workflows/x-only-in-fixture/CONTEXT.md", "target": "USER"},
            {"file": "workflows/x-only-in-fixture/CONTEXT.md",
             "target": "workflows/no-such-workflow/CONTEXT"}]})

    def test_positive_every_project_path_follows_a_set_root(self):
        module = load("link_check_run_root_probe", {"BOOK_DRAGON_ROOT": str(self.root)})
        root = self.root.resolve()
        self.assertEqual(module.PROJECT_ROOT, root)
        self.assertEqual(module.WORKFLOW_LOG, root / "workflows" / "link-check" / "LOG.md")
        self.assertEqual(module.ROOT_LOG, root / "LOG.md")

    def test_rejection_a_bad_value_exits_2_before_checking(self):
        no_agents = self.tmp / "no-agents"
        no_agents.mkdir()
        for value in (str(self.tmp / "missing"), str(no_agents), ""):
            with self.subTest(value=value):
                done = self.check(value, "--audit", "--json", "--no-log")
                self.assertEqual(done.returncode, 2)
                self.assertIn("BOOK_DRAGON_ROOT", done.stderr)
                self.assertEqual(done.stdout, "")


if __name__ == "__main__":
    unittest.main()
