#!/usr/bin/env python3
"""Unit tests for the optimised gitignore-aware directory walk (collect_dirs).

Covers the two speed optimisations while pinning the pruning semantics:
  A. a non-Git tree never spawns git;
  B. one batched check per depth level, with ignored/no-recurse dirs pruned
     before they are descended into.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import builder  # noqa: E402


def _mkdirs(root: Path, *rels: str) -> None:
    for rel in rels:
        (root / rel).mkdir(parents=True, exist_ok=True)


class TestIsGitWorktree(unittest.TestCase):
    def test_true_when_dot_git_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            self.assertTrue(builder._is_git_worktree(root))

    def test_detects_ancestor_dot_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            child = root / "a" / "b"
            child.mkdir(parents=True)
            self.assertTrue(builder._is_git_worktree(child))


class TestNonGitFastPath(unittest.TestCase):
    def test_non_worktree_never_spawns_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _mkdirs(root, "workflows/foo", "workflows/bar")
            with mock.patch.object(builder, "_is_git_worktree", return_value=False), \
                 mock.patch.object(builder.subprocess, "run",
                                   side_effect=AssertionError("git must not run")):
                dirs = builder.collect_dirs(root)
            rels = {p.relative_to(root).as_posix() for p in dirs}
            self.assertEqual(rels, {"workflows", "workflows/foo", "workflows/bar"})


class TestIgnorePruning(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _mkdirs(self.root, "keep/sub", "secret/child", "workflows")

    def tearDown(self):
        self._tmp.cleanup()

    def test_ignored_dir_and_its_children_pruned(self):
        # Pretend we are in a repo and that "secret" is gitignored.
        with mock.patch.object(builder, "_is_git_worktree", return_value=True), \
             mock.patch.object(builder, "_git_check_ignored_batch",
                               side_effect=lambda root, rels: {"secret"} & set(rels)):
            dirs = builder.collect_dirs(self.root)
        rels = {p.relative_to(self.root).as_posix() for p in dirs}
        self.assertIn("keep", rels)
        self.assertIn("keep/sub", rels)
        self.assertNotIn("secret", rels)
        self.assertNotIn("secret/child", rels)  # never descended into

    def test_skip_dirs_and_hidden_excluded(self):
        _mkdirs(self.root, "__pycache__", ".hidden", ".venv")
        with mock.patch.object(builder, "_is_git_worktree", return_value=False):
            dirs = builder.collect_dirs(self.root)
        rels = {p.relative_to(self.root).as_posix() for p in dirs}
        self.assertNotIn("__pycache__", rels)
        self.assertNotIn(".hidden", rels)
        self.assertNotIn(".venv", rels)


class TestNoRecurse(unittest.TestCase):
    def test_no_recurse_dir_recorded_but_not_descended(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _mkdirs(root, "data/inner", "raw/inner", "workflows/foo")
            with mock.patch.object(builder, "_is_git_worktree", return_value=False):
                dirs = builder.collect_dirs(root)
            rels = {p.relative_to(root).as_posix() for p in dirs}
            self.assertIn("data", rels)
            self.assertIn("raw", rels)
            self.assertNotIn("data/inner", rels)
            self.assertNotIn("raw/inner", rels)
            self.assertIn("workflows/foo", rels)


class TestBatchingPerLevel(unittest.TestCase):
    def test_one_call_per_level_not_per_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # One level of five sibling dirs: the old code spawned git five
            # times (once per parent-with-children); batching does it in one.
            _mkdirs(root, "a", "b", "c", "d", "e")
            calls = {"n": 0}

            def counting(root_, rels):
                calls["n"] += 1
                return set()

            with mock.patch.object(builder, "_is_git_worktree", return_value=True), \
                 mock.patch.object(builder, "_git_check_ignored_batch", counting):
                builder.collect_dirs(root)
            # Exactly one batched call for the single populated level.
            self.assertEqual(calls["n"], 1)

    def test_result_is_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _mkdirs(root, "zeta", "alpha", "mid/child")
            with mock.patch.object(builder, "_is_git_worktree", return_value=False):
                dirs = builder.collect_dirs(root)
            self.assertEqual(dirs, sorted(dirs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
