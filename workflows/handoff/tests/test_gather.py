#!/usr/bin/env python3
"""Unit tests for the handoff deterministic gather readers."""
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent.parent.parent
sys.path.insert(0, str(TESTS_DIR.parent / "scripts"))

import gather  # noqa: E402


class TestChangedDirs(unittest.TestCase):
    def test_unique_parents_and_root(self):
        entries = [
            ("M ", "AGENTS.md"),                       # root -> "."
            (" M", "workflows/handoff/scripts/run.py"),
            ("??", "workflows/handoff/scripts/gather.py"),
            ("A ", "workflows/triggers/config/triggers.yaml"),
        ]
        self.assertEqual(
            gather.changed_dirs(entries),
            [".", "workflows/handoff/scripts", "workflows/triggers/config"])

    def test_empty(self):
        self.assertEqual(gather.changed_dirs([]), [])


class TestLogTail(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_tail_and_entry_filter(self):
        (self.root / "sub").mkdir()
        (self.root / "sub" / "LOG.md").write_bytes(
            b"# Log\n"
            b"[2026-07-01T10:00:00+01:00] | one\n"
            b"not an entry line\n"
            b"[2026-07-02T10:00:00+01:00] | two\n"
            b"[2026-07-03T10:00:00+01:00] | three\n")
        tail = gather.log_tail(self.root, "sub", 2)
        self.assertEqual(len(tail), 2)
        self.assertIn("two", tail[0])
        self.assertIn("three", tail[1])

    def test_missing_log_is_empty(self):
        self.assertEqual(gather.log_tail(self.root, "nope", 5), [])

    def test_root_dir(self):
        (self.root / "LOG.md").write_bytes(
            b"[2026-07-03T10:00:00+01:00] | root entry\n")
        tail = gather.log_tail(self.root, ".", 5)
        self.assertEqual(len(tail), 1)
        self.assertIn("root entry", tail[0])


class TestActiveBacklog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "memory").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_active_top_level_titles(self):
        (self.root / "memory" / "backlog.md").write_bytes(
            b"# Task Backlog\n\n"
            b"## Active\n\n"
            b"- **First item** - description one.\n"
            b"  - **Nested child** - should be ignored.\n"
            b"- **Second item** - description two.\n\n"
            b"## Completed\n\n"
            b"- **Done item** - should be ignored.\n")
        titles = gather.active_backlog(self.root)
        self.assertEqual(titles, ["First item", "Second item"])

    def test_missing_backlog_is_empty(self):
        self.assertEqual(gather.active_backlog(self.root), [])


class TestRecentSessions(unittest.TestCase):
    def test_no_index_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                gather.recent_sessions(Path(tmp) / "data", date(2026, 7, 3), 1),
                {"total": 0, "by_ai": {}, "titles": []})

    def test_reads_shard_within_lookback(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            db = data / "sessions-test.db"
            conn = sqlite3.connect(db)
            conn.execute(
                "CREATE TABLE sessions (session_id TEXT, session_title TEXT, "
                "ai_identity TEXT, timestamp TEXT)")
            conn.executemany(
                "INSERT INTO sessions VALUES (?, ?, ?, ?)",
                [
                    ("s1", "Handoff build", "Claude Code", "2026-07-03T09:00:00Z"),
                    ("s1", "Handoff build", "Claude Code", "2026-07-03T09:05:00Z"),
                    ("s2", "", "Codex CLI", "2026-07-03T08:00:00Z"),
                    ("s3", "Old work", "Claude Code", "2026-06-01T08:00:00Z"),
                ])
            conn.commit()
            conn.close()
            summary = gather.recent_sessions(data, date(2026, 7, 3), 1)
            self.assertEqual(summary["total"], 2)            # s1, s2 (s3 too old)
            self.assertEqual(summary["by_ai"],
                             {"Claude Code": 1, "Codex CLI": 1})
            self.assertEqual(summary["titles"], ["Handoff build"])


class TestGitDegrade(unittest.TestCase):
    def test_bad_path_status_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            # A temp dir is not a git repo, so git returns non-zero -> empty.
            self.assertEqual(gather.git_status(tmp), [])
            self.assertEqual(gather.recent_commits(tmp, 5), [])
            self.assertEqual(gather.current_branch(tmp), "")

    def test_doc_sync_drift_degrades_without_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            # No doc-sync-guard under this root -> reader degrades to [].
            self.assertEqual(gather.doc_sync_drift(tmp), [])


class TestDocSyncDriftSeverity(unittest.TestCase):
    """The packet's drift section carries drift and nothing else.

    `doc_sync_drift` keeps `severity == "WARN"`, so the guard's DEGRADED
    findings (a component of the guard that could not run, today its
    output-inventory probe on an interpreter without PyYAML) never reach
    HANDOVER.md. That boundary is documented in this workflow's CONTEXT.md and
    in `workflows/handoff/scripts/CONTEXT.md`; these tests keep the
    documentation and the code from drifting apart.

    The guard is stubbed at the subprocess boundary rather than run for real, so
    the assertion is about the filter and not about the state of the working
    tree at test time.
    """

    DEGRADE = {"severity": "DEGRADED", "label": "doc-sync",
               "message": "output inventory unavailable - no doc-sync "
                          "exceptions applied"}
    DRIFT = {"severity": "WARN", "label": "doc-sync",
             "message": "workflows/foo: CONTEXT.md not updated for changes in "
                        "this directory"}

    def _drift_for(self, findings):
        payload = json.dumps({"findings": list(findings)})
        completed = mock.Mock(returncode=0, stdout=payload, stderr="")
        with mock.patch.object(gather.subprocess, "run",
                               return_value=completed):
            return gather.doc_sync_drift(PROJECT_ROOT)

    def test_drift_alone_is_returned(self):
        # Positive control: the stub payload does reach the reader, so an empty
        # result below is the filter's doing rather than a broken harness.
        self.assertEqual(self._drift_for([self.DRIFT]),
                         [self.DRIFT["message"]])

    def test_a_degrade_alone_returns_nothing(self):
        self.assertEqual(self._drift_for([self.DEGRADE]), [])

    def test_a_degrade_never_masks_real_drift(self):
        self.assertEqual(self._drift_for([self.DEGRADE, self.DRIFT]),
                         [self.DRIFT["message"]])


class TestBuildPacket(unittest.TestCase):
    def test_has_all_sections(self):
        packet = gather.build_packet(
            timestamp="2026-07-03T10:00:00+01:00",
            branch="feature/x",
            status=[("??", "workflows/handoff/")],
            diffstat=" AGENTS.md | 2 +-",
            commits=[("abc1234", "2026-07-03", "a commit")],
            dir_logs=[("workflows", ["[2026-07-03T10:00:00+01:00] | did a thing"])],
            backlog=["Some open item"],
            sessions={"total": 2, "by_ai": {"Claude Code": 2}, "titles": ["t"]})
        for heading in ("Working tree", "CONTEXT/LOG drift", "Line churn",
                        "Recent commits", "Recent LOG.md activity",
                        "Active backlog", "Recent session activity"):
            self.assertIn(heading, packet)
        self.assertIn("a commit", packet)
        self.assertIn("Some open item", packet)
        self.assertIn("feature/x", packet)

    def test_clean_tree_message(self):
        packet = gather.build_packet(
            timestamp="2026-07-03T10:00:00+01:00", branch="main",
            status=[], diffstat="", commits=[], dir_logs=[], backlog=[],
            sessions={"total": 0, "by_ai": {}, "titles": []})
        self.assertIn("Clean - no uncommitted changes.", packet)
        # Default doc_sync is empty -> the clean drift message.
        self.assertIn("every changed directory's CONTEXT.md / LOG.md is current",
                      packet)

    def test_doc_sync_section_lists_drift(self):
        packet = gather.build_packet(
            timestamp="2026-07-03T10:00:00+01:00", branch="main",
            status=[], diffstat="", commits=[], dir_logs=[], backlog=[],
            sessions={"total": 0, "by_ai": {}, "titles": []},
            doc_sync=["workflows/foo: CONTEXT.md not updated for changes in this "
                      "directory"])
        self.assertIn("Fix before handing off", packet)
        self.assertIn("workflows/foo: CONTEXT.md not updated", packet)


if __name__ == "__main__":
    unittest.main()
