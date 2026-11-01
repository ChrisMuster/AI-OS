#!/usr/bin/env python3
"""The A4 brief's test floors (Constraints: no test is lost). Each named suite is
loaded by its path and its cases counted with unittest's loader, without running any
of them, so counting starts no external service. Fails unless every count is at or
above its floor.

    python workflows/review-orchestration/tests/test_a4_test_floors.py
"""

import importlib.util
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

# Floors from the A4 brief: a group of files counts together.
FLOORS = (
    ("test_loop*.py", 276),
    ("test_approval.py", 56),
    ("test_planning.py", 76),
    (("test_plan_review.py", "test_plan_wiring.py"), 67),
    ("test_stopreasons.py", 39),
    ("test_brief.py", 118),
    ("test_providers.py", 144),
    ("test_start.py", 32),
    ("test_runrecord.py", 10),
    ("test_limits.py", 63),
)
KNOWN_LOOP_FILES = {"test_loop.py", "test_loop_usage.py", "test_loop_trusted.py",
                    "test_loop_parts.py", "test_loop_pairing.py"}


def files_for(names):
    if isinstance(names, str) and "*" in names:
        return sorted(TESTS.glob(names))
    names = (names,) if isinstance(names, str) else names
    return [TESTS / name for name in names]


def count(path):
    """The number of test cases a suite file defines, loaded under a name of its own so
    its module code runs (as an import) but no test does."""
    name = f"_floors_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return unittest.defaultTestLoader.loadTestsFromModule(module).countTestCases()


def shortfalls(floors):
    """Every ``(names, total, floor)`` whose total is below its floor."""
    found = []
    for names, floor in floors:
        total = sum(count(path) for path in files_for(names))
        if total < floor:
            found.append((names, total, floor))
    return found


class FloorTests(unittest.TestCase):

    def test_positive_a_raised_floor_is_reported_so_the_comparison_bites(self):
        total = count(TESTS / "test_runrecord.py")
        self.assertEqual(shortfalls([("test_runrecord.py", total)]), [])
        self.assertEqual(shortfalls([("test_runrecord.py", total + 1)]),
                         [("test_runrecord.py", total, total + 1)])

    def test_positive_the_loop_glob_finds_the_five_known_files(self):
        found = {path.name for path in files_for("test_loop*.py")}
        self.assertTrue(KNOWN_LOOP_FILES <= found, found)

    def test_positive_every_count_is_at_or_above_its_floor(self):
        for names, _floor in FLOORS:
            for path in files_for(names):
                self.assertTrue(path.is_file(), path.name)
        self.assertEqual(shortfalls(FLOORS), [], "(names, tests found, floor)")


if __name__ == "__main__":
    unittest.main()
