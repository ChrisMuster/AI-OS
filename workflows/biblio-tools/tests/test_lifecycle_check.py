#!/usr/bin/env python3
"""Tests for lifecycle_check.py's Codex plugin check, `_plugin_launcher_death`.

The check exists to catch a server that survives its launcher. Until cross-review
R4-1 (2026-11-24) that very case hung the check: a reader thread stayed blocked on a
pipe the surviving process still held, and closing that buffered stream waited for
the reader. Every wait is now bounded and the stream is closed only once no reader is
left on it.

Each test runs the real function against a fake plugin launcher (a short Python
program, run as a real subprocess) with the waits shortened, and asserts both the
verdict and that it came back within a bound:
- a launcher whose death closes the pipe: PASS;
- a server that ignores the launcher's death but exits when its stdin closes:
  FAIL, reported, the clean-up then ending it;
- a server that ignores both (the R4-1 case): FAIL within the bound, not a hang.
  The test kills that server itself afterwards.

Standard library only.
"""

import contextlib
import gc
import io
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import warnings
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lifecycle_check  # noqa: E402

ANSWER = 'import json; print(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}), flush=True)'

# The launcher answers the initialize itself and then waits to be killed.
LAUNCHER_ONLY = ANSWER + "\nimport time; time.sleep(120)\n"

# The launcher starts a "server" that inherits its stdout (so holds the pipe) and
# writes its PID to argv[1]; SERVER_STDIN says what the server does with stdin.
WITH_SERVER = """
import subprocess, sys, time
server = subprocess.Popen([sys.executable, "-c", SERVER], stdin=STDIN)
open(sys.argv[1], "w", encoding="utf-8", newline="\\n").write(str(server.pid))
""" + ANSWER + "\ntime.sleep(120)\n"

ENDS_ON_STDIN_CLOSE = ('SERVER = "import sys; sys.stdin.buffer.read()"\n'
                       "STDIN = None\n")
IGNORES_STDIN = ('SERVER = "import time; time.sleep(120)"\n'
                 "STDIN = subprocess.DEVNULL\n")

SHORT = {"PLUGIN_ANSWER_WAIT": 20, "PLUGIN_EXIT_WAIT": 3, "PLUGIN_STDIN_WAIT": 3}
BOUND = 20  # seconds: well over the shortened waits, far under a hang


class PluginLauncherDeathTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.pid_file = self.folder / "server.pid"
        for name, value in SHORT.items():
            patcher = mock.patch.object(lifecycle_check, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self._kill_server)

    def _kill_server(self):
        if self.pid_file.exists():
            try:
                os.kill(int(self.pid_file.read_text(encoding="utf-8")), signal.SIGTERM)
            except (OSError, ValueError):
                pass

    def _run(self, program):
        with open(self.folder / "stderr.txt", "w+", encoding="utf-8", newline="\n") as err:
            started = time.monotonic()
            verdict = lifecycle_check._plugin_launcher_death(
                [sys.executable, "-c", program, str(self.pid_file)],
                self.folder, dict(os.environ), err)
            return verdict, time.monotonic() - started

    def test_launcher_whose_death_closes_the_pipe_passes(self):
        (ok, detail), took = self._run(LAUNCHER_ONLY)
        self.assertTrue(ok, detail)
        self.assertLess(took, BOUND)

    def test_server_that_survives_the_launcher_fails(self):
        (ok, detail), took = self._run("import subprocess\n" + ENDS_ON_STDIN_CLOSE + WITH_SERVER)
        self.assertFalse(ok)
        self.assertIn("survived", detail)
        self.assertLess(took, BOUND)

    def test_server_holding_the_pipe_fails_within_the_bound(self):
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            (ok, detail), took = self._run("import subprocess\n" + IGNORES_STDIN + WITH_SERVER)
        self.assertFalse(ok)
        self.assertIn("survived", detail)
        self.assertLess(took, BOUND)
        self.assertIn("may be left running", said.getvalue())
        # The check leaves the held stream to its reader on purpose; once the server is
        # killed, collect it here so its ResourceWarning does not land in another test.
        self._kill_server()
        time.sleep(1)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            gc.collect()


if __name__ == "__main__":
    unittest.main()
