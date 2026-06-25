#!/usr/bin/env python3
"""Unit tests for the audit's personal-data-guard hook.

Covers the pure ``personal_findings`` merge helper (severity filtering, label,
message passthrough) and the graceful-degradation behaviour of
``run_personal_data_check`` without spawning the real guard.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run  # noqa: E402


class TestPersonalFindings(unittest.TestCase):
    def test_info_is_dropped(self):
        payload = {"findings": [
            {"severity": "INFO", "label": "personal-data", "message": "USER.md not found"},
        ]}
        self.assertEqual(run.personal_findings(payload), [])

    def test_warn_and_fail_are_mapped(self):
        payload = {"findings": [
            {"severity": "WARN", "label": "personal-data", "message": "a.md: denylisted term"},
            {"severity": "FAIL", "label": "personal-data", "message": "b.md: email address"},
        ]}
        self.assertEqual(
            run.personal_findings(payload),
            [
                ("WARN", "personal-data", "a.md: denylisted term"),
                ("FAIL", "personal-data", "b.md: email address"),
            ],
        )

    def test_label_is_personal_data(self):
        payload = {"findings": [
            {"severity": "FAIL", "label": "personal-data", "message": "msg"},
        ]}
        self.assertEqual(run.personal_findings(payload)[0][1], "personal-data")

    def test_empty_and_missing_findings(self):
        self.assertEqual(run.personal_findings({"findings": []}), [])
        self.assertEqual(run.personal_findings({}), [])

    def test_output_findings_are_valid_audit_tuples(self):
        payload = {"findings": [
            {"severity": "WARN", "label": "personal-data", "message": "m"},
        ]}
        for finding in run.personal_findings(payload):
            self.assertEqual(len(finding), 3)
            self.assertIn(finding[0], ("FAIL", "WARN", "INFO"))


class TestRunPersonalDataCheckGracefulSkip(unittest.TestCase):
    def test_missing_cli_returns_single_info(self):
        bogus = Path("workflows/personal-data-guard/scripts/NOPE_does_not_exist.py")
        with mock.patch.object(run, "PERSONAL_RUN_PY", bogus):
            findings = run.run_personal_data_check()
        self.assertEqual(len(findings), 1)
        level, label, message = findings[0]
        self.assertEqual(level, "INFO")
        self.assertEqual(label, "personal-data")
        self.assertIn("skipped", message)

    def test_unparseable_output_returns_single_info(self):
        fake = mock.Mock(stdout="not json", stderr="boom", returncode=1)
        with mock.patch.object(run.subprocess, "run", return_value=fake):
            findings = run.run_personal_data_check()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][0], "INFO")
        self.assertIn("skipped", findings[0][2])

    def test_subprocess_exception_returns_single_info(self):
        with mock.patch.object(run.subprocess, "run", side_effect=OSError("nope")):
            findings = run.run_personal_data_check()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][0], "INFO")
        self.assertIn("skipped", findings[0][2])

    def test_valid_payload_is_merged(self):
        fake = mock.Mock(
            stdout='{"findings": [{"severity": "FAIL", "label": "personal-data", "message": "x.md: email address"}]}',
            stderr="",
            returncode=1,
        )
        with mock.patch.object(run.subprocess, "run", return_value=fake):
            findings = run.run_personal_data_check()
        self.assertEqual(findings, [("FAIL", "personal-data", "x.md: email address")])


if __name__ == "__main__":
    unittest.main()
