#!/usr/bin/env python3
"""Tests for link-check's ``--audit --json`` output and its ``--no-log`` switch.

The review orchestrator's trusted host link check reads ``--json`` rather than the
Markdown report, so the output's exact shape is under test, the project-relative
file path included. The audit walks one fixture CONTEXT.md at
``workflows/x/CONTEXT.md`` inside a temporary project root (``PROJECT_ROOT`` and
``collect_context_files`` are pointed there), where ``workflows/audit/CONTEXT``
exists and ``workflows/no-such-workflow/CONTEXT`` does not. The log paths are
temporary files; the real LOG.md files are never touched.

    python workflows/link-check/tests/test_json_and_no_log.py
"""

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run.py"


def load_run():
    spec = importlib.util.spec_from_file_location("link_check_run_json", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["link_check_run_json"] = module
    spec.loader.exec_module(module)
    return module


run = load_run()

LIVE = "[[workflows/audit/CONTEXT]]"
DEAD = "[[workflows/no-such-workflow/CONTEXT]]"
SEED = b"[2026-01-01T00:00:00+00:00] | Actor: Biblio | Action: created | Note: seed\n"


class LinkCheckCase(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.workflow_log = self.dir / "workflow-LOG.md"
        self.root_log = self.dir / "root-LOG.md"
        for path in (self.workflow_log, self.root_log):
            path.write_bytes(SEED)
        for name, value in (("WORKFLOW_LOG", self.workflow_log),
                            ("ROOT_LOG", self.root_log)):
            patcher = mock.patch.object(run, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fixture(self, body):
        """Point the walk at one CONTEXT.md holding ``body``, inside a temporary
        project root (code review R4-2: the expected path is then a literal the
        test states, never one worked out by the helper under test). The root
        also holds ``workflows/audit/CONTEXT.md`` so the live link resolves."""
        root = self.dir / "project"
        path = root / "workflows" / "x" / "CONTEXT.md"
        path.parent.mkdir(parents=True)
        path.write_bytes(body.encode("utf-8"))
        (root / "workflows" / "audit").mkdir()
        (root / "workflows" / "audit" / "CONTEXT.md").write_bytes(b"# Audit\n")
        for name, value in (("PROJECT_ROOT", root),
                            ("collect_context_files", lambda: [path])):
            patcher = mock.patch.object(run, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def main(self, *args):
        out = io.StringIO()
        with mock.patch.object(run.sys, "argv", ["run.py", *args]), \
                redirect_stdout(out), redirect_stderr(io.StringIO()):
            run.main()
        return out.getvalue()

    def unchanged(self):
        return (self.workflow_log.read_bytes() == SEED
                and self.root_log.read_bytes() == SEED)


class JsonOutputTests(LinkCheckCase):

    def test_positive_control_one_dead_link_is_reported_exactly(self):
        self.fixture(f"# X\n\nSee {LIVE} and {DEAD}.\n")
        payload = json.loads(self.main("--audit", "--json", "--no-log"))
        self.assertEqual(payload, {"dead_links": [
            {"file": "workflows/x/CONTEXT.md",
             "target": "workflows/no-such-workflow/CONTEXT"}]})

    def test_negative_control_no_dead_link_is_an_empty_list(self):
        self.fixture(f"# X\n\nSee {LIVE}.\n")
        self.assertEqual(json.loads(self.main("--audit", "--json", "--no-log")),
                         {"dead_links": []})

    def test_negative_control_without_json_the_report_is_markdown(self):
        self.fixture(f"# X\n\nSee {DEAD}.\n")
        out = self.main("--audit", "--no-log")
        self.assertIn("# Book Dragon", out)
        with self.assertRaises(ValueError):
            json.loads(out)

    def test_rejection_control_json_is_refused_outside_audit_mode(self):
        self.fixture(f"# X\n\nSee {DEAD}.\n")
        for args in (("--fix", "--json"), ("--link", "--json"),
                     ("--audit", "--json", "--save")):
            with self.subTest(args=args):
                with self.assertRaises(SystemExit) as caught:
                    self.main(*args)
                self.assertEqual(caught.exception.code, 2)


class NoLogTests(LinkCheckCase):

    def test_positive_control_an_audit_writes_both_logs_by_default(self):
        self.fixture(f"# X\n\nSee {LIVE}.\n")
        self.main("--audit")
        self.assertIn(b"Running link-check in audit mode.", self.workflow_log.read_bytes())
        self.assertIn(b"link-check workflow ran.", self.root_log.read_bytes())

    def test_rejection_control_no_log_leaves_both_logs_byte_identical(self):
        self.fixture(f"# X\n\nSee {DEAD}.\n")
        for args, found in ((("--audit", "--no-log"), "**Dead:** 1"),
                            (("--audit", "--json", "--no-log"),
                             '"target": "workflows/no-such-workflow/CONTEXT"')):
            with self.subTest(args=args):
                out = self.main(*args)
                self.assertTrue(self.unchanged())
                # Writing nothing must not come from auditing nothing.
                self.assertIn(found, out)

    def test_positive_control_json_without_no_log_still_logs(self):
        self.fixture(f"# X\n\nSee {DEAD}.\n")
        self.main("--audit", "--json")
        self.assertIn(b"1 dead link(s) found", self.workflow_log.read_bytes())


if __name__ == "__main__":
    unittest.main()
