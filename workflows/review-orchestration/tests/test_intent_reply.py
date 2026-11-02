#!/usr/bin/env python3
"""Tests for the intent checker's reply contract (plan 10.6 item 2, A4):
``stopreasons.INTENT_SCHEMA``, ``intent_schema`` and ``parse_intent_reply``. A reply
names each Goal and Constraint line by its number and its opening words rather than
copying it, and the parser fills in the brief's own text. A reply that leaves a line
out, numbers the lines wrongly, gives an opening that is not its line's, gives one
with no evidence or does not match its schema raises ``ReplyError``, the existing
error path, and never counts as a pass or as ``cannot tell``. Pure functions; no
file, process or session.

    python workflows/review-orchestration/tests/test_intent_reply.py
"""

import importlib.util
import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sr = _load("stopreasons")

# The second line is longer than eight words, so its opening is a part of it; the
# first and third are shorter, so their opening is the whole line.
LINES = ["The check is built.",
         "No new dependency is added to the requirements files, and none to the image.",
         "The report lists each one."]


def entry(number, text, status="met"):
    return {"number": number, "opening": sr.opening(text), "status": status,
            "evidence": "seen"}


def reply(lines=LINES, status="met", numbers=None, **extra):
    numbers = numbers or range(1, len(lines) + 1)
    value = {"lines": [entry(n, text, status) for n, text in zip(numbers, lines)],
             "unasked_changes": [], "undelivered": []}
    value.update(extra)
    return value


class ReplyTests(unittest.TestCase):

    def refused(self, value, changed=None, lines=LINES):
        with self.assertRaises(sr.ReplyError) as caught:
            sr.parse_intent_reply(value, lines, changed_files=changed)
        return str(caught.exception)

    def test_positive_a_full_reply_parses(self):
        value = reply(unasked_changes=[{"item": "a stray file", "evidence": "the diff"}],
                      undelivered=[{"item": "the report", "evidence": "absent"}])
        result = sr.parse_intent_reply(value, LINES)
        self.assertEqual([e["status"] for e in result["lines"]], ["met"] * 3)
        self.assertEqual(result["unasked_changes"][0]["item"], "a stray file")

    def test_positive_the_result_carries_the_brief_text_not_the_opening(self):
        # The report and the causes read entry["line"]: it must be the brief's whole
        # line, filled in by the parser, and the opening is not passed on.
        result = sr.parse_intent_reply(reply(), LINES)
        self.assertEqual([e["line"] for e in result["lines"]], LINES)
        self.assertEqual([e["number"] for e in result["lines"]], [1, 2, 3])
        self.assertEqual(set(result["lines"][1]), {"number", "line", "status",
                                                   "evidence"})
        self.assertNotEqual(sr.opening(LINES[1]), LINES[1])

    def test_positive_the_opening_is_eight_words_or_the_whole_line(self):
        self.assertEqual(sr.opening(LINES[1]),
                         "No new dependency is added to the requirements")
        self.assertEqual(sr.opening(LINES[0]), LINES[0])
        self.assertEqual(sr.opening("  One\ttwo\n three  "), "One two three")

    def test_positive_every_status_is_accepted_and_cannot_tell_is_kept(self):
        for status in sr.INTENT_STATUSES:
            result = sr.parse_intent_reply(reply(status=status), LINES)
            self.assertEqual({e["status"] for e in result["lines"]}, {status})

    def test_positive_whitespace_runs_in_the_opening_are_collapsed(self):
        value = reply()
        value["lines"][0]["opening"] = " The  check is\nbuilt. "
        value["lines"][1]["opening"] = "No new\tdependency is added to the  requirements"
        sr.parse_intent_reply(value, LINES)

    def test_positive_a_json_text_reply_parses(self):
        sr.parse_intent_reply(json.dumps(reply()), LINES)

    def test_positive_lines_sharing_their_opening_are_each_checked_by_number(self):
        # Two lines whose first eight words agree (as lines 13 and 14 of the A4 proof
        # brief share their first six): each is matched against its own number, so
        # swapping their verdicts cannot be seen and is not refused, but each line's
        # own text is what the result carries.
        shared = ["The run report names every step and the time each took.",
                  "The run report names every step and the model each used."]
        result = sr.parse_intent_reply(reply(lines=shared), shared)
        self.assertEqual([e["line"] for e in result["lines"]], shared)

    def test_rejection_a_line_left_out(self):
        message = self.refused(reply(lines=LINES[:2]))
        self.assertIn("leaves out line 3", message)

    def test_rejection_a_gap_in_the_numbers(self):
        message = self.refused(reply(numbers=[1, 3, 4]))
        self.assertIn("leaves out line 2", message)

    def test_rejection_a_number_repeated(self):
        message = self.refused(reply(numbers=[1, 2, 2]))
        self.assertIn("leaves out line 3", message)
        value = reply(lines=LINES + [LINES[2]], numbers=[1, 2, 3, 3])
        self.assertIn("names line 3 twice", self.refused(value))

    def test_rejection_a_wrong_number(self):
        message = self.refused(reply(lines=LINES + ["Something else."],
                                     numbers=[1, 2, 3, 4]))
        self.assertIn("names line 4, which the brief does not have", message)
        self.assertIn("names line 0", self.refused(reply(numbers=[0, 1, 2, 3],
                                                         lines=["x"] + LINES)))

    def test_rejection_entries_swapped(self):
        # The entries in the wrong order, each keeping its own number and opening.
        value = reply()
        value["lines"][0], value["lines"][1] = value["lines"][1], value["lines"][0]
        self.assertIn("in the brief's order", self.refused(value))

    def test_rejection_a_verdict_under_the_wrong_number(self):
        # Numbers in order, but the first two openings swapped: a verdict meant for
        # one line given under the other's number.
        value = reply()
        value["lines"][0]["opening"], value["lines"][1]["opening"] = (
            value["lines"][1]["opening"], value["lines"][0]["opening"])
        message = self.refused(value)
        self.assertIn("line 1's opening", message)

    def test_rejection_a_wrong_opening(self):
        for opening in ("The check exists.", "The check is", "check is built.",
                        "No new dependency is added to the requirements files,",
                        "No new dependency is added to the", ""):
            with self.subTest(opening):
                value = reply()
                index = 1 if opening.startswith("No") else 0
                value["lines"][index]["opening"] = opening
                self.refused(value)

    def test_rejection_the_whole_line_copied_where_it_is_longer(self):
        # A copied line longer than eight words is not its opening: the rule is the
        # opening words exactly, so a reply in the old form is refused, not half-read.
        value = reply()
        value["lines"][1]["opening"] = LINES[1]
        self.refused(value)

    def test_rejection_the_old_reply_form(self):
        value = {"lines": [{"line": t, "status": "met", "evidence": "seen"}
                           for t in LINES], "unasked_changes": [], "undelivered": []}
        self.refused(value)

    def test_rejection_a_number_that_is_not_a_whole_number(self):
        for number in ("1", 1.0, True, None):
            with self.subTest(number=number):
                value = reply()
                value["lines"][0]["number"] = number
                self.refused(value)

    def test_rejection_blank_evidence(self):
        value = reply()
        value["lines"][1]["evidence"] = "   "
        self.refused(value)

    def test_rejection_a_list_item_with_no_evidence(self):
        self.refused(reply(undelivered=[{"item": "x", "evidence": ""}]))
        self.refused(reply(unasked_changes=[{"item": " ", "evidence": "e"}]))

    def test_rejection_an_unknown_status(self):
        self.refused(reply(status="partly"))

    def test_rejection_a_missing_or_extra_key(self):
        value = reply()
        del value["undelivered"]
        self.refused(value)
        self.refused(reply(summary="extra"))
        bad = reply()
        bad["lines"][0]["note"] = "extra"
        self.refused(bad)
        missing = reply()
        del missing["lines"][0]["opening"]
        self.refused(missing)

    def test_rejection_a_non_list_field_and_a_non_object(self):
        self.refused(reply(lines="all met"))
        self.refused(reply(undelivered={"item": "x"}))
        self.refused(["not", "an", "object"])
        self.refused("not json")

    def test_rejection_files_reviewed_must_cover_every_changed_path(self):
        changed = ["work/a.txt", "work/b.txt"]
        sr.parse_intent_reply(reply(files_reviewed=["./work/a.txt", "work\\b.txt"]),
                              LINES, changed_files=changed)
        self.refused(reply(files_reviewed=["work/a.txt"]), changed)
        self.refused(reply(), changed)  # no files_reviewed at all
        self.refused(reply(files_reviewed="work/a.txt"), changed)

    def test_negative_the_plan_check_takes_no_files_reviewed(self):
        self.refused(reply(files_reviewed=[]))  # an extra key for the first check

    def test_positive_the_schema_is_strict_and_the_final_one_adds_files_reviewed(self):
        def strict(node):
            if node.get("type") == "object":
                self.assertFalse(node["additionalProperties"])
                self.assertEqual(set(node["required"]), set(node["properties"]))
                for child in node["properties"].values():
                    strict(child)
            elif node.get("type") == "array":
                strict(node["items"])
        strict(sr.intent_schema())
        strict(sr.intent_schema(final=True))
        self.assertNotIn("files_reviewed", sr.intent_schema()["required"])
        self.assertIn("files_reviewed", sr.intent_schema(final=True)["required"])
        item = sr.INTENT_SCHEMA["properties"]["lines"]["items"]
        self.assertEqual(item["required"], ["number", "opening", "status", "evidence"])
        self.assertEqual(item["properties"]["number"], {"type": "integer"})
        self.assertEqual(item["properties"]["status"]["enum"],
                         ["met", "not met", "cannot tell"])


if __name__ == "__main__":
    unittest.main()
