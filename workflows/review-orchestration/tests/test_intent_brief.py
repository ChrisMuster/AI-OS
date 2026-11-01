#!/usr/bin/env python3
"""Tests for the brief readings the intent checks use (plan 10.6, A4):
``brief.section_lines``, ``intent_lines``, ``out_of_scope_items``, ``brief_edit_paths``
and ``brief_acceptance``, and that ``read_brief`` gives the same edit paths as the
helper, so the approval scope rebuild and the intent checks cannot read a brief
differently. Text only, plus one temporary brief file for ``read_brief``.

    python workflows/review-orchestration/tests/test_intent_brief.py
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
# The checker imports approver for the verification lists, beside it in scripts/.
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location("brief", SCRIPTS / "brief.py")
brief = importlib.util.module_from_spec(spec)
sys.modules["brief"] = brief
spec.loader.exec_module(brief)

TEXT = """\
<!-- a comment that is not a line -->
## Goal
- The first goal.
- A second goal that
  wraps onto a second line.
<!-- a comment inside the section -->
- TODO

## Constraints
- One constraint.

## Out of scope
- `config/` and `scripts/start.py`, plus behaviour in prose.
- A line with no backticks at all.
- An empty span `` gives nothing.

## Acceptance checks
- python workflows/demo/tests/test_demo.py
- python workflows/demo/tests/test_demo.py

## Edit paths
- work/ | Measure: git ls-files work
- notes/n.md | Measure: git ls-files notes

## Open questions
None

## Source
- `work/`
"""


class IntentBriefTests(unittest.TestCase):

    def test_positive_one_entry_per_content_line_in_order_tagged_by_section(self):
        lines = brief.intent_lines(TEXT)
        self.assertEqual([(l["section"], l["text"]) for l in lines],
                         [("Goal", "The first goal."),
                          ("Goal", "A second goal that"),
                          ("Goal", "wraps onto a second line."),
                          ("Constraints", "One constraint.")])
        self.assertEqual([l["line"] for l in lines], [3, 4, 5, 10])

    def test_negative_comments_and_placeholders_are_dropped(self):
        texts = [l["text"] for l in brief.intent_lines(TEXT)]
        self.assertNotIn("TODO", texts)
        self.assertFalse(any("comment" in t for t in texts))

    def test_positive_every_backtick_span_under_out_of_scope_is_found(self):
        items = brief.out_of_scope_items(TEXT)
        self.assertEqual([(i["line"], i["path"]) for i in items],
                         [(13, "config/"), (13, "scripts/start.py")])

    def test_negative_none_and_prose_give_no_items(self):
        none = TEXT.replace("- `config/` and `scripts/start.py`, plus behaviour in prose.\n"
                            "- A line with no backticks at all.\n"
                            "- An empty span `` gives nothing.", "- None.")
        self.assertEqual(brief.out_of_scope_items(none), [])
        prose = none.replace("- None.", "- Anything to do with the build harness.")
        self.assertEqual(brief.out_of_scope_items(prose), [])

    def test_positive_edit_paths_drop_the_measure_part(self):
        self.assertEqual(brief.brief_edit_paths(TEXT), ["work/", "notes/n.md"])

    def test_positive_acceptance_keeps_a_command_written_twice(self):
        self.assertEqual(brief.brief_acceptance(TEXT),
                         ["python workflows/demo/tests/test_demo.py"] * 2)

    def test_positive_read_brief_gives_the_helpers_reading(self):
        # A brief that passes the check, read both ways, in a temporary project root.
        text = TEXT.replace("- python workflows/demo/tests/test_demo.py\n- python",
                            "- python")
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        (root / "brief.md").write_text(text, encoding="utf-8", newline="\n")
        (root / "work").mkdir()
        read = brief.read_brief("brief.md", root)
        self.assertEqual(read["edit_paths"], brief.brief_edit_paths(text))
        self.assertEqual(read["acceptance"], brief.brief_acceptance(text))
        self.assertEqual(read["sections"]["Goal"],
                         [c for _n, c in brief.section_lines(text)["Goal"]])


if __name__ == "__main__":
    unittest.main()
