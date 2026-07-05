#!/usr/bin/env python3
"""Unit tests for the audit's DEGRADED status.

A check that could not run is reported as DEGRADED so it can never be mistaken
for a pass: it is counted, shown in its own report section, and carries a
remediation. Covers the ``degraded`` helper and ``format_report`` rendering.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run  # noqa: E402


class DegradedHelperTests(unittest.TestCase):
    def test_repairable_degrade_is_degraded_and_names_setup(self):
        level, label, message = run.degraded("encoding", "boom")
        self.assertEqual(level, run.DEGRADED)
        self.assertEqual(label, "encoding")
        self.assertIn("did not run", message)
        self.assertIn("setup.py", message)

    def test_unrepairable_degrade_omits_setup_and_flags_missing(self):
        _level, _label, message = run.degraded(
            "encoding", "encoding-guard CLI not found", repairable=False)
        self.assertNotIn("setup.py", message)
        self.assertIn("missing", message)


class DegradedReportTests(unittest.TestCase):
    def test_degraded_finding_gets_its_own_section_and_count(self):
        findings = [run.degraded("ai-style", "could not run (boom)")]
        report = run.format_report(findings, dir_count=3)
        self.assertIn("**Degraded (did not run):** 1", report)
        self.assertIn("## Degraded (did not run)", report)
        # A degrade is not a clean pass.
        self.assertNotIn("All structural checks passed", report)

    def test_clean_run_reports_zero_degraded_and_passes(self):
        report = run.format_report([], dir_count=3)
        self.assertIn("**Degraded (did not run):** 0", report)
        self.assertIn("All structural checks passed", report)

    def test_degraded_does_not_add_a_failure(self):
        findings = [run.degraded("encoding", "could not run (boom)")]
        report = run.format_report(findings, dir_count=1)
        self.assertIn("**Failures:** 0", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
