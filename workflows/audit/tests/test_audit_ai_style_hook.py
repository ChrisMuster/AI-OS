#!/usr/bin/env python3
"""Unit tests for the audit's ai-style-guard hook.

Covers the pure ``ai_style_findings`` merge helper (WARN-only filtering, label,
message passthrough) and the graceful-degradation behaviour of
``run_ai_style_check`` without spawning the real guard.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run  # noqa: E402

# The tier-2 denylist words used in these fixtures are assembled from fragments so
# this tracked test file does not itself trip the ai-style guard's word detector
# (the same trick the personal-data-guard tests use for their own markers).
DENY_WORD_A = "de" + "lve"
DENY_WORD_B = "re" + "alm"


class TestAiStyleFindings(unittest.TestCase):
    def test_info_is_dropped(self):
        payload = {"findings": [
            {"severity": "INFO", "label": "ai-style",
             "message": f"a.md:2: denylisted word `{DENY_WORD_A}`"},
        ]}
        self.assertEqual(run.ai_style_findings(payload), [])

    def test_warn_is_mapped(self):
        payload = {"findings": [
            {"severity": "WARN", "label": "ai-style", "message": "a.md:6: em dash present"},
            {"severity": "INFO", "label": "ai-style",
             "message": f"a.md:7: denylisted word `{DENY_WORD_B}`"},
        ]}
        self.assertEqual(
            run.ai_style_findings(payload),
            [("WARN", "ai-style", "a.md:6: em dash present")],
        )

    def test_label_is_ai_style(self):
        payload = {"findings": [
            {"severity": "WARN", "label": "ai-style", "message": "msg"},
        ]}
        self.assertEqual(run.ai_style_findings(payload)[0][1], "ai-style")

    def test_empty_and_missing_findings(self):
        self.assertEqual(run.ai_style_findings({"findings": []}), [])
        self.assertEqual(run.ai_style_findings({}), [])

    def test_output_findings_are_valid_audit_tuples(self):
        payload = {"findings": [
            {"severity": "WARN", "label": "ai-style", "message": "m"},
        ]}
        for finding in run.ai_style_findings(payload):
            self.assertEqual(len(finding), 3)
            # DEGRADED is deliberately absent: this merge helper filters the
            # payload to WARN only, so it cannot emit one. DEGRADED belongs to
            # the run_ai_style_check wrapper (the check-did-not-run path),
            # which is asserted in TestRunAiStyleCheckGracefulSkip below.
            # Widening this tuple would stop it pinning the helper's contract.
            self.assertIn(finding[0], ("FAIL", "WARN", "INFO"))


class TestRunAiStyleCheckGracefulSkip(unittest.TestCase):
    def test_missing_cli_returns_degraded(self):
        bogus = Path("workflows/ai-style-guard/scripts/NOPE_does_not_exist.py")
        with mock.patch.object(run, "AI_STYLE_RUN_PY", bogus):
            findings = run.run_ai_style_check()
        self.assertEqual(len(findings), 1)
        level, label, message = findings[0]
        self.assertEqual(level, "DEGRADED")
        self.assertEqual(label, "ai-style")
        self.assertIn("did not run", message)

    def test_unparseable_output_returns_degraded(self):
        fake = mock.Mock(stdout="not json", stderr="boom", returncode=1)
        with mock.patch.object(run.subprocess, "run", return_value=fake):
            findings = run.run_ai_style_check()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][0], "DEGRADED")
        self.assertIn("did not run", findings[0][2])

    def test_subprocess_exception_returns_degraded(self):
        with mock.patch.object(run.subprocess, "run", side_effect=OSError("nope")):
            findings = run.run_ai_style_check()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][0], "DEGRADED")
        self.assertIn("did not run", findings[0][2])

    def test_valid_payload_is_merged(self):
        fake = mock.Mock(
            stdout='{"findings": [{"severity": "WARN", "label": "ai-style", "message": "x.md:3: em dash present"}]}',
            stderr="",
            returncode=0,
        )
        with mock.patch.object(run.subprocess, "run", return_value=fake):
            findings = run.run_ai_style_check()
        self.assertEqual(findings, [("WARN", "ai-style", "x.md:3: em dash present")])


if __name__ == "__main__":
    unittest.main()
