#!/usr/bin/env python3
"""Unit tests for the trigger-phrase registry loader and renderer."""
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import run as run_mod  # noqa: E402

_REGISTRY = Path(__file__).resolve().parent.parent / "config" / "triggers.yaml"

SAMPLE = """categories:
  - name: handoff
    summary: Save session state.
    runs: python x.py
    phrases:
      - do the handoff
      - hand off
  - name: audit
    summary: Run the audit.
    runs: python y.py
    phrases:
      - run the audit
"""


class TestLoad(unittest.TestCase):
    def test_loads_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "triggers.yaml"
            path.write_bytes(SAMPLE.encode("utf-8"))
            cats = run_mod.load_registry(path)
            self.assertEqual([c["name"] for c in cats], ["handoff", "audit"])

    def test_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run_mod.load_registry(Path(tmp) / "nope.yaml"), [])

    def test_malformed_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "triggers.yaml"
            path.write_bytes("categories: [ : : ]\n: bad".encode("utf-8"))
            self.assertEqual(run_mod.load_registry(path), [])

    def test_entries_without_name_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "triggers.yaml"
            path.write_bytes("categories:\n  - summary: no name here\n  - name: ok\n".encode("utf-8"))
            cats = run_mod.load_registry(path)
            self.assertEqual([c["name"] for c in cats], ["ok"])


class TestFilter(unittest.TestCase):
    def setUp(self):
        self.cats = [{"name": "handoff"}, {"name": "audit"}]

    def test_no_filter_returns_all(self):
        self.assertEqual(run_mod.filter_categories(self.cats, None), self.cats)

    def test_case_insensitive_match(self):
        self.assertEqual(
            [c["name"] for c in run_mod.filter_categories(self.cats, "HANDOFF")],
            ["handoff"])

    def test_unknown_category_empty(self):
        self.assertEqual(run_mod.filter_categories(self.cats, "nope"), [])


class TestRender(unittest.TestCase):
    def test_render_contains_phrases_and_headings(self):
        cats = [{"name": "handoff", "summary": "Save state.",
                 "runs": "python x.py", "phrases": ["do the handoff", "hand off"]}]
        text = run_mod.render(cats)
        self.assertIn("## handoff", text)
        self.assertIn("do the handoff", text)
        self.assertIn("Runs: python x.py", text)

    def test_render_empty(self):
        self.assertIn("No triggers", run_mod.render([]))


class TestShippedRegistry(unittest.TestCase):
    """Data-integrity guard for the real registry that ships with the repo."""

    def test_registry_is_well_formed(self):
        cats = run_mod.load_registry(_REGISTRY)
        self.assertTrue(cats, "shipped triggers.yaml should load with categories")
        names = [c["name"] for c in cats]
        self.assertIn("handoff", names)
        self.assertIn("triggers", names)
        self.assertIn("doc-sync", names)
        for cat in cats:
            self.assertTrue(cat.get("summary"), f"{cat['name']} needs a summary")
            self.assertTrue(cat.get("runs"), f"{cat['name']} needs a runs value")
            self.assertTrue(cat.get("phrases"), f"{cat['name']} needs phrases")

    def _phrases(self):
        return {c["name"]: c.get("phrases", []) for c in run_mod.load_registry(_REGISTRY)}

    def test_no_phrase_is_registered_twice(self):
        seen = {}
        for name, phrases in self._phrases().items():
            for phrase in phrases:
                key = phrase.casefold()
                self.assertNotIn(key, seen, f"{phrase!r} is in {seen.get(key)} and {name}")
                seen[key] = name

    def test_review_orchestration_run_phrases(self):
        phrases = self._phrases()
        self.assertEqual(phrases["review-orchestration"],
                         ["write a run brief", "start the build run",
                          "approve the run's plan", "resume the build run"])
        for phrase in phrases["review-orchestration"]:
            self.assertNotIn("review", phrase, "a run phrase never says review")

    def test_cross_review_phrases(self):
        phrases = self._phrases()
        self.assertEqual(phrases["cross-review"],
                         ["do the review", "cross-review this",
                          "get the other AI to review this"])
        for phrase in phrases["cross-review"]:
            self.assertIn("review", phrase)
        self.assertEqual(phrases["weekly-review"], ["run the weekly review", "weekly review"])


if __name__ == "__main__":
    unittest.main()
