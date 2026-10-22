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
                           reading(20, credits=dict(CREDITS, balance="4.25"))]
        run = start_built("brief.md", "demo", str(self.personal), root=self.root,
                          deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                          settings=self.settings, builder="claude", allow_paid=True)
        asyncio.run(run.advance())
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
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


if __name__ == "__main__":
    unittest.main()
