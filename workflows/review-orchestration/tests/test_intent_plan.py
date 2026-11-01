#!/usr/bin/env python3
"""Hermetic tests for A4's first intent check, step ``intent-plan`` (plan 10.6 and
10.3 step 5): it runs after a clean plan review and before the approval pause, on the
provider that did not build, its result goes into ``plan-report.md``, and it never ends
the run.

Every provider, Docker and git interaction is the harness's fake or the temporary
repositories the fixtures build. No test starts Docker, a model session, a Claude probe
or a Codex app server, and none needs ``--allow-paid`` to pass.

    python workflows/review-orchestration/tests/test_intent_plan.py
"""

import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_fixtures import *  # noqa: E402,F401,F403 - the loop suites' shared fixtures


class IntentPlanCase(LoopCase):

    def planning_run(self, builder="claude"):
        settings = settings_mod.assign_roles(settings_mod.load_settings(), builder)
        if builder == "codex":
            self.h.swapped = True
        return loop.start("brief.md", "demo", str(self.personal), root=self.root,
                          deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                          settings=settings, builder=builder)

    def advance(self, run):
        with redirect_stderr(io.StringIO()):
            asyncio.run(planning.advance(run))
        return run

    def assertPaused(self, run):
        self.assertEqual(run.state.get("pause"), sr.AWAITING_PLAN_APPROVAL,
                         json.dumps(run.state.get("stop_reasons"))[-1500:])

    def report(self, run):
        return (run.run_dir / planning.PLAN_REPORT).read_text(encoding="utf-8")

    def load(self, run):
        return loop.load(run.state["run_id"], root=self.root, deps=self.h.deps(),
                         log_path=self.log, runs_dir=self.runs)


class IntentPlanTests(IntentPlanCase):

    def test_positive_a_clean_plan_review_leads_to_the_check_then_the_pause(self):
        run = self.advance(self.planning_run())
        self.assertPaused(run)
        completed = planning.planning_state(run)["completed"]
        self.assertEqual(completed[-2:], ["plan-review-R1", "intent-plan"])
        self.assertEqual([c["provider"] for c in self.h.intent_calls], ["codex"])
        # Recorded as a step on the other provider, with its usage reading.
        self.assertIn("intent-plan", [r["step"] for r in run.state["readings"]])
        step = [s for s in run.state["steps"] if s["step"] == "intent-plan"][0]
        self.assertEqual((step["role"], step["provider"]), ("intent_checker", "codex"))
        self.assertTrue((run.run_dir / "transcripts" / "intent-plan.json").is_file())
        report = self.report(run)
        self.assertIn("## Intent check", report)
        self.assertNotIn("No intent check has run", report)
        self.assertIn("- met: work/a.txt says done.", report)

    def test_positive_codex_building_puts_the_check_on_claude(self):
        run = self.advance(self.planning_run("codex"))
        self.assertPaused(run)
        self.assertEqual([c["provider"] for c in self.h.intent_calls], ["claude"])
        # A Claude step: the usage probe is sent before it.
        self.assertIn("intent-plan", [r["step"] for r in run.state["readings"]])

    def test_positive_the_payload_holds_only_the_brief_and_the_merged_plan(self):
        self.h.planner_replies = {"claude": plan_reply("PLANTED-PLAN-ONE"),
                                  "codex": plan_reply("PLANTED-PLAN-TWO")}
        self.h.synthesis = synthesis_reply(agreements=["PLANTED-SYNTHESIS-NOTE"])
        run = self.advance(self.planning_run())
        self.assertPaused(run)
        prompt = self.h.intent_calls[0]["prompt"]
        self.assertIn(BRIEF.split("\n")[1], prompt)
        self.assertIn("The merged plan.", prompt)
        for planted in ("PLANTED-PLAN-ONE", "PLANTED-PLAN-TWO", "PLANTED-SYNTHESIS-NOTE"):
            self.assertNotIn(planted, prompt)
        self.assertIn("1. (Goal) work/a.txt says done.", prompt)
        self.assertIn("2. (Constraints) Touch nothing else.", prompt)

    def test_positive_a_planted_out_of_scope_file_and_missed_goal_are_shown(self):
        # Out of scope names the committed brief, which exists; the merged plan names it
        # in its files list, and the checker says the goal is not met. The run still
        # pauses: the first check never ends the run or starts a revision.
        brief_text = BRIEF.replace("## Out of scope\n- None.",
                                   "## Out of scope\n- `brief.md` and `no-such-thing`.")
        (self.root / "brief.md").write_bytes(brief_text.encode("utf-8"))
        git(self.root, "commit", "-qam", "brief")
        merged = {k: v for k, v in plan_reply(summary="The merged plan.").items()
                  if k != "open_questions"}
        merged["steps"].append({"action": "Edit the brief.", "files": ["brief.md"],
                                "verification": "Read it."})
        self.h.synthesis = synthesis_reply(merged_plan=merged)
        self.h.intent_replies = [lambda p: intent_reply(p, statuses=["not met", "met"])]
        run = self.advance(self.planning_run())
        self.assertPaused(run)
        result = planning.planning_state(run)["intent"]
        problem, = result["mechanical"]["path_problems"]
        self.assertEqual(problem["path"], "brief.md")
        self.assertEqual(problem["reasons"], ["outside the edit paths",
                                              "inside the Out of scope path `brief.md`"])
        report = self.report(run)
        self.assertIn("`brief.md`: outside the edit paths; inside the Out of scope", report)
        self.assertIn("- not met: work/a.txt says done.", report)
        self.assertIn("`no-such-thing` (Out of scope, line", report)
        self.assertIn("not checked by the script: no such path", report)
        self.assertEqual([c["role"] for c in self.h.planning_calls].count("synthesiser"), 1)

    def test_positive_cannot_tell_is_listed_and_the_run_pauses(self):
        self.h.intent_replies = [lambda p: intent_reply(p, status="cannot tell")]
        run = self.advance(self.planning_run())
        self.assertPaused(run)
        self.assertEqual(len(planning.planning_state(run)["intent"]["cannot_tell"]), 2)
        self.assertIn("Cannot tell (not a failure): 2 line(s)", self.report(run))

    def test_rejection_a_refused_reply_stops_resumably_and_keeps_its_transcript(self):
        bad = [lambda p: {**intent_reply(p), "lines": intent_reply(p)["lines"][:1]},
               lambda p: {**intent_reply(p), "lines": [
                   dict(entry, evidence=" ") for entry in intent_reply(p)["lines"]]},
               {"lines": [], "unasked": []}]
        for script in bad:
            with self.subTest(script=str(script)[:40]):
                self.setUp()
                self.h.intent_replies = [script]
                run = self.advance(self.planning_run())
                self.assertEqual(self.reason(run), sr.ERROR)
                self.assertEqual(run.state["stop_reasons"][-1]["evidence"]["step"],
                                 "intent-plan")
                self.assertTrue((run.run_dir / "transcripts" / "intent-plan.json")
                                .is_file())
                self.assertNotIn("intent", planning.planning_state(run))
                self.assertFalse(self.log.exists(), "no LOG.md entry for a resumable end")

    def test_rejection_a_failed_session_stops_resumably_then_resume_runs_only_it(self):
        self.h.intent_replies = [RuntimeError("session failed")]
        run = self.advance(self.planning_run())
        self.assertEqual(self.reason(run), sr.ERROR)
        reviews = [c["role"] for c in self.h.planning_calls].count("plan_reviewer")
        again = self.load(run)
        with redirect_stderr(io.StringIO()):
            planning.resume(again)
        self.advance(again)
        self.assertPaused(again)
        self.assertEqual([c["role"] for c in self.h.planning_calls].count("plan_reviewer"),
                         reviews, "no new plan-review round")
        self.assertEqual(len(self.h.intent_calls), 2)

    def test_rejection_a_usage_limit_stops_then_resume_runs_only_it(self):
        self.h.intent_replies = [limits.UsageLimitError("codex", "limit reached", 1234)]
        run = self.advance(self.planning_run())
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(run.state["stop_reasons"][-1]["evidence"]["step"], "intent-plan")
        self.assertEqual(planning.next_step_name(run), "intent-plan")
        self.assertEqual(loop.next_providers(run), ("codex",))
        again = self.load(run)
        with redirect_stderr(io.StringIO()):
            planning.resume(again)
        self.advance(again)
        self.assertPaused(again)
        self.assertEqual(len(self.h.intent_calls), 2)

    def test_positive_allow_paid_permits_exactly_the_intent_step(self):
        self.h.intent_replies = [RuntimeError("session failed")]
        run = self.advance(self.planning_run())
        again = self.load(run)
        with redirect_stderr(io.StringIO()):
            planning.resume(again, allow_paid=True)
        self.assertEqual(again.state["paid_usage_pending"], "intent-plan")
        self.assertEqual(again.state["paid_usage"][-1]["step"], "intent-plan")
        self.advance(again)
        self.assertPaused(again)
        self.assertNotIn("paid_usage_pending", again.state)

    def test_positive_an_amendment_reruns_the_check_on_the_revised_edit_paths(self):
        run = self.advance(self.planning_run())
        self.assertPaused(run)
        # The revised brief allows only other/, so the merged plan's work/a.txt is now
        # outside the edit paths, while state.json still holds the original work/.
        revised = BRIEF.replace("- work/ | Measure: git ls-files work",
                                "- other/ | Measure: git ls-files other")
        (self.root / "revised.md").write_bytes(revised.encode("utf-8"))
        with redirect_stderr(io.StringIO()):
            loop.amend(run, "revised.md")
        self.assertNotIn("intent", planning.planning_state(run))
        self.advance(run)
        self.assertPaused(run)
        self.assertEqual(run.state["edit_paths"], ["work/"])
        problem, = planning.planning_state(run)["intent"]["mechanical"]["path_problems"]
        self.assertEqual((problem["path"], problem["reasons"]),
                         ("work/a.txt", ["outside the edit paths"]))
        self.assertEqual(len(self.h.intent_calls), 2)

    def test_positive_an_amendment_adding_an_edit_path_accepts_the_newly_allowed_file(self):
        merged = {k: v for k, v in plan_reply(summary="The merged plan.").items()
                  if k != "open_questions"}
        merged["steps"].append({"action": "Add a note.", "files": ["notes/n.md"],
                                "verification": "Read it."})
        self.h.synthesis = synthesis_reply(merged_plan=merged)
        run = self.advance(self.planning_run())
        first, = planning.planning_state(run)["intent"]["mechanical"]["path_problems"]
        self.assertEqual(first["path"], "notes/n.md")
        revised = BRIEF.replace("- work/ | Measure: git ls-files work",
                                "- work/ | Measure: git ls-files work\n"
                                "- notes/ | Measure: git ls-files notes")
        (self.root / "revised.md").write_bytes(revised.encode("utf-8"))
        with redirect_stderr(io.StringIO()):
            loop.amend(run, "revised.md")
        self.advance(run)
        self.assertEqual(run.state["edit_paths"], ["work/"])
        self.assertEqual(planning.planning_state(run)["intent"]["mechanical"]
                         ["path_problems"], [])

    def test_negative_the_plan_check_runs_no_acceptance_command(self):
        run = self.advance(self.planning_run())
        self.assertPaused(run)
        self.assertEqual(self.h.check_calls, [])
        self.assertNotIn("acceptance", planning.planning_state(run)["intent"]["mechanical"])


if __name__ == "__main__":
    unittest.main()
