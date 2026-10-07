#!/usr/bin/env python3
"""Tests for the personal-data guard's project-root override (orchestrator
isolation S3, plan 4.2).

``BOOK_DRAGON_ROOT`` names the project a copy of this guard scans: the files it
scans, ``USER.md``, ``.env`` and the denylist all follow it, and a value that is
not an existing folder holding AGENTS.md exits 2. The fixture is a temporary git
repository with a made-up name in its own USER.md and a made-up term in its own
denylist, each written into one committable file, so a hit on either can only
come from the fixture's markers being the ones read.

    python workflows/personal-data-guard/tests/test_root_override.py
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run.py"

FAKE_NAME = "Zedwick Quornby"
FAKE_TERM = "glimmerfrostvale"


class RootOverrideTests(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        root = self.root = self.tmp / "project"
        config = root / "workflows" / "personal-data-guard" / "config"
        config.mkdir(parents=True)
        (root / "AGENTS.md").write_bytes(b"# fixture\n")
        (root / ".gitignore").write_bytes(
            b"USER.md\nworkflows/personal-data-guard/config/denylist.txt\n")
        (root / "USER.md").write_bytes(f"**Name:** {FAKE_NAME}\n".encode())
        (config / "denylist.txt").write_bytes(f"{FAKE_TERM}\n".encode())
        (root / "notes.txt").write_bytes(f"Written by {FAKE_NAME}.\n".encode())
        (root / "terms.txt").write_bytes(f"A {FAKE_TERM} here.\n".encode())
        subprocess.run(["git", "init", "-q"], cwd=str(root), check=True,
                       capture_output=True)

    def guard(self, root):
        env = dict(os.environ, BOOK_DRAGON_ROOT=root)
        return subprocess.run([sys.executable, str(SCRIPT), "--check", "--json"],
                              env=env, capture_output=True, text=True,
                              encoding="utf-8", timeout=120)

    def test_positive_the_set_roots_files_and_markers_are_the_ones_used(self):
        done = self.guard(str(self.root))
        hits = {(f["severity"], f["file"], f["kind"])
                for f in json.loads(done.stdout)["findings"] if f.get("file")}
        self.assertEqual(hits, {("FAIL", "notes.txt", "personal_name"),
                                ("WARN", "terms.txt", "denylisted_term")})
        self.assertEqual(done.returncode, 1)  # a FAIL, as the guard documents

    def test_positive_every_project_path_follows_a_set_root(self):
        spec = importlib.util.spec_from_file_location("pdg_run_root_probe", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, {"BOOK_DRAGON_ROOT": str(self.root)}):
            spec.loader.exec_module(module)
        root = self.root.resolve()
        self.assertEqual(module.PROJECT_ROOT, root)
        self.assertEqual(module.USER_MD, root / "USER.md")
        self.assertEqual(module.ENV_FILE, root / ".env")
        self.assertEqual(module.DENYLIST_FILE, root / "workflows" / "personal-data-guard"
                         / "config" / "denylist.txt")

    def test_positive_a_completed_scan_says_so(self):
        # Code review R9-1: --json states whether the files were scanned.
        self.assertIs(json.loads(self.guard(str(self.root)).stdout)["scanned"], True)

    def test_rejection_a_scan_git_cannot_list_files_for_says_it_did_not_run(self):
        # A project folder that is not a git repository: git lists nothing, the
        # guard scans no file, exits 0, and must say so outside the message.
        bare = self.tmp / "not-a-repo"
        bare.mkdir()
        (bare / "AGENTS.md").write_bytes(b"# fixture\n")
        env = dict(os.environ, BOOK_DRAGON_ROOT=str(bare),
                   GIT_CEILING_DIRECTORIES=str(self.tmp))
        done = subprocess.run([sys.executable, str(SCRIPT), "--check", "--json"],
                              env=env, capture_output=True, text=True,
                              encoding="utf-8", timeout=120)
        payload = json.loads(done.stdout)
        self.assertEqual(done.returncode, 0)
        self.assertIs(payload["scanned"], False)
        self.assertEqual([f["severity"] for f in payload["findings"]
                          if f["message"].startswith("scan skipped")], ["INFO"])

    def test_negative_the_skip_note_is_still_the_plain_tuple(self):
        spec = importlib.util.spec_from_file_location("pdg_run_skip_probe", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with mock.patch.object(module.subprocess, "run", side_effect=OSError("no git")):
            paths, notes = module.committable_files(self.root)
        self.assertEqual(paths, [])
        self.assertEqual(notes, [("INFO", "personal-data",
                                  "scan skipped - could not run git (no git)")])
        self.assertIs(notes[0].skipped, True)
        self.assertIn('"scanned": false', module.findings_json(notes))

    def test_rejection_a_bad_value_exits_2_before_scanning(self):
        no_agents = self.tmp / "no-agents"
        no_agents.mkdir()
        for value in (str(self.tmp / "missing"), str(no_agents), ""):
            with self.subTest(value=value):
                done = self.guard(value)
                self.assertEqual(done.returncode, 2)
                self.assertIn("BOOK_DRAGON_ROOT", done.stderr)
                self.assertEqual(done.stdout, "")


if __name__ == "__main__":
    unittest.main()
