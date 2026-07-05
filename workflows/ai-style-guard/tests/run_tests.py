#!/usr/bin/env python3
"""One-command test runner for the ai-style-guard workflow.

    python workflows/ai-style-guard/tests/run_tests.py

Bootstraps into the project ``.venv`` before discovery so the documented
command passes under a plain ``python``. The config-loader tests import the
guard module and call ``load_config()``, which reads the tells config via
PyYAML; without the handoff those tests die with ``ModuleNotFoundError: No
module named 'yaml'`` on any interpreter that lacks the project packages.
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
