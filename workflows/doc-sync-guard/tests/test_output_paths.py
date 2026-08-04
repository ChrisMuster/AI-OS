#!/usr/bin/env python3
"""Tests for the classified output inventory and its loader.

Two questions are under test here, and they are the two the stage exists to
answer: are all known guard-output and state destinations ignored before
anything writes to them, and is there exactly one loader that every consumer
goes through?

The assertions run against the real repository (the inventory describes this
project, so a fixture copy would only prove the fixture is self-consistent),
except for the before-and-after ignore test, which needs a repository whose
`.gitignore` does not yet carry the block; that one builds a throwaway repo with
the block stripped out, so "these paths were not ignored before" is measured
rather than remembered.
"""

import contextlib
import io
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
WORKFLOW_DIR = TESTS_DIR.parent
PROJECT_ROOT = WORKFLOW_DIR.parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import inventory  # noqa: E402
import run  # noqa: E402

GITIGNORE = PROJECT_ROOT / ".gitignore"
BLOCK_HEADER = "# Doc-sync-guard certification output and ignored-content state"
BLOCK_LINES = 6  # the comment plus its five patterns

# The containers whose item names are personal. A tracked file may not name an
# item below one of these.
PERSONAL_CONTAINERS = (
    "wikis/", "conversations/", "journal/entries/", "reviews/",
    "user-inputs/", "memory/",
)
HOUSEKEEPING_BASENAMES = (".gitkeep", "CONTEXT.md", "LOG.md")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=str(cwd), check=check,
                          capture_output=True, encoding="utf-8")


def is_ignored(root, path):
    """True when git says *path* is ignored inside *root*.

    `git check-ignore` exits 1 for "not ignored", which is a negative answer and
    not a failure, so the exit code is read rather than checked. Paths are passed
    as arguments rather than on stdin: on Windows a CR byte can otherwise become
    part of the path.
    """
    result = git(root, "check-ignore", "-q", "--", path, check=False)
    if result.returncode not in (0, 1):
        raise AssertionError(
            f"git check-ignore failed for {path!r}: {result.stderr.strip()}")
    return result.returncode == 0


def gitignore_without_block():
    """The project `.gitignore` as it read before the stage 3a block landed."""
    lines = GITIGNORE.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.strip() == BLOCK_HEADER:
            del lines[i:i + BLOCK_LINES]
            return "".join(lines)
    raise AssertionError(f"{BLOCK_HEADER!r} not found in .gitignore")


class TempRepo:
    """A throwaway git repository carrying a chosen `.gitignore`."""

    def __init__(self, gitignore_text):
        self.dir = Path(tempfile.mkdtemp(prefix="inventory-test-"))
        git(self.dir, "init", "-q")
        git(self.dir, "config", "user.email", "t@example.com")
        git(self.dir, "config", "user.name", "Test")
        (self.dir / ".gitignore").write_bytes(gitignore_text.encode("utf-8"))

    def cleanup(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)


def load_fixture(tmpdir, body):
    """Write *body* as a config file and load it. Returns the Inventory."""
    path = Path(tmpdir) / "fixture.yaml"
    path.write_bytes(textwrap.dedent(body).encode("utf-8"))
    return inventory.load(path)


VALID_ROW = """\
version: 1
rows:
  - path: "wikis/*"
    assertion: assert-ignored
    example: "wikis/example/wiki/index.md"
    seed: true
    doc_sync: checked
    provenance: ["sweep-1"]
"""


def config_with(row_body, version_line="version: 1"):
    return f"{version_line}\nrows:\n{textwrap.indent(textwrap.dedent(row_body), '  ')}"


def container_forms_ok(value):
    """The permitted forms below a personal container.

    Below one of the containers the remainder must be a single `*` (the
    wildcard granularity the container rows are written at), or a first segment
    beginning with `example` (the synthetic marker), or exactly `.gitkeep`,
    `CONTEXT.md` or `LOG.md` at the container's immediate level. A generic
    basename does not make a path generic once an item name sits in front of it.
    """
    for container in PERSONAL_CONTAINERS:
        if not value.startswith(container):
            continue
        rest = value[len(container):]
        if rest == "*":
            return True
        if rest.split("/", 1)[0].startswith("example"):
            return True
        return rest in HOUSEKEEPING_BASENAMES
    return True


# ---------------------------------------------------------------------------
# The .gitignore block: before and after
# ---------------------------------------------------------------------------
class TestFutureArtefactPaths(unittest.TestCase):
    """The paths named before they exist, asserted either side of the block."""

    def setUp(self):
        self.inv = inventory.load()
        self.future = [
            a for a in self.inv.assertions()
            if a.kind == "ignored"
            and self._row(a.row_path).provenance == ["decision-13a"]
        ]

    def _row(self, path):
        for row in self.inv.rows:
            if row.path == path:
                return row
        raise AssertionError(f"no row for {path!r}")

    def test_there_are_eight_future_ignored_assertions(self):
        # Decision 13a names six destinations producing eight asserted ignored
        # paths (two stores need a temp-path assertion each). A count that drifts
        # from eight means a destination was added or dropped without the
        # decision moving.
        self.assertEqual(len(self.future), 8,
                         [a.path for a in self.future])

    def test_paths_are_not_ignored_before_the_block(self):
        repo = TempRepo(gitignore_without_block())
        try:
            unprotected = [a.path for a in self.future
                           if is_ignored(repo.dir, a.path)]
            self.assertEqual(unprotected, [],
                             "these were already ignored, so the block's effect "
                             "on them is unproven")
        finally:
            repo.cleanup()

    def test_paths_are_ignored_after_the_block(self):
        repo = TempRepo(GITIGNORE.read_text(encoding="utf-8"))
        try:
            unprotected = [a.path for a in self.future
                           if not is_ignored(repo.dir, a.path)]
            self.assertEqual(unprotected, [])
        finally:
            repo.cleanup()

    def test_certification_skeleton_stays_trackable(self):
        for path in ("workflows/doc-sync-guard/certification/.gitkeep",
                     "workflows/doc-sync-guard/certification/CONTEXT.md"):
            with self.subTest(path=path):
                self.assertFalse(is_ignored(PROJECT_ROOT, path))

    def test_inventory_config_is_tracked(self):
        rel = "workflows/doc-sync-guard/config/output-inventory.yaml"
        self.assertFalse(is_ignored(PROJECT_ROOT, rel))
        committable = git(
            PROJECT_ROOT, "ls-files", "--cached", "--others",
            "--exclude-standard", "-z", "--", rel).stdout.split("\0")
        self.assertIn(rel, [p for p in committable if p])


# ---------------------------------------------------------------------------
# Every row asserted against the real repository
# ---------------------------------------------------------------------------
class TestRepositoryAssertions(unittest.TestCase):
    def setUp(self):
        self.inv = inventory.load()

    def test_every_assert_ignored_path_is_ignored(self):
        wrong = [a.path for a in self.inv.assertions()
                 if a.kind == "ignored" and not is_ignored(PROJECT_ROOT, a.path)]
        self.assertEqual(wrong, [])

    def test_every_assert_tracked_path_is_not_ignored(self):
        wrong = [a.path for a in self.inv.assertions()
                 if a.kind == "tracked" and is_ignored(PROJECT_ROOT, a.path)]
        self.assertEqual(wrong, [])

    def test_every_gitignore_line_has_a_row(self):
        # The inventory is derived one row per non-comment .gitignore line, so
        # an ignore line added without a row is a path nothing here answers
        # for. Asserting the mapping rather than the count means the failure
        # names the line instead of leaving two numbers to reconcile.
        # Two lines are answered by more than one row rather than by one of
        # their own: `ignored-state.jsonl*` and `tell-baseline.jsonl*` each
        # cover a store and its temp path, and decision 13a asserts both
        # separately. A line is therefore satisfied by a row declaring it
        # verbatim, or by covering at least one row's asserted path.
        declared = {row.path for row in self.inv.rows}
        asserted = [row.assertion_path for row in self.inv.rows]
        missing = []
        for raw in GITIGNORE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            pattern = line.lstrip("!")
            if pattern in declared:
                continue
            if any(inventory.matches(pattern, path) for path in asserted):
                continue
            missing.append(line)
        self.assertEqual(missing, [])

    def test_future_rows_are_the_only_rows_no_sweep_found(self):
        future = [r for r in self.inv.rows if r.provenance == ["decision-13a"]]
        self.assertEqual(len(future), 11, [r.path for r in future])
        for row in self.inv.rows:
            if row in future:
                continue
            with self.subTest(path=row.path):
                self.assertNotIn("decision-13a", row.provenance)


# ---------------------------------------------------------------------------
# Schema validation: the loader refuses what it cannot answer for
# ---------------------------------------------------------------------------
class TestKeySetRefusals(unittest.TestCase):
    """The seven key-set refusals, each asserted on its own.

    Refusals 2 and 6 come first deliberately. They are the direction nobody
    checks, and a loader written from the requirement halves alone (a patterned
    row needs an example, an out-of-scope row needs a reason) passes both while
    letting an unread key sit in the config drifting quietly.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _refuses(self, body, version_line="version: 1"):
        with self.assertRaises(inventory.InventoryError) as ctx:
            load_fixture(self.tmp.name, config_with(body, version_line))
        return str(ctx.exception)

    def test_2_concrete_row_carrying_an_example(self):
        message = self._refuses("""\
            - path: "workflows/audit/last-report.md"
              assertion: assert-ignored
              example: "workflows/audit/last-report.md"
              seed: true
              doc_sync: E11
              provenance: ["sweep-1"]
        """)
        self.assertIn("example", message)

    def test_6_assertion_row_carrying_a_reason(self):
        message = self._refuses("""\
            - path: "workflows/audit/last-report.md"
              assertion: assert-ignored
              seed: true
              doc_sync: E11
              reason: generated output
              provenance: ["sweep-1"]
        """)
        self.assertIn("reason", message)

    def test_1_patterned_row_with_no_example(self):
        message = self._refuses("""\
            - path: "wikis/*"
              assertion: assert-ignored
              seed: true
              doc_sync: checked
              provenance: ["sweep-1"]
        """)
        self.assertIn("example", message)

    def test_3_assertion_row_with_no_doc_sync(self):
        message = self._refuses("""\
            - path: "workflows/audit/last-report.md"
              assertion: assert-ignored
              seed: true
              provenance: ["sweep-1"]
        """)
        self.assertIn("doc_sync", message)

    def test_4_out_of_scope_row_carrying_a_doc_sync(self):
        message = self._refuses("""\
            - path: "dist/"
              assertion: out-of-scope
              reason: packaging output
              seed: false
              doc_sync: E10
              provenance: ["sweep-1"]
        """)
        self.assertIn("doc_sync", message)

    def test_5_out_of_scope_row_with_no_reason(self):
        message = self._refuses("""\
            - path: "dist/"
              assertion: out-of-scope
              seed: false
              provenance: ["sweep-1"]
        """)
        self.assertIn("reason", message)

    def test_7_row_with_no_provenance(self):
        message = self._refuses("""\
            - path: "workflows/audit/last-report.md"
              assertion: assert-ignored
              seed: true
              doc_sync: E11
        """)
        self.assertIn("provenance", message)

    def test_7_row_with_an_empty_provenance_list(self):
        message = self._refuses("""\
            - path: "workflows/audit/last-report.md"
              assertion: assert-ignored
              seed: true
              doc_sync: E11
              provenance: []
        """)
        self.assertIn("provenance", message)

    def test_out_of_scope_row_carrying_an_example(self):
        # An out-of-scope row is never asserted, so an example there is a value
        # no consumer reads: refusal 2's argument, one row kind across.
        message = self._refuses("""\
            - path: "dist/"
              assertion: out-of-scope
              reason: packaging output
              example: "dist/example.whl"
              seed: false
              provenance: ["sweep-1"]
        """)
        self.assertIn("example", message)


class TestSchemaValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _refuses(self, text):
        with self.assertRaises(inventory.InventoryError) as ctx:
            load_fixture(self.tmp.name, text)
        return str(ctx.exception)

    def test_valid_fixture_loads(self):
        # The positive control: every refusal below is a single edit away from
        # this config, so a refusal proves the edit was caught and not that the
        # fixture was broken to begin with.
        inv = load_fixture(self.tmp.name, VALID_ROW)
        self.assertEqual(len(inv.rows), 1)

    def test_unknown_doc_sync_value(self):
        # A negative control on the enum: it has grown once and can grow again,
        # and a loader that silently accepts E14 from a typo exempts a path by
        # spelling mistake.
        message = self._refuses(VALID_ROW.replace("checked", "E14"))
        self.assertIn("E14", message)

    def test_unknown_provenance_value(self):
        message = self._refuses(VALID_ROW.replace('"sweep-1"', '"sweep-4"'))
        self.assertIn("sweep-4", message)

    def test_unknown_assertion_value(self):
        message = self._refuses(VALID_ROW.replace("assert-ignored", "assert-maybe"))
        self.assertIn("assertion", message)

    def test_missing_seed(self):
        message = self._refuses(VALID_ROW.replace("    seed: true\n", ""))
        self.assertIn("seed", message)

    def test_unknown_key(self):
        message = self._refuses(VALID_ROW + '    signature_excluded: true\n')
        self.assertIn("signature_excluded", message)

    def test_duplicate_path(self):
        message = self._refuses(VALID_ROW + VALID_ROW.split("rows:\n", 1)[1])
        self.assertIn("duplicate", message)

    def test_version_other_than_one(self):
        message = self._refuses(VALID_ROW.replace("version: 1", "version: 2"))
        self.assertIn("version", message)

    def test_missing_version(self):
        message = self._refuses(VALID_ROW.replace("version: 1\n", ""))
        self.assertIn("version", message)

    def test_empty_rows_list(self):
        message = self._refuses("version: 1\nrows: []\n")
        self.assertIn("rows", message)


class TestFailsClosed(unittest.TestCase):
    """A missing or unparseable config raises; it never answers emptily."""

    def test_missing_config_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "not-here.yaml"
            with self.assertRaises(inventory.InventoryError):
                inventory.load(missing)

    def test_malformed_config_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.yaml"
            path.write_bytes(b"version: 1\nrows:\n  - path: [unclosed\n")
            with self.assertRaises(inventory.InventoryError):
                inventory.load(path)

    def test_non_mapping_config_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "list.yaml"
            path.write_bytes(b"- just\n- a\n- list\n")
            with self.assertRaises(inventory.InventoryError):
                inventory.load(path)


# ---------------------------------------------------------------------------
# Loader answers
# ---------------------------------------------------------------------------
class TestLoaderAnswers(unittest.TestCase):
    def setUp(self):
        self.inv = inventory.load()

    def test_quoted_star_paths_survive_yaml(self):
        # Unquoted scalars beginning with * are YAML aliases. If a quote were
        # dropped the parse would fail or the value would arrive as something
        # other than the pattern, so this asserts the values, not just the load.
        for path in ("**/LOG.md", "*.py[cod]", "**/*-PLAN.md"):
            with self.subTest(path=path):
                self.assertTrue(any(r.path == path for r in self.inv.rows))
        for example in ("wikis/example/wiki/index.md",
                        "workflows/reddit-collector/Read example.bat"):
            with self.subTest(example=example):
                self.assertTrue(any(r.example == example for r in self.inv.rows))

    def test_assertions_use_example_for_patterned_rows(self):
        by_row = {a.row_path: a.path for a in self.inv.assertions()}
        self.assertEqual(by_row["wikis/*"], "wikis/example/wiki/index.md")
        self.assertEqual(by_row["workflows/audit/last-report.md"],
                         "workflows/audit/last-report.md")

    def test_denylist_is_e13(self):
        self.assertEqual(
            self.inv.doc_sync_exception(
                "workflows/personal-data-guard/config/denylist.txt"),
            "E13")

    def test_reddit_launcher_is_checked(self):
        self.assertEqual(
            self.inv.doc_sync_exception(
                "workflows/reddit-collector/Read example.bat"),
            "checked")

    def test_signature_excluded_for_an_e13_row(self):
        self.assertTrue(self.inv.signature_excluded(
            "workflows/personal-data-guard/config/denylist.txt"))

    def test_signature_included_for_wikis_and_launchers(self):
        for path in ("wikis/*",
                     "workflows/reddit-collector/Read *.bat",
                     "workflows/reddit-collector/Stop *.bat"):
            with self.subTest(path=path):
                self.assertFalse(self.inv.signature_excluded(path))

    def test_context_and_log_are_signature_excluded_by_basename(self):
        self.assertTrue(self.inv.signature_excluded("workflows/audit/CONTEXT.md"))
        self.assertTrue(self.inv.signature_excluded("workflows/audit/LOG.md"))

    def test_unmatched_path_defaults_to_checked(self):
        self.assertEqual(
            self.inv.doc_sync_exception("workflows/audit/scripts/run.py"),
            "checked")

    def test_out_of_scope_rows_are_seed_only(self):
        self.assertIn(".venv/", self.inv.seed_paths())
        with self.assertRaises(inventory.InventoryError):
            self.inv.doc_sync_exception(".venv/lib/site-packages/thing.py")
        with self.assertRaises(inventory.InventoryError):
            self.inv.signature_excluded("dist/wheel.whl")

    def test_seed_paths_are_row_paths_not_examples(self):
        seeds = self.inv.seed_paths()
        self.assertIn("wikis/*", seeds)
        self.assertNotIn("wikis/example/wiki/index.md", seeds)


class TestDocumentedChildInsideAnExemptStore(unittest.TestCase):
    """An E10 row exempts the store's loose contents, never a documented child.

    A prefix-matching loader passes the first assertion and fails the second,
    and a prefix matcher is the implementation the `conversations/*` row
    invites. The ownership half (which directory answers for a file) belongs to
    the caller that knows the filesystem; what is asserted here is that the
    inventory does not hand a child an answer it never claimed.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.inv = load_fixture(self.tmp.name, """\
            version: 1
            rows:
              - path: "synthetic-store/*"
                assertion: assert-ignored
                example: "synthetic-store/example.md"
                seed: true
                doc_sync: E10
                provenance: ["sweep-1"]
        """)

    def test_loose_file_inside_the_store_is_exempt(self):
        self.assertEqual(
            self.inv.doc_sync_exception("synthetic-store/loose-file.md"), "E10")

    def test_documented_child_is_checked(self):
        self.assertEqual(
            self.inv.doc_sync_exception("synthetic-store/child/CONTEXT.md"),
            "checked")

    def test_documented_child_reaches_checked_through_the_default(self):
        # Not through a row naming it: no row may name an item below a store.
        self.assertIsNone(self.inv.row_for("synthetic-store/child/CONTEXT.md"))
        self.assertFalse(
            inventory.matches("synthetic-store/*",
                              "synthetic-store/child/CONTEXT.md"))


# ---------------------------------------------------------------------------
# The tracked config must never name a personal item
# ---------------------------------------------------------------------------
class TestPersonalContainerRows(unittest.TestCase):
    def setUp(self):
        self.inv = inventory.load()

    def test_no_row_names_an_item_below_a_personal_container(self):
        offenders = []
        for row in self.inv.rows:
            for value in (row.path, row.example):
                if value is not None and not container_forms_ok(value):
                    offenders.append(value)
        self.assertEqual(offenders, [])

    def test_must_fail_fixtures_are_refused(self):
        # A real, date-shaped, harmless-looking journal file name: earlier plan
        # rounds carried exactly this in the stage's own worked rows, in the
        # same stage as the rule banning it.
        self.assertFalse(container_forms_ok("journal/entries/2026-01.md"))
        # The negative control on the generic-basename form: a generic basename
        # below an item directory still names the item. The fixture name is
        # synthetic and deliberately does not begin with `example`, or it would
        # pass on the marker rather than fail on the rule.
        self.assertFalse(container_forms_ok("wikis/topic-name/CONTEXT.md"))

    def test_permitted_forms_are_admitted(self):
        # The positive control, drawn from the three forms in the spec rather
        # than from the rows the check just passed.
        for value in ("memory/*", "memory/example.md", "memory/CONTEXT.md",
                      "journal/entries/.gitkeep", "wikis/example/wiki/index.md"):
            with self.subTest(value=value):
                self.assertTrue(container_forms_ok(value))


# ---------------------------------------------------------------------------
# One loader, and a guard that still runs without it
# ---------------------------------------------------------------------------
class TestSingleConsumer(unittest.TestCase):
    def test_only_inventory_py_reads_the_config(self):
        allowed = "workflows/doc-sync-guard/scripts/inventory.py"
        listing = git(PROJECT_ROOT, "ls-files", "--cached", "--others",
                      "--exclude-standard", "-z", "--", "*.py").stdout
        offenders = []
        for rel in [p for p in listing.split("\0") if p]:
            if rel == allowed or "/tests/" in f"/{rel}":
                continue
            try:
                text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "output-inventory.yaml" in text:
                offenders.append(rel)
        self.assertEqual(offenders, [],
                         "the inventory is read through inventory.py only")


class TestRunPyImportBoundary(unittest.TestCase):
    def test_run_py_imports_without_pyyaml(self):
        """`run.py` must not import the loader (and so PyYAML) at module level."""
        script = textwrap.dedent(f"""
            import importlib.abc
            import sys

            class BlockYaml(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname == "yaml" or fullname.startswith("yaml."):
                        raise ImportError("yaml is blocked for this test")
                    return None

            sys.meta_path.insert(0, BlockYaml())
            sys.path.insert(0, {str(SCRIPTS_DIR)!r})

            # Positive control: the blocker really does block.
            try:
                import inventory
            except ImportError:
                print("BLOCKER-WORKS")
            else:
                print("BLOCKER-BROKEN")

            import run
            print("RUN-IMPORTED", bool(run.LABEL))
        """)
        result = subprocess.run([sys.executable, "-c", script],
                                capture_output=True, encoding="utf-8",
                                cwd=str(PROJECT_ROOT))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("BLOCKER-WORKS", result.stdout)
        self.assertIn("RUN-IMPORTED True", result.stdout)


class TestRunCheckDegrades(unittest.TestCase):
    """`run_check` reports DEGRADED and exempts nothing when the inventory is
    unreadable.

    DEGRADED rather than WARN is the load-bearing part. A `doc-sync` WARN means
    drift to every consumer: close-out hard-fails on it and the pre-commit hook
    prints a drift advisory. An interpreter without PyYAML is not drift, so each
    test below asserts the severity, not merely the message.
    """

    def setUp(self):
        self.repo = TempRepo("**/LOG.md\n")
        self.addCleanup(self.repo.cleanup)
        (self.repo.dir / "CONTEXT.md").write_bytes(b"# Root\n")
        git(self.repo.dir, "add", "-A")
        git(self.repo.dir, "commit", "-q", "-m", "base", "--no-verify")
        inventory.clear_cache()
        self.addCleanup(inventory.clear_cache)

    def _messages(self, findings):
        return " || ".join(m for _, _, m in findings)

    def _probe(self, findings):
        """The inventory-probe findings, whatever severity they carry."""
        return [f for f in findings if "output inventory unavailable" in f[2]]

    @contextlib.contextmanager
    def _no_pyyaml(self):
        """Reproduce an interpreter with no PyYAML.

        The loader is already imported by this test module, so blocking `yaml`
        alone would prove nothing - the cached module would satisfy the probe's
        import. Dropping `inventory` from the cache first forces the real import
        path, which then fails on `yaml` exactly as it does on a machine where
        the package was never installed.
        """
        with mock.patch.dict(sys.modules):
            sys.modules.pop("inventory", None)
            sys.modules["yaml"] = None
            yield

    def _assert_degraded_not_warn(self, findings):
        probe = self._probe(findings)
        self.assertEqual(len(probe), 1, self._messages(findings))
        self.assertEqual(probe[0][0], run.DEGRADED)
        self.assertEqual(probe[0][1], run.LABEL)
        # The negative control, and the one that matters: it must not reach any
        # consumer as drift.
        self.assertNotIn(run.WARN, [f[0] for f in probe])

    def test_clean_when_the_inventory_loads(self):
        findings = run.run_check(self.repo.dir)
        self.assertEqual(self._probe(findings), [])

    def test_degraded_when_the_config_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "gone.yaml"
            with mock.patch.object(inventory, "DEFAULT_CONFIG", missing):
                findings = run.run_check(self.repo.dir)
        self._assert_degraded_not_warn(findings)

    def test_degraded_when_the_config_is_malformed(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "broken.yaml"
            broken.write_bytes(b"version: 1\nrows:\n  - path: [unclosed\n")
            with mock.patch.object(inventory, "DEFAULT_CONFIG", broken):
                findings = run.run_check(self.repo.dir)
        self._assert_degraded_not_warn(findings)

    def test_degraded_when_pyyaml_is_missing(self):
        # What an interpreter with no PyYAML produces: the lazy import fails on
        # the third-party package, and only that case is allowed to degrade.
        with self._no_pyyaml():
            findings = run.run_check(self.repo.dir)
        self._assert_degraded_not_warn(findings)
        # Positive control on the simulation itself: the import must have failed
        # on yaml, not on something incidental that would degrade for a reason
        # the probe is not claiming.
        self.assertIn("yaml", self._probe(findings)[0][2])

    def test_unexpected_import_failure_propagates(self):
        """A broken import inside the loader is a defect, not a degrade.

        The probe degrades on a missing runtime. An `inventory.py` that cannot
        be imported for any other reason is a bug in this repository, and
        reporting it as "output inventory unavailable" would hide a code defect
        behind a setup hint.
        """
        with mock.patch.dict(sys.modules, {"inventory": None}):
            with self.assertRaises(ImportError):
                run.run_check(self.repo.dir)

    def test_degraded_run_still_scans_and_exempts_nothing(self):
        # The guard keeps checking; it does not treat an unreadable exception
        # set as permission to skip the scan.
        (self.repo.dir / "thing.txt").write_bytes(b"v1\n")
        with self._no_pyyaml():
            findings = run.run_check(self.repo.dir)
        messages = self._messages(findings)
        self.assertIn("output inventory unavailable", messages)
        self.assertIn("CONTEXT.md not updated", messages)
        # And real drift found during a degraded run is still a WARN, so the
        # severity split is per-finding rather than a mode the whole run enters.
        drift = [f for f in findings if "CONTEXT.md not updated" in f[2]]
        self.assertEqual([f[0] for f in drift], [run.WARN])


class TestStrictGatesOnDriftOnly(unittest.TestCase):
    """--strict exits 1 on drift and never on a degrade.

    This is the regression the fix exists for: the inventory probe used to emit
    WARN, so an interpreter without PyYAML made `--strict` exit 1 and close-out
    hard-fail. Tested through the extracted decision rather than by running the
    CLI, whose exit code would otherwise depend on the real working tree.
    """

    DEGRADE = (run.DEGRADED, run.LABEL, "output inventory unavailable - x")
    DRIFT = (run.WARN, run.LABEL, "workflows/x - CONTEXT.md not updated")
    NOTE = (run.INFO, run.LABEL, "something informational")

    def test_no_findings_passes(self):
        self.assertFalse(run.strict_failed([]))

    def test_a_degrade_alone_passes(self):
        self.assertFalse(run.strict_failed([self.DEGRADE]))

    def test_info_alone_passes(self):
        self.assertFalse(run.strict_failed([self.NOTE]))

    def test_drift_fails(self):
        self.assertTrue(run.strict_failed([self.DRIFT]))

    def test_drift_still_fails_alongside_a_degrade(self):
        # The degrade must not mask real drift either - the exemption is for the
        # degrade itself, not for the run that contains one.
        self.assertTrue(run.strict_failed([self.DEGRADE, self.DRIFT]))


class TestReportRendersDegraded(unittest.TestCase):
    """A degrade-only report still reads as 'no drift', and shows the degrade."""

    def _render(self, findings):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            run.print_report(findings)
        return buffer.getvalue()

    def test_degrade_only_report_says_no_drift_and_shows_the_degrade(self):
        out = self._render([TestStrictGatesOnDriftOnly.DEGRADE])
        self.assertIn("**Degraded (did not run):** 1", out)
        self.assertIn("No CONTEXT.md / LOG.md drift found", out)
        self.assertIn("## DEGRADED (did not run)", out)
        self.assertIn("output inventory unavailable", out)
        self.assertNotIn("## WARN", out)

    def test_clean_report_counts_zero_degraded(self):
        out = self._render([])
        self.assertIn("**Degraded (did not run):** 0", out)
        self.assertIn("No CONTEXT.md / LOG.md drift found", out)

    def test_drift_report_does_not_claim_no_drift(self):
        out = self._render([TestStrictGatesOnDriftOnly.DRIFT])
        self.assertNotIn("No CONTEXT.md / LOG.md drift found", out)
        self.assertIn("## WARN", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
