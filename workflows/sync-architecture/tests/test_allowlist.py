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


# ------------------------------------------------------------- tracked by both

class TrackedByBothTests(unittest.TestCase):
    """Counting checker. Subject: a path carried in both repositories' indexes.

    This answers the Stage A verify line, which asks about the state *after* the
    seed commit. It is deliberately a different question from the one SelectTests
    covers: that one asks whether the pending selection contains a publicly
    tracked file, and is answerable while the personal index is still empty.

    The rejection control is the load-bearing one. This check reports a problem by
    returning a non-empty set, so its clean answer and its could-not-run answer
    look identical from the call site unless the second is distinguishable. An
    unanswered question returning an empty set would be a check that passes
    loudest exactly when it is broken.
    """

    def _personal(self, root):
        gd = os.path.join(os.path.dirname(root), "personal.git")
        _git(root, "init", "--bare", "-q", gd)
        return gd

    def test_positive_control_a_file_in_both_indexes_is_found(self):
        """notes/oops.md is tracked publicly. Staging it into the personal
        repository puts one path in both indexes, which is the whole subject."""
        with fixture() as root:
            gd = self._personal(root)
            self.assertTrue(allowlist.stage({"notes/oops.md"}, gd))
            self.assertEqual(allowlist.tracked_by_both(gd), {"notes/oops.md"})

    def test_negative_control_a_personal_only_file_is_not_found(self):
        """The normal, correct case: the personal repository carries a private
        file the public repository has never heard of."""
        with fixture() as root:
            gd = self._personal(root)
            self.assertTrue(allowlist.stage({"notes/private.md"}, gd))
            self.assertEqual(allowlist.tracked_by_both(gd), set())

    def test_negative_control_a_public_only_file_is_not_found(self):
        with fixture() as root:
            gd = self._personal(root)
            self.assertTrue(allowlist.stage({"notes/private.md"}, gd))
            self.assertNotIn("readme.md", allowlist.tracked_by_both(gd))

    def test_negative_control_an_empty_personal_index_is_clean(self):
        with fixture() as root:
            gd = self._personal(root)
            self.assertEqual(allowlist.tracked_by_both(gd), set())

    def test_rejection_control_an_unanswerable_question_is_not_a_clean_result(self):
        with fixture() as root:
            missing = os.path.join(os.path.dirname(root), "absent.git")
            self.assertIsNone(allowlist.tracked_by_both(missing))


# ----------------------------------------------------------------- raw/ at depth

class RawDepthExclusionTests(unittest.TestCase):
    """Validator. Subject: the depth-independence of the bulk-material exclusion.

    The classification excludes source material under any `raw/` folder at any
    depth, written `wikis/**/raw/**`. The superseded one-level spelling
    `wikis/*/raw/**` reaches a live wiki's `raw/` and misses an archived wiki's,
    which sits one level deeper. That difference is what let a source PDF be
    claimed by Category A for two days without anyone editing a rule.

    **These fixtures are synthetic on purpose, and that is the point of the class.**
    The suite's other check on this is a sweep over the real selection, which can
    only tell the two spellings apart while the tree actually contains a `raw/`
    folder two levels down. Today exactly one does, it contributes three files out
    of more than sixty-two thousand, and it is queued for deletion immediately after
    Stage A. From that deletion onward the sweep would pass on either spelling, so
    the distinction would stop being tested at the moment the evidence was removed.
    A fixture does not decay when the tree changes shape.
    """

    FILES = ("wikis/live/raw/source.pdf",
             "wikis/live/wiki/page.md",
             "wikis/archived/old/raw/source.pdf",
             "wikis/archived/old/wiki/page.md")

    CORRECT = [":(glob)wikis/**", ":(exclude,glob)wikis/**/raw/**"]
    SUPERSEDED = [":(glob)wikis/**", ":(exclude,glob)wikis/*/raw/**"]

    @contextlib.contextmanager
    def _wikis(self):
        with fixture(ignore_rules="wikis/\n", force_add=(), plain_add=(),
                     files=self.FILES) as root:
            yield root

    def test_positive_control_the_live_spelling_excludes_raw_at_both_depths(self):
        with self._wikis():
            selected = allowlist.shadow_selection(self.CORRECT)
            self.assertIsNotNone(selected)
            self.assertNotIn("wikis/live/raw/source.pdf", selected)
            self.assertNotIn("wikis/archived/old/raw/source.pdf", selected)

    def test_positive_control_authored_wiki_content_survives_the_exclusion(self):
        """Without this, the class above would pass on a selection of nothing."""
        with self._wikis():
            selected = allowlist.shadow_selection(self.CORRECT)
            self.assertIn("wikis/live/wiki/page.md", selected)
            self.assertIn("wikis/archived/old/wiki/page.md", selected)

    def test_rejection_control_the_superseded_spelling_leaks_the_deep_one(self):
        """The load-bearing control. It proves this suite can tell the correct
        spelling from the wrong one, rather than passing on both. If this ever
        starts passing, the test has stopped measuring depth."""
        with self._wikis():
            selected = allowlist.shadow_selection(self.SUPERSEDED)
            self.assertIsNotNone(selected)
            self.assertNotIn("wikis/live/raw/source.pdf", selected)
            self.assertIn("wikis/archived/old/raw/source.pdf", selected)


# --------------------------------------------------- authored folders, every type

class AuthoredFolderFileTypeTests(unittest.TestCase):
    """Validator. Subject: the four authored personal folders take EVERY file type.

    The decision of 2026-08-24 settled that `conversations/`, `reviews/`,
    `user-inputs/` and `journal/entries/` take everything they contain regardless
    of extension, and that the media-and-PDF exclusion is scoped to bulk source
    material under any `raw/`. Six real files turned on it: a PDF, four PNGs in a
    subfolder, and a zip. `user-inputs/` exists precisely to receive a CV as a PDF
    or a `.docx`.

    **These fixtures are synthetic on purpose, for two reasons.**

    First, a sweep over the real tree cannot test the rule for three of the four
    folders: as of 2026-08-27 `reviews/`, `user-inputs/` and `journal/entries/`
    contain no non-markdown file at all, so there is nothing there for a real-tree
    control to find. The rule would go untested until the day a CV arrived, which
    is the day it matters.

    Second, a real-tree control cannot tell a broken rule from an ordinary tidy-up.
    A file that stops being selected because the pattern broke and a file that stops
    being selected because it was deleted produce identical output, and this suite
    gates the Stage A seed commit, so a false alarm blocks a build. A fixture asks
    only about the rule, which cannot be deleted by accident.
    """

    FILES = ("conversations/notes.md",
             "conversations/review.pdf",
             "conversations/assets/screenshot.png",
             "conversations/pack.zip",
             "reviews/2026-08-23.md",
             "reviews/attachment.pdf",
             "user-inputs/cv.pdf",
             "user-inputs/cv.docx",
             "journal/entries/2026-08.md",
             "journal/entries/scan.png",
             "wikis/w/raw/bulk.pdf")

    AUTHORED = [":(glob)conversations/**", ":(glob)reviews/**",
                ":(glob)user-inputs/**", ":(glob)journal/entries/**"]
    # The shape the decision replaced: scoped to markdown, so it misses every one
    # of the six files the decision was taken to protect.
    MARKDOWN_ONLY = [":(glob)conversations/*.md", ":(glob)reviews/*.md",
                     ":(glob)user-inputs/*.md", ":(glob)journal/entries/*.md"]

    NON_MARKDOWN = ("conversations/review.pdf",
                    "conversations/assets/screenshot.png",
                    "conversations/pack.zip",
                    "reviews/attachment.pdf",
                    "user-inputs/cv.pdf",
                    "user-inputs/cv.docx",
                    "journal/entries/scan.png")

    @contextlib.contextmanager
    def _authored(self):
        rules = "conversations/\nreviews/\nuser-inputs/\njournal/\nwikis/\n"
        with fixture(ignore_rules=rules, force_add=(), plain_add=(),
                     files=self.FILES) as root:
            yield root

    def test_positive_control_every_authored_folder_takes_every_file_type(self):
        """One assertion per file, so a partial regression names the file that
        broke rather than reporting that 'something' is missing."""
        with self._authored():
            selected = allowlist.shadow_selection(self.AUTHORED)
            self.assertIsNotNone(selected)
            for rel in self.NON_MARKDOWN:
                with self.subTest(path=rel):
                    self.assertIn(rel, selected)

    def test_positive_control_a_subfolder_contributes_a_non_markdown_file(self):
        """The four PNGs sat in a subfolder, and a single `*` does not reach one.
        Asserted separately because a depth failure and a file-type failure are
        different defects that a combined check would conflate."""
        with self._authored():
            selected = allowlist.shadow_selection(self.AUTHORED)
            self.assertIn("conversations/assets/screenshot.png", selected)

    def test_positive_control_markdown_is_still_selected(self):
        """Without this the class above would pass on a rule that took only
        non-markdown files, which is the opposite defect and equally wrong."""
        with self._authored():
            selected = allowlist.shadow_selection(self.AUTHORED)
            self.assertIn("conversations/notes.md", selected)
            self.assertIn("journal/entries/2026-08.md", selected)

    def test_rejection_control_a_markdown_scoped_rule_misses_all_of_them(self):
        """The load-bearing control. It proves this suite can tell the settled rule
        from the shape it replaced, rather than passing on both. If this ever starts
        passing, the class has stopped measuring file type."""
        with self._authored():
            selected = allowlist.shadow_selection(self.MARKDOWN_ONLY)
            self.assertIsNotNone(selected)
            self.assertIn("conversations/notes.md", selected)
            for rel in self.NON_MARKDOWN:
                with self.subTest(path=rel):
                    self.assertNotIn(rel, selected)

    def test_negative_control_bulk_material_is_not_swept_in(self):
        """A PDF under a `raw/` folder is not an instance of the subject at all.
        Without this, 'the authored patterns select PDFs' could be satisfied by a
        rule that selects every PDF anywhere, which would swallow the bulk."""
        with self._authored():
            selected = allowlist.shadow_selection(self.AUTHORED)
            self.assertNotIn("wikis/w/raw/bulk.pdf", selected)


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
