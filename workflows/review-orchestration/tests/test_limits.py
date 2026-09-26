#!/usr/bin/env python3
"""Hermetic tests for the usage-limit readings (limits.py) and settings (settings.py).

Same three controls as test_brief.py:

    positive control  a legal instance built from the specification or recorded
                      from a real provider. It must read as the plan says.
    rejection control an instance the plan says stops a run, or refuses a setting.
                      It must be refused, with the reason named.
    negative control  something that looks like the subject but is not an instance
                      of it (throttling that is not the allowance, an event with no
                      utilisation). It must not be taken for it.

No test makes a model call or starts a Codex app-server: Codex responses are built
from the SDK's own response model and handed to a fake client, and the Claude event
is the one recorded in the B1 probe transcript, parsed by the SDK's own parser. The
tests that need an SDK are skipped, with the reason, when it is not installed; the
workflow's requirements.txt installs both.

    python workflows/review-orchestration/tests/test_limits.py
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

WORKFLOW = Path(__file__).resolve().parent.parent
SCRIPTS = WORKFLOW / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


limits = _load("limits")
settings = _load("settings")

try:
    from openai_codex.errors import (CodexError, InternalRpcError,
                                     TransportClosedError)
    from openai_codex.generated.v2_all import GetAccountRateLimitsResponse, TurnError
    HAVE_CODEX = True
except ImportError:
    HAVE_CODEX = False

try:
    from claude_agent_sdk._internal.message_parser import parse_message
    HAVE_CLAUDE = True
except ImportError:
    HAVE_CLAUDE = False

NEEDS_CODEX = unittest.skipUnless(HAVE_CODEX, "openai-codex is not installed; see "
                                  "workflows/review-orchestration/requirements.txt")
NEEDS_CLAUDE = unittest.skipUnless(HAVE_CLAUDE, "claude-agent-sdk is not installed; see "
                                   "workflows/review-orchestration/requirements.txt")

# The shape the live app-server returned on 2026-09-24 (plan section 10.10): both
# windows present, ordinary usage allowed. The percentages here are made up.
LIVE_SHAPE = {
    "ordinaryUsageAllowed": True,
    "rateLimits": {
        "primary": {"usedPercent": 12, "resetsAt": 1790190000, "windowDurationMins": 300},
        "secondary": {"usedPercent": 44, "resetsAt": 1790492400,
                      "windowDurationMins": 10080},
    },
}

# The rate-limit event recorded by the B1 probe (orchestration-scratch
# b1/out/claude-unmarked.json), in the CLI's wire form. Its top-level utilization is
# absent: the numbers are only in unifiedWindows.
B1_EVENT = {
    "type": "rate_limit_event",
    "uuid": "b1-event",
    "session_id": "b1-session",
    "rate_limit_info": {
        "status": "allowed",
        "resetsAt": 1790190000,
        "rateLimitType": "five_hour",
        "overageStatus": "allowed",
        "overageResetsAt": 1790812800,
        "isUsingOverage": False,
        "unifiedWindows": {
            "five_hour": {"utilization": 0.09, "resetsAt": 1790190000},
            "seven_day": {"utilization": 0.43, "resetsAt": 1790492400},
        },
    },
}


def codex_response(payload):
    return GetAccountRateLimitsResponse.model_validate(payload)


class FakeClient:
    """Stands in for CodexClient.request: validates like the real one, or raises."""

    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def request(self, method, params, *, response_model):
        self.calls.append((method, params, response_model))
        if self.error is not None:
            raise self.error
        if not isinstance(self.payload, dict):
            raise CodexError(f"{method} response must be a JSON object")
        return response_model.model_validate(self.payload)


def info(status="allowed", rate_limit_type="five_hour", utilization=None,
         resets_at=None, raw=None):
    """A duck-typed RateLimitInfo, for the cases the B1 transcript does not cover."""
    return SimpleNamespace(status=status, rate_limit_type=rate_limit_type,
                           utilization=utilization, resets_at=resets_at, raw=raw or {})


# ----------------------------------------------------------------------- Codex


@NEEDS_CODEX
class CodexReadingTests(unittest.TestCase):

    def test_positive_live_shape_reads_the_higher_window(self):
        reading = limits.classify_codex(codex_response(LIVE_SHAPE))
        self.assertEqual(reading.source, limits.REPORTED)
        self.assertEqual(reading.percent, 44)
        self.assertEqual(reading.resets_at, 1790492400)
        self.assertEqual(reading.window, "secondary, 10080 min")
        self.assertEqual(reading.raw["rateLimits"]["primary"]["usedPercent"], 12)

    def test_positive_one_window_is_enough(self):
        payload = {"rateLimits": {"primary": {"usedPercent": 7, "resetsAt": 5}}}
        reading = limits.classify_codex(codex_response(payload))
        self.assertEqual((reading.source, reading.percent, reading.resets_at, reading.window),
                         (limits.REPORTED, 7, 5, "primary"))

    def test_positive_tie_takes_the_later_reset(self):
        payload = {"rateLimits": {"primary": {"usedPercent": 50, "resetsAt": 100},
                                  "secondary": {"usedPercent": 50, "resetsAt": 900}}}
        reading = limits.classify_codex(codex_response(payload))
        self.assertEqual((reading.window, reading.resets_at), ("secondary", 900))

    def test_positive_tie_prefers_a_known_reset_over_an_unknown_one(self):
        payload = {"rateLimits": {"primary": {"usedPercent": 50, "resetsAt": 100},
                                  "secondary": {"usedPercent": 50}}}
        reading = limits.classify_codex(codex_response(payload))
        self.assertEqual((reading.window, reading.resets_at), ("primary", 100))

    def test_rejection_usage_not_allowed_wins_over_low_windows(self):
        payload = dict(LIVE_SHAPE, ordinaryUsageAllowed=False)
        reading = limits.classify_codex(codex_response(payload))
        self.assertEqual(reading.source, limits.LIMIT_REPORTED)
        self.assertEqual(reading.reason, "ordinaryUsageAllowed is false")
        self.assertEqual((reading.percent, reading.resets_at), (44, 1790492400))

    def test_rejection_usage_not_allowed_with_no_window(self):
        reading = limits.classify_codex(
            codex_response({"ordinaryUsageAllowed": False, "rateLimits": {}}))
        self.assertEqual(reading.source, limits.LIMIT_REPORTED)
        self.assertIsNone(reading.percent)
        self.assertIsNone(reading.resets_at)
        self.assertIn("usage unavailable", reading.summary())
        self.assertIn("resets at unavailable", reading.summary())

    def test_negative_no_window_and_no_verdict_is_unavailable(self):
        for allowed in (True, None):
            payload = {"rateLimits": {}}
            if allowed is not None:
                payload["ordinaryUsageAllowed"] = allowed
            with self.subTest(ordinaryUsageAllowed=allowed):
                reading = limits.classify_codex(codex_response(payload))
                self.assertEqual(reading.source, limits.UNAVAILABLE)
                self.assertEqual(reading.reason, "the response carried neither usage window")
                self.assertEqual(reading.raw["rateLimits"], {})

    def test_positive_the_request_is_the_planned_one(self):
        client = FakeClient(LIVE_SHAPE)
        limits.read_codex(client)
        self.assertEqual(client.calls, [("account/rateLimits/read",
                                         {"excludeResetCreditDetails": True},
                                         GetAccountRateLimitsResponse)])

    def test_rejection_json_rpc_error_is_unavailable_with_its_text(self):
        client = FakeClient(error=InternalRpcError(-32603, "backend down"))
        reading = limits.read_codex(client)
        self.assertEqual(reading.source, limits.UNAVAILABLE)
        self.assertIn("backend down", reading.reason)
        self.assertEqual(reading.summary(),
                         f"Codex usage not reported: {reading.reason}")

    def test_rejection_non_object_response_is_unavailable(self):
        reading = limits.read_codex(FakeClient(payload=["not", "an", "object"]))
        self.assertEqual(reading.source, limits.UNAVAILABLE)
        self.assertIn("must be a JSON object", reading.reason)

    def test_rejection_invalid_response_names_the_field_that_failed(self):
        reading = limits.read_codex(FakeClient(payload={"ordinaryUsageAllowed": True}))
        self.assertEqual(reading.source, limits.UNAVAILABLE)
        self.assertIn("failed validation", reading.reason)
        self.assertIn("rateLimits", reading.reason)
        self.assertIn("Field required", reading.reason)
        self.assertNotIn("\n", reading.reason)

    def test_positive_a_new_run_applies_the_ceiling(self):
        run = limits.RunUsage()
        reading = run.codex(limits.classify_codex(codex_response(
            {"rateLimits": {"primary": {"usedPercent": 95, "resetsAt": 7}}})))
        self.assertEqual(reading.source, limits.REPORTED)
        self.assertIsNotNone(limits.check_ceiling([reading], 80))

    def test_rejection_a_negative_result_holds_for_the_rest_of_the_run(self):
        run = limits.RunUsage()
        first = run.codex(limits.read_codex(FakeClient(error=InternalRpcError(-32603, "down"))))
        self.assertEqual(first.source, limits.UNAVAILABLE)
        later = run.codex(limits.classify_codex(codex_response(
            {"rateLimits": {"primary": {"usedPercent": 95, "resetsAt": 7}}})))
        self.assertEqual(later.source, limits.UNAVAILABLE)
        self.assertEqual((later.percent, later.resets_at), (95, 7))
        self.assertIn("down", later.reason)
        self.assertIsNone(limits.check_ceiling([later], 80))
        # The next run starts clean: nothing is carried over.
        fresh = limits.RunUsage().codex(limits.classify_codex(codex_response(
            {"rateLimits": {"primary": {"usedPercent": 95, "resetsAt": 7}}})))
        self.assertEqual(fresh.source, limits.REPORTED)

    def test_negative_a_negative_result_never_softens_a_reported_limit(self):
        run = limits.RunUsage()
        run.codex(limits.Reading(limits.CODEX, limits.UNAVAILABLE, reason="x"))
        limit = run.codex(limits.classify_codex(codex_response(
            {"ordinaryUsageAllowed": False, "rateLimits": {}})))
        self.assertEqual(limit.source, limits.LIMIT_REPORTED)
        self.assertIsNotNone(limits.check_ceiling([limit], 80))

    def test_negative_closed_transport_is_not_a_usage_answer(self):
        client = FakeClient(error=TransportClosedError("gone"))
        with self.assertRaises(TransportClosedError):
            limits.read_codex(client)

    def test_rejection_usage_limit_turn_error(self):
        error = TurnError.model_validate({"message": "You've hit your usage limit.",
                                          "codexErrorInfo": "usageLimitExceeded"})
        caught = limits.codex_turn_error(error)
        self.assertIsInstance(caught, limits.UsageLimitError)
        self.assertEqual(caught.provider, limits.CODEX)
        self.assertIn("usageLimitExceeded", str(caught))

    def test_rejection_usage_limit_turn_error_in_json_form(self):
        caught = limits.codex_turn_error({"message": "limit",
                                          "codexErrorInfo": "usageLimitExceeded"})
        self.assertIsInstance(caught, limits.UsageLimitError)

    def test_negative_other_turn_errors_are_not_usage_limits(self):
        cases = [
            {"message": "slow down", "codexErrorInfo": "rateLimitExceeded"},
            {"message": "overloaded", "codexErrorInfo": "serverOverloaded"},
            {"message": "no info"},
            {"message": "http", "codexErrorInfo": {"httpConnectionFailed":
                                                   {"httpStatusCode": 502}}},
        ]
        for case in cases:
            with self.subTest(case=case):
                self.assertIsNone(limits.codex_turn_error(TurnError.model_validate(case)))
        self.assertIsNone(limits.codex_turn_error(None))


# ---------------------------------------------------------------------- Claude


class ClaudeReadingTests(unittest.TestCase):

    def test_negative_no_event_yet_is_unavailable(self):
        reading = limits.ClaudeUsage().reading()
        self.assertEqual(reading.source, limits.UNAVAILABLE)
        self.assertEqual(reading.summary(),
                         "Claude usage not reported: no rate-limit event received yet")

    @NEEDS_CLAUDE
    def test_positive_recorded_b1_event_through_the_sdk_parser(self):
        event = parse_message(B1_EVENT)
        self.assertIsNone(event.rate_limit_info.utilization)
        usage = limits.ClaudeUsage()
        usage.observe(event)
        reading = usage.reading()
        self.assertEqual(reading.source, limits.REPORTED)
        self.assertEqual((reading.percent, reading.window, reading.resets_at),
                         (43.0, "seven_day", 1790492400))

    def test_positive_top_level_utilisation_when_no_unified_windows(self):
        usage = limits.ClaudeUsage()
        usage.observe(info(utilization=0.5, resets_at=42))
        reading = usage.reading()
        self.assertEqual((reading.source, reading.percent, reading.window, reading.resets_at),
                         (limits.REPORTED, 50.0, "five_hour", 42))

    def test_positive_windows_accumulate_across_events(self):
        usage = limits.ClaudeUsage()
        usage.observe(info(rate_limit_type="five_hour", utilization=0.2, resets_at=1))
        usage.observe(info(rate_limit_type="seven_day", utilization=0.6, resets_at=2))
        self.assertEqual(usage.reading().percent, 60.0)

    def test_rejection_rejected_status_is_a_reported_limit(self):
        usage = limits.ClaudeUsage()
        usage.observe(info(status="rejected", rate_limit_type="five_hour", resets_at=77,
                           raw={"unifiedWindows": {"five_hour": {"utilization": 1.0,
                                                                  "resetsAt": 77}}}))
        reading = usage.reading()
        self.assertEqual(reading.source, limits.LIMIT_REPORTED)
        self.assertEqual((reading.percent, reading.resets_at, reading.window),
                         (100.0, 77, "five_hour"))
        self.assertEqual(reading.reason, "rate-limit status is rejected")

    @NEEDS_CLAUDE
    def test_rejection_paid_overage_stops_with_the_included_reset(self):
        wire = json.loads(json.dumps(B1_EVENT))
        wire["rate_limit_info"]["isUsingOverage"] = True
        wire["rate_limit_info"]["unifiedWindows"]["five_hour"]["utilization"] = 1.0
        usage = limits.ClaudeUsage()
        usage.observe(parse_message(wire))
        reading = usage.reading()
        self.assertEqual(reading.source, limits.LIMIT_REPORTED)
        self.assertEqual(reading.reason, "using paid overage")
        self.assertEqual((reading.percent, reading.window, reading.resets_at),
                         (100.0, "five_hour", 1790190000))
        stop = limits.check_ceiling([reading], 80)
        self.assertIn("using paid overage", stop.reason)

    @NEEDS_CLAUDE
    def test_rejection_rejected_overage_reports_overage_and_the_included_reset(self):
        # Overage itself refused: status rejected on the overage window, with overage in
        # use. The run still resumes when the included allowance resets, not overage.
        wire = json.loads(json.dumps(B1_EVENT))
        wire["rate_limit_info"].update(status="rejected", rateLimitType="overage",
                                       resetsAt=1790812800, isUsingOverage=True)
        wire["rate_limit_info"]["unifiedWindows"]["five_hour"]["utilization"] = 1.0
        event = parse_message(wire)
        self.assertEqual((event.rate_limit_info.status, event.rate_limit_info.rate_limit_type),
                         ("rejected", "overage"))
        usage = limits.ClaudeUsage()
        usage.observe(event)
        reading = usage.reading()
        self.assertEqual(reading.source, limits.LIMIT_REPORTED)
        self.assertEqual(reading.reason, "using paid overage")
        self.assertEqual((reading.percent, reading.window, reading.resets_at),
                         (100.0, "five_hour", 1790190000))

    @NEEDS_CLAUDE
    def test_rejection_overage_never_borrows_another_windows_reset(self):
        # The fullest window has no reset; the event names an emptier window that has
        # one. That reset would not free the fullest window, so the time stays unknown.
        wire = json.loads(json.dumps(B1_EVENT))
        wire["rate_limit_info"]["isUsingOverage"] = True
        wire["rate_limit_info"]["unifiedWindows"]["seven_day"] = {"utilization": 1.0}
        usage = limits.ClaudeUsage()
        usage.observe(parse_message(wire))
        reading = usage.reading()
        self.assertEqual(reading.reason, "using paid overage")
        self.assertEqual((reading.percent, reading.window, reading.resets_at),
                         (100.0, "seven_day", None))

    @NEEDS_CLAUDE
    def test_positive_overage_takes_the_events_reset_for_the_fullest_window(self):
        # The event names the fullest window itself and carries its reset at the top
        # level only: that is the right time to resume.
        wire = json.loads(json.dumps(B1_EVENT))
        wire["rate_limit_info"]["isUsingOverage"] = True
        wire["rate_limit_info"]["unifiedWindows"]["five_hour"] = {"utilization": 1.0}
        usage = limits.ClaudeUsage()
        usage.observe(parse_message(wire))
        reading = usage.reading()
        self.assertEqual(reading.reason, "using paid overage")
        self.assertEqual((reading.percent, reading.window, reading.resets_at),
                         (100.0, "five_hour", 1790190000))

    def test_rejection_an_overage_window_event_stops(self):
        usage = limits.ClaudeUsage()
        usage.observe(info(rate_limit_type="five_hour", utilization=0.99, resets_at=50))
        # The overage window is fuller and resets later: counted as an included window,
        # it would win the percentage and the reset time.
        usage.observe(info(rate_limit_type="overage", utilization=1.0, resets_at=900))
        reading = usage.reading()
        self.assertEqual(reading.source, limits.LIMIT_REPORTED)
        self.assertEqual(reading.reason, "using paid overage")
        # The overage window never sets the figure or the time the run can resume.
        self.assertEqual((reading.percent, reading.window, reading.resets_at),
                         (99.0, "five_hour", 50))

    def test_negative_overage_that_has_ended_does_not_stop(self):
        usage = limits.ClaudeUsage()
        usage.observe(info(utilization=0.5, resets_at=5, raw={"isUsingOverage": True}))
        usage.observe(info(utilization=0.1, resets_at=9, raw={"isUsingOverage": False}))
        self.assertEqual(usage.reading().source, limits.REPORTED)

    def test_negative_overage_available_but_unused_does_not_stop(self):
        usage = limits.ClaudeUsage()
        usage.observe(info(utilization=0.5, resets_at=5,
                           raw={"overageStatus": "allowed", "isUsingOverage": False}))
        self.assertEqual(usage.reading().source, limits.REPORTED)

    def test_positive_a_later_allowed_event_clears_a_rejection(self):
        usage = limits.ClaudeUsage()
        usage.observe(info(status="rejected", utilization=1.0, resets_at=5))
        usage.observe(info(status="allowed", utilization=0.1, resets_at=9))
        self.assertEqual(usage.reading().source, limits.REPORTED)

    def test_negative_events_without_utilisation_are_unavailable(self):
        usage = limits.ClaudeUsage()
        usage.observe(info(status="allowed_warning"))
        reading = usage.reading()
        self.assertEqual(reading.source, limits.UNAVAILABLE)
        self.assertEqual(reading.reason, "the rate-limit events carried no utilisation")

    def test_rejection_rate_limit_message_error(self):
        caught = limits.claude_message_error(SimpleNamespace(error="rate_limit"))
        self.assertIsInstance(caught, limits.UsageLimitError)
        self.assertEqual(caught.provider, limits.CLAUDE)

    def test_negative_other_message_errors_are_not_usage_limits(self):
        for error in ("server_error", "billing_error", None):
            with self.subTest(error=error):
                self.assertIsNone(limits.claude_message_error(SimpleNamespace(error=error)))
        self.assertIsNone(limits.claude_message_error(object()))


# --------------------------------------------------------------------- ceiling


def reported(provider, percent):
    return limits.Reading(provider, limits.REPORTED, percent=percent, resets_at=1)


class CeilingTests(unittest.TestCase):

    def test_positive_below_the_ceiling_goes_on(self):
        self.assertIsNone(limits.check_ceiling(
            [reported("codex", 79), reported("claude", 79.9)], 80))

    def test_rejection_at_the_ceiling_stops(self):
        stop = limits.check_ceiling([reported("codex", 10), reported("claude", 80)], 80)
        self.assertEqual(stop.provider, "claude")
        self.assertEqual(stop.reason, "Claude usage 80% is at or above the 80% ceiling")

    def test_rejection_a_reported_limit_stops_first(self):
        limit = limits.Reading("claude", limits.LIMIT_REPORTED, reason="rejected")
        stop = limits.check_ceiling([reported("codex", 95), limit], 80)
        self.assertEqual(stop.provider, "claude")
        self.assertIn("usage limit reached", stop.reason)

    def test_rejection_a_reported_limit_stops_with_no_percentage(self):
        limit = limits.Reading("codex", limits.LIMIT_REPORTED, reason="x")
        self.assertIsNotNone(limits.check_ceiling([limit], 100))

    def test_negative_unavailable_never_stops(self):
        unavailable = limits.Reading("codex", limits.UNAVAILABLE, reason="nothing")
        self.assertIsNone(limits.check_ceiling([unavailable], 1))

    def test_negative_reported_with_no_percentage_does_not_stop(self):
        self.assertIsNone(limits.check_ceiling(
            [limits.Reading("codex", limits.REPORTED)], 1))


# -------------------------------------------------------------------- settings


class SettingsTests(unittest.TestCase):

    def write(self, text):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "settings.json"
        path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
        return path

    def test_positive_the_shipped_file_loads_at_80(self):
        self.assertEqual(settings.load_settings()["usage_ceiling_percent"], 80)

    def test_positive_missing_key_takes_the_default(self):
        loaded = settings.load_settings(self.write("{}"))
        self.assertEqual(loaded["usage_ceiling_percent"], settings.DEFAULT_USAGE_CEILING)

    def test_positive_other_keys_are_kept(self):
        loaded = settings.load_settings(self.write('{"usage_ceiling_percent": 60, "x": 1}'))
        self.assertEqual(loaded, {"usage_ceiling_percent": 60, "x": 1})

    def test_negative_a_fraction_of_a_percent_is_a_number(self):
        loaded = settings.load_settings(self.write('{"usage_ceiling_percent": 79.5}'))
        self.assertEqual(loaded["usage_ceiling_percent"], 79.5)

    def test_rejection_bad_values(self):
        cases = {
            "true": "must be a number",
            '"80"': "must be a number",
            "null": "must be a number",
            "0": "above 0 and at most 100",
            "101": "above 0 and at most 100",
            "-5": "above 0 and at most 100",
        }
        for value, reason in cases.items():
            with self.subTest(value=value):
                path = self.write(f'{{"usage_ceiling_percent": {value}}}')
                with self.assertRaisesRegex(settings.SettingsError, reason):
                    settings.load_settings(path)

    def test_rejection_unreadable_files(self):
        cases = {
            "[80]": "must hold a JSON object",
            "{": "is not valid JSON",
            b"\xff\xfe": "is not valid UTF-8",
        }
        for text, reason in cases.items():
            with self.subTest(text=text):
                with self.assertRaisesRegex(settings.SettingsError, reason):
                    settings.load_settings(self.write(text))

    def test_rejection_missing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(settings.SettingsError, "cannot read"):
                settings.load_settings(Path(folder) / "settings.json")

    def test_positive_the_shipped_file_is_plain_json(self):
        raw = (WORKFLOW / "config" / "settings.json").read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertEqual(json.loads(raw), {"usage_ceiling_percent": 80})


if __name__ == "__main__":
    unittest.main()
