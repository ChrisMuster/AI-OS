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
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run.py"


def load_run():
    spec = importlib.util.spec_from_file_location("closeout_run_undertest", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


run = load_run()


class BlockingLabelsAreDocumentedTests(unittest.TestCase):
    """The workflow's own CONTEXT.md must describe the real blocking contract.

    ``BLOCKING_LABELS`` is the single source of truth for what hard-fails
    close-out, and adding an entry is deliberately a one-line change in run.py.
    That is exactly how the documentation fell behind: ``skill-hardening`` was
    added as a second blocking label in July, and a month later CONTEXT.md still
    called ``doc-sync`` "the one exception" and never named severity at all. A
    reader landing on that file would have concluded the label alone blocks.
    These two assertions make the prose follow the map.
    """

    def context_md(self):
        path = Path(__file__).resolve().parents[1] / "CONTEXT.md"
        return path.read_text(encoding="utf-8")

    def test_every_blocking_label_is_named_in_context_md(self):
        content = self.context_md()
        missing = [label for label in run.BLOCKING_LABELS
                   if label not in content]
        self.assertEqual(missing, [])

    def test_context_md_states_that_only_a_warn_blocks(self):
        # The severity half of the contract, and the half that kept going
        # stale. Deliberately an exact-claim assertion: rewording the sentence
        # is fine, dropping the claim is not.
        self.assertIn("only a warn", self.context_md().lower())


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
        path.write_bytes(body.encode("utf-8"))
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


class DegradedSurfacingTests(unittest.TestCase):
    """A DEGRADED check is shown loudly and never counted as a pass."""

    def _gates(self, degraded):
        return [
            {"name": "structural audit", "passed": True,
             "detail": "3 dirs checked, 0 FAIL, 0 WARN, 1 DEGRADED",
             "degraded": degraded},
            {"name": "link audit", "passed": True, "detail": "0 dead link(s)"},
            {"name": "tests", "passed": True, "detail": "0 files", "files": []},
        ]

    def test_collect_degraded_aggregates_across_gates(self):
        gates = [{"degraded": ["a"]}, {"degraded": ["b"]}, {"detail": "x"}]
        self.assertEqual(run.collect_degraded(gates), ["a", "b"])

    def test_overall_status_distinguishes_degraded_from_clean_pass(self):
        self.assertEqual(run.overall_status(True, []), "pass")
        self.assertEqual(run.overall_status(True, ["x did not run"]), "degraded")
        self.assertEqual(run.overall_status(False, []), "fail")
        self.assertEqual(run.overall_status(False, ["x did not run"]), "fail")

    def test_report_surfaces_degraded_and_is_not_a_clean_pass(self):
        gates = self._gates(["encoding check did not run - boom. Fix: run setup.py"])
        report = run.build_report("all", gates)
        self.assertIn("DEGRADED - 1 check(s) did not run", report)
        self.assertIn("encoding check did not run", report)
        # The verdict must read as DEGRADED, distinct from a clean pass, so a
        # reader of only the RESULT line cannot mistake an unrun check for a pass.
        self.assertIn("RESULT: DEGRADED", report)
        self.assertNotIn("RESULT: PASS", report)

    def test_report_includes_repair_note_when_given(self):
        gates = self._gates(["x did not run"])
        report = run.build_report("all", gates,
                                  repair_note="--repair: repair ran setup.py (exit 0); all checks now run.")
        self.assertIn("--repair: repair ran setup.py", report)


class RepairTests(unittest.TestCase):
    def test_missing_setup_py_reports_cleanly(self):
        with mock.patch.object(run, "SETUP_PY", Path("nope/does-not-exist/setup.py")):
            ok, note = run.run_repair()
        self.assertFalse(ok)
        self.assertIn("setup.py", note)

    def test_run_repair_invokes_setup_py(self):
        fake = mock.Mock(returncode=0)
        # Point SETUP_PY at a real file so the existence check passes; mock the run.
        with mock.patch.object(run, "SETUP_PY", Path(__file__)), \
             mock.patch.object(run.subprocess, "run", return_value=fake) as spy:
            ok, note = run.run_repair()
        self.assertTrue(ok)
        self.assertIn("setup.py", note)
        spy.assert_called_once()


class DocSyncTeethTests(unittest.TestCase):
    """Close-out hard-fails on a doc-sync WARN while the audit stays advisory.

    Plan R2-3, Option B: doc-sync drift is an advisory WARN inside the audit
    (audit exit code unchanged), but the close-out verifier turns it into a hard
    fail by inspecting label *and severity* on the single in-process audit call
    it already makes. Severity is load-bearing: a doc-sync DEGRADED means the
    guard could not run, which is non-blocking and surfaces via the DEGRADED
    path instead (see ``test_degraded_doc_sync_is_not_drift``).
    """

    def _fake_audit(self, findings, dir_count=5):
        fake_mod = mock.Mock()
        fake_mod.run_audit.return_value = (findings, dir_count)
        return mock.patch.object(run, "load_module", return_value=fake_mod)

    def test_doc_sync_finding_hard_fails_gate(self):
        findings = [("WARN", "doc-sync",
                     "workflows/foo: CONTEXT.md not updated for changes in this "
                     "directory")]
        with self._fake_audit(findings):
            gate = run.gate_audit()
        self.assertFalse(gate["passed"])
        self.assertEqual(len(gate["blocking"]["doc-sync"]), 1)
        self.assertIn("doc-sync drift", gate["detail"])

    def test_log_drift_also_hard_fails(self):
        findings = [("WARN", "doc-sync",
                     "workflows/foo: no LOG.md for a directory whose content "
                     "changed")]
        with self._fake_audit(findings):
            gate = run.gate_audit()
        self.assertFalse(gate["passed"])

    def test_non_doc_sync_warn_stays_advisory(self):
        # ai-style and personal-data WARNs are advisory in the audit and must not
        # flip the close-out gate. Only a WARN under one of the BLOCKING_LABELS
        # (doc-sync, skill-hardening) is a hard fail; ai-style and personal-data
        # are not blocking labels at any severity.
        findings = [("WARN", "ai-style", "x.md:3: em dash present"),
                    ("WARN", "personal-data", "y.md: denylisted term")]
        with self._fake_audit(findings):
            gate = run.gate_audit()
        self.assertTrue(gate["passed"])
        self.assertEqual(gate["blocking"]["doc-sync"], [])

    def test_degraded_doc_sync_is_not_drift(self):
        # A DEGRADED doc-sync finding means the guard could not run. It is
        # non-blocking: it must not count as drift or hard-fail the structural
        # gate; it surfaces via the DEGRADED path instead. (Regression: the gate
        # once collected every doc-sync finding regardless of severity, so a
        # degraded guard wrongly failed close-out as drift.)
        findings = [("DEGRADED", "doc-sync", "doc-sync check did not run - boom")]
        with self._fake_audit(findings):
            gate = run.gate_audit()
        self.assertTrue(gate["passed"])
        self.assertEqual(gate["blocking"]["doc-sync"], [])
        self.assertIn("doc-sync check did not run", " ".join(gate["degraded"]))

    def test_structural_fail_still_fails_gate(self):
        findings = [("FAIL", "structural", "workflows/foo - Missing CONTEXT.md")]
        with self._fake_audit(findings):
            gate = run.gate_audit()
        self.assertFalse(gate["passed"])

    def test_clean_audit_passes(self):
        with self._fake_audit([]):
            gate = run.gate_audit()
        self.assertTrue(gate["passed"])
        self.assertEqual(gate["blocking"]["doc-sync"], [])

    def test_report_surfaces_doc_sync_drift_and_fails(self):
        gates = [
            {"name": "structural audit", "passed": False,
             "detail": "5 dirs checked, 0 FAIL, 0 WARN, 1 doc-sync drift",
             "blocking": {"doc-sync": ["workflows/foo: no LOG.md for a directory "
                                       "whose content changed"]}},
            {"name": "link audit", "passed": True, "detail": "0 dead link(s)"},
            {"name": "tests", "passed": True, "detail": "0 files", "files": []},
        ]
        report = run.build_report("all", gates)
        self.assertIn("RESULT: FAIL", report)
        self.assertIn("[doc-sync]", report)
        self.assertIn("no LOG.md", report)


class SkillHardeningTeethTests(unittest.TestCase):
    """Close-out hard-fails on skill-hardening findings while the audit stays advisory.

    The skill-hardening guard is the second advisory-in-audit / hard-fail-at-
    close-out label (same treatment as doc-sync): a SKILL.md missing its Hardening
    section or a required field blocks close-out even though it never changes the
    audit's own exit code.
    """

    def _fake_audit(self, findings, dir_count=5):
        fake_mod = mock.Mock()
        fake_mod.run_audit.return_value = (findings, dir_count)
        return mock.patch.object(run, "load_module", return_value=fake_mod)

    def test_skill_hardening_finding_hard_fails_gate(self):
        findings = [("WARN", "skill-hardening",
                     "skills/foo/SKILL.md: missing `## Hardening` section")]
        with self._fake_audit(findings):
            gate = run.gate_audit()
        self.assertFalse(gate["passed"])
        self.assertEqual(len(gate["blocking"]["skill-hardening"]), 1)
        self.assertIn("skill-hardening gap", gate["detail"])

    def test_missing_field_also_hard_fails(self):
        findings = [("WARN", "skill-hardening",
                     "skills/foo/SKILL.md: Hardening field `Never` is empty")]
        with self._fake_audit(findings):
            gate = run.gate_audit()
        self.assertFalse(gate["passed"])

    def test_non_blocking_warn_stays_advisory(self):
        # ai-style and personal-data WARNs must not flip the gate; only doc-sync
        # and skill-hardening are hard fails.
        findings = [("WARN", "ai-style", "x.md:3: em dash present"),
                    ("WARN", "personal-data", "y.md: denylisted term")]
        with self._fake_audit(findings):
            gate = run.gate_audit()
        self.assertTrue(gate["passed"])
        self.assertEqual(gate["blocking"]["skill-hardening"], [])

    def test_degraded_skill_hardening_is_not_gap(self):
        # A DEGRADED skill-hardening finding means the guard could not run. It is
        # non-blocking and surfaces via the DEGRADED path, not as a hard fail.
        findings = [("DEGRADED", "skill-hardening",
                     "skill-hardening check did not run - boom")]
        with self._fake_audit(findings):
            gate = run.gate_audit()
        self.assertTrue(gate["passed"])
        self.assertEqual(gate["blocking"]["skill-hardening"], [])
        self.assertIn("skill-hardening check did not run",
                      " ".join(gate["degraded"]))

    def test_clean_audit_passes(self):
        with self._fake_audit([]):
            gate = run.gate_audit()
        self.assertTrue(gate["passed"])
        self.assertEqual(gate["blocking"]["skill-hardening"], [])

    def test_report_surfaces_skill_hardening_gap_and_fails(self):
        gates = [
            {"name": "structural audit", "passed": False,
             "detail": "5 dirs checked, 0 FAIL, 0 WARN, 1 skill-hardening gap",
             "blocking": {"skill-hardening": ["skills/foo/SKILL.md: missing "
                                              "`## Hardening` section"]}},
            {"name": "link audit", "passed": True, "detail": "0 dead link(s)"},
            {"name": "tests", "passed": True, "detail": "0 files", "files": []},
        ]
        report = run.build_report("all", gates)
        self.assertIn("RESULT: FAIL", report)
        self.assertIn("[skill-hardening]", report)
        self.assertIn("missing `## Hardening` section", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
