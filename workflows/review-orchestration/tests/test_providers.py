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
import os
import re
import shutil
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
container = _load("container")  # checks imports container
checks = _load("checks")
frozen = _load("frozen")
CONFIG = WORKFLOW / "config"


def make_frozen(folder, root, edit_paths=("notes/",), run_id=None):
    """The run's frozen folder, made as the orchestrator makes it (frozen.populate, from
    the workflow's own files), since a builder session only checks it (isolation plan 6
    item 1). Returns the folder. Since A3 the edit paths and the image tag are written
    to approval.json, as approval writes them before any builder session exists (plan
    10.3 step 6)."""
    folder = Path(folder)
    folder.mkdir(parents=True)
    frozen.populate(folder, SCRIPTS, CONFIG,
                    frozen.hook_config(run_id or folder.name, root, "codex", "0" * 40))
    frozen.write_approval(folder, list(edit_paths), "bookdragon-checks:0123456789ab")
    return folder

try:
    from claude_agent_sdk import (AssistantMessage, PermissionResultAllow,
                                  PermissionResultDeny, ResultMessage, SystemMessage,
                                  TextBlock, ToolResultBlock, ToolUseBlock,
                                  UserMessage)
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


def used(tool, call_id):
    """An assistant message holding one tool-use block, as the SDK yields it."""
    return AssistantMessage(content=[ToolUseBlock(id=call_id, name=tool, input={})],
                            model=MODEL)


def returned(call_id, content, is_error=None):
    """A user message holding one tool-result block, as the SDK yields it."""
    return UserMessage(content=[ToolResultBlock(tool_use_id=call_id, content=content,
                                                is_error=is_error)])


def result(text="final answer", session_id="sess-1", is_error=False, subtype="success",
           errors=None):
    return ResultMessage(subtype=subtype, duration_ms=1, duration_api_ms=1,
                         is_error=is_error, num_turns=2, session_id=session_id,
                         total_cost_usd=0.25, usage={"input_tokens": 10}, result=text,
                         errors=errors)


def overage_event():
    info = SimpleNamespace(status="allowed", rate_limit_type="five_hour",
                           resets_at=2000000,
                           raw={"isUsingOverage": True, "unifiedWindows": {
                               "five_hour": {"utilization": 1, "resetsAt": 2000000}}})
    return type("RateLimitEvent", (), {"rate_limit_info": info})()


def hook_call(tool_name, tool_input):
    return {"hook_event_name": "PreToolUse", "session_id": "sess-1",
            "transcript_path": "t", "cwd": "c", "tool_name": tool_name,
            "tool_input": tool_input, "tool_use_id": "use-1"}


class FakeClaudeClient:
    """Stands in for ClaudeSDKClient: records what it is given, replays messages. A
    scripted item may be an async function, run where it stands (the PreToolUse callback
    firing during the turn, as the SDK calls it), or an exception, raised there (a reply
    stream that fails after it began); ``query_errors`` makes sending a prompt fail."""

    instances = []

    def __init__(self, options):
        self.options = options
        self.prompts = []
        self.script = []
        self.query_errors = []
        self.connected = False
        self.disconnected = False
        self.interrupts = 0
        FakeClaudeClient.instances.append(self)

    async def connect(self):
        self.connected = True

    async def query(self, prompt):
        self.prompts.append(prompt)
        if self.query_errors:
            raise self.query_errors.pop(0)

    async def receive_response(self):
        for message in self.script.pop(0):
            if isinstance(message, BaseException):
                raise message
            if callable(message):
                await message()
                continue
            yield message

    async def disconnect(self):
        self.disconnected = True

    async def interrupt(self):
        self.interrupts += 1


def add_rule_hooks(frozen_dir):
    """A stand-in trusted rule-hooks script in a frozen folder's trusted/ copy, enough
    for a launch's own check that the folder is whole; a test that runs it replaces it."""
    script = Path(frozen_dir).joinpath(*codex_rules.TRUSTED_RULE_HOOKS)
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("import sys\nsys.stdin.read()\n", encoding="utf-8", newline="\n")
    return script


@NEEDS_CLAUDE
class ClaudeBuilderTests(unittest.TestCase):

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        base = Path(folder.name).resolve()
        self.root = base / "project"
        (self.root / "notes").mkdir(parents=True)
        self.temp = base / "temp"
        self.run_dir = base / "runs" / RUN_ID
        self.frozen = self.temp / providers.FROZEN_PARENT / RUN_ID
        make_frozen(self.frozen, self.root)
        add_rule_hooks(self.frozen)
        FakeClaudeClient.instances = []
        # The trusted rule hooks, stood in for: each call recorded, and the answer the
        # test sets (None lets the call through).
        self.rule_calls = []
        self.rule_answer = None
        self.builder = self.make()

    def rule_hook(self, python, frozen_dir, ai, event, root, run_id, *, temp_root=None):
        self.rule_calls.append({"python": python, "frozen": frozen_dir, "ai": ai,
                                "event": event, "root": root, "run_id": run_id})
        if isinstance(self.rule_answer, BaseException):  # interrupted or failing (R49-2)
            raise self.rule_answer
        return self.rule_answer

    def make(self, **kwargs):
        return providers.ClaudeBuilder(MODEL, "medium", an_approver(self.root),
                                       cwd=self.root, client_factory=FakeClaudeClient,
                                       run_dir=self.run_dir, temp_root=self.temp,
                                       rule_hook=self.rule_hook, **kwargs)

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

    def test_rejection_paid_overage_interrupts_the_unpermitted_turn(self):
        with self.assertRaises(limits.UsageLimitError):
            self.run_turns([init(), overage_event(), result()])
        self.assertEqual(self.client.interrupts, 1)

    def test_rejection_an_event_save_failure_still_interrupts_paid_overage(self):
        def fail(reading):
            raise OSError("state unavailable")
        self.builder.usage.on_event = fail
        with self.assertRaisesRegex(limits.UsageLimitError, "saving the event failed"):
            self.run_turns([init(), overage_event(), result()])
        self.assertEqual(self.client.interrupts, 1)

    def test_positive_paid_permission_leaves_the_turn_running(self):
        self.builder.usage.paid_allowed = True
        records = self.run_turns([init(), overage_event(), result()])
        self.assertEqual(len(records), 1)
        self.assertEqual(self.client.interrupts, 0)

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
             "the shell is not available"),
            ("mcp__biblio-tools__append_log", {"directory": "memory", "note": "x"},
             "is not available to a builder"),
            ("mcp__biblio-tools__run_new_month", {"month": "2026-10"},
             "is not available to a builder"),
            ("mcp__biblio-tools__run_link_check", {"mode": "link"},
             "is not available to a builder"),
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
        self.assertIn("the shell is not available", deny.message)
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
        builder = self.make(resume="sess-0")
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

    # ------------------------------------- trusted programs only (plan 7.4, 7.6)

    def test_positive_the_options_give_only_the_check_helper_and_no_settings_hook(self):
        options = self.builder.options()
        self.assertTrue(options.strict_mcp_config, "ignores .mcp.json, user and plugins")
        self.assertEqual(options.mcp_servers, {"checks": {
            "type": "stdio", "command": sys.executable,
            "args": [str(self.frozen / "check_server.py")]}})
        self.assertEqual(json.loads(options.settings), {"disableAllHooks": True})
        # A check can take minutes: Claude's MCP call limit, in milliseconds.
        self.assertEqual(options.env, {"MCP_TOOL_TIMEOUT": "1800000"})
        self.assertEqual(self.builder.frozen_dir, self.frozen)

    def test_positive_an_allowed_edit_goes_through_the_trusted_rule_hooks(self):
        call = hook_call("Write", {"file_path": "notes/a.md", "content": "x"})
        out = asyncio.run(self.builder.pre_tool_use(call, "use-1", None))
        self.assertEqual(out, {})
        seen, = self.rule_calls
        self.assertEqual((seen["ai"], seen["run_id"], seen["frozen"], seen["root"]),
                         ("claude", RUN_ID, self.frozen, self.root))
        self.assertEqual(seen["event"]["tool_name"], "Write")
        self.assertEqual(seen["event"]["hook_event_name"], "PreToolUse")
        self.assertEqual(self.builder.callback_calls,
                         [{"tool": "Write", "id": "use-1", "decision": "allow"}])

    # ------------------------------- the callback's record (decision 39)

    def fires(self, tool, call_id, tool_input=None):
        """A scripted step: the callback judging one call mid-turn, as the SDK calls
        it before running the tool."""
        builder = self.builder
        return lambda: builder.pre_tool_use(
            hook_call(tool, tool_input if tool_input is not None
                      else {"file_path": "notes/a.md"}), call_id, None)

    def test_positive_the_callback_records_name_id_and_decision(self):
        self.rule_answer = None
        asyncio.run(self.builder.pre_tool_use(
            hook_call("Write", {"file_path": "notes/a.md"}), "w1", None))
        asyncio.run(self.builder.pre_tool_use(
            hook_call("Bash", {"command": "git status"}), "b1", None))
        self.rule_answer = "BLOCKED: rule B3 says no"
        asyncio.run(self.builder.pre_tool_use(
            hook_call("Edit", {"file_path": "notes/a.md"}), "e1", None))
        self.assertEqual(self.builder.callback_calls, [
            {"tool": "Write", "id": "w1", "decision": "allow"},
            {"tool": "Bash", "id": "b1", "decision": "deny"},
            {"tool": "Edit", "id": "e1", "decision": "deny"}])

    def test_positive_a_turn_records_its_calls_and_tool_uses_matching(self):
        self.run_turns([init(), self.fires("Write", "t1"), used("Write", "t1"),
                        self.fires("Read", "t2"), used("Read", "t2"), result()])
        record = self.builder.callback_record()
        self.assertEqual(record["judged"], [
            {"tool": "Write", "id": "t1", "decision": "allow"},
            {"tool": "Read", "id": "t2", "decision": "allow"}])
        self.assertEqual(record["tool_uses"], [{"tool": "Write", "id": "t1"},
                                               {"tool": "Read", "id": "t2"}])
        self.assertEqual((record["not_judged"], record["not_in_transcript"],
                          record["duplicate_ids"]), ([], [], []))

    def test_rejection_a_tool_use_the_callback_never_saw(self):
        self.run_turns([init(), self.fires("Write", "t1"), used("Write", "t1"),
                        used("Read", "t2"), result()])
        record = self.builder.callback_record()
        self.assertEqual(record["not_judged"], [{"id": "t2", "tool": "Read"}])
        self.assertEqual(record["not_in_transcript"], [])

    # -------------------- calls Claude Code rejects before the hook (plan 10.7)

    def test_positive_an_edit_claude_code_rejected_is_not_counted_unjudged(self):
        # Claude Code refused the edit itself, before any PreToolUse hook ran, so the
        # callback never saw it; the step's every call that ran was judged.
        refusal = ("<tool_use_error>File has not been read yet. Read it first before "
                   "writing to it.</tool_use_error>")
        self.run_turns([init(), used("Edit", "e1"), returned("e1", refusal, is_error=True),
                        self.fires("Write", "w1"), used("Write", "w1"),
                        returned("w1", "File created"), result()])
        record = self.builder.callback_record()
        self.assertEqual(record["rejected_before_hook"], [{"id": "e1", "tool": "Edit"}])
        self.assertEqual(record["not_judged"], [])
        self.assertEqual(record["judged"], [{"tool": "Write", "id": "w1",
                                             "decision": "allow"}])
        self.assertEqual((record["not_in_transcript"], record["duplicate_ids"],
                          record["undecided"]), ([], [], []))

    def test_rejection_an_error_result_without_the_element_stays_not_judged(self):
        # An error of any other kind is no evidence Claude Code refused the call before
        # the hook, so the unjudged call is still reported as such.
        self.run_turns([init(), used("Bash", "b1"),
                        returned("b1", [{"type": "text", "text": "Exit code 1"}],
                                 is_error=True), result()])
        record = self.builder.callback_record()
        self.assertEqual(record["not_judged"], [{"id": "b1", "tool": "Bash"}])
        self.assertEqual(record["rejected_before_hook"], [])

    def test_negative_a_turn_with_no_error_results_records_as_before(self):
        self.run_turns([init(), self.fires("Write", "t1"), used("Write", "t1"),
                        returned("t1", "ok", is_error=False), used("Read", "t2"),
                        returned("t2", "text"), result()])
        self.assertEqual(self.builder.callback_record(), {
            "judged": [{"tool": "Write", "id": "t1", "decision": "allow"}],
            "tool_uses": [{"tool": "Write", "id": "t1"}, {"tool": "Read", "id": "t2"}],
            "not_judged": [{"id": "t2", "tool": "Read"}], "rejected_before_hook": [],
            "not_in_transcript": [], "duplicate_ids": [], "undecided": []})

    def test_positive_each_turn_records_only_its_own_calls(self):
        # R46-1: one session serves the build and the fix; each step's record holds
        # only its own turn's calls.
        records = []

        async def go():
            await self.builder.start()
            client = FakeClaudeClient.instances[-1]
            client.script = [
                [init(), self.fires("Write", "b1"), used("Write", "b1"), result()],
                [self.fires("Edit", "f1"), used("Edit", "f1"), result()]]
            try:
                for step in ("build", "fix-R1"):
                    await self.builder.turn(step, "go")
                    records.append(self.builder.callback_record())
            finally:
                await self.builder.close()
        asyncio.run(go())
        build, fix = records
        self.assertEqual([c["id"] for c in build["judged"]], ["b1"])
        self.assertEqual([u["id"] for u in build["tool_uses"]], ["b1"])
        self.assertEqual([c["id"] for c in fix["judged"]], ["f1"])
        self.assertEqual([u["id"] for u in fix["tool_uses"]], ["f1"])
        for record in records:
            self.assertEqual((record["not_judged"], record["not_in_transcript"],
                              record["duplicate_ids"]), ([], [], []))

    def failed_fix_record(self, fix_script, query_error=None):
        """A build with one tool call, then a fix turn that fails; the fix's record."""
        async def go():
            await self.builder.start()
            client = FakeClaudeClient.instances[-1]
            client.script = [[init(), self.fires("Write", "b1"), used("Write", "b1"),
                              result()], fix_script]
            try:
                await self.builder.turn("build", "go")
                if query_error is not None:
                    client.query_errors = [query_error]
                with self.assertRaises(Exception):
                    await self.builder.turn("fix-R1", "fix")
                return self.builder.callback_record()
            finally:
                await self.builder.close()
        return asyncio.run(go())

    def test_rejection_a_fix_whose_prompt_cannot_be_sent_records_no_build_call(self):
        # R47-1: the turn-local state is set before the prompt is sent.
        record = self.failed_fix_record([], query_error=ConnectionError("send failed"))
        self.assertEqual((record["judged"], record["tool_uses"], record["not_judged"],
                          record["not_in_transcript"]), ([], [], [], []))

    def test_rejection_a_fix_whose_reply_stream_fails_records_no_build_call(self):
        record = self.failed_fix_record([init(), RuntimeError("stream broke")])
        self.assertEqual((record["judged"], record["tool_uses"], record["not_judged"],
                          record["not_in_transcript"]), ([], [], [], []))

    def rate_limited(self, tool, call_id):
        """An assistant message flagged as a usage limit that still carries a tool-use
        block, a shape the SDK allows (R49-1)."""
        return AssistantMessage(content=[ToolUseBlock(id=call_id, name=tool, input={})],
                                model=MODEL, error="rate_limit")

    def test_positive_a_rate_limited_message_keeps_its_tool_uses_in_the_record(self):
        with self.assertRaises(limits.UsageLimitError):
            self.run_turns([init(), self.fires("Write", "t1"),
                            self.rate_limited("Write", "t1"), result()])
        record = self.builder.callback_record()
        self.assertEqual(record["tool_uses"], [{"tool": "Write", "id": "t1"}])
        self.assertEqual((record["not_judged"], record["not_in_transcript"],
                          record["undecided"]), ([], [], []))

    def test_rejection_an_unjudged_call_in_a_rate_limited_message_is_not_hidden(self):
        # Without collecting first, the failed turn would record no tool use and no
        # judged call, an empty comparison that hides the call the callback never saw.
        with self.assertRaises(limits.UsageLimitError):
            self.run_turns([init(), self.rate_limited("Write", "t1"), result()])
        self.assertEqual(self.builder.callback_record()["not_judged"],
                         [{"id": "t1", "tool": "Write"}])

    def test_rejection_an_interrupted_or_failing_callback_records_no_decision(self):
        # R49-2: the callback gave the SDK no answer, so its record holds none.
        for label, error in (("cancelled", asyncio.CancelledError()),
                             ("failed", RuntimeError("rule hook crashed"))):
            with self.subTest(label):
                self.setUp()
                self.rule_answer = error
                with self.assertRaises(type(error)):
                    asyncio.run(self.builder.pre_tool_use(
                        hook_call("Write", {"file_path": "notes/a.md"}), "w1", None))
                self.assertEqual(self.builder.callback_calls,
                                 [{"tool": "Write", "id": "w1", "decision": "pending"}])
                self.assertEqual(providers.callback_match(
                    self.builder.callback_calls, [{"tool": "Write", "id": "w1"}])["undecided"],
                    [{"id": "w1", "tool": "Write"}])

    def test_rejection_what_the_rule_hooks_block_is_denied_with_their_reason(self):
        self.rule_answer = "BLOCKED: rule B3 says no"
        call = hook_call("Edit", {"file_path": "notes/a.md"})
        specific = asyncio.run(self.builder.pre_tool_use(call, "use-1", None))[
            "hookSpecificOutput"]
        self.assertEqual((specific["permissionDecision"],
                          specific["permissionDecisionReason"]),
                         ("deny", "BLOCKED: rule B3 says no"))
        refusal, = self.builder.approver.refusals
        self.assertEqual((refusal["tool"], refusal["reason"]),
                         ("Edit", "BLOCKED: rule B3 says no"))

    def test_negative_the_rule_hooks_never_run_for_a_read_or_a_refused_call(self):
        for tool, tool_input in (("Read", {"file_path": "AGENTS.md"}),
                                 ("Glob", {"pattern": "**/*.py"}),
                                 ("Write", {"file_path": "outside/a.md"}),
                                 ("Bash", {"command": "git status"}),
                                 ("mcp__checks__get_timestamp", {})):
            with self.subTest(tool=tool):
                asyncio.run(self.builder.pre_tool_use(hook_call(tool, tool_input),
                                                      "use-1", None))
        self.assertEqual(self.rule_calls, [])

    def test_positive_the_real_rule_hooks_runner_denies_a_block(self):
        # The default runner, codex_rules.run_rule_hook, against a stand-in rule-hooks
        # script that blocks as Claude's adapter does: exit 2, the reason on stderr.
        add_rule_hooks(self.frozen).write_text(
            "import sys\nsys.stdin.read()\nsys.stderr.write('BLOCKED by rule X')\n"
            "sys.exit(2)\n", encoding="utf-8", newline="\n")
        builder = providers.ClaudeBuilder(MODEL, "medium", an_approver(self.root),
                                          cwd=self.root, client_factory=FakeClaudeClient,
                                          run_dir=self.run_dir, temp_root=self.temp)
        specific = asyncio.run(builder.pre_tool_use(
            hook_call("Write", {"file_path": "notes/a.md"}), "use-1", None))[
            "hookSpecificOutput"]
        self.assertEqual(specific["permissionDecisionReason"], "BLOCKED by rule X")
        self.assertTrue((self.temp / "book-dragon-runlogs" / RUN_ID).is_dir(),
                        "the run's own fire-log folder, outside the project")

    def test_rejection_a_start_whose_frozen_folder_lacks_the_helper_or_rule_hooks(self):
        for path in (self.frozen / "check_server.py",
                     self.frozen.joinpath(*codex_rules.TRUSTED_RULE_HOOKS)):
            with self.subTest(path=path.name):
                kept = path.read_bytes()
                path.unlink()
                try:
                    with self.assertRaisesRegex(providers.ProviderError, "lacks"):
                        asyncio.run(self.make().start())
                finally:
                    path.write_bytes(kept)
        self.assertEqual(FakeClaudeClient.instances, [], "the CLI is never started")

    def test_rejection_a_claude_builder_without_the_run_folder(self):
        with self.assertRaisesRegex(providers.ProviderError, "needs the run's folder"):
            providers.ClaudeBuilder(MODEL, "medium", an_approver(self.root), cwd=self.root)


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


class FakeReviewerRpc:
    """Stands in for the reviewer's ``Codex._client``: answers the launch's read-backs
    (plan 7.6 item 4) and its ``command/exec`` self-test (R35-1) from a test's world."""

    def __init__(self, world, overrides):
        self.world = world
        self.overrides = tuple(overrides)
        self.requests = []

    def _request_raw(self, method, params=None):
        self.requests.append((method, params))
        world = self.world
        if method == "hooks/list":
            return {"data": [{"cwd": params["cwds"][0], "errors": [], "warnings": [],
                              "hooks": hooks_listing(self.overrides, world.project,
                                                     world.ignore_disable)}]}
        if method == "mcpServerStatus/list":
            return servers_listing(world.servers, self.overrides, world.keep_counting)
        if method == "config/read":
            given = None
            for override in self.overrides:
                prefix = "shell_environment_policy.set.PYTHONPATH="
                if override.startswith(prefix) and world.honour_env:
                    given = override[len(prefix):].strip('"')
            return {"config": {"shell_environment_policy": {"set": {"PYTHONPATH": given}}
                               if given is not None else None}, "origins": {}}
        if method == "command/exec":
            return world.exec_reply
        raise AssertionError(f"unexpected request {method}")


class FakeCodex:
    """Stands in for the Codex client: a model listing and one thread, and the raw
    client the reviewer's launch reads back through."""

    def __init__(self, models, events, world=None, overrides=()):
        self._models = models
        self.thread = FakeThread(events)
        self.started = None
        self.closed = False
        self.overrides = tuple(overrides)
        self._client = FakeReviewerRpc(world, overrides)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def close(self):
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

    def setUp(self):
        self.frozen = Path(tempfile.gettempdir()) / providers.FROZEN_PARENT / RUN_ID
        self.wire = (self.frozen / "tripwire").as_posix()
        # Another PYTHONPATH entry, a folder beside the run's (decision 38): no drive
        # letter on Linux, so `os.pathsep` splits it as on Windows.
        self.other = (self.frozen.parent / "other").as_posix()
        self.world = SimpleNamespace(
            project=project_hooks(), ignore_disable=False, honour_env=True,
            servers=[server("biblio-tools")], keep_counting=(),
            exec_reply={"exitCode": 0, "stderr": "",
                        "stdout": f"{self.wire}{os.pathsep}{self.other}\n"
                                  f"{self.wire}/sitecustomize.py\n"})
        self.opened = []

    def factory(self, models, events):
        def open_client(overrides):
            client = FakeCodex(list(models), events, self.world, overrides)
            self.opened.append(client)
            self.codex = client
            return client
        return open_client

    def reviewer(self, events, models=("gpt-6-sol", "gpt-6-astra"), model="gpt-6-sol"):
        return providers.CodexReviewer(model, "high", cwd="C:/project",
                                       frozen_dir=self.frozen,
                                       codex_factory=self.factory(models, events))

    def review(self, events, models=("gpt-6-sol", "gpt-6-astra"), model="gpt-6-sol"):
        return self.reviewer(events, models, model).review("review this",
                                                            stopreasons.REVIEW_SCHEMA)

    # --------------------------- trusted programs only, no project code (7.6 item 4)

    def test_positive_the_launch_overrides_and_read_backs(self):
        record = self.review(turn())
        first, second = self.opened
        base = ("features.plugins=false", "features.apps=false",
                f'shell_environment_policy.set.PYTHONPATH="{self.wire}"')
        self.assertEqual(first.overrides, base)
        self.assertTrue(first.closed, "the first client only lists the hooks and servers")
        self.assertEqual([r[0] for r in first._client.requests],
                         ["hooks/list", "mcpServerStatus/list"])
        self.assertIsNone(first.started)
        state = ("hooks.state={" + ", ".join(f"'{h['key']}'={{enabled=false}}"
                                             for h in project_hooks()) + "}")
        self.assertEqual(second.overrides, (*base, state))
        self.assertEqual([r[0] for r in second._client.requests],
                         ["hooks/list", "mcpServerStatus/list", "config/read",
                          "command/exec"])
        method, params = second._client.requests[-1]
        self.assertEqual(params, {"command": [sys.executable, "-c",
                                              providers.REVIEWER_SELF_TEST],
                                  "cwd": str(self.frozen),
                                  "sandboxPolicy": {"type": "readOnly"},
                                  "timeoutMs": 60000})
        # Live check 4b: the launch's readings travel with the review.
        self.assertEqual(record.launch["shell_pythonpath"], self.wire)
        self.assertEqual(record.launch["self_test"]["exitCode"], 0)
        self.assertEqual({e["enabled"] for e in record.launch["project_hooks"]}, {False})

    def test_rejection_each_reason_the_reviewer_does_no_review(self):
        wire, other = self.wire, self.other
        cases = [
            ("a project hook still on", {"ignore_disable": True}, "is not switched off"),
            ("a plugin's server Codex does not switch off",
             {"servers": [server("x", ("t",), plugin="p@q")], "keep_counting": ("x",)},
             "an MCP server is in force: 'x' \\(plugin p@q\\)"),
            ("a user server Codex does not switch off",
             {"servers": [server("biblio-tools"), *outside_servers()],
              "keep_counting": ("node_repl",)},
             "an MCP server is in force: 'node_repl'"),
            ("a server name that cannot be written",
             {"servers": [server("bad name!", ("t",))]},
             "name cannot be written as an override"),
            ("the shell's PYTHONPATH not reported", {"honour_env": False},
             "reports the shell's PYTHONPATH as None"),
            ("a self-test that failed", {"exec_reply": {"exitCode": 1, "stdout": ""}},
             "self-test failed.*exited 1"),
            ("the tripwire not first",
             {"exec_reply": {"exitCode": 0,
                             "stdout": f"{other}{os.pathsep}{wire}\n{wire}/sitecustomize.py\n"}},
             f"PYTHONPATH starts '{re.escape(other)}', not the tripwire folder"),
            ("no sitecustomize loaded",
             {"exec_reply": {"exitCode": 0, "stdout": f"{wire}\n\n"}},
             "the sitecustomize it loaded is 'none'"),
            ("a sitecustomize from elsewhere",
             {"exec_reply": {"exitCode": 0, "stdout": f"{wire}\nC:/x/sitecustomize.py\n"}},
             "loaded is 'C:/x/sitecustomize.py'"),
        ]
        for label, change, reason in cases:
            with self.subTest(label):
                self.setUp()
                for name, value in change.items():
                    setattr(self.world, name, value)
                with self.assertRaisesRegex(providers.ProviderError, reason):
                    self.review(turn())
                self.assertTrue(all(c.started is None for c in self.opened),
                                "no thread may start")
                self.assertTrue(all(c.closed for c in self.opened))

    def test_positive_outside_servers_are_switched_off_and_none_allowed(self):
        # Decision 35: the reviewer allows no server, so node_repl is switched off by
        # name and codex_apps by apps off; biblio-tools, with no tools, is never named.
        self.world.servers = [server("biblio-tools"), *outside_servers()]
        self.review(turn())
        first, second = self.opened
        off = [o for o in second.overrides if o.startswith("mcp_servers.")]
        self.assertEqual(off, ["mcp_servers.node_repl.enabled=false"])

    def test_rejection_a_reviewer_without_the_frozen_folder(self):
        with self.assertRaisesRegex(providers.ProviderError, "needs the run's frozen"):
            providers.CodexReviewer("gpt-6-sol", "high", cwd="C:/project")

    def test_positive_the_real_self_test_code_reports_the_tripwire(self):
        # The self-test's own code, run with the run's interpreter and a fixture
        # tripwire folder first on PYTHONPATH (no Codex): it names that folder and the
        # sitecustomize.py loaded from it, and imports no project code.
        with tempfile.TemporaryDirectory() as folder:
            wire = Path(folder) / "tripwire"
            wire.mkdir()
            (wire / "sitecustomize.py").write_text("LOADED = True\n", encoding="utf-8",
                                                   newline="\n")
            done = subprocess.run([sys.executable, "-c", providers.REVIEWER_SELF_TEST],
                                  env={**os.environ, "PYTHONPATH": str(wire),
                                       "PYTHONDONTWRITEBYTECODE": "1"},
                                  capture_output=True, encoding="utf-8", cwd=folder,
                                  timeout=60)
        first, loaded = done.stdout.splitlines()[:2]
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(providers._same_file(first.split(os.pathsep)[0], wire))
        self.assertTrue(providers._same_file(loaded, wire / "sitecustomize.py"))

    def test_positive_the_reviewer_prompt_no_longer_invites_running_tests(self):
        text = (WORKFLOW / "prompts" / "reviewer-tools-codex.md").read_text(encoding="utf-8")
        self.assertNotIn("such as the tests", text)
        self.assertIn("run no project code", text)

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
    """The project's own four Codex hooks, as hooks/list reported them on 2026-10-02,
    each with its own key."""
    return [listed("preToolUse", "project", "Bash", key="C:\\p\\.codex\\config.toml:pre:0"),
            listed("preToolUse", "project", "apply_patch",
                   key="C:\\p\\.codex\\config.toml:pre:1"),
            listed("sessionStart", "project", key="C:\\p\\.codex\\config.toml:start:0"),
            listed("stop", "project", key="C:\\p\\.codex\\config.toml:stop:0")]


def server(name, tools=(), plugin=None, error=None):
    """One mcpServerStatus/list row, with the fields the installed SDK's type names."""
    return {"name": name, "pluginId": plugin, "authStatus": "unsupported",
            "resources": [], "resourceTemplates": [], "toolsError": error,
            "tools": {tool: {"name": tool, "inputSchema": {"type": "object"}}
                      for tool in tools}}


def builder_servers():
    """A builder's listing as plan 7.6 item 1 expects it: the check helper with its two
    tools, and the project's own biblio-tools listed, disabled and with no tools."""
    return [server("checks", ("run_check", "get_timestamp")), server("biblio-tools")]


def servers_listing(rows, overrides, keep_counting=()):
    """mcpServerStatus/list as Codex reports it under ``overrides`` (decision 35's
    probe, 2026-10-11): `codex_apps` is gone once apps are off, and a server switched
    off by name is listed with no tools, unless a test has Codex ignore the switch for
    it (``keep_counting``)."""
    out = []
    for row in rows:
        name = row["name"]
        if name == "codex_apps" and providers.APPS_OFF in overrides:
            continue
        row = dict(row)
        if f"mcp_servers.{name}.enabled=false" in overrides and name not in keep_counting:
            row.update(tools={}, toolsError=None)
        out.append(row)
    return {"data": out, "nextCursor": None}


def outside_servers():
    """What a launch in this project lists with plugins off on 2026-10-11: Codex's own
    apps and a server from the user's own configuration, each counting."""
    return [server("codex_apps", ("app_a", "app_b")), server("node_repl", ("js", "eval"))]


def hooks_listing(overrides, rows, ignore_disable=False):
    """hooks/list's rows as Codex reports them under ``overrides``: a hook switched off
    by key in a hooks.state override reports enabled false, as probe 9 showed, unless
    a test has Codex ignore it."""
    out = []
    for row in rows:
        row = dict(row)
        off = f"'{row['key']}'={{enabled=false}}"
        if not ignore_disable and any(o.startswith("hooks.state=") and off in o
                                      for o in overrides):
            row["enabled"] = False
        out.append(row)
    return out


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
            hooks = hooks_listing(self.overrides, world.project, world.ignore_disable)
            for count in range(world.run_hooks):
                hooks.append(listed("preToolUse", "sessionFlags", world.matcher,
                                    trust=world.trust if trusted else "untrusted",
                                    enabled=world.enabled, timeout=world.timeout,
                                    key=world.key + ("" if count == 0 else str(count))))
            return {"data": [{"cwd": params["cwds"][0], "hooks": hooks, "errors": [],
                              "warnings": []}]}
        if method == "config/read":
            off = providers.WEB_SEARCH_OFF in self.overrides and world.search_off
            config = {"web_search": "disabled" if off else "live"}
            # As the config probe of 2026-10-12 showed (decision 36): the check helper's
            # entry carries default_tools_approval_mode only when the launch sent it. A
            # test may make Codex report something else (`report_approval`).
            sent = [o for o in self.overrides if o.startswith("mcp_servers.checks=")]
            if sent:
                found = re.search(r'default_tools_approval_mode="([a-z]+)"', sent[0])
                mode = (world.report_approval if world.report_approval != "as sent"
                        else found.group(1) if found else None)
                entry = {"command": "python", "enabled": True}
                if mode is not None:
                    entry["default_tools_approval_mode"] = mode
                config["mcp_servers"] = {"checks": entry}
            return {"config": config, "origins": {}}
        if method == "mcpServerStatus/list":
            return servers_listing(world.servers, self.overrides, world.keep_counting)
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
            script=[], servers=builder_servers(), ignore_disable=False, keep_counting=(),
            report_approval="as sent")
        self.clients = []
        self.frozen = self.temp / providers.FROZEN_PARENT / RUN_ID
        self.decisions = self.frozen / "hook-decisions.jsonl"
        make_frozen(self.frozen, self.root)

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

    def test_positive_a_first_launch_uses_the_orchestrators_folder_as_it_is(self):
        # Isolation plan 6 item 1: the orchestrator made the folder; the session copies
        # nothing into it, so a file the session would have copied stays as it was.
        marker = self.frozen / "codex-verify-commands.txt"
        marker.write_bytes(marker.read_bytes() + b"# as the orchestrator froze it\n")
        before = {p.name: p.read_bytes() for p in self.frozen.iterdir() if p.is_file()}
        builder = self.started()
        self.assertEqual(builder.frozen_dir, self.frozen)
        after = {p.name: p.read_bytes() for p in self.frozen.iterdir() if p.is_file()}
        # The self-test ran the real hook from the folder, and its line is gone.
        self.assertEqual(after.pop("hook-decisions.jsonl"), b"")
        self.assertEqual(after, before)

    def test_rejection_a_frozen_folder_that_is_missing_on_a_first_launch(self):
        frozen.remove(self.frozen)
        self.refused_start("frozen folder .* is missing")
        self.assertFalse(self.frozen.exists(), "a launch never creates the folder")
        self.assertEqual(self.clients, [], "Codex is never started")

    def test_rejection_a_frozen_folder_missing_any_file_names_it(self):
        for name in ("codex_hook.py", "approver.py", "check_server.py",
                     "codex-read-commands.txt", "verify-commands.txt", "hook.json"):
            for resume in (None, THREAD):
                with self.subTest(name=name, resume=resume):
                    self.clients = []
                    path = self.frozen / name
                    kept = path.read_bytes()
                    path.unlink()
                    try:
                        self.refused_start(f"lacks {re.escape(name)}", resume=resume)
                    finally:
                        path.write_bytes(kept)
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
        frozen.remove(self.frozen)
        self.refused_start("frozen folder .* is missing", resume=THREAD)
        self.assertFalse(self.frozen.exists(), "a resume never creates the folder")
        self.assertEqual(self.clients, [])

    def test_positive_a_first_launch_that_fails_leaves_the_folder(self):
        # The folder is the run's: the next launch checks the same one (plan 6 item 1).
        self.world.search_off = False
        self.refused_start("web search is not switched off")
        self.assertTrue((self.frozen / "codex_hook.py").is_file())
        self.assertTrue(all(client.closed for client in self.clients))
        self.world.search_off = True
        self.started()  # and the next launch finds it whole

    def test_positive_a_session_closed_before_any_turn_leaves_its_folder(self):
        builder = self.started()
        self.assertIsNone(builder.session_id,
                          "a thread with no turn cannot be resumed, so it is not offered")
        asyncio.run(builder.close())
        self.assertTrue((self.frozen / "codex_hook.py").is_file())
        self.assertTrue(self.clients[-1].closed)

    def test_positive_the_frozen_parent_is_the_orchestrators(self):
        self.assertEqual(providers.FROZEN_PARENT, frozen.FROZEN_PARENT)

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
                self.assertTrue((self.frozen / "codex_hook.py").is_file(),
                                "the run's folder stays for the next launch")

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
        # Plan 7.5 and 7.6: plugins off, and the check helper from the frozen folder as
        # the builder's one MCP server, with a call limit no shorter than a check's and
        # Codex told not to ask approval for its tools (decision 36).
        checks = (f'mcp_servers.checks={{command="{python}", '
                  f'args=["{self.frozen.as_posix()}/check_server.py"], '
                  'tool_timeout_sec=1800, default_tools_approval_mode="approve"}')
        base = (hook, 'web_search="disabled"', "features.plugins=false",
                "features.apps=false", checks)
        self.assertEqual(first.overrides, base)
        self.assertIsNone(first.handler)
        self.assertTrue(first.closed, "the first client only lists the hooks and servers")
        self.assertEqual([r[0] for r in first.requests],
                         ["hooks/list", "mcpServerStatus/list"])
        # Keys hold backslashes, so each is a TOML literal string: the run's hook gets
        # its trust, and every project hook is switched off by its own key (7.6 item 2).
        state = ("hooks.state={'" + HOOK_KEY + "'={trusted_hash=\"sha256:abc\"}, "
                 + ", ".join(f"'{h['key']}'={{enabled=false}}" for h in project_hooks())
                 + "}")
        self.assertEqual(second.overrides, (*base, state))
        self.assertEqual(second.handler, builder._approve)
        self.assertEqual([r[0] for r in second.requests],
                         ["hooks/list", "config/read", "mcpServerStatus/list",
                          "thread/start"])
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
        self.assertEqual(record["checks_approval"], "approve")  # live check 3
        self.assertEqual(record["thread"], {"method": "start", "id": THREAD,
                                            "sandbox": dict(SANDBOX),
                                            "approvalPolicy": "untrusted"})
        project = [hook for hook in project_hooks()]
        self.assertEqual(len(record["project_hooks"]), len(project))
        self.assertTrue(all(set(entry) == {"eventName", "matcher", "trustStatus", "enabled"}
                            for entry in record["project_hooks"]))
        self.assertEqual({entry["enabled"] for entry in record["project_hooks"]}, {False})
        # Live check 4a: the servers read back, the helper's tools by name.
        self.assertEqual(record["mcp_servers"], [
            {"name": "checks", "pluginId": None, "tools": ["get_timestamp", "run_check"],
             "toolsError": None},
            {"name": "biblio-tools", "pluginId": None, "tools": [], "toolsError": None}])
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
            # Decision 36: Codex not reporting the check helper's approval setting, or
            # reporting another value, is refused before any turn.
            ("the approval setting not reported", {"report_approval": None},
             "does not report the check helper's approval setting \\(default_tools_"
             "approval_mode reads None"),
            ("the approval setting reported as prompt", {"report_approval": "prompt"},
             "default_tools_approval_mode reads 'prompt', not 'approve'"),
            # Plan 7.6 item 2: Codex not honouring the switch-off is refused.
            ("a project hook still on", {"ignore_disable": True},
             "the project's preToolUse hook \\('Bash'\\) is not switched off"),
            # Plan 7.6 item 1: any server but the helper that still counts (reports tools
            # or a failed discovery) once switched off by name refuses the session, by
            # name: here Codex is made to ignore the switch-off (decision 35's backstop).
            ("a plugin's server",
             {"servers": [*builder_servers()[:1], server("biblio-tools", ("verify_setup",),
                                                         plugin="biblio-tools@book-dragon")],
              "keep_counting": ("biblio-tools",)},
             "an MCP server other than the check helper is in force: 'biblio-tools' "
             "\\(plugin biblio-tools@book-dragon\\)"),
            ("a project server with tools",
             {"servers": [server("checks", ("run_check",)), server("biblio-tools", ("x",))],
              "keep_counting": ("biblio-tools",)},
             "an MCP server other than the check helper is in force: 'biblio-tools'"),
            ("a server whose discovery failed",
             {"servers": [*builder_servers(), server("added", error="timed out")],
              "keep_counting": ("added",)},
             "an MCP server other than the check helper is in force: 'added'"),
            ("a user server Codex does not switch off",
             {"servers": [*builder_servers(), *outside_servers()],
              "keep_counting": ("node_repl",)},
             "an MCP server other than the check helper is in force: 'node_repl'"),
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

    def test_positive_servers_from_outside_the_project_are_switched_off_by_name(self):
        # Decision 35, with the listing live check 4c met: apps off removes codex_apps;
        # node_repl is switched off by name; biblio-tools (no tools) and checks (the
        # helper) are never named.
        self.world.servers = [*builder_servers(), *outside_servers()]
        builder = self.started()
        first, second = self.clients
        self.assertIn("features.apps=false", first.overrides)
        off = [o for o in second.overrides if o.startswith("mcp_servers.") and
               o.endswith(".enabled=false")]
        self.assertEqual(off, ["mcp_servers.node_repl.enabled=false"])
        self.assertEqual({entry["name"] for entry in builder.launch_record["mcp_servers"]},
                         {"checks", "biblio-tools", "node_repl"})

    def test_rejection_a_server_name_that_cannot_be_written(self):
        self.world.servers = [*builder_servers(), server("bad name!", ("t",))]
        self.refused_start("name cannot be written as an override")
        self.assertEqual(len(self.clients), 1, "the session's own client never starts")

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
        make_frozen(self.temp / providers.FROZEN_PARENT / RUN_ID, self.root)
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

    def test_rejection_a_project_pre_tool_use_hook_that_ran_whatever_its_status(self):
        # Plan 7.6 item 3: the project's hooks are off for a builder, so a run of one
        # ended the turn whatever it reported, even a block or a pass.
        for status in ("blocked", "completed", "failed"):
            for source in ("project", "user"):
                with self.subTest(status=status, source=source):
                    self.setUp()
                    builder = self.started()
                    self.ended(builder, self.decided("exec-1", "allow"),
                               hook_done("exec-1"),
                               hook_done("exec-1", status, source=source), turn_done(),
                               reason=f"a {source} preToolUse hook ran \\('{status}'")

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
        # Every kind neither a message, a tool call nor one of the two passed below still
        # ends the turn: here a kind the pinned SDK defines and one it does not.
        for kind in ("exitedReviewMode", "someFutureKind"):
            with self.subTest(kind=kind):
                self.setUp()
                builder = self.started()
                with self.assertRaisesRegex(providers.ProviderError,
                                            "an item of a kind the session does not "
                                            f"know: {kind}"):
                    self.turn(builder, item_done({"type": kind, "id": "k1"}),
                              said_codex("ok"), turn_done())

    def test_positive_a_context_compaction_item_is_accepted_without_a_decision(self):
        # Plan 10.3, chunk 2's first follow-on: in openai-codex 0.157.0 the item holds
        # only `id` and `type`, so there is nothing for a hook to judge.
        builder = self.started()
        record = self.turn(builder, item_done({"type": "contextCompaction", "id": "c1"}),
                           said_codex("ok"), turn_done())
        self.assertEqual(record.final_text, "ok")

    def test_positive_a_plan_item_is_accepted_without_a_decision(self):
        # The same follow-on: a plan item holds only `id`, `text` and `type`.
        builder = self.started()
        record = self.turn(builder, item_done({"type": "plan", "id": "p1",
                                               "text": "a plan"}),
                           said_codex("ok"), turn_done())
        self.assertEqual(record.final_text, "ok")

    def test_negative_the_two_passed_kinds_are_not_tool_calls(self):
        # Passing them is not judging them as tools: they are in neither list the
        # hook's decisions are reconciled against.
        self.assertEqual(providers._CODEX_NO_TOOL_ITEMS, {"contextCompaction", "plan"})
        self.assertFalse(providers._CODEX_NO_TOOL_ITEMS & providers._CODEX_TOOL_ITEMS)
        self.assertFalse(providers._CODEX_NO_TOOL_ITEMS & providers._CODEX_MESSAGE_ITEMS)

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

    def test_rejection_a_check_helper_approval_request_still_ends_the_turn(self):
        # Decision 36: with the setting in force none should arrive, but one that does
        # (a Codex that renamed or ignored it) is still not answered: the turn ends,
        # even after the hook allowed the call. The request is as proof run 1 recorded.
        builder = self.started()
        replies = []
        request = {"threadId": THREAD, "turnId": BUILD_TURN, "serverName": "checks",
                   "mode": "form", "message": 'Allow the checks MCP server to run tool '
                   '"run_check"?', "requestedSchema": {"type": "object", "properties": {}},
                   "_meta": {"codex_approval_kind": "mcp_tool_call",
                             "persist": ["session", "always"],
                             "tool_params": {"command": "python -m py_compile x.py"}}}
        self.ended(builder,
                   self.decided("exec-1", "allow", tool="mcp__checks__run_check"),
                   hook_done("exec-1"),
                   lambda client: replies.append(client.handler(
                       "mcpServer/elicitation/request", request)),
                   said_codex("unreached"), turn_done(),
                   reason="a request the orchestrator does not answer: "
                          "mcpServer/elicitation/request")
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
        # Plan 7.6 item 4: no MCP server at all, and no settings-file hook.
        self.assertTrue(options.strict_mcp_config)
        self.assertEqual(options.mcp_servers, {})
        self.assertEqual(json.loads(options.settings), {"disableAllHooks": True})

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

    def test_rejection_paid_overage_interrupts_the_review(self):
        with self.assertRaises(limits.UsageLimitError):
            self.review(init(), overage_event(), review_result(self.REPLY))
        self.assertEqual(self.client.interrupts, 1)
        self.assertTrue(self.client.disconnected)

    def test_positive_paid_permission_allows_the_review_to_complete(self):
        self.usage.paid_allowed = True
        record = self.review(init(), overage_event(), review_result(self.REPLY))
        self.assertEqual(record.reply, self.REPLY)
        self.assertEqual(self.client.interrupts, 0)

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

    def test_positive_claude_turn_limits_come_from_resolved_settings(self):
        loaded = self.loaded({"max_turns": {"builder": 17, "reviewer": 39}})
        builder = providers.make_builder(loaded, an_approver(self.root), cwd=self.root,
                                         run_dir=self.root / RUN_ID)
        self.assertEqual(builder.options().max_turns, 17)
        swapped = self.loaded({"max_turns": {"builder": 17, "reviewer": 39}},
                              builder="codex")
        reviewer = providers.make_reviewer(swapped, cwd=self.root, usage=limits.ClaudeUsage())
        self.assertEqual(reviewer.options({"type": "object"}).max_turns, 39)

    def test_positive_the_claude_builds_pairing(self):
        loaded = settings.assign_roles(settings.load_settings(), "claude")
        builder = providers.make_builder(loaded, an_approver(self.root), cwd=self.root,
                                         run_dir=self.root / RUN_ID)
        reviewer = providers.make_reviewer(loaded, cwd=self.root, frozen_dir=self.root)
        self.assertIsInstance(builder, providers.ClaudeBuilder)
        self.assertEqual((builder.model, builder.effort), (MODEL, "medium"))
        self.assertIsInstance(reviewer, providers.CodexReviewer)
        self.assertEqual((reviewer.model, reviewer.effort), ("gpt-6-sol", "high"))
        # Each is passed what it takes: the Claude builder the run folder, for its
        # frozen folder; the Codex reviewer the frozen folder, for its tripwire.
        self.assertEqual(builder.run_id, RUN_ID)
        self.assertEqual(reviewer.frozen_dir, self.root)

    def test_positive_models_come_from_the_settings_not_the_code(self):
        loaded = self.loaded({"models": {"codex": {"reviewer": {"model": "gpt-7",
                                                                "effort": "max"}}}})
        reviewer = providers.make_reviewer(loaded, cwd=self.root, frozen_dir=self.root)
        self.assertEqual((reviewer.model, reviewer.effort), ("gpt-7", "max"))

    def test_positive_the_swapped_pairing_on_the_defaults(self):
        loaded = self.loaded({}, builder="codex")
        usage = limits.ClaudeUsage()
        builder = providers.make_builder(loaded, an_approver(self.root), cwd=self.root,
                                         resume="thread-9", run_dir=self.root / RUN_ID,
                                         usage=usage)
        reviewer = providers.make_reviewer(loaded, cwd=self.root, usage=usage,
                                           frozen_dir=self.root)  # a Claude one takes none
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
        providers.make_reviewer(loaded, cwd=self.root, usage=usage,
                                frozen_dir=self.root)  # takes no usage
        with self.assertRaisesRegex(providers.ProviderError, "needs the run's frozen"):
            providers.make_reviewer(loaded, cwd=self.root, usage=usage)

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


PROJECT = WORKFLOW.parent.parent


class RuleHooksFromTrustedCopyTests(unittest.TestCase):
    """Plan 7.6 item 3 and section 8, "Rule hook from the trusted copy": the real rule
    hooks, run from a frozen folder's trusted/ copy by ``codex_rules.run_rule_hook``,
    reach rule B3 and load the personal-data guard beside their own code, never the live
    project's; the fire log lands in the run's own folder outside the project; and the
    frozen folder is unchanged after a block and after a warning."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        base = Path(folder.name).resolve()
        self.root = base / "project"
        self.root.mkdir()
        (self.root / "AGENTS.md").write_bytes(b"# agents\n")
        subprocess.run(["git", "init", "-q"], cwd=str(self.root), check=True,
                       capture_output=True)
        # A side effect planted in the live tree's guard: it must never run.
        self.fired = base / "fired-live-guard"
        live = self.root / "workflows" / "personal-data-guard" / "scripts"
        live.mkdir(parents=True)
        (live / "run.py").write_text(f"open(r'{self.fired}', 'w').close()\n",
                                     encoding="utf-8", newline="\n")
        self.temp = base / "temp"
        self.frozen = self.temp / providers.FROZEN_PARENT / RUN_ID
        trusted = self.frozen / "trusted" / "workflows"
        skip = shutil.ignore_patterns("__pycache__", "LOG.md", "*.pyc")
        for name in ("rule-hooks", "personal-data-guard"):
            shutil.copytree(PROJECT / "workflows" / name / "scripts",
                            trusted / name / "scripts", ignore=skip)

    def state(self):
        return {p.relative_to(self.frozen).as_posix(): p.read_bytes()
                for p in sorted(self.frozen.rglob("*")) if p.is_file()}

    def judge(self, event):
        environ = {k: v for k, v in os.environ.items()
                   if k not in ("BOOK_DRAGON_ROOT", "BOOK_DRAGON_FIRE_LOG")}
        return codex_rules.run_rule_hook(sys.executable, self.frozen, "claude", event,
                                         self.root, RUN_ID, temp_root=self.temp,
                                         environ=environ)

    def test_positive_b3_uses_the_trusted_guard_and_the_runs_own_fire_log(self):
        before = self.state()
        address = "a.person" + "@" + "mailbox.org"  # built here, so this file holds none
        block = self.judge({"hook_event_name": "PreToolUse", "tool_name": "Write",
                            "tool_input": {"file_path": str(self.root / "notes.md"),
                                           "content": f"Write to {address}."},
                            "cwd": str(self.root), "session_id": "s"})
        self.assertIsNotNone(block, "rule B3 blocks a committable file holding an email")
        self.assertIn("personal data", block)
        self.assertFalse(self.fired.exists(), "the live project's guard never ran")
        self.assertEqual(self.state(), before, "nothing written in the frozen folder")
        # A warning: rule A3's byte-level allowance, logged and let through.
        warned = self.judge({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                             "tool_input": {"command": "grep -c $'\\r' AGENTS.md"},
                             "cwd": str(self.root), "session_id": "s"})
        self.assertIsNone(warned)
        self.assertEqual(self.state(), before)
        log = self.temp / "book-dragon-runlogs" / RUN_ID / "fire-log.jsonl"
        events = [json.loads(line)["event"]
                  for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertIn("block", events)
        self.assertIn("warn", events)
        self.assertFalse((self.root / "workflows" / "rule-hooks").exists(),
                         "no fire log, or anything else, lands in the project")


class UncachedTokensTests(unittest.TestCase):
    """Plan 10.10 item 6 (A3 chunk 1): each step's tokens without cache reads, the one
    figure the price alarm compares."""

    def test_positive_claude_leaves_out_cache_reads_only(self):
        usage = {"input_tokens": 10, "cache_creation_input_tokens": 20,
                 "output_tokens": 5, "cache_read_input_tokens": 9000}
        self.assertEqual(providers.claude_uncached_tokens(usage), 35)
        self.assertEqual(providers.claude_tokens(usage), 9035)  # the total is unchanged
        self.assertEqual(providers.uncached_tokens(limits.CLAUDE, usage), 35)

    def test_positive_a_missing_cache_field_counts_as_zero(self):
        self.assertEqual(providers.claude_uncached_tokens(
            {"input_tokens": 10, "output_tokens": 5}), 15)
        self.assertEqual(providers.codex_uncached_tokens(
            {"total": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}}), 15)

    def test_rejection_without_input_or_output_there_is_no_figure(self):
        for usage in ({"output_tokens": 5}, {"input_tokens": 10},
                      {"input_tokens": True, "output_tokens": 5}, None, "x"):
            with self.subTest(usage=usage):
                self.assertIsNone(providers.claude_uncached_tokens(usage))
        for usage in ({"total": {"outputTokens": 5, "totalTokens": 5}},
                      {"total": {"inputTokens": 5, "totalTokens": 5}},
                      {"total": {"inputTokens": 5, "outputTokens": 1}},  # no total
                      {"total": {"total_tokens": 1000}}, None):
            with self.subTest(usage=usage):
                self.assertIsNone(providers.codex_uncached_tokens(usage))

    def test_positive_codex_reads_either_spelling_from_the_block_codex_tokens_reads(self):
        camel = {"cachedInputTokens": 30, "inputTokens": 100, "outputTokens": 7,
                 "totalTokens": 107}
        snake = {"cached_input_tokens": 30, "input_tokens": 100, "output_tokens": 7,
                 "total_tokens": 107}
        for block in (camel, snake):
            with self.subTest(block=sorted(block)):
                self.assertEqual(providers.codex_uncached_tokens({"total": block}), 77)
                self.assertEqual(providers.uncached_tokens(limits.CODEX, {"total": block}),
                                 77)
        # total first, then last, as codex_tokens reads its total.
        both = {"total": camel, "last": {"inputTokens": 1, "outputTokens": 1,
                                         "totalTokens": 2}}
        self.assertEqual((providers.codex_tokens(both),
                          providers.codex_uncached_tokens(both)), (107, 77))
        only_last = {"total": {"x": 1}, "last": camel}
        self.assertEqual((providers.codex_tokens(only_last),
                          providers.codex_uncached_tokens(only_last)), (107, 77))

    def test_negative_an_unknown_provider_has_no_figure(self):
        self.assertIsNone(providers.uncached_tokens(None, {"input_tokens": 1,
                                                           "output_tokens": 1}))


class MarkerTests(unittest.TestCase):

    def test_positive_the_marker_is_the_agents_md_clause(self):
        agents = (WORKFLOW.parent.parent / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn(f"`{providers.MARKER}`", agents)


class CallbackMatchTests(unittest.TestCase):
    """Decision 39 and R45-1: the comparison counts (id, tool) pairs on each side."""

    def judged(self, *pairs):
        return [{"tool": tool, "id": call_id, "decision": "allow"} for call_id, tool in pairs]

    def uses(self, *pairs):
        return [{"tool": tool, "id": call_id} for call_id, tool in pairs]

    def test_positive_one_decision_per_call_matches(self):
        self.assertEqual(providers.callback_match(self.judged(("a", "Write"), ("b", "Read")),
                                                  self.uses(("b", "Read"), ("a", "Write"))),
                         {"not_judged": [], "rejected_before_hook": [],
                          "not_in_transcript": [], "duplicate_ids": [], "undecided": []})

    def test_rejection_a_call_still_pending_is_undecided(self):
        # R49-2: a callback that never returned leaves its call pending.
        judged = self.judged(("a", "Write"))
        judged[0]["decision"] = "pending"
        match = providers.callback_match(judged, self.uses(("a", "Write")))
        self.assertEqual(match["undecided"], [{"id": "a", "tool": "Write"}])
        self.assertEqual((match["not_judged"], match["not_in_transcript"]), ([], []))

    def test_rejection_the_same_call_recorded_twice(self):
        match = providers.callback_match(self.judged(("a", "Write"), ("a", "Write")),
                                         self.uses(("a", "Write")))
        self.assertEqual(match["not_in_transcript"], [{"id": "a", "tool": "Write"}])
        self.assertEqual(match["duplicate_ids"], ["a"])
        self.assertEqual(match["not_judged"], [])

    def test_rejection_an_id_recorded_under_another_tool(self):
        match = providers.callback_match(self.judged(("a", "Read")),
                                         self.uses(("a", "Write")))
        self.assertEqual(match["not_judged"], [{"id": "a", "tool": "Write"}])
        self.assertEqual(match["not_in_transcript"], [{"id": "a", "tool": "Read"}])
        self.assertEqual(match["duplicate_ids"], [])

    def test_rejection_a_call_with_no_decision_and_a_decision_with_no_call(self):
        match = providers.callback_match(self.judged(("a", "Write"), ("x", "Bash")),
                                         self.uses(("a", "Write"), ("b", "Read")))
        self.assertEqual(match["not_judged"], [{"id": "b", "tool": "Read"}])
        self.assertEqual(match["not_in_transcript"], [{"id": "x", "tool": "Bash"}])

    def test_negative_an_empty_turn_matches(self):
        self.assertEqual(providers.callback_match([], []),
                         {"not_judged": [], "rejected_before_hook": [],
                          "not_in_transcript": [], "duplicate_ids": [], "undecided": []})


@NEEDS_CLAUDE
class ClaudeProbeTests(unittest.TestCase):

    def probe(self, messages):
        def factory(options):
            client = FakeClaudeClient(options)
            client.script = [messages]
            return client
        with tempfile.TemporaryDirectory() as folder:
            record = asyncio.run(providers.probe_claude(
                "claude-haiku-5-5", cwd=folder, client_factory=factory))
            client = FakeClaudeClient.instances[-1]
            self.assertEqual(client.prompts, ["ok"])
            self.assertEqual(client.options.max_turns, 1)
            self.assertEqual(client.options.cwd, folder)
            self.assertEqual(client.options.allowed_tools, [])
            self.assertEqual(client.options.mcp_servers, {})
            self.assertEqual(client.options.setting_sources, [])
            self.assertTrue(client.options.strict_mcp_config)
            self.assertEqual(json.loads(client.options.settings), {"disableAllHooks": True})
            return record

    def test_positive_event_and_tokens_are_separate(self):
        record = self.probe([init("claude-haiku-5-5"), overage_event(), result()])
        self.assertEqual(len(record.events), 1)
        self.assertEqual(record.reading.reason, "using paid overage")
        self.assertEqual(record.tokens, 10)

    def test_negative_no_event_is_unavailable(self):
        record = self.probe([init("claude-haiku-5-5"), result()])
        self.assertEqual(record.events, [])
        self.assertEqual(record.reading.source, limits.UNAVAILABLE)

    def test_rejection_a_different_init_model(self):
        with tempfile.TemporaryDirectory() as folder:
            seen = []
            def factory(options):
                client = FakeClaudeClient(options)
                client.script = [[overage_event(), init("another-model"), result()]]
                return client
            with self.assertRaisesRegex(providers.ProviderError, "not the requested"):
                asyncio.run(providers.probe_claude(
                    "claude-haiku-5-5", cwd=folder, client_factory=factory,
                    on_event=seen.append))
            self.assertEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()
