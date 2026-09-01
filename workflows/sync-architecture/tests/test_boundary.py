#!/usr/bin/env python3
"""Tests for the boundary-completeness check.

Same three controls as the rest of this workflow's suites: a positive control (a
legal instance the check must find), a rejection control (an illegal instance it
must refuse), and a negative control (something that is not an instance at all,
so a clean result is known not to be silence from over-matching).

**The integration controls live in the module's `--self-test` and are reached
from here rather than copied.** That self-test builds real fixtures and proves
every one of the five failing conditions can fire. Re-implementing them here
would be the second copy of a rule this workflow exists to keep in one place.
`BoundarySmokeTests.test_the_self_test_passes` is what gives close-out reach to
them, because close-out discovers suites at `workflows/*/tests/test_*.py` only,
and a check that exists but is unreachable by the gate is not covered by it.

What this file adds beyond reach is the `raw/` predicate in isolation. That one
deserves unit coverage of its own because **no run against the real tree can
test it.** Every `raw/` directory in this project sits under `wikis/`, so a
segment test and a `wikis/` prefix test return the same answer on every path
that exists, and a green run distinguishes nothing. The plan settles it by
specification rather than by measurement, and these tests pin the specification.
"""
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = PROJECT_ROOT / "workflows" / "sync-architecture" / "scripts" / "boundary.py"


def load_module():
    spec = importlib.util.spec_from_file_location("boundary_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


boundary = load_module()


class UnderRawTests(unittest.TestCase):
    """The subject is 'a path sitting inside a directory named raw'.

    The invariant this backs is deliberately wider than the `wikis/**/raw/**`
    pathspec that carves raw material out of the allowlist block. That pathspec
    says which files the personal repository declines to capture today; this
    says where bulk material may EVER be claimed from, and it has to hold for a
    raw/ folder created somewhere the pathspec does not reach.
    """

    def test_positive_control_a_path_under_wikis_raw(self):
        self.assertTrue(boundary.under_raw("wikis/general-programming/raw/book.pdf"))

    def test_positive_control_an_archived_wiki_two_levels_down(self):
        # The depth case that broke a pattern once: `wikis/*/raw/**` misses this.
        self.assertTrue(boundary.under_raw("wikis/archived/basic-python/raw/x.pdf"))

    def test_positive_control_a_raw_folder_outside_wikis(self):
        # THE load-bearing case. A `wikis/` prefix test passes all of these and
        # the real tree cannot tell the two readings apart, because every raw/
        # directory in it happens to sit under wikis/.
        for path in ("bulk/raw/big.bin", "raw/thing.png",
                     "workflows/x/raw/y/z.json"):
            with self.subTest(path=path):
                self.assertTrue(boundary.under_raw(path))

    def test_negative_control_a_file_named_raw_is_not_a_folder(self):
        # A file called `raw` is not a directory of bulk material.
        self.assertFalse(boundary.under_raw("wikis/a/raw"))
        self.assertFalse(boundary.under_raw("raw"))

    def test_negative_control_a_component_merely_starting_with_raw(self):
        # The defect a substring test would introduce.
        for path in ("a/rawdata/b.md", "a/raws/b.md", "raw-notes/b.md",
                     "a/b/draft-raw/c.md"):
            with self.subTest(path=path):
                self.assertFalse(boundary.under_raw(path))

    def test_negative_control_an_extension_containing_raw(self):
        self.assertFalse(boundary.under_raw("photos/img.raw"))
        self.assertFalse(boundary.under_raw("notes/raw.md"))


class BoundarySmokeTests(unittest.TestCase):
    """The real CLI as a subprocess, from the project root."""

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "workflows/sync-architecture/scripts/boundary.py", *args],
            cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8")

    def test_the_self_test_passes(self):
        proc = self._run("--self-test")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("All self-tests passed.", proc.stdout)

    def test_rejection_control_a_missing_git_dir_is_refused(self):
        # Five of the six conditions need no git directory, so a run without one
        # would do five sixths of the check while appearing to do all of it.
        proc = self._run()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("--git-dir is required", proc.stdout)

    def test_rejection_control_a_missing_repository_is_refused(self):
        proc = self._run("--git-dir", "no/such/repository.git")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("did not run", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
