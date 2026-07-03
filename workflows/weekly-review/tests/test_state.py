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
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(state_mod.load_state(path), {})


if __name__ == "__main__":
    unittest.main()
