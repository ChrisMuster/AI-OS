#!/usr/bin/env python3
"""Unit tests for the tolerant CONTEXT.md parser."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import parser as ctxparser  # noqa: E402

WELL_FORMED = """# Example Workflow

**Last modified:** 2026-06-19

## Purpose
Does an example thing in one sentence. Second sentence here.

## Contents
- scripts/ — `workflows/example/scripts/` [[workflows/example/scripts/CONTEXT]] — The scripts.

## Dependencies
- `AGENTS.md` [[AGENTS]] (root) — The rules.
- Some prose mentioning `.venv` and a flag `--exclude foo` that are not paths.

## Steps
1. See `workflows/example/scripts/run.py` for details.

## Revision History
- 2026-06-19 — Initial creation referencing `workflows/old/path/CONTEXT` historically.
"""


class TestParser(unittest.TestCase):
    def test_well_formed(self):
        pc = ctxparser.parse_context(WELL_FORMED)
        self.assertEqual(pc.title, "Example Workflow")
        self.assertEqual(pc.last_modified, "2026-06-19")
        self.assertTrue(pc.purpose.startswith("Does an example thing"))
        self.assertIn("Purpose", pc.sections_present)
        self.assertIn("Dependencies", pc.sections_present)

    def test_contents_backticks(self):
        pc = ctxparser.parse_context(WELL_FORMED)
        self.assertIn("workflows/example/scripts/", pc.section_backticks["Contents"])

    def test_dependencies_backticks_include_noise(self):
        # The parser is pure and keeps all backtick tokens; the builder filters.
        pc = ctxparser.parse_context(WELL_FORMED)
        deps = pc.section_backticks["Dependencies"]
        self.assertIn("AGENTS.md", deps)
        self.assertIn(".venv", deps)

    def test_links_extracted_and_aliased(self):
        pc = ctxparser.parse_context("See [[workflows/x/CONTEXT|the X workflow]] and [[AGENTS]].")
        self.assertIn("workflows/x/CONTEXT", pc.links)
        self.assertIn("AGENTS", pc.links)

    def test_fenced_code_ignored(self):
        content = (
            "# T\n\n## Purpose\nReal.\n\n## Contents\n"
            "```\n- fake — `workflows/fake/` [[workflows/fake/CONTEXT]]\n```\n"
            "- real — `workflows/real/` [[workflows/real/CONTEXT]]\n"
        )
        pc = ctxparser.parse_context(content)
        contents = pc.section_backticks.get("Contents", [])
        self.assertIn("workflows/real/", contents)
        self.assertNotIn("workflows/fake/", contents)
        self.assertIn("workflows/real/CONTEXT", pc.links)
        self.assertNotIn("workflows/fake/CONTEXT", pc.links)

    def test_malformed_does_not_raise(self):
        for content in ("", "no markdown here", "##\n#\n```unclosed", "\x00\x01weird"):
            pc = ctxparser.parse_context(content)
            self.assertIsInstance(pc.sections_present, list)

    def test_missing_sections_empty(self):
        pc = ctxparser.parse_context("# Only a title")
        self.assertEqual(pc.sections_present, [])
        self.assertEqual(pc.purpose, "")
        self.assertEqual(pc.links, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
