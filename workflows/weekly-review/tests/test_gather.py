#!/usr/bin/env python3
"""Unit tests for the weekly-review deterministic gather readers."""
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import gather  # noqa: E402


JOURNAL = """# Journal - July 2026

## 1 July
Did the first thing.
Second line.

## 2 July

## 3 July
Third day work.
"""


class TestJournal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "entries").mkdir()
        (root / "entries" / "2026-07.md").write_bytes(JOURNAL.encode("utf-8"))
        self.journal_root = root

    def tearDown(self):
        self.tmp.cleanup()

    def test_sections_parse(self):
        sections = gather._month_sections(JOURNAL)
        self.assertTrue(sections[1])
        self.assertEqual(sections[2], "")   # empty day
        self.assertTrue(sections[3])

    def test_checker_content_vs_empty(self):
        has = gather.make_journal_checker(self.journal_root)
        self.assertTrue(has(date(2026, 7, 1)))
        self.assertFalse(has(date(2026, 7, 2)))   # heading present but blank
        self.assertTrue(has(date(2026, 7, 3)))
        self.assertFalse(has(date(2026, 7, 9)))   # no heading at all

    def test_checker_missing_month_file(self):
        has = gather.make_journal_checker(self.journal_root)
        self.assertFalse(has(date(2026, 8, 1)))   # no 2026-08.md

    def test_entries_returns_only_filled(self):
        entries = gather.journal_entries(
            self.journal_root, ["2026-07-01", "2026-07-02", "2026-07-03"])
        days = [iso for iso, _ in entries]
        self.assertEqual(days, ["2026-07-01", "2026-07-03"])  # 07-02 blank, excluded


class TestLogAndMemory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_log_entries_date_filter(self):
        (self.root / "LOG.md").write_bytes(
            ("[2026-06-25T10:00:00+01:00] | before window\n"
             "[2026-06-28T10:00:00+01:00] | in window\n"
             "[2026-07-05T10:00:00+01:00] | after window\n"
             "not a log line\n").encode("utf-8"))
        results = gather.log_entries(self.root, date(2026, 6, 26), date(2026, 7, 2))
        self.assertEqual(len(results), 1)
        _rel, hits = results[0]
        self.assertEqual(len(hits), 1)
        self.assertIn("in window", hits[0])

    def test_log_entries_skips_configured_dirs(self):
        data_dir = self.root / "workflows" / "x" / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "LOG.md").write_bytes("[2026-06-28T10:00:00+01:00] | should be skipped\n".encode("utf-8"))
        results = gather.log_entries(self.root, date(2026, 6, 26), date(2026, 7, 2))
        self.assertEqual(results, [])

    def test_memory_changes_filter(self):
        mem = self.root / "memory"
        mem.mkdir()
        (mem / "LOG.md").write_bytes(
            ("[2026-06-20T10:00:00+01:00] | old\n"
             "[2026-06-29T10:00:00+01:00] | recent change\n").encode("utf-8"))
        hits = gather.memory_changes(self.root, date(2026, 6, 26), date(2026, 7, 2))
        self.assertEqual(len(hits), 1)
        self.assertIn("recent change", hits[0])

    def test_memory_changes_absent(self):
        self.assertEqual(
            gather.memory_changes(self.root, date(2026, 6, 26), date(2026, 7, 2)), [])


class TestSessionsAndReviews(unittest.TestCase):
    def test_session_summary_no_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = gather.session_summary(
                Path(tmp) / "data", date(2026, 6, 26), date(2026, 7, 2))
            self.assertEqual(summary, {"total": 0, "by_ai": {}, "titles": []})

    def test_prior_reviews_excludes_context_log_and_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CONTEXT.md").write_bytes("# Reviews".encode("utf-8"))
            (root / "LOG.md").write_bytes("log line".encode("utf-8"))
            (root / "2026-W25.md").write_bytes("week 25 review".encode("utf-8"))
            (root / "2026-W26.md").write_bytes("week 26 review".encode("utf-8"))
            (root / "2026-W27.md").write_bytes("current draft".encode("utf-8"))
            picked = gather.prior_reviews(root, "2026-W27", 2)
            labels = [label for label, _ in picked]
            self.assertEqual(labels, ["2026-W25", "2026-W26"])  # no CONTEXT/LOG/current

    def test_prior_reviews_respects_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for wk in (24, 25, 26):
                (root / f"2026-W{wk}.md").write_bytes(f"week {wk}".encode("utf-8"))
            picked = gather.prior_reviews(root, "2026-W27", 1)
            self.assertEqual([l for l, _ in picked], ["2026-W26"])  # most recent only


class TestMisc(unittest.TestCase):
    def test_iso_week_label(self):
        self.assertEqual(gather.iso_week_label(date(2026, 7, 2)), "2026-W27")

    def test_git_commits_bad_path_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                gather.git_commits(tmp, date(2026, 6, 26), date(2026, 7, 2)), [])

    def test_git_commits_bounds_are_whole_days(self):
        # A date-only git bound means that date at the current time of day, which let
        # the run day's morning commits in and left the first day's morning out.
        import os
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            def git(*args, when=None):
                env = dict(os.environ)
                if when:
                    env.update(GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
                subprocess.run(["git", "-C", tmp, *args], check=True, env=env,
                               capture_output=True)
            git("init", "-q")
            git("config", "user.email", "t@example.invalid")
            git("config", "user.name", "t")
            for subject, when in (("before", "2026-09-25T23:30:00"),
                                  ("first-morning", "2026-09-26T00:30:00"),
                                  ("last-night", "2026-10-03T23:30:00"),
                                  ("run-day-morning", "2026-10-04T00:30:00")):
                git("commit", "-q", "--allow-empty", "-m", subject, when=when)
            commits = gather.git_commits(tmp, date(2026, 9, 26), date(2026, 10, 3))
        self.assertEqual(sorted(subject for _h, _d, subject in commits),
                         ["first-morning", "last-night"])

    def test_git_commits_are_dated_on_the_local_calendar(self):
        # A commit stamped with another timezone's offset is dated by its moment on
        # the local clock, the clock the bounds use, so it is neither dropped nor
        # counted twice at a boundary. Expectations come from this machine's clock.
        import os
        import subprocess
        from datetime import datetime, timezone
        moment = datetime(2026, 10, 3, 23, 30, tzinfo=timezone.utc)
        local = moment.astimezone().date()
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, GIT_AUTHOR_DATE="2026-10-03T23:30:00+00:00",
                       GIT_COMMITTER_DATE="2026-10-03T23:30:00+00:00")
            for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                         ["config", "user.name", "t"]):
                subprocess.run(["git", "-C", tmp, *args], check=True, capture_output=True)
            subprocess.run(["git", "-C", tmp, "commit", "-q", "--allow-empty", "-m",
                            "offset"], check=True, env=env, capture_output=True)
            own = gather.git_commits(tmp, local, local)
            other = gather.git_commits(tmp, local - timedelta(days=7),
                                       local - timedelta(days=1))
        self.assertEqual([(d, s) for _h, d, s in own], [(local.isoformat(), "offset")])
        self.assertEqual(other, [])

    def test_a_session_crossing_midnight_counts_once(self):
        # Counted on the day of its first message, so never in two reviews.
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "sessions-test.db"
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE sessions (timestamp TEXT, session_id TEXT, "
                         "session_title TEXT, ai_identity TEXT)")
            conn.executemany("INSERT INTO sessions VALUES (?, ?, ?, ?)", [
                ("2026-10-03T23:50:00", "late", "late night", "Claude Code"),
                ("2026-10-04T00:20:00", "late", "late night", "Claude Code"),
                ("2026-10-04T10:00:00", "next", "next day", "Codex CLI"),
            ])
            conn.commit()
            conn.close()
            first = gather.session_summary(tmp, date(2026, 9, 26), date(2026, 10, 3))
            second = gather.session_summary(tmp, date(2026, 10, 4), date(2026, 10, 10))
        self.assertEqual((first["total"], first["titles"]), (1, ["late night"]))
        self.assertEqual((second["total"], second["titles"]), (1, ["next day"]))

    def test_a_utc_session_start_is_dated_on_the_local_calendar(self):
        # The index stores UTC. A session whose first message is 23:20Z on 3 October
        # began on 4 October wherever local time is ahead of UTC, so it belongs to
        # that day's window there; the expectation is worked out on this machine's
        # clock, so the test holds in any timezone.
        import sqlite3
        from datetime import datetime, timezone
        stamp = "2026-10-03T23:20:00.000Z"
        local = datetime(2026, 10, 3, 23, 20, tzinfo=timezone.utc).astimezone().date()
        self.assertEqual(gather.local_day(stamp), local.isoformat())
        self.assertEqual(gather.local_day("2026-10-03T23:20:00"), "2026-10-03")
        self.assertIsNone(gather.local_day("not a time"))
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "sessions-test.db")
            conn.execute("CREATE TABLE sessions (timestamp TEXT, session_id TEXT, "
                         "session_title TEXT, ai_identity TEXT)")
            conn.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)",
                         (stamp, "edge", "edge", "Codex CLI"))
            conn.commit()
            conn.close()
            day = gather.session_summary(tmp, local, local)
            before = gather.session_summary(tmp, local - timedelta(days=7),
                                            local - timedelta(days=1))
        self.assertEqual(day["total"], 1)
        self.assertEqual(before["total"], 0)

    def test_build_packet_has_all_sections(self):
        packet = gather.build_packet(
            label="2026-W27", start=date(2026, 6, 26), end=date(2026, 7, 2),
            included_days=["2026-06-26"], empty_days=["2026-07-02"],
            journal=[("2026-06-26", "did a thing")],
            logs=[("LOG.md", ["[2026-06-28] | x"])],
            commits=[("abc1234", "2026-06-28", "a commit")],
            memory=["[2026-06-28] | memory change"],
            sessions={"total": 3, "by_ai": {"Claude Code": 3}, "titles": ["t"]},
            reviews=[("2026-W26", "prior review body")])
        for heading in ("Journal gaps", "Journal entries", "Session activity",
                        "Git commits", "Memory changes", "LOG.md activity",
                        "Prior reviews"):
            self.assertIn(heading, packet)
        self.assertIn("did a thing", packet)
        self.assertIn("a commit", packet)


if __name__ == "__main__":
    unittest.main()
