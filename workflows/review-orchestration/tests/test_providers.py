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
import re
import subprocess
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
codex_rules = _load("codex_rules")
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
RUN_ID = "20261002-101500-ab12"

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


BUILD_TURN = "turn-b1"
THREAD = "thread-b1"
HOOK_KEY = "C:\\<session-flags>\\config.toml:pre_tool_use:0:0"
SANDBOX = {"type": "workspaceWrite", "writableRoots": [], "networkAccess": False,
           "excludeTmpdirEnvVar": True, "excludeSlashTmp": True}


def listed(event, source, matcher=None, trust="trusted", enabled=True, timeout=600,
           key="project-key"):
    """One hooks/list row, with the fields the live app-server returns."""
    return {"eventName": event, "matcher": matcher, "source": source,
            "trustStatus": trust, "enabled": enabled, "timeoutSec": timeout,
            "handlerType": "command", "key": key, "currentHash": "sha256:abc"}


def project_hooks():
    """The project's own four Codex hooks, as hooks/list reported them on 2026-10-02."""
    return [listed("preToolUse", "project", "Bash"),
            listed("preToolUse", "project", "apply_patch"),
            listed("sessionStart", "project"), listed("stop", "project")]


def codex_note(method, payload):
    """A notification as the client hands it over: the SDK's own model where it has
    one for the method, so the session reads what it would read live."""
    from openai_codex.client import CodexClient
    return CodexClient._coerce_notification(None, method, payload)


def hook_done(call, status="completed", source="sessionFlags", event="preToolUse"):
    prefix = {"preToolUse": "pre-tool-use:0", "sessionStart": "session-start:2",
              "stop": "stop:3"}[event]
    run_id = f"{prefix}:C:\\<session-flags>\\config.toml" + (f":{call}" if call else "")
    return codex_note("hook/completed", {
        "threadId": THREAD, "turnId": BUILD_TURN,
        "run": {"id": run_id, "eventName": event, "source": source, "status": status,
                "handlerType": "command", "executionMode": "sync", "scope": "turn",
                "displayOrder": 0, "entries": [], "startedAt": 1, "completedAt": 2,
                "durationMs": 400, "sourcePath": "C:\\<session-flags>\\config.toml",
                "statusMessage": None}})


def item_done(item):
    return codex_note("item/completed", {"completedAtMs": 1, "threadId": THREAD,
                                         "turnId": BUILD_TURN, "item": item})


def said_codex(text):
    return item_done({"type": "agentMessage", "id": "msg-1", "text": text})


def command_item(call, status="completed"):
    return item_done({"type": "commandExecution", "id": call, "command": "x", "cwd": "C:\\p",
                      "commandActions": [], "status": status, "exitCode": 0,
                      "aggregatedOutput": "", "durationMs": 1, "processId": "1",
                      "source": "agent"})


def tokens(total=4321):
    usage = {"cacheWriteInputTokens": 0, "cachedInputTokens": 0, "inputTokens": total - 1,
             "outputTokens": 1, "reasoningOutputTokens": 0, "totalTokens": total}
    return codex_note("thread/tokenUsage/updated", {
        "threadId": THREAD, "turnId": BUILD_TURN,
        "tokenUsage": {"last": usage, "total": usage, "modelContextWindow": 258400}})


def turn_done(status="completed", error=None):
    payload = {"id": BUILD_TURN, "items": [], "itemsView": "notLoaded", "status": status}
    if error is not None:
        payload["error"] = error
    return codex_note("turn/completed", {"threadId": THREAD, "turn": payload})


class FakeCodexClient:
    """Stands in for an initialised CodexClient. It answers the requests the builder
    session makes from the state a test sets, and replays one turn's notifications. A
    scripted step may be a function, run when the session asks for the next event, as
    the hook or an approval request happens between two notifications live."""

    def __init__(self, world, overrides, handler):
        self.world = world
        self.overrides = overrides
        self.handler = handler
        self.requests = []
        self.prompts = []
        self.interrupted = []
        self.closed = False
        self.registered = None

    def _request_raw(self, method, params=None):
        self.requests.append((method, params))
        world = self.world
        if method == "hooks/list":
            trusted = any(o.startswith("hooks.state=") for o in self.overrides)
            hooks = list(world.project)
            for count in range(world.run_hooks):
                hooks.append(listed("preToolUse", "sessionFlags", world.matcher,
                                    trust=world.trust if trusted else "untrusted",
                                    enabled=world.enabled, timeout=world.timeout,
                                    key=world.key + ("" if count == 0 else str(count))))
            return {"data": [{"cwd": params["cwds"][0], "hooks": hooks, "errors": [],
                              "warnings": []}]}
        if method == "config/read":
            off = providers.WEB_SEARCH_OFF in self.overrides and world.search_off
            return {"config": {"web_search": "disabled" if off else "live"}, "origins": {}}
        if method in ("thread/start", "thread/resume"):
            return world.thread_reply
        raise AssertionError(f"unexpected request {method}")

    def model_list(self):
        return SimpleNamespace(data=[SimpleNamespace(id=m) for m in self.world.models])

    def turn_start(self, thread_id, prompt, params):
        self.prompts.append((thread_id, prompt, params))
        return SimpleNamespace(turn=SimpleNamespace(id=BUILD_TURN))

    def register_turn_notifications(self, turn_id):
        self.registered = turn_id

    def unregister_turn_notifications(self, turn_id):
        self.registered = None

    def next_turn_notification(self, turn_id):
        step = self.world.script.pop(0)
        while callable(step):
            step(self)
            step = self.world.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def turn_interrupt(self, thread_id, turn_id):
        self.interrupted.append((thread_id, turn_id))

    def close(self):
        self.closed = True


@NEEDS_CODEX
class CodexBuilderTests(unittest.TestCase):
    """The Codex builder session (short plan section 3), against a fake client that
    answers as the live app-server answered the probes."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        base = Path(folder.name).resolve()
        self.root = base / "project"
        (self.root / "notes").mkdir(parents=True)
        self.temp = base / "temp"
        self.run_dir = base / "runs" / RUN_ID
        self.world = SimpleNamespace(
            project=project_hooks(), run_hooks=1, trust="trusted", enabled=True,
            timeout=30, matcher=".*", key=HOOK_KEY, search_off=True,
            models=["gpt-6-sol", "gpt-6-astra"],
            thread_reply={"thread": {"id": THREAD}, "model": "gpt-6-sol",
                          "sandbox": dict(SANDBOX), "approvalPolicy": "untrusted"},
            script=[])
        self.clients = []
        self.frozen = self.temp / providers.FROZEN_PARENT / RUN_ID
        self.decisions = self.frozen / "hook-decisions.jsonl"

    def factory(self, overrides, handler):
        client = FakeCodexClient(self.world, overrides, handler)
        self.clients.append(client)
        return client

    def builder(self, resume=None, **kwargs):
        return providers.CodexBuilder(
            "gpt-6-sol", "high", an_approver(self.root), cwd=self.root, resume=resume,
            run_dir=self.run_dir, client_factory=self.factory, temp_root=self.temp,
            **kwargs)

    def started(self, resume=None, **kwargs):
        builder = self.builder(resume, **kwargs)
        asyncio.run(builder.start())
        return builder

    def refused_start(self, reason, resume=None, **kwargs):
        builder = self.builder(resume, **kwargs)
        with self.assertRaisesRegex(providers.ProviderError, reason):
            asyncio.run(builder.start())
        return builder

    def decided(self, call, decision, tool="Bash", turn=BUILD_TURN):
        """A step in which the run's hook records its decision for one call."""
        def write(client):
            with open(self.decisions, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps({
                    "tool_use_id": call, "turn_id": turn, "session_id": THREAD,
                    "tool": tool, "decision": decision, "detail": f"detail of {call}",
                    "reason": "" if decision == "allow" else "Refused by the "
                    "orchestrator: no."}) + "\n")
        return write

    def turn(self, builder, *script, prompt="build it"):
        self.world.script = list(script)
        return asyncio.run(builder.turn("build", prompt))

    def ended(self, builder, *script, reason):
        with self.assertRaisesRegex(providers.ProviderError, reason):
            self.turn(builder, *script)
        self.assertEqual(self.clients[-1].interrupted, [(THREAD, BUILD_TURN)],
                         "the turn is interrupted before the error is raised")

    # ---------------------------------------------------------- the frozen folder

    def test_positive_a_first_launch_creates_the_frozen_folder(self):
        builder = self.started()
        self.assertEqual(builder.frozen_dir, self.frozen)
        names = {path.name for path in self.frozen.iterdir() if path.is_file()}
        self.assertEqual(names, {"codex_hook.py", "codex_rules.py", "approver.py",
                                 "brief.py", "check_server.py", "container.py",
                                 "codex-verify-commands.txt", "codex-read-commands.txt",
                                 "verify-commands.txt", "hook.json",
                                 "hook-decisions.jsonl"})
        config = json.loads((self.frozen / "hook.json").read_text(encoding="utf-8"))
        self.assertEqual(config["edit_paths"], ["notes/"])
        self.assertEqual(config["project_root"], str(self.root))
        # The self-test ran the real hook from the folder, and its line is gone.
        self.assertEqual(self.decisions.read_text(encoding="utf-8"), "")

    def test_rejection_a_frozen_folder_that_exists_before_the_first_launch(self):
        self.frozen.mkdir(parents=True)
        (self.frozen / "codex_hook.py").write_bytes(b"someone else's rules\n")
        self.refused_start("already\\s+exists before this run's first launch")
        self.assertEqual((self.frozen / "codex_hook.py").read_bytes(),
                         b"someone else's rules\n", "a folder it did not make is left")
        self.assertEqual(self.clients, [], "Codex is never started")

    def test_positive_a_resume_uses_the_folder_as_it_is(self):
        first = self.started()
        self.turn(first, self.decided("exec-1", "allow"), hook_done("exec-1"),
                  command_item("exec-1"), said_codex("done"), turn_done())
        asyncio.run(first.close())
        # The builder may since have changed the project's copies: nothing is copied
        # again, so the rules stay the ones the run started with.
        marker = self.frozen / "codex-read-commands.txt"
        marker.write_bytes(marker.read_bytes() + b"# as frozen at the first launch\n")
        kept = marker.read_bytes()
        again = self.started(resume=THREAD)
        self.assertEqual(marker.read_bytes(), kept)
        self.assertEqual(len(codex_rules.read_decisions(self.decisions)), 1,
                         "the earlier turn's line is kept; only the self-test's is removed")
        method, params = [r for r in self.clients[-1].requests
                          if r[0].startswith("thread/")][0]
        self.assertEqual(method, "thread/resume")
        self.assertEqual(params["threadId"], THREAD)
        self.assertEqual(again.session_id, THREAD)

    def test_rejection_a_resume_whose_frozen_folder_is_gone(self):
        self.refused_start("frozen hook folder .* is missing", resume=THREAD)
        self.assertFalse(self.frozen.exists(), "a resume never creates the folder")
        self.assertEqual(self.clients, [])

    def test_positive_a_first_launch_that_fails_removes_the_folder_it_made(self):
        self.world.search_off = False
        self.refused_start("web search is not switched off")
        self.assertFalse(self.frozen.exists(), "so the next launch is a first launch")
        self.assertTrue(all(client.closed for client in self.clients))

    def test_positive_a_session_closed_before_any_turn_removes_its_folder(self):
        builder = self.started()
        self.assertIsNone(builder.session_id,
                          "a thread with no turn cannot be resumed, so it is not offered")
        asyncio.run(builder.close())
        self.assertFalse(self.frozen.exists())
        self.assertTrue(self.clients[-1].closed)

    def test_negative_a_session_that_took_a_turn_keeps_its_folder(self):
        builder = self.started()
        self.turn(builder, said_codex("done"), turn_done())
        asyncio.run(builder.close())
        self.assertTrue(self.frozen.is_dir())
        resumed = self.started(resume=THREAD)
        asyncio.run(resumed.close())
        self.assertTrue(self.frozen.is_dir(), "a resumed session never removes it")

    # ------------------------------------------------------------- the self-test

    def test_rejection_each_way_the_self_test_fails(self):
        def timed_out(*args):
            raise subprocess.TimeoutExpired("hook", 30)

        def missing(*args):
            raise FileNotFoundError("no interpreter")
        deny = json.dumps({"hookSpecificOutput": {"permissionDecision": "deny"}})
        for label, runner in (("exit 1", lambda *a: (1, deny)),
                              ("exit 2", lambda *a: (2, "")),
                              ("says nothing", lambda *a: (0, "")),
                              ("not JSON", lambda *a: (0, "garbage")),
                              ("allows", lambda *a: (0, json.dumps(
                                  {"hookSpecificOutput": {"permissionDecision": "allow"}}))),
                              ("times out", timed_out), ("cannot start", missing)):
            with self.subTest(label):
                self.refused_start("did not refuse an unlisted command in\\s+its self-test",
                                   self_test=runner)
                self.assertEqual(self.clients, [], "Codex is not started on a hook that "
                                                   "cannot answer")
                self.assertFalse(self.frozen.exists())

    def test_positive_the_self_test_runs_the_hook_as_codex_will(self):
        seen = []

        def runner(python, script, run_id, event, timeout):
            seen.append((python, script, run_id, event, timeout))
            return providers._run_hook_once(python, script, run_id, event, timeout)
        self.started(self_test=runner)
        python, script, run_id, event, timeout = seen[0]
        self.assertEqual((python, run_id, timeout), (sys.executable, RUN_ID, 30))
        self.assertEqual(script, (self.frozen / "codex_hook.py").as_posix())
        self.assertEqual(event["tool_name"], "Bash")

    # ------------------------------------------------- the launch and its checks

    def test_positive_the_two_launches_and_the_overrides_they_send(self):
        builder = self.started()
        first, second = self.clients
        python = sys.executable.replace("\\", "/")
        hook = ('hooks.PreToolUse=[{matcher=".*", hooks=[{type="command", command="'
                f'{python} {self.frozen.as_posix()}/codex_hook.py {RUN_ID}", '
                'timeout=30}]}]')
        self.assertEqual(first.overrides, (hook, 'web_search="disabled"'))
        self.assertIsNone(first.handler)
        self.assertTrue(first.closed, "the first client only lists the hook")
        self.assertEqual([r[0] for r in first.requests], ["hooks/list"])
        # The key holds backslashes, so it is a TOML literal string.
        trust = "hooks.state={'" + HOOK_KEY + "'={trusted_hash=\"sha256:abc\"}}"
        self.assertEqual(second.overrides, (hook, 'web_search="disabled"', trust))
        self.assertEqual(second.handler, builder._approve)
        self.assertEqual([r[0] for r in second.requests],
                         ["hooks/list", "config/read", "thread/start"])
        self.assertEqual(second.requests[0][1], {"cwds": [str(self.root)]})
        self.assertFalse(second.closed)

    def test_positive_the_thread_is_started_with_the_measured_settings(self):
        builder = self.started()
        method, params = self.clients[-1].requests[-1]
        self.assertEqual(method, "thread/start")
        self.assertEqual(params, {
            "cwd": str(self.root), "model": "gpt-6-sol", "approvalPolicy": "untrusted",
            "sandbox": "workspace-write",
            "config": {"sandbox_workspace_write": {
                "network_access": False, "exclude_tmpdir_env_var": True,
                "exclude_slash_tmp": True, "writable_roots": []}}})
        self.assertEqual(builder.thread_model, "gpt-6-sol")
        self.assertNotIn("thread/shellCommand",
                         [r[0] for c in self.clients for r in c.requests])

    def test_positive_the_launch_record_holds_what_was_read_back(self):
        # The chunk (d) short plan, 11.2 (live checks 1 and 3): each value is what the
        # fake client reported, for a start and for a resume.
        builder = self.started()
        record = builder.launch_record
        self.assertEqual(record["hook"], {"trustStatus": "trusted", "enabled": True,
                                          "timeoutSec": 30, "matcher": ".*"})
        self.assertEqual(record["self_test"], "passed")
        self.assertEqual(record["web_search"], "disabled")
        self.assertEqual(record["thread"], {"method": "start", "id": THREAD,
                                            "sandbox": dict(SANDBOX),
                                            "approvalPolicy": "untrusted"})
        project = [hook for hook in project_hooks()]
        self.assertEqual(len(record["project_hooks"]), len(project))
        self.assertTrue(all(set(entry) == {"eventName", "matcher", "trustStatus", "enabled"}
                            for entry in record["project_hooks"]))
        self.turn(builder, said_codex("done"), turn_done())
        asyncio.run(builder.close())
        again = self.started(resume=THREAD)
        self.assertEqual(again.launch_record["thread"]["method"], "resume")
        self.assertEqual(again.launch_record["thread"]["id"], THREAD)

    def test_negative_a_launch_that_fails_leaves_no_record(self):
        builder = self.builder()
        self.world.search_off = False
        with self.assertRaises(providers.ProviderError):
            asyncio.run(builder.start())
        self.assertIsNone(builder.launch_record)

    def test_rejection_each_reason_the_session_does_no_builder_work(self):
        cases = [
            ("the run's hook untrusted", {"trust": "untrusted"},
             "the run's hook is not trusted and enabled"),
            ("the run's hook disabled", {"enabled": False},
             "the run's hook is not trusted and enabled"),
            ("another time limit", {"timeout": 600}, "a time limit of\\s+600 seconds"),
            # Code review finding R4-1: the run's hook must apply to every tool.
            ("a narrower matcher", {"matcher": "Bash"},
             "the run's hook applies to 'Bash', not\\s+to every tool"),
            ("no matcher reported", {"matcher": None},
             "the run's hook applies to None, not\\s+to every tool"),
            ("web search on", {"search_off": False}, "web search is not switched off"),
            ("a project hook untrusted",
             {"project": [listed("preToolUse", "project", "Bash", trust="untrusted"),
                          *project_hooks()[1:]]},
             "the project's preToolUse hook \\('Bash'\\) is not trusted and enabled"),
            ("a project hook disabled",
             {"project": [*project_hooks()[:3], listed("stop", "project", enabled=False)]},
             "the project's stop hook .* is not trusted and enabled"),
            ("no project hook for the shell", {"project": project_hooks()[1:]},
             "the project's preToolUse hook for Bash is not\\s+listed"),
            ("no project hook for patches",
             {"project": [project_hooks()[0], *project_hooks()[2:]]},
             "the project's preToolUse hook for apply_patch is not\\s+listed"),
            ("no project hooks at all", {"project": []},
             "the project's preToolUse hook for Bash is not\\s+listed"),
        ]
        for label, change, reason in cases:
            with self.subTest(label):
                self.setUp()
                for name, value in change.items():
                    setattr(self.world, name, value)
                self.refused_start(reason)
                self.assertNotIn("thread/start",
                                 [r[0] for c in self.clients for r in c.requests])
                self.assertTrue(all(client.closed for client in self.clients))

    def test_rejection_not_exactly_one_run_hook_or_a_key_with_a_quote(self):
        for label, change, reason in (
                ("no run hook listed", {"run_hooks": 0}, "lists 0 run-supplied"),
                ("two run hooks listed", {"run_hooks": 2}, "lists 2 run-supplied"),
                ("a key with a quote", {"key": "C:\\it's"}, "cannot be written as a trust")):
            with self.subTest(label):
                self.setUp()
                for name, value in change.items():
                    setattr(self.world, name, value)
                self.refused_start(reason)
                self.assertEqual(len(self.clients), 1, "the session's own client never "
                                                       "starts")

    def test_rejection_a_model_codex_does_not_have(self):
        self.world.models = ["gpt-6-astra"]
        self.refused_start("Codex has no model 'gpt-6-sol'; a model is never")

    def test_rejection_a_thread_whose_sandbox_or_policy_differs_or_is_missing(self):
        replies = [
            ("network on", {"sandbox": {**SANDBOX, "networkAccess": True}}, "sandbox"),
            ("temp folder writable", {"sandbox": {**SANDBOX, "excludeTmpdirEnvVar": False}},
             "sandbox"),
            ("a writable root", {"sandbox": {**SANDBOX, "writableRoots": ["C:\\x"]}},
             "sandbox"),
            ("full access", {"sandbox": {"type": "dangerFullAccess"}}, "sandbox"),
            ("no sandbox reported", {"sandbox": None}, "sandbox"),
            ("another policy", {"approvalPolicy": "never"}, "approval policy"),
            ("no policy reported", {"approvalPolicy": None}, "approval policy"),
            # Code review finding R1-3: a model is never substituted.
            ("another model", {"model": "gpt-6-astra"}, "model Codex reports"),
            ("no model reported", {"model": None}, "model Codex reports"),
        ]
        for resume in (None, THREAD):
            for label, change, reason in replies:
                with self.subTest(label, resume=resume):
                    self.setUp()
                    if resume:
                        asyncio.run(self.started().close())
                        self.frozen.mkdir(parents=True)
                        (self.frozen / "codex_hook.py").write_bytes(
                            (SCRIPTS / "codex_hook.py").read_bytes())
                        self.world.thread_reply = {**self.world.thread_reply, **change}
                        self.refused_start(f"{reason}.*|not as asked for", resume=resume,
                                           self_test=lambda *a: (0, json.dumps(
                                               {"hookSpecificOutput":
                                                {"permissionDecision": "deny"}})))
                    else:
                        self.world.thread_reply = {**self.world.thread_reply, **change}
                        self.refused_start("not as asked for, so no\\s+turn runs on it")
                    self.assertTrue(self.clients[-1].closed)

    def test_rejection_a_thread_on_another_model_is_refused_naming_the_model(self):
        # Code review finding R1-3, with the reason itself checked.
        for model in ("gpt-6-astra", None):
            with self.subTest(model=model):
                self.setUp()
                self.world.thread_reply = {**self.world.thread_reply, "model": model}
                self.refused_start("the model Codex reports is not 'gpt-6-sol': "
                                   + re.escape(repr(model)))
                self.assertEqual(self.clients[-1].prompts, [], "no turn may start")

    def test_rejection_a_hook_command_that_would_need_quoting(self):
        for python in ("C:/Program Files/Python/python.exe", "C:/it's/python.exe",
                       'C:/a"b/python.exe'):
            with self.subTest(python=python):
                self.setUp()
                self.refused_start("cannot be written safely", python=python)
                self.assertEqual(self.clients, [])
        self.setUp()
        self.temp = self.temp.parent / "temp folder"
        self.refused_start("cannot be written safely")

    # ------------------------------------------------------------------- a turn

    def test_positive_a_turn_returns_the_reply_the_thread_and_its_tokens(self):
        builder = self.started()
        record = self.turn(builder, self.decided("exec-1", "allow"), hook_done("exec-1"),
                           command_item("exec-1"), said_codex("working"), tokens(4321),
                           said_codex("Changed notes/a.md."), turn_done())
        self.assertEqual(record.final_text, "Changed notes/a.md.")
        self.assertEqual((record.session_id, record.model), (THREAD, "gpt-6-sol"))
        self.assertEqual(record.tokens, 4321)
        self.assertIsNone(record.cost_usd_equivalent)
        self.assertEqual(builder.session_id, THREAD)
        json.dumps(record.messages)  # the transcript must be JSON-safe
        self.assertEqual(len(record.messages), 6)
        thread, prompt, params = self.clients[-1].prompts[0]
        self.assertEqual((thread, params), (THREAD, {"effort": "high"}))
        self.assertTrue(prompt.startswith(providers.MARKER + "\n\n"))
        self.assertEqual(self.clients[-1].interrupted, [])
        self.assertIsNone(self.clients[-1].registered, "the turn's queue is released")
        # The decisions file is copied into the run record after every turn.
        self.assertEqual((self.run_dir / "hook-decisions.jsonl").read_bytes(),
                         self.decisions.read_bytes())

    def test_positive_the_marker_leads_the_first_prompt_of_a_new_session_only(self):
        builder = self.started()
        self.turn(builder, said_codex("one"), turn_done())
        self.turn(builder, said_codex("two"), turn_done(), prompt="fix R1-1")
        self.assertEqual(self.clients[-1].prompts[1][1], "fix R1-1")
        asyncio.run(builder.close())
        resumed = self.started(resume=THREAD)
        self.turn(resumed, said_codex("three"), turn_done(), prompt="continue")
        self.assertEqual(self.clients[-1].prompts[0][1], "continue")

    def test_rejection_a_failed_turn_and_a_usage_limit(self):
        builder = self.started()
        with self.assertRaisesRegex(providers.ProviderError, "turn failed: boom"):
            self.turn(builder, said_codex("half"),
                      turn_done("failed", {"message": "boom"}))
        self.assertEqual(len(builder.last_messages), 2, "the transcript so far is kept")
        with self.assertRaises(limits.UsageLimitError) as caught:
            self.turn(builder, turn_done("failed", {
                "message": "weekly limit", "codexErrorInfo": "usageLimitExceeded"}))
        self.assertEqual(caught.exception.provider, "codex")
        with self.assertRaisesRegex(providers.ProviderError, "turn failed: slow down"):
            self.turn(builder, turn_done("failed", {
                "message": "slow down", "codexErrorInfo": "rateLimitExceeded"}))
        with self.assertRaisesRegex(providers.ProviderError, "turn ended interrupted"):
            self.turn(builder, turn_done("interrupted"))

    def test_rejection_a_turn_before_start(self):
        with self.assertRaisesRegex(providers.ProviderError, "has not been started"):
            asyncio.run(self.builder().turn("build", "x"))

    # ---------------------------------------------- the table of hook-run statuses

    def test_positive_the_runs_hook_completed_with_an_allow_line(self):
        builder = self.started()
        self.turn(builder, self.decided("exec-1", "allow"), hook_done("exec-1"),
                  command_item("exec-1"), said_codex("ok"), turn_done())

    def test_positive_the_runs_hook_blocked_with_a_deny_line(self):
        # A refusal is the hook working, and it reaches the run report.
        builder = self.started()
        record = self.turn(builder, self.decided("exec-1", "deny"),
                           hook_done("exec-1", "blocked"), said_codex("refused"),
                           turn_done())
        self.assertEqual(record.refusals, [{"tool": "Bash", "detail": "detail of exec-1",
                                            "reason": "Refused by the orchestrator: no."}])
        self.assertEqual(builder.refusals, [], "returned once, not twice")

    def test_rejection_every_other_outcome_of_the_runs_own_hook(self):
        cases = [
            ("completed with no line", [hook_done("exec-1")], "no decision line"),
            ("completed with a deny line",
             [self.decided("exec-1", "deny"), hook_done("exec-1")], "with deny"),
            ("blocked with no line", [hook_done("exec-1", "blocked")], "no decision line"),
            ("blocked with an allow line",
             [self.decided("exec-1", "allow"), hook_done("exec-1", "blocked")],
             "with allow"),
            ("failed", [self.decided("exec-1", "allow"), hook_done("exec-1", "failed")],
             "ended 'failed'"),
            ("stopped", [self.decided("exec-1", "allow"), hook_done("exec-1", "stopped")],
             "ended 'stopped'"),
            ("a line for another call only",
             [self.decided("exec-2", "allow"), hook_done("exec-1")], "no decision line"),
        ]
        for label, script, reason in cases:
            with self.subTest(label):
                self.setUp()
                builder = self.started()
                self.ended(builder, *script, said_codex("unreached"), turn_done(),
                           reason=reason)
                self.assertEqual(len(builder.last_messages), 1,
                                 "nothing after the failed hook run is read")

    def test_rejection_a_status_the_session_does_not_know(self):
        # The SDK has no model for a status it does not list, so the notification
        # arrives unparsed; the session reads it all the same.
        builder = self.started()
        note = codex_note("hook/completed", {
            "threadId": THREAD, "turnId": BUILD_TURN,
            "run": {"id": "pre-tool-use:0:x:exec-1", "eventName": "preToolUse",
                    "source": "sessionFlags", "status": "paused"}})
        self.assertEqual(type(note.payload).__name__, "UnknownNotification")
        self.ended(builder, self.decided("exec-1", "allow"), note, turn_done(),
                   reason="ended 'paused'")

    def test_positive_a_project_rule_refusing_a_call_is_that_rule_working(self):
        builder = self.started()
        self.turn(builder,
                  self.decided("exec-1", "allow"), hook_done("exec-1"),
                  hook_done("exec-1", "blocked", source="project"),
                  self.decided("exec-2", "allow"), hook_done("exec-2"),
                  hook_done("exec-2", "completed", source="project"),
                  command_item("exec-2"), said_codex("ok"), turn_done())

    def test_rejection_a_project_pre_tool_use_hook_that_failed(self):
        builder = self.started()
        self.ended(builder, self.decided("exec-1", "allow"), hook_done("exec-1"),
                   hook_done("exec-1", "failed", source="project"), turn_done(),
                   reason="a project preToolUse hook run ended 'failed'")

    def test_negative_the_projects_session_start_and_stop_hooks_failing(self):
        # Both report failed in every Codex session in the project today. They are not
        # preToolUse runs, so they do not end a builder's turn.
        builder = self.started()
        record = self.turn(
            builder, hook_done(None, "failed", source="project", event="sessionStart"),
            said_codex("ok"),
            hook_done(None, "failed", source="project", event="stop"), turn_done())
        self.assertEqual(record.final_text, "ok")

    # ------------------------------------------------- items, and the second layer

    def test_rejection_a_tool_item_with_no_decision_line(self):
        builder = self.started()
        with self.assertRaisesRegex(providers.ProviderError,
                                    "commandExecution item exec-9 has no hook decision"):
            self.turn(builder, command_item("exec-9"), said_codex("ok"), turn_done())

    def test_rejection_an_item_that_ran_on_a_deny_line(self):
        builder = self.started()
        with self.assertRaisesRegex(providers.ProviderError,
                                    "exec-1 ran without the hook allowing it"):
            self.turn(builder, self.decided("exec-1", "deny"),
                      hook_done("exec-1", "blocked"), command_item("exec-1"), turn_done())

    def test_negative_a_declined_item_with_a_deny_line(self):
        builder = self.started()
        self.turn(builder, self.decided("exec-1", "deny"), hook_done("exec-1", "blocked"),
                  command_item("exec-1", status="declined"), said_codex("ok"), turn_done())

    def test_rejection_an_item_of_a_kind_the_session_does_not_know(self):
        builder = self.started()
        with self.assertRaisesRegex(providers.ProviderError,
                                    "an item of a kind the session does not know: plan"):
            self.turn(builder, item_done({"type": "plan", "id": "p1", "text": "a plan"}),
                      said_codex("ok"), turn_done())

    def test_rejection_a_web_search_that_ran(self):
        # Web search needs no approval; with the hook refusing it and the tool switched
        # off it never runs, so an item of it means a call got past.
        builder = self.started()
        with self.assertRaisesRegex(providers.ProviderError, "webSearch item ws-1 has no"):
            self.turn(builder, item_done({"type": "webSearch", "id": "ws-1",
                                          "query": "example.com"}), turn_done())

    def test_positive_the_approval_handler_accepts_only_what_the_hook_allowed(self):
        builder = self.started()
        replies = []

        def ask(method, call):
            return lambda client: replies.append(client.handler(
                method, {"itemId": call, "command": "the wrapped command"}))
        record = self.turn(
            builder, self.decided("exec-1", "allow"), hook_done("exec-1"),
            ask("item/commandExecution/requestApproval", "exec-1"), command_item("exec-1"),
            self.decided("exec-2", "allow", tool="apply_patch"), hook_done("exec-2"),
            ask("item/fileChange/requestApproval", "exec-2"),
            ask("item/commandExecution/requestApproval", "exec-3"),
            said_codex("ok"), turn_done())
        self.assertEqual(replies, [{"decision": "accept"}, {"decision": "accept"},
                                   {"decision": "decline"}])
        self.assertEqual([r["reason"] for r in record.refusals],
                         ["Refused by the orchestrator: no hook decision for this call."])

    def test_rejection_a_request_of_any_other_kind_ends_the_turn(self):
        builder = self.started()
        replies = []
        self.ended(builder,
                   lambda client: replies.append(client.handler(
                       "item/permissions/requestApproval", {"itemId": "x"})),
                   said_codex("unreached"), turn_done(),
                   reason="a request the orchestrator does not answer: "
                          "item/permissions/requestApproval")
        self.assertEqual(replies, [{}])

    def test_positive_refusals_are_kept_when_the_turn_fails(self):
        builder = self.started()
        with self.assertRaises(providers.ProviderError):
            self.turn(builder, self.decided("exec-1", "deny"),
                      hook_done("exec-1", "blocked"), turn_done("failed", {"message": "x"}))
        self.assertEqual([r["tool"] for r in builder.refusals], ["Bash"],
                         "the loop takes them from the session when no turn is returned")
        self.assertTrue((self.run_dir / "hook-decisions.jsonl").is_file())

    def test_positive_a_transport_failure_still_copies_the_decisions(self):
        builder = self.started()
        with self.assertRaisesRegex(RuntimeError, "transport closed"):
            self.turn(builder, self.decided("exec-1", "allow"), hook_done("exec-1"),
                      RuntimeError("transport closed"))
        self.assertTrue((self.run_dir / "hook-decisions.jsonl").is_file())
        self.assertIsNone(self.clients[-1].registered)


class FakeReviewClient(FakeClaudeClient):
    """One Claude review session: connect, one query, the scripted messages."""


def review_result(structured, **kwargs):
    message = result(**kwargs)
    message.structured_output = structured
    return message


@NEEDS_CLAUDE
class ClaudeReviewerTests(unittest.TestCase):
    """The Claude reviewer session (short plan section 5)."""

    REPLY = {"summary": "s", "findings": [], "files_reviewed": ["notes/a.md"]}
    SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}}}

    def setUp(self):
        FakeClaudeClient.instances = []
        self.usage = limits.ClaudeUsage()
        self.reviewer = providers.ClaudeReviewer(MODEL, "high", cwd="C:/project",
                                                 usage=self.usage,
                                                 client_factory=self.factory)
        self.script = []

    def factory(self, options):
        client = FakeReviewClient(options)
        client.script = [self.script]
        self.client = client
        return client

    def review(self, *messages):
        self.script = list(messages)
        return self.reviewer.review("review this", self.SCHEMA)

    def hook(self, tool, tool_input=None):
        return asyncio.run(self.reviewer.pre_tool_use(
            {"tool_name": tool, "tool_input": tool_input or {}}, "use-1", None))

    def test_positive_the_structured_reply_is_the_review(self):
        record = self.review(init(), said("reading"), review_result(self.REPLY))
        self.assertEqual(record.reply, self.REPLY)
        self.assertEqual((record.thread_id, record.status, record.model),
                         ("sess-1", "completed", MODEL))
        self.assertEqual(record.tokens, 10)
        self.assertEqual(record.refusals, [])
        json.dumps(record.raw)
        self.assertTrue(self.client.prompts[0].startswith(providers.MARKER + "\n\n"))
        self.assertTrue(self.client.connected and self.client.disconnected)

    def test_positive_the_session_is_read_only_by_its_options(self):
        options = self.reviewer.options(self.SCHEMA)
        self.assertEqual(options.allowed_tools, ["Read", "Grep", "Glob"])
        self.assertEqual(options.permission_mode, "default")
        self.assertEqual(options.output_format, {"type": "json_schema",
                                                 "schema": self.SCHEMA})
        self.assertEqual((options.model, options.effort, options.cwd),
                         (MODEL, "high", "C:/project"))
        matcher, = options.hooks["PreToolUse"]
        self.assertIsNone(matcher.matcher, "a matcher of None matches every tool")
        self.assertEqual(matcher.hooks, [self.reviewer.pre_tool_use])
        self.assertIsNone(options.resume, "a fresh session per round")

    def test_positive_the_hook_lets_the_read_tools_and_the_reply_through(self):
        for tool, tool_input in (("Read", {"file_path": "notes/a.md"}),
                                 ("Grep", {"pattern": "x", "path": "notes"}),
                                 ("Glob", {"pattern": "**/*.py"}),
                                 ("StructuredOutput", self.REPLY)):
            with self.subTest(tool=tool):
                self.assertEqual(self.hook(tool, tool_input), {},
                                 "no decision, so the project's own rules still run")
        self.assertEqual(self.reviewer.refusals, [])

    def test_rejection_the_hook_denies_everything_else_and_keeps_it(self):
        tools = ("Write", "Edit", "Bash", "TodoWrite", "ToolSearch", "WebFetch", "Agent",
                 "mcp__biblio-tools__append_log", "", None)
        for tool in tools:
            with self.subTest(tool=tool):
                specific = self.hook(tool, {"file_path": "x.txt"})["hookSpecificOutput"]
                self.assertEqual(specific["hookEventName"], "PreToolUse")
                self.assertEqual(specific["permissionDecision"], "deny")
                self.assertEqual(specific["permissionDecisionReason"],
                                 "Refused by the orchestrator: the reviewer is read-only.")
        self.assertEqual([r["tool"] for r in self.reviewer.refusals], list(tools))

    def test_rejection_the_read_rules_bind_the_reviewer(self):
        for tool, tool_input, reason in (
                ("Read", {"file_path": ".env"}, "may not name a `.env` file"),
                ("Grep", {"pattern": "KEY", "glob": "*"}, "may not carry a file wildcard")):
            specific = self.hook(tool, tool_input)["hookSpecificOutput"]
            self.assertEqual(specific["permissionDecision"], "deny")
            self.assertIn(reason, specific["permissionDecisionReason"])

    def test_positive_a_denial_is_returned_with_the_record(self):
        async def write_attempt(self_client=None):
            await self.reviewer.pre_tool_use(
                {"tool_name": "Write", "tool_input": {"file_path": "x.txt"}}, "u", None)
        asyncio.run(write_attempt())
        record = self.review(init(), review_result(self.REPLY))
        self.assertEqual([(r["tool"], r["reason"]) for r in record.refusals],
                         [("Write", providers.REVIEWER_REFUSAL)])
        self.assertIn("x.txt", record.refusals[0]["detail"])

    def test_positive_rate_limit_events_feed_the_runs_usage_object(self):
        self.review(init(), parse_message(RATE_EVENT), review_result(self.REPLY))
        reading = self.usage.reading()
        self.assertEqual((reading.source, reading.percent), ("reported", 30.0))

    def test_rejection_each_reply_that_is_not_a_review(self):
        with self.assertRaisesRegex(providers.ProviderError, "ended without a result"):
            self.review(init(), said("gone"))
        self.assertTrue(self.client.disconnected)
        with self.assertRaisesRegex(providers.ProviderError,
                                    r"ended in error \(error_max_turns\): max turns"):
            self.review(init(), review_result(None, is_error=True,
                                              subtype="error_max_turns",
                                              errors=["max turns"], text=None))
        for empty in (None, {}):
            with self.assertRaisesRegex(providers.ProviderError, "gave no structured reply"):
                self.review(init(), review_result(empty))

    def test_rejection_a_substituted_model(self):
        with self.assertRaisesRegex(providers.ProviderError,
                                    "not the requested 'claude-opus-5-5'"):
            self.review(init("claude-sonnet-5"), review_result(self.REPLY))
        self.assertTrue(self.client.disconnected)

    def test_rejection_a_usage_limit_mid_review(self):
        with self.assertRaises(limits.UsageLimitError) as caught:
            self.review(init(), said("", error="rate_limit"), review_result(self.REPLY))
        self.assertEqual(caught.exception.provider, "claude")

    def test_negative_an_ordinary_assistant_error_is_not_a_usage_limit(self):
        record = self.review(init(), said("oops", error="server_error"),
                             review_result(self.REPLY))
        self.assertEqual(record.reply, self.REPLY)

    def test_positive_review_is_an_ordinary_blocking_call_from_a_worker_thread(self):
        # The loop calls review through asyncio.to_thread, where no event loop runs.
        async def from_the_loop():
            self.script = [init(), review_result(self.REPLY)]
            return await asyncio.to_thread(self.reviewer.review, "review this", self.SCHEMA)
        self.assertEqual(asyncio.run(from_the_loop()).reply, self.REPLY)


class RegistryTests(unittest.TestCase):

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()

    def loaded(self, data, builder="claude"):
        path = self.root / "settings.json"
        path.write_bytes(json.dumps(data).encode("utf-8"))
        return settings.assign_roles(settings.load_settings(path), builder)

    def test_positive_the_claude_builds_pairing(self):
        loaded = settings.assign_roles(settings.load_settings(), "claude")
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

    def test_positive_the_swapped_pairing_on_the_defaults(self):
        loaded = self.loaded({}, builder="codex")
        usage = limits.ClaudeUsage()
        builder = providers.make_builder(loaded, an_approver(self.root), cwd=self.root,
                                         resume="thread-9", run_dir=self.root / RUN_ID,
                                         usage=usage)
        reviewer = providers.make_reviewer(loaded, cwd=self.root, usage=usage)
        self.assertIsInstance(builder, providers.CodexBuilder)
        self.assertEqual((builder.model, builder.effort), ("gpt-6-sol", "high"))
        # Each session is passed what it takes: a Codex builder the run folder, whose
        # name is the run id, and a Claude reviewer the run's usage object.
        self.assertEqual((builder.run_id, builder.resume), (RUN_ID, "thread-9"))
        self.assertIsInstance(reviewer, providers.ClaudeReviewer)
        self.assertEqual((reviewer.model, reviewer.effort), (MODEL, "high"))
        self.assertIs(reviewer.usage, usage)

    def test_positive_todays_pairing_is_passed_what_it_takes(self):
        loaded = settings.assign_roles(settings.load_settings(), "claude")
        usage = limits.ClaudeUsage()
        builder = providers.make_builder(loaded, an_approver(self.root), cwd=self.root,
                                         resume="sess-0", run_dir=self.root / RUN_ID,
                                         usage=usage)
        self.assertIs(builder.usage, usage)
        self.assertEqual(builder.resume, "sess-0")
        self.assertFalse(builder.needs_turn_check)
        self.assertTrue(providers.CodexBuilder.needs_turn_check)
        providers.make_reviewer(loaded, cwd=self.root, usage=usage)  # takes no usage

    def test_rejection_a_provider_with_no_session_is_named_not_substituted(self):
        loaded = settings.load_settings()
        loaded["roles"] = {"builder": "gemini", "reviewer": "llama"}
        with self.assertRaisesRegex(providers.ProviderError,
                                    "no builder session exists for gemini"):
            providers.make_builder(loaded, an_approver(self.root), cwd=self.root)
        with self.assertRaisesRegex(providers.ProviderError,
                                    "no reviewer session exists for llama"):
            providers.make_reviewer(loaded, cwd=self.root)

    def test_rejection_a_codex_builder_without_the_run_folder(self):
        loaded = self.loaded({}, builder="codex")
        with self.assertRaisesRegex(providers.ProviderError, "needs the run's folder"):
            providers.make_builder(loaded, an_approver(self.root), cwd=self.root)


class MarkerTests(unittest.TestCase):

    def test_positive_the_marker_is_the_agents_md_clause(self):
        agents = (WORKFLOW.parent.parent / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn(f"`{providers.MARKER}`", agents)


if __name__ == "__main__":
    unittest.main()
