#!/usr/bin/env python3
"""Hermetic tests for backlog-guard."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run.py"


def load_run():
    spec = importlib.util.spec_from_file_location("backlog_guard_run", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run = load_run()


BACKLOG = """# Backlog

## Rules
Keep it tidy.

## Active

- **First item** - Do the first thing.
- **Second item** - Do the second thing.

## Build Only When Needed

- **Later item** - Build only when needed.

## Completed Archive

See archive.
"""


MISSING_BUILD = """# Backlog

## Active

- **First item** - Do the first thing.
"""


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


class BacklogGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.backlog = self.root / "memory" / "backlog.md"
        self.backups = self.root / "memory" / "backlog-backups"
        self.log = self.root / "workflows" / "backlog-guard" / "LOG.md"
        write_text(self.backlog, BACKLOG)

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_counts_required_sections(self):
        state = run.parse_backlog(self.backlog)
        self.assertEqual(state.missing_sections, [])
        self.assertEqual(state.counts["Active"], 2)
        self.assertEqual(state.counts["Build Only When Needed"], 1)

    def test_missing_build_only_section_fails_check(self):
        run.create_snapshot(self.backlog, self.backups, 5, "baseline", self.log)
        write_text(self.backlog, MISSING_BUILD)
        ok, messages, _current, _baseline, _path = run.check_backlog(
            self.backlog, self.backups)
        self.assertFalse(ok)
        self.assertTrue(any("missing ## Build Only When Needed" in m for m in messages))

    def test_snapshot_creates_exact_byte_copy(self):
        target, _state, _removed = run.create_snapshot(
            self.backlog, self.backups, 5, "baseline", self.log)
        self.assertEqual(target.read_bytes(), self.backlog.read_bytes())

    def test_rotation_keeps_newest_snapshots(self):
        for index in range(7):
            write_text(self.backlog, BACKLOG.replace("First item", f"First item {index}"))
            run.create_snapshot(self.backlog, self.backups, 3, f"snapshot {index}", self.log)
        snapshots = run.list_snapshots(self.backups)
        self.assertEqual(len(snapshots), 3)

    def test_unexpected_active_drop_fails(self):
        run.create_snapshot(self.backlog, self.backups, 5, "baseline", self.log)
        write_text(self.backlog, BACKLOG.replace("- **Second item** - Do the second thing.\n", ""))
        ok, messages, _current, _baseline, _path = run.check_backlog(
            self.backlog, self.backups)
        self.assertFalse(ok)
        self.assertTrue(any("Active count dropped" in m for m in messages))

    def test_allowed_active_drop_passes(self):
        run.create_snapshot(self.backlog, self.backups, 5, "baseline", self.log)
        write_text(self.backlog, BACKLOG.replace("- **Second item** - Do the second thing.\n", ""))
        ok, messages, _current, _baseline, _path = run.check_backlog(
            self.backlog, self.backups, allow_active_drop=1)
        self.assertTrue(ok, messages)

    def test_oversized_item_fails(self):
        run.create_snapshot(self.backlog, self.backups, 5, "baseline", self.log)
        large = BACKLOG.replace(
            "- **First item** - Do the first thing.",
            "- **First item** - Do the first thing.\n  "
            + "\n  ".join(f"detail {i}" for i in range(10)),
        )
        write_text(self.backlog, large)
        ok, messages, _current, _baseline, _path = run.check_backlog(
            self.backlog, self.backups)
        self.assertFalse(ok)
        self.assertTrue(any("oversized backlog item" in m for m in messages))

    def test_restore_requires_force(self):
        target, _state, _removed = run.create_snapshot(
            self.backlog, self.backups, 5, "baseline", self.log)
        with self.assertRaises(RuntimeError):
            run.restore_snapshot(target.name, self.backlog, self.backups, False, self.log)

    def test_restore_replaces_current_backlog(self):
        target, _state, _removed = run.create_snapshot(
            self.backlog, self.backups, 5, "baseline", self.log)
        write_text(self.backlog, MISSING_BUILD)
        run.restore_snapshot(target.name, self.backlog, self.backups, True, self.log)
        self.assertEqual(self.backlog.read_text(encoding="utf-8"), BACKLOG)


if __name__ == "__main__":
    unittest.main()
