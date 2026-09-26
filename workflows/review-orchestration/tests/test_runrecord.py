#!/usr/bin/env python3
"""Hermetic tests for the run record (runrecord.py), plus the classification of the
real ``runs/`` path.

Every run folder is created under a temporary directory, so no test writes into the
real ``runs/``. The classification tests ask git about a path that does not exist:
``git check-ignore`` answers from the ignore rules alone, which is the property
being proved, that a run record is ignored before any exists.

    python workflows/review-orchestration/tests/test_runrecord.py
"""

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent
SCRIPTS = WORKFLOW / "scripts"
PROJECT_ROOT = WORKFLOW.parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runrecord = _load("runrecord")

NOW = datetime(2026, 9, 25, 9, 5, 7, tzinfo=timezone(timedelta(hours=1)))


class RunRecordTests(unittest.TestCase):

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.runs = Path(folder.name) / "runs"

    def test_positive_run_id_form(self):
        self.assertEqual(runrecord.new_run_id(NOW, "0a1f"), "20260925-090507-0a1f")
        self.assertRegex(runrecord.new_run_id(), runrecord.RUN_ID)

    def test_positive_run_ids_do_not_repeat_within_a_second(self):
        ids = {runrecord.new_run_id(NOW) for _ in range(50)}
        self.assertGreater(len(ids), 1)

    def test_positive_create_makes_the_folder_and_its_state(self):
        run_dir = runrecord.create_run("20260925-090507-0a1f", runs_dir=self.runs, now=NOW)
        self.assertEqual(run_dir, self.runs / "20260925-090507-0a1f")
        self.assertEqual(runrecord.read_state(run_dir), {
            "run_id": "20260925-090507-0a1f",
            "created": "2026-09-25T09:05:07+01:00",
            "status": "running",
            "step": "created",
            "pause": None,
            "stop_reasons": [],
            "rounds": [],
        })
        self.assertEqual(sorted(p.name for p in run_dir.iterdir()), ["state.json"])

    def test_positive_create_without_an_id_makes_one(self):
        run_dir = runrecord.create_run(runs_dir=self.runs)
        self.assertRegex(run_dir.name, runrecord.RUN_ID)

    def test_rejection_an_existing_run_is_never_reused(self):
        runrecord.create_run("20260925-090507-0a1f", runs_dir=self.runs, now=NOW)
        with self.assertRaisesRegex(FileExistsError, "already exists"):
            runrecord.create_run("20260925-090507-0a1f", runs_dir=self.runs, now=NOW)

    def test_rejection_malformed_ids(self):
        for bad in ("latest", "20260925-090507", "20260925-090507-0A1F",
                    "../20260925-090507-0a1f", "20260925-090507-0a1f/x"):
            with self.subTest(run_id=bad):
                with self.assertRaisesRegex(ValueError, "not a run ID"):
                    runrecord.create_run(bad, runs_dir=self.runs)
        self.assertFalse(self.runs.exists())

    def test_positive_dry_run_writes_nothing(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            run_dir = runrecord.create_run("20260925-090507-0a1f", runs_dir=self.runs,
                                           dry_run=True)
        self.assertFalse(run_dir.exists())
        self.assertFalse(self.runs.exists())
        self.assertIn("[DRY RUN] would create 20260925-090507-0a1f/", err.getvalue())

    def test_positive_state_rewrites_atomically_with_lf_and_utf8(self):
        run_dir = runrecord.create_run("20260925-090507-0a1f", runs_dir=self.runs, now=NOW)
        state = runrecord.read_state(run_dir)
        state["step"] = "review R1"
        state["note"] = "café"
        runrecord.write_state(run_dir, state)
        raw = (run_dir / "state.json").read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertIn("café".encode("utf-8"), raw)
        self.assertEqual(json.loads(raw.decode("utf-8"))["step"], "review R1")
        self.assertFalse((run_dir / "state.json.tmp").exists())


class ClassificationTests(unittest.TestCase):
    """The real tree: a run record is ignored by the public repository, and the
    workflow's tracked files beside it are not."""

    def check_ignore(self, path):
        result = subprocess.run(["git", "check-ignore", "-q", path], cwd=PROJECT_ROOT,
                                capture_output=True, text=True, encoding="utf-8")
        if result.returncode not in (0, 1):
            self.skipTest(f"git check-ignore failed: {result.stderr.strip()}")
        return result.returncode == 0

    def test_positive_a_run_record_is_ignored_before_it_exists(self):
        for name in ("state.json", "state.json.tmp", "brief.md", "rounds/R1/diff.patch"):
            with self.subTest(name=name):
                self.assertTrue(self.check_ignore(
                    f"workflows/review-orchestration/runs/20260101-000000-abcd/{name}"))

    def test_negative_the_workflow_files_beside_runs_are_not_ignored(self):
        for path in ("workflows/review-orchestration/config/settings.json",
                     "workflows/review-orchestration/scripts/runrecord.py",
                     "workflows/review-orchestration/requirements.txt"):
            with self.subTest(path=path):
                self.assertFalse(self.check_ignore(path))


if __name__ == "__main__":
    unittest.main()
