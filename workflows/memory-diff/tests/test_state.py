#!/usr/bin/env python3
"""Unit tests for the memory-diff content watermark logic."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import state as state_mod  # noqa: E402

# A few realistic memory/LOG.md entry lines, oldest-first.
E1 = "[2026-07-01T09:00:00+01:00] | Actor: Biblio | Action: created | Note: Wrote project_a.md."
E2 = "[2026-07-02T10:00:00+01:00] | Actor: Biblio | Action: modified | Note: Updated backlog.md."
E3 = "[2026-07-03T11:00:00+01:00] | Actor: Biblio | Action: archived | Note: Archived project_b.md."


class TestLoadSave(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            payload = {"seen_line": E2}
            state_mod.save_state(path, payload)
            self.assertEqual(state_mod.load_state(path), payload)

    def test_missing_file_returns_empty(self):
        # A missing state file is a genuine first run: return {} silently.
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(state_mod.load_state(Path(tmp) / "nope.json"), {})

    def test_corrupt_file_raises(self):
        # A corrupt state file is an anomaly, not a first run: it must be surfaced.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(state_mod.StateError) as ctx:
                state_mod.load_state(path)
            self.assertEqual(ctx.exception.reason, "corrupt")

    def test_non_object_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("[1, 2, 3]", encoding="utf-8")
            with self.assertRaises(state_mod.StateError) as ctx:
                state_mod.load_state(path)
            self.assertEqual(ctx.exception.reason, "malformed")

    def test_empty_object_raises(self):
        # A present-but-empty object has no watermark: it must be loud (malformed),
        # never treated as a first run, or it would silently re-baseline.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(state_mod.StateError) as ctx:
                state_mod.load_state(path)
            self.assertEqual(ctx.exception.reason, "malformed")

    def test_empty_seen_line_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"seen_line": ""}', encoding="utf-8")
            with self.assertRaises(state_mod.StateError) as ctx:
                state_mod.load_state(path)
            self.assertEqual(ctx.exception.reason, "malformed")

    def test_whitespace_seen_line_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"seen_line": "   "}', encoding="utf-8")
            with self.assertRaises(state_mod.StateError) as ctx:
                state_mod.load_state(path)
            self.assertEqual(ctx.exception.reason, "malformed")

    def test_non_string_seen_line_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"seen_line": 123}', encoding="utf-8")
            with self.assertRaises(state_mod.StateError) as ctx:
                state_mod.load_state(path)
            self.assertEqual(ctx.exception.reason, "malformed")

    def test_extra_keys_tolerated(self):
        # A valid watermark plus unknown keys still loads: only seen_line matters.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps({"seen_line": E2, "note": "hand-added"}),
                            encoding="utf-8")
            loaded = state_mod.load_state(path)
            self.assertEqual(loaded["seen_line"], E2)


class TestLatestLine(unittest.TestCase):
    def test_empty(self):
        self.assertIsNone(state_mod.latest_line([]))

    def test_returns_last(self):
        self.assertEqual(state_mod.latest_line([E1, E2, E3]), E3)


class TestLineToken(unittest.TestCase):
    def test_none_for_none(self):
        self.assertIsNone(state_mod.line_token(None))

    def test_stable_for_same_line(self):
        self.assertEqual(state_mod.line_token(E1), state_mod.line_token(E1))

    def test_differs_between_lines(self):
        self.assertNotEqual(state_mod.line_token(E1), state_mod.line_token(E2))


class TestNewEntries(unittest.TestCase):
    def test_first_run_is_baseline(self):
        status, new = state_mod.new_entries([E1, E2, E3], {})
        self.assertEqual(status, state_mod.FIRST_RUN)
        self.assertEqual(new, [])

    def test_watermark_found_returns_following(self):
        status, new = state_mod.new_entries([E1, E2, E3], {"seen_line": E1})
        self.assertEqual(status, state_mod.OK)
        self.assertEqual(new, [E2, E3])

    def test_watermark_at_end_returns_nothing(self):
        status, new = state_mod.new_entries([E1, E2, E3], {"seen_line": E3})
        self.assertEqual(status, state_mod.OK)
        self.assertEqual(new, [])

    def test_missing_watermark_is_anomaly(self):
        # A stored watermark absent from a populated log is "cannot prove what
        # changed", not a silent re-baseline.
        status, new = state_mod.new_entries([E1, E2, E3], {"seen_line": "gone"})
        self.assertEqual(status, state_mod.WATERMARK_MISSING)
        self.assertEqual(new, [])

    def test_duplicate_line_uses_last_occurrence(self):
        # If the exact line text appears twice, the delta starts after the later one.
        status, new = state_mod.new_entries([E1, E2, E1, E3], {"seen_line": E1})
        self.assertEqual(status, state_mod.OK)
        self.assertEqual(new, [E3])

    def test_empty_log_with_watermark_is_anomaly(self):
        status, new = state_mod.new_entries([], {"seen_line": E1})
        self.assertEqual(status, state_mod.WATERMARK_MISSING)
        self.assertEqual(new, [])


if __name__ == "__main__":
    unittest.main()
