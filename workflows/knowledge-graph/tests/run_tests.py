#!/usr/bin/env python3
"""run_tests.py — Single entry point for the knowledge-graph test suite.

Discovers and runs every ``test_*.py`` in this directory in one command, so the
four test modules (parser, builder, validate, query) do not have to be invoked
separately. Exits non-zero if any test fails, making it usable in close-out.

    python workflows/knowledge-graph/tests/run_tests.py

Note: the MCP dispatcher test lives with its server in
``workflows/biblio-tools/tests/test_kg_query.py`` and is run from there.
"""

import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent


def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(TESTS_DIR), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
