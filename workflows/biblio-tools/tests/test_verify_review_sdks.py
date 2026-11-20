#!/usr/bin/env python3
"""Unit tests for the review-orchestration SDK check in verify.py.

The check asks the project interpreter whether each SDK can be found, loads
neither, and reports a missing one as a WARN naming the workflow's
requirements file, never a FAIL. The probe is run for real against this
machine's .venv and against a planted package that raises on import, so the
"loads neither" claim is tested rather than asserted.

Standard library only.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import verify  # noqa: E402


class TestReviewSdkCheck(unittest.TestCase):
    def _check(self, missing):
        with mock.patch.object(verify, "_venv_python", return_value=Path(sys.executable)), \
             mock.patch.object(verify, "_missing_review_sdks", return_value=missing):
            return verify.check_review_sdks()

    def test_both_found_passes(self):
        self.assertEqual(self._check([])["status"], "PASS")

    def test_missing_sdk_warns_naming_it_and_the_requirements_file(self):
        result = self._check(["openai_codex"])
        self.assertEqual(result["status"], "WARN")
        self.assertIn("openai_codex", result["detail"])
        self.assertIn("workflows/review-orchestration/requirements.txt", result["detail"])
        self.assertFalse(result.get("surface"))

    def test_no_venv_warns(self):
        with mock.patch.object(verify, "_venv_python", return_value=None):
            result = verify.check_review_sdks()
        self.assertEqual(result["status"], "WARN")
        self.assertIn("requirements.txt", result["detail"])

    def test_probe_failure_warns_not_fails(self):
        with mock.patch.object(verify, "_venv_python", return_value=Path(sys.executable)), \
             mock.patch.object(verify, "_missing_review_sdks",
                               side_effect=RuntimeError("boom")):
            result = verify.check_review_sdks()
        self.assertEqual(result["status"], "WARN")
        self.assertIn("boom", result["detail"])

    def test_never_fails(self):
        for missing in ([], ["claude_agent_sdk"], list(verify.REVIEW_SDKS)):
            self.assertNotEqual(self._check(missing)["status"], "FAIL")

    def test_runs_for_every_ai(self):
        for ai in verify.AI_REQUIREMENTS:
            names = [r["check"] for r in verify.run_checks(ai, dry_run=True)]
            self.assertIn(verify.REVIEW_SDK_CHECK, names, ai)

    def test_names_match_the_requirements_file(self):
        """The import names stand for the two pinned distributions."""
        text = (verify.PROJECT_ROOT / verify.REVIEW_REQUIREMENTS).read_text(encoding="utf-8")
        pinned = sorted(line.split("==")[0].replace("-", "_") for line in text.splitlines()
                        if line and not line.startswith("#"))
        self.assertEqual(pinned, sorted(verify.REVIEW_SDKS))


def _plant(tmp):
    """A package whose import raises, so finding it proves it was not loaded."""
    pkg = Path(tmp) / "bd_planted_sdk"
    pkg.mkdir()
    (pkg / "__init__.py").write_bytes(b"raise RuntimeError('loaded')\n")


class TestProbe(unittest.TestCase):
    def test_probe_finds_without_loading(self):
        """A package whose import raises is reported found, so it was never run.
        The folder is put on sys.path by the code itself, since -I ignores
        PYTHONPATH."""
        with tempfile.TemporaryDirectory() as tmp:
            _plant(tmp)
            code = f"import sys; sys.path.insert(0, {tmp!r})\n" + verify.REVIEW_SDK_PROBE
            result = subprocess.run(
                [sys.executable, "-I", "-c", code, "bd_planted_sdk", "bd_absent_sdk"],
                capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["bd_absent_sdk"])

    def test_pythonpath_cannot_supply_an_sdk(self):
        """Cross-review R1-1: a package reachable only through PYTHONPATH is
        still reported missing. Without -I this returned no missing names."""
        with tempfile.TemporaryDirectory() as tmp:
            _plant(tmp)
            with mock.patch.dict(os.environ, {"PYTHONPATH": tmp}), \
                 mock.patch.object(verify, "REVIEW_SDKS", ("bd_planted_sdk",)):
                missing = verify._missing_review_sdks(Path(sys.executable))
        self.assertEqual(missing, ["bd_planted_sdk"])

    def test_real_venv_on_this_machine(self):
        """Control: the real probe on this machine's .venv, judged by the same rule."""
        python = verify._venv_python()
        if python is None:
            self.skipTest("no project .venv on this machine")
        missing = verify._missing_review_sdks(python)
        self.assertTrue(set(missing) <= set(verify.REVIEW_SDKS))
        expected = "WARN" if missing else "PASS"
        self.assertEqual(verify.check_review_sdks()["status"], expected)

    def test_real_probe_reports_an_absent_name(self):
        """Positive control: the real probe can report a miss."""
        python = verify._venv_python()
        if python is None:
            self.skipTest("no project .venv on this machine")
        with mock.patch.object(verify, "REVIEW_SDKS", ("bd_no_such_sdk",)):
            self.assertEqual(verify._missing_review_sdks(python), ["bd_no_such_sdk"])


if __name__ == "__main__":
    unittest.main()
