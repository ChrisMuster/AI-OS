#!/usr/bin/env python3
"""One-command test runner for the doc-sync-guard workflow.

    python workflows/doc-sync-guard/tests/run_tests.py

The guard itself is standard library plus the git CLI, but the output-inventory
loader parses YAML, so the suite bootstraps into the project ``.venv`` before
discovery. Without the handoff the inventory tests die with
``ModuleNotFoundError: No module named 'yaml'`` on any interpreter that lacks
the project packages. The guard's own no-PyYAML behaviour is still covered: one
test runs it under an interpreter with ``yaml`` blocked. Exits non-zero on any
failure.
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
    suite = unittest.defaultTestLoader.discover(str(TESTS_DIR), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
