#!/usr/bin/env python3
"""Tests for the intent checker's reply contract (plan 10.6 item 2, A4):
``stopreasons.INTENT_SCHEMA``, ``intent_schema`` and ``parse_intent_reply``. A reply
that leaves a line out, gives one with no evidence or does not match its schema raises
``ReplyError``, the existing error path, and never counts as a pass or as ``cannot
tell``. Pure functions; no file, process or session.

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

LINES = ["The check is built.", "No new dependency.", "The report lists each one."]


def reply(lines=LINES, status="met", **extra):
    value = {"lines": [{"line": text, "status": status, "evidence": "seen"}
                       for text in lines],
             "unasked_changes": [], "undelivered": []}
    value.update(extra)
    return value


class ReplyTests(unittest.TestCase):

    def refused(self, value, changed=None):
        with self.assertRaises(sr.ReplyError):
            sr.parse_intent_reply(value, LINES, changed_files=changed)

    def test_positive_a_full_reply_parses(self):
        value = reply(unasked_changes=[{"item": "a stray file", "evidence": "the diff"}],
                      undelivered=[{"item": "the report", "evidence": "absent"}])
        result = sr.parse_intent_reply(value, LINES)
        self.assertEqual([e["status"] for e in result["lines"]], ["met"] * 3)
        self.assertEqual(result["unasked_changes"][0]["item"], "a stray file")

    def test_positive_every_status_is_accepted_and_cannot_tell_is_kept(self):
        for status in sr.INTENT_STATUSES:
            result = sr.parse_intent_reply(reply(status=status), LINES)
            self.assertEqual({e["status"] for e in result["lines"]}, {status})

    def test_positive_whitespace_runs_are_collapsed_before_matching(self):
        spaced = ["The  check is\nbuilt.", " No new dependency. ", LINES[2]]
        sr.parse_intent_reply(reply(lines=spaced), LINES)

    def test_positive_a_json_text_reply_parses(self):
        sr.parse_intent_reply(json.dumps(reply()), LINES)

    def test_rejection_a_line_left_out(self):
        self.refused(reply(lines=LINES[:2]))

    def test_rejection_a_line_reordered(self):
        self.refused(reply(lines=[LINES[1], LINES[0], LINES[2]]))

    def test_rejection_an_extra_line(self):
        self.refused(reply(lines=LINES + ["Something else."]))

    def test_rejection_a_paraphrased_line(self):
        self.refused(reply(lines=["The check exists.", LINES[1], LINES[2]]))

    def test_rejection_repeated_brief_lines_must_each_have_their_entry(self):
        twice = ["Same line.", "Same line."]
        sr.parse_intent_reply(reply(lines=twice), twice)
        with self.assertRaises(sr.ReplyError):
            sr.parse_intent_reply(reply(lines=["Same line."]), twice)

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
        status = sr.INTENT_SCHEMA["properties"]["lines"]["items"]["properties"]["status"]
        self.assertEqual(status["enum"], ["met", "not met", "cannot tell"])


if __name__ == "__main__":
    unittest.main()
