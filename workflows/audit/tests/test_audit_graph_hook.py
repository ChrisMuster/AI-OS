#!/usr/bin/env python3
"""Unit tests for the audit's knowledge-graph validation hook.

Covers the pure ``graph_findings`` merge helper (severity filtering, field
remap, label, message format) and the graceful-degradation behaviour of
``run_graph_validation`` without spawning the real validator: the missing-CLI
path and the unparseable-output path are exercised by pointing the hook at a
bogus path and by patching ``subprocess.run`` respectively.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run  # noqa: E402


class TestGraphFindings(unittest.TestCase):
    """The pure dict-in / tuples-out merge helper."""

    def test_info_is_dropped(self):
        payload = {"findings": [
            {"severity": "INFO", "subject": "a", "message": "forward ref"},
        ]}
        self.assertEqual(run.graph_findings(payload), [])

    def test_warn_and_fail_are_mapped(self):
        payload = {"findings": [
            {"severity": "WARN", "subject": "foo/bar", "message": "broken reference"},
            {"severity": "FAIL", "subject": "baz", "message": "hard fail"},
        ]}
        self.assertEqual(
            run.graph_findings(payload),
            [
                ("WARN", "knowledge-graph", "`foo/bar` — broken reference"),
                ("FAIL", "knowledge-graph", "`baz` — hard fail"),
            ],
        )

    def test_mixed_severities_keep_only_actionable(self):
        payload = {"findings": [
            {"severity": "INFO", "subject": "a", "message": "x"},
            {"severity": "WARN", "subject": "b", "message": "y"},
            {"severity": "INFO", "subject": "c", "message": "z"},
            {"severity": "FAIL", "subject": "d", "message": "w"},
        ]}
        result = run.graph_findings(payload)
        self.assertEqual([f[0] for f in result], ["WARN", "FAIL"])
        self.assertEqual([f[1] for f in result], ["knowledge-graph", "knowledge-graph"])

    def test_label_is_knowledge_graph_and_subject_in_message(self):
        payload = {"findings": [
            {"severity": "WARN", "subject": "workflows/x", "message": "msg"},
        ]}
        level, label, message = run.graph_findings(payload)[0]
        self.assertEqual(label, "knowledge-graph")
        self.assertIn("`workflows/x`", message)
        self.assertIn("msg", message)

    def test_empty_findings_is_empty_list(self):
        self.assertEqual(run.graph_findings({"findings": []}), [])

    def test_missing_findings_key_is_empty_list(self):
        self.assertEqual(run.graph_findings({}), [])

    def test_unknown_severity_is_dropped(self):
        payload = {"findings": [
            {"severity": "DEBUG", "subject": "a", "message": "x"},
        ]}
        self.assertEqual(run.graph_findings(payload), [])

    def test_output_findings_are_valid_audit_tuples(self):
        payload = {"findings": [
            {"severity": "WARN", "subject": "a", "message": "x"},
        ]}
        for finding in run.graph_findings(payload):
            self.assertEqual(len(finding), 3)
            self.assertIn(finding[0], ("FAIL", "WARN", "INFO"))


class TestRunGraphValidationGracefulSkip(unittest.TestCase):
    """The validator invocation must never raise — worst case is one INFO note."""

    def test_missing_cli_returns_single_info(self):
        bogus = Path("workflows/knowledge-graph/scripts/NOPE_does_not_exist.py")
        with mock.patch.object(run, "KG_RUN_PY", bogus):
            findings = run.run_graph_validation()
        self.assertEqual(len(findings), 1)
        level, label, message = findings[0]
        self.assertEqual(level, "INFO")
        self.assertEqual(label, "knowledge-graph")
        self.assertIn("skipped", message)

    # The real KG_RUN_PY exists in-repo, so .exists() is True for the tests
    # below; only the subprocess boundary needs patching. (Path instance
    # attributes are read-only, so the missing-CLI case patches the module
    # attribute instead, above.)

    def test_unparseable_output_returns_single_info(self):
        fake = mock.Mock(stdout="not json", stderr="boom", returncode=1)
        with mock.patch.object(run.subprocess, "run", return_value=fake):
            findings = run.run_graph_validation()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][0], "INFO")
        self.assertIn("skipped", findings[0][2])

    def test_subprocess_exception_returns_single_info(self):
        with mock.patch.object(run.subprocess, "run", side_effect=OSError("nope")):
            findings = run.run_graph_validation()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][0], "INFO")
        self.assertIn("skipped", findings[0][2])

    def test_valid_payload_is_merged(self):
        fake = mock.Mock(
            stdout='{"findings": [{"severity": "WARN", "subject": "n", "message": "m"}]}',
            stderr="",
            returncode=0,
        )
        with mock.patch.object(run.subprocess, "run", return_value=fake):
            findings = run.run_graph_validation()
        self.assertEqual(findings, [("WARN", "knowledge-graph", "`n` — m")])


if __name__ == "__main__":
    unittest.main()
