#!/usr/bin/env python3
"""Unit tests for verify.py's Codex MCP registry check.

On 2026-11-23 Codex was reinstalled as a Volta `codex.cmd` shim. The check ran
the bare name `codex`, which Windows `subprocess` resolves only to `codex.exe`,
so it failed with WinError 2 although Codex was installed and working. The check
now runs the path `shutil.which` resolved, which honours PATHEXT.

Covered here:
- the command the check runs is the resolved path, not the bare name;
- a missing Codex is a WARN and runs nothing;
- end to end against a fake Codex on PATH, run as a real subprocess: a `.cmd`
  shim on Windows (the case that failed), an executable script elsewhere.

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

ENABLED = "biblio_tools\n  enabled: true\n"


class RegistryCommandTests(unittest.TestCase):
    def test_runs_the_resolved_path_not_the_bare_name(self):
        resolved = str(Path("C:/tools/codex.cmd"))
        done = subprocess.CompletedProcess([], 0, stdout=ENABLED, stderr="")
        with mock.patch.object(verify.shutil, "which", return_value=resolved), \
                mock.patch.object(verify.subprocess, "run", return_value=done) as run:
            result = verify.check_codex_mcp_registry()
        self.assertEqual(run.call_args.args[0], [resolved, "mcp", "get", "biblio_tools"])
        self.assertEqual(result["status"], "PASS")

    def test_missing_codex_warns_and_runs_nothing(self):
        with mock.patch.object(verify.shutil, "which", return_value=None), \
                mock.patch.object(verify.subprocess, "run") as run:
            result = verify.check_codex_mcp_registry()
        self.assertEqual(result["status"], "WARN")
        run.assert_not_called()


class RegistryShimTests(unittest.TestCase):
    """A fake Codex on PATH, run for real, in the shape that failed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        folder = Path(self.tmp.name)
        if os.name == "nt":
            shim = folder / "codex.cmd"
            shim.write_text("@echo off\r\necho biblio_tools\r\necho   enabled: true\r\n",
                            encoding="utf-8", newline="")
        else:
            shim = folder / "codex"
            shim.write_text("#!/bin/sh\nprintf 'biblio_tools\\n  enabled: true\\n'\n",
                            encoding="utf-8", newline="\n")
            shim.chmod(0o755)
        path = os.pathsep.join([str(folder), os.environ.get("PATH", "")])
        patched = mock.patch.dict(os.environ, {"PATH": path, "CODEX_HOME": str(folder)})
        patched.start()
        self.addCleanup(patched.stop)

    def test_a_shim_on_path_passes(self):
        result = verify.check_codex_mcp_registry()
        self.assertEqual(result["status"], "PASS", result["detail"])


if __name__ == "__main__":
    unittest.main()
