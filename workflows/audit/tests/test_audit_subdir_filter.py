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


if __name__ == "__main__":
    unittest.main()
