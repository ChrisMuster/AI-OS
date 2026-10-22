#!/usr/bin/env python3
"""Hermetic tests for A3 chunk 3's live wiring and its two follow-ons (plan 10.3, the
chunk 3 bullet; 10.10 items 1, 3, 5, 7 and 8).

What is under test: ``start.py --run`` (``run.py --run``) beginning with the planning
half and pausing ``awaiting-plan-approval`` with no Docker command but the status query
and no write outside the run record, ``--approve`` continuing the same run into the
build, ``--advance`` and the hand-over for a run in its planning half; each step stopped
only by its own provider's readings, and each preflight checking only the next step's
provider or providers; ``--allow-paid`` in the planning half, the two planners one step;
a user-started ``--resume --wait`` waiting at its preflight in both halves, and acting
only on the stop it waited for; and ``runs/<run-id>/resume.lock``.

Every provider, Docker and git interaction is the harness's fake or the temporary
repositories the fixtures build. No test starts Docker, a model session, a Claude probe
or a Codex app server, and none needs ``--allow-paid`` to pass. Same three controls as
the other suites.

    python workflows/review-orchestration/tests/test_plan_wiring.py
"""

import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_fixtures import *  # noqa: E402,F401,F403 - the loop suites' shared fixtures

OVERAGE = "using paid overage"


def saved_claude(source="reported", percent=95, reason=None, resets_at=None):
    return {"source": source, "reason": reason, "percent": percent,
            "window": "five_hour", "resets_at": resets_at, "read_at": 1}


class WiringCase(LoopCase):

    def call(self, *argv, deps=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = run_py.main(list(argv), root=self.root, log_path=self.log,
                               deps=deps or self.h.deps(), runs_dir=self.runs)
        self.err = err.getvalue()
        return code, out.getvalue()

    def waking_deps(self, during=None):
        """Deps whose sleep moves the harness clock on, as time passes, and can act
        while the process sleeps."""
        deps = self.h.deps()

        def sleep(seconds):
            self.h.slept.append(seconds)
            self.h.clock += seconds
            if during is not None:
                during()
        deps.sleep = sleep
        return deps

    def load(self, run_id):
        return loop.load(run_id, root=self.root, deps=self.h.deps(), log_path=self.log,
                         runs_dir=self.runs)

    def planning_run(self):
        return loop.start("brief.md", "demo", str(self.personal), root=self.root,
                          deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                          settings=self.settings, builder="claude")

    def built(self, inject=None):
        """An approved run, its build ended by an injected limit at ``inject``."""
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = start_built("brief.md", "demo", str(self.personal), root=self.root,
                          deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                          settings=self.settings, builder="claude", inject=inject)
        asyncio.run(run.advance())
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.h.probe_calls.clear()  # the build step's own probe, before the limit
        return run

    def outside(self):
        """Every file of the project outside the run records and git."""
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in sorted(self.root.rglob("*")) if p.is_file()
                and not p.relative_to(self.root).as_posix().startswith(("runs/", ".git/"))}

    def record(self, run_dir):
        return {p.relative_to(run_dir).as_posix(): p.read_bytes()
                for p in sorted(Path(run_dir).rglob("*")) if p.is_file()}


# ----------------------------------------------------------- the start and approval


class StartWiringTests(WiringCase):
    """The chunk 3 bullet: ``--run`` begins with the planners and builds only after
    ``--approve``; "Docker and the check image wait for approval"."""

    def test_positive_run_plans_reviews_and_pauses_with_only_the_status_query(self):
        before = self.outside()
        code, out = self.call("--run", "brief.md", "--builder", "claude", "--item", "demo",
                              "--git-dir", str(self.personal))
        self.assertEqual(code, 1, out)
        self.assertIn(f"is paused ({sr.AWAITING_PLAN_APPROVAL})", out)
        self.assertIn("--approve", out)
        self.assertEqual(self.h.docker_calls, ["status"])
        self.assertEqual(self.h.image_calls, [])
        self.assertEqual(self.outside(), before, "nothing written outside the run record")
        self.assertFalse(self.log.exists())
        run_dir, = list(self.runs.iterdir())
        state = runrecord.read_state(run_dir)
        self.assertFalse((Path(state["frozen_dir"]) / codex_rules.APPROVAL_FILE).exists())
        self.assertEqual(sorted({c["role"] for c in self.h.planning_calls}),
                         ["plan_reviewer", "planner", "synthesiser"])
        report = (run_dir / planning.PLAN_REPORT).read_text(encoding="utf-8")
        self.assertIn("No intent check has run", report)
        self.assertEqual(self.h.builders, [])

    def test_positive_approve_continues_the_same_run_into_the_build(self):
        self.call("--run", "brief.md", "--builder", "claude", "--item", "demo",
                  "--git-dir", str(self.personal))
        run_id = next(self.runs.iterdir()).name
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        code, out = self.call("--approve", run_id)
        self.assertEqual(code, 0, out)
        self.assertIn("ended reviewed-clean", out)
        state = runrecord.read_state(self.runs / run_id)
        self.assertEqual([s["step"] for s in state["steps"]
                          if s["role"] in ("builder", "reviewer")], ["build", "review-R2"])
        self.assertEqual(self.log_actions(), ["started", "completed"])
        self.assertIn("inspect", self.h.docker_calls, "the image step is approval's")

    def test_positive_advance_takes_a_planning_runs_planning_steps(self):
        run = self.planning_run()
        code, out = None, io.StringIO()
        with redirect_stdout(out):
            code = run_py.main(["--advance", run.state["run_id"]], root=self.root,
                               log_path=self.log, deps=self.h.deps(), runs_dir=self.runs,
                               frozen=Path(run.state["frozen_dir"]))
        self.assertEqual(code, 1, out.getvalue())
        state = runrecord.read_state(run.run_dir)
        self.assertEqual(state["pause"], sr.AWAITING_PLAN_APPROVAL)
        self.assertEqual(self.h.builders, [])

    def test_positive_a_stop_before_any_plan_review_names_a_packet_that_exists(self):
        # Code review R3-1: a planning stop before the first plan review (here a
        # planner's usage limit) still names the run record's packet, written with it.
        run = self.planning_run()
        self.h.readings = [reading(), reading(95)]
        asyncio.run(planning.advance(run))
        self.assertEqual(run.state["stop_reasons"][-1]["evidence"]["step"],
                         "planner-codex")
        report = json.loads((run.run_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["packet"],
                         f"runs/{run.state['run_id']}/plan-review/packet.md")
        self.assertTrue((self.root / report["packet"]).is_file())
        self.assertIn("## Open\n\nNone.", (self.root / report["packet"])
                      .read_text(encoding="utf-8"))
        self.assertFalse(packet.packet_path(self.root, "demo").exists())

    def test_rejection_a_failed_hand_over_of_a_planning_run_writes_no_log(self):
        run = self.planning_run()

        def runner(argv, cwd=None, env=None):
            raise FileNotFoundError(2, "No such file", argv[0])
        with redirect_stdout(io.StringIO()):
            code = run_py.hand_over(run, root=self.root, python=sys.executable,
                                    environ={"PATH": ""}, runner=runner,
                                    arm=lambda folder, root: None)
        self.assertEqual(code, 1)
        state = runrecord.read_state(run.run_dir)
        self.assertEqual(state["stop_reasons"][-1]["reason"], sr.ERROR)
        self.assertIn("could not be started", state["stop_reasons"][-1]["evidence"]
                      ["hand_over"])
        self.assertTrue((run.run_dir / "report.md").is_file())
        self.assertFalse(self.log.exists(), "a resumable planning end writes nothing")


# ------------------------------------------------------- each step's own provider


class ProviderRuleTests(WiringCase):
    """The chunk 3 bullet's second follow-on (10.10 items 1, 3 and 8): a step is stopped
    only by its own provider's readings; both readings are recorded with it."""

    def test_positive_a_codex_step_starts_with_claude_over_the_ceiling_overage_or_limit(self):
        run = self.built(inject="review-R1")
        for label, saved in (("ceiling", saved_claude(percent=95, resets_at=2_000_000)),
                             ("overage", saved_claude("limit-reported", 100, OVERAGE,
                                                      2_000_000)),
                             ("limit", saved_claude("limit-reported", 100,
                                                    "rate-limit status is rejected",
                                                    2_000_000))):
            with self.subTest(label):
                run.state["claude_last_reading"] = saved
                self.assertIsNone(run.before_step("review-R1", "codex"))
                entry = run.state["readings"][-1]
                self.assertEqual(entry["step"], "review-R1")
                self.assertIn("Codex", entry["codex"])
                self.assertIn("Claude", entry["claude"])
        self.assertEqual(self.h.probe_calls, [], "no probe before a Codex step")

    def test_positive_a_claude_step_starts_with_codex_over_the_ceiling_paid_or_limit(self):
        run = self.built(inject="build")
        cases = (("ceiling", reading(95)),
                 ("credits changed", reading(credits=dict(CREDITS, balance="1.00"))),
                 ("credits next", reading(reached="rate_limit_reached")),
                 ("limit", limits.Reading(limits.CODEX, limits.LIMIT_REPORTED,
                                          reason="ordinaryUsageAllowed is false")))
        for label, codex in cases:
            with self.subTest(label):
                self.h.readings = [codex]
                self.assertIsNone(run.before_step("build", "claude"))
                self.assertIn("claude", run.state["readings"][-1])
                self.assertIn("codex", run.state["readings"][-1])
        self.assertEqual(len(self.h.probe_calls), len(cases))

    def test_rejection_each_providers_own_reading_still_stops_its_own_steps(self):
        run = self.built(inject="build")
        self.h.readings = [reading(95)]
        self.assertIn("ceiling", run.before_step("review-R1", "codex")["detail"])
        run.state["claude_last_reading"] = saved_claude(percent=95, resets_at=2_000_000)
        stop = run.before_step("build", "claude")
        self.assertEqual(stop["provider"], "claude")
        self.assertEqual(self.h.probe_calls, [], "a known stop sends no probe")

    def test_positive_a_resume_into_a_codex_review_is_not_refused_on_claudes_reading(self):
        run = self.built(inject="review-R1")  # the build ran; the review is next
        run.state["claude_last_reading"] = saved_claude("limit-reported", 100, OVERAGE,
                                                        2_000_000)
        run.save()
        again = self.load(run.state["run_id"])
        loop.resume(again)
        self.assertEqual(again.state["status"], sr.RUNNING)

    def test_rejection_a_resume_into_a_claude_build_is_still_refused_on_it(self):
        run = self.built(inject="build")
        run.state["claude_last_reading"] = saved_claude("limit-reported", 100, OVERAGE,
                                                        2_000_000)
        run.save()
        before = self.record(run.run_dir)
        with self.assertRaisesRegex(loop.RunRefused, "paid overage"):
            loop.resume(self.load(run.state["run_id"]))
        self.assertEqual(self.record(run.run_dir), before)

    def test_positive_a_claude_next_step_records_untrackable_credits_and_codex_stops(self):
        for label, codex in (("untrackable", reading(credits=None)),
                             ("permission absent", limits.Reading(
                                 limits.CODEX, limits.LIMIT_REPORTED,
                                 reason="ordinaryUsageAllowed is null or absent"))):
            with self.subTest(label):
                self.setUp()
                run = self.built(inject="build")
                self.h.readings = [codex]
                again = self.load(run.state["run_id"])
                loop.resume(again)  # the next step is the Claude build: not refused
                self.assertIsNone(again.state["credits"])
                self.h.builder_script = [edit(self.root, "y\n")]
                asyncio.run(again.advance())
                stop = again.state["stop_reasons"][-1]
                self.assertEqual((stop["reason"], stop["evidence"]["step"]),
                                 (sr.USAGE_LIMIT, "review-R1"))
                self.assertIn("could not be tracked", stop["evidence"]["detail"])

    def test_positive_an_approval_into_a_claude_build_is_not_refused_on_codex(self):
        run = self.planning_run()
        pause_planned(run)
        self.h.readings = [limits.Reading(limits.CODEX, limits.LIMIT_REPORTED,
                                          reason="ordinaryUsageAllowed is null or absent")]
        with redirect_stderr(io.StringIO()):
            loop.approve(self.load(run.state["run_id"]))
        self.assertEqual(runrecord.read_state(run.run_dir)["status"], sr.RUNNING)

    def test_positive_a_planning_resume_checks_only_the_next_steps_provider(self):
        run = self.planning_run()
        # Stopped before the synthesis, a Claude step, by Claude's probe.
        self.h.probe_readings = [limits.Reading(limits.CLAUDE, limits.REPORTED, percent=1),
                                 limits.Reading(limits.CLAUDE, limits.REPORTED, percent=95,
                                                resets_at=self.h.clock + 60)]
        asyncio.run(planning.advance(run))
        self.assertEqual(run.state["stop_reasons"][-1]["evidence"]["step"], "synthesis")
        self.assertEqual(planning.next_providers(run), ("claude",))
        self.h.readings = [reading(95)]  # Codex over the ceiling: not the next step's
        again = self.load(run.state["run_id"])
        planning.resume(again)
        self.assertEqual(again.state["status"], sr.RUNNING)


# ------------------------------------------------------ paid usage in the planning half


class PaidPlanningTests(WiringCase):
    """The chunk 3 bullet's first follow-on: ``--allow-paid`` reaches the planning half,
    the two planners counting as one step, and the next step checks as usual."""

    def test_positive_allow_paid_permits_the_planners_once_then_checks_again(self):
        run = self.planning_run()
        paid = limits.Reading(limits.CLAUDE, limits.LIMIT_REPORTED, reason=OVERAGE,
                              resets_at=self.h.clock + 3600)
        self.h.probe_readings = [paid]
        asyncio.run(planning.advance(run))
        self.assertEqual(run.state["stop_reasons"][-1]["evidence"]["step"],
                         "planner-claude")
        self.assertTrue(run.state["stop_reasons"][-1]["evidence"]["paid_usage"])
        self.h.probe_readings = [paid, paid]
        code, out = self.call("--resume", run.state["run_id"], "--allow-paid")
        self.assertEqual(code, 1, out)
        state = runrecord.read_state(run.run_dir)
        self.assertEqual([p["step"] for p in state["paid_usage"]], ["planners"])
        self.assertIn("planners", state["planning"]["completed"])
        stop = state["stop_reasons"][-1]
        self.assertEqual((stop["reason"], stop["evidence"]["step"]),
                         (sr.USAGE_LIMIT, "synthesis"), "the next step checks as usual")
        self.assertNotIn("paid_usage_pending", state)
        self.assertFalse(self.log.exists())

    def test_positive_one_permission_covers_both_planners(self):
        self.assertTrue(loop._permits("planners", "planner-claude"))
        self.assertTrue(loop._permits("planners", "planner-codex"))
        self.assertFalse(loop._permits("planners", "synthesis"))
        self.assertFalse(loop._permits("synthesis", "planner-claude"))
        run = self.planning_run()
        self.assertEqual(loop.next_step_name(run), "planners")
        self.assertEqual(planning.next_providers(run), ("claude", "codex"))


# ------------------------------------------------------------ --resume --wait


class WaitTests(WiringCase):
    """10.10 item 5: a user-started ``--resume --wait`` waits at its preflight, in both
    halves; a plain ``--resume`` is refused as before."""

    def test_positive_build_half_waits_through_the_ceiling_and_resumes(self):
        run = self.built(inject="review-R1")
        self.h.readings = [reading(95, resets=self.h.clock + 600), reading(), reading()]
        code, out = self.call("--resume", run.state["run_id"], "--wait",
                              deps=self.waking_deps())
        self.assertEqual(code, 0, out + self.err)
        self.assertEqual(self.h.slept, [600 + loop.WAIT_MARGIN_SECONDS])
        self.assertIn("ended reviewed-clean", out)

    def test_positive_build_half_waits_through_known_paid_overage(self):
        run = self.built(inject="build")
        run.state["claude_last_reading"] = saved_claude(
            "limit-reported", 100, OVERAGE, self.h.clock + 600)
        run.save()
        code, out = self.call("--resume", run.state["run_id"], "--wait",
                              deps=self.waking_deps())
        self.assertEqual(code, 0, out + self.err)
        self.assertEqual(self.h.slept, [600 + loop.WAIT_MARGIN_SECONDS])
        self.assertEqual(len(self.h.probe_calls), 1, "the probe comes at the step")

    def test_positive_planning_half_waits_through_the_ceiling_and_plans(self):
        run = self.planning_run()
        self.h.readings = [reading(), reading(95, resets=self.h.clock + 100)]
        asyncio.run(planning.advance(run))
        self.assertEqual(run.state["stop_reasons"][-1]["evidence"]["step"],
                         "planner-codex")
        self.h.readings = [reading(95, resets=self.h.clock + 600)]
        code, out = self.call("--resume", run.state["run_id"], "--wait",
                              deps=self.waking_deps())
        self.assertEqual(code, 1, out + self.err)
        self.assertIn(f"is paused ({sr.AWAITING_PLAN_APPROVAL})", out)
        self.assertEqual(self.h.slept, [600 + loop.WAIT_MARGIN_SECONDS])
        self.assertFalse(self.log.exists())

    def test_positive_planning_half_waits_through_known_paid_overage(self):
        # The plan's chunk 3 bullet names this case in each half: the planning half
        # stopped by Claude's paid overage with a known reset, at the Claude planner.
        run = self.planning_run()
        self.h.probe_readings = [limits.Reading(limits.CLAUDE, limits.LIMIT_REPORTED,
                                                reason=OVERAGE,
                                                resets_at=self.h.clock + 600)]
        asyncio.run(planning.advance(run))
        evidence = run.state["stop_reasons"][-1]["evidence"]
        self.assertEqual(evidence["step"], "planner-claude")
        self.assertTrue(evidence["paid_usage"])
        # The live probe's rate-limit event saves this reading; the fake probe returns
        # its reading without the event, so it is saved here, as the build-half test does.
        run.state["claude_last_reading"] = saved_claude(
            "limit-reported", 100, OVERAGE, self.h.clock + 600)
        run.save()
        code, out = self.call("--resume", run.state["run_id"], "--wait",
                              deps=self.waking_deps())
        self.assertEqual(code, 1, out + self.err)
        self.assertIn(f"is paused ({sr.AWAITING_PLAN_APPROVAL})", out)
        self.assertEqual(self.h.slept, [600 + loop.WAIT_MARGIN_SECONDS])
        state = runrecord.read_state(run.run_dir)
        self.assertFalse(state.get("paid_usage"), "waiting is not a paid permission")
        self.assertFalse(self.log.exists())

    def test_rejection_an_unknown_reset_refuses_as_today(self):
        run = self.built(inject="review-R1")
        before = self.record(run.run_dir)
        self.h.readings = [reading(95)]
        code, out = self.call("--resume", run.state["run_id"], "--wait",
                              deps=self.waking_deps())
        self.assertEqual(code, run_py.REFUSED, out)
        self.assertEqual(self.h.slept, [])
        self.assertEqual(self.record(run.run_dir), before)

    def test_rejection_a_plain_resume_meets_the_same_readings_and_is_refused(self):
        run = self.built(inject="review-R1")
        self.h.readings = [reading(95, resets=self.h.clock + 600)]
        code, out = self.call("--resume", run.state["run_id"], deps=self.waking_deps())
        self.assertEqual(code, run_py.REFUSED, out)
        self.assertIn("ceiling", out)
        self.assertEqual(self.h.slept, [])
        self.setUp()
        run = self.built(inject="build")
        run.state["claude_last_reading"] = saved_claude(
            "limit-reported", 100, OVERAGE, self.h.clock + 600)
        run.save()
        code, out = self.call("--resume", run.state["run_id"], deps=self.waking_deps())
        self.assertEqual(code, run_py.REFUSED, out)
        self.assertIn("paid overage", out)
        self.assertEqual(self.h.slept, [])

    def test_positive_unknown_reset_overage_clears_for_a_plain_resume_only(self):
        run = self.built(inject="build")
        run.state["claude_last_reading"] = saved_claude("limit-reported", 100, OVERAGE)
        run.save()
        before = self.record(run.run_dir)
        code, out = self.call("--resume", run.state["run_id"], "--wait",
                              deps=self.waking_deps())
        self.assertEqual(code, run_py.REFUSED, out)
        self.assertIn("paid overage", out)
        self.assertEqual((self.h.slept, self.h.probe_calls), ([], []), "no probe sent")
        self.assertEqual(self.record(run.run_dir), before)
        # A plain resume is the user's word that the allowance has reset: it goes on to
        # the probe before the build, which reads the live figure.
        code, out = self.call("--resume", run.state["run_id"])
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.h.probe_calls), 1)

    def test_positive_the_unknown_reset_allowance_survives_a_codex_step(self):
        # Code review R3-2: a plain resume's allowance for an unknown-reset overage
        # reading is the first Claude step's, so the Codex review the resume starts
        # with keeps it, and the Claude fix after it probes rather than stopping.
        run = self.built(inject="review-R1")  # the review, a Codex step, is next
        run.state["claude_last_reading"] = saved_claude("limit-reported", 100, OVERAGE)
        run.save()
        again = self.load(run.state["run_id"])
        loop.resume(again)
        self.assertTrue(again.state["claude_unknown_reset_resume"])
        self.h.reviews = [[finding()], []]
        self.h.builder_script = [reply(actions(("R1-1", "fixed")))]
        asyncio.run(again.advance())
        self.assertEqual(self.reason(again), sr.REVIEWED_CLEAN,
                         again.state["stop_reasons"][-1])
        self.assertEqual(len(self.h.probe_calls), 1, "the Claude fix's probe, sent")
        self.assertFalse(again.state["claude_unknown_reset_resume"])

    def test_positive_the_planning_half_keeps_the_allowance_through_a_plan_review(self):
        run = self.planning_run()
        run.state["claude_unknown_reset_resume"] = True
        planning._finish(run, "plan-review-R1", False)  # the reviewer's, Codex
        self.assertTrue(run.state["claude_unknown_reset_resume"])
        planning._finish(run, "plan-revise-R1", False)  # the synthesiser's, Claude
        self.assertFalse(run.state["claude_unknown_reset_resume"])
        run.state["claude_unknown_reset_resume"] = True
        planning._finish(run, "planners", False)  # both, Claude among them
        self.assertFalse(run.state["claude_unknown_reset_resume"])

    def test_rejection_a_waiting_process_woken_after_a_hand_resume_changes_nothing(self):
        for label, stop_again in (("still running", False), ("stopped again", True)):
            with self.subTest(label):
                self.setUp()
                run = self.built(inject="review-R1")
                run_id = run.state["run_id"]
                after_hand = {}

                def hand_resume():
                    hand = self.load(run_id)
                    loop.resume(hand)
                    if stop_again:
                        hand.end(sr.USAGE_LIMIT, dict(run.state["stop_reasons"][-1]
                                                      ["evidence"]))
                    after_hand.update(self.record(hand.run_dir))
                self.h.readings = [reading(95, resets=self.h.clock + 600), reading()]
                code, out = self.call("--resume", run_id, "--wait",
                                      deps=self.waking_deps(during=hand_resume))
                self.assertEqual(code, run_py.REFUSED, out)
                self.assertIn("changed while this --wait slept", out)
                self.assertEqual(self.record(self.runs / run_id), after_hand)

    def test_positive_the_in_run_wait_acts_only_on_the_stop_it_waited_for(self):
        def limited(prompt):
            raise limits.UsageLimitError(limits.CLAUDE, "rate_limit",
                                         resets_at=self.h.clock + 600)
        self.h.builder_script = [limited]
        run = start_built("brief.md", "demo", str(self.personal), root=self.root,
                          deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                          settings=self.settings, builder="claude")
        asyncio.run(run.advance())
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        run.wait = True
        state = runrecord.read_state(run.run_dir)
        state["stop_reasons"].append(dict(state["stop_reasons"][-1]))
        runrecord.write_state(run.run_dir, state)  # someone else acted meanwhile
        with redirect_stderr(io.StringIO()) as err:
            self.assertFalse(run.wait_then_reload())
        self.assertIn("changed while this process slept", err.getvalue())


# --------------------------------------------------------------- resume.lock


class LockTests(WiringCase):
    """10.10 item 5: every resume and approval claims the run with an exclusive
    ``runs/<run-id>/resume.lock``; one that finds it refuses and changes nothing."""

    def refused_with_lock(self, run_dir, call):
        lock = Path(run_dir) / loop.LOCK_FILE
        lock.write_bytes(b"held\n")
        before = self.record(run_dir)
        with redirect_stderr(io.StringIO()), \
                self.assertRaisesRegex(loop.RunRefused, "resume.lock exists") as caught:
            call()
        self.assertIn("delete that file", " ".join(caught.exception.args[0]))
        self.assertEqual(self.record(run_dir), before)
        self.assertTrue(lock.exists(), "a refused command leaves another's lock alone")
        lock.unlink()

    def test_rejection_a_build_resume_finding_the_lock(self):
        run = self.built(inject="build")
        self.refused_with_lock(run.run_dir,
                               lambda: loop.resume(self.load(run.state["run_id"])))
        self.assertEqual(self.h.docker_calls.count("start"), 0)

    def test_rejection_a_planning_resume_finding_the_lock(self):
        run = self.planning_run()
        self.h.readings = [reading(), reading(95, resets=5)]
        asyncio.run(planning.advance(run))
        self.refused_with_lock(run.run_dir,
                               lambda: planning.resume(self.load(run.state["run_id"])))

    def test_rejection_an_approval_finding_the_lock(self):
        run = self.planning_run()
        pause_planned(run)
        self.refused_with_lock(run.run_dir,
                               lambda: loop.approve(self.load(run.state["run_id"])))
        self.assertEqual(self.h.image_calls, [])

    def test_positive_a_resume_removes_its_lock_once_saved_running(self):
        run = self.built(inject="build")
        seen = []
        real = runrecord.write_state

        def write(run_dir, state):
            seen.append((Path(run_dir) / loop.LOCK_FILE).exists())
            return real(run_dir, state)
        again = self.load(run.state["run_id"])
        with mock.patch.object(loop.runrecord, "write_state", write):
            loop.resume(again)
        self.assertEqual(seen, [True], "the run is saved running under the lock")
        self.assertFalse((run.run_dir / loop.LOCK_FILE).exists())

    def test_rejection_a_run_changed_since_it_was_read_is_refused_under_the_lock(self):
        run = self.built(inject="build")
        again = self.load(run.state["run_id"])
        state = runrecord.read_state(run.run_dir)
        state["stop_reasons"].append(dict(state["stop_reasons"][-1]))
        runrecord.write_state(run.run_dir, state)
        with self.assertRaisesRegex(loop.RunRefused, "changed after this command read it"):
            loop.resume(again)
        self.assertFalse((run.run_dir / loop.LOCK_FILE).exists())
        self.assertEqual(self.h.docker_calls.count("start"), 0)


if __name__ == "__main__":
    unittest.main()
