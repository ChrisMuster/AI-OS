#!/usr/bin/env python3
"""run_tests.py - Single entry point for the triggers test suite.

Discovers and runs every ``test_*.py`` in this directory in one command. Exits
non-zero if any test fails, making it usable in close-out.

    python workflows/triggers/tests/run_tests.py
"""
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent.parent.parent
RUNTIME_SCRIPTS = PROJECT_ROOT / "workflows" / "biblio-tools" / "scripts"

sys.path.insert(0, str(RUNTIME_SCRIPTS))


def main() -> int:
    from runtime import ensure_project_runtime

    ensure_project_runtime()
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(TESTS_DIR), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
