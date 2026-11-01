#!/usr/bin/env python3
"""Tests for the intent check's mechanical layer (plan 10.6 item 1, A4), as pure
functions of ``loop.py``: ``normal_path``, ``out_of_scope_paths``, ``path_problems``,
``acceptance_exits`` and ``acceptance_failures``. The checks wired into a run, with an
amended brief, are in ``test_intent_plan.py`` and ``test_intent_final.py``.

    python workflows/review-orchestration/tests/test_intent_mechanical.py
"""

import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_fixtures import *  # noqa: E402,F401,F403 - the loop suites' shared fixtures

EXISTENCE = ["cache", "cache/big.bin", "config", "config/a.txt", "scripts",
             "scripts/start.py", "work", "work/a.txt"]
TREES = {"README.md", "workflows/x/run.py"}


def items(*paths):
    return [{"line": 3, "path": path} for path in paths]


class OutOfScopeTests(unittest.TestCase):

    def test_positive_existing_files_and_folders_are_checked(self):
        checked, unchecked = loop.out_of_scope_paths(
            items("config/", "scripts/start.py", "README.md", "workflows/x"),
            EXISTENCE, TREES)
        self.assertEqual(checked, ["config", "scripts/start.py", "README.md",
                                   "workflows/x"])
        self.assertEqual(unchecked, [])

    def test_positive_a_bare_name_or_code_word_is_not_checked(self):
        checked, unchecked = loop.out_of_scope_paths(
            items("start.py", "reviewed-clean", "  "), EXISTENCE, TREES)
        self.assertEqual(checked, [])
        self.assertEqual([i["path"] for i in unchecked], ["start.py", "reviewed-clean",
                                                          "  "])

    def test_positive_an_ignored_folder_existing_at_creation_is_checked(self):
        # In the creation-time listing only: in neither tree, not in the hash selection.
        checked, _ = loop.out_of_scope_paths(items("cache"), EXISTENCE, TREES)
        self.assertEqual(checked, ["cache"])

    def test_negative_a_path_made_after_the_run_started_is_still_no_such_path(self):
        # Existence comes from the snapshot, never the live tree at check time.
        checked, unchecked = loop.out_of_scope_paths(items("later/new.txt"), EXISTENCE,
                                                     TREES)
        self.assertEqual((checked, len(unchecked)), ([], 1))

    def test_positive_spellings_are_normalised(self):
        checked, _ = loop.out_of_scope_paths(
            items(".\\Config\\", "./SCRIPTS/start.py", "config"), EXISTENCE, TREES)
        self.assertEqual(checked, ["Config", "SCRIPTS/start.py"], "repeats are dropped")
        self.assertEqual(loop.normal_path(" ./a\\b/ "), "a/b")


class PathRuleTests(unittest.TestCase):

    def test_positive_outside_inside_and_both_are_each_reported(self):
        problems = loop.path_problems(
            ["work/a.txt", "other/b.txt", "work/config/c.txt", "config/a.txt"],
            ["work/", "config/"], ["work/config", "config"])
        self.assertEqual(problems, [
            {"path": "other/b.txt", "reasons": ["outside the edit paths"]},
            {"path": "work/config/c.txt",
             "reasons": ["inside the Out of scope path `work/config`"]},
            {"path": "config/a.txt",
             "reasons": ["inside the Out of scope path `config`"]}])
        both = loop.path_problems(["scripts/start.py"], ["work/"], ["scripts"])
        self.assertEqual(both, [{"path": "scripts/start.py",
                                 "reasons": ["outside the edit paths",
                                             "inside the Out of scope path `scripts`"]}])

    def test_positive_a_folder_covers_everything_beneath_it(self):
        self.assertEqual(loop.path_problems(["work/deep/x/y.txt"], ["work"], []), [])
        self.assertEqual(len(loop.path_problems(["config/deep/x"], ["config/deep/"],
                                                ["config"])), 1)

    def test_negative_a_prefix_collision_does_not_match(self):
        self.assertEqual(loop.path_problems(["work2/a.txt"], ["work2/"], ["work"]), [])
        self.assertEqual(len(loop.path_problems(["workers/a.txt"], ["work/"], [])), 1)

    def test_positive_case_and_backslash_spellings_match(self):
        self.assertEqual(loop.path_problems(["WORK\\A.txt"], ["work/"], []), [])
        self.assertEqual(len(loop.path_problems(["work/Config/x"], ["work/"],
                                                ["work/config"])), 1)

    def test_negative_a_repeated_path_is_judged_once(self):
        self.assertEqual(len(loop.path_problems(["x.txt", "./x.txt"], ["work/"], [])), 1)


class AcceptanceTests(unittest.TestCase):

    TEXT = ("checked work\n"
            "acceptance check PASS (exit 0): python a.py\n"
            "acceptance check FAIL (exit 2): python b.py --flag\n"
            "acceptance check PASS (exit 0): python a.py\n"
            "acceptance check garbled: python c.py\n")

    def test_positive_exits_are_read_in_order_and_garbled_lines_give_nothing(self):
        self.assertEqual(loop.acceptance_exits(self.TEXT),
                         [{"command": "python a.py", "exit": 0},
                          {"command": "python b.py --flag", "exit": 2},
                          {"command": "python a.py", "exit": 0}])
        self.assertEqual(loop.acceptance_exits(None), [])

    def test_positive_a_non_zero_exit_and_a_missing_result_fail(self):
        results = loop.acceptance_exits(self.TEXT)
        failures = loop.acceptance_failures(
            ["python a.py", "python b.py --flag", "python c.py"], results)
        self.assertEqual(failures, [
            {"command": "python b.py --flag", "problem": "exited 2"},
            {"command": "python c.py", "problem": "no result recorded"}])

    def test_positive_a_command_written_twice_needs_two_results(self):
        once = [{"command": "python a.py", "exit": 0}]
        self.assertEqual(loop.acceptance_failures(["python a.py"] * 2, once),
                         [{"command": "python a.py", "problem": "no result recorded"}])
        twice = once * 2
        self.assertEqual(loop.acceptance_failures(["python a.py"] * 2, twice), [])

    def test_rejection_no_recorded_round_fails_every_check(self):
        self.assertEqual(loop.acceptance_failures(["python a.py"], None),
                         [{"command": "python a.py", "problem": "no result recorded"}])


if __name__ == "__main__":
    unittest.main()
