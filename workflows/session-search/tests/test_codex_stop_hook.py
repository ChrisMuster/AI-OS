#!/usr/bin/env python3
"""Tests for index.py's --codex-stop-hook mode.

Codex reports a Stop hook that exits 0 with anything but JSON on stdout as failed,
which this project's did in every Codex session until 2026-10-05: the indexer
prints progress text. In this mode stdout carries only an empty JSON object and
the progress text, the archiver's included, goes to stderr. The tests run the
script as a subprocess with --dry-run, so nothing is archived or indexed, and
they pass whether or not this machine has sessions to archive.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

INDEX_PY = Path(__file__).resolve().parent.parent / "scripts" / "index.py"


def _run(*extra):
    return subprocess.run([sys.executable, str(INDEX_PY), "--dry-run", *extra],
                          capture_output=True, text=True, encoding="utf-8",
                          timeout=300)


class TestCodexStopHook(unittest.TestCase):
    def test_positive_stdout_is_only_an_empty_json_object(self):
        done = _run("--codex-stop-hook")
        self.assertEqual(done.returncode, 0, done.stderr[-500:])
        self.assertEqual(json.loads(done.stdout), {})
        self.assertEqual(done.stdout.strip(), "{}")

    def test_the_progress_text_goes_to_stderr(self):
        # The archiver runs as its own process; its lines must not reach stdout.
        done = _run("--codex-stop-hook")
        self.assertIn("Indexing", done.stderr)
        self.assertNotIn("Indexing", done.stdout)

    def test_negative_without_the_flag_stdout_is_the_usual_text(self):
        done = _run()
        self.assertEqual(done.returncode, 0, done.stderr[-500:])
        self.assertIn("Indexing", done.stdout)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(done.stdout)


class TestCodexStopHookArchiveFailure(unittest.TestCase):
    """A failed archive.py is reported as a failed hook (exit non-zero, nothing on
    stdout), not as a success; an ordinary run still carries on and indexes."""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("index_under_test", INDEX_PY)
        self.index = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.index)

    def run_main(self, argv, archive_code):
        import contextlib
        import io
        from unittest import mock
        out, err = io.StringIO(), io.StringIO()
        failed = subprocess.CompletedProcess(["archive.py"], archive_code)
        with mock.patch.object(self.index.subprocess, "run", return_value=failed), \
                mock.patch.object(sys, "argv", ["index.py", *argv]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                self.index.main()
                code = 0
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_rejection_a_failed_archive_fails_the_hook(self):
        code, out, err = self.run_main(["--codex-stop-hook", "--dry-run"], 2)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("archive.py failed (exit 2)", err)

    def test_positive_a_clean_archive_gives_the_empty_object(self):
        code, out, _err = self.run_main(["--codex-stop-hook", "--dry-run"], 0)
        self.assertEqual((code, out.strip()), (0, "{}"))

    def test_negative_an_ordinary_run_warns_and_carries_on(self):
        code, out, _err = self.run_main(["--dry-run"], 2)
        self.assertEqual(code, 0)
        self.assertIn("archive.py exited 2", out)
        self.assertIn("Done.", out)


if __name__ == "__main__":
    unittest.main()
