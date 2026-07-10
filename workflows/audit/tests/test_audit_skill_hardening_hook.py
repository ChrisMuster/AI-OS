#!/usr/bin/env python3
"""Unit tests for the audit's skill-hardening-guard hook.

Covers the pure ``skill_hardening_findings`` merge helper (WARN and DEGRADED
mapping, label, message passthrough), the graceful-degradation behaviour of
``run_skill_hardening_check`` without spawning the real guard, and the
producer/consumer contract between the real guard's ``findings_json`` output and
this hook's parser.
"""

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run  # noqa: E402

GUARD_RUN = (run.PROJECT_ROOT / "workflows" / "skill-hardening-guard"
             / "scripts" / "run.py")


def load_guard():
    spec = importlib.util.spec_from_file_location("shg_run_contract", GUARD_RUN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestSkillHardeningFindings(unittest.TestCase):
    def test_warn_is_mapped(self):
        payload = {"findings": [
            {"severity": "WARN", "label": "skill-hardening",
             "message": "skills/foo/SKILL.md: missing `## Hardening` section"},
        ]}
        self.assertEqual(
            run.skill_hardening_findings(payload),
            [("WARN", "skill-hardening",
              "skills/foo/SKILL.md: missing `## Hardening` section")],
        )

    def test_info_dropped_but_degraded_kept(self):
        # DEGRADED (a SKILL.md the guard could not read) passes through so the
        # close-out gate can surface it as non-blocking; only INFO is dropped.
        payload = {"findings": [
            {"severity": "WARN", "label": "skill-hardening", "message": "keep"},
            {"severity": "DEGRADED", "label": "skill-hardening", "message": "cant"},
            {"severity": "INFO", "label": "skill-hardening", "message": "drop"},
        ]}
        self.assertEqual(
            run.skill_hardening_findings(payload),
            [("WARN", "skill-hardening", "keep"),
             ("DEGRADED", "skill-hardening", "cant")],
        )

    def test_label_is_skill_hardening(self):
        payload = {"findings": [
            {"severity": "WARN", "label": "skill-hardening", "message": "m"},
        ]}
        self.assertEqual(run.skill_hardening_findings(payload)[0][1],
                         "skill-hardening")

    def test_empty_and_missing_findings(self):
        self.assertEqual(run.skill_hardening_findings({"findings": []}), [])
        self.assertEqual(run.skill_hardening_findings({}), [])

    def test_output_findings_are_valid_audit_tuples(self):
        payload = {"findings": [
            {"severity": "WARN", "label": "skill-hardening", "message": "m"},
        ]}
        for finding in run.skill_hardening_findings(payload):
            self.assertEqual(len(finding), 3)
            self.assertIn(finding[0], ("FAIL", "WARN", "INFO", "DEGRADED"))


class TestRunSkillHardeningCheckGracefulSkip(unittest.TestCase):
    def test_missing_cli_returns_degraded(self):
        bogus = Path("workflows/skill-hardening-guard/scripts/NOPE.py")
        with mock.patch.object(run, "SKILL_HARDENING_RUN_PY", bogus):
            findings = run.run_skill_hardening_check()
        self.assertEqual(len(findings), 1)
        level, label, message = findings[0]
        self.assertEqual(level, "DEGRADED")
        self.assertEqual(label, "skill-hardening")
        self.assertIn("did not run", message)

    def test_unparseable_output_returns_degraded(self):
        fake = mock.Mock(stdout="not json", stderr="boom", returncode=1)
        with mock.patch.object(run.subprocess, "run", return_value=fake):
            findings = run.run_skill_hardening_check()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][0], "DEGRADED")
        self.assertIn("did not run", findings[0][2])

    def test_subprocess_exception_returns_degraded(self):
        with mock.patch.object(run.subprocess, "run", side_effect=OSError("nope")):
            findings = run.run_skill_hardening_check()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][0], "DEGRADED")
        self.assertIn("did not run", findings[0][2])

    def test_valid_payload_is_merged(self):
        fake = mock.Mock(
            stdout='{"findings": [{"severity": "WARN", "label": "skill-hardening", '
                   '"message": "skills/foo/SKILL.md: Hardening field `Never` is '
                   'empty"}]}',
            stderr="",
            returncode=0,
        )
        with mock.patch.object(run.subprocess, "run", return_value=fake):
            findings = run.run_skill_hardening_check()
        self.assertEqual(
            findings,
            [("WARN", "skill-hardening",
              "skills/foo/SKILL.md: Hardening field `Never` is empty")],
        )


class TestGuardAuditContract(unittest.TestCase):
    """The guard is the producer and this hook the consumer; they are wired only
    by the JSON shape and the ``skill-hardening`` label. This asserts a payload
    the real guard emits survives the hook's parser with severity and label
    intact - so a rename or shape change on either side fails a test."""

    def test_findings_json_round_trips_through_hook(self):
        guard = load_guard()
        findings = [
            ("WARN", guard.LABEL, "skills/foo/SKILL.md: missing `## Hardening` "
             "section"),
            ("DEGRADED", guard.LABEL, "skills/bar/SKILL.md: could not read (boom)"),
        ]
        payload = json.loads(guard.findings_json(findings))
        merged = run.skill_hardening_findings(payload)
        self.assertEqual(
            merged,
            [("WARN", "skill-hardening",
              "skills/foo/SKILL.md: missing `## Hardening` section"),
             ("DEGRADED", "skill-hardening",
              "skills/bar/SKILL.md: could not read (boom)")],
        )

    def test_guard_label_matches_hook_filter(self):
        # The label the guard stamps must be exactly the one the hook keeps.
        guard = load_guard()
        self.assertEqual(guard.LABEL, "skill-hardening")


if __name__ == "__main__":
    unittest.main()
