#!/usr/bin/env python3
"""Hermetic tests for the builder and reviewer sessions (providers.py).

No test makes a model call or starts a provider process. The Claude builder is driven
by a fake client that yields the SDK's own message types (and a rate-limit event
parsed by the SDK's own parser); the Codex reviewer by a fake client whose turn result
carries the SDK's own status type. What is under test is the provider's handling of
those messages: the approver wiring, the marker, model checks, usage limits, and the
role registry.

Same three controls as test_brief.py:

    positive control  a session the plan expects to work. It must work.
    rejection control a provider answer the plan says ends the run (a substituted
                      model, a usage limit, a failed turn). It must raise, named.
    negative control  something that looks like a limit or a failure but is not (an
                      ordinary assistant error, request throttling). It must not be
                      taken for one.

    python workflows/review-orchestration/tests/test_providers.py
"""

import asyncio
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


brief = _load("brief")
limits = _load("limits")
settings = _load("settings")
approver = _load("approver")
stopreasons = _load("stopreasons")
providers = _load("providers")

try:
    from claude_agent_sdk import (AssistantMessage, PermissionResultAllow,
                                  PermissionResultDeny, ResultMessage, SystemMessage,
                                  TextBlock)
    from claude_agent_sdk._internal.message_parser import parse_message
    HAVE_CLAUDE = True
except ImportError:
    HAVE_CLAUDE = False

try:
    from openai_codex import ApprovalMode, Sandbox
    from openai_codex._run import _collect_turn_result
    from openai_codex.generated.v2_all import (ItemCompletedNotification,
                                               TurnCompletedNotification)
    from openai_codex.models import Notification
    HAVE_CODEX = True
except ImportError:
    HAVE_CODEX = False

NEEDS_CLAUDE = unittest.skipUnless(HAVE_CLAUDE, "claude-agent-sdk is not installed; see "
                                   "workflows/review-orchestration/requirements.txt")
NEEDS_CODEX = unittest.skipUnless(HAVE_CODEX, "openai-codex is not installed; see "
                                  "workflows/review-orchestration/requirements.txt")

MODEL = "claude-opus-5-5"

RATE_EVENT = {
    "type": "rate_limit_event",
    "uuid": "event",
    "session_id": "sess-1",
    "rate_limit_info": {
        "status": "allowed",
        "resetsAt": 1790190000,
        "rateLimitType": "five_hour",
        "isUsingOverage": False,
        "unifiedWindows": {"five_hour": {"utilization": 0.3, "resetsAt": 1790190000}},
    },
}


def an_approver(root):
    return approver.Approver(["notes/"], approver.load_verify_commands(), root=root)


def init(model=MODEL):
    return SystemMessage(subtype="init", data={"model": model})


def said(text, error=None):
    return AssistantMessage(content=[TextBlock(text=text)], model=MODEL, error=error)


def result(text="final answer", session_id="sess-1", is_error=False, subtype="success",
           errors=None):
    return ResultMessage(subtype=subtype, duration_ms=1, duration_api_ms=1,
                         is_error=is_error, num_turns=2, session_id=session_id,
                         total_cost_usd=0.25, usage={"input_tokens": 10}, result=text,
                         errors=errors)


def hook_call(tool_name, tool_input):
    return {"hook_event_name": "PreToolUse", "session_id": "sess-1",
            "transcript_path": "t", "cwd": "c", "tool_name": tool_name,
            "tool_input": tool_input, "tool_use_id": "use-1"}


class FakeClaudeClient:
    """Stands in for ClaudeSDKClient: records what it is given, replays messages."""

    instances = []

    def __init__(self, options):
        self.options = options
        self.prompts = []
        self.script = []
        self.connected = False
        self.disconnected = False
        FakeClaudeClient.instances.append(self)

    async def connect(self):
        self.connected = True

    async def query(self, prompt):
        self.prompts.append(prompt)

    async def receive_response(self):
        for message in self.script.pop(0):
            yield message

    async def disconnect(self):
        self.disconnected = True


@NEEDS_CLAUDE
class ClaudeBuilderTests(unittest.TestCase):

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        FakeClaudeClient.instances = []
        self.builder = providers.ClaudeBuilder(MODEL, "medium", an_approver(self.root),
                                               cwd=self.root,
                                               client_factory=FakeClaudeClient)

    def run_turns(self, *scripts, prompts=("build it",)):
        async def go():
            await self.builder.start()
            self.client = FakeClaudeClient.instances[-1]
            self.client.script = list(scripts)
            records = []
            try:
                for n, prompt in enumerate(prompts):
                    records.append(await self.builder.turn(f"t{n}", prompt))
            finally:
                await self.builder.close()
            return records
        return asyncio.run(go())

    def test_positive_options_route_every_tool_call_through_the_approver(self):
        options = self.builder.options()
        self.assertEqual(options.can_use_tool, self.builder.can_use_tool)
        self.assertEqual((options.model, options.effort), (MODEL, "medium"))
        self.assertEqual(options.permission_mode, "default")
        self.assertEqual(options.cwd, str(self.root))
        self.assertIsNone(options.resume)

    def test_positive_a_pre_tool_use_hook_covers_every_tool(self):
        matchers = self.builder.options().hooks["PreToolUse"]
        self.assertEqual(len(matchers), 1)
        self.assertIsNone(matchers[0].matcher, "a matcher of None matches every tool")
        self.assertEqual(matchers[0].hooks, [self.builder.pre_tool_use])

    def test_rejection_the_hook_refuses_calls_the_project_allowlist_permits(self):
        # R1-1: can_use_tool never sees a call a permissions.allow rule permits, so the
        # hook must refuse these itself. First prove they really are allowlisted.
        settings_file = WORKFLOW.parent.parent / ".claude" / "settings.json"
        allow = json.loads(settings_file.read_text(encoding="utf-8"))["permissions"]["allow"]
        self.assertIn("Bash(python *workflows/sync-architecture/scripts/allowlist.py*)", allow)
        self.assertIn("mcp__biblio-tools__append_log", allow)
        self.assertIn("mcp__biblio-tools__run_new_month", allow)
        cases = [
            ("Bash", {"command": "python workflows/sync-architecture/scripts/allowlist.py "
                                 "--stage --git-dir C:/BookDragon/personal-work.git"},
             "only these verification commands"),
            ("mcp__biblio-tools__append_log", {"directory": "memory", "note": "x"},
             "append_log"),
            ("mcp__biblio-tools__run_new_month", {"month": "2026-10"},
             "is not permitted in this run"),
            ("mcp__biblio-tools__run_link_check", {"mode": "link"}, "may only audit"),
        ]
        for tool, tool_input, reason in cases:
            with self.subTest(tool=tool):
                out = asyncio.run(self.builder.pre_tool_use(hook_call(tool, tool_input),
                                                            "use-1", None))
                specific = out["hookSpecificOutput"]
                self.assertEqual(specific["hookEventName"], "PreToolUse")
                self.assertEqual(specific["permissionDecision"], "deny")
                self.assertIn(reason, specific["permissionDecisionReason"])
        self.assertEqual(len(self.builder.approver.refusals), len(cases))

    def test_negative_the_hook_gives_no_decision_for_an_allowed_call(self):
        # An allowed call gets no decision, not "allow", which would skip the rules after
        # it (the project's own hooks and permission rules still apply).
        for tool, tool_input in (("Read", {"file_path": "AGENTS.md"}),
                                 ("Write", {"file_path": "notes/a.md"})):
            with self.subTest(tool=tool):
                out = asyncio.run(self.builder.pre_tool_use(hook_call(tool, tool_input),
                                                            "use-1", None))
                self.assertEqual(out, {})
        self.assertEqual(self.builder.approver.refusals, [])

    def test_rejection_a_turn_that_ends_in_error(self):
        # R1-2: a provider failure or the turn cap is never a completed pass.
        with self.assertRaisesRegex(providers.ProviderError,
                                    r"ended in error \(error_max_turns\): max turns"):
            self.run_turns([init(), said("half done"),
                            result(text=None, is_error=True, subtype="error_max_turns",
                                   errors=["max turns"])])
        self.assertEqual(len(self.builder.last_messages), 3,
                         "the partial transcript is kept for the run record")
        self.assertTrue(self.client.disconnected)

    def test_positive_the_callback_translates_the_approvers_answer(self):
        allow = asyncio.run(self.builder.can_use_tool("Write", {"file_path": "notes/a.md"},
                                                       None))
        deny = asyncio.run(self.builder.can_use_tool("Bash", {"command": "git push"}, None))
        self.assertIsInstance(allow, PermissionResultAllow)
        self.assertIsInstance(deny, PermissionResultDeny)
        self.assertIn("only these verification commands", deny.message)
        self.assertEqual(len(self.builder.approver.refusals), 1)

    def test_positive_a_turn_collects_result_session_and_usage(self):
        event = parse_message(RATE_EVENT)
        record, = self.run_turns([init(), said("working"), event, result()])
        self.assertEqual(record.final_text, "final answer")
        self.assertEqual(record.session_id, "sess-1")
        self.assertEqual(record.model, MODEL)
        self.assertEqual((record.num_turns, record.cost_usd_equivalent), (2, 0.25))
        self.assertEqual(len(record.messages), 4)
        json.dumps(record.messages)  # transcripts must be JSON-safe
        reading = self.builder.usage.reading()
        self.assertEqual((reading.source, reading.percent), ("reported", 30.0))

    def test_positive_the_marker_leads_the_first_prompt_only(self):
        self.run_turns([init(), result()], [result()], prompts=("build it", "fix R1-1"))
        first, second = self.client.prompts
        self.assertTrue(first.startswith(providers.MARKER + "\n\n"))
        self.assertTrue(first.endswith("build it"))
        self.assertEqual(second, "fix R1-1")

    def test_positive_a_prompt_already_carrying_the_marker_is_not_doubled(self):
        self.run_turns([init(), result()], prompts=(providers.MARKER + "\n\nbuild it",))
        self.assertEqual(self.client.prompts[0].count(providers.MARKER), 1)

    def test_positive_a_resumed_session_sends_no_marker_and_keeps_its_id(self):
        builder = providers.ClaudeBuilder(MODEL, "medium", an_approver(self.root),
                                          cwd=self.root, resume="sess-0",
                                          client_factory=FakeClaudeClient)
        self.assertEqual(builder.options().resume, "sess-0")
        self.builder = builder
        record, = self.run_turns([result(session_id="sess-0")], prompts=("continue",))
        self.assertEqual(self.client.prompts, ["continue"])
        self.assertEqual(record.session_id, "sess-0")

    def test_positive_text_is_the_fallback_when_the_result_has_none(self):
        record, = self.run_turns([init(), said("part one"), said("part two"),
                                  result(text=None)])
        self.assertEqual(record.final_text, "part one\npart two")

    def test_rejection_a_substituted_model(self):
        with self.assertRaisesRegex(providers.ProviderError,
                                    "not the requested 'claude-opus-5-5'"):
            self.run_turns([init("claude-sonnet-5"), result()])

    def test_rejection_a_usage_limit_mid_turn_keeps_the_partial_transcript(self):
        with self.assertRaises(limits.UsageLimitError) as caught:
            self.run_turns([init(), said("half done"), said("", error="rate_limit"),
                            result()])
        self.assertEqual(caught.exception.provider, "claude")
        self.assertEqual(len(self.builder.last_messages), 3)
        self.assertTrue(self.client.disconnected, "the session is closed on the way out")

    def test_negative_an_ordinary_assistant_error_is_not_a_usage_limit(self):
        record, = self.run_turns([init(), said("oops", error="server_error"), result()])
        self.assertEqual(record.final_text, "final answer")

    def test_rejection_a_turn_with_no_result(self):
        with self.assertRaisesRegex(providers.ProviderError, "ended without a result"):
            self.run_turns([init(), said("gone")])

    def test_rejection_a_turn_before_start(self):
        with self.assertRaisesRegex(providers.ProviderError, "has not been started"):
            asyncio.run(self.builder.turn("t", "x"))


TURN_ID = "turn-1"


class FakeHandle:
    """Stands in for the SDK's TurnHandle: replays one turn's notifications."""

    def __init__(self, events):
        self.id = TURN_ID
        self.events = events
        self.closed = False

    def stream(self):
        handle = self

        def gen():
            try:
                yield from handle.events
            finally:
                handle.closed = True
        return gen()


class FakeThread:
    def __init__(self, events):
        self.id = "thread-1"
        self.events = events
        self.turns = []
        self.handle = None

    def turn(self, prompt, **kwargs):
        self.turns.append((prompt, kwargs))
        self.handle = FakeHandle(self.events)
        return self.handle

    def run(self, prompt, **kwargs):
        raise AssertionError("the reviewer must read the turn's events, not call run()")


class FakeCodex:
    """Stands in for the Codex client: a model listing and one thread."""

    def __init__(self, models, events):
        self._models = models
        self.thread = FakeThread(events)
        self.started = None
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def models(self):
        return SimpleNamespace(data=[SimpleNamespace(id=m) for m in self._models])

    def thread_start(self, **kwargs):
        self.started = kwargs
        return self.thread


def turn(status="completed", final=None, error=None, completed=True):
    """One turn's notifications, built from the SDK's own models: the agent's final
    message (if any) and the turn-completed event. ``error`` is a TurnError's JSON
    form, as the app-server sends it."""
    if final is None:
        final = json.dumps({"summary": "s", "findings": []})
    events = []
    if final:
        events.append(Notification(method="item/completed", payload=ItemCompletedNotification
                                   .model_validate({
                                       "completedAtMs": 1, "threadId": "thread-1",
                                       "turnId": TURN_ID,
                                       "item": {"type": "agentMessage", "id": "m1",
                                                "text": final}})))
    if completed:
        payload = {"id": TURN_ID, "items": [], "status": status}
        if error is not None:
            payload["error"] = error
        events.append(Notification(method="turn/completed",
                                   payload=TurnCompletedNotification.model_validate(
                                       {"threadId": "thread-1", "turn": payload})))
    return events


@NEEDS_CODEX
class CodexReviewerTests(unittest.TestCase):

    def review(self, events, models=("gpt-6-sol", "gpt-6-astra"), model="gpt-6-sol"):
        self.codex = FakeCodex(list(models), events)
        reviewer = providers.CodexReviewer(model, "high", cwd="C:/project",
                                           codex_factory=lambda: self.codex)
        return reviewer.review("review this", stopreasons.REVIEW_SCHEMA)

    def test_positive_a_fresh_read_only_thread_with_the_schema(self):
        record = self.review(turn())
        self.assertEqual(record.reply, {"summary": "s", "findings": []})
        self.assertEqual(record.thread_id, "thread-1")
        self.assertEqual(self.codex.started["sandbox"], Sandbox.read_only)
        self.assertEqual(self.codex.started["approval_mode"], ApprovalMode.deny_all)
        self.assertEqual(self.codex.started["model"], "gpt-6-sol")
        prompt, kwargs = self.codex.thread.turns[0]
        self.assertTrue(prompt.startswith(providers.MARKER + "\n\n"))
        self.assertIs(kwargs["output_schema"], stopreasons.REVIEW_SCHEMA)
        self.assertEqual(kwargs["effort"], "high")
        self.assertTrue(self.codex.closed)
        self.assertTrue(self.codex.thread.handle.closed, "the turn's stream is closed")
        self.assertEqual(record.status, "completed")

    def test_rejection_a_model_codex_does_not_have(self):
        with self.assertRaisesRegex(providers.ProviderError,
                                    "Codex has no model 'gpt-6-sol'; a model is never"):
            self.review(turn(), models=("gpt-6-astra",))
        self.assertIsNone(self.codex.started, "no thread may start on a missing model")

    def test_positive_the_sdks_own_collector_loses_the_code(self):
        # The control that proves why the reviewer reads the events itself (R2-1): the
        # installed SDK's collector, given a failed usage-limit turn, raises a plain
        # RuntimeError with only the message.
        events = turn(status="failed", final="",
                      error={"message": "weekly limit", "codexErrorInfo": "usageLimitExceeded"})
        with self.assertRaisesRegex(RuntimeError, "^weekly limit$"):
            _collect_turn_result(iter(events), turn_id=TURN_ID)

    def test_rejection_a_usage_limit_turn_error(self):
        events = turn(status="failed", final="",
                      error={"message": "weekly limit", "codexErrorInfo": "usageLimitExceeded"})
        with self.assertRaises(limits.UsageLimitError) as caught:
            self.review(events)
        self.assertEqual(caught.exception.provider, "codex")
        self.assertIn("usageLimitExceeded", str(caught.exception))
        self.assertTrue(self.codex.thread.handle.closed)

    def test_negative_throttling_is_a_failure_not_a_usage_limit(self):
        events = turn(status="failed", final="",
                      error={"message": "slow down", "codexErrorInfo": "rateLimitExceeded"})
        with self.assertRaisesRegex(providers.ProviderError, "turn failed: slow down"):
            self.review(events)

    def test_rejection_a_failed_turn_with_no_error_detail(self):
        with self.assertRaisesRegex(providers.ProviderError, "turn failed: no detail"):
            self.review(turn(status="failed", final=""))

    def test_rejection_a_turn_with_no_completed_event(self):
        with self.assertRaisesRegex(providers.ProviderError,
                                    "did not complete: turn completed event not received"):
            self.review(turn(completed=False))

    def test_rejection_an_interrupted_turn(self):
        with self.assertRaisesRegex(providers.ProviderError, "ended interrupted"):
            self.review(turn(status="interrupted"))

    def test_rejection_no_or_bad_final_response(self):
        with self.assertRaisesRegex(providers.ProviderError, "gave no final response"):
            self.review(turn(final=""))
        with self.assertRaisesRegex(providers.ProviderError, "reply is not JSON"):
            self.review(turn(final="Here are my findings"))


class RegistryTests(unittest.TestCase):

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()

    def loaded(self, data):
        path = self.root / "settings.json"
        path.write_bytes(json.dumps(data).encode("utf-8"))
        return settings.load_settings(path)

    def test_positive_the_default_pairing(self):
        loaded = settings.load_settings()
        builder = providers.make_builder(loaded, an_approver(self.root), cwd=self.root)
        reviewer = providers.make_reviewer(loaded, cwd=self.root)
        self.assertIsInstance(builder, providers.ClaudeBuilder)
        self.assertEqual((builder.model, builder.effort), (MODEL, "medium"))
        self.assertIsInstance(reviewer, providers.CodexReviewer)
        self.assertEqual((reviewer.model, reviewer.effort), ("gpt-6-sol", "high"))

    def test_positive_models_come_from_the_settings_not_the_code(self):
        loaded = self.loaded({"models": {"codex": {"reviewer": {"model": "gpt-7",
                                                                "effort": "max"}}}})
        reviewer = providers.make_reviewer(loaded, cwd=self.root)
        self.assertEqual((reviewer.model, reviewer.effort), ("gpt-7", "max"))

    def test_rejection_the_swapped_pairing_is_named_not_substituted(self):
        loaded = self.loaded({
            "roles": {"builder": "codex", "reviewer": "claude"},
            "models": {"codex": {"builder": {"model": "gpt-6-sol", "effort": "high"}},
                       "claude": {"reviewer": {"model": MODEL, "effort": "high"}}}})
        with self.assertRaisesRegex(providers.ProviderError,
                                    "no builder session exists for codex yet"):
            providers.make_builder(loaded, an_approver(self.root), cwd=self.root)
        with self.assertRaisesRegex(providers.ProviderError,
                                    "no reviewer session exists for claude yet"):
            providers.make_reviewer(loaded, cwd=self.root)


class MarkerTests(unittest.TestCase):

    def test_positive_the_marker_is_the_agents_md_clause(self):
        agents = (WORKFLOW.parent.parent / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn(f"`{providers.MARKER}`", agents)


if __name__ == "__main__":
    unittest.main()
