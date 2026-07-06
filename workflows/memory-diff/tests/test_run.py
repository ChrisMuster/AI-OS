#!/usr/bin/env python3
"""Integration tests for the memory-diff run.py entry point.

These drive run.py as a subprocess against temporary fixtures, redirected via the
MEMORY_DIFF_* environment overrides, so the real gitignored state.json and the
real memory/LOG.md are never touched. They cover the command glue the pure-module
tests do not: JSON/text status, ack writing state and logging, the loud anomaly
paths (corrupt state, missing log, lost watermark) and their --force-baseline
reset, the display cap, and the status/ack race guard (--through).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RUN_PY = Path(__file__).resolve().parent.parent / "scripts" / "run.py"


def entry(day, action="modified", note=None):
    note = note or f"change {day}"
    return (f"[2026-07-{day:02d}T09:00:00+01:00] | Actor: Biblio | "
            f"Action: {action} | Note: {note}.")


class RunTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.mem_log = self.tmp / "mem_log.md"
        self.state = self.tmp / "state.json"
        self.wf_log = self.tmp / "wf_log.md"
        self.env = {
            "MEMORY_DIFF_MEMORY_LOG": str(self.mem_log),
            "MEMORY_DIFF_STATE": str(self.state),
            "MEMORY_DIFF_WORKFLOW_LOG": str(self.wf_log),
        }

    def tearDown(self):
        for path in (self.mem_log, self.state, self.wf_log):
            if path.exists():
                path.unlink()
        self.tmp.rmdir()

    def write_log(self, entries):
        text = "# Memory - Log\n\n" + "".join(e + "\n" for e in entries)
        self.mem_log.write_text(text, encoding="utf-8")

    def run_cli(self, *args):
        env = dict(os.environ)
        env.update(self.env)
        return subprocess.run(
            [sys.executable, str(RUN_PY), *args],
            capture_output=True, text=True, encoding="utf-8", env=env)

    def set_watermark(self, line):
        self.state.write_text(
            json.dumps({"seen_line": line}), encoding="utf-8")


class TestStatus(RunTestCase):
    def test_json_with_changes(self):
        self.write_log([entry(1, "created"), entry(2, "modified"), entry(3, "archived")])
        self.set_watermark(entry(1, "created"))
        proc = self.run_cli("--status", "--json")
        self.assertEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertFalse(payload["anomaly"])
        self.assertTrue(payload["has_changes"])
        self.assertEqual(payload["count"], 2)
        self.assertIsNotNone(payload["through"])
        self.assertIn("Updated", payload["groups"])
        self.assertIn("Archived", payload["groups"])

    def test_text_with_changes(self):
        self.write_log([entry(1, "created"), entry(2, "modified")])
        self.set_watermark(entry(1, "created"))
        proc = self.run_cli("--status")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("1 memory change(s) since last session", proc.stdout)
        self.assertIn("Updated:", proc.stdout)

    def test_no_changes(self):
        self.write_log([entry(1, "created"), entry(2, "modified")])
        self.set_watermark(entry(2, "modified"))
        proc = self.run_cli("--status")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("No memory changes since last session", proc.stdout)

    def test_first_run_baseline_message(self):
        self.write_log([entry(1, "created")])
        proc = self.run_cli("--status")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("not yet initialised", proc.stdout)

    def test_display_cap(self):
        # 20 changes after the watermark; only the cap (15) is listed, the rest
        # counted. The full count is still reported.
        entries = [entry(1, "created")] + [entry(d, "modified") for d in range(2, 22)]
        self.write_log(entries)
        self.set_watermark(entry(1, "created"))
        proc = self.run_cli("--status")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("20 memory change(s) since last session", proc.stdout)
        self.assertIn("and 5 older change(s) not shown", proc.stdout)


class TestAck(RunTestCase):
    def test_ack_writes_state_and_logs(self):
        self.write_log([entry(1, "created"), entry(2, "modified")])
        self.set_watermark(entry(1, "created"))
        proc = self.run_cli("--ack")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Acknowledged 1 memory change(s)", proc.stdout)
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(saved["seen_line"], entry(2, "modified"))
        self.assertTrue(self.wf_log.exists())
        self.assertIn("acknowledged", self.wf_log.read_text(encoding="utf-8"))

    def test_ack_already_current_no_log(self):
        self.write_log([entry(1, "created"), entry(2, "modified")])
        self.set_watermark(entry(2, "modified"))
        proc = self.run_cli("--ack")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("already current", proc.stdout)
        self.assertFalse(self.wf_log.exists())

    def test_first_run_ack_baselines_without_logging(self):
        self.write_log([entry(1, "created"), entry(2, "modified")])
        proc = self.run_cli("--ack")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("baseline established", proc.stdout)
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(saved["seen_line"], entry(2, "modified"))
        # First run surfaces nothing, so it must not write a workflow log entry.
        self.assertFalse(self.wf_log.exists())

    def test_ack_dry_run_writes_nothing(self):
        self.write_log([entry(1, "created"), entry(2, "modified")])
        self.set_watermark(entry(1, "created"))
        proc = self.run_cli("--ack", "--dry-run")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("[DRY RUN]", proc.stdout)
        # Dry run must not advance the watermark or write a log entry.
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(saved["seen_line"], entry(1, "created"))
        self.assertFalse(self.wf_log.exists())


class TestAnomalies(RunTestCase):
    def test_corrupt_state_status_and_refuse_ack(self):
        self.write_log([entry(1, "created"), entry(2, "modified")])
        self.state.write_text("{not json", encoding="utf-8")
        status = self.run_cli("--status")
        self.assertEqual(status.returncode, 2)
        self.assertIn("could not compute a reliable delta", status.stdout)
        ack = self.run_cli("--ack")
        self.assertEqual(ack.returncode, 2)
        self.assertIn("Refusing to acknowledge", ack.stdout)

    def test_corrupt_state_force_baseline_resets(self):
        self.write_log([entry(1, "created"), entry(2, "modified")])
        self.state.write_text("{not json", encoding="utf-8")
        proc = self.run_cli("--ack", "--force-baseline")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("force-baselined", proc.stdout)
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(saved["seen_line"], entry(2, "modified"))

    def test_missing_log_is_anomaly(self):
        # No memory log written at all.
        self.set_watermark(entry(1, "created"))
        proc = self.run_cli("--status")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("the memory log is missing", proc.stdout)

    def test_lost_watermark_refuses_then_force(self):
        self.write_log([entry(1, "created"), entry(2, "modified")])
        self.set_watermark("[2026-06-30T00:00:00+01:00] | Actor: Biblio | Action: created | Note: gone.")
        status = self.run_cli("--status")
        self.assertEqual(status.returncode, 2)
        self.assertIn("watermark is no longer in the memory log", status.stdout)
        refuse = self.run_cli("--ack")
        self.assertEqual(refuse.returncode, 2)
        forced = self.run_cli("--ack", "--force-baseline")
        self.assertEqual(forced.returncode, 0)
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(saved["seen_line"], entry(2, "modified"))

    def test_empty_object_state_is_anomaly(self):
        # An existing state file with no watermark ({}) must be loud, not a silent
        # re-baseline: --status warns and exits 2, --ack refuses and exits 2, and
        # the malformed state file is left untouched.
        self.write_log([entry(1, "created"), entry(2, "modified")])
        self.state.write_text("{}", encoding="utf-8")
        status = self.run_cli("--status")
        self.assertEqual(status.returncode, 2)
        self.assertIn("the saved state is malformed", status.stdout)
        ack = self.run_cli("--ack")
        self.assertEqual(ack.returncode, 2)
        self.assertIn("Refusing to acknowledge", ack.stdout)
        # The refused ack must not have rewritten the malformed state.
        self.assertEqual(self.state.read_text(encoding="utf-8"), "{}")

    def test_empty_object_state_json_flags_anomaly(self):
        # The JSON status path the AGENTS.md startup step reads must also flag it.
        self.write_log([entry(1, "created"), entry(2, "modified")])
        self.state.write_text("{}", encoding="utf-8")
        proc = self.run_cli("--status", "--json")
        self.assertEqual(proc.returncode, 2)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "state_malformed")
        self.assertTrue(payload["anomaly"])
        self.assertFalse(payload["baseline"])

    def test_empty_object_state_force_baseline_resets(self):
        # An explicit reset past the malformed-state anomaly baselines to latest,
        # matching the corrupt-state reset behaviour.
        self.write_log([entry(1, "created"), entry(2, "modified")])
        self.state.write_text("{}", encoding="utf-8")
        proc = self.run_cli("--ack", "--force-baseline")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("force-baselined", proc.stdout)
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(saved["seen_line"], entry(2, "modified"))


class TestRaceGuard(RunTestCase):
    def test_through_mismatch_refuses(self):
        self.write_log([entry(1, "created"), entry(2, "modified")])
        self.set_watermark(entry(1, "created"))
        payload = json.loads(self.run_cli("--status", "--json").stdout)
        token = payload["through"]
        # The log moves on: a new entry is appended before ack.
        self.write_log([entry(1, "created"), entry(2, "modified"), entry(3, "archived")])
        proc = self.run_cli("--ack", "--through", token)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("changed since the status check", proc.stdout)
        # The refused ack must not have advanced the watermark.
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(saved["seen_line"], entry(1, "created"))

    def test_through_match_advances(self):
        self.write_log([entry(1, "created"), entry(2, "modified")])
        self.set_watermark(entry(1, "created"))
        payload = json.loads(self.run_cli("--status", "--json").stdout)
        token = payload["through"]
        proc = self.run_cli("--ack", "--through", token)
        self.assertEqual(proc.returncode, 0)
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(saved["seen_line"], entry(2, "modified"))


if __name__ == "__main__":
    unittest.main()
