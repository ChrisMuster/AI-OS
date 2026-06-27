#!/usr/bin/env python3
"""Unit tests for report formatting."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import report  # noqa: E402


class TestReport(unittest.TestCase):
    def test_count_updates(self):
        results = [
            {"category": "Python packages", "name": "a", "installed": "1.0.0",
             "latest": "1.1.0", "change": "minor"},
            {"category": "AI CLI tools", "name": "b", "installed": "1.0.0",
             "latest": "1.0.0", "change": "up to date"},
            {"category": "AI CLI tools", "name": "c", "installed": None,
             "latest": None, "change": "not installed"},
        ]
        self.assertEqual(report.count_updates(results), 1)

    def test_contains_sections(self):
        results = [
            {"category": "Python packages", "name": "requests", "installed": "2.31.0",
             "latest": "2.32.3", "change": "minor", "note": ""},
        ]
        text = report.build_report(results, {"PyPI": "ok"}, "2026-06-26T18:00:00+01:00")
        self.assertIn("PYTHON PACKAGES", text)
        self.assertIn("requests", text)
        self.assertIn("1 update(s) available.", text)

    def test_all_up_to_date(self):
        text = report.build_report([], {"PyPI": "ok"}, "ts")
        self.assertIn("Everything is up to date.", text)

    def test_suggest_commands(self):
        results = [
            {"category": "Python packages", "name": "requests", "installed": "2.31.0",
             "latest": "2.32.3", "change": "minor", "note": ""},
            {"category": "AI CLI tools", "name": "Claude Code", "installed": "1.0.0",
             "latest": "1.2.0", "change": "minor", "note": "",
             "upgrade": "npm install -g @anthropic-ai/claude-code@latest"},
        ]
        text = report.build_report(results, {}, "ts", suggest_commands=True)
        self.assertIn("pip install -U requests", text)
        self.assertIn("npm install -g @anthropic-ai/claude-code@latest", text)


if __name__ == "__main__":
    unittest.main()
