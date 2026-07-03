#!/usr/bin/env python3
"""Unit tests for the handoff seen-watermark and handoff-timestamp logic."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import state as state_mod  # noqa: E402


class TestLoadSave(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            payload = {"seen_handoff": "2026-07-03T10:00:00+01:00"}
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


class TestReadHandoffTimestamp(unittest.TestCase):
    def test_reads_created_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "HANDOVER.md"
            path.write_text(
                "# Handover\n\n**Created:** 2026-07-03T10:00:00+01:00  \n"
                "**Branch:** feature/x\n",
                encoding="utf-8")
            self.assertEqual(
                state_mod.read_handoff_timestamp(path), "2026-07-03T10:00:00+01:00")

    def test_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                state_mod.read_handoff_timestamp(Path(tmp) / "nope.md"), "")

    def test_falls_back_to_mtime_without_created_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "HANDOVER.md"
            path.write_text("# Handover\n\nNo created line here.\n", encoding="utf-8")
            ts = state_mod.read_handoff_timestamp(path)
            self.assertTrue(ts)                 # non-empty
            self.assertIn("T", ts)              # ISO-ish timestamp


class TestIsUnread(unittest.TestCase):
    def test_no_handoff_is_not_unread(self):
        self.assertFalse(state_mod.is_unread("", {"seen_handoff": "x"}))

    def test_new_handoff_is_unread(self):
        self.assertTrue(
            state_mod.is_unread("2026-07-03T10:00:00+01:00", {}))

    def test_differing_timestamp_is_unread(self):
        self.assertTrue(state_mod.is_unread(
            "2026-07-03T11:00:00+01:00",
            {"seen_handoff": "2026-07-03T10:00:00+01:00"}))

    def test_seen_timestamp_is_not_unread(self):
        ts = "2026-07-03T10:00:00+01:00"
        self.assertFalse(state_mod.is_unread(ts, {"seen_handoff": ts}))


if __name__ == "__main__":
    unittest.main()
