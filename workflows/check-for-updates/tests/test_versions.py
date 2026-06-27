#!/usr/bin/env python3
"""Unit tests for the semver parse/classify helper."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import versions  # noqa: E402


class TestVersions(unittest.TestCase):
    def test_parse_basic(self):
        self.assertEqual(versions.parse("1.2.3"), (1, 2, 3))
        self.assertEqual(versions.parse("v1.2.3"), (1, 2, 3))
        self.assertEqual(versions.parse("claude 1.4.0 (build 42)"), (1, 4, 0))

    def test_parse_non_semver(self):
        self.assertIsNone(versions.parse(""))
        self.assertIsNone(versions.parse(None))
        self.assertIsNone(versions.parse("dev"))

    def test_classify_levels(self):
        self.assertEqual(versions.classify("1.2.3", "1.2.3"), "up to date")
        self.assertEqual(versions.classify("1.2.3", "1.2.4"), "patch")
        self.assertEqual(versions.classify("1.2.3", "1.3.0"), "minor")
        self.assertEqual(versions.classify("1.2.3", "2.0.0"), "major")
        self.assertEqual(versions.classify("1.2.3", "1.2.0"), "up to date")

    def test_classify_prerelease_not_behind(self):
        self.assertEqual(versions.classify("1.2.3", "1.3.0-rc1"), "up to date")
        self.assertEqual(versions.classify("1.2.3", "2.0.0-beta"), "up to date")

    def test_classify_unknown(self):
        self.assertEqual(versions.classify("abc", "def"), "unknown")
        self.assertEqual(versions.classify("abc", "abc"), "up to date")


if __name__ == "__main__":
    unittest.main()
