#!/usr/bin/env python3
"""Unit tests for the weekly-review coverage state and backfill logic."""
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import state as state_mod  # noqa: E402


def filled(*iso_dates):
    """Return has_content(date) -> bool backed by a set of ISO date strings."""
    have = set(iso_dates)
    return lambda d: d.isoformat() in have


class TestComputeWindow(unittest.TestCase):
    def test_fallback_without_watermark(self):
        start, end = state_mod.compute_window({}, date(2026, 7, 2), 7, 31)
        self.assertEqual(end, date(2026, 7, 2))
        self.assertEqual(start, date(2026, 6, 26))  # 7-day inclusive look-back

    def test_starts_day_after_watermark(self):
        state = {"last_reviewed_through": "2026-06-27"}
        start, end = state_mod.compute_window(state, date(2026, 7, 4), 7, 31)
        self.assertEqual(start, date(2026, 6, 28))
        self.assertEqual(end, date(2026, 7, 4))

    def test_span_capped_at_max(self):
        state = {"last_reviewed_through": "2026-01-01"}
        start, end = state_mod.compute_window(state, date(2026, 7, 4), 7, 31)
        self.assertEqual(start, date(2026, 6, 4))  # end - 30 days

    def test_already_reviewed_through_today_is_thin(self):
        state = {"last_reviewed_through": "2026-07-04"}
        start, end = state_mod.compute_window(state, date(2026, 7, 4), 7, 31)
        self.assertEqual(start, end)  # start clamped to end; nothing new

    def test_corrupt_watermark_falls_back(self):
        state = {"last_reviewed_through": "not-a-date"}
        start, end = state_mod.compute_window(state, date(2026, 7, 2), 7, 31)
        self.assertEqual(start, date(2026, 6, 26))


class TestRunDayLeftOut(unittest.TestCase):
    """Nothing from the run day belongs to its own review, for any source: the window
    and the watermark both end the day before (the user's rule, 2026-10-04, after W39
    and W40 each had to be corrected by hand)."""

    def test_review_ends_the_day_before_the_run_day(self):
        self.assertEqual(state_mod.review_end(date(2026, 10, 4)), date(2026, 10, 3))
        self.assertEqual(state_mod.review_end(date(2026, 3, 1)), date(2026, 2, 28))

    def test_the_run_day_opens_the_next_window(self):
        # W40, run on 4 October after W39 covered through 25 September.
        run_day = date(2026, 10, 4)
        start, end = state_mod.compute_window(
            {"last_reviewed_through": "2026-09-25"}, state_mod.review_end(run_day), 7, 31)
        self.assertEqual((start, end), (date(2026, 9, 26), date(2026, 10, 3)))
        # --record writes `end` as the watermark; the next review starts on the run day.
        start, end = state_mod.compute_window(
            {"last_reviewed_through": end.isoformat()},
            state_mod.review_end(date(2026, 10, 11)), 7, 31)
        self.assertEqual((start, end), (date(2026, 10, 4), date(2026, 10, 10)))

    def test_the_run_day_is_neither_included_nor_carried(self):
        # It lies outside the window, so it is simply the next window's first day.
        run_day = date(2026, 10, 4)
        included, pending = state_mod.resolve_journal_days(
            {}, date(2026, 9, 26), state_mod.review_end(run_day),
            filled("2026-10-03", "2026-10-04"), 28, run_day=run_day)
        self.assertEqual(included, ["2026-10-03"])
        self.assertNotIn("2026-10-04", pending)

    def test_run_resolve_ends_every_source_the_day_before(self):
        # run.py's own window, with its state and journal paths pointed at a fixture.
        import run as run_mod
        with tempfile.TemporaryDirectory() as tmp:
            saved = run_mod._STATE_PATH, run_mod._JOURNAL_ROOT
            run_mod._STATE_PATH = Path(tmp) / "state.json"
            run_mod._JOURNAL_ROOT = Path(tmp) / "journal"
            try:
                state_mod.save_state(run_mod._STATE_PATH,
                                     {"last_reviewed_through": "2026-09-25"})
                _st, start, end, _inc, _pend, empty = run_mod._resolve(date(2026, 10, 4))
            finally:
                run_mod._STATE_PATH, run_mod._JOURNAL_ROOT = saved
        self.assertEqual((start, end), (date(2026, 9, 26), date(2026, 10, 3)))
        self.assertNotIn("2026-10-04", empty)

    def test_a_pending_day_after_the_window_is_kept_not_included(self):
        # The state W40 left: 4 October pending. Replayed on 4 October, it must not
        # bring that day's journal into a window ending on 3 October.
        included, pending = state_mod.resolve_journal_days(
            {"pending_days": ["2026-10-04"]}, date(2026, 9, 26), date(2026, 10, 3),
            filled("2026-10-03", "2026-10-04"), 28, run_day=date(2026, 10, 4))
        self.assertNotIn("2026-10-04", included)
        self.assertIn("2026-10-04", pending)

class TestRecordRefusals(unittest.TestCase):
    """A review is weekly: once this week's is recorded, the gather and --record both
    refuse until the next is due (the user's rule, 2026-10-05: no second review in a
    week, no part-weeks). --record also refuses a file that does not state its
    window."""

    def setUp(self):
        import argparse
        import run as run_mod
        self.run = run_mod
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        names = ("_STATE_PATH", "_JOURNAL_ROOT", "_REVIEWS_DIR", "_LAST_RUN_PATH",
                 "_LOG_PATH")
        self.saved = {n: getattr(run_mod, n) for n in names}
        run_mod._STATE_PATH = base / "state.json"
        run_mod._JOURNAL_ROOT = base / "journal"
        run_mod._REVIEWS_DIR = base / "reviews"
        run_mod._LAST_RUN_PATH = base / ".last-run"
        run_mod._LOG_PATH = base / "LOG.md"
        run_mod._REVIEWS_DIR.mkdir()
        self.args = argparse.Namespace(today=None, dry_run=False, json=False)

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(self.run, name, value)
        self.tmp.cleanup()

    def prepare(self, last_run_days_ago, review_text):
        # The run date is 4 October (args.today); the last review was recorded that
        # many calendar days earlier, at 18:15 local time.
        from datetime import datetime, timedelta
        state_mod.save_state(self.run._STATE_PATH, {"last_reviewed_through": "2026-09-25"})
        if last_run_days_ago is not None:
            stamp = (datetime(2026, 10, 4, 18, 15).astimezone()
                     - timedelta(days=last_run_days_ago)).isoformat()
            self.run._LAST_RUN_PATH.write_bytes((stamp + "\n").encode("utf-8"))
        if review_text is not None:
            (self.run._REVIEWS_DIR / "2026-W40.md").write_bytes(review_text.encode("utf-8"))
        self.args.today = "2026-10-04"
        self.before = (self.run._STATE_PATH.read_bytes(),
                       self.run._LAST_RUN_PATH.read_bytes()
                       if self.run._LAST_RUN_PATH.exists() else None)

    def unchanged(self):
        after = (self.run._STATE_PATH.read_bytes(),
                 self.run._LAST_RUN_PATH.read_bytes()
                 if self.run._LAST_RUN_PATH.exists() else None)
        self.assertEqual(after, self.before)

    def test_positive_a_due_review_stating_its_window_is_recorded(self):
        for days_ago in (None, 7, 8):  # never run, due today, overdue
            with self.subTest(days_ago=days_ago):
                self.prepare(days_ago, "**Window:** 2026-09-26 to 2026-10-03\n")
                self.assertEqual(self.run.cmd_record(self.args), 0)
                saved = state_mod.load_state(self.run._STATE_PATH)
                self.assertEqual(saved["last_reviewed_through"], "2026-10-03")

    def test_rejection_this_weeks_review_already_done(self):
        # Recorded today, yesterday, or six days ago: nothing to do until it is due.
        for days_ago in (0, 1, 6):
            with self.subTest(days_ago=days_ago):
                self.prepare(days_ago, "**Window:** 2026-09-26 to 2026-10-03\n")
                self.assertEqual(self.run.cmd_record(self.args), 1)
                self.assertEqual(self.run.cmd_gather(self.args), 1)
                self.unchanged()

    def test_positive_due_on_the_morning_of_the_due_date(self):
        # Recorded on 4 October at 18:15: due on 11 October from midnight, by calendar
        # day, not only after 18:15; still refused on 10 October. --today is the date.
        from datetime import datetime
        self.prepare(None, None)
        stamp = datetime(2026, 10, 4, 18, 15).astimezone().isoformat()
        self.run._LAST_RUN_PATH.write_bytes((stamp + "\n").encode("utf-8"))
        self.assertIsNone(self.run._already_done(date(2026, 10, 11)))
        self.assertIn("2026-10-11", self.run._already_done(date(2026, 10, 10)))

    def test_rejection_a_file_that_does_not_state_this_window(self):
        self.prepare(8, "**Window:** 2026-09-19 to 2026-09-25\n")
        self.assertEqual(self.run.cmd_record(self.args), 1)
        self.unchanged()


class TestResolveJournalDays(unittest.TestCase):
    def test_window_split_into_included_and_pending(self):
        has = filled("2026-06-28", "2026-06-30")
        included, pending = state_mod.resolve_journal_days(
            {}, date(2026, 6, 28), date(2026, 6, 30), has, 28)
        self.assertEqual(included, ["2026-06-28", "2026-06-30"])
        self.assertEqual(pending, ["2026-06-29"])

    def test_backfilled_pending_day_is_picked_up(self):
        # 2026-06-20 was empty at last review, now backfilled; it is OUTSIDE the
        # current window but must still be included.
        state = {"pending_days": ["2026-06-20"]}
        has = filled("2026-06-20", "2026-06-28")
        included, pending = state_mod.resolve_journal_days(
            state, date(2026, 6, 28), date(2026, 6, 28), has, 28)
        self.assertIn("2026-06-20", included)   # backfilled, caught late
        self.assertIn("2026-06-28", included)
        self.assertNotIn("2026-06-20", pending)

    def test_still_empty_pending_day_carried_within_horizon(self):
        state = {"pending_days": ["2026-06-20"]}
        has = filled()  # nothing has content
        included, pending = state_mod.resolve_journal_days(
            state, date(2026, 6, 28), date(2026, 6, 28), has, 28)
        self.assertEqual(included, [])
        self.assertIn("2026-06-20", pending)    # still empty, still tracked

    def test_pending_day_dropped_past_horizon(self):
        state = {"pending_days": ["2026-05-01"]}
        has = filled()
        included, pending = state_mod.resolve_journal_days(
            state, date(2026, 6, 28), date(2026, 6, 28), has, 28)
        self.assertNotIn("2026-05-01", pending)  # beyond carry-forward horizon
        self.assertEqual(included, [])

    def test_backfilled_day_beyond_horizon_is_dropped_not_included(self):
        # Even if a very old pending day is backfilled, once past the horizon it
        # is no longer tracked; it will not resurface. Documents the boundary.
        state = {"pending_days": ["2026-05-01"]}
        has = filled("2026-05-01")
        included, pending = state_mod.resolve_journal_days(
            state, date(2026, 6, 28), date(2026, 6, 28), has, 28)
        self.assertNotIn("2026-05-01", included)
        self.assertNotIn("2026-05-01", pending)

    def test_corrupt_pending_entry_ignored(self):
        state = {"pending_days": ["garbage", None]}
        has = filled("2026-06-28")
        included, pending = state_mod.resolve_journal_days(
            state, date(2026, 6, 28), date(2026, 6, 28), has, 28)
        self.assertEqual(included, ["2026-06-28"])
        self.assertEqual(pending, [])

    def test_run_day_excluded_from_inclusion_and_deferred(self):
        # The run day (2026-06-30) must not be counted in its own review even
        # though it has content; it is deferred to pending for a later review.
        has = filled("2026-06-28", "2026-06-30")
        included, pending = state_mod.resolve_journal_days(
            {}, date(2026, 6, 28), date(2026, 6, 30), has, 28, run_day=date(2026, 6, 30))
        self.assertEqual(included, ["2026-06-28"])          # 06-30 not included
        self.assertIn("2026-06-30", pending)                # deferred
        self.assertIn("2026-06-29", pending)                # genuinely empty

    def test_run_day_deferred_when_empty(self):
        has = filled()
        included, pending = state_mod.resolve_journal_days(
            {}, date(2026, 7, 3), date(2026, 7, 3), has, 28, run_day=date(2026, 7, 3))
        self.assertEqual(included, [])
        self.assertEqual(pending, ["2026-07-03"])           # today, carried forward

    def test_deferred_run_day_caught_by_next_review(self):
        # A run day deferred last time (2026-07-03) is now a past day with content;
        # the next review (run day 2026-07-10) must pick it up.
        state = {"pending_days": ["2026-07-03"]}
        has = filled("2026-07-03")
        included, pending = state_mod.resolve_journal_days(
            state, date(2026, 7, 4), date(2026, 7, 10), has, 28, run_day=date(2026, 7, 10))
        self.assertIn("2026-07-03", included)               # caught late
        self.assertIn("2026-07-10", pending)                # new run day deferred
        self.assertNotIn("2026-07-03", pending)


class TestLoadSave(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            payload = {"last_reviewed_through": "2026-07-02", "pending_days": ["2026-06-29"]}
            state_mod.save_state(path, payload)
            self.assertEqual(state_mod.load_state(path), payload)

    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(state_mod.load_state(Path(tmp) / "nope.json"), {})

    def test_corrupt_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_bytes("{not json".encode("utf-8"))
            self.assertEqual(state_mod.load_state(path), {})


if __name__ == "__main__":
    unittest.main()
