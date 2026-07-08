#!/usr/bin/env python3
"""Unit tests for the audit's doc-sync-guard hook.

Covers the pure ``doc_sync_findings`` merge helper (WARN mapping, label,
message passthrough) and the graceful-degradation behaviour of
``run_doc_sync_check`` without spawning the real guard.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run  # noqa: E402


class TestDocSyncFindings(unittest.TestCase):
    def test_scan_skipped_warn_is_mapped(self):
        payload = {"findings": [
            {"severity": "WARN", "label": "doc-sync",
             "message": "scan skipped - could not run git"},
        ]}
        self.assertEqual(
            run.doc_sync_findings(payload),
            [("WARN", "doc-sync", "scan skipped - could not run git")],
        )

    def test_warn_is_mapped(self):
        payload = {"findings": [
            {"severity": "WARN", "label": "doc-sync",
             "message": "workflows/foo: CONTEXT.md not updated for changes in "
                        "this directory"},
            {"severity": "INFO", "label": "doc-sync", "message": "scan skipped"},
        ]}
        self.assertEqual(
            run.doc_sync_findings(payload),
            [("WARN", "doc-sync",
              "workflows/foo: CONTEXT.md not updated for changes in this "
              "directory")],
        )

    def test_label_is_doc_sync(self):
        payload = {"findings": [
            {"severity": "WARN", "label": "doc-sync", "message": "msg"},
        ]}
        self.assertEqual(run.doc_sync_findings(payload)[0][1], "doc-sync")

    def test_empty_and_missing_findings(self):
        self.assertEqual(run.doc_sync_findings({"findings": []}), [])
        self.assertEqual(run.doc_sync_findings({}), [])

    def test_output_findings_are_valid_audit_tuples(self):
        payload = {"findings": [
            {"severity": "WARN", "label": "doc-sync", "message": "m"},
        ]}
        for finding in run.doc_sync_findings(payload):
            self.assertEqual(len(finding), 3)
            self.assertIn(finding[0], ("FAIL", "WARN", "INFO", "DEGRADED"))


class TestRunDocSyncCheckGracefulSkip(unittest.TestCase):
    def test_missing_cli_returns_degraded(self):
        bogus = Path("workflows/doc-sync-guard/scripts/NOPE_does_not_exist.py")
        with mock.patch.object(run, "DOC_SYNC_RUN_PY", bogus):
            findings = run.run_doc_sync_check()
        self.assertEqual(len(findings), 1)
        level, label, message = findings[0]
        self.assertEqual(level, "DEGRADED")
        self.assertEqual(label, "doc-sync")
        self.assertIn("did not run", message)

    def test_unparseable_output_returns_degraded(self):
        fake = mock.Mock(stdout="not json", stderr="boom", returncode=1)
        with mock.patch.object(run.subprocess, "run", return_value=fake):
            findings = run.run_doc_sync_check()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][0], "DEGRADED")
        self.assertIn("did not run", findings[0][2])

    def test_subprocess_exception_returns_degraded(self):
        with mock.patch.object(run.subprocess, "run", side_effect=OSError("nope")):
            findings = run.run_doc_sync_check()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][0], "DEGRADED")
        self.assertIn("did not run", findings[0][2])

    def test_valid_payload_is_merged(self):
        fake = mock.Mock(
            stdout='{"findings": [{"severity": "WARN", "label": "doc-sync", '
                   '"message": "workflows/foo: no LOG.md for a directory whose '
                   'content changed"}]}',
            stderr="",
            returncode=0,
        )
        with mock.patch.object(run.subprocess, "run", return_value=fake):
            findings = run.run_doc_sync_check()
        self.assertEqual(
            findings,
            [("WARN", "doc-sync",
              "workflows/foo: no LOG.md for a directory whose content changed")],
        )


if __name__ == "__main__":
    unittest.main()
