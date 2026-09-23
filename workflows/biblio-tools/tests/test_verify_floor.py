#!/usr/bin/env python3
"""Unit tests for the Python floor checks in verify.py and setup.py.

Covers the three outcomes of the floor judgement (FAIL below, PASS at, WARN
above), the `surface` marker that makes the above-floor WARN visible at session
startup, and the .venv interpreter check: both verify.py's own check and
setup.py's refusal to pass or repair a .venv built below the floor.

The .venv version is faked by replacing the probe function, so no second
interpreter is needed. The real current-machine .venv is also checked once, as
the control that the normal path still passes.

Standard library only; neither module imports the `mcp` package.
"""

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import verify  # noqa: E402


def _load_setup():
    """Load scripts/setup.py under a name that cannot shadow setuptools."""
    spec = importlib.util.spec_from_file_location("bd_setup", SCRIPTS / "setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bd_setup = _load_setup()

FLOOR = verify.PYTHON_FLOOR
BELOW = (FLOOR[0], FLOOR[1] - 1, 0)
AT = (FLOOR[0], FLOOR[1], 1)
ABOVE = (FLOOR[0], FLOOR[1] + 1, 0)


class TestFloorResult(unittest.TestCase):
    def test_below_at_above(self):
        self.assertEqual(verify.floor_result("c", BELOW)["status"], "FAIL")
        self.assertEqual(verify.floor_result("c", AT)["status"], "PASS")
        self.assertEqual(verify.floor_result("c", ABOVE)["status"], "WARN")

    def test_only_the_above_floor_warn_surfaces(self):
        self.assertTrue(verify.floor_result("c", ABOVE).get("surface"))
        self.assertFalse(verify.floor_result("c", AT).get("surface"))
        self.assertFalse(verify.floor_result("c", BELOW).get("surface"))

    def test_check_python_version_reads_the_running_interpreter(self):
        with mock.patch.object(verify.sys, "version_info", ABOVE + ("final", 0)):
            result = verify.check_python_version()
        self.assertEqual(result["status"], "WARN")
        self.assertTrue(result["surface"])

    def test_floors_agree(self):
        self.assertEqual(verify.PYTHON_FLOOR, bd_setup.PYTHON_FLOOR)


class TestStartupVisibility(unittest.TestCase):
    """The startup rule reads the summary and the plain-text markers, so the
    surface flag has to reach both, not just the check's return value."""

    def setUp(self):
        self.results = [
            {"check": "a", "status": "PASS", "detail": "ok"},
            verify.floor_result("Python", ABOVE),
            {"check": "b", "status": "WARN", "detail": "ordinary warning"},
        ]

    def test_summary_counts_surfaced_results(self):
        summary = verify.summarise(self.results)
        self.assertEqual(summary, {"pass": 1, "warn": 2, "fail": 0, "surface": 1})

    def test_plain_text_marks_only_the_surfaced_result(self):
        text = verify.format_results(self.results)
        self.assertEqual(text.count("[SURFACE]"), 2)  # the line and the summary
        self.assertIn("[WARN] [SURFACE] Python", text)
        self.assertNotIn("[WARN] [SURFACE] b", text)
        self.assertIn("must be shown to the user", text)

    def test_no_surface_text_when_nothing_surfaces(self):
        text = verify.format_results(self.results[:1] + self.results[2:])
        self.assertNotIn("[SURFACE]", text)


class TestVerifyRuntimeVersion(unittest.TestCase):
    def _check(self, version):
        with mock.patch.object(verify, "_venv_python", return_value=Path(sys.executable)), \
             mock.patch.object(verify, "_venv_python_version", return_value=version):
            return verify.check_runtime_python_version()

    def test_old_venv_fails(self):
        result = self._check(BELOW)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("rebuilt", result["detail"])

    def test_current_venv_passes(self):
        self.assertEqual(self._check(AT)["status"], "PASS")

    def test_newer_venv_warns_and_surfaces(self):
        result = self._check(ABOVE)
        self.assertEqual(result["status"], "WARN")
        self.assertTrue(result["surface"])

    def test_missing_venv_fails(self):
        with mock.patch.object(verify, "_venv_python", return_value=None):
            self.assertEqual(verify.check_runtime_python_version()["status"], "FAIL")

    def test_real_venv_on_this_machine(self):
        """Control: the real .venv, probed for real, is judged by the same rule."""
        python = verify._venv_python()
        if python is None:
            self.skipTest("no project .venv on this machine")
        version = verify._venv_python_version(python)
        expected = verify.floor_result("x", version)["status"]
        self.assertEqual(verify.check_runtime_python_version()["status"], expected)


class TestReportEncoding(unittest.TestCase):
    def test_report_is_utf8_through_a_pipe(self):
        """A pipe on Windows defaults to cp1252; the report must be UTF-8.
        The --list header is ASCII, so the check is on the docstring epilog
        printed by --help, which carries a non-ASCII dash."""
        import subprocess
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "verify.py"), "--help"],
            capture_output=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0)
        text = result.stdout.decode("utf-8")  # raises if not UTF-8
        self.assertIn(chr(0x2014), text)  # the em dash in verify.py's docstring


class TestSetupRuntimeVersion(unittest.TestCase):
    def _probe(self, version):
        packages = {name: True for name in bd_setup.PACKAGE_IMPORTS}
        return {"version": list(version), "packages": packages}

    def _patched(self, version):
        return (
            mock.patch.object(bd_setup, "venv_python",
                              return_value=bd_setup.PROJECT_ROOT / "requirements.txt"),
            mock.patch.object(bd_setup, "runtime_probe", return_value=self._probe(version)),
        )

    def test_check_fails_on_an_old_venv(self):
        """Positive control for PF2: before the repair, --check passed here
        because it read package availability only."""
        venv, probe = self._patched(BELOW)
        with venv, probe, mock.patch("builtins.print"):
            self.assertEqual(bd_setup.check(), 1)

    def test_check_passes_on_a_current_venv(self):
        venv, probe = self._patched(AT)
        with venv, probe, mock.patch("builtins.print"):
            self.assertEqual(bd_setup.check(), 0)

    def test_setup_refuses_to_repair_an_old_venv(self):
        venv, probe = self._patched(BELOW)
        with venv, probe, \
             mock.patch.object(bd_setup.subprocess, "run",
                               side_effect=AssertionError("pip must not run")):
            with self.assertRaises(RuntimeError) as ctx:
                bd_setup.setup(dry_run=False)
        self.assertIn("cannot be repaired in place", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
