#!/usr/bin/env python3
"""Unit tests for audit subdirectory filtering."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run  # noqa: E402


class TestImmediateSubdirs(unittest.TestCase):
    """The unlisted-subdirectory check should ignore gitignored children."""

    def test_gitignored_children_are_filtered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "listed").mkdir()
            (root / "ignored-child").mkdir()
            (root / ".hidden").mkdir()
            (root / "__pycache__").mkdir()

            with mock.patch.object(
                run,
                "_git_check_ignored",
                return_value={"ignored-child"},
            ) as check_ignored:
                names = [p.name for p in run.get_immediate_subdirs(root)]

        self.assertEqual(names, ["listed"])
        check_ignored.assert_called_once_with(
            str(root),
            ["ignored-child", "listed"],
        )


# A minimal, fully-valid standard-format CONTEXT.md so audit_directory's other
# checks (sections, metadata, paths, links, stale phrases) stay silent and only
# the unlisted-subdirectory check can produce a finding.
_CLEAN_CONTEXT = """# Test

**Last modified:** 2026-06-26

## Purpose
Test fixture.

## Contents
- `listed/` - the one listed subdirectory.

## Inputs
None

## Outputs
None

## Steps
N/A

## Dependencies
None

## Known Issues
None

## Revision History
- 2026-06-26 - Initial creation.
"""


def _make_dir_with_children(root: Path) -> tuple[Path, Path]:
    """Create root/CONTEXT.md + LOG.md and two child dirs (listed, unlisted)."""
    (root / "CONTEXT.md").write_text(_CLEAN_CONTEXT, encoding="utf-8")
    (root / "LOG.md").write_text("", encoding="utf-8")
    listed = root / "listed"
    unlisted = root / "unlisted"
    listed.mkdir()
    unlisted.mkdir()
    return listed, unlisted


def _unlisted_warnings(findings):
    return [
        msg for level, _, msg in findings
        if level == "WARN" and "is not listed in Contents" in msg
    ]


class TestAuditDirectoryThreadedSubdirs(unittest.TestCase):
    """audit_directory reuses a passed-in subdirs list and only falls back to
    get_immediate_subdirs (the per-dir git spawn) when none is supplied."""

    def test_explicit_subdirs_used_without_calling_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            listed, unlisted = _make_dir_with_children(root)

            with mock.patch.object(run, "PROJECT_ROOT", root), \
                 mock.patch.object(
                     run, "get_immediate_subdirs",
                     side_effect=AssertionError("must not be called in full audit"),
                 ):
                findings = run.audit_directory(root, [listed, unlisted])

        warnings = _unlisted_warnings(findings)
        self.assertEqual(len(warnings), 1)
        self.assertIn("unlisted/", warnings[0])

    def test_none_falls_back_to_get_immediate_subdirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            listed, unlisted = _make_dir_with_children(root)

            with mock.patch.object(run, "PROJECT_ROOT", root), \
                 mock.patch.object(
                     run, "get_immediate_subdirs",
                     return_value=[listed, unlisted],
                 ) as gis:
                findings = run.audit_directory(root)

        gis.assert_called_once_with(root)
        warnings = _unlisted_warnings(findings)
        self.assertEqual(len(warnings), 1)
        self.assertIn("unlisted/", warnings[0])


class TestChildrenMapMatchesImmediateSubdirs(unittest.TestCase):
    """The parent->children map run_audit builds from collect_dirs must equal
    get_immediate_subdirs for every audited directory."""

    def test_map_equals_per_dir_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in ("workflows/a/sub", "workflows/b", "skills/c", "templates"):
                (root / rel).mkdir(parents=True, exist_ok=True)

            with mock.patch.object(run, "PROJECT_ROOT", root), \
                 mock.patch.object(run, "_is_git_worktree", return_value=False), \
                 mock.patch.object(run, "_git_check_ignored", return_value=set()):
                dirs = run.collect_dirs()
                children_by_parent: dict[Path, list[Path]] = {}
                for d in dirs:
                    children_by_parent.setdefault(d.parent, []).append(d)

                for d in dirs:
                    mapped = {p.name for p in children_by_parent.get(d, [])}
                    direct = {p.name for p in run.get_immediate_subdirs(d)}
                    self.assertEqual(
                        mapped, direct,
                        f"mismatch for {d.relative_to(root).as_posix()}",
                    )


if __name__ == "__main__":
    unittest.main()
