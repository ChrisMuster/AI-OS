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


class RetiredInfoVocabularyTests(unittest.TestCase):
    """No test in this suite may still announce the retired INFO degrade path.

    Advisory hooks stopped degrading to "a single INFO note" on 2026-07-04 and
    began returning first-class DEGRADED findings. Twelve test methods kept the
    old name for a month while their bodies asserted DEGRADED, so the suite
    output lied to whoever read it. Two prose sweeps missed them because the
    stale word lived in an identifier rather than in a sentence. This makes the
    retirement mechanical instead of trusting the next sweep to be thorough.
    """

    RETIRED_WORDS = ("single_info", "singleinfo")

    def test_no_definition_name_uses_the_retired_info_vocabulary(self):
        offenders = []
        tests_dir = Path(__file__).resolve().parent
        for path in sorted(tests_dir.glob("test_*.py")):
            lines = path.read_text(encoding="utf-8").splitlines()
            for lineno, line in enumerate(lines, 1):
                stripped = line.strip()
                if not (stripped.startswith("def ")
                        or stripped.startswith("class ")):
                    continue
                lowered = stripped.lower()
                if any(word in lowered for word in self.RETIRED_WORDS):
                    offenders.append(f"{path.name}:{lineno}: {stripped}")
        self.assertEqual(offenders, [])

    def test_the_check_would_catch_a_reintroduction(self):
        # The positive control. Without it this passes just as happily if the
        # scan is broken, which is how a checker validates its own definition.
        sample = "    def test_missing_cli_returns_single_info(self):"
        lowered = sample.strip().lower()
        self.assertTrue(
            any(word in lowered for word in self.RETIRED_WORDS),
            "the retired-vocabulary scan no longer matches the real defect",
        )


class DegradedHelperTests(unittest.TestCase):
    def test_repairable_degrade_is_degraded_and_names_setup(self):
        level, label, message = run.degraded("encoding", "boom")
        self.assertEqual(level, run.DEGRADED)
        self.assertEqual(label, "encoding")
        self.assertIn("did not run", message)
        self.assertIn("setup.py", message)

    def test_default_what_is_the_whole_check(self):
        _level, _label, message = run.degraded("encoding", "boom")
        self.assertIn("encoding check did not run", message)

    def test_what_names_a_component_instead_of_the_whole_check(self):
        # A guard whose main check completed while one component could not must
        # not report that the check did not run - it did.
        _level, _label, message = run.degraded(
            "doc-sync", "boom", what="inventory probe")
        self.assertIn("doc-sync inventory probe did not run", message)
        self.assertNotIn("doc-sync check did not run", message)
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
