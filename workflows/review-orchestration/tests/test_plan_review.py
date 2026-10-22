#!/usr/bin/env python3
"""Hermetic tests for A3 chunk 3's plan review (plan 10.3 step 4), with the parts of
step 6 and "The approved plan in the build" it reaches.

What is under test: ``planning.py``'s plan-review round and the synthesiser's revision
(``review_round``, ``revise``, ``next_step``), ``stopreasons.parse_plan_actions``, the
labels running on from the planning half into the build (``loop.Run.next_step`` and
``label_offset``, the build's round cap counting its own rounds), an amendment
restarting planning with the round count reset (``loop.amend``,
``loop.restart_planning``), the plan-review findings in the run's one ledger, rendered to
``runs/<run-id>/plan-review/packet.md`` and ``plan-report.md`` during planning and to
the item's packet in ``memory/`` from approval on, and the approved merged plan in the
builder's, the fix pass's and the reviewer's prompts.

Every session is the harness's fake (``loop_fixtures.FakePlanningSession`` for the
planning half), in a temporary project that is a real git repository with a second
repository standing in for the personal one. No test starts Docker, a model session, a
Claude probe or a Codex app server. Same three controls as the other suites: a positive
control must work, a rejection control must end or refuse the run with the reason named
and change nothing it should not, a negative control must not.

    python workflows/review-orchestration/tests/test_plan_review.py
"""

import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_fixtures import *  # noqa: E402,F401,F403 - the loop suites' shared fixtures


class PlanCase(LoopCase):
    """A run started as ``start.py --run`` starts one since A3 chunk 3, its planning
    half driven by the harness's fake sessions."""

    def started(self, plan_cap=2, cap=3, builder="claude", item="demo"):
        settings = json.loads(json.dumps(self.settings))
        settings["round_caps"]["plan_review"] = plan_cap
        settings["round_caps"]["build_review"] = cap
        return loop.start("brief.md", item, str(self.personal), root=self.root,
                          deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                          settings=settings, builder=builder)

    def plan(self, run):
        asyncio.run(planning.advance(run))
        return run

    def load(self, run):
        return loop.load(run.state["run_id"], root=self.root, deps=self.h.deps(),
                         log_path=self.log, runs_dir=self.runs)

    def reviews_of(self, role="plan_reviewer"):
        return [c for c in self.h.planning_calls if c["role"] == role]

    def revisions(self):
        return [c for c in self.h.planning_calls if c["role"] == "synthesiser"
                and "actions" in c["schema"]["properties"]]

    def approve(self, run):
        again = self.load(run)
        with redirect_stderr(io.StringIO()):
            loop.approve(again)
        return again

    def packet_text(self, item="demo"):
        return packet.packet_path(self.root, item).read_text(encoding="utf-8")


# ------------------------------------------------------------------ the loop


class PlanReviewLoopTests(PlanCase):
    """Plan 10.3 step 4: the plan reviewer reviews the merged plan, the synthesiser
    revises, until a round finds nothing or the cap is reached."""

    def test_positive_a_clean_first_review_pauses_for_approval(self):
        run = self.plan(self.started())
        self.assertEqual((run.state["status"], run.state["pause"]),
                         (sr.PAUSED, sr.AWAITING_PLAN_APPROVAL))
        review, = self.reviews_of()
        # The other provider's session, on its plan_reviewer entry.
        self.assertEqual(review["provider"], "codex")
        self.assertIn("THE MERGED PLAN", review["prompt"])
        self.assertIn("The merged plan.", review["prompt"])
        self.assertIn("work/a.txt says done.", review["prompt"])
        self.assertNotIn("Plan from one planner.", review["prompt"],
                         "the reviewer sees the merged plan, not the two plans")
        self.assertEqual(review["schema"]["required"], ["summary", "findings"])
        self.assertEqual(run.state["label_rounds"], 1)
        self.assertEqual(planning.planning_state(run)["review_rounds"], 1)
        self.assertFalse(self.log.exists())

    def test_positive_findings_are_revised_then_a_clean_round_pauses(self):
        self.h.plan_reviews = [[finding("A step names no file")], []]
        run = self.plan(self.started())
        self.assertEqual(run.state["pause"], sr.AWAITING_PLAN_APPROVAL)
        self.assertEqual(planning.planning_state(run)["completed"],
                         ["planners", "identity", "synthesis", "plan-review-R1",
                          "plan-revise-R1", "plan-review-R2"])
        ledger = {f["label"]: f for f in run.state["ledger"]}
        self.assertEqual(list(ledger), ["R1-1"])
        self.assertEqual((ledger["R1-1"]["action"], ledger["R1-1"]["source"]),
                         (sr.FIXED, sr.REVIEWER))
        revision, = self.revisions()
        self.assertEqual(revision["provider"], "claude", "the synthesiser is the builder's")
        self.assertIn("R1-1 (major) A step names no file", revision["prompt"])
        self.assertIn("PLAN 1", revision["prompt"])
        self.assertIn("THE SYNTHESIS SO FAR", revision["prompt"])
        self.assertEqual(revision["schema"], planning.REVISION_SCHEMA)
        # The second review is shown the prior finding with the synthesiser's action.
        second = self.reviews_of()[1]["prompt"]
        self.assertIn("R1-1 (reviewer) A step names no file: synthesiser action fixed",
                      second)

    def test_positive_the_revised_synthesis_is_kept(self):
        self.h.plan_reviews = [[finding()], []]
        merged = {k: v for k, v in plan_reply(summary="The revised plan.").items()
                  if k != "open_questions"}
        self.h.revisions = [revision_reply(merged_plan=merged)]
        run = self.plan(self.started())
        synthesis = planning.planning_state(run)["synthesis"]
        self.assertEqual(synthesis["merged_plan"]["summary"], "The revised plan.")
        self.assertNotIn("actions", synthesis)
        self.assertIn("The revised plan.", (run.run_dir / planning.PLAN_REPORT)
                      .read_text(encoding="utf-8"))

    def test_rejection_the_cap_ends_max_rounds_writing_nothing_in_the_project(self):
        self.h.plan_reviews = [[finding("one")], [finding("two")]]
        run = self.plan(self.started(plan_cap=2))
        self.assertEqual(self.reason(run), sr.MAX_ROUNDS)
        evidence = run.state["stop_reasons"][-1]["evidence"]
        self.assertEqual((evidence["round"], evidence["plan_review_rounds"],
                          evidence["cap"], evidence["half"]), (2, 2, 2, "planning"))
        self.assertEqual(evidence["open"], ["R2-1"])
        self.assertFalse(self.log.exists(), "a resumable planning end writes no entry")
        self.assertFalse(packet.packet_path(self.root, "demo").exists())
        self.assertTrue((run.run_dir / "plan-review" / "packet.md").is_file())
        # The report points to the packet that exists, the run record's (code review
        # R2-1), never the item's packet in memory/, which approval writes first.
        reported = f"runs/{run.state['run_id']}/plan-review/packet.md"
        report = json.loads((run.run_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["packet"], reported)
        self.assertTrue((self.root / report["packet"]).is_file())
        self.assertIn(f"in `{reported}`", (run.run_dir / "report.md")
                      .read_text(encoding="utf-8"))

    def test_positive_resume_with_rounds_revises_then_reviews_up_to_n_more(self):
        self.h.plan_reviews = [[finding("one")], [finding("two")], []]
        run = self.plan(self.started(plan_cap=2))
        self.assertEqual(self.reason(run), sr.MAX_ROUNDS)
        plans = json.loads(json.dumps(planning.planning_state(run)["plans"]))
        again = self.load(run)
        planning.resume(again, rounds=1)
        self.assertEqual(planning.planning_state(again)["round_cap"], 3)
        self.h.planning_calls.clear()
        self.plan(again)
        self.assertEqual(again.state["pause"], sr.AWAITING_PLAN_APPROVAL)
        # The synthesiser acted first on the open finding, then one more review; no
        # planner ran again and both plans were kept.
        self.assertEqual([c["role"] for c in self.h.planning_calls],
                         ["synthesiser", "plan_reviewer"])
        self.assertIn("R2-1", self.revisions()[0]["prompt"])
        self.assertEqual(planning.planning_state(again)["plans"], plans)
        self.assertEqual(planning.planning_state(again)["completed"][-2:],
                         ["plan-revise-R2", "plan-review-R3"])

    def test_rejection_a_plain_resume_after_the_cap_is_refused(self):
        # Code review R1-1: more plan-review rounds are authorised only by --rounds N,
        # so a plain resume after a cap stop is refused, starting no session and
        # changing nothing.
        self.h.plan_reviews = [[finding("one")], [finding("two")], [finding("three")]]
        run = self.plan(self.started(plan_cap=2))
        before = {p.relative_to(run.run_dir).as_posix(): p.read_bytes()
                  for p in sorted(run.run_dir.rglob("*")) if p.is_file()}
        self.h.planning_calls.clear()
        with redirect_stderr(io.StringIO()), \
                self.assertRaisesRegex(loop.RunRefused, "--rounds N"):
            planning.resume(self.load(run))
        self.assertEqual(self.h.planning_calls, [], "no synthesiser or reviewer session")
        after = {p.relative_to(run.run_dir).as_posix(): p.read_bytes()
                 for p in sorted(run.run_dir.rglob("*")) if p.is_file()}
        self.assertEqual(after, before)
        with redirect_stdout(io.StringIO()):
            code = run_py.main(["--resume", run.state["run_id"]], root=self.root,
                               log_path=self.log, deps=self.h.deps(), runs_dir=self.runs)
        self.assertEqual(code, run_py.REFUSED)
        self.assertEqual(self.h.planning_calls, [])
        self.assertEqual(planning.planning_state(self.load(run))["review_rounds"], 2)

    def test_rejection_a_reraise_of_a_merits_decline_ends_disagreement(self):
        self.h.plan_reviews = [[finding("one")], [finding("again", reraises="R1-1")]]
        self.h.revisions = [revision_reply(action=sr.DECLINED_MERITS)]
        run = self.plan(self.started(plan_cap=3))
        self.assertEqual(self.reason(run), sr.DISAGREEMENT)
        # An end no approval can follow writes its entry, as stopped-by-user does, and
        # still no packet in memory/.
        self.assertIn(f"Run {run.state['run_id']} ended disagreement",
                      self.log.read_text(encoding="utf-8"))
        self.assertFalse(packet.packet_path(self.root, "demo").exists())

    def test_positive_a_merits_decline_not_raised_again_is_accepted(self):
        self.h.plan_reviews = [[finding("one")], []]
        self.h.revisions = [revision_reply(action=sr.DECLINED_MERITS)]
        run = self.plan(self.started())
        self.assertEqual(run.state["pause"], sr.AWAITING_PLAN_APPROVAL)
        ledger = {f["label"]: f for f in run.state["ledger"]}
        self.assertEqual(ledger["R1-1"]["decline_accepted"], 2)
        self.assertTrue(packet.is_addressed(ledger["R1-1"]))

    def test_rejection_a_failed_plan_review_ends_error_and_resumes_at_that_round(self):
        self.h.plan_reviews = [providers.ProviderError("plan reviewer down"), []]
        run = self.plan(self.started())
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertIn("plan-review-R1", json.dumps(run.state["stop_reasons"][-1]))
        self.assertFalse(self.log.exists())
        again = self.load(run)
        planning.resume(again)
        self.plan(again)
        self.assertEqual(again.state["pause"], sr.AWAITING_PLAN_APPROVAL)
        self.assertEqual(again.state["label_rounds"], 1, "the failed round's label reused")

    def test_rejection_a_usage_limit_before_a_review_round(self):
        # The plan review is a Codex step for a Claude builder: Codex over the ceiling
        # stops it before it starts. Readings: start, two planners, synthesis, review.
        self.h.readings = [reading(), reading(), reading(), reading(), reading(95)]
        run = self.plan(self.started())
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(run.state["stop_reasons"][-1]["evidence"]["step"],
                         "plan-review-R1")
        self.assertEqual(self.reviews_of(), [])


# ------------------------------------------------------- the synthesiser's actions


class RevisionActionTests(PlanCase):
    """Plan 10.3 step 4: every open finding exactly once, each fixed or declined-merits
    with a note; declined-scope, any other value or a missing finding ends ``error``."""

    def broken(self, actions):
        self.h.plan_reviews = [[finding("one"), finding("two")], []]
        self.h.revisions = [lambda prompt: {**synthesis_reply(), "actions": actions}]
        run = self.plan(self.started())
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertFalse(self.log.exists())
        return run.state["stop_reasons"][-1]["evidence"]["error"]

    def act(self, label, action="fixed", note="n"):
        return {"label": label, "action": action, "note": note}

    def test_rejection_declined_scope_has_no_meaning_before_a_build(self):
        error = self.broken([self.act("R1-1", sr.DECLINED_SCOPE), self.act("R1-2")])
        self.assertIn("declined-scope", error)
        self.assertIn("no meaning before a build", error)

    def test_rejection_any_other_value(self):
        error = self.broken([self.act("R1-1", "maybe"), self.act("R1-2")])
        self.assertIn("'maybe'", error)
        self.assertIn("fixed, declined-merits", error)

    def test_rejection_a_missing_finding(self):
        error = self.broken([self.act("R1-1")])
        self.assertIn("gave no action for: R1-2", error)

    def test_rejection_a_finding_given_twice_or_not_open(self):
        self.assertIn("more than one action",
                      self.broken([self.act("R1-1"), self.act("R1-1"), self.act("R1-2")]))
        self.setUp()
        self.assertIn("not an open finding",
                      self.broken([self.act("R1-1"), self.act("R1-2"), self.act("R9-9")]))

    def test_rejection_a_revision_breaking_the_synthesis_schema(self):
        self.h.plan_reviews = [[finding()], []]
        self.h.revisions = [lambda prompt: {
            **{k: v for k, v in synthesis_reply().items() if k != "agreements"},
            "actions": [self.act("R1-1")]}]
        run = self.plan(self.started())
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertIn("must hold exactly", run.state["stop_reasons"][-1]["evidence"]["error"])

    def test_positive_parse_plan_actions_takes_both_allowed_actions(self):
        got = sr.parse_plan_actions([self.act("R1-1"), self.act("R1-2",
                                                                sr.DECLINED_MERITS, "no")],
                                    ["R1-1", "R1-2"])
        self.assertEqual(got, {"R1-1": (sr.FIXED, "n"), "R1-2": (sr.DECLINED_MERITS, "no")})
        with self.assertRaises(sr.ReplyError):
            sr.parse_plan_actions("not a list", ["R1-1"])
        with self.assertRaises(sr.ReplyError):
            sr.parse_plan_actions([{"label": "R1-1", "action": "fixed"}], ["R1-1"])


# ---------------------------------------------- labels, the packet and the build


class LabelTests(PlanCase):
    """Plan 10.3 step 4: labels run on across both halves; each half's round count is
    kept apart from the label number."""

    def test_positive_the_builds_rounds_carry_on_from_the_plan_reviews_labels(self):
        self.h.plan_reviews = [[finding("plan gap")], []]
        run = self.plan(self.started(cap=1))
        approved = self.approve(run)
        self.assertEqual(approved.state["label_offset"], 2)
        self.assertEqual(approved.next_step(), "build")
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[finding("build gap")]]
        asyncio.run(approved.advance())
        self.assertEqual([s["step"] for s in approved.state["steps"]
                          if s["role"] in ("builder", "reviewer")],
                         ["build", "review-R3"])
        labels = [f["label"] for f in approved.state["ledger"]]
        self.assertEqual(labels, ["R1-1", "R3-1"])
        # The build's cap counts its own rounds: one round at cap 1 is the cap, though
        # its label is R3.
        self.assertEqual(self.reason(approved), sr.MAX_ROUNDS)
        self.assertEqual(approved.state["stop_reasons"][-1]["evidence"]["label"], "R3")

    def test_positive_resume_rounds_count_the_builds_own_rounds(self):
        self.h.plan_reviews = [[finding("plan gap")], []]
        run = self.plan(self.started(cap=1))
        approved = self.approve(run)
        self.h.builder_script = [edit(self.root, "x\n"), reply(actions(("R3-1", "fixed")))]
        self.h.reviews = [[finding("build gap")], []]
        asyncio.run(approved.advance())
        again = self.load(approved)
        loop.resume(again, rounds=1)
        self.assertEqual(again.state["round_cap"], 2, "one build round so far, plus one")
        asyncio.run(again.advance())
        self.assertEqual(self.reason(again), sr.REVIEWED_CLEAN)
        self.assertEqual([s["step"] for s in again.state["steps"]
                          if s["role"] in ("builder", "reviewer")],
                         ["build", "review-R3", "fix-R3", "review-R4"])

    def test_positive_an_amendment_resets_the_round_count_and_labels_carry_on(self):
        self.h.plan_reviews = [[finding("one")], []]
        run = self.plan(self.started())
        self.assertEqual(planning.planning_state(run)["review_rounds"], 2)
        # An open plan-review finding at the pause (one a test plants), to show it moves.
        run.ledger.add_round(3, [], [finding("still open")])
        run.state["label_rounds"] = 3
        run.save()
        again = self.load(run)
        (self.root / "revised.md").write_bytes(BRIEF.replace(
            "- work/a.txt says done.", "- work/a.txt says done twice.").encode("utf-8"))
        superseded = loop.amend(again, "revised.md")
        self.assertEqual(superseded, ["R3-1"])
        ledger = {f["label"]: f for f in again.state["ledger"]}
        self.assertTrue(packet.is_addressed(ledger["R3-1"]))
        self.assertEqual(ledger["R3-1"]["note"], loop.SUPERSEDED_NOTE)
        planning_record = planning.planning_state(again)
        self.assertEqual((planning_record["completed"], planning_record["review_rounds"]),
                         ([], 0))
        self.assertEqual(again.state["status"], sr.RUNNING)
        self.h.plan_reviews = [[]]
        self.h.planning_calls.clear()
        self.plan(again)
        self.assertEqual(again.state["pause"], sr.AWAITING_PLAN_APPROVAL)
        self.assertEqual(planning.planning_state(again)["completed"][-1], "plan-review-R4")
        self.assertEqual(planning.planning_state(again)["review_rounds"], 1)
        self.assertIn("work/a.txt says done twice.",
                      self.reviews_of()[0]["prompt"], "reviewed against the revised brief")
        report = (again.run_dir / planning.PLAN_REPORT).read_text(encoding="utf-8")
        self.assertIn("R3-1 (addressed", report)
        self.assertIn("Superseded by amendment.", report)

    def test_negative_a_run_with_no_planning_half_has_no_offset(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(run.label_offset(), 0)
        self.assertEqual([s["step"] for s in run.state["steps"]], ["build", "review-R1"])


class PacketTests(PlanCase):
    """Plan 10.3 step 6, "The run writes nothing in the project before approval": the
    findings stay in the run's ledger, rendered in the run record, until approval writes
    the item's packet, and every later render holds both halves."""

    def test_positive_plan_review_findings_stay_in_the_run_record(self):
        self.h.plan_reviews = [[finding("plan gap")], []]
        run = self.plan(self.started())
        self.assertEqual([f["label"] for f in run.state["ledger"]], ["R1-1"])
        rendered = (run.run_dir / "plan-review" / "packet.md").read_text(encoding="utf-8")
        self.assertIn("### R1-1 - plan gap", rendered)
        self.assertIn("## Addressed", rendered)
        report = (run.run_dir / planning.PLAN_REPORT).read_text(encoding="utf-8")
        self.assertIn("## Plan review findings", report)
        self.assertIn("R1-1 (addressed, major) plan gap", report)
        self.assertIn("Synthesiser: fixed.", report)
        self.assertIn("Plan-review rounds for this plan: 2 of 2", report)
        self.assertIn("No intent check has run", report)
        self.assertFalse(packet.packet_path(self.root, "demo").exists())

    def test_positive_approval_writes_the_packet_and_later_renders_hold_both_halves(self):
        self.h.plan_reviews = [[finding("plan gap")], []]
        run = self.plan(self.started())
        approved = self.approve(run)
        first = self.packet_text()
        self.assertIn("### R1-1 - plan gap", first)
        self.h.builder_script = [edit(self.root, "x\n"), reply(actions(("R3-1", "fixed")))]
        self.h.reviews = [[finding("build gap")], []]
        asyncio.run(approved.advance())
        later = self.packet_text()
        self.assertIn("### R1-1 - plan gap", later)
        self.assertIn("### R3-1 - build gap", later)
        # From approval on the report points to the item's packet in memory/.
        report = json.loads((approved.run_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["packet"], "memory/demo_review_packet.md")

    def test_rejection_a_run_stopped_at_the_pause_writes_no_packet(self):
        self.h.plan_reviews = [[finding("plan gap")], []]
        run = self.plan(self.started())
        again = self.load(run)
        loop.stop(again)
        self.assertEqual(self.reason(again), sr.STOPPED_BY_USER)
        self.assertFalse(packet.packet_path(self.root, "demo").exists())
        self.assertIn("ended stopped-by-user", self.log.read_text(encoding="utf-8"))


class ApprovedPlanPromptTests(PlanCase):
    """Plan 10.3, "The approved plan in the build": a section of its own after the
    brief in every builder turn's prompt and in the reviewer's."""

    def test_positive_the_plan_is_in_the_builder_fix_and_reviewer_prompts(self):
        merged = {k: v for k, v in plan_reply(summary="Approved: write done.").items()
                  if k != "open_questions"}
        self.h.synthesis = synthesis_reply(merged_plan=merged)
        run = self.plan(self.started())
        approved = self.approve(run)
        self.h.builder_script = [edit(self.root, "x\n"), reply(actions(("R2-1", "fixed")))]
        self.h.reviews = [[finding()], []]
        asyncio.run(approved.advance())
        self.assertEqual(self.reason(approved), sr.REVIEWED_CLEAN)
        builder_prompts = [p for _, p in self.h.builders[0].prompts]
        self.assertEqual(len(builder_prompts), 2)
        for prompt in builder_prompts + [c[0] for c in self.h.review_calls]:
            self.assertIn("THE APPROVED PLAN", prompt)
            self.assertIn("Approved: write done.", prompt)
        build, fix = builder_prompts
        self.assertLess(build.index("THE BRIEF"), build.index("THE APPROVED PLAN"))
        reviewer = self.h.review_calls[0][0]
        self.assertLess(reviewer.index("THE BRIEF"), reviewer.index("THE APPROVED PLAN"))
        self.assertIn("THE OPEN FINDINGS", fix)
        # R4-1: every fix turn carries the brief, before the approved plan.
        self.assertIn(approved.brief_text(), fix)
        self.assertLess(fix.index("THE BRIEF"), fix.index("THE APPROVED PLAN"))
        self.assertLess(fix.index("THE APPROVED PLAN"), fix.index("THE OPEN FINDINGS"))

    def test_negative_a_run_with_no_plan_says_so(self):
        run = SimpleNamespace(state={})
        self.assertEqual(loop.Run.approved_plan(run), "None: this run has no approved plan.")


class PlanReviewerSessionTests(unittest.TestCase):
    """The plan reviewer's session (step 4 and step 7): the reviewer provider's
    read-only session class on its plan_reviewer entry, with the reviewers' lockdown."""

    def test_positive_the_reviewer_provider_and_its_plan_reviewer_entry(self):
        for builder in ("claude", "codex"):
            with self.subTest(builder=builder):
                settings = settings_mod.assign_roles(settings_mod.load_settings(), builder)
                settings["models"][settings["roles"]["reviewer"]]["plan_reviewer"] = {
                    "model": "plan-model-x", "effort": "low"}
                session = providers.make_plan_reviewer(settings, cwd=PROJECT,
                                                       frozen_dir=PROJECT)
                reviewer = settings["roles"]["reviewer"]
                self.assertIs(type(session), providers.REVIEWERS[reviewer])
                self.assertEqual((session.model, session.effort), ("plan-model-x", "low"))

    def test_positive_a_claude_plan_reviewer_has_the_reviewers_lockdown(self):
        settings = settings_mod.assign_roles(settings_mod.load_settings(), "codex")
        session = providers.make_plan_reviewer(settings, cwd=PROJECT)
        options = session.options(planning.plan_review_schema(with_pattern=False))
        self.assertEqual((options.mcp_servers, options.strict_mcp_config), ({}, True))
        self.assertIn("disableAllHooks", options.settings)


if __name__ == "__main__":
    unittest.main()
