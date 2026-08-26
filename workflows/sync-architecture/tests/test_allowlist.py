#!/usr/bin/env python3
"""Tests for the sync architecture's shared selection module.

Every check carries the three controls the project's verification protocol
requires, stated against **the subject** (the thing the check is about):

    positive control  a LEGAL instance of the subject, which the check must find
                      (for a counter) or pass silently (for a validator).
    rejection control an ILLEGAL instance, which the check must refuse. It proves
                      the refusal path fires and says nothing about whether the
                      definition is right.
    negative control  something that is NOT AN INSTANCE at all, so a clean result
                      is known not to be silence from over-matching.

**These tests are not hermetic, and that is deliberate.** doc-verify's suite can
be, because it processes text. This module's entire subject is the difference
between two git contexts, so a test that stubbed git would be testing the stub.
Every fixture is a real repository built in a temporary directory: deterministic,
no network, nothing written outside `tempfile`, and the working directory is
restored even when a test fails.

**The load-bearing control is `test_positive_control_shadow_context_exposes_it`.**
The defect this module was written to remove is a check that ran in a different
git context from the build it was checking, and the symptom was an assertion that
could not fail. A suite run only against a clean tree cannot tell "the two
contexts agree" from "I am not really asking two different questions". The
fixture therefore constructs the one state where a correct implementation must
return two different answers: a file that is both publicly tracked and matched by
an ignore rule.
"""
import contextlib
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = PROJECT_ROOT / "workflows" / "sync-architecture" / "scripts" / "allowlist.py"


def load_module():
    spec = importlib.util.spec_from_file_location("allowlist_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


allowlist = load_module()


def _git(cwd, *args):
    subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True,
                   text=True, encoding="utf-8", check=False)


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


@contextlib.contextmanager
def fixture(ignore_rules="notes/*\n", force_add=("notes/oops.md",),
            plain_add=("readme.md",), files=("notes/private.md", "notes/oops.md",
                                             "readme.md")):
    """Build a real repository and chdir into it, restoring the cwd afterwards.

    Defaults produce the three states that matter:
      readme.md         tracked, not ignored      (a normal public file)
      notes/private.md  untracked, ignored        (a normal private file)
      notes/oops.md     tracked AND ignored       (the state under test)
    """
    original = os.getcwd()
    tmp = tempfile.TemporaryDirectory(prefix="sync-arch-test-")
    try:
        root = os.path.join(tmp.name, "proj")
        os.makedirs(root)
        for rel in files:
            full = os.path.join(root, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            _write(full, "content\n")
        _write(os.path.join(root, ".gitignore"), ignore_rules)
        os.chdir(root)
        _git(root, "init", "-q", ".")
        for rel in plain_add:
            _git(root, "add", rel)
        for rel in force_add:
            _git(root, "add", "-f", rel)
        _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed")
        yield root
    finally:
        os.chdir(original)
        tmp.cleanup()


# --------------------------------------------------------------------------- block

class ExtractBlockTests(unittest.TestCase):
    """Counting checker. Subject: a pathspec inside the plan's fenced block."""

    def test_positive_control_pathspecs_are_returned(self):
        text = "intro\n\n```allowlist\n:(glob)a/**\nb/c.md\n```\n\nafter\n"
        self.assertEqual(allowlist.extract_block(text), [":(glob)a/**", "b/c.md"])

    def test_positive_control_comments_and_blanks_are_stripped(self):
        text = "```allowlist\n# a comment\n\n:(glob)a/**\n\n# another\n```\n"
        self.assertEqual(allowlist.extract_block(text), [":(glob)a/**"])

    def test_rejection_control_no_block_returns_none(self):
        self.assertIsNone(allowlist.extract_block("no fenced block here at all\n"))

    def test_rejection_control_unterminated_block_returns_none(self):
        self.assertIsNone(allowlist.extract_block("```allowlist\n:(glob)a/**\n"))

    def test_rejection_control_empty_block_returns_none(self):
        """An empty block is refused rather than returning [], because a check
        resolving zero pathspecs would report an empty selection as clean."""
        self.assertIsNone(allowlist.extract_block("```allowlist\n# only comments\n```\n"))

    def test_negative_control_a_different_fence_is_not_the_block(self):
        text = "```python\n:(glob)a/**\n```\n"
        self.assertIsNone(allowlist.extract_block(text))


# ----------------------------------------------------------------- the invariant

class TrackedAndIgnoredTests(unittest.TestCase):
    """Counting checker. Subject: a file both publicly tracked and ignored.

    This is the invariant the whole design rests on. Its positive control is also
    a regression control on the `--no-index` flag: without it git refuses to
    report a tracked path as ignored, so the function would return an empty set
    for a tree in any state, which is how the first hand-run of this measurement
    produced a correct answer from an instrument incapable of producing another.
    """

    def test_positive_control_the_odd_file_is_found(self):
        with fixture():
            self.assertEqual(allowlist.tracked_and_ignored(), {"notes/oops.md"})

    def test_negative_control_a_normal_tracked_file_is_not_found(self):
        with fixture(force_add=()):
            self.assertEqual(allowlist.tracked_and_ignored(), set())

    def test_negative_control_a_normal_ignored_file_is_not_found(self):
        """notes/private.md is ignored but untracked, so it is not an instance."""
        with fixture(force_add=()):
            self.assertNotIn("notes/private.md", allowlist.tracked_and_ignored())

    def test_negative_control_a_repository_with_no_ignore_rules(self):
        with fixture(ignore_rules="\n", force_add=()):
            self.assertEqual(allowlist.tracked_and_ignored(), set())


# -------------------------------------------------------------------- the contexts

class ShadowSelectionTests(unittest.TestCase):
    """Counting checker. Subject: a file the personal repository would stage.

    The two contexts must disagree about exactly one file, and that disagreement
    is the reason this module exists.
    """

    def test_negative_control_public_context_hides_the_tracked_file(self):
        with fixture():
            out = subprocess.run(
                ["git", "ls-files", "--others", "--ignored",
                 "--exclude-standard", "-z", "--", "notes/*"],
                capture_output=True, text=True, encoding="utf-8")
            public = {p for p in out.stdout.split("\0") if p}
            self.assertNotIn("notes/oops.md", public)
            self.assertIn("notes/private.md", public)

    def test_positive_control_shadow_context_exposes_it(self):
        """The load-bearing control: the contexts really do return different sets."""
        with fixture():
            raw = allowlist.shadow_selection(["notes/*"])
            self.assertIsNotNone(raw)
            self.assertIn("notes/oops.md", raw)
            self.assertIn("notes/private.md", raw)

    def test_negative_control_a_file_matching_no_pathspec_is_not_selected(self):
        with fixture():
            raw = allowlist.shadow_selection(["nothing-matches-this/*"])
            self.assertEqual(raw, set())

    def test_negative_control_an_unignored_file_is_not_selected(self):
        """readme.md is untracked by the personal repo but matches no ignore rule,
        so --ignored excludes it. Selection is not simply 'everything'."""
        with fixture(plain_add=()):
            raw = allowlist.shadow_selection(["readme.md"])
            self.assertEqual(raw, set())


# ------------------------------------------------------------------------ select

class SelectTests(unittest.TestCase):
    """Validating composite. Subject: the corrected set the build stages."""

    def test_positive_control_the_corrected_set_excludes_the_overlap(self):
        with fixture():
            out = allowlist.select(["notes/*"])
            self.assertEqual(out["final"], {"notes/private.md"})

    def test_rejection_control_the_overlap_is_reported_not_swallowed(self):
        with fixture():
            out = allowlist.select(["notes/*"])
            self.assertEqual(out["overlap"], {"notes/oops.md"})

    def test_positive_control_subtraction_is_what_removes_it(self):
        """Pins the mechanism rather than the outcome: the correction is the
        subtraction, not a property of how the pathspecs resolve."""
        with fixture():
            out = allowlist.select(["notes/*"])
            self.assertEqual(out["final"], out["raw"] - out["tracked"])
            self.assertIn("notes/oops.md", out["raw"])

    def test_negative_control_a_clean_tree_has_no_overlap(self):
        with fixture(force_add=()):
            out = allowlist.select(["notes/*"])
            self.assertEqual(out["overlap"], set())
            self.assertEqual(out["final"], out["raw"])


# ------------------------------------------------------------------------ staging

class StageTests(unittest.TestCase):
    """Writing path. Subject: the personal repository's index after staging."""

    def _personal(self, root):
        gd = os.path.join(os.path.dirname(root), "personal.git")
        _git(root, "init", "--bare", "-q", gd)
        return gd

    def _tracked_by(self, gd, root):
        out = subprocess.run(
            ["git", "--git-dir", gd, "--work-tree", root, "ls-files", "-z"],
            capture_output=True, text=True, encoding="utf-8")
        return {p for p in out.stdout.split("\0") if p}

    def test_positive_control_selected_files_become_tracked(self):
        with fixture() as root:
            gd = self._personal(root)
            self.assertTrue(allowlist.stage({"notes/private.md"}, gd))
            self.assertIn("notes/private.md", self._tracked_by(gd, root))

    def test_positive_control_a_bare_probe_repository_accepts_the_add(self):
        """Pins the property the verifier depends on: `--work-tree` overrides
        `core.bare`, so the throwaway probe repository the check builds can be
        staged into without special handling. Asserted because it was briefly
        believed to be false and worked around; the workaround fixed nothing and
        was removed."""
        with fixture() as root:
            gd = self._personal(root)
            self.assertEqual(
                subprocess.run(["git", "--git-dir", gd, "config", "core.bare"],
                               capture_output=True, text=True,
                               encoding="utf-8").stdout.strip(), "true")
            self.assertTrue(allowlist.stage({"notes/private.md"}, gd))
            self.assertIn("notes/private.md", self._tracked_by(gd, root))

    def test_positive_control_a_multi_batch_stage_adds_every_file(self):
        """The batching exists for the Windows command-line length limit, so the
        boundary is exercised rather than assumed: the real selection is several
        hundred files and every test above stages one."""
        with fixture() as root:
            gd = self._personal(root)
            os.makedirs(os.path.join(root, "notes", "many"), exist_ok=True)
            wanted = set()
            for i in range(40):
                rel = f"notes/many/f{i:03d}.md"
                _write(os.path.join(root, rel), "x\n")
                wanted.add(rel)
            original = allowlist._CHUNK_CHARS
            try:
                allowlist._CHUNK_CHARS = 60  # force many batches
                self.assertTrue(allowlist.stage(wanted, gd))
            finally:
                allowlist._CHUNK_CHARS = original
            self.assertEqual(self._tracked_by(gd, root) & wanted, wanted)

    def test_rejection_control_dry_run_writes_nothing(self):
        with fixture() as root:
            gd = self._personal(root)
            self.assertTrue(allowlist.stage({"notes/private.md"}, gd, dry_run=True))
            self.assertEqual(self._tracked_by(gd, root), set())

    def test_rejection_control_a_failed_add_returns_false_and_says_why(self):
        """A refused force-add used to return False having discarded git's
        stderr, so it printed nothing and read as a successful no-op. The
        message is asserted, not just the return value."""
        import io
        with fixture() as root:
            missing = os.path.join(os.path.dirname(root), "absent.git")
            captured = io.StringIO()
            with contextlib.redirect_stderr(captured):
                ok = allowlist.stage({"notes/private.md"}, missing)
            self.assertFalse(ok)
            self.assertIn("git refused the force-add", captured.getvalue())
            self.assertTrue(captured.getvalue().strip().endswith("'") or
                            "repository" in captured.getvalue())

    def test_positive_control_staging_is_idempotent(self):
        with fixture() as root:
            gd = self._personal(root)
            allowlist.stage({"notes/private.md"}, gd)
            self.assertTrue(allowlist.stage({"notes/private.md"}, gd))
            self.assertEqual(self._tracked_by(gd, root), {"notes/private.md"})

    def test_negative_control_the_public_working_tree_is_untouched(self):
        with fixture() as root:
            gd = self._personal(root)
            allowlist.stage({"notes/private.md"}, gd)
            out = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                                 capture_output=True, text=True, encoding="utf-8")
            self.assertNotIn("notes/private.md", out.stdout)


# -------------------------------------------------------------------------- smoke

class SmokeTests(unittest.TestCase):
    """The real CLI as a subprocess, run from the project root."""

    def _run(self, *args):
        return subprocess.run([sys.executable, str(MODULE_PATH)] + list(args),
                              cwd=str(PROJECT_ROOT), capture_output=True,
                              text=True, encoding="utf-8")

    def test_positive_control_self_test_passes(self):
        result = self._run("--self-test")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("All self-tests passed", result.stdout)

    def test_positive_control_list_returns_a_selection(self):
        result = self._run("--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("file(s)", result.stderr)

    def test_rejection_control_stage_without_a_git_dir_is_refused(self):
        result = self._run("--stage")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--git-dir", result.stdout)

    def test_rejection_control_a_missing_repository_is_named(self):
        result = self._run("--stage", "--git-dir", "no/such/repo.git", "--dry-run")
        self.assertEqual(result.returncode, 1)
        self.assertIn("No repository at", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
