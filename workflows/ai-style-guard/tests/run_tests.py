#!/usr/bin/env python3
"""One-command test runner for the ai-style-guard workflow."""

import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(str(TESTS_DIR), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
