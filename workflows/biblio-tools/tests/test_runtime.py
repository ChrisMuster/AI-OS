#!/usr/bin/env python3
"""Unit tests for runtime.process_is_alive.

server.py's lifecycle watchdog, launch.py's parent check and the reddit reader's
stale-PID check all ask it whether a process is alive. On POSIX a killed process
stays a zombie until its parent reaps it, and `os.kill(pid, 0)` still succeeds on a
zombie, so a force-killed launcher read as alive and the server stayed up (cross-review
R1-1, 2026-11-24). A zombie now counts as dead.

Covered here, on every platform: a running child is alive; a killed child that has not
been waited for is not (on POSIX, the zombie case); a reaped child is not. On POSIX
also the `ps` path used where there is no `/proc` (macOS), by pointing `_PROC` at a
folder that does not exist: against the real `ps` where one is installed, and against
a fake `ps` everywhere, for how its answer is read.

Standard library only.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import runtime  # noqa: E402

MISSING = Path(tempfile.gettempdir()) / f"no-proc-{os.getpid()}"


def _sleeper():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


class ProcessIsAliveTests(unittest.TestCase):
    def setUp(self):
        self.child = _sleeper()
        self.addCleanup(self._reap)

    def _reap(self):
        if self.child.poll() is None:
            self.child.kill()
        self.child.wait()

    def _killed_unreaped(self):
        """Kill the child without waiting, then give the kill time to land. On POSIX
        the child is now a zombie; on Windows its exit code is set."""
        self.child.kill()
        time.sleep(1)

    def test_running_child_is_alive(self):
        self.assertTrue(runtime.process_is_alive(self.child.pid))

    def test_killed_unreaped_child_is_not_alive(self):
        self._killed_unreaped()
        self.assertFalse(runtime.process_is_alive(self.child.pid))

    def test_reaped_child_is_not_alive(self):
        self.child.kill()
        self.child.wait()
        self.assertFalse(runtime.process_is_alive(self.child.pid))

    @unittest.skipIf(sys.platform == "win32", "POSIX zombie state; Windows has none")
    @unittest.skipUnless(shutil.which("ps"), "no ps installed here (the checks image has none)")
    def test_without_proc_ps_reports_the_zombie(self):
        with mock.patch.object(runtime, "_PROC", MISSING):
            self.assertTrue(runtime.process_is_alive(self.child.pid))
            self._killed_unreaped()
            self.assertFalse(runtime.process_is_alive(self.child.pid))


@unittest.skipIf(sys.platform == "win32", "the ps fallback is POSIX only")
class PsFallbackTests(unittest.TestCase):
    """How `_is_zombie` reads `ps`, with a fake `ps` first on PATH, so it runs where
    no real `ps` is installed."""

    def _fake_ps(self, body):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        script = Path(folder.name) / "ps"
        script.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8", newline="\n")
        script.chmod(0o755)
        path = os.pathsep.join([folder.name, os.environ.get("PATH", "")])
        for patcher in (mock.patch.dict(os.environ, {"PATH": path}),
                        mock.patch.object(runtime, "_PROC", MISSING)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_z_state_is_a_zombie(self):
        self._fake_ps("echo 'Z+'")
        self.assertTrue(runtime._is_zombie(os.getpid()))

    def test_running_state_is_not(self):
        self._fake_ps("echo 'S+'")
        self.assertFalse(runtime._is_zombie(os.getpid()))

    def test_no_such_process_counts_as_gone(self):
        self._fake_ps("exit 1")
        self.assertTrue(runtime._is_zombie(os.getpid()))

    def test_no_ps_at_all_keeps_the_kill_probe_answer(self):
        with mock.patch.object(runtime, "_PROC", MISSING), \
                mock.patch.object(runtime.subprocess, "run", side_effect=FileNotFoundError):
            self.assertFalse(runtime._is_zombie(os.getpid()))


if __name__ == "__main__":
    unittest.main()
