#!/usr/bin/env python3
"""Hermetic tests for a stop and resume at A4's final intent check (plan 10.6 and the
10.5 resume rule): ``intent-final`` is a step in usage readings, paid-step permission
and ``state.json``, on the provider that did not build, and a stop there (an error or a
usage limit) resumes there with no builder turn, no review round and no acceptance run.

Whole runs are driven with the fixtures' fakes in a temporary repository. No test
starts Docker, a model session, a Claude probe or a Codex app server, and none needs
``--allow-paid`` to pass.

    python workflows/review-orchestration/tests/test_intent_resume.py
"""

import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_fixtures import *  # noqa: E402,F401,F403 - the loop suites' shared fixtures


class ResumeCase(LoopCase):

    def stopped(self, script=None, inject=None):
        """A run whose build is clean, stopped at the final intent check."""
        self.h.builder_script = [edit(self.root, "done\n")]
        self.h.reviews = [[]]
        if script is not None:
            self.h.intent_replies = [script]
        return self.start(inject=inject)

    def counts(self):
        return (len(self.h.builders), len(self.h.review_calls), len(self.h.check_calls),
                len(self.h.intent_calls))

    def intent_readings(self, run):
        return [r for r in run.state["readings"] if r["step"] == "intent-final"]


class StopAndResumeTests(ResumeCase):

    def test_positive_a_clean_round_leaves_the_next_step_intent_final(self):
        run = self.stopped(RuntimeError("session failed"))
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertEqual(run.state["step"], "review-R1", "the round is the last step")
        self.assertEqual(run.next_step(), "intent-final")
        saved = runrecord.read_state(run.run_dir)
        self.assertEqual(saved["intent"]["final_round"], 1)
        self.assertEqual(saved["acceptance_exits"]["R1"][0]["exit"], 0)
        self.assertEqual(loop.next_step_name(run), "intent-final")
        self.assertEqual(loop.next_providers(run), ("codex",))

    def test_positive_a_failed_session_resumes_at_intent_final_only(self):
        run = self.stopped(RuntimeError("session failed"))
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertEqual(run.state["stop_reasons"][-1]["evidence"]["step"],
                         "intent-final")
        before, readings = self.counts(), len(self.intent_readings(run))
        again = self.resume(run)
        self.assertEqual(self.reason(again), sr.CLEAN)
        builders, reviews, checks, intents = self.counts()
        self.assertEqual((reviews, checks), before[1:3], "no review round, no checks")
        self.assertEqual(intents, before[3] + 1)
        self.assertEqual(builders, before[0], "no builder session, so no builder turn")
        self.assertEqual(len(self.intent_readings(again)), readings + 1)
        self.assertEqual([s["reason"] for s in again.state["stop_reasons"]],
                         [sr.ERROR, sr.CLEAN])

    def test_positive_a_usage_limit_resumes_at_intent_final(self):
        run = self.stopped(limits.UsageLimitError("codex", "limit reached", 99))
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        evidence = run.state["stop_reasons"][-1]["evidence"]
        self.assertEqual((evidence["step"], evidence["resets_at"]), ("intent-final", 99))
        again = self.resume(run)
        self.assertEqual(self.reason(again), sr.CLEAN)
        self.assertEqual(len(self.h.review_calls), 1)

    def test_positive_a_reading_over_the_ceiling_stops_before_the_session(self):
        # Three readings before it: the approval's, the build's and the review's.
        self.h.readings = [reading(), reading(), reading(), reading(99, resets=500)]
        run = self.stopped()
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(run.state["stop_reasons"][-1]["evidence"]["step"],
                         "intent-final")
        self.assertEqual(self.h.intent_calls, [])
        self.assertEqual(self.reason(self.resume(run)), sr.CLEAN)

    def test_positive_the_test_limit_switch_names_intent_final(self):
        self.assertTrue(loop.STEP.match("intent-final"))
        run = self.stopped(inject="intent-final")
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertIn("injected", run.state["stop_reasons"][-1]["evidence"]["detail"])
        self.assertEqual(self.reason(self.resume(run)), sr.CLEAN)

    def test_positive_allow_paid_permits_exactly_intent_final(self):
        run = self.stopped(RuntimeError("session failed"))
        again = loop.load(run.state["run_id"], root=self.root, deps=self.h.deps(),
                          log_path=self.log, runs_dir=self.runs)
        loop.resume(again, allow_paid=True)
        self.assertEqual(again.state["paid_usage_pending"], "intent-final")
        self.assertEqual(again.state["paid_usage"][-1]["step"], "intent-final")
        asyncio.run(again.advance())
        self.assertEqual(self.reason(again), sr.CLEAN)
        self.assertNotIn("paid_usage_pending", again.state)

    def test_positive_codex_building_resumes_on_claude_with_its_probe(self):
        self.h.swapped = True
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.h.frozen = Path(folder.name).resolve() / "frozen"
        self.h.frozen.mkdir()
        (self.h.frozen / "codex_hook.py").write_bytes(b"rules\n")
        run = self.stopped(RuntimeError("session failed"))
        self.assertEqual(self.reason(run), sr.ERROR)
        probes = len(self.h.probe_calls)
        again = self.resume(run)
        self.assertEqual(self.reason(again), sr.CLEAN)
        self.assertEqual(len(self.h.probe_calls), probes + 1, "one probe, before it")
        self.assertEqual([c["provider"] for c in self.h.intent_calls],
                         ["claude", "claude"])


if __name__ == "__main__":
    unittest.main()
