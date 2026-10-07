#!/usr/bin/env python3
"""The one skip rule for tests that need this project's gitignored files.

Some tests here read the live `SYNC-ARCHITECTURE-PLAN.md` or select from the
project's gitignored files. Inside the clean copy the review orchestrator's
container runs (tracked and unignored files only), neither exists, so those tests
cannot run there. They are skipped when, and only when, BOTH hold:

- `BOOK_DRAGON_CLEAN_COPY=1` is set, which only the container sets; and
- `SYNC-ARCHITECTURE-PLAN.md` is absent.

On the host, with no marker, a missing plan still fails as before, so the skip
cannot hide a plan that went missing on the user's machine. The rule lives here
once so the suites cannot drift apart on it.
"""

import os
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PLAN_PATH = PROJECT_ROOT / "SYNC-ARCHITECTURE-PLAN.md"
REASON = ("needs the project's gitignored files, which the clean copy leaves out; "
          "runs on the host")


def clean_copy_without_plan(environ=None, plan_path=None):
    """True only inside the clean copy with the plan absent. The defaults are read
    when called, not when this module loads, so a test can point them elsewhere."""
    environ = os.environ if environ is None else environ
    plan_path = PLAN_PATH if plan_path is None else plan_path
    return environ.get("BOOK_DRAGON_CLEAN_COPY") == "1" and not Path(plan_path).exists()


def needs_live_data(test):
    """Decorator for a test method or class that needs the gitignored files. The
    condition is decided when the decorator is applied, at import."""
    return unittest.skipIf(clean_copy_without_plan(), REASON)(test)
