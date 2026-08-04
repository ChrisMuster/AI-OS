#!/usr/bin/env python3
"""Unit tests for the Revision History ordering check.

The CONTEXT.md schema says newest at the bottom. Until now nothing enforced it:
an out-of-order entry passed the full audit, the doc-sync guard, and two rounds
of review before a reader noticed, and a sweep then found two more files with
the same defect. These tests pin the rule so it stays mechanical.

The checks below use whole CONTEXT.md fixtures rather than a bare list of dates,
because ``check_context_metadata`` reads the section out of a document and the
section boundary is part of what can go wrong.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run  # noqa: E402


def context(last_modified, entries, trailing_section=""):
    """Build a minimal CONTEXT.md carrying the given Revision History entries."""
    body = "\n".join(f"- {date} - Entry text." for date in entries)
    return (
        "# Fixture\n\n"
        f"**Last modified:** {last_modified}\n\n"
        "## Revision History\n"
        f"{body}\n"
        f"{trailing_section}"
    )


def order_warnings(content):
    return [w for w in run.check_context_metadata(content)
            if "out of order" in w]


def governed_context_files():
    """Return the CONTEXT.md files the audit actually governs.

    Scope must match the audit's own walker, ``run.collect_dirs()``, which
    prunes hidden, gitignored, skip and no-recurse directories. A raw
    ``rglob("CONTEXT.md")`` is a strictly wider set: it also picks up hidden
    tool directories such as ``.codex/`` and gitignored personal content such as
    wikis and saved conversations. Policing those from this suite would let
    local files the audit never checks fail the audit's tests.

    No file counts are quoted here on purpose. The two sets move every time a
    directory is added, so a number written into this docstring is wrong by the
    next commit and is read as current truth in the meantime. The relationship
    itself is asserted in ``test_rglob_is_a_strictly_wider_set`` below, where it
    is re-derived on every run instead.
    """
    return [d / "CONTEXT.md" for d in run.collect_dirs()
            if (d / "CONTEXT.md").is_file()]


class RevisionHistoryOrderTests(unittest.TestCase):
    def test_ascending_entries_pass(self):
        # The negative control: a correct file must stay silent, or the check
        # would flag the whole project rather than the defect.
        content = context("2026-03-01", ["2026-01-01", "2026-02-01",
                                         "2026-03-01"])
        self.assertEqual(order_warnings(content), [])

    def test_newest_at_the_top_is_flagged(self):
        # The positive control, drawn from the real defect: the newer entry was
        # written above the older one.
        content = context("2026-02-01", ["2026-02-01", "2026-01-01"])
        warnings = order_warnings(content)
        self.assertEqual(len(warnings), 1)
        self.assertIn("2026-01-01 follows 2026-02-01", warnings[0])
        self.assertIn("newest entry goes at the bottom", warnings[0])

    def test_same_day_entries_are_not_out_of_order(self):
        # Two entries on one day are explicitly allowed by the schema, and the
        # doc-sync guard already treats a same-day second entry as a real gain.
        content = context("2026-02-01", ["2026-01-01", "2026-02-01",
                                         "2026-02-01"])
        self.assertEqual(order_warnings(content), [])

    def test_several_inversions_are_reported_once_with_a_count(self):
        # One warning per file, not one per pair: a file whose history was
        # written in reverse would otherwise bury the rest of the report.
        content = context("2026-04-01", ["2026-04-01", "2026-03-01",
                                         "2026-02-01", "2026-01-01"])
        warnings = order_warnings(content)
        self.assertEqual(len(warnings), 1)
        self.assertIn("and 2 more", warnings[0])

    def test_archive_reference_line_does_not_trip_the_check(self):
        # The archive line carries a date and sits above entries that are newer
        # than it. It is not an entry, so it must not be compared as one.
        content = (
            "# Fixture\n\n"
            "**Last modified:** 2026-03-01\n\n"
            "## Revision History\n"
            "Earlier history archived to LOG.md on 2026-02-15.\n"
            "- 2026-02-20 - Entry text.\n"
            "- 2026-03-01 - Entry text.\n"
        )
        self.assertEqual(order_warnings(content), [])

    def test_entries_after_the_section_are_not_compared(self):
        # Only the Revision History section is ordered. A dated bullet in a
        # later section (or another file's history pasted below) must not be
        # pulled into the comparison.
        content = context("2026-02-01", ["2026-01-01", "2026-02-01"],
                          trailing_section="\n## Notes\n- 2025-01-01 - Older.\n")
        self.assertEqual(order_warnings(content), [])

    def test_a_missing_section_is_not_an_ordering_failure(self):
        content = "# Fixture\n\n**Last modified:** 2026-02-01\n\n## Purpose\nx\n"
        self.assertEqual(order_warnings(content), [])


class RevisionHistoryOrderAgainstTheProjectTests(unittest.TestCase):
    """The rule holds across the real repository, not only over fixtures.

    A check that passes its fixtures while the project is full of violations is
    a check nobody can turn on. This asserts the project is currently clean, so
    the first new violation fails here rather than accumulating.
    """

    def test_every_governed_context_file_is_in_order(self):
        offenders = []
        for path in sorted(governed_context_files()):
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if order_warnings(content):
                offenders.append(
                    path.relative_to(run.PROJECT_ROOT).as_posix())
        self.assertEqual(offenders, [])

    def test_scope_never_reaches_outside_the_audit_walker(self):
        # The invariant behind the assertion above, pinned so a future
        # "simplification" back to a raw rglob() fails here instead of silently
        # widening the suite onto hidden and gitignored files. Every file
        # checked must sit in a directory the audit itself walks.
        governed_dirs = set(run.collect_dirs())
        for path in governed_context_files():
            self.assertIn(path.parent, governed_dirs)

    def test_rglob_is_a_strictly_wider_set(self):
        # The claim in governed_context_files()'s docstring, derived rather than
        # quoted as a count. A count would rot on the next directory added; this
        # re-measures on every run, so the docstring can never drift from it.
        governed = set(governed_context_files())
        everything = set(run.PROJECT_ROOT.rglob("CONTEXT.md"))
        self.assertTrue(governed < everything,
                        "governed CONTEXT.md files must be a proper subset of "
                        "a raw rglob; if this fails the walker has widened onto "
                        "hidden or gitignored files")


if __name__ == "__main__":
    unittest.main()
