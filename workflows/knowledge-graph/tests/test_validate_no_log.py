#!/usr/bin/env python3
"""Tests for ``validate --no-log``: the read-only close-out verifier's audit runs
the validator with it, so a dry run of the review orchestrator writes nothing.

Both log paths are pointed at temporary files holding one line, so the real
LOG.md files are never touched. Each rule has a positive control: without
``--no-log`` the same run does write, which is what shows the files were the
ones being written to.
"""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run  # noqa: E402

SEED = b"[2026-01-01T00:00:00+00:00] | Actor: Biblio | Action: created | Note: seed\n"


class ValidateNoLogTests(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.workflow_log = Path(tmp.name) / "workflow-LOG.md"
        self.root_log = Path(tmp.name) / "root-LOG.md"
        for path in (self.workflow_log, self.root_log):
            path.write_bytes(SEED)
        for name, path in (("WORKFLOW_LOG", self.workflow_log),
                           ("ROOT_LOG", self.root_log)):
            patcher = mock.patch.object(run, name, path)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _validate(self, *flags):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return run.main(["validate", "--json", "--no-backrefs", *flags])

    def _unchanged(self):
        return (self.workflow_log.read_bytes() == SEED
                and self.root_log.read_bytes() == SEED)

    def test_positive_control_validate_writes_both_logs_by_default(self):
        self._validate()
        self.assertIn(b"Validating knowledge graph.", self.workflow_log.read_bytes())
        self.assertIn(b"knowledge-graph validate ran.", self.root_log.read_bytes())

    def test_rejection_control_no_log_leaves_both_logs_byte_identical(self):
        self._validate("--no-log")
        self.assertTrue(self._unchanged())

    def test_positive_control_a_failed_validation_logs_by_default(self):
        with mock.patch.object(run, "_load_graph", side_effect=RuntimeError("boom")):
            self.assertEqual(self._validate(), 1)
        self.assertIn(b"validation failed: boom", self.workflow_log.read_bytes())

    def test_rejection_control_a_failed_validation_with_no_log_writes_nothing(self):
        with mock.patch.object(run, "_load_graph", side_effect=RuntimeError("boom")):
            self.assertEqual(self._validate("--no-log"), 1)
        self.assertTrue(self._unchanged())

    def test_negative_control_no_log_changes_nothing_but_the_logging(self):
        """The validation itself, its JSON and its exit code, are the same with
        and without the switch."""
        outputs = []
        for flags in ((), ("--no-log",)):
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(io.StringIO()):
                code = run.main(["validate", "--json", "--no-backrefs", *flags])
            outputs.append((code, buf.getvalue()))
        self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()
