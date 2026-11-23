#!/usr/bin/env python3
"""Hermetic tests for the intent check run as information, step ``intent-info``, and
the report's "Not checked for intent" line (plan 10.6, "A run that ends without the
final check" and "At a `reopened` or `max-rounds` end the final check runs anyway, as
information").

At a ``reopened`` or ``max-rounds`` end the stop reason is decided first, the final
check's two layers then run as information, and the run ends with that stop reason
whatever the check found or whatever stopped it. Every other end that is not ``clean``
or ``intent-failed`` gets only the "Not checked for intent" line.

Whole runs are driven with the fixtures' fakes in a temporary repository. No test
starts Docker, a model session, a Claude probe or a Codex app server, and none needs
``--allow-paid`` to pass.

    python workflows/review-orchestration/tests/test_intent_info.py
"""

import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_fixtures import *  # noqa: E402,F401,F403 - the loop suites' shared fixtures

ACCEPTANCE = "python workflows/demo/tests/test_demo.py"
MAX_HEADING = ("## Intent check at a `max-rounds` end (information; the stop reason "
               "stands)")
REOPENED_HEADING = ("## Intent check at a `reopened` end (information; the stop reason "
                    "stands)")


class InfoCase(LoopCase):

    def capped(self, **kwargs):
        """A run that ends ``max-rounds`` after its first review round."""
        self.h.builder_script = [edit(self.root, "done\n")]
        self.h.reviews = [[finding()]]
        return self.start(cap=1, **kwargs)

    def reopened(self):
        """A run whose first finding is raised again in rounds 2 and 3, each time of a
        finding the builder had marked fixed: ``reopened`` after round 3."""
        self.h.builder_script = [edit(self.root, "done\n"),
                                 reply(actions(("R1-1", "fixed"))),
                                 reply(actions(("R2-1", "fixed")))]
        self.h.reviews = [[finding()], [finding(reraises="R1-1")],
                          [finding(reraises="R2-1")]]
        return self.start()

    def report(self, run):
        return (run.run_dir / "report.md").read_text(encoding="utf-8")

    def report_json(self, run):
        return json.loads((run.run_dir / "report.json").read_text(encoding="utf-8"))

    def info(self, run):
        return (run.state.get("intent") or {}).get("info") or []

    def info_steps(self, run):
        return [s for s in run.state.get("steps", []) if s["step"] == "intent-info"]


class RunsAtTheTwoEndsTests(InfoCase):

    def test_positive_max_rounds_runs_the_check_and_keeps_its_stop(self):
        run = self.capped()
        self.assertEqual(self.reason(run), sr.MAX_ROUNDS)
        self.assertEqual([s["reason"] for s in run.state["stop_reasons"]],
                         [sr.MAX_ROUNDS], "one stop, the one decided")
        self.assertEqual([c["provider"] for c in self.h.intent_calls], ["codex"])
        [entry] = self.info(run)
        self.assertEqual((entry["round"], entry["stop_reason"], entry["acceptance_round"],
                          entry["causes"]), (1, sr.MAX_ROUNDS, "R1", []))
        self.assertNotIn("final", run.state["intent"])
        self.assertNotIn("info_pending", run.state["intent"])
        # Not a completed step: a resume carries on from the last review round.
        self.assertEqual(run.state["step"], "review-R1")
        self.assertEqual(len(self.info_steps(run)), 1)
        self.assertEqual(self.info_steps(run)[0]["role"], "intent_checker")
        self.assertTrue((run.run_dir / "intent" / "info-R1-diff.patch").is_file())
        self.assertTrue((run.run_dir / "transcripts" / "intent-info-R1.json").is_file())
        self.assertFalse((run.run_dir / "transcripts" / "intent-final.json").exists())
        report = self.report(run)
        self.assertIn(MAX_HEADING, report)
        self.assertIn("**Result (information only):** no cause found.", report)
        self.assertIn("Acceptance results: those of review round R1, the last round, "
                      "which had findings", report)
        self.assertEqual(self.log_actions()[-1], "completed")

    def test_positive_reopened_runs_the_check_before_the_record_is_removed(self):
        run = self.reopened()
        self.assertEqual(self.reason(run), sr.REOPENED)
        [entry] = self.info(run)
        # A result at all means the start snapshot was still there when it ran.
        self.assertNotIn("not_run", entry)
        self.assertEqual((entry["round"], entry["stop_reason"]), (3, sr.REOPENED))
        self.assertIn(REOPENED_HEADING, self.report(run))
        # reopened cannot be resumed: the snapshot is removed at the end, after it.
        self.assertFalse((run.run_dir / "start-snapshot.json").exists())

    def test_positive_a_cause_found_is_reported_and_the_stop_stands(self):
        self.h.intent_replies = [lambda p: intent_reply(p, statuses=["not met", "met"])]
        run = self.capped()
        self.assertEqual(self.reason(run), sr.MAX_ROUNDS)
        [entry] = self.info(run)
        self.assertTrue(any("Goal line not met" in c for c in entry["causes"]))
        report = self.report(run)
        self.assertIn("**Result (information only):** 1 cause(s) found.", report)
        self.assertIn("Goal line not met: work/a.txt says done.", report)
        self.assertNotIn(sr.INTENT_FAILED, [s["reason"] for s in run.state["stop_reasons"]])

    def test_positive_the_acceptance_results_are_the_last_rounds(self):
        self.h.acceptance_exits = {ACCEPTANCE: 3}
        run = self.capped()
        self.assertEqual(self.reason(run), sr.MAX_ROUNDS)
        [entry] = self.info(run)
        self.assertIn(f"acceptance check `{ACCEPTANCE}`: exited 3", entry["causes"])
        self.assertEqual(len(self.h.check_calls), 1, "no acceptance check is run again")

    def test_positive_the_payload_is_the_brief_and_the_diff(self):
        run = self.capped()
        prompt = self.h.intent_calls[0]["prompt"]
        self.assertIn("work/a.txt says done.", prompt)
        self.assertIn("- work/a.txt", prompt)
        self.assertNotIn("A defect", prompt, "no reviewer finding reaches the checker")
        self.assertEqual(self.reason(run), sr.MAX_ROUNDS)

    def test_positive_a_resumed_max_rounds_run_checks_again_at_its_next_end(self):
        self.h.builder_script = [edit(self.root, "done\n"),
                                 reply(actions(("R1-1", "fixed")))]
        self.h.reviews = [[finding()], [finding(title="Another defect")]]
        run = self.start(cap=1)
        self.assertEqual(self.reason(run), sr.MAX_ROUNDS)
        again = self.resume(run, rounds=1)
        self.assertEqual([s["reason"] for s in again.state["stop_reasons"]],
                         [sr.MAX_ROUNDS, sr.MAX_ROUNDS])
        self.assertEqual([e["round"] for e in self.info(again)], [1, 2])
        self.assertEqual(again.state["step"], "review-R2")
        fixes = [s["step"] for s in again.state["steps"] if s["step"].startswith("fix")]
        self.assertEqual(fixes, ["fix-R1"], "the resume carried on from review-R1")
        for n in (1, 2):
            self.assertTrue((again.run_dir / "transcripts" / f"intent-info-R{n}.json")
                            .is_file())
        self.assertEqual(self.report(again).count(MAX_HEADING), 2)


class SwappedInfoTests(SwappedCase, InfoCase):

    def test_positive_codex_building_puts_the_check_on_claude(self):
        run = self.capped()
        self.assertEqual(self.reason(run), sr.MAX_ROUNDS)
        self.assertEqual([c["provider"] for c in self.h.intent_calls], ["claude"])
        before = [r for r in run.state["readings"] if r["step"] == "intent-info"]
        self.assertEqual(len(before), 1)
        self.assertIn("usage_probe", before[0], "a Claude step sends the probe first")


class NeverANewEndTests(InfoCase):
    """Whatever stops the information check is a note; the run ends with the stop
    reason already decided, once."""

    def assertNoted(self, run, reason):
        self.assertEqual(self.reason(run), sr.MAX_ROUNDS)
        self.assertEqual([s["reason"] for s in run.state["stop_reasons"]], [sr.MAX_ROUNDS])
        [entry] = self.info(run)
        self.assertEqual((entry["not_run"], entry["round"], entry["stop_reason"]),
                         (reason, 1, sr.MAX_ROUNDS))
        self.assertNotIn("info_pending", run.state["intent"])
        self.assertIn(f"**Not run:** `{reason}` at the step", self.report(run))
        self.assertIn("The stop reason stands.", self.report(run))
        self.assertEqual(self.log_actions()[-1], "completed")
        # Still resumable as max-rounds, from the last review round.
        self.assertEqual(run.state["step"], "review-R1")
        self.assertTrue((run.run_dir / "start-snapshot.json").is_file())
        return entry

    def test_rejection_an_injected_usage_limit_is_a_note(self):
        run = self.capped(inject="intent-info")
        entry = self.assertNoted(run, sr.USAGE_LIMIT)
        self.assertIn("injected", entry["detail"]["detail"])

    def test_rejection_a_reading_over_the_ceiling_before_the_step_is_a_note(self):
        def during_review():
            self.h.readings[:] = [reading(85)]
        self.h.during_review = during_review
        run = self.capped()
        self.assertNoted(run, sr.USAGE_LIMIT)
        self.assertEqual(self.h.intent_calls, [], "the session never started")

    def test_rejection_a_failed_session_is_a_note(self):
        self.h.intent_replies = [RuntimeError("the session broke")]
        run = self.capped()
        entry = self.assertNoted(run, sr.ERROR)
        self.assertIn("the session broke", entry["detail"]["error"])

    def test_rejection_a_refused_reply_is_a_note_with_its_transcript_kept(self):
        self.h.intent_replies = [lambda p: {"verdict": "fine"}]
        run = self.capped()
        entry = self.assertNoted(run, sr.ERROR)
        self.assertIn("ReplyError", entry["detail"]["error"])
        self.assertTrue((run.run_dir / "transcripts" / "intent-info-R1.json").is_file())


class OtherEndsTests(InfoCase):
    """No information check at any other end; the "Not checked for intent" line at
    every end but ``clean`` and ``intent-failed``."""

    def test_negative_out_of_scope_gets_the_line_and_no_check(self):
        self.h.builder_script = [edit(self.root, "x\n"),
                                 reply(actions(("R1-1", "declined-scope")))]
        self.h.reviews = [[finding()]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.OUT_OF_SCOPE)
        self.assertEqual(self.h.intent_calls, [])
        self.assertEqual(self.info(run), [])
        report = self.report(run)
        self.assertIn("Not checked for intent: the run ended `out-of-scope` before the "
                      "final intent check.", report)
        self.assertNotIn("--resume", report.split("Not checked for intent")[1]
                         .split("\n")[0], "out-of-scope cannot be resumed")
        self.assertNotIn("information; the stop reason stands", report)
        self.assertEqual(self.report_json(run)["intent_not_checked"]["stop_reason"],
                         sr.OUT_OF_SCOPE)

    def test_negative_disagreement_decided_in_a_review_round_gets_no_check(self):
        # The one other stop a review round decides itself (an out-of-scope stop is
        # decided by the fix pass): a declined finding raised again.
        self.h.builder_script = [edit(self.root, "x\n"),
                                 reply(actions(("R1-1", "declined-merits")))]
        self.h.reviews = [[finding()], [finding(reraises="R1-1")]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.DISAGREEMENT)
        self.assertEqual(self.h.intent_calls, [])
        self.assertEqual(self.info(run), [])
        self.assertIn("Not checked for intent: the run ended `disagreement`",
                      self.report(run))

    def test_negative_a_usage_limit_gets_the_line_with_the_resume(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start(inject="build")
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(self.h.intent_calls, [])
        line = [l for l in self.report(run).splitlines()
                if l.startswith("Not checked for intent")]
        self.assertEqual(len(line), 1)
        self.assertIn("`usage-limit`", line[0])
        self.assertIn(f"--resume {run.state['run_id']}", line[0])
        self.assertIn("Finishing by hand", line[0])

    def test_positive_the_line_is_on_the_opening_lines(self):
        run = self.capped()
        lines = self.report(run).splitlines()
        stop = lines.index(f"**Stop reason:** `{sr.MAX_ROUNDS}`")
        line = next(i for i, l in enumerate(lines) if l.startswith("Not checked"))
        self.assertLess(line, stop + 5)
        self.assertLess(line, lines.index(MAX_HEADING), "the line stays above the check")
        data = self.report_json(run)
        self.assertEqual(data["intent_not_checked"]["stop_reason"], sr.MAX_ROUNDS)
        self.assertEqual(len(data["intent_info"]), 1)

    def test_negative_clean_has_no_line(self):
        self.h.builder_script = [edit(self.root, "done\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.CLEAN)
        self.assertNotIn("Not checked for intent", self.report(run))
        self.assertIsNone(self.report_json(run)["intent_not_checked"])
        self.assertEqual(self.report_json(run)["intent_info"], [])
        self.assertEqual(len(self.h.intent_calls), 1, "the final check only")

    def test_negative_intent_failed_has_no_line(self):
        self.h.intent_replies = [lambda p: intent_reply(p, undelivered=["the summary"])]
        self.h.builder_script = [edit(self.root, "done\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.INTENT_FAILED)
        self.assertNotIn("Not checked for intent", self.report(run))
        self.assertIsNone(self.report_json(run)["intent_not_checked"])

    def test_positive_the_line_names_each_end_with_its_next_step(self):
        for reason in sr.STOP_REASONS:
            line = loop.not_checked_line(reason, "r-1")
            if reason in (sr.CLEAN, sr.INTENT_FAILED):
                self.assertIsNone(line, reason)
                continue
            self.assertTrue(line.startswith(f"Not checked for intent: the run ended "
                                            f"`{reason}` before the final intent check."),
                            reason)
            self.assertEqual("--resume r-1" in line, reason in sr.RESUMABLE, reason)

    def test_positive_inject_limit_names_intent_info(self):
        self.assertTrue(loop.STEP.match("intent-info"))
        with self.assertRaises(loop.RunRefused) as caught:
            self.start(inject="intent-nothing")
        self.assertIn("intent-info", " ".join(caught.exception.args[0]))


if __name__ == "__main__":
    unittest.main()
