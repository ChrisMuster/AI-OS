#!/usr/bin/env python3
"""Unit tests for the Python-deps and CLI-tool checkers."""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import checkers  # noqa: E402


class _Proc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class TestInstalledVersion(unittest.TestCase):
    def test_absent_command(self):
        with mock.patch.object(checkers.shutil, "which", return_value=None):
            self.assertIsNone(checkers._installed_version("nope", "--version", None))

    def test_regex_extraction(self):
        with mock.patch.object(checkers.shutil, "which", return_value="/usr/bin/tool"), \
                mock.patch.object(checkers.subprocess, "run",
                                  return_value=_Proc(stdout="tool version 2.5.1\n")):
            result = checkers._installed_version("tool", "--version", r"(\d+\.\d+\.\d+)")
        self.assertEqual(result, "2.5.1")


class TestCheckCliTool(unittest.TestCase):
    def test_not_installed(self):
        with mock.patch.object(checkers, "_installed_version", return_value=None):
            result, source, error = checkers.check_cli_tool({"name": "X", "command": "x"})
        self.assertEqual(result["change"], "not installed")
        self.assertIsNone(source)
        self.assertIsNone(error)

    def test_outdated(self):
        spec = {"name": "X", "command": "x", "latest": {"source": "npm", "id": "@x/x"}}
        with mock.patch.object(checkers, "_installed_version", return_value="1.0.0"), \
                mock.patch.object(checkers.registries, "resolve_latest",
                                  return_value=("1.2.0", None)):
            result, source, error = checkers.check_cli_tool(spec)
        self.assertEqual(result["change"], "minor")
        self.assertEqual(result["upgrade"], "npm install -g @x/x@latest")
        self.assertEqual(source, "npm")

    def test_latest_unavailable(self):
        spec = {"name": "X", "command": "x", "latest": {"source": "npm", "id": "@x/x"}}
        with mock.patch.object(checkers, "_installed_version", return_value="1.0.0"), \
                mock.patch.object(checkers.registries, "resolve_latest",
                                  return_value=(None, "HTTP 500")):
            result, source, error = checkers.check_cli_tool(spec)
        self.assertEqual(result["change"], "unknown")
        self.assertEqual(result["note"], "latest unavailable")
        self.assertEqual(error, "HTTP 500")


class TestCheckPythonDeps(unittest.TestCase):
    def test_outdated_package(self):
        payload = json.dumps(
            [{"name": "requests", "version": "2.31.0", "latest_version": "2.32.3"}])
        with mock.patch.object(checkers.subprocess, "run",
                               return_value=_Proc(stdout=payload)):
            results, status = checkers.check_python_deps("python")
        self.assertEqual(status, "ok")
        self.assertEqual(results[0]["name"], "requests")
        self.assertEqual(results[0]["change"], "minor")

    def test_pip_failure(self):
        with mock.patch.object(checkers.subprocess, "run",
                               return_value=_Proc(stderr="boom", returncode=1)):
            results, status = checkers.check_python_deps("python")
        self.assertEqual(results, [])
        self.assertIn("pip exited 1", status)


if __name__ == "__main__":
    unittest.main()
