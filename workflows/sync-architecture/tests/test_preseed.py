#!/usr/bin/env python3
"""Tests for the pre-seed gate.

Same three controls as the rest of this workflow's suites, stated against the
subject each check is about:

    positive control  a LEGAL instance, which the check must find (for a counter)
                      or pass silently (for a validator).
    rejection control an ILLEGAL instance, which the check must refuse. It proves
                      the refusal path fires and says nothing about whether the
                      definition is right.
    negative control  something that is NOT AN INSTANCE at all, so a clean result
                      is known not to be silence from over-matching.

**The integration controls live in the module's own `--self-test` and are reached
from here by `PreseedSmokeTests`, deliberately rather than being copied.** That
self-test builds two real repositories and asserts every blocking part fires on a
dirty tree and stays silent on a clean one. Re-implementing those assertions here
would be a second copy of a rule this workflow exists to keep in one place, and
the second copy is the thing that drifts. What this file adds is coverage the
self-test cannot give: the two predicates isolated from git, where a fixture
would otherwise decide the answer.

The reason the self-test is reached at all is a lesson from this workflow's own
history. `allowlist.py` shipped with a `--self-test` that close-out never ran,
because close-out discovers suites at `workflows/*/tests/test_*.py` only. A check
that exists and is unreachable by the gate is not covered by it.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = PROJECT_ROOT / "workflows" / "sync-architecture" / "scripts" / "preseed.py"


def load_module():
    spec = importlib.util.spec_from_file_location("preseed_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preseed = load_module()


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


# --------------------------------------------------------------- the .env rule

class EnvPredicateTests(unittest.TestCase):
    """The subject is 'a path that is the secrets file'.

    This predicate is the one declared case-folded comparison in the whole
    design, and it is deliberately broader than the plan's `category-b` block,
    which is a bare `.env` and therefore names the root file alone. Both
    properties are asserted here rather than left to be inferred, because the
    two readings differ only on a file that does not exist yet, which means no
    run against the real tree can tell them apart.
    """

    def test_positive_control_the_root_secrets_file(self):
        self.assertTrue(preseed.is_env_path(".env"))

    def test_positive_control_a_secrets_file_in_a_subdirectory(self):
        # The narrow reading of the category-b block would miss both of these.
        self.assertTrue(preseed.is_env_path("sub/.env"))
        self.assertTrue(preseed.is_env_path("a/b/c/.env"))

    def test_positive_control_case_variants_are_caught(self):
        # The filesystems this runs on are case-insensitive, so all of these are
        # the same file to the operating system as `.env`.
        for path in (".ENV", ".Env", "sub/.ENV", "SUB/.Env"):
            with self.subTest(path=path):
                self.assertTrue(preseed.is_env_path(path))

    def test_negative_control_a_name_merely_ending_in_env(self):
        # The defect a naive endswith(".env") would introduce. These are ordinary
        # files and blocking a build over one would be a false positive that
        # trains people to route around the gate.
        for path in ("foo.env", "config/prod.env", "notes/my.env"):
            with self.subTest(path=path):
                self.assertFalse(preseed.is_env_path(path))

    def test_negative_control_neighbouring_names(self):
        for path in (".envrc", ".env.example", "sub/.environment", "env"):
            with self.subTest(path=path):
                self.assertFalse(preseed.is_env_path(path))

    def test_counting_control_violations_are_reported_sorted_and_deduped(self):
        found = preseed.env_violations(
            ["notes/a.md", "sub/.env", ".env", "readme.md", "deep/.ENV"])
        self.assertEqual(found, [".env", "deep/.ENV", "sub/.env"])

    def test_negative_control_a_clean_selection_yields_nothing(self):
        self.assertEqual(preseed.env_violations(["a.md", "b/c.png", "d.env"]), [])


# ------------------------------------------------------------------ the size flag

class OversizedTests(unittest.TestCase):
    """The subject is 'a selected file larger than the threshold'.

    The limit is an argument so the controls can prove the predicate fires
    without writing 25 MiB to disk. That makes a separate assertion on the real
    constant necessary rather than optional: without it, nothing here would fail
    if the shipped threshold were changed.
    """

    def test_positive_control_a_file_over_the_limit_is_found(self):
        with tempfile.TemporaryDirectory(prefix="preseed-size-") as tmp:
            root = Path(tmp)
            _write(root / "big.md", "x" * 50)
            hits = preseed.oversized(["big.md"], project=root, limit=10)
            self.assertEqual([p for p, _ in hits], ["big.md"])
            self.assertEqual(hits[0][1], 50)

    def test_negative_control_a_file_exactly_at_the_limit_is_not_flagged(self):
        # The rule is "exceeds", so equality must not fire. An off-by-one here
        # would make the flag noisier than the threshold it documents.
        with tempfile.TemporaryDirectory(prefix="preseed-size-") as tmp:
            root = Path(tmp)
            _write(root / "exact.md", "x" * 10)
            self.assertEqual(preseed.oversized(["exact.md"], project=root, limit=10), [])

    def test_counting_control_hits_are_ordered_largest_first(self):
        with tempfile.TemporaryDirectory(prefix="preseed-size-") as tmp:
            root = Path(tmp)
            _write(root / "medium.md", "x" * 30)
            _write(root / "huge.md", "x" * 90)
            _write(root / "small.md", "x")
            hits = preseed.oversized(["medium.md", "huge.md", "small.md"],
                                     project=root, limit=10)
            self.assertEqual([p for p, _ in hits], ["huge.md", "medium.md"])

    def test_rejection_control_a_vanished_path_does_not_raise(self):
        # This is a flag, not a gate. A path that has gone since the selection
        # was computed must not be the thing that stops a build.
        with tempfile.TemporaryDirectory(prefix="preseed-size-") as tmp:
            self.assertEqual(
                preseed.oversized(["gone.md"], project=Path(tmp), limit=10), [])

    def test_negative_control_a_directory_is_not_a_file(self):
        with tempfile.TemporaryDirectory(prefix="preseed-size-") as tmp:
            root = Path(tmp)
            (root / "adir").mkdir()
            self.assertEqual(preseed.oversized(["adir"], project=root, limit=1), [])

    def test_the_shipped_threshold_is_25_mib(self):
        self.assertEqual(preseed.SIZE_LIMIT, 26_214_400)
        self.assertEqual(preseed.SIZE_LIMIT, 25 * 1024 * 1024)


# ----------------------------------------------------------------------- the CLI

class PreseedSmokeTests(unittest.TestCase):
    """The real CLI as a subprocess, from the project root.

    `test_the_self_test_passes` is the load-bearing one: it is how close-out
    reaches the integration controls that prove each blocking part can fire.
    """

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "workflows/sync-architecture/scripts/preseed.py", *args],
            cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8")

    def test_the_self_test_passes(self):
        proc = self._run("--self-test")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("All self-tests passed.", proc.stdout)

    def test_rejection_control_a_missing_repository_is_refused(self):
        # An unanswerable question must block. A gate that treats "I could not
        # tell" as a pass is not a gate.
        proc = self._run("--git-dir", "no/such/repository.git")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("did not run", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
