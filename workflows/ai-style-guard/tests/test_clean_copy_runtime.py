#!/usr/bin/env python3
"""Tests for the AI-style guard's runtime hand-off in the clean copy (orchestrator
isolation S3, decision 23).

The guard hands off to the project's .venv before checking. The review
orchestrator's offline container runs a copy of the project with no .venv, and its
own interpreter (built from the .venv versions) is the runtime there, so with
``BOOK_DRAGON_CLEAN_COPY=1`` (only ``1``) the hand-off is skipped. A stand-in
``runtime`` module records whether it was asked; the check itself is a stub.

    python workflows/ai-style-guard/tests/test_clean_copy_runtime.py
"""

import io
import os
import sys
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run  # noqa: E402


class CleanCopyRuntimeTests(unittest.TestCase):

    def main(self, marker):
        handoff = mock.Mock()
        fake = types.ModuleType("runtime")
        fake.ensure_project_runtime = handoff
        with mock.patch.dict(sys.modules, {"runtime": fake}), \
                mock.patch.dict(os.environ), \
                mock.patch.object(run, "run_check", return_value=[]) as check, \
                mock.patch.object(run.sys, "argv", ["run.py", "--check", "--json"]), \
                redirect_stdout(io.StringIO()):
            os.environ.pop("BOOK_DRAGON_CLEAN_COPY", None)
            if marker is not None:
                os.environ["BOOK_DRAGON_CLEAN_COPY"] = marker
            try:
                run.main()
            except SystemExit:
                pass
        return handoff.call_count, check.call_count

    def test_positive_with_the_marker_the_check_runs_without_the_handoff(self):
        self.assertEqual(self.main("1"), (0, 1))

    def test_rejection_without_the_marker_the_handoff_is_made(self):
        self.assertEqual(self.main(None), (1, 1))

    def test_rejection_only_1_is_the_marker(self):
        for value in ("0", "true", ""):
            with self.subTest(value=value):
                self.assertEqual(self.main(value), (1, 1))


if __name__ == "__main__":
    unittest.main()
