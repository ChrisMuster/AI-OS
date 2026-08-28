#!/usr/bin/env python3
"""Tests for the sync architecture's plan-consistency checks.

Same three controls as the selection suite: a positive control is a legal
instance the check must find, a rejection control is an illegal instance it must
refuse, and a negative control is something that is not an instance at all, so a
clean result is known not to be silence from over-matching.

**The subject here is a reference, in a plan document, to a script.** The defect
being guarded against is real and cost three review rounds: two plans and a memory
file carried an instruction to run `scope_measure.py`, a script that had only ever
existed in a session scratchpad, so a derivation nobody could re-execute read as a
measurement. The general form is that a named script which is absent because it was
never written is indistinguishable, from the text alone, from one a stage has yet to
build. The declaration block makes that difference explicit, and these tests prove
the check can tell them apart.

**The load-bearing control is `test_rejection_control_an_absent_declaration_excuses_nothing`.**
An empty or missing declaration block must mean "nothing is exempt". If it were read
as "everything is exempt", deleting the block would silently disable the check, which
is the failure mode where a guard reports loudest exactly when it has stopped working.
"""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = PROJECT_ROOT / "workflows" / "sync-architecture" / "scripts" / "run.py"


def load_module():
    spec = importlib.util.spec_from_file_location("sync_run_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load_module()


# ------------------------------------------------------------------ declaration

class ExtractPlannedTests(unittest.TestCase):
    """Counting checker. Subject: a path declared in the planned-artefacts block."""

    def test_positive_control_declared_paths_are_returned(self):
        text = ("intro\n\n```planned-artefacts\n"
                "workflows/a/scripts/one.py\n"
                "workflows/b/scripts/two.py\n```\n")
        self.assertEqual(runner.extract_planned(text),
                         {"workflows/a/scripts/one.py", "workflows/b/scripts/two.py"})

    def test_positive_control_trailing_comments_are_stripped(self):
        text = ("```planned-artefacts\n"
                "# a heading comment\n"
                "workflows/a/scripts/one.py   # step 3, the gate\n```\n")
        self.assertEqual(runner.extract_planned(text), {"workflows/a/scripts/one.py"})

    def test_negative_control_no_block_is_an_empty_set(self):
        """Empty, never None: an absent block means nothing is exempt."""
        self.assertEqual(runner.extract_planned("no block here\n"), set())

    def test_negative_control_unterminated_block_is_an_empty_set(self):
        self.assertEqual(
            runner.extract_planned("```planned-artefacts\nworkflows/a/b.py\n"), set())

    def test_negative_control_a_different_fence_is_not_the_block(self):
        self.assertEqual(
            runner.extract_planned("```allowlist\nworkflows/a/b.py\n```\n"), set())


# ------------------------------------------------------------ script references

class UnresolvedScriptsTests(unittest.TestCase):
    """Counting checker. Subject: a script a document names but which is absent."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="sync-arch-run-test-")
        self.root = Path(self._tmp.name)
        real = self.root / "workflows" / "present" / "scripts"
        real.mkdir(parents=True)
        with open(real / "here.py", "w", encoding="utf-8", newline="\n") as fh:
            fh.write("x\n")
        self.addCleanup(self._tmp.cleanup)

    def test_positive_control_a_missing_undeclared_script_is_reported(self):
        text = "Run `workflows/absent/scripts/gone.py` to measure the scope.\n"
        self.assertEqual(
            runner.unresolved_scripts(text, set(), root=self.root),
            ["workflows/absent/scripts/gone.py"])

    def test_negative_control_a_script_that_exists_is_not_reported(self):
        text = "Run `workflows/present/scripts/here.py` first.\n"
        self.assertEqual(runner.unresolved_scripts(text, set(), root=self.root), [])

    def test_negative_control_a_declared_future_script_is_not_reported(self):
        """A stage that has yet to build something is not the same defect."""
        text = "Build `workflows/absent/scripts/gone.py` in step 3.\n"
        self.assertEqual(
            runner.unresolved_scripts(text, {"workflows/absent/scripts/gone.py"},
                                      root=self.root),
            [])

    def test_negative_control_prose_without_a_script_path_is_not_an_instance(self):
        text = "The knowledge graph is rebuilt and the counts compared.\n"
        self.assertEqual(runner.unresolved_scripts(text, set(), root=self.root), [])

    def test_rejection_control_an_absent_declaration_excuses_nothing(self):
        """Deleting the declaration block must not disable the check. This pairs
        with the empty-set default in extract_planned: if an absent block were read
        as a blanket exemption, removing it would turn every missing script into a
        pass, which is the shape of a guard that fails silently."""
        text = "Run `workflows/absent/scripts/gone.py`.\n"
        planned = runner.extract_planned("this document has no declaration block\n")
        self.assertEqual(
            runner.unresolved_scripts(text, planned, root=self.root),
            ["workflows/absent/scripts/gone.py"])

    def test_positive_control_several_missing_scripts_are_all_reported(self):
        text = ("`workflows/a/scripts/one.py` and `workflows/b/scripts/two.py`, "
                "plus `workflows/present/scripts/here.py` which is fine.\n")
        self.assertEqual(
            runner.unresolved_scripts(text, set(), root=self.root),
            ["workflows/a/scripts/one.py", "workflows/b/scripts/two.py"])


# --------------------------------------------------------------- review baseline

class CompareToBaselineTests(unittest.TestCase):
    """Validator. Subject: a plan measured against the version last reviewed.

    The convention this encodes: the baseline holds the version the reviewing AI
    last reviewed, and it is refreshed as soon as that AI has *recorded its
    findings*, which is not the same moment as those findings being fixed. A
    plan differing from its baseline is therefore the normal state for most of an
    item's life, and the difference is the deliverable for the next review rather
    than a defect.

    **The load-bearing control is `test_positive_control_drift_is_information_not_failure`.**
    This check previously asserted byte-identity and failed on drift, with a message
    telling the reader to refresh before a review round, which is the one action that
    destroys the diff. That check was red for the whole duration of any correction
    pass, which is the condition that trains a reader to ignore a check.
    """

    def test_positive_control_drift_is_information_not_failure(self):
        level, added, removed = runner.compare_to_baseline("a\nb\n", "a\nb\nc\n")
        self.assertEqual(level, "INFO")
        self.assertEqual((added, removed), (1, 0))

    def test_positive_control_counts_are_a_real_diff_not_a_positional_compare(self):
        """One line inserted at the top shifts every following line. A lockstep
        comparison calls all of them changed; a diff calls one of them added. On the
        real pair of documents that read 1457 lines against a true 273."""
        level, added, removed = runner.compare_to_baseline("b\nc\nd\n", "a\nb\nc\nd\n")
        self.assertEqual((added, removed), (1, 0))
        self.assertEqual(level, "INFO")

    def test_negative_control_an_identical_baseline_reports_no_change(self):
        level, added, removed = runner.compare_to_baseline("a\nb\n", "a\nb\n")
        self.assertEqual((level, added, removed), ("INFO", 0, 0))

    def test_rejection_control_a_missing_baseline_is_a_failure(self):
        """The one genuine defect: no baseline means no diff surface at all, so the
        next review has to re-read the whole document blind."""
        level, _, _ = runner.compare_to_baseline(None, "a\nb\n")
        self.assertEqual(level, "FAIL")

    def test_negative_control_a_missing_live_plan_is_skipped_not_failed(self):
        """A gitignored plan is absent on a fresh clone. A check that cannot run
        must say so rather than pass or fail."""
        level, _, _ = runner.compare_to_baseline("a\nb\n", None)
        self.assertEqual(level, "SKIP")

    def test_positive_control_a_deletion_is_counted_on_the_removed_side(self):
        level, added, removed = runner.compare_to_baseline("a\nb\nc\n", "a\nc\n")
        self.assertEqual((level, added, removed), ("INFO", 0, 1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
