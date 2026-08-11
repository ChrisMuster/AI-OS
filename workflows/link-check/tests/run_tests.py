#!/usr/bin/env python3
"""One-command test runner for the link-check workflow.

    python workflows/link-check/tests/run_tests.py

A local convenience only. The close-out verifier does not read this file: it
discovers suites by globbing ``tests/test_*.py`` under each test root
(``discover_suites`` at ``workflows/close-out/scripts/run.py:141``), so an
assertion written here rather than in a ``test_*.py`` module would sit outside
the verification gate while looking like it counted.

No runtime bootstrap, unlike the doc-sync-guard runner: link-check and this suite
are standard library only, so there is no third-party import to hand off for.
Exits non-zero on any failure.
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
