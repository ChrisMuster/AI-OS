#!/usr/bin/env python3
"""Unit tests for the handoff deterministic gather readers."""
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

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
        (self.root / "sub" / "LOG.md").write_text(
            "# Log\n"
            "[2026-07-01T10:00:00+01:00] | one\n"
            "not an entry line\n"
            "[2026-07-02T10:00:00+01:00] | two\n"
            "[2026-07-03T10:00:00+01:00] | three\n",
            encoding="utf-8")
        tail = gather.log_tail(self.root, "sub", 2)
        self.assertEqual(len(tail), 2)
        self.assertIn("two", tail[0])
        self.assertIn("three", tail[1])

    def test_missing_log_is_empty(self):
        self.assertEqual(gather.log_tail(self.root, "nope", 5), [])

    def test_root_dir(self):
        (self.root / "LOG.md").write_text(
            "[2026-07-03T10:00:00+01:00] | root entry\n", encoding="utf-8")
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
        (self.root / "memory" / "backlog.md").write_text(
            "# Task Backlog\n\n"
            "## Active\n\n"
            "- **First item** - description one.\n"
            "  - **Nested child** - should be ignored.\n"
            "- **Second item** - description two.\n\n"
            "## Completed\n\n"
            "- **Done item** - should be ignored.\n",
            encoding="utf-8")
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
        for heading in ("Working tree", "Line churn", "Recent commits",
                        "Recent LOG.md activity", "Active backlog",
                        "Recent session activity"):
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


if __name__ == "__main__":
    unittest.main()
