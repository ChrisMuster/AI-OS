#!/usr/bin/env python3
"""Hermetic tests for the pauses, stop reasons and loop-exit rules (stopreasons.py).

Same three controls as test_brief.py:

    positive control  a legal reply or history, built from plan section 10.5. It must
                      be read as the plan says, and end no run it should not.
    rejection control a reply that breaks the loop-exit fields, or a history a rule
                      ends. It must be refused, or end the run, with the reason named.
    negative control  a history that looks like a rule's trigger but is not (a
                      reraise of a declined-merits finding's neighbour, a root raised
                      in two rounds that are not consecutive). It must not end the run.

    python workflows/review-orchestration/tests/test_stopreasons.py
"""

import importlib.util
import json
import sys
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent
SCRIPTS = WORKFLOW / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sr = _load("stopreasons")


def finding(title="A defect", reraises="", severity="major"):
    return {"title": title, "severity": severity, "file": "x.py", "evidence": "e",
            "fix": "f", "reraises": reraises}


def reply(*findings):
    return {"summary": "s", "findings": list(findings)}


def actions_block(entries, prose="Done."):
    return f"{prose}\n\n```json\n{json.dumps(entries)}\n```\n"


def act(label, action="fixed", note="n"):
    return {"label": label, "action": action, "note": note}


class Run:
    """Drives a Ledger through review rounds and fix passes as the loop will."""

    def __init__(self):
        self.ledger = sr.Ledger()
        self.round = 0

    def review(self, *findings, mechanical=()):
        self.round += 1
        parsed = sr.parse_review(reply(*findings), self.ledger)
        return self.ledger.add_round(self.round, list(mechanical), parsed)

    def fix(self, *entries):
        actions = sr.parse_builder_reply(actions_block(list(entries)),
                                         self.ledger.open_labels())
        self.ledger.apply_actions(actions)

    def decide(self):
        return sr.decide(self.ledger)


class LabelTests(unittest.TestCase):

    def test_positive_labels_are_issued_mechanical_first_then_reviewer(self):
        run = Run()
        new = run.review(finding("r1"), finding("r2"),
                         mechanical=[{"title": "doc-sync: drift"}])
        self.assertEqual([(f["label"], f["source"]) for f in new],
                         [("R1-1", "mechanical"), ("R1-2", "reviewer"), ("R1-3", "reviewer")])
        self.assertTrue(all(sr.LABEL.match(f["label"]) for f in new))
        self.assertIsNone(new[0]["reraises"])

    def test_rejection_a_round_not_after_the_last(self):
        run = Run()
        run.review(finding())
        with self.assertRaises(ValueError):
            run.ledger.add_round(1, [], [])

    def test_positive_title_label_detection(self):
        for title in ("R1-2 again", "same as R12-3", "see (R2-1)", "R3 - 1 persists",
                      "R3" + chr(0x2014) + "1 persists"):
            with self.subTest(title=title):
                self.assertTrue(sr.title_has_label(title))

    def test_negative_label_lookalikes(self):
        for title in ("R2-D2 droid", "PR12-3 merged", "HR1-2 form", "R2 is fine",
                      "Round 2-1", "ARR1-2"):
            with self.subTest(title=title):
                self.assertFalse(sr.title_has_label(title))


class ReviewReplyTests(unittest.TestCase):

    def setUp(self):
        self.ledger = sr.Ledger()
        self.ledger.add_round(1, [], [finding()])

    def test_positive_a_valid_reply_and_its_text_form(self):
        parsed = sr.parse_review(reply(finding(reraises="R1-1"), finding()), self.ledger)
        self.assertEqual([f["reraises"] for f in parsed], ["R1-1", ""])
        again = sr.parse_review(json.dumps(reply()), self.ledger)
        self.assertEqual(again, [])

    def test_positive_reraises_is_trimmed(self):
        parsed = sr.parse_review(reply(finding(reraises=" R1-1 ")), self.ledger)
        self.assertEqual(parsed[0]["reraises"], "R1-1")

    def test_rejection_malformed_replies(self):
        bad_item = finding()
        del bad_item["fix"]
        cases = [
            ("not json", "is not JSON"),
            ([], "is not a JSON object"),
            ({"findings": []}, "must hold exactly: summary, findings"),
            ({"summary": "s", "findings": [], "extra": 1}, "must hold exactly"),
            ({"summary": 1, "findings": []}, "summary is not text"),
            ({"summary": "s", "findings": {}}, "findings are not a list"),
            (reply(bad_item), "reviewer finding 1 must hold exactly"),
            (reply({**finding(), "extra": ""}), "reviewer finding 1 must hold exactly"),
            (reply(finding(severity="critical")), "severity 'critical' is not one of"),
            (reply({**finding(), "evidence": None}), "evidence is not text"),
            (reply(finding(title="R1-1 is back")), "the title holds a finding label"),
            (reply(finding(reraises="R9-9")), "reraises 'R9-9', which this run has not"),
        ]
        for value, reason in cases:
            with self.subTest(value=value):
                with self.assertRaisesRegex(sr.ReplyError, reason):
                    sr.parse_review(value, self.ledger)

    def test_positive_the_schema_matches_what_the_parser_requires(self):
        items = sr.REVIEW_SCHEMA["properties"]["findings"]["items"]
        self.assertEqual(set(items["required"]), set(items["properties"]))
        self.assertIn("reraises", items["required"])
        self.assertNotIn("repeats", items["properties"])
        self.assertFalse(items["additionalProperties"])
        self.assertEqual(items["properties"]["severity"]["enum"], list(sr.SEVERITIES))

    def test_positive_the_schema_title_pattern_refuses_a_label(self):
        import re
        pattern = re.compile(sr.TITLE_PATTERN)
        self.assertIsNone(pattern.match("R1-2 again"))
        self.assertIsNone(pattern.match("same as R1 - 2"))
        self.assertIsNotNone(pattern.match("R2-D2 droid"))
        self.assertIsNotNone(pattern.match("A plain title"))


class BuilderReplyTests(unittest.TestCase):

    OPEN = ["R1-1", "R1-2"]

    def parse(self, text, open_labels=None):
        return sr.parse_builder_reply(text, self.OPEN if open_labels is None else open_labels)

    def test_positive_every_open_finding_once(self):
        text = actions_block([act("R1-2", "declined-merits", "wrong"), act("R1-1")])
        self.assertEqual(self.parse(text), {"R1-1": ("fixed", "n"),
                                            "R1-2": ("declined-merits", "wrong")})

    def test_positive_the_last_block_is_the_answer(self):
        text = (actions_block([act("R1-1", "declined-scope")], prose="Draft:")
                + actions_block([act("R1-1"), act("R1-2")], prose="Final:"))
        self.assertEqual(self.parse(text)["R1-1"][0], "fixed")

    def test_positive_no_open_findings_and_an_empty_list(self):
        self.assertEqual(self.parse(actions_block([]), open_labels=[]), {})

    def test_rejection_breaks_of_the_loop_exit_fields(self):
        cases = [
            ("no block at all", "has no fenced json block"),
            ("```\n[]\n```", "has no fenced json block"),
            ("```json\n[{]\n```", "is not valid JSON"),
            (actions_block({"R1-1": "fixed"}), "is not a JSON list"),
            (actions_block([act("R1-1")]), "gave no action for: R1-2"),
            (actions_block([act("R1-1"), act("R1-1"), act("R1-2")]),
             "R1-1 is given more than one action"),
            (actions_block([act("R1-1"), act("R1-2", "declined")]),
             "action 'declined'; the action must be one of"),
            (actions_block([act("R1-1"), act("R1-2"), act("R1-3")]),
             "names 'R1-3', which is not an open finding"),
            (actions_block([act("R1-1"), {"label": "R1-2", "action": "fixed"}]),
             "must hold exactly: label, action, note"),
            (actions_block([act("R1-1"), act("R1-2", note=None)]), "note is not text"),
            ("", "has no fenced json block"),
            (None, "has no fenced json block"),
        ]
        for text, reason in cases:
            with self.subTest(text=text):
                with self.assertRaisesRegex(sr.ReplyError, reason):
                    self.parse(text)

    def test_negative_a_closed_finding_cannot_be_actioned_again(self):
        run = Run()
        run.review(finding())
        run.fix(act("R1-1"))
        run.review(finding())
        with self.assertRaisesRegex(sr.ReplyError, "'R1-1', which is not an open finding"):
            run.fix(act("R1-1"), act("R2-1"))


class RuleTests(unittest.TestCase):

    def test_positive_fixed_findings_that_stay_fixed_end_nothing(self):
        run = Run()
        run.review(finding(), finding())
        run.fix(act("R1-1"), act("R1-2"))
        self.assertIsNone(run.decide())
        run.review(finding("new"))
        self.assertIsNone(run.decide())

    def test_rejection_rule1_declined_scope_ends_at_once(self):
        run = Run()
        run.review(finding(), finding())
        run.fix(act("R1-1"), act("R1-2", "declined-scope"))
        self.assertEqual(run.decide(), ("out-of-scope", {"declined_scope": ["R1-2"]}))

    def test_rejection_rule2_reraise_of_declined_merits(self):
        run = Run()
        run.review(finding())
        run.fix(act("R1-1", "declined-merits"))
        self.assertIsNone(run.decide(), "a declined-merits action alone ends nothing")
        run.review(finding("still wrong", reraises="R1-1"))
        reason, evidence = run.decide()
        self.assertEqual(reason, "disagreement")
        self.assertEqual(evidence["reraises_of_declined_merits"],
                         [{"finding": "R2-1", "reraises": "R1-1"}])

    def test_negative_rule2_a_reraise_of_a_fixed_neighbour(self):
        run = Run()
        run.review(finding(), finding())
        run.fix(act("R1-1", "declined-merits"), act("R1-2"))
        run.review(finding(reraises="R1-2"))
        self.assertIsNone(run.decide())

    def test_rejection_rule3_root_reraised_in_two_consecutive_rounds(self):
        run = Run()
        run.review(finding())                         # R1-1
        run.fix(act("R1-1"))
        run.review(finding(reraises="R1-1"))          # R2-1 -> root R1-1, R1-1 was fixed
        self.assertIsNone(run.decide(), "one reraise is not yet reopened")
        run.fix(act("R2-1"))
        run.review(finding(reraises="R2-1"))          # R3-1 -> root R1-1, R2-1 was fixed
        self.assertEqual(run.decide(), ("reopened", {"rounds": [2, 3],
                                                     "first_findings": ["R1-1"]}))

    def test_negative_rule3_rounds_that_are_not_consecutive(self):
        run = Run()
        run.review(finding())                         # R1-1
        run.fix(act("R1-1"))
        run.review(finding(reraises="R1-1"))          # R2-1, root R1-1
        run.fix(act("R2-1"))
        run.review(finding("other"))                  # R3-1, new
        run.fix(act("R3-1"))
        run.review(finding(reraises="R2-1"))          # R4-1, root R1-1, but round 3 had none
        self.assertIsNone(run.decide())

    def test_negative_rule3_different_roots_in_consecutive_rounds(self):
        run = Run()
        run.review(finding(), finding())              # R1-1, R1-2
        run.fix(act("R1-1"), act("R1-2"))
        run.review(finding(reraises="R1-1"))          # R2-1 root R1-1
        run.fix(act("R2-1"))
        run.review(finding(reraises="R1-2"))          # R3-1 root R1-2
        self.assertIsNone(run.decide())

    def test_negative_rule3_needs_the_reraised_finding_marked_fixed(self):
        # Round 3 raises R1-1's chain again, but through R2-1, which the builder never
        # marked fixed (built without that fix pass, so R2-1 has no action). Only
        # round 2 counts, so the root is not raised in two consecutive rounds.
        ledger = sr.Ledger()
        ledger.add_round(1, [], [finding()])
        ledger.apply_actions({"R1-1": ("fixed", "")})
        ledger.add_round(2, [], sr.parse_review(reply(finding(reraises="R1-1")), ledger))
        ledger.add_round(3, [], sr.parse_review(reply(finding(reraises="R2-1")), ledger))
        self.assertIsNone(sr.decide(ledger))

    def test_positive_rule_order_scope_before_disagreement(self):
        run = Run()
        run.review(finding(), finding())
        run.fix(act("R1-1", "declined-merits"), act("R1-2"))
        run.review(finding(reraises="R1-1"), finding())
        run.fix(act("R2-1", "declined-merits"), act("R2-2", "declined-scope"))
        self.assertEqual(run.decide()[0], "out-of-scope")

    def test_positive_root_follows_the_whole_chain(self):
        run = Run()
        run.review(finding())
        run.fix(act("R1-1"))
        run.review(finding(reraises="R1-1"))
        run.fix(act("R2-1"))
        self.assertEqual(run.ledger.root("R2-1"), "R1-1")
        self.assertEqual(run.ledger.root("R1-1"), "R1-1")

    def test_positive_mechanical_findings_take_actions_but_never_reraise(self):
        run = Run()
        new = run.review(mechanical=[{"title": "close-out gate failed", "reraises": "R0-1"}])
        self.assertIsNone(new[0]["reraises"], "only a reviewer's finding can reraise")
        run.fix(act("R1-1"))
        self.assertIsNone(run.decide())


class OtherStopTests(unittest.TestCase):

    def test_rejection_an_empty_first_diff(self):
        self.assertEqual(sr.after_first_pass(True), ("no-change-made", {"diff": "empty"}))

    def test_positive_a_first_diff_with_changes(self):
        self.assertIsNone(sr.after_first_pass(False))

    def test_rejection_the_cap_reached_with_findings(self):
        reason, evidence = sr.after_review(3, 3, [{"label": "R3-1"}])
        self.assertEqual(reason, "max-rounds")
        self.assertEqual(evidence, {"round": 3, "cap": 3, "open": ["R3-1"]})

    def test_negative_the_cap_reached_with_no_findings(self):
        # No findings at the cap is not max-rounds; clean also needs the intent check.
        self.assertIsNone(sr.after_review(3, 3, []))

    def test_positive_below_the_cap(self):
        self.assertIsNone(sr.after_review(2, 3, [{"label": "R2-1"}]))


class StateTests(unittest.TestCase):

    def state(self):
        return {"status": "running", "pause": None, "stop_reasons": [], "rounds": []}

    def test_positive_a_pause_writes_no_stop_reason(self):
        state = sr.enter_pause(self.state(), "awaiting-plan-approval")
        self.assertEqual((state["status"], state["pause"]), ("paused", "awaiting-plan-approval"))
        self.assertEqual(state["stop_reasons"], [])
        state = sr.continue_run(state)
        self.assertEqual((state["status"], state["pause"]), ("running", None))

    def test_rejection_pauses_and_stops_outside_the_tables(self):
        with self.assertRaises(ValueError):
            sr.enter_pause(self.state(), "usage-limit")
        with self.assertRaises(ValueError):
            sr.record_stop(self.state(), "awaiting-user")
        with self.assertRaises(ValueError):
            sr.record_stop(self.state(), "done")

    def test_positive_an_ended_run_carries_exactly_one_new_reason(self):
        state = sr.record_stop(self.state(), "max-rounds", {"round": 3}, now="T1")
        self.assertEqual(state["status"], "ended")
        self.assertEqual(state["stop_reasons"],
                         [{"reason": "max-rounds", "evidence": {"round": 3}, "at": "T1"}])
        with self.assertRaisesRegex(ValueError, "already ended"):
            sr.record_stop(state, "error")

    def test_positive_resume_keeps_the_earlier_reason(self):
        state = sr.record_stop(self.state(), "usage-limit", now="T1")
        state = sr.continue_run(state)
        state = sr.record_stop(state, "clean", now="T2")
        self.assertEqual([s["reason"] for s in state["stop_reasons"]],
                         ["usage-limit", "clean"])

    def test_rejection_resume_of_a_reason_the_table_does_not_allow(self):
        for reason in ("clean", "intent-failed", "no-change-made", "reopened",
                       "out-of-scope", "disagreement", "stopped-by-user"):
            with self.subTest(reason=reason):
                state = sr.record_stop(self.state(), reason, now="T")
                with self.assertRaisesRegex(ValueError, "cannot be resumed"):
                    sr.continue_run(state)

    def test_positive_every_resumable_reason_resumes(self):
        for reason in sr.RESUMABLE:
            with self.subTest(reason=reason):
                state = sr.continue_run(sr.record_stop(self.state(), reason, now="T"))
                self.assertEqual(state["status"], "running")

    def test_rejection_pausing_a_run_that_is_not_running(self):
        state = sr.record_stop(self.state(), "error", now="T")
        with self.assertRaisesRegex(ValueError, "only a running run can pause"):
            sr.enter_pause(state, "awaiting-user")
        with self.assertRaisesRegex(ValueError, "already running"):
            sr.continue_run(self.state())

    def test_positive_state_rounds_record_label_source_action_reraises(self):
        run = Run()
        run.review(finding(), mechanical=[{"title": "audit: x"}])
        run.fix(act("R1-1"), act("R1-2", "declined-merits"))
        run.review(finding(reraises="R1-1"))
        state = sr.record_rounds(self.state(), run.ledger)
        self.assertEqual(state["rounds"], [
            {"round": 1, "findings": [
                {"label": "R1-1", "source": "mechanical", "action": "fixed", "reraises": None},
                {"label": "R1-2", "source": "reviewer", "action": "declined-merits",
                 "reraises": None}]},
            {"round": 2, "findings": [
                {"label": "R2-1", "source": "reviewer", "action": None,
                 "reraises": "R1-1"}]}])
        json.dumps(state)  # the record must be JSON-safe for state.json

    def test_positive_the_vocabulary_matches_the_plan_tables(self):
        self.assertEqual(set(sr.STOP_REASONS), {
            "clean", "intent-failed", "no-change-made", "max-rounds", "reopened",
            "out-of-scope", "disagreement", "usage-limit", "stopped-by-user", "error"})
        self.assertEqual(set(sr.PAUSES), {"awaiting-plan-approval", "awaiting-user"})
        self.assertEqual(set(sr.RESUMABLE), {"max-rounds", "usage-limit", "error"})
        self.assertEqual(set(sr.ACTIONS), {"fixed", "declined-scope", "declined-merits"})


if __name__ == "__main__":
    unittest.main()
