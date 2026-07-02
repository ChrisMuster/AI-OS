#!/usr/bin/env python3
"""Tests for the close-out verifier (workflows/close-out/scripts/run.py).

Hermetic and fast: these do not run the full structural audit. They cover
scope selection, the subprocess test runner, gate aggregation, report
formatting, and the import coupling to the audit and link-check scripts.
"""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run.py"


def load_run():
    spec = importlib.util.spec_from_file_location("closeout_run_undertest", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


run = load_run()


class DiscoverAndScopeTests(unittest.TestCase):
    def test_discovers_known_suites(self):
        owners = [owner for owner, _ in run.discover_suites()]
        self.assertIn("workflows/audit", owners)
        self.assertIn("workflows/close-out", owners)

    def test_all_scope_returns_everything(self):
        suites, label = run.select_suites("all")
        self.assertEqual(label, "all")
        self.assertEqual(len(suites), len(run.discover_suites()))

    def test_name_scope_filters_to_one_workflow(self):
        suites, _ = run.select_suites("audit")
        self.assertGreaterEqual(len(suites), 1)
        self.assertTrue(all(owner == "workflows/audit" for owner, _ in suites))

    def test_affected_scope_uses_changed_paths(self):
        original = run.changed_paths
        try:
            run.changed_paths = lambda: {"workflows/audit/scripts/run.py"}
            suites, label = run.select_suites("affected")
            owners = [owner for owner, _ in suites]
            self.assertEqual(label, "affected")
            self.assertIn("workflows/audit", owners)
            self.assertNotIn("workflows/close-out", owners)
        finally:
            run.changed_paths = original

    def test_affected_falls_back_to_all_when_git_unavailable(self):
        original = run.changed_paths
        try:
            run.changed_paths = lambda: None
            suites, label = run.select_suites("affected")
            self.assertIn("running all", label)
            self.assertEqual(len(suites), len(run.discover_suites()))
        finally:
            run.changed_paths = original

    def test_cross_cutting_change_escalates_to_all(self):
        for changed in ({"AGENTS.md"}, {"templates/CONTEXT.md"}, {"README.md"}):
            original = run.changed_paths
            try:
                run.changed_paths = lambda c=changed: c
                suites, label = run.select_suites("affected")
                self.assertIn("cross-cutting", label)
                self.assertEqual(len(suites), len(run.discover_suites()))
            finally:
                run.changed_paths = original

    def test_non_cross_cutting_change_stays_scoped(self):
        original = run.changed_paths
        try:
            run.changed_paths = lambda: {"workflows/audit/scripts/run.py"}
            suites, label = run.select_suites("affected")
            owners = [owner for owner, _ in suites]
            self.assertEqual(label, "affected")
            self.assertIn("workflows/audit", owners)
            self.assertNotIn("workflows/close-out", owners)
        finally:
            run.changed_paths = original


class CrossCuttingTests(unittest.TestCase):
    def test_root_markdown_is_cross_cutting(self):
        self.assertTrue(run.is_cross_cutting({"AGENTS.md"}))
        self.assertTrue(run.is_cross_cutting({"README.md"}))

    def test_templates_dir_is_cross_cutting(self):
        self.assertTrue(run.is_cross_cutting({"templates/SKILL.md.template"}))

    def test_workflow_file_is_not_cross_cutting(self):
        self.assertFalse(run.is_cross_cutting({"workflows/audit/scripts/run.py"}))
        self.assertFalse(run.is_cross_cutting({"workflows/audit/CONTEXT.md"}))

    def test_empty_change_set_is_not_cross_cutting(self):
        self.assertFalse(run.is_cross_cutting(set()))


class ReexecTests(unittest.TestCase):
    def test_venv_python_path_is_platform_shaped(self):
        path = run.venv_python()
        parts = path.as_posix()
        self.assertIn(".venv", parts)
        self.assertTrue(parts.endswith("python") or parts.endswith("python.exe"))

    def test_reexec_is_noop_when_marker_set(self):
        original = os.environ.get(run.REEXEC_MARKER)
        try:
            os.environ[run.REEXEC_MARKER] = "1"
            # Must return without raising SystemExit (would re-exec otherwise).
            self.assertIsNone(run.reexec_under_venv())
        finally:
            if original is None:
                os.environ.pop(run.REEXEC_MARKER, None)
            else:
                os.environ[run.REEXEC_MARKER] = original


class TestRunnerTests(unittest.TestCase):
    def _write(self, folder, name, body):
        path = Path(folder) / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_passing_test_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._write(folder, "test_pass.py", "import sys\nsys.exit(0)\n")
            ok, tail = run.run_test_file(path)
            self.assertTrue(ok)
            self.assertEqual(tail, "")

    def test_failing_test_file_captures_tail(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._write(folder, "test_fail.py", "print('boom')\nraise SystemExit(1)\n")
            ok, tail = run.run_test_file(path)
            self.assertFalse(ok)
            self.assertIn("boom", tail)

    def test_gate_tests_aggregates_pass_and_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            good = self._write(folder, "test_good.py", "import sys\nsys.exit(0)\n")
            bad = self._write(folder, "test_bad.py", "import sys\nsys.exit(1)\n")
            green = run.gate_tests([("tmp/good", [good])])
            self.assertTrue(green["passed"])
            red = run.gate_tests([("tmp/bad", [good, bad])])
            self.assertFalse(red["passed"])


class ReportTests(unittest.TestCase):
    def test_report_pass(self):
        gates = [
            {"name": "structural audit", "passed": True, "detail": "ok"},
            {"name": "link audit", "passed": True, "detail": "0 dead link(s)"},
            {"name": "tests", "passed": True, "detail": "0 files", "files": []},
        ]
        self.assertIn("RESULT: PASS", run.build_report("all", gates))

    def test_report_fail_lists_failed_gate_and_file(self):
        gates = [
            {"name": "structural audit", "passed": False, "detail": "1 FAIL"},
            {"name": "tests", "passed": False, "detail": "x",
             "files": [{"file": "a/test_x.py", "passed": False, "tail": "boom"}]},
        ]
        report = run.build_report("all", gates)
        self.assertIn("RESULT: FAIL", report)
        self.assertIn("structural audit", report)
        self.assertIn("test_x.py", report)


class CouplingTests(unittest.TestCase):
    def test_audit_exposes_run_audit(self):
        mod = run.load_module(run.AUDIT_RUN, "probe_audit")
        self.assertTrue(hasattr(mod, "run_audit"))

    def test_link_exposes_run_audit_mode(self):
        mod = run.load_module(run.LINK_RUN, "probe_link")
        self.assertTrue(hasattr(mod, "run_audit_mode"))

    def test_changed_paths_returns_set_or_none(self):
        result = run.changed_paths()
        self.assertTrue(result is None or isinstance(result, set))


if __name__ == "__main__":
    unittest.main(verbosity=2)
