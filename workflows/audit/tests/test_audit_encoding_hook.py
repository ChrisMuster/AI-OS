#!/usr/bin/env python3
"""Unit tests for the audit's encoding-guard hook.

Covers the pure ``encoding_findings`` merge helper (severity filtering, label,
message passthrough) and the graceful-degradation behaviour of
``run_encoding_check`` without spawning the real checker.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run  # noqa: E402


class TestEncodingFindings(unittest.TestCase):
    def test_info_is_dropped(self):
        payload = {"findings": [
            {"severity": "INFO", "label": "encoding", "message": "x: open() no encoding"},
        ]}
        self.assertEqual(run.encoding_findings(payload), [])

    def test_warn_and_fail_are_mapped(self):
        payload = {"findings": [
            {"severity": "WARN", "label": "encoding", "message": "a.md: mojibake"},
            {"severity": "FAIL", "label": "encoding", "message": "b.md: not valid UTF-8"},
        ]}
        self.assertEqual(
            run.encoding_findings(payload),
            [
                ("WARN", "encoding", "a.md: mojibake"),
                ("FAIL", "encoding", "b.md: not valid UTF-8"),
            ],
        )

    def test_label_is_encoding(self):
        payload = {"findings": [
            {"severity": "FAIL", "label": "encoding", "message": "msg"},
        ]}
        self.assertEqual(run.encoding_findings(payload)[0][1], "encoding")

    def test_empty_and_missing_findings(self):
        self.assertEqual(run.encoding_findings({"findings": []}), [])
        self.assertEqual(run.encoding_findings({}), [])

    def test_output_findings_are_valid_audit_tuples(self):
        payload = {"findings": [
            {"severity": "WARN", "label": "encoding", "message": "m"},
        ]}
        for finding in run.encoding_findings(payload):
            self.assertEqual(len(finding), 3)
            # DEGRADED is deliberately absent: this merge helper filters the
            # payload to WARN/FAIL, so it cannot emit one. DEGRADED belongs to
            # the run_encoding_check wrapper (the check-did-not-run path),
            # which is asserted in TestRunEncodingCheckGracefulSkip below.
            # Widening this tuple would stop it pinning the helper's contract.
            self.assertIn(finding[0], ("FAIL", "WARN", "INFO"))


class TestRunEncodingCheckGracefulSkip(unittest.TestCase):
    def test_missing_cli_returns_degraded(self):
        bogus = Path("workflows/encoding-guard/scripts/NOPE_does_not_exist.py")
        with mock.patch.object(run, "ENCODING_RUN_PY", bogus):
            findings = run.run_encoding_check()
        self.assertEqual(len(findings), 1)
        level, label, message = findings[0]
        self.assertEqual(level, "DEGRADED")
        self.assertEqual(label, "encoding")
        self.assertIn("did not run", message)

    def test_unparseable_output_returns_degraded(self):
        fake = mock.Mock(stdout="not json", stderr="boom", returncode=1)
        with mock.patch.object(run.subprocess, "run", return_value=fake):
            findings = run.run_encoding_check()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][0], "DEGRADED")
        self.assertIn("did not run", findings[0][2])

    def test_subprocess_exception_returns_degraded(self):
        with mock.patch.object(run.subprocess, "run", side_effect=OSError("nope")):
            findings = run.run_encoding_check()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][0], "DEGRADED")
        self.assertIn("did not run", findings[0][2])

    def test_valid_payload_is_merged(self):
        fake = mock.Mock(
            stdout='{"findings": [{"severity": "FAIL", "label": "encoding", "message": "x.md: not valid UTF-8"}]}',
            stderr="",
            returncode=1,
        )
        with mock.patch.object(run.subprocess, "run", return_value=fake):
            findings = run.run_encoding_check()
        self.assertEqual(findings, [("FAIL", "encoding", "x.md: not valid UTF-8")])


if __name__ == "__main__":
    unittest.main()
