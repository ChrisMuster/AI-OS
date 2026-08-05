#!/usr/bin/env python3
"""Unit tests for the memory-diff log reader and categoriser."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import diff  # noqa: E402

CREATED = "[2026-07-01T09:00:00+01:00] | Actor: Biblio | Action: created | Note: Wrote project_a.md."
MODIFIED = "[2026-07-02T10:00:00+0100] | Actor: Biblio | Action: modified | Note: Updated backlog.md."
ARCHIVED = "[2026-07-03T00:00:00] | Actor: Biblio | Action: archived | Note: Archived project_b.md."
COMPLETED = "[2026-07-04T12:00:00+01:00] | Actor: Biblio | Action: completed | Note: Ran a workflow."


class TestReadLogEntries(unittest.TestCase):
    def _write(self, tmp, text):
        path = Path(tmp) / "LOG.md"
        path.write_bytes(text.encode("utf-8"))
        return path

    def test_parses_entries_and_skips_header_and_blanks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                tmp, f"# Memory - Log\n\n{CREATED}\n\n{MODIFIED}\n")
            self.assertEqual(diff.read_log_entries(path), [CREATED, MODIFIED])

    def test_missing_file_raises(self):
        # The memory log is the sole signal source; its absence is an anomaly to
        # surface, not an empty diff to swallow.
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(diff.LogError) as ctx:
                diff.read_log_entries(Path(tmp) / "nope.md")
            self.assertEqual(ctx.exception.reason, "missing")

    def test_empty_readable_log_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "# Memory - Log\n\n")
            self.assertEqual(diff.read_log_entries(path), [])

    def test_ignores_non_entry_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, f"Some prose that is not an entry.\n{CREATED}\n")
            self.assertEqual(diff.read_log_entries(path), [CREATED])

    def test_preserves_file_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, f"{CREATED}\n{MODIFIED}\n{ARCHIVED}\n")
            self.assertEqual(
                diff.read_log_entries(path), [CREATED, MODIFIED, ARCHIVED])


class TestParseEntry(unittest.TestCase):
    def test_parses_fields(self):
        parsed = diff.parse_entry(CREATED)
        self.assertEqual(parsed["actor"], "Biblio")
        self.assertEqual(parsed["action"], "created")
        self.assertEqual(parsed["note"], "Wrote project_a.md.")
        self.assertEqual(parsed["ts"], "2026-07-01T09:00:00+01:00")

    def test_non_entry_returns_none(self):
        self.assertIsNone(diff.parse_entry("not an entry"))


class TestCategorise(unittest.TestCase):
    def test_maps_actions_to_categories(self):
        groups = diff.categorise([CREATED, MODIFIED, ARCHIVED, COMPLETED])
        self.assertEqual([e["note"] for e in groups["Added"]], ["Wrote project_a.md."])
        self.assertEqual([e["note"] for e in groups["Updated"]], ["Updated backlog.md."])
        self.assertEqual([e["note"] for e in groups["Archived"]], ["Archived project_b.md."])
        self.assertEqual([e["note"] for e in groups["Other"]], ["Ran a workflow."])

    def test_all_categories_present_even_when_empty(self):
        groups = diff.categorise([CREATED])
        for name in diff.CATEGORIES:
            self.assertIn(name, groups)
        self.assertEqual(groups["Updated"], [])

    def test_preserves_order_within_category(self):
        second_created = CREATED.replace("project_a", "project_c")
        groups = diff.categorise([CREATED, second_created])
        self.assertEqual(
            [e["note"] for e in groups["Added"]],
            ["Wrote project_a.md.", "Wrote project_c.md."])


if __name__ == "__main__":
    unittest.main()
