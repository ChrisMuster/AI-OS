#!/usr/bin/env python3
"""The A5 brief's test floors (Constraints: no test is lost, and all pass). Each named
suite is loaded by its path and its cases counted with unittest's loader, without
running any of them, so counting starts no external service. Fails unless every count
is at or above its floor, and unless a suite named here exists.

    python workflows/review-orchestration/tests/test_a5_test_floors.py
"""

import importlib.util
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

# Floors from the A5 brief; a group of files counts together.
FLOORS = (
    ("test_loop.py", 115),
    ("test_loop_pairing.py", 53),
    ("test_loop_parts.py", 53),
    ("test_loop_trusted.py", 32),
    ("test_loop_usage.py", 23),
    ("test_approval.py", 56),
    ("test_approver.py", 58),
    ("test_brief.py", 118),
    ("test_check_server.py", 34),
    ("test_codex_rules.py", 92),
    ("test_container.py", 110),
    ("test_frozen.py", 24),
    ("test_limits.py", 63),
    ("test_plan_review.py", 41),
    ("test_plan_wiring.py", 38),
    ("test_planning.py", 76),
    ("test_providers.py", 144),
    ("test_runrecord.py", 10),
    ("test_start.py", 32),
    ("test_stopreasons.py", 40),
    ("test_trusted_checks.py", 46),
    ("test_a4_sessions.py", 5),
    ("test_a4_test_floors.py", 3),
    ("test_intent_*.py", 112),
)


def files_for(names):
    if isinstance(names, str) and "*" in names:
        return sorted(TESTS.glob(names))
    names = (names,) if isinstance(names, str) else names
    return [TESTS / name for name in names]


def count(path):
    """The number of test cases a suite file defines, loaded under a name of its own so
    its module code runs (as an import) but no test does."""
    name = f"_a5_floors_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return unittest.defaultTestLoader.loadTestsFromModule(module).countTestCases()


def shortfalls(floors):
    """Every ``(names, total, floor)`` whose total is below its floor, a missing file
    counting as no tests."""
    found = []
    for names, floor in floors:
        total = sum(count(path) for path in files_for(names) if path.is_file())
        if total < floor:
            found.append((names, total, floor))
    return found


class FloorTests(unittest.TestCase):

    def test_positive_a_raised_floor_is_reported_so_the_comparison_bites(self):
        total = count(TESTS / "test_runrecord.py")
        self.assertEqual(shortfalls([("test_runrecord.py", total)]), [])
        self.assertEqual(shortfalls([("test_runrecord.py", total + 1)]),
                         [("test_runrecord.py", total, total + 1)])

    def test_positive_a_missing_suite_is_reported(self):
        self.assertEqual(shortfalls([("test_no_such_suite.py", 1)]),
                         [("test_no_such_suite.py", 0, 1)])

    def test_positive_the_intent_group_finds_its_files(self):
        found = {path.name for path in files_for("test_intent_*.py")}
        self.assertTrue({"test_intent_plan.py", "test_intent_final.py",
                         "test_intent_prompts.py"} <= found, found)

    def test_positive_every_count_is_at_or_above_its_floor(self):
        for names, _floor in FLOORS:
            for path in files_for(names):
                self.assertTrue(path.is_file(), path.name)
        self.assertEqual(shortfalls(FLOORS), [], "(names, tests found, floor)")


if __name__ == "__main__":
    unittest.main()
