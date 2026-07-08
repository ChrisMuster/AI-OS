#!/usr/bin/env python3
"""One-command test runner for the doc-sync-guard workflow.

    python workflows/doc-sync-guard/tests/run_tests.py

The guard uses only the Python 3.9+ standard library and the git CLI (no
third-party packages), so no project-.venv bootstrap is needed here; plain
unittest discovery is enough. Exits non-zero on any failure.
"""

import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent


def main() -> int:
    suite = unittest.defaultTestLoader.discover(str(TESTS_DIR), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
