#!/usr/bin/env python3
"""Tests for the audit's read-only mode (``--read-only`` and
``run_audit(read_only=True)``), which the read-only close-out verifier relies on.

Nothing here touches a real LOG.md: the log paths and the report folder are
pointed at a temporary folder, and the slow checks are replaced with stubs so
each test asks one question. Every rule has a positive control showing the
default mode still writes, which is what shows the temporary files were the
ones being written.
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


class _Proc:
    returncode = 0
    stdout = '{"findings": []}'
    stderr = ""


class GraphValidationNoLogTests(unittest.TestCase):
    """run_graph_validation hands --no-log to the validator only when asked."""

    def _argv(self, **kw):
        with mock.patch.object(run.subprocess, "run", return_value=_Proc()) as spy:
            run.run_graph_validation(**kw)
        return spy.call_args.args[0]

    def test_positive_control_no_log_is_passed_when_asked(self):
        self.assertIn("--no-log", self._argv(no_log=True))

    def test_rejection_control_the_default_does_not_pass_it(self):
        self.assertNotIn("--no-log", self._argv())

    def test_negative_control_the_rest_of_the_command_is_unchanged(self):
        self.assertEqual([a for a in self._argv(no_log=True) if a != "--no-log"],
                         self._argv())


ADVISORY_CHECKS = ("run_encoding_check", "run_personal_data_check",
                   "run_ai_style_check", "run_doc_sync_check",
                   "run_skill_hardening_check")


class RunAuditReadOnlyTests(unittest.TestCase):
    """run_audit(read_only=True) reaches the graph validation as no_log=True,
    and is still the full audit: every advisory check runs once, as in the
    default mode (code review R5-1; the review orchestrator's start check
    relies on the read-only audit being the whole audit)."""

    def _run(self, **kw):
        """Run run_audit with every check stubbed; return the graph call's
        keyword arguments and how often each advisory check ran."""
        stubs = {name: mock.patch.object(run, name, return_value=[])
                 for name in ("collect_dirs", "check_python_scripts", *ADVISORY_CHECKS)}
        with mock.patch.object(run, "run_graph_validation", return_value=[]) as graph:
            mocks = {name: patcher.start() for name, patcher in stubs.items()}
            try:
                run.run_audit(with_graph=True, **kw)
            finally:
                for patcher in stubs.values():
                    patcher.stop()
        return graph.call_args.kwargs, {n: mocks[n].call_count for n in ADVISORY_CHECKS}

    def test_positive_control_read_only_reaches_the_validator(self):
        self.assertEqual(self._run(read_only=True)[0], {"no_log": True})

    def test_rejection_control_the_default_logs(self):
        self.assertEqual(self._run()[0], {"no_log": False})

    def test_positive_control_read_only_runs_every_advisory_check_once(self):
        self.assertEqual(self._run(read_only=True)[1], dict.fromkeys(ADVISORY_CHECKS, 1))

    def test_negative_control_the_default_runs_every_advisory_check_once(self):
        self.assertEqual(self._run()[1], dict.fromkeys(ADVISORY_CHECKS, 1))


class MainReadOnlyTests(unittest.TestCase):
    """main() with --read-only writes no LOG.md entry and refuses --save."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.workflow_log = self.dir / "workflow-LOG.md"
        self.root_log = self.dir / "root-LOG.md"
        for path in (self.workflow_log, self.root_log):
            path.write_bytes(SEED)
        for name, value in (("WORKFLOW_LOG", self.workflow_log),
                            ("ROOT_LOG", self.root_log),
                            ("WORKFLOW_DIR", self.dir)):
            patcher = mock.patch.object(run, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.audit = mock.patch.object(run, "run_audit", return_value=([], 3)).start()
        self.addCleanup(mock.patch.stopall)

    def _main(self, *args):
        with mock.patch.object(run.sys, "argv", ["run.py", *args]), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            run.main()

    def _unchanged(self):
        return (self.workflow_log.read_bytes() == SEED
                and self.root_log.read_bytes() == SEED)

    def test_positive_control_the_default_writes_both_logs(self):
        self._main()
        self.assertIn(b"structural audit", self.workflow_log.read_bytes())
        self.assertIn(b"audit workflow ran.", self.root_log.read_bytes())

    def test_rejection_control_read_only_leaves_both_logs_byte_identical(self):
        self._main("--read-only")
        self.assertTrue(self._unchanged())
        self.assertEqual(self.audit.call_args.kwargs, {"with_graph": True,
                                                       "read_only": True})

    def test_rejection_control_read_only_in_context_mode_writes_nothing(self):
        with mock.patch.object(run, "run_context_audit", return_value=([], 1)) as ctx:
            self._main("--context", "workflows/audit", "--read-only")
        self.assertTrue(self._unchanged())
        # Writing nothing must not come from auditing nothing.
        ctx.assert_called_once_with(["workflows/audit"])

    def test_rejection_control_save_is_refused_in_read_only_mode(self):
        with self.assertRaises(SystemExit) as caught:
            self._main("--read-only", "--save")
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse((self.dir / "last-report.md").exists())
        self.assertTrue(self._unchanged())
        self.audit.assert_not_called()

    def test_negative_control_save_alone_still_saves(self):
        self._main("--save")
        self.assertTrue((self.dir / "last-report.md").exists())


if __name__ == "__main__":
    unittest.main()
