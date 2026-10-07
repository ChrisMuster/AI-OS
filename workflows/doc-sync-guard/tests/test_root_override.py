#!/usr/bin/env python3
"""Tests for the doc-sync guard's project-root override (orchestrator isolation
S3, plan 4.2).

``BOOK_DRAGON_ROOT`` names the project a copy of this guard checks: its git
repository and its CONTEXT.md and LOG.md files follow it, while the output
inventory, configuration shipped beside the script, is still read from there. A
value that is not an existing folder holding AGENTS.md exits 2. The fixture is
the suite's own throwaway repository (``RepoFixture``), whose ``workflows/foo``
exists nowhere in the real project.

    python workflows/doc-sync-guard/tests/test_root_override.py
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "scripts"
SCRIPT = SCRIPTS / "run.py"
sys.path.insert(0, str(TESTS))

from test_doc_sync_guard import RepoFixture, _git  # noqa: E402


class RootOverrideTests(unittest.TestCase):

    def setUp(self):
        self.repo = RepoFixture()
        self.addCleanup(self.repo.cleanup)
        self.repo.write("AGENTS.md", "# fixture\n")
        _git(self.repo.dir, "add", "AGENTS.md")
        _git(self.repo.dir, "commit", "-q", "-m", "agents", "--no-verify")

    def guard(self, root):
        env = dict(os.environ, BOOK_DRAGON_ROOT=root)
        return subprocess.run([sys.executable, str(SCRIPT), "--check", "--json"],
                              env=env, capture_output=True, text=True,
                              encoding="utf-8", timeout=120)

    def findings(self, done):
        self.assertEqual(done.returncode, 0, done.stderr)
        return [(f["severity"], f["message"]) for f in json.loads(done.stdout)["findings"]]

    def test_negative_control_the_fixture_at_rest_is_clean(self):
        self.assertEqual(self.findings(self.guard(str(self.repo.dir))), [])

    def test_positive_the_set_roots_change_is_the_one_checked(self):
        # A content change in workflows/foo with no CONTEXT.md update: drift that
        # exists only in the fixture. No DEGRADED either, so the inventory was
        # found beside the script, not looked for under the fixture.
        self.repo.write("workflows/foo/run.py", "print('v2')\n")
        self.repo.append_log("workflows/foo/LOG.md", time.time() + 5)
        found = self.findings(self.guard(str(self.repo.dir)))
        self.assertTrue(any(sev == "WARN" and msg.startswith("workflows/foo")
                            and "CONTEXT.md not updated" in msg
                            for sev, msg in found), found)
        self.assertEqual([f for f in found if f[0] == "DEGRADED"], [])

    def test_positive_the_root_follows_and_the_inventory_stays_beside_the_script(self):
        spec = importlib.util.spec_from_file_location("dsg_run_root_probe", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, {"BOOK_DRAGON_ROOT": str(self.repo.dir)}):
            spec.loader.exec_module(module)
        self.assertEqual(module.PROJECT_ROOT, self.repo.dir.resolve())
        import inventory
        self.assertEqual(inventory.DEFAULT_CONFIG,
                         SCRIPTS.parent / "config" / "output-inventory.yaml")

    def test_rejection_a_bad_value_exits_2_before_checking(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        tmp = Path(holder.name)
        (tmp / "no-agents").mkdir()
        for value in (str(tmp / "missing"), str(tmp / "no-agents"), ""):
            with self.subTest(value=value):
                done = self.guard(value)
                self.assertEqual(done.returncode, 2)
                self.assertIn("BOOK_DRAGON_ROOT", done.stderr)
                self.assertEqual(done.stdout, "")


if __name__ == "__main__":
    unittest.main()
