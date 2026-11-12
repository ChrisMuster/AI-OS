#!/usr/bin/env python3
"""Hermetic tests for the build-and-review loop, part of the split of the old single
loop file (plan 10.3, the chunk list): the usage guard of plan 10.10 items 7 and 8,
the Claude usage probe before each Claude step, the saved last reading, and the
one-step permission for paid usage (``--allow-paid``).

The shared fixtures (``Harness``, the fake sessions, ``LoopCase``) are in
``loop_fixtures.py``. No test makes a model call, sends a provider request or starts
Docker. Same three controls as the other suites: a positive control must work, a
rejection control must end or refuse the run with the reason named, a negative control
must not.

    python workflows/review-orchestration/tests/test_loop_usage.py
"""

import dataclasses
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_fixtures import *  # noqa: E402,F401,F403 - the loop suites' shared fixtures


class UsageGuardTests(LoopCase):
    """The probe and one-step permission, with no provider request or Docker launch."""

    def prepared(self):
        """A run approved and ready to build (since A3 chunk 3 a start plans first)."""
        return start_built("brief.md", "demo", str(self.personal), root=self.root,
                           deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                           settings=self.settings, builder="claude")

    def test_positive_two_claude_steps_each_get_a_probe_and_no_preflight_gets_one(self):
        run = self.prepared()
        self.assertEqual(self.h.probe_calls, [])
        self.assertIsNone(run.before_step("build", "claude"))
        self.assertIsNone(run.before_step("fix-R1", "claude"))
        self.assertEqual(len(self.h.probe_calls), 2)
        self.assertEqual([r["usage_probe"]["probe_event"] for r in
                          run.state["readings"]], [False, False])

    def test_negative_no_event_keeps_the_last_reading(self):
        run = self.prepared()
        saved = {"source": "reported", "percent": 20, "window": "five_hour",
                 "resets_at": 2000000, "reason": None, "read_at": 1}
        run.state["claude_last_reading"] = dict(saved)
        run.before_step("build", "claude")
        self.assertEqual(run.state["claude_last_reading"], saved)
        self.assertIn("not reported", run.state["readings"][-1]["claude"])

    def test_rejection_known_overage_stops_without_a_probe(self):
        run = self.prepared()
        run.state["claude_last_reading"] = {
            "source": "limit-reported", "reason": "using paid overage",
            "percent": 90, "window": "five_hour", "resets_at": 2000000,
            "read_at": 1}
        stop = run.before_step("build", "claude")
        self.assertIn("paid overage", stop["detail"])
        self.assertEqual(self.h.probe_calls, [])

    def test_expired_saved_window_allows_a_fresh_probe(self):
        run = self.prepared()
        run.state["claude_last_reading"] = {
            "source": "reported", "reason": None, "percent": 95,
            "window": "five_hour", "resets_at": self.h.clock - 1, "read_at": 1}
        self.assertIsNone(run.before_step("build", "claude"))
        self.assertEqual(len(self.h.probe_calls), 1)

    def test_unknown_reset_needs_a_user_resume_credit_for_one_step(self):
        run = self.prepared()
        run.state["claude_last_reading"] = {
            "source": "limit-reported", "reason": "using paid overage",
            "percent": 90, "window": "five_hour", "resets_at": None,
            "read_at": 1}
        self.assertIsNotNone(run.before_step("build", "claude"))
        self.assertEqual(self.h.probe_calls, [])
        run.state["claude_unknown_reset_resume"] = True
        self.assertIsNone(run.before_step("build", "claude"))
        self.assertEqual(len(self.h.probe_calls), 1)
        self.assertIsNotNone(run.before_step("fix-R1", "claude"))
        self.assertEqual(len(self.h.probe_calls), 1)

    def test_user_resume_checks_saved_overage_without_a_probe(self):
        run = self.prepared()
        run.state["status"] = sr.ENDED
        run.state["stop_reasons"] = [{"reason": sr.USAGE_LIMIT, "evidence": {}}]
        run.state["claude_last_reading"] = {
            "source": "limit-reported", "reason": "using paid overage",
            "percent": 100, "window": "five_hour", "resets_at": 2000000,
            "read_at": 1}
        # Saved, since a resume's claim re-reads state.json (plan 10.10 item 5).
        run.save()
        with self.assertRaisesRegex(loop.RunRefused, "paid overage"):
            loop.resume(run)
        self.assertEqual(self.h.probe_calls, [])
        run.state["claude_last_reading"]["resets_at"] = None
        run.save()
        loop.resume(run)
        self.assertTrue(run.state["claude_unknown_reset_resume"])
        self.assertIsNone(run.before_step("build", "claude"))
        self.assertEqual(len(self.h.probe_calls), 1)

    def test_paid_permission_covers_a_known_overage_at_resume_and_step(self):
        run = self.prepared()
        run.state["status"] = sr.ENDED
        run.state["stop_reasons"] = [{"reason": sr.USAGE_LIMIT, "evidence": {}}]
        run.state["claude_last_reading"] = {
            "source": "limit-reported", "reason": "using paid overage",
            "percent": 100, "window": "five_hour", "resets_at": 2000000,
            "read_at": 1}
        run.save()
        loop.resume(run, allow_paid=True)
        self.assertEqual(run.state["paid_usage_pending"], "build")
        self.assertEqual(self.h.probe_calls, [])
        self.assertIsNone(run.before_step("build", "claude"))
        self.assertEqual(len(self.h.probe_calls), 1)

    def _failed_permitted_step_expires_permission(self, error, expected_reason):
        run = self.prepared()
        run.state["paid_usage_pending"] = "build"
        run.state["claude_last_reading"] = {
            "source": "limit-reported", "reason": "using paid overage",
            "percent": 100, "window": "five_hour", "resets_at": 2000000,
            "read_at": 1}

        async def failing_build():
            raise error

        run.build = failing_build
        asyncio.run(run.take("build"))
        self.assertEqual(self.reason(run), expected_reason)
        self.assertFalse(run.claude_usage.paid_allowed)
        saved = json.loads((run.run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertNotIn("paid_usage_pending", saved)
        probes = len(self.h.probe_calls)
        again = loop.load(run.state["run_id"], root=self.root, deps=self.h.deps(),
                          log_path=self.log, runs_dir=self.runs)
        with self.assertRaisesRegex(loop.RunRefused, "paid overage"):
            loop.resume(again)
        self.assertEqual(len(self.h.probe_calls), probes)

    def test_paid_permission_expires_when_a_step_hits_a_usage_limit(self):
        self._failed_permitted_step_expires_permission(
            limits.UsageLimitError("claude", "rate_limit"), sr.USAGE_LIMIT)

    def test_paid_permission_expires_when_a_step_fails_otherwise(self):
        self._failed_permitted_step_expires_permission(
            RuntimeError("provider failed"), sr.ERROR)

    def test_resume_discards_a_leftover_permission_from_an_older_record(self):
        run = self.prepared()
        run.state["status"] = sr.ENDED
        run.state["stop_reasons"] = [{"reason": sr.ERROR, "evidence": {}}]
        run.state["paid_usage_pending"] = "build"
        run.save()
        loop.resume(run)
        saved = json.loads((run.run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertNotIn("paid_usage_pending", saved)
        self.assertEqual(self.h.probe_calls, [])

    def test_probe_on_overage_is_recorded_and_named_in_the_report(self):
        run = self.prepared()
        info = SimpleNamespace(status="allowed", rate_limit_type="five_hour",
                               resets_at=2000000,
                               raw={"isUsingOverage": True, "unifiedWindows": {
                                   "five_hour": {"utilization": 1,
                                                 "resetsAt": 2000000}}})
        self.h.probe_events = [[type("RateLimitEvent", (),
                                      {"rate_limit_info": info})()]]
        self.h.probe_readings = [limits.Reading(
            "claude", limits.LIMIT_REPORTED, reason="using paid overage",
            resets_at=2000000)]
        stop = run.before_step("build", "claude")
        self.assertTrue(stop["paid_usage"])
        self.assertTrue(run.state["readings"][-1]["usage_probe"]["probe_on_overage"])
        self.assertEqual(run.state["claude_last_reading"]["reason"], "using paid overage")
        run.end(sr.USAGE_LIMIT, stop)
        report = (run.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("probe_on_overage", report)
        self.assertIn("--allow-paid", report)

    def test_probe_failure_after_an_event_keeps_the_event(self):
        run = self.prepared()
        info = SimpleNamespace(status="allowed", rate_limit_type="five_hour",
                               resets_at=2000000,
                               raw={"unifiedWindows": {"five_hour": {
                                   "utilization": 0.25, "resetsAt": 2000000}}})
        self.h.probe_events = [[type("RateLimitEvent", (),
                                      {"rate_limit_info": info})()]]
        self.h.probe_error = RuntimeError("probe failed")
        with self.assertRaisesRegex(RuntimeError, "probe failed"):
            run.before_step("build", "claude")
        self.assertEqual(run.state["claude_last_reading"]["percent"], 25)
        saved = json.loads((run.run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["claude_last_reading"]["percent"], 25)

    def test_failed_probe_that_found_overage_still_names_its_credit_use(self):
        run = self.prepared()
        info = SimpleNamespace(status="allowed", rate_limit_type="five_hour",
                               resets_at=2000000,
                               raw={"isUsingOverage": True,
                                    "unifiedWindows": {"five_hour": {
                                        "utilization": 1, "resetsAt": 2000000}}})
        self.h.probe_events = [[type("RateLimitEvent", (),
                                      {"rate_limit_info": info})()]]
        self.h.probe_error = RuntimeError("probe failed")
        with self.assertRaisesRegex(RuntimeError, "probe failed"):
            run.before_step("build", "claude")
        self.assertTrue(run.state["readings"][-1]["usage_probe"]["probe_on_overage"])
        run.end(sr.ERROR, {"step": "build", "error": "probe failed"})
        self.assertIn("probe_on_overage", (run.run_dir / "report.md")
                      .read_text(encoding="utf-8"))

    def test_rejection_a_failed_probe_ends_error_before_the_builder(self):
        run = self.prepared()
        self.h.probe_error = RuntimeError("probe failed")
        asyncio.run(run.take("build"))
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertEqual(self.h.builders, [])
        self.assertEqual(run.state["step"], loop.APPROVED_STEP)

    def test_paid_permission_lifts_the_ceiling_and_credit_check_for_one_step(self):
        run = self.prepared()
        run.state["paid_usage_pending"] = "build"
        changed = dict(CREDITS, balance="4.50")
        self.h.readings = [reading(95, credits=changed) for _ in range(4)]
        self.h.probe_readings = [limits.Reading("claude", limits.REPORTED,
                                                 percent=95, window="five_hour")
                                 for _ in range(2)]
        # A Claude step: the permitted one passes Claude's ceiling, the next stops.
        self.assertIsNone(run.before_step("build", "claude"))
        run.state.pop("paid_usage_pending")
        self.assertIsNotNone(run.before_step("fix-R1", "claude"))
        # A Codex step: the permitted one passes Codex's ceiling and the changed
        # credits; the next stops.
        run.state["paid_usage_pending"] = "review-R1"
        self.assertIsNone(run.before_step("review-R1", "codex"))
        run.state.pop("paid_usage_pending")
        self.assertIsNotNone(run.before_step("review-R2", "codex"))

    def test_after_a_permitted_step_the_new_credit_snapshot_is_the_baseline(self):
        self.h.builder_script = [edit(self.root, "done\n")]
        self.h.reviews = [[]]
        self.h.readings = [reading(20),
                           reading(20, credits=dict(CREDITS, balance="4.50")),
                           reading(20, credits=dict(CREDITS, balance="4.25")),
                           reading(20, credits=dict(CREDITS, balance="4.25")),
                           # Before the final intent check (A4), still the baseline.
                           reading(20, credits=dict(CREDITS, balance="4.25"))]
        run = start_built("brief.md", "demo", str(self.personal), root=self.root,
                          deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                          settings=self.settings, builder="claude", allow_paid=True)
        asyncio.run(run.advance())
        self.assertEqual(self.reason(run), sr.CLEAN)
        self.assertEqual(run.state["credits"]["balance"], "4.25")
        self.assertEqual([entry["step"] for entry in run.state["paid_usage"]], ["build"])

    def test_rejection_hard_stops_remain_with_paid_permission(self):
        run = self.prepared()
        # A Codex step, since Codex's reading stops only a Codex step (A3 chunk 3).
        run.state["paid_usage_pending"] = "review-R1"
        self.h.readings = [limits.Reading("codex", limits.LIMIT_REPORTED,
                                          reason="ordinaryUsageAllowed is false")]
        self.assertIn("ordinaryUsageAllowed",
                      run.before_step("review-R1", "codex")["detail"])
        self.assertEqual(self.h.probe_calls, [])

    def test_unknown_codex_permission_stops_unless_paid_is_allowed(self):
        unknown = limits.Reading("codex", limits.LIMIT_REPORTED,
                                 reason="ordinaryUsageAllowed is null or absent")
        self.h.readings = [unknown]
        with self.assertRaisesRegex(loop.RunRefused, "ordinaryUsageAllowed"):
            self.prepared()
        self.h.readings = [unknown]
        run = loop.start("brief.md", "demo", str(self.personal), root=self.root,
                         deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                         settings=self.settings, builder="claude", allow_paid=True)
        # Since A3 chunk 3 a start's first step is the two planners, one step for a
        # permission.
        self.assertEqual(run.state["paid_usage_pending"], "planners")
        self.assertEqual(run.state["paid_usage"][0]["step"], "planners")

    def test_paid_permission_does_not_override_rejected_claude(self):
        run = self.prepared()
        run.state["paid_usage_pending"] = "build"
        run.state["claude_last_reading"] = {
            "source": "limit-reported", "reason": "rate-limit status is rejected",
            "percent": 100, "window": "five_hour", "resets_at": 2000000,
            "read_at": 1}
        self.assertIn("rejected", run.before_step("build", "claude")["detail"])
        self.assertEqual(self.h.probe_calls, [])

    def test_rejection_allow_paid_and_wait(self):
        with self.assertRaisesRegex(loop.RunRefused, "--wait"):
            loop.start("brief.md", "demo", str(self.personal), root=self.root,
                       deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                       settings=self.settings, builder="claude", allow_paid=True,
                       wait=True)

    def test_command_line_passes_paid_permission_for_all_three_modes(self):
        cases = [("--run", ["brief.md", "--builder", "claude", "--item", "demo",
                            "--git-dir", str(self.personal)], "start"),
                 ("--resume", ["20261019-160323-3f96"], "resume"),
                 ("--approve", ["20261019-160323-3f96"], "approve")]
        for mode, value, target in cases:
            with self.subTest(mode=mode), mock.patch.object(loop, target) as called, \
                    mock.patch.object(loop, "load", return_value=object()):
                if mode == "--run":
                    called.return_value = None
                self.assertEqual(run_py.main([mode, *value, "--allow-paid", "--dry-run"],
                                             root=self.root, log_path=self.log,
                                             runs_dir=self.runs), 0)
                self.assertTrue(called.call_args.kwargs["allow_paid"])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            run_py.main(["--resume", "20261019-160323-3f96", "--allow-paid", "--wait"])
        self.assertEqual(caught.exception.code, 2)

    def test_dry_start_sends_no_probe_and_grants_nothing(self):
        with redirect_stderr(io.StringIO()) as output:
            result = loop.start("brief.md", "demo", str(self.personal), root=self.root,
                                deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                                settings=self.settings, builder="claude", dry_run=True,
                                allow_paid=True)
        self.assertIsNone(result)
        self.assertEqual(self.h.probe_calls, [])
        self.assertIn("would permit", output.getvalue())
        self.assertFalse(self.runs.exists())

    def test_paid_stop_recommends_both_choices(self):
        recommendation = loop.recommendation(sr.USAGE_LIMIT, "run-id", paid=True)
        self.assertIn("--resume run-id)", recommendation)
        self.assertIn("--resume run-id --allow-paid", recommendation)
        self.assertNotIn("--allow-paid", loop.recommendation(sr.USAGE_LIMIT, "run-id"))


class StepCeilingTests(LoopCase):
    """The ceiling during a Claude builder's build and fix steps (plan 10.10 item 7,
    the user's decision of 2026-11-10): which steps the run arms it for, and the stop a
    ceiling interrupt ends in. The interrupt itself is tested in test_providers.py."""

    def prepared(self):
        return start_built("brief.md", "demo", str(self.personal), root=self.root,
                           deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                           settings=self.settings, builder="claude")

    def armed(self, run, step):
        """The ``step_ceiling`` the step ran with, and the run's after it."""
        seen = []

        async def body(*_args):
            seen.append(getattr(run.claude_usage, "step_ceiling", None))

        run.build = body
        run.fix = body
        run.review = lambda *_args: seen.append(
            getattr(run.claude_usage, "step_ceiling", None))
        with mock.patch.object(run, "before_step", return_value=None):
            asyncio.run(run.take(step))
        return seen[0], getattr(run.claude_usage, "step_ceiling", None)

    def test_positive_a_claude_builders_build_and_fix_steps_run_with_the_ceiling(self):
        run = self.prepared()
        ceiling = run.settings["usage_ceiling_percent"]
        for step in ("build", "fix-R1"):
            with self.subTest(step=step):
                self.assertEqual(self.armed(run, step), (ceiling, None))

    def test_negative_a_review_step_runs_without_it(self):
        run = self.prepared()
        self.assertEqual(self.armed(run, "review-R1"), (None, None))

    def test_negative_a_codex_builders_steps_run_without_it(self):
        # The review step here is a Claude reviewer's: a Claude step, never armed.
        run = self.prepared()
        run.settings["roles"]["builder"] = "codex"
        run.settings["roles"]["reviewer"] = "claude"
        for step in ("build", "fix-R1", "review-R1"):
            with self.subTest(step=step):
                self.assertEqual(self.armed(run, step), (None, None))

    def test_rejection_a_ceiling_interrupt_ends_usage_limit_resumable_not_paid(self):
        run = self.prepared()

        async def interrupted_build():
            raise limits.UsageLimitError(
                "claude", "Claude usage 80% reached the 80% ceiling during the step",
                2000000)

        run.build = interrupted_build
        with mock.patch.object(run, "before_step", return_value=None):
            asyncio.run(run.take("build"))
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        evidence = run.state["stop_reasons"][-1]["evidence"]
        self.assertEqual((evidence["step"], evidence["resets_at"], evidence["paid_usage"]),
                         ("build", 2000000, False))
        self.assertEqual(run.state["interrupted"], "build")
        self.assertTrue((run.run_dir / "partial" / "build.patch").exists())
        self.assertIsNone(run.claude_usage.step_ceiling)
        report = (run.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("reached the 80% ceiling during the step", report)


class MaxTurnsTests(LoopCase):
    """``--max-turns N`` on ``--run``, ``--approve`` and ``--resume`` (plan 10.10 item
    9, the user's decision of 2026-11-10)."""

    def prepared(self, item="demo"):
        # Each run writes its item's packet at approval, so a second run in one test
        # needs another item name.
        return start_built("brief.md", item, str(self.personal), root=self.root,
                           deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                           settings=self.settings, builder="claude")

    def builder_limit(self, run):
        """The turn limit the run's builder session is made with."""
        seen = []
        original = run.deps.make_builder

        def capture(settings, *args):
            seen.append(settings["max_turns"]["builder"])
            return original(settings, *args)

        run.deps = dataclasses.replace(run.deps, make_builder=capture)
        run.builder = None
        asyncio.run(run._builder("build"))
        return seen[0]

    def main(self, *argv):
        with redirect_stdout(io.StringIO()) as out:
            code = run_py.main(list(argv), root=self.root, log_path=self.log,
                               runs_dir=self.runs)
        return code, out.getvalue()

    def test_positive_without_it_the_builder_keeps_the_settings_limit(self):
        run = self.prepared()
        self.assertEqual(self.builder_limit(run), run.settings["max_turns"]["builder"])
        self.assertNotIn("max_turns_builder", run.state)

    def test_positive_a_given_limit_is_recorded_and_used_by_the_builder(self):
        run = self.prepared()
        reviewer_limit = run.settings["max_turns"]["reviewer"]
        loop.set_max_turns(run, 150, "--resume")
        saved = json.loads((run.run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual((saved["max_turns_builder"]["value"],
                          saved["max_turns_builder"]["command"]), (150, "--resume"))
        self.assertTrue(saved["max_turns_builder"]["at"])
        self.assertEqual(self.builder_limit(run), 150)
        # The saved settings and the reviewer's limit are left as they were.
        settings = json.loads((run.run_dir / "settings.json").read_text(encoding="utf-8"))
        self.assertNotEqual(settings["max_turns"]["builder"], 150)
        self.assertEqual(run.settings["max_turns"]["reviewer"], reviewer_limit)
        run.end(sr.USAGE_LIMIT, {"step": "build", "provider": "claude", "detail": "x",
                                 "resets_at": None, "paid_usage": False})
        report = (run.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("**Builder turn limit:** 150, given with `--resume`", report)

    def test_negative_a_dry_run_reports_and_sets_nothing(self):
        run = self.prepared()
        with redirect_stdout(io.StringIO()) as out:
            loop.set_max_turns(run, 150, "--resume", dry_run=True)
        self.assertIn("would set the Claude builder's turn limit to 150", out.getvalue())
        self.assertNotIn("max_turns_builder", run.state)

    def test_rejection_a_codex_builder_is_refused_naming_why(self):
        run = SimpleNamespace(settings={"roles": {"builder": "codex"}},
                              state={"run_id": "r1"})
        self.assertIn("takes no turn limit", loop.max_turns_refusal(run))
        self.assertIsNone(loop.max_turns_refusal(
            SimpleNamespace(settings={"roles": {"builder": "claude"}}, state={})))

    def test_rejection_bad_values_and_other_modes_are_usage_errors(self):
        cases = [["--resume", "r1", "--max-turns", "0"],
                 ["--approve", "r1", "--max-turns", "-5"],
                 ["--resume", "r1", "--max-turns", "ten"],
                 ["--stop", "r1", "--max-turns", "90"],
                 ["--amend", "r1", "b.md", "--max-turns", "90"],
                 ["--run", "brief.md", "--builder", "codex", "--item", "demo",
                  "--git-dir", "x", "--max-turns", "90"]]
        for argv in cases:
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as caught:
                run_py.main(argv, root=self.root, log_path=self.log, runs_dir=self.runs)
            self.assertEqual(caught.exception.code, 2)

    def test_positive_approve_and_resume_record_it_after_their_checks(self):
        for mode, target in (("--approve", "approve"), ("--resume", "resume")):
            with self.subTest(mode=mode):
                run = self.prepared(target)

                async def no_steps(_run):
                    return None

                with mock.patch.object(loop, "load", return_value=run), \
                        mock.patch.object(loop, target) as called, \
                        mock.patch.object(run_py, "take_steps", no_steps), \
                        mock.patch.object(run_py, "_outcome", return_value=0):
                    code, _ = self.main(mode, run.state["run_id"], "--max-turns", "120")
                self.assertEqual(code, 0)
                called.assert_called_once()
                self.assertEqual((run.state["max_turns_builder"]["value"],
                                  run.state["max_turns_builder"]["command"]), (120, mode))

    def test_rejection_a_refused_approve_or_resume_sets_nothing(self):
        for mode, target in (("--approve", "approve"), ("--resume", "resume")):
            with self.subTest(mode=mode):
                run = self.prepared(target)
                with mock.patch.object(loop, "load", return_value=run), \
                        mock.patch.object(loop, target,
                                          side_effect=loop.RunRefused(["no"])):
                    code, _ = self.main(mode, run.state["run_id"], "--max-turns", "120")
                self.assertEqual(code, run_py.REFUSED)
                self.assertNotIn("max_turns_builder", run.state)

    def test_rejection_a_codex_built_run_is_refused_before_the_mode_runs(self):
        for mode, target in (("--approve", "approve"), ("--resume", "resume")):
            with self.subTest(mode=mode):
                run = self.prepared(target)
                run.settings["roles"]["builder"] = "codex"
                with mock.patch.object(loop, "load", return_value=run), \
                        mock.patch.object(loop, target) as called:
                    code, out = self.main(mode, run.state["run_id"], "--max-turns", "120")
                self.assertEqual(code, run_py.REFUSED)
                self.assertIn("takes no turn limit", out)
                called.assert_not_called()
                self.assertNotIn("max_turns_builder", run.state)

    def test_negative_dry_approve_and_resume_report_and_set_nothing(self):
        for mode, target in (("--approve", "approve"), ("--resume", "resume")):
            with self.subTest(mode=mode):
                run = self.prepared(target)
                with mock.patch.object(loop, "load", return_value=run), \
                        mock.patch.object(loop, target):
                    code, out = self.main(mode, run.state["run_id"], "--max-turns",
                                          "120", "--dry-run")
                self.assertEqual(code, 0)
                self.assertIn("would set the Claude builder's turn limit to 120", out)
                self.assertNotIn("max_turns_builder", run.state)

    def test_positive_a_start_records_it_and_a_dry_start_only_reports_it(self):
        run = self.prepared()
        with mock.patch.object(loop, "start", return_value=run), \
                mock.patch.object(run_py, "take_steps", lambda _run: asyncio.sleep(0)), \
                mock.patch.object(run_py, "_outcome", return_value=0):
            self.main("--run", "brief.md", "--builder", "claude", "--item", "demo",
                      "--git-dir", str(self.personal), "--max-turns", "200")
        self.assertEqual((run.state["max_turns_builder"]["value"],
                          run.state["max_turns_builder"]["command"]), (200, "--run"))
        with mock.patch.object(loop, "start", return_value=None):
            code, out = self.main("--run", "brief.md", "--builder", "claude", "--item",
                                  "demo", "--git-dir", str(self.personal),
                                  "--max-turns", "200", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("would set the Claude builder's turn limit to 200", out)


def five_hour_event(percent, resets=2000000):
    """A Claude reading whose event carries the five-hour window at ``percent``."""
    return limits.Reading(limits.CLAUDE, limits.REPORTED, percent=percent,
                          window="five_hour", resets_at=resets,
                          raw={"unifiedWindows": {"five_hour": {
                              "utilization": percent / 100, "resetsAt": resets}}})


class SegmentRecordTests(LoopCase):
    """Segment records for a Claude builder's build and fix steps (plan 10.10 item 10,
    R67-2): one record and one transcript per segment, kept across a stop and a
    resume."""

    def prepared(self):
        return start_built("brief.md", "demo", str(self.personal), root=self.root,
                           deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                           settings=self.settings, builder="claude")

    def turn(self, run, act, step="build"):
        """One Claude builder turn through ``_builder_turn``, ``act`` being the turn's
        script; returns the exception it raised, or None."""
        builder = FakeBuilder(self.h, None)
        self.h.builders.append(builder)
        self.h.builder_script = [act]
        try:
            asyncio.run(run._builder_turn(step, builder, "p"))
        except BaseException as exc:  # noqa: BLE001 - the test inspects it
            return exc
        return None

    def segments(self, run, step="build"):
        return run.state["segments"][step]

    def test_positive_a_completed_build_has_one_segment_and_keeps_the_old_files(self):
        self.h.builder_script = [edit(self.root, "done\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.CLEAN)
        [segment] = self.segments(run)
        self.assertEqual((segment["segment"], segment["ended"], segment["num_turns"],
                          segment["transcript"]),
                         (1, "completed", 1, "transcripts/build-segment-1.json"))
        self.assertTrue((run.run_dir / "transcripts" / "build-segment-1.json").exists())
        self.assertTrue((run.run_dir / "transcripts" / "build.json").exists())
        build = next(s for s in run.state["steps"] if s["step"] == "build")
        self.assertEqual(build["segments"], self.segments(run))
        self.assertEqual(runrecord.read_state(run.run_dir)["segments"]["build"],
                         self.segments(run))

    def test_positive_a_stopped_and_resumed_build_keeps_both_segments(self):
        run = self.prepared()

        def dropped(prompt):
            self.h.builders[-1].last_messages.append(
                {"_type": "AssistantMessage", "content": "first part"})
            raise RuntimeError("the session dropped")

        self.h.builder_script = [dropped]
        asyncio.run(run.advance())
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertTrue((run.run_dir / "transcripts" / "build-partial.json").exists())
        self.h.builder_script = [edit(self.root, "done\n")]
        self.h.reviews = [[]]
        again = self.resume(run)
        self.assertEqual(self.reason(again), sr.CLEAN)
        first, second = self.segments(again)
        self.assertEqual([(s["segment"], s["ended"]) for s in (first, second)],
                         [(1, "error"), (2, "completed")])
        self.assertEqual((first["num_turns"], first["assistant_messages"]), (None, 1))
        folder = again.run_dir / "transcripts"
        one = json.loads((folder / "build-segment-1.json").read_text(encoding="utf-8"))
        two = json.loads((folder / "build-segment-2.json").read_text(encoding="utf-8"))
        self.assertIn({"_type": "AssistantMessage", "content": "first part"}, one)
        self.assertEqual(two, [{"n": 1}])
        build = next(s for s in again.state["steps"] if s["step"] == "build")
        self.assertEqual(len(build["segments"]), 2)

    def test_rejection_a_usage_limit_and_a_turn_limit_end_as_they_did(self):
        run = self.prepared()

        def limited(prompt):
            raise limits.UsageLimitError("claude", "using paid overage", 2000000)

        def capped(prompt):
            self.h.builders[-1].last_messages.extend([
                {"_type": "AssistantMessage", "content": "a"},
                {"_type": "AssistantMessage", "content": "b"},
                {"_type": "ResultMessage", "subtype": "error_max_turns", "num_turns": 80}])
            raise providers.ProviderError("the builder turn ended in error")

        self.h.builders = []
        builder = FakeBuilder(self.h, None)
        self.h.builders.append(builder)
        for act in (limited, capped):
            self.h.builder_script = [act]
            with self.assertRaises(Exception):
                asyncio.run(run._builder_turn("fix-R1", builder, "p"))
        first, second = self.segments(run, "fix-R1")
        self.assertEqual((first["ended"], first["num_turns"]), ("usage-limit", None))
        self.assertEqual((second["ended"], second["num_turns"],
                          second["assistant_messages"]), ("error", 80, 2))
        self.assertTrue((run.run_dir / "transcripts" / "fix-R1-segment-2.json").exists())

    def test_positive_five_hour_readings_with_a_reset_between_them(self):
        run = self.prepared()

        def act(prompt):
            for percent in (40, 55, 3, 10):
                run.note_claude_event(five_hour_event(percent))
            # An event about another window only is not a five-hour reading.
            run.note_claude_event(limits.Reading(
                limits.CLAUDE, limits.REPORTED, percent=90, window="seven_day",
                raw={"unifiedWindows": {"seven_day": {"utilization": 0.9}}}))
            return "Done."

        self.assertIsNone(self.turn(run, act))
        window = self.segments(run)[0]["five_hour"]
        self.assertEqual((window["first"], window["last"], window["readings"],
                          window["drops"]), (40, 10, 4, [{"from": 55, "to": 3}]))
        # An event outside any segment is not added to a finished one.
        run.note_claude_event(five_hour_event(70))
        self.assertEqual(self.segments(run)[0]["five_hour"]["last"], 10)

    def test_negative_a_segment_with_no_five_hour_event_has_no_readings(self):
        run = self.prepared()
        self.assertIsNone(self.turn(run, lambda prompt: "Done."))
        self.assertIsNone(self.segments(run)[0]["five_hour"])

    def test_rejection_ctrl_c_and_a_killed_process_leave_interrupted(self):
        run = self.prepared()
        seen = []

        def killed(prompt):
            # What a process killed here would leave: the record already on disk.
            seen.append(runrecord.read_state(run.run_dir)["segments"]["build"][-1])
            self.h.builders[-1].last_messages.append({"_type": "AssistantMessage"})
            raise KeyboardInterrupt

        self.h.builders = [FakeBuilder(self.h, None)]
        self.h.builder_script = [killed]
        with self.assertRaises(KeyboardInterrupt):
            asyncio.run(run._builder_turn("build", self.h.builders[0], "p"))
        self.assertEqual((seen[0]["ended"], seen[0]["transcript"]), ("interrupted", None))
        saved = runrecord.read_state(run.run_dir)["segments"]["build"][-1]
        self.assertEqual((saved["ended"], saved["transcript"]),
                         ("interrupted", "transcripts/build-segment-1.json"))



def windowed(five=(20, 19000), weekly=(10, 600000), source="reported", credits=CREDITS):
    """A Codex reading carrying its windows apart, as ``limits.classify_codex`` makes
    one from part (e): ``five`` and ``weekly`` are (percent, reset time), or None for a
    window the reading lacks."""
    windows = {}
    if five is not None:
        windows["five_hour"] = {"percent": five[0], "resets_at": five[1]}
    if weekly is not None:
        windows["weekly"] = {"percent": weekly[0], "resets_at": weekly[1]}
    shown = max((w for w in (five, weekly) if w is not None), default=(None, None))
    return dataclasses.replace(reading(percent=shown[0], resets=shown[1], source=source,
                                       credits=credits), windows=windows or None)


class CodexSegmentTests(SwappedCase):
    """Segment records for a Codex builder's build and fix steps (plan 10.10 items 7 and
    10, part (e)): one per turn, the reading made before the step first and a reading
    at the turn's end last, each with when it was read and its reset time, and a reset
    between two of them told by reset time; and every Codex reading keeping both
    windows, with ``codex_last_five_hour``."""

    def prepared(self):
        run = start_built("brief.md", "demo", str(self.personal), root=self.root,
                          deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                          settings=self.settings, builder="codex")
        self.clock = [1000]
        run.deps.now = lambda: self.clock[0]
        return run

    def turn(self, run, act, step="build", before=None, end_at=1600):
        """The reading before the step (``before``, through the real ``before_step``),
        then one Codex builder turn through ``_builder_turn``, the clock at ``end_at``
        once the turn is over; returns the exception the turn raised, or None."""
        self.h.readings = [before or windowed()]
        self.clock[0] = 1000
        self.assertIsNone(run.before_step(step, limits.CODEX))
        builder = FakeBuilder(self.h, None)
        self.h.builders.append(builder)

        def timed(prompt):
            self.clock[0] = end_at
            return act(prompt)
        self.h.builder_script = [timed]
        try:
            asyncio.run(run._builder_turn(step, builder, "p"))
        except BaseException as exc:  # noqa: BLE001 - the test inspects it
            return exc
        return None

    def segments(self, run, step="build"):
        return run.state["segments"][step]

    def test_positive_a_whole_codex_run_records_its_build_as_one_segment(self):
        self.h.readings = [windowed()] * 12
        self.h.watch_readings = [windowed(five=(23, 19000))]
        self.h.builder_script = [edit(self.root, "done\n")]
        self.h.reviews = [[]]
        run = self.start()
        [segment] = self.segments(run)
        readings = segment["five_hour"]["readings"]
        self.assertEqual([(r["at"], r["percent"], r["resets_at"]) for r in readings],
                         [("before-step", 20, 19000), ("turn-end", 23, 19000)])
        self.assertTrue(all(isinstance(r["read_at"], (int, float)) for r in readings))
        self.assertEqual((segment["ended"], segment["num_turns"],
                          segment["assistant_messages"], segment["five_hour"]["resets"]),
                         ("completed", None, None, []))
        self.assertTrue((run.run_dir / "transcripts" / "build-segment-1.json").exists())
        build = next(s for s in run.state["steps"] if s["step"] == "build")
        self.assertEqual(build["segments"], self.segments(run))
        # The turn-end reading was made on the watch's reader, from the frozen folder.
        self.assertEqual(self.h.watch_folders, [run.state["frozen_dir"]])
        saved = runrecord.read_state(run.run_dir)
        self.assertEqual(saved["segments"]["build"], self.segments(run))
        self.assertEqual({entry["step"]: entry["codex_windows"]
                          for entry in saved["readings"]}["build"],
                         {"five_hour": {"percent": 20, "resets_at": 19000},
                          "weekly": {"percent": 10, "resets_at": 600000}})
        self.assertEqual(saved["codex_last_five_hour"]["percent"], 20)

    def test_rejection_each_way_a_turn_ends_is_recorded(self):
        run = self.prepared()

        def limited(prompt):
            raise limits.UsageLimitError("codex", "usageLimitExceeded", 19000)

        def failed(prompt):
            raise RuntimeError("the turn failed")

        def outside(prompt):
            (self.root / "outside.txt").write_bytes(b"not in the edit paths\n")
            return "Done."

        for act, kind in ((limited, limits.UsageLimitError), (failed, RuntimeError),
                          (outside, loop.TurnCheckError)):
            self.assertIsInstance(self.turn(run, act, step="fix-R1"), kind)
        (self.root / "outside.txt").unlink()
        ended = [(s["ended"], [r["at"] for r in s["five_hour"]["readings"]])
                 for s in self.segments(run, "fix-R1")]
        self.assertEqual(ended, [("usage-limit", ["before-step", "turn-end"]),
                                 ("error", ["before-step", "turn-end"]),
                                 ("error", ["before-step", "turn-end"])])

    def test_rejection_ctrl_c_leaves_the_segment_interrupted_with_no_turn_end_reading(self):
        run = self.prepared()

        def killed(prompt):
            raise KeyboardInterrupt
        self.assertIsInstance(self.turn(run, killed), KeyboardInterrupt)
        [segment] = runrecord.read_state(run.run_dir)["segments"]["build"]
        self.assertEqual(segment["ended"], "interrupted")
        self.assertEqual([r["at"] for r in segment["five_hour"]["readings"]], ["before-step"])
        self.assertEqual(self.h.watch_folders, [])

    def test_positive_a_resumed_step_has_a_segment_per_turn_each_from_its_own_reading(self):
        run = self.prepared()

        def failed(prompt):
            raise RuntimeError("dropped")
        self.h.watch_readings = [windowed(), windowed(five=(5, 37000))]
        self.turn(run, failed, before=windowed(five=(30, 19000)))
        self.assertIsNone(self.turn(run, lambda prompt: "Done.",
                                    before=windowed(five=(4, 37000))))
        first, second = self.segments(run)
        self.assertEqual([(s["segment"], s["ended"]) for s in (first, second)],
                         [(1, "error"), (2, "completed")])
        self.assertEqual([[r["percent"] for r in s["five_hour"]["readings"]]
                          for s in (first, second)], [[30, 20], [4, 5]])

    def test_negative_a_failed_turn_end_reading_is_recorded_retried_and_the_turn_kept(self):
        run = self.prepared()
        self.h.watch_readings = [RuntimeError("the client would not start"),
                                 windowed(five=(20, 19000)),
                                 reading(source="unavailable", credits=None),
                                 windowed(five=(21, 19000))]
        for _ in range(2):
            self.assertIsNone(self.turn(run, lambda prompt: "Done."))
        failed = [s["five_hour"]["readings"][-2] for s in self.segments(run)]
        # Judged from part (e2b): an unavailable answer is failed for its missing
        # credit snapshot, its own reason kept in the text.
        self.assertEqual([(r["at"], r["failed"]) for r in failed],
                         [("turn-end", "RuntimeError: the client would not start"),
                          ("turn-end", "the reading carried no credit snapshot "
                                       "(Codex usage not reported: x)")])
        # Retried once after a minute (code review R76-3), and the retry answered.
        self.assertEqual([(r["at"], r["percent"]) for r in
                          (s["five_hour"]["readings"][-1] for s in self.segments(run))],
                         [("turn-end", 20), ("turn-end", 21)])
        # A failed turn-end reading whose retry answers stops nothing.
        self.assertTrue(all("would_stop" not in s for s in self.segments(run)))
        self.assertTrue(all(s["ended"] == "completed" for s in self.segments(run)))
        # A failed reading is not a pair to test for a reset.
        self.assertTrue(all(s["five_hour"]["resets"] == [] == s["five_hour"]["unknown_resets"]
                            for s in self.segments(run)))

    def test_negative_the_turn_end_reading_never_sets_the_runs_unavailable_latch(self):
        run = self.prepared()
        self.h.watch_readings = [reading(source="unavailable", credits=None)]
        self.assertIsNone(self.turn(run, lambda prompt: "Done."))
        self.assertIsNone(run.usage.codex_negative)
        self.assertNotEqual(run.state.get("usage_source", {}).get("codex"),
                            limits.UNAVAILABLE)

    def test_positive_a_reset_between_the_readings_is_told_by_reset_time(self):
        run = self.prepared()
        cases = [((22, 19600), "none"),   # unused-window drift, allowed for
                 ((25, 37000), "reset"),   # about five hours on
                 ((22, None), "unknown")]  # a reset time the test needs is unknown
        for five, _ in cases:
            self.h.watch_readings = [windowed(five=five)]
            self.assertIsNone(self.turn(run, lambda prompt: "Done."))
        found = [("reset" if s["five_hour"]["resets"] else
                  "unknown" if s["five_hour"]["unknown_resets"] else "none")
                 for s in self.segments(run)]
        self.assertEqual(found, [kind for _, kind in cases])
        pair = self.segments(run)[1]["five_hour"]["resets"][0]
        self.assertEqual(([pair["before"]["at"], pair["after"]["at"]],
                          pair["after"]["read_at"] - pair["before"]["read_at"]),
                         (["before-step", "turn-end"], 600))

    def test_positive_codex_last_five_hour_follows_five_hour_readings_only(self):
        run = self.prepared()
        self.h.watch_readings = [windowed(five=(27, 19000))]
        self.assertIsNone(self.turn(run, lambda prompt: "Done."))
        self.assertEqual(run.state["codex_last_five_hour"],
                         {"percent": 27, "resets_at": 19000, "read_at": 1600})
        # A weekly-only reading before a step leaves it as it was.
        self.h.readings = [windowed(five=None)]
        self.assertIsNone(run.before_step("review-R1", limits.CODEX))
        self.assertEqual(run.state["codex_last_five_hour"]["percent"], 27)
        self.assertEqual(run.state["readings"][-1]["codex_windows"],
                         {"weekly": {"percent": 10, "resets_at": 600000}})

    def test_negative_a_claude_event_during_a_codex_segment_is_not_added_to_it(self):
        run = self.prepared()

        def act(prompt):
            run.note_claude_event(five_hour_event(70))
            return "Done."
        self.assertIsNone(self.turn(run, act))
        self.assertEqual([r["at"] for r in self.segments(run)[0]["five_hour"]["readings"]],
                         ["before-step", "turn-end"])


def old_codex_before_step(codex, step, permitted, ceiling, state):
    """The Codex half of ``Run.before_step`` as it was before part (e2a) moved it into
    ``loop.codex_stop``, kept here word for word (but for its names) so the move is
    shown to change nothing."""
    stop = loop._usage_stop([codex], ceiling, allow_paid=permitted)
    if stop is not None:
        return {"step": step, "provider": stop.provider, "detail": stop.reason,
                "reading": stop.reading.summary(), "resets_at": stop.reading.resets_at,
                "paid_usage": (loop._paid_overage(stop.reading) or
                               stop.reading.reason ==
                               "ordinaryUsageAllowed is null or absent")}
    if not permitted:
        now = loop.credit_snapshot(codex)
        recorded = state.get("credits")
        then = recorded or {}
        if now is None:
            return {"step": step, "provider": limits.CODEX, "resets_at": None,
                    "detail": "the Codex credit snapshot is missing from the reading "
                              f"({codex.summary()})", "paid_usage": True}
        unusable = loop.untrackable(recorded) if "credits" in state else None
        if unusable is not None:
            return {"step": step, "provider": limits.CODEX, "resets_at": None,
                    "detail": "the Codex credit snapshot recorded at preflight could "
                              f"not be tracked ({unusable})", "paid_usage": True}
        differs = [key for key in ("hasCredits", "unlimited", "balance")
                   if now.get(key) != then.get(key)]
        if differs:
            return {"step": step, "provider": limits.CODEX, "resets_at": None,
                    "detail": "the Codex credit snapshot changed since preflight ("
                              + ", ".join(f"{key} {then.get(key)!r} -> {now[key]!r}"
                                          for key in differs) + ")",
                    "paid_usage": True}
        if loop._reached_type(codex) and now.get("hasCredits"):
            return {"step": step, "provider": limits.CODEX, "resets_at":
                    codex.resets_at, "detail": "Codex reports its included allowance "
                    f"reached ({loop._reached_type(codex)}) with credits next",
                    "paid_usage": True}
    return None


def refused(reason):
    """A Codex reading reporting a limit, ``reason`` being ``limits.classify_codex``'s."""
    return dataclasses.replace(reading(percent=10, source="limit-reported"), reason=reason)


FALSE = "ordinaryUsageAllowed is false"
ABSENT = "ordinaryUsageAllowed is null or absent"


class WatchRuleTests(unittest.TestCase):
    """Part (e2a), plan 10.10 item 7: the Codex stop rules shared by the check before
    a step and the watch during a Codex builder's step (``codex_stop``), how a watch
    reading is judged (``judge_watch_reading``) and when the next one comes
    (``next_watch_delay``). Pure functions; E2b runs them beside the turn."""

    STATE = {"credits": dict(CREDITS)}

    def judge(self, answer, *, permitted=False, state=None, negative=None,
              failed_before=None):
        return loop.judge_watch_reading(answer, "build", permitted=permitted, ceiling=80,
                                        state=self.STATE if state is None else state,
                                        negative=negative, failed_before=failed_before)

    def readings_table(self):
        changed = dict(CREDITS, balance="1.00")
        return {
            "low": reading(percent=30), "at the ceiling": reading(percent=80),
            "false": refused(FALSE), "absent": refused(ABSENT),
            "unavailable": reading(source="unavailable", credits=None),
            "unavailable with credits": reading(source="unavailable"),
            "no credits": reading(credits=None), "credits changed": reading(credits=changed),
            "reached, credits next": reading(reached="primary"),
            "reached, no credits": reading(reached="primary",
                                           credits=dict(CREDITS, hasCredits=False)),
        }

    def test_positive_codex_stop_decides_exactly_as_the_old_before_step_code(self):
        states = {"trackable": {"credits": dict(CREDITS)},
                  "untrackable": {"credits": {"hasCredits": True, "unlimited": False,
                                              "balance": "absent"}},
                  "never recorded": {}}
        for name, answer in self.readings_table().items():
            for label, state in states.items():
                for permitted in (False, True):
                    with self.subTest(reading=name, state=label, permitted=permitted):
                        self.assertEqual(
                            loop.codex_stop(answer, "build", permitted=permitted,
                                            ceiling=80, state=state),
                            old_codex_before_step(answer, "build", permitted, 80, state))

    def test_rejection_false_stops_first_with_permission_and_without_credits(self):
        bare = dataclasses.replace(refused(FALSE), raw={"rateLimits": {}})
        for permitted in (False, True):
            decision = self.judge(bare, permitted=permitted, failed_before="earlier")
            self.assertEqual((decision.action, decision.failure),
                             (loop.WATCH_STOP, None))
            self.assertIn(FALSE, decision.stop["detail"])

    def test_rejection_a_failure_is_retried_once_then_stops_without_permission(self):
        first = self.judge(RuntimeError("no client"))
        self.assertEqual((first.action, first.delay, first.failure),
                         (loop.WATCH_RETRY_ACTION, 60, "RuntimeError: no client"))
        second = self.judge(reading(source="unavailable", credits=None),
                            failed_before=first.failure)
        self.assertEqual(second.action, loop.WATCH_STOP)
        self.assertIn("RuntimeError: no client", second.stop["detail"])
        self.assertIn("no credit snapshot", second.stop["detail"])
        self.assertTrue(second.stop["paid_usage"])

    def test_negative_with_permission_a_failure_never_stops(self):
        for failed_before in (None, "an earlier failure"):
            decision = self.judge(RuntimeError("down"), permitted=True,
                                  failed_before=failed_before)
            self.assertEqual((decision.action, decision.delay, decision.stop),
                             (loop.WATCH_GO, 600, None))
            self.assertEqual(decision.failure, "RuntimeError: down")

    def test_positive_a_retry_that_answers_is_judged_as_any_reading(self):
        self.assertEqual(self.judge(reading(percent=30), failed_before="x").action,
                         loop.WATCH_GO)
        high = self.judge(reading(percent=85), failed_before="x")
        self.assertEqual(high.action, loop.WATCH_STOP)
        self.assertIn("ceiling", high.stop["detail"])

    def test_rejection_every_before_step_rule_stops_the_watch_too(self):
        for name in ("at the ceiling", "absent", "credits changed",
                     "reached, credits next"):
            with self.subTest(reading=name):
                self.assertEqual(self.judge(self.readings_table()[name]).action,
                                 loop.WATCH_STOP)
        permitted = self.judge(self.readings_table()["credits changed"], permitted=True)
        self.assertEqual(permitted.action, loop.WATCH_GO)

    def test_negative_the_watch_never_sets_the_latch_and_honours_one_held(self):
        held = limits.Reading(limits.CODEX, limits.UNAVAILABLE, reason="down earlier")
        # Held: the ceiling is not applied, the credit and hard-refusal checks are.
        self.assertEqual(self.judge(reading(percent=95), negative=held).action,
                         loop.WATCH_GO)
        changed = reading(percent=95, credits=dict(CREDITS, balance="1.00"))
        self.assertEqual(self.judge(changed, negative=held).action, loop.WATCH_STOP)
        self.assertEqual(self.judge(refused(FALSE), negative=held).action, loop.WATCH_STOP)
        # Not held: the ceiling applies, and judging a failure leaves nothing behind.
        self.assertEqual(self.judge(reading(percent=95)).action, loop.WATCH_STOP)
        state = {"credits": dict(CREDITS)}
        self.judge(RuntimeError("down"), state=state)
        self.assertEqual(state, {"credits": dict(CREDITS)})

    def entry(self, read_at, percent, resets_at=19000):
        return {"at": "watch", "read_at": read_at, "percent": percent,
                "resets_at": resets_at}

    def test_positive_the_next_reading_comes_at_half_the_projected_time(self):
        # 2 points in 2 minutes, 40 points of room: 40 minutes away, so 20 minutes,
        # held to the 10-minute bound; 10 points of room, 10 minutes, so 5.
        self.assertEqual(loop.next_watch_delay(
            [self.entry(0, 38), self.entry(120, 40)], 80), 600)
        self.assertEqual(loop.next_watch_delay(
            [self.entry(0, 68), self.entry(120, 70)], 80), 300)
        # Fast: 10 points a minute with 5 left is 30 seconds, held to the 1-minute floor.
        self.assertEqual(loop.next_watch_delay(
            [self.entry(0, 65), self.entry(60, 75)], 80), 60)

    def test_negative_no_rise_a_reset_or_too_few_readings_gives_the_longest_wait(self):
        for readings in ([self.entry(0, 40), self.entry(120, 40)],
                         [self.entry(0, 40), self.entry(120, 30)],
                         [self.entry(0, 40), self.entry(120, 45, resets_at=37000)],
                         [self.entry(0, 40), self.entry(120, 45, resets_at=None)],
                         [self.entry(0, 40)],
                         [self.entry(0, 40), {"at": "watch", "read_at": 120,
                                              "failed": "x"}]):
            with self.subTest(readings=readings):
                self.assertEqual(loop.next_watch_delay(readings, 80), 600)

    def test_positive_a_failed_reading_is_skipped_for_the_rate(self):
        readings = [self.entry(0, 68), {"at": "watch", "read_at": 60, "failed": "x"},
                    self.entry(120, 70)]
        self.assertEqual(loop.next_watch_delay(readings, 80), 300)

    def test_rejection_no_room_left_gives_the_shortest_wait(self):
        self.assertEqual(loop.next_watch_delay(
            [self.entry(0, 78), self.entry(120, 81)], 80), 60)

    def test_positive_the_fixed_figures_are_the_plans(self):
        self.assertEqual((loop.WATCH_FIRST, loop.WATCH_MIN, loop.WATCH_MAX,
                          loop.WATCH_RETRY), (120, 60, 600, 60))


class CodexWatchTests(unittest.TestCase):
    """``loop.CodexWatch`` (part (e2b), plan 10.10 item 7) on its own thread against a
    stand-in run, its schedule run a thousand times faster and its clock moving by the
    schedule's own seconds, so each reading's time is the one the live watch would
    have."""

    BEFORE = {"at": "before-step", "read_at": 1000, "percent": 20, "resets_at": 19000}

    def setUp(self):
        self.answers = []
        self.drained = threading.Event()
        self.interrupts = []
        self.state = {"credits": dict(CREDITS)}
        self.usage = limits.RunUsage()
        self.kept = []
        self.watch = None

        def read(root, folder=None):
            answer = self.answers.pop(0) if self.answers else None
            if not self.answers:
                self.drained.set()
            if callable(answer) and not isinstance(answer, BaseException):
                answer = answer()
            if answer is None:
                answer = windowed(five=(20, 19000))
            if isinstance(answer, BaseException):
                raise answer
            return answer
        deps = SimpleNamespace(watch_scale=0.001, read_codex_watch=read,
                               now=lambda: 1000 + sum(self.watch.delays))
        self.run = SimpleNamespace(state=self.state, usage=self.usage, deps=deps,
                                   settings={"usage_ceiling_percent": 80},
                                   root=Path("."),
                                   _keep_codex_five_hour=self.kept.append)
        self.segment = {"segment": 1, "five_hour": {"readings": [dict(self.BEFORE)],
                                                    "resets": [], "unknown_resets": []}}

    def watched(self, *answers, until="drained", step="build", interrupt=None):
        """Start a watch on a turn, then end the turn and finish; return the watch. The
        turn runs ``until`` the scripted readings are used up (``drained``), until the
        watch's thread ends by itself (``thread``), or not at all (None). ``interrupt``
        replaces the turn's interrupt, which by default records itself and returns."""
        self.answers = list(answers)
        self.watch = loop.CodexWatch(self.run, step, self.segment)
        self.watch.start(interrupt or (lambda: self.interrupts.append(True)))
        if until == "drained":
            self.assertTrue(self.drained.wait(10), "the watch made its readings")
            for _ in range(200):  # give a decision under way time to land
                if self.watch.stopped is not None or not self.watch._thread.is_alive():
                    break
                time.sleep(0.005)
        elif until == "thread":
            self.watch._thread.join(10)
            self.assertFalse(self.watch._thread.is_alive())
        self.watch.turn_ended()
        self.watch.finish()
        return self.watch

    def readings(self):
        return self.segment["five_hour"]["readings"]

    def test_positive_the_first_reading_at_two_minutes_then_the_schedule(self):
        # 22 to 60 in the ten minutes from 1120 to 1720: 3.8 points a minute, 20 left,
        # 5.26 minutes away, so the next at half of it, 157 seconds.
        watch = self.watched(windowed(five=(22, 19000)), windowed(five=(60, 19000)),
                             windowed(five=(61, 19000)))
        self.assertEqual(watch.delays[:3], [120, 600, 157])
        self.assertEqual([r["at"] for r in self.readings()][:4],
                         ["before-step", "watch", "watch", "watch"])
        self.assertEqual([r["read_at"] for r in self.readings()][1:3], [1120, 1720])
        self.assertIsNone(watch.stopped)
        self.assertEqual(self.readings()[-1]["at"], "turn-end")

    def test_rejection_a_failure_is_retried_after_a_minute_and_a_second_stops_the_turn(self):
        watch = self.watched(RuntimeError("one"), RuntimeError("two"))
        self.assertEqual(watch.delays[:2], [120, 60])
        self.assertEqual(self.interrupts, [True])
        self.assertIn("RuntimeError: one; then RuntimeError: two", watch.stopped["detail"])
        self.assertTrue(watch.stopped["paid_usage"])
        self.assertEqual(self.segment["watch_stop"], watch.stopped)
        # A turn the watch stopped has no reading after its stopping one.
        self.assertEqual([r.get("failed") for r in self.readings()[1:]],
                         ["RuntimeError: one", "RuntimeError: two"])

    def test_positive_a_retry_that_answers_goes_back_to_the_schedule(self):
        watch = self.watched(RuntimeError("one"), windowed(five=(21, 19000)),
                             windowed(five=(21, 19000)))
        self.assertEqual(watch.delays[:3], [120, 60, 600])
        self.assertIsNone(watch.stopped)
        self.assertEqual(self.interrupts, [])

    def test_negative_with_paid_permission_failures_never_stop(self):
        self.state["paid_usage_pending"] = "build"
        watch = self.watched(RuntimeError("one"), RuntimeError("two"), RuntimeError("three"))
        self.assertEqual(watch.delays[:3], [120, 600, 600])
        self.assertIsNone(watch.stopped)
        self.assertEqual(self.interrupts, [])

    def test_rejection_a_reading_at_the_ceiling_interrupts_the_running_turn(self):
        watch = self.watched(windowed(five=(85, 19000)))
        self.assertEqual(self.interrupts, [True])
        self.assertIn("ceiling", watch.stopped["detail"])
        self.assertEqual(watch.stopped["resets_at"], 19000)
        self.assertNotIn("would_stop", self.segment)

    def test_negative_a_stop_decided_after_the_turn_ended_interrupts_nothing(self):
        def late():
            self.watch.turn_ended()  # turn/completed arrived while the reading ran
            return windowed(five=(85, 19000))
        watch = self.watched(late, windowed(five=(86, 19000)), until="thread")
        self.assertEqual(self.interrupts, [])
        self.assertIsNone(watch.stopped)
        self.assertIn("ceiling", self.segment["would_stop"]["detail"])

    def test_rejection_the_turn_end_reading_is_judged_but_stops_nothing(self):
        watch = self.watched(windowed(five=(90, 19000)), until=None)
        self.assertEqual(self.interrupts, [])
        self.assertIsNone(watch.stopped)
        self.assertEqual(self.readings()[-1]["at"], "turn-end")
        self.assertIn("ceiling", self.segment["would_stop"]["detail"])

    def test_negative_anything_wrong_in_a_reading_is_a_failed_reading(self):
        # Not a reading at all: judging it raises, and that is a failure, retried once.
        watch = self.watched(lambda: 42, lambda: 42)
        self.assertEqual([r["at"] for r in self.readings()[1:]], ["watch", "watch"])
        self.assertTrue(all("failed" in r for r in self.readings()[1:]))
        self.assertIsNotNone(watch.stopped)

    def test_negative_the_watch_never_sets_the_latch_and_honours_one_held(self):
        self.watched(reading(source="unavailable"), windowed(five=(30, 19000)))
        self.assertIsNone(self.usage.codex_negative)
        self.usage.codex_negative = limits.Reading(limits.CODEX, limits.UNAVAILABLE,
                                                   reason="down before the step")
        self.segment["five_hour"]["readings"] = [dict(self.BEFORE)]
        self.interrupts.clear()
        watch = self.watched(windowed(five=(95, 19000)))
        self.assertIsNone(watch.stopped)
        self.assertEqual(self.interrupts, [])

    def test_positive_five_hour_readings_are_kept_as_the_last_one(self):
        self.watched(windowed(five=(33, 19000)))
        self.assertTrue(self.kept)
        self.assertEqual(self.kept[0].windows["five_hour"]["percent"], 33)

    # Code review R76-2: an interrupt that raises is not a stop.

    def failing_interrupt(self, times):
        """An interrupt that raises its first ``times`` calls, then works."""
        def interrupt():
            self.interrupts.append(True)
            if len(self.interrupts) <= times:
                raise RuntimeError(f"interrupt {len(self.interrupts)} refused")
        return interrupt

    def test_rejection_a_failed_interrupt_keeps_watching_and_tries_again(self):
        watch = self.watched(windowed(five=(85, 19000)), windowed(five=(86, 19000)),
                             interrupt=self.failing_interrupt(1), until="thread")
        self.assertEqual(watch.delays[:2], [120, 60])
        self.assertEqual(len(self.interrupts), 2)
        self.assertEqual([entry["error"] for entry in self.segment["watch_interrupt_failed"]],
                         ["RuntimeError: interrupt 1 refused"])
        self.assertTrue(watch.interrupted)
        self.assertEqual(self.segment["watch_stop"], watch.stopped)
        self.assertIn("86%", watch.stopped["reading"])
        # The second, successful stop's reading is the last.
        self.assertEqual(self.readings()[-1]["percent"], 86)

    def test_rejection_a_turn_that_ends_after_failed_interrupts_gets_its_turn_end_reading(self):
        # The third reading, under the ceiling, comes after both failed interrupts.
        watch = self.watched(windowed(five=(85, 19000)), windowed(five=(86, 19000)),
                             windowed(five=(20, 19000)), interrupt=self.failing_interrupt(99))
        self.assertEqual(len(self.segment["watch_interrupt_failed"]), 2)
        self.assertFalse(watch.interrupted)
        self.assertNotIn("watch_stop", self.segment)
        # Kept, so a turn that was interrupted after all still ends as this stop.
        self.assertIsNotNone(watch.stopped)
        self.assertEqual(self.readings()[-1]["at"], "turn-end")
        self.assertNotIn("failed", self.readings()[-1])

    # Code review R77-1: an interrupt never answered is a failed interrupt, not a wait.

    def silent_interrupt(self, times):
        """An interrupt whose first ``times`` calls are never answered (until the test
        ends), then answered at once."""
        released = threading.Event()
        self.addCleanup(released.set)

        def interrupt():
            self.interrupts.append(True)
            if len(self.interrupts) <= times:
                released.wait(30)
        return interrupt

    def test_rejection_an_unanswered_interrupt_is_failed_and_tried_again(self):
        watch = self.watched(windowed(five=(85, 19000)), windowed(five=(86, 19000)),
                             interrupt=self.silent_interrupt(1), until="thread")
        self.assertEqual(watch.delays[:2], [120, 60])
        self.assertEqual([entry["error"] for entry in self.segment["watch_interrupt_failed"]],
                         ["TimeoutError: the interrupt was not answered within 60 seconds"])
        self.assertTrue(watch.interrupted)
        self.assertEqual(self.segment["watch_stop"], watch.stopped)

    def test_rejection_a_turn_ending_under_unanswered_interrupts_still_finishes(self):
        started = time.monotonic()
        watch = self.watched(windowed(five=(85, 19000)), windowed(five=(86, 19000)),
                             windowed(five=(20, 19000)), interrupt=self.silent_interrupt(99))
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(len(self.segment["watch_interrupt_failed"]), 2)
        self.assertFalse(watch.interrupted)
        self.assertIsNotNone(watch.stopped)
        self.assertEqual(self.readings()[-1]["at"], "turn-end")
        self.assertNotIn("failed", self.readings()[-1])

    def test_positive_the_interrupt_wait_is_a_minute(self):
        self.assertEqual(loop.WATCH_INTERRUPT_TIMEOUT, 60)

    # Code review R76-3: a failure as the turn ends is retried, not dropped.

    def test_rejection_a_failure_pending_at_the_turn_end_is_carried_into_its_reading(self):
        def first():
            self.watch.turn_ended()  # the turn completed while this reading failed
            raise RuntimeError("one")
        watch = self.watched(first, RuntimeError("two"), until="thread")
        self.assertEqual(self.interrupts, [])
        self.assertIsNone(watch.stopped)
        self.assertEqual([(r["at"], r.get("failed")) for r in self.readings()[1:]],
                         [("watch", "RuntimeError: one"), ("turn-end", "RuntimeError: two")])
        self.assertIn("RuntimeError: one; then RuntimeError: two",
                      self.segment["would_stop"]["detail"])

    def test_positive_a_failed_turn_end_reading_is_retried_after_a_minute(self):
        watch = self.watched(RuntimeError("one"), windowed(five=(25, 19000)), until=None)
        self.assertEqual(watch.delays, [120, 60])
        self.assertEqual([(r["at"], r.get("failed")) for r in self.readings()[1:]],
                         [("turn-end", "RuntimeError: one"), ("turn-end", None)])
        self.assertEqual(self.readings()[-1]["percent"], 25)
        self.assertNotIn("would_stop", self.segment)

    def test_negative_with_paid_permission_a_failed_turn_end_reading_is_not_retried(self):
        self.state["paid_usage_pending"] = "build"
        watch = self.watched(RuntimeError("one"), until=None)
        self.assertEqual(watch.delays, [120])
        self.assertEqual(self.readings()[-1].get("failed"), "RuntimeError: one")
        self.assertNotIn("would_stop", self.segment)


class CodexWatchRunTests(SwappedCase):
    """The watch in whole Codex-built runs (part (e2b)): a stop mid-turn ends the run
    ``usage-limit`` with the work kept and a resume continues the same thread; a watch
    that never stops changes nothing."""

    def hold_until_interrupted(self, text="Done."):
        def act(prompt):
            (self.root / "work" / "a.txt").write_bytes(b"part of the work\n")
            self.h.builders[-1].interrupted.wait(10)
            return text
        return act

    def test_rejection_a_watch_stop_mid_turn_ends_the_run_usage_limit_and_a_resume_continues(self):
        self.h.watch_readings = [windowed(five=(90, 19000))]
        self.h.builder_script = [self.hold_until_interrupted()]
        run = self.start()
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        evidence = run.state["stop_reasons"][-1]["evidence"]
        self.assertEqual((evidence["step"], evidence["provider"], evidence["resets_at"],
                          evidence["paid_usage"]), ("build", limits.CODEX, 19000, False))
        self.assertIn("ceiling", evidence["detail"])
        [segment] = run.state["segments"]["build"]
        self.assertEqual(segment["ended"], "usage-limit")
        self.assertIn("ceiling", segment["watch_stop"]["detail"])
        self.assertTrue((run.run_dir / "partial" / "build.patch").exists())
        self.assertIn("part of the work",
                      (run.run_dir / "partial" / "build.patch").read_text(encoding="utf-8"))
        # The resume continues the same thread, and the build completes.
        self.h.watch_readings = []
        self.h.builder_script = [edit(self.root, "done\n")]
        self.h.reviews = [[]]
        again = self.resume(run)
        self.assertEqual(self.h.builders[-1].resume, "s-1")
        self.assertEqual([s["ended"] for s in again.state["segments"]["build"]],
                         ["usage-limit", "completed"])

    def test_rejection_two_failed_watch_readings_stop_the_run_as_a_paid_usage_stop(self):
        self.h.watch_readings = [RuntimeError("one"), RuntimeError("two")]
        self.h.builder_script = [self.hold_until_interrupted()]
        run = self.start()
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        evidence = run.state["stop_reasons"][-1]["evidence"]
        self.assertTrue(evidence["paid_usage"])
        self.assertIn("RuntimeError: one; then RuntimeError: two", evidence["detail"])
        self.assertIn("--allow-paid", (run.run_dir / "report.md").read_text(encoding="utf-8"))

    def test_negative_a_watch_that_never_stops_leaves_the_run_as_before(self):
        self.h.watch_readings = [windowed(five=(25, 19000))] * 3

        def slow(prompt):
            (self.root / "work" / "a.txt").write_bytes(b"done\n")
            time.sleep(0.3)  # long enough for the first reading at 0.12 s
            return "Changed work/a.txt."
        self.h.builder_script = [slow]
        self.h.reviews = [[]]
        run = self.start()
        self.assertNotEqual(self.reason(run), sr.USAGE_LIMIT)
        [segment] = run.state["segments"]["build"]
        self.assertIn("watch", [r["at"] for r in segment["five_hour"]["readings"]])
        self.assertNotIn("watch_stop", segment)
        self.assertEqual(segment["ended"], "completed")


if __name__ == "__main__":
    unittest.main()
