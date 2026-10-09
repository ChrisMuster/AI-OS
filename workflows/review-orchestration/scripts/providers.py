#!/usr/bin/env python3
"""
providers.py - the builder and reviewer sessions a run drives (Stage A2).

Roles are named by what they do, not by who does them. A run asks for a builder
session and a reviewer session (``make_builder``, ``make_reviewer``), and the run's
``roles``, set from the builder the user names at the start (``--builder``), says
which provider holds each. Both pairings are built:

  ClaudeBuilder   one Claude session for the whole build, resumed for every fix pass.
                  Every tool call goes through the run's ``Approver`` in a PreToolUse
                  hook, so a call the project's allowlist permits is still checked;
                  rate-limit events feed ``limits.ClaudeUsage``, a limit hit
                  mid-session is raised as ``limits.UsageLimitError``, and a turn that
                  ends in error is raised as ``ProviderError``.
  CodexReviewer   a fresh read-only Codex session for every review round, answering
                  in structured JSON against a schema. The turn's events are read
                  directly, so a limit hit mid-turn keeps its structured code and is
                  raised as ``limits.UsageLimitError``.
  CodexBuilder    one Codex thread for the whole build, resumed for every fix pass.
                  It is held by a hook the session supplies when it launches Codex
                  (``codex_hook.py``, run from a frozen copy outside the project), with
                  Codex's sandbox beneath it for file writes and an approval handler
                  that accepts only what the hook allowed. The session does no builder
                  work until Codex reports the hook, and the project's own hooks, in
                  force, and it ends the turn if a hook run fails.
  ClaudeReviewer  a fresh read-only Claude session for every review round: only Read,
                  Grep and Glob, under the read rules of ``approver.read_rules``, with
                  the reply's shape enforced by the SDK's ``output_format``.

Asking for a provider with no session for the role raises ``ProviderError`` naming
what is missing, rather than quietly running another pairing.

Every session starts with the orchestrated-session marker as the first line of its
first prompt, so the session skips the AGENTS.md startup sequence (which would
otherwise acknowledge the user's handoff and memory watermarks). A requested model
the provider does not have is an error, never a substitution (plan section 3).

The SDKs are imported inside the methods that need them, and every SDK entry point
can be replaced by a factory argument, so the tests drive the sessions with fakes and
no model call.
"""

import asyncio
import dataclasses
import json
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import codex_rules
import limits
from approver import read_rules
from settings import model_for

MARKER = "Orchestrated session: skip the AGENTS.md session-startup sequence."

EDIT_MAX_TURNS = 80
REVIEW_MAX_TURNS = 200

# The Codex builder's settings (short plan, facts 1, 4, 12 and 13). The frozen folder's
# parent is frozen.FROZEN_PARENT's; a test checks the two agree.
FROZEN_PARENT = "book-dragon-orchestration"
HOOK_TIMEOUT_SECONDS = 30
HOOK_MATCHER = ".*"  # the run's hook applies to every tool, and Codex reports it so
CODEX_APPROVAL_POLICY = "untrusted"
CODEX_SANDBOX_CONFIG = {"sandbox_workspace_write": {
    "network_access": False, "exclude_tmpdir_env_var": True, "exclude_slash_tmp": True,
    "writable_roots": []}}
CODEX_SANDBOX_REPORTED = {"type": "workspaceWrite", "writableRoots": [],
                          "networkAccess": False, "excludeTmpdirEnvVar": True,
                          "excludeSlashTmp": True}
WEB_SEARCH_OFF = 'web_search="disabled"'
# Items of a Codex turn that are the model talking, and items that are tool calls.
# Every tool call must have a decision line from the run's hook.
_CODEX_MESSAGE_ITEMS = frozenset({"userMessage", "agentMessage", "reasoning"})
_CODEX_TOOL_ITEMS = frozenset({"commandExecution", "fileChange", "webSearch",
                               "mcpToolCall", "dynamicToolCall", "collabAgentToolCall",
                               "imageGeneration", "imageView"})

REVIEWER_TOOLS = ("Read", "Grep", "Glob")
STRUCTURED_OUTPUT_TOOL = "StructuredOutput"
REVIEWER_REFUSAL = "Refused by the orchestrator: the reviewer is read-only."


class ProviderError(Exception):
    """A provider failed, or cannot hold the role asked of it. The run ends ``error``."""


@dataclasses.dataclass
class TurnRecord:
    """One builder turn, for the run record and report."""

    name: str
    final_text: str
    session_id: str | None
    model: str | None
    num_turns: int | None
    is_error: bool | None
    usage: dict | None
    cost_usd_equivalent: float | None
    wall_seconds: float
    messages: list
    tokens: int | None = None
    refusals: list = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class ReviewRecord:
    """One review round's session, for the run record and report. ``thread_id`` is the
    Codex thread or the Claude session."""

    reply: dict
    thread_id: str | None
    status: str | None
    usage: object
    wall_seconds: float
    raw: object
    tokens: int | None = None
    model: str | None = None
    refusals: list = dataclasses.field(default_factory=list)


def claude_tokens(usage):
    """A Claude session's tokens: input, output and both cache counts added."""
    if not isinstance(usage, dict):
        return None
    keys = ("input_tokens", "output_tokens", "cache_read_input_tokens",
            "cache_creation_input_tokens")
    values = [usage.get(key) for key in keys]
    numbers = [v for v in values if isinstance(v, int) and not isinstance(v, bool)]
    return sum(numbers) if numbers else None


def codex_tokens(usage):
    """A Codex thread's total tokens, from either spelling of its usage."""
    if not isinstance(usage, dict):
        return None
    for part in ("total", "last"):
        block = usage.get(part)
        if isinstance(block, dict):
            for key in ("total_tokens", "totalTokens"):
                if isinstance(block.get(key), int):
                    return block[key]
    return None


def with_marker(prompt):
    """The prompt with the marker as its first line, added once."""
    return prompt if prompt.startswith(MARKER) else f"{MARKER}\n\n{prompt}"


def jsonable(obj):
    """A JSON-safe copy of an SDK object, for transcripts."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {"_type": type(obj).__name__,
                **{f.name: jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}}
    if hasattr(obj, "model_dump"):
        return {"_type": type(obj).__name__, **obj.model_dump(mode="json")}
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return repr(obj)


# ----------------------------------------------------------------------- builder


class ClaudeBuilder:
    """The builder session on Claude, through ``claude-agent-sdk``."""

    provider = limits.CLAUDE
    wants = ("resume", "usage")
    # The check after every turn is for a Codex builder; a Claude builder's every call
    # goes through the approver in the orchestrator's own process.
    needs_turn_check = False
    frozen_dir = None

    def __init__(self, model, effort, approver, *, cwd, resume=None, client_factory=None,
                 usage=None, max_turns=EDIT_MAX_TURNS):
        self.model = model
        self.effort = effort
        self.approver = approver
        self.cwd = str(cwd)
        self.resume = resume
        self.usage = usage if usage is not None else limits.ClaudeUsage()
        self.max_turns = max_turns
        self._client_factory = client_factory
        self._client = None
        self._first_prompt_sent = resume is not None
        self.session_id = resume
        self.stderr = []
        self.last_messages = []

    async def pre_tool_use(self, hook_input, tool_use_id, context):
        """A PreToolUse hook that puts every tool call past the approver.

        ``can_use_tool`` alone is not enough: the SDK calls it only for a call the
        permission rules would otherwise ask about, never for one a
        ``permissions.allow`` rule in the project's settings already permits (the
        installed SDK's docstring for ``ClaudeAgentOptions.can_use_tool``, and review
        finding R1-1). A hook runs for every call. A refusal is returned as a ``deny``
        decision; an allowed call gets no decision at all, so the normal rules carry
        on, rather than ``allow``, which would also skip every rule after it.
        """
        decision = self.approver.decide(hook_input.get("tool_name"),
                                        hook_input.get("tool_input"))
        if decision.allowed:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "deny",
                                       "permissionDecisionReason": decision.reason}}

    async def can_use_tool(self, tool_name, tool_input, context):
        """The SDK's permission callback: the approver's answer, in SDK types. A second
        layer behind ``pre_tool_use``, for a call the rules would ask about."""
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
        decision = self.approver.decide(tool_name, tool_input)
        if decision.allowed:
            return PermissionResultAllow()
        return PermissionResultDeny(message=decision.reason)

    def options(self):
        from claude_agent_sdk import ClaudeAgentOptions, HookMatcher
        # A matcher of None matches every tool.
        hooks = {"PreToolUse": [HookMatcher(matcher=None, hooks=[self.pre_tool_use])]}
        return ClaudeAgentOptions(cwd=self.cwd, model=self.model, effort=self.effort,
                                  permission_mode="default", can_use_tool=self.can_use_tool,
                                  hooks=hooks, include_hook_events=True,
                                  max_turns=self.max_turns, resume=self.resume,
                                  stderr=self.stderr.append)

    async def start(self):
        if self._client_factory is None:
            from claude_agent_sdk import ClaudeSDKClient
            factory = ClaudeSDKClient
        else:
            factory = self._client_factory
        self._client = factory(options=self.options())
        await self._client.connect()

    async def turn(self, name, prompt):
        """Send one prompt and collect the session's answer to it."""
        if self._client is None:
            raise ProviderError("the builder session has not been started")
        if not self._first_prompt_sent:
            prompt = with_marker(prompt)
            self._first_prompt_sent = True
        started = time.time()
        await self._client.query(prompt)
        messages, text, result, init_model = [], [], None, None
        # Kept on the session as it grows, so a turn cut off by a usage limit or a
        # provider failure still leaves its partial transcript for the run record.
        self.last_messages = messages
        async for message in self._client.receive_response():
            messages.append(jsonable(message))
            kind = type(message).__name__
            if kind == "RateLimitEvent":
                self.usage.observe(message)
            elif kind == "SystemMessage" and getattr(message, "subtype", None) == "init":
                init_model = (getattr(message, "data", None) or {}).get("model")
                if init_model and init_model != self.model:
                    raise ProviderError(f"Claude started on {init_model!r}, not the "
                                        f"requested {self.model!r}; a model is never "
                                        "substituted")
            elif kind == "AssistantMessage":
                hit = limits.claude_message_error(message)
                if hit is not None:
                    raise hit
                text += [block.text for block in getattr(message, "content", [])
                         if type(block).__name__ == "TextBlock"]
            elif kind == "ResultMessage":
                result = message
        if result is None:
            raise ProviderError("the builder session ended without a result")
        self.session_id = result.session_id or self.session_id
        if result.is_error:
            # A provider failure or the turn cap: never a completed pass (R1-2). The
            # partial transcript stays in last_messages for the run record.
            detail = "; ".join(result.errors or []) or result.result or "no detail"
            raise ProviderError(f"the builder turn ended in error ({result.subtype}): "
                                f"{detail}")
        final = result.result if result.result is not None else "\n".join(text)
        return TurnRecord(name=name, final_text=final, session_id=self.session_id,
                          model=init_model, num_turns=result.num_turns,
                          is_error=result.is_error, usage=result.usage,
                          cost_usd_equivalent=result.total_cost_usd,
                          wall_seconds=round(time.time() - started, 1), messages=messages,
                          tokens=claude_tokens(result.usage))

    async def close(self):
        if self._client is not None:
            await self._client.disconnect()
            self._client = None


def _run_hook_once(python, script, run_id, event, timeout):
    """Run the hook program once, as Codex would. Returns its exit code and stdout."""
    done = subprocess.run([python, script, run_id], input=json.dumps(event),
                          capture_output=True, encoding="utf-8", errors="replace",
                          timeout=timeout)
    return done.returncode, done.stdout


def _note(notification):
    """One Codex notification as a JSON-safe ``{"method", "payload"}``."""
    payload = notification.payload
    if hasattr(payload, "model_dump"):
        data = payload.model_dump(mode="json", by_alias=True)
    elif isinstance(getattr(payload, "params", None), dict):
        # A notification the SDK has no model for, or could not parse (a hook status it
        # does not list, say), carries what the server sent under ``params``. It is
        # read all the same: an unparsed hook run must not pass unjudged.
        data = jsonable(payload.params)
    else:
        data = jsonable(payload)
    if not isinstance(data, dict):
        data = {"value": data}
    return {"method": notification.method, "payload": data}


class CodexBuilder:
    """The builder session on Codex, through ``openai-codex``'s ``CodexClient`` (the
    public ``Codex`` class takes no approval handler and no raw thread parameters)."""

    provider = limits.CODEX
    wants = ("resume", "run_dir")
    needs_turn_check = True

    def __init__(self, model, effort, approver, *, cwd, resume=None, run_dir=None,
                 client_factory=None, temp_root=None, python=None, self_test=None):
        if run_dir is None:
            raise ProviderError("a Codex builder needs the run's folder, whose name is "
                                "the run id")
        self.model = model
        self.effort = effort
        self.edit_paths = list(approver.edit_paths)
        self.cwd = str(cwd)
        self.resume = resume
        self.run_dir = Path(run_dir)
        self.run_id = self.run_dir.name
        self.frozen_dir = (Path(temp_root or tempfile.gettempdir()) / FROZEN_PARENT
                           / self.run_id)
        self._client_factory = client_factory
        self._python = python or sys.executable
        self._self_test = self_test or _run_hook_once
        self._client = None
        self._thread_id = None
        self._first_prompt_sent = resume is not None
        self._failed_request = None
        # The thread id is published only once a turn has started: a thread with no
        # turn cannot be resumed, so a resume must not be given one.
        self.session_id = resume
        self.thread_model = None
        self.last_messages = []
        self.refusals = []
        # What this launch read back and relied on, for the run record (the chunk (d)
        # short plan, 11.2); set only once every check of the launch has passed.
        self.launch_record = None

    # ------------------------------------------------------------- launch

    def _open(self, overrides, handler=None):
        if self._client_factory is not None:
            return self._client_factory(tuple(overrides), handler)
        from openai_codex.client import CodexClient, CodexConfig
        client = CodexClient(CodexConfig(cwd=self.cwd, config_overrides=tuple(overrides)),
                             approval_handler=handler)
        client.start()
        try:
            client.initialize()
        except BaseException:
            client.close()
            raise
        return client

    def _freeze(self):
        """Step 1: the frozen folder. The orchestrator makes it for every run before
        any session starts (orchestrator isolation plan 6 item 1); a launch, first or
        resumed, only checks it holds every file the hook and the rules need, and
        raises naming what is missing."""
        folder = self.frozen_dir
        if not folder.is_dir():
            raise ProviderError(f"the run's frozen folder {folder.as_posix()} is missing, "
                                "so the rules this run started with are gone; start the "
                                "run again from its brief")
        names = (*codex_rules.FROZEN_MODULES, *codex_rules.FROZEN_LISTS,
                 codex_rules.HOOK_CONFIG)
        missing = [name for name in names if not (folder / name).is_file()]
        if missing:
            raise ProviderError(f"the run's frozen folder {folder.as_posix()} lacks "
                                + ", ".join(missing))

    def _hook_command(self):
        """Step 2: the command Codex runs, refused if it would need quoting."""
        python = self._python.replace("\\", "/")
        script = (self.frozen_dir / "codex_hook.py").as_posix()
        for path in (python, script):
            if any(character in path for character in (" ", '"', "'", "\\")):
                raise ProviderError(f"the hook command cannot be written safely: {path!r} "
                                    "holds a space, a quote or a backslash, and quoting "
                                    "rules for a hook command are not modelled")
        return python, script, f"{python} {script} {self.run_id}"

    def _check_hook_answers(self, python, script):
        """Step 3: the hook must refuse an unlisted command before Codex is started. A
        hook that cannot answer lets calls through, so it is shown to answer first."""
        call_id = f"self-test-{uuid.uuid4().hex}"
        event = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                 "tool_input": {"command": "orchestrator-hook-self-test"},
                 "tool_use_id": call_id, "turn_id": "self-test",
                 "session_id": self.resume or "self-test", "cwd": self.cwd}
        try:
            code, out = self._self_test(self._python, script, self.run_id, event,
                                        HOOK_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            code, out = "timeout", ""
        except OSError as exc:
            code, out = f"could not start ({exc})", ""
        finally:
            self._drop_decisions(call_id)
        try:
            answer = json.loads(out)["hookSpecificOutput"]["permissionDecision"]
        except (ValueError, KeyError, TypeError):
            answer = None
        if code != 0 or answer != "deny":
            raise ProviderError("the run's hook did not refuse an unlisted command in "
                                f"its self-test (exit {code}, output {out[:200]!r}), so "
                                "it cannot be relied on to hold the builder")

    def _drop_decisions(self, call_id):
        """Remove the self-test's line from the decisions file."""
        path = self.frozen_dir / codex_rules.DECISIONS_FILE
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        kept = []
        for line in lines:
            try:
                if json.loads(line).get("tool_use_id") == call_id:
                    continue
            except (ValueError, AttributeError):
                pass
            kept.append(line)
        path.write_text("".join(line + "\n" for line in kept), encoding="utf-8",
                        newline="\n")

    @staticmethod
    def _hooks(reply):
        return [hook for entry in (reply or {}).get("data", [])
                for hook in entry.get("hooks", [])]

    def _run_hooks(self, hooks):
        return [h for h in hooks
                if h.get("source") == "sessionFlags" and h.get("eventName") == "preToolUse"]

    def _launch_problems(self, hooks, config):
        """Step 6: why the session may not do builder work, as a list."""
        problems = []
        own = self._run_hooks(hooks)
        if len(own) != 1:
            problems.append(f"Codex lists {len(own)} run-supplied preToolUse hooks, not 1")
        else:
            hook = own[0]
            if hook.get("trustStatus") != "trusted" or hook.get("enabled") is not True:
                problems.append("the run's hook is not trusted and enabled (trust "
                                f"{hook.get('trustStatus')!r}, enabled "
                                f"{hook.get('enabled')!r}), so Codex would not run it")
            # The hook must apply to every tool, as launched (code review finding R4-1):
            # a narrower matcher would let a tool outside it run with no hook decision.
            if hook.get("matcher") != HOOK_MATCHER:
                problems.append(f"the run's hook applies to {hook.get('matcher')!r}, not "
                                f"to every tool ({HOOK_MATCHER!r})")
            if hook.get("timeoutSec") != HOOK_TIMEOUT_SECONDS:
                problems.append(f"the run's hook has a time limit of "
                                f"{hook.get('timeoutSec')!r} seconds, not "
                                f"{HOOK_TIMEOUT_SECONDS}")
        project = [h for h in hooks if h.get("source") == "project"]
        for hook in project:
            if hook.get("trustStatus") != "trusted" or hook.get("enabled") is not True:
                problems.append(f"the project's {hook.get('eventName')} hook "
                                f"({hook.get('matcher')!r}) is not trusted and enabled, "
                                "so the project's guardrails are not in force")
        for matcher in ("Bash", "apply_patch"):
            if not any(h.get("eventName") == "preToolUse" and h.get("matcher") == matcher
                       for h in project):
                problems.append(f"the project's preToolUse hook for {matcher} is not "
                                "listed, so the project's guardrails are not in force")
        search = ((config or {}).get("config") or {}).get("web_search")
        if search != "disabled":
            problems.append(f"web search is not switched off (web_search reads "
                            f"{search!r}), and a web search needs no approval")
        return problems

    def _thread_reply_problems(self, reply):
        problems = []
        if not isinstance(reply, dict) or reply.get("sandbox") != CODEX_SANDBOX_REPORTED:
            problems.append("the sandbox Codex reports is not the one asked for: "
                            f"{(reply or {}).get('sandbox') if isinstance(reply, dict) else reply!r}")
        if not isinstance(reply, dict) or reply.get("approvalPolicy") != CODEX_APPROVAL_POLICY:
            problems.append("the approval policy Codex reports is not "
                            f"{CODEX_APPROVAL_POLICY!r}")
        # A model is never substituted (code review finding R1-3): the thread runs only
        # on the model asked for, and a reply naming another, or none, is refused.
        if not isinstance(reply, dict) or reply.get("model") != self.model:
            problems.append(f"the model Codex reports is not {self.model!r}: "
                            f"{(reply or {}).get('model') if isinstance(reply, dict) else None!r}")
        return problems

    def _launch_record(self, hooks, config, reply):
        """The readings this launch passed on, in the form the run record keeps (the
        chunk (d) short plan, 11.2, for live checks 1 and 3). Every value is what Codex
        reported; a launch whose checks failed raises and is never recorded."""
        own = self._run_hooks(hooks)[0]
        return {
            "hook": {key: own.get(key) for key in ("trustStatus", "enabled", "timeoutSec",
                                                   "matcher")},
            "project_hooks": [{key: hook.get(key) for key in
                               ("eventName", "matcher", "trustStatus", "enabled")}
                              for hook in hooks if hook.get("source") == "project"],
            "self_test": "passed",
            "web_search": ((config or {}).get("config") or {}).get("web_search"),
            "thread": {"method": "resume" if self.resume is not None else "start",
                       "id": self._thread_id, "sandbox": reply.get("sandbox"),
                       "approvalPolicy": reply.get("approvalPolicy")},
        }

    async def start(self):
        self._freeze()
        try:
            python, script, command = self._hook_command()
            self._check_hook_answers(python, script)
            hook_override = (f'hooks.PreToolUse=[{{matcher="{HOOK_MATCHER}", '
                             'hooks=[{type="command", '
                             f'command="{command}", timeout={HOOK_TIMEOUT_SECONDS}}}]}}]')
            base = (hook_override, WEB_SEARCH_OFF)
            # Step 4: ask Codex for the hook's key and fingerprint, to supply its trust.
            first = self._open(base)
            try:
                own = self._run_hooks(self._hooks(
                    first._request_raw("hooks/list", {"cwds": [self.cwd]})))
            finally:
                first.close()
            if len(own) != 1:
                raise ProviderError(f"Codex lists {len(own)} run-supplied preToolUse "
                                    "hooks, not 1, so the run's hook cannot be trusted")
            key, fingerprint = own[0].get("key"), own[0].get("currentHash")
            if not isinstance(key, str) or not isinstance(fingerprint, str) or "'" in key:
                raise ProviderError("the run's hook key cannot be written as a trust "
                                    f"override: {key!r}")
            trust = "hooks.state={'" + key + "'={trusted_hash=\"" + fingerprint + "\"}}"
            # Step 5: the session's client, with the hook, its trust and the handler.
            self._client = self._open((*base, trust), self._approve)
            # Step 6: nothing is done until the hook and the project's own are in force.
            hooks = self._hooks(self._client._request_raw("hooks/list",
                                                          {"cwds": [self.cwd]}))
            config = self._client._request_raw("config/read", {"includeLayers": False,
                                                               "cwd": self.cwd})
            problems = self._launch_problems(hooks, config)
            if problems:
                raise ProviderError("the Codex builder session does no work: "
                                    + "; ".join(problems))
            available = _model_ids(self._client.model_list())
            if self.model not in available:
                raise ProviderError(f"Codex has no model {self.model!r}; a model is never "
                                    f"substituted (available: {', '.join(available)})")
            params = {"cwd": self.cwd, "model": self.model,
                      "approvalPolicy": CODEX_APPROVAL_POLICY, "sandbox": "workspace-write",
                      "config": CODEX_SANDBOX_CONFIG}
            if self.resume is not None:
                reply = self._client._request_raw("thread/resume",
                                                  {"threadId": self.resume, **params})
            else:
                reply = self._client._request_raw("thread/start", params)
            problems = self._thread_reply_problems(reply)
            if problems:
                raise ProviderError("the Codex builder thread is not as asked for, so no "
                                    "turn runs on it: " + "; ".join(problems))
            self._thread_id = self.resume or reply["thread"]["id"]
            self.thread_model = reply.get("model")
            self.launch_record = self._launch_record(hooks, config, reply)
        except BaseException:
            # The frozen folder belongs to the run, not to this launch: it stays, and
            # the next launch checks the same folder (plan 6 item 1).
            self._close_client()
            raise

    # ------------------------------------------------------- the two layers

    def _decisions(self):
        return codex_rules.read_decisions(self.frozen_dir / codex_rules.DECISIONS_FILE)

    def _approve(self, method, params):
        """The approval handler: accept only what the hook allowed."""
        reply, refusal, failed = codex_rules.approval_reply(method, params,
                                                            self._decisions())
        if refusal is not None:
            self.refusals.append(refusal)
        if failed and self._failed_request is None:
            self._failed_request = method
        return reply

    def _hook_problem(self, payload):
        """Why a completed preToolUse hook run ends the turn, or None (short plan 3.3)."""
        run = payload.get("run") if isinstance(payload, dict) else None
        if not isinstance(run, dict) or run.get("eventName") != "preToolUse":
            return None
        status, source, run_id = run.get("status"), run.get("source"), str(run.get("id"))
        if source != "sessionFlags":
            # A project rule refusing a call is that rule working.
            if status in ("completed", "blocked"):
                return None
            return f"a {source} preToolUse hook run ended {status!r} ({run_id})"
        lines = self._decisions()
        calls = {line.get("tool_use_id") for line in lines
                 if isinstance(line.get("tool_use_id"), str) and line.get("tool_use_id")
                 and run_id.endswith(line["tool_use_id"])}
        decisions = {codex_rules.decision_for(lines, call) for call in calls}
        wanted = {"completed": "allow", "blocked": "deny"}.get(status)
        if wanted is not None and decisions == {wanted}:
            return None
        recorded = ", ".join(sorted(str(d) for d in decisions)) or "no decision line"
        return (f"the run's hook ended {status!r} for call {run_id} with {recorded}, so "
                "the call was not judged as the hook's rules require")

    def _item_problem(self, events):
        """Why the turn's items do not reconcile with the hook's decisions, or None."""
        lines = self._decisions()
        for event in events:
            if event["method"] != "item/completed":
                continue
            item = event["payload"].get("item") or {}
            kind, item_id = item.get("type"), item.get("id")
            if kind in _CODEX_MESSAGE_ITEMS:
                continue
            if kind not in _CODEX_TOOL_ITEMS:
                return f"the turn holds an item of a kind the session does not know: {kind}"
            decision = codex_rules.decision_for(lines, item_id)
            if decision is None:
                return f"the {kind} item {item_id} has no hook decision"
            if item.get("status") != "declined" and decision != "allow":
                return f"the {kind} item {item_id} ran without the hook allowing it"
        return None

    def _after_turn(self, turn_id):
        """Copy the decisions file into the run record and collect the turn's refusals."""
        source = self.frozen_dir / codex_rules.DECISIONS_FILE
        if source.is_file():
            self.run_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, self.run_dir / codex_rules.DECISIONS_FILE)
        for line in self._decisions():
            if line.get("turn_id") == turn_id and line.get("decision") == "deny":
                self.refusals.append({"tool": line.get("tool"),
                                      "detail": line.get("detail") or "",
                                      "reason": line.get("reason") or ""})

    def _take_refusals(self):
        taken, self.refusals = self.refusals, []
        return taken

    async def turn(self, name, prompt):
        """Send one prompt and collect the thread's answer to it."""
        if self._client is None or self._thread_id is None:
            raise ProviderError("the builder session has not been started")
        if not self._first_prompt_sent:
            prompt = with_marker(prompt)
            self._first_prompt_sent = True
        started = time.time()
        client, thread_id = self._client, self._thread_id
        turn_id = client.turn_start(thread_id, prompt, {"effort": self.effort}).turn.id
        self.session_id = thread_id
        events = []
        self.last_messages = events
        problem = None
        client.register_turn_notifications(turn_id)
        try:
            while True:
                event = _note(client.next_turn_notification(turn_id))
                events.append(event)
                if self._failed_request is not None:
                    problem = ("Codex sent a request the orchestrator does not answer: "
                               f"{self._failed_request}")
                elif event["method"] == "hook/completed":
                    problem = self._hook_problem(event["payload"])
                if problem is not None or event["method"] == "turn/completed":
                    break
            if problem is not None:
                try:
                    client.turn_interrupt(thread_id, turn_id)
                except Exception as exc:  # noqa: BLE001 - the turn is ended either way
                    problem += f" (and the turn could not be interrupted: {exc})"
        finally:
            client.unregister_turn_notifications(turn_id)
            self._after_turn(turn_id)
        if problem is not None:
            raise ProviderError(f"the Codex builder turn was ended: {problem}")
        turn = events[-1]["payload"].get("turn") or {}
        status = turn.get("status")
        if status == "failed":
            hit = limits.codex_turn_error(turn.get("error"))
            if hit is not None:
                raise hit
            detail = (turn.get("error") or {}).get("message") or "no detail"
            raise ProviderError(f"the Codex builder turn failed: {detail}")
        if status != "completed":
            raise ProviderError(f"the Codex builder turn ended {status}")
        problem = self._item_problem(events)
        if problem is not None:
            raise ProviderError(f"the Codex builder turn cannot be accepted: {problem}")
        text, usage = "", None
        for event in events:
            item = event["payload"].get("item") or {}
            if event["method"] == "item/completed" and item.get("type") == "agentMessage":
                text = item.get("text") or text
            elif event["method"] == "thread/tokenUsage/updated":
                usage = event["payload"].get("tokenUsage") or usage
        return TurnRecord(name=name, final_text=text, session_id=thread_id,
                          model=self.thread_model, num_turns=None, is_error=False,
                          usage=usage, cost_usd_equivalent=None,
                          wall_seconds=round(time.time() - started, 1), messages=events,
                          tokens=codex_tokens(usage), refusals=self._take_refusals())

    def _close_client(self):
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None

    async def close(self):
        # The frozen folder is the run's; closing a session never removes it.
        self._close_client()


# ---------------------------------------------------------------------- reviewer


class CodexReviewer:
    """The reviewer session on Codex, through ``openai-codex``: a fresh read-only
    thread per round, with approvals refused."""

    provider = limits.CODEX

    def __init__(self, model, effort, *, cwd, codex_factory=None):
        self.model = model
        self.effort = effort
        self.cwd = str(cwd)
        self._codex_factory = codex_factory

    def _open(self):
        if self._codex_factory is not None:
            return self._codex_factory()
        from openai_codex import Codex, CodexConfig
        return Codex(CodexConfig(cwd=self.cwd))

    def review(self, prompt, schema):
        """Run one review round. Returns a ReviewRecord whose ``reply`` is the parsed
        JSON object; checking it against the loop-exit rules is the caller's job
        (``stopreasons.parse_review``)."""
        from openai_codex import ApprovalMode, Sandbox
        from openai_codex._run import _collect_turn_result
        started = time.time()
        with self._open() as codex:
            available = _model_ids(codex.models())
            if self.model not in available:
                raise ProviderError(f"Codex has no model {self.model!r}; a model is never "
                                    f"substituted (available: {', '.join(available)})")
            thread = codex.thread_start(cwd=self.cwd, model=self.model,
                                        sandbox=Sandbox.read_only,
                                        approval_mode=ApprovalMode.deny_all)
            # Not thread.run(): on a failed turn the SDK's collector raises a plain
            # RuntimeError carrying only the message, so a usage limit's structured
            # code is lost (review finding R2-1). The turn's events are read here
            # instead, the failed turn is judged from its own error, and a completed
            # turn goes through the SDK's own collector unchanged.
            handle = thread.turn(with_marker(prompt), effort=self.effort,
                                 output_schema=schema)
            events = _drain(handle)
            thread_id = getattr(thread, "id", None)
        failed = _failed_turn(events, handle.id)
        if failed is not None:
            hit = limits.codex_turn_error(failed.error)
            if hit is not None:
                raise hit
            detail = failed.error.message if failed.error is not None else "no detail"
            raise ProviderError(f"the Codex review turn failed: {detail}")
        try:
            result = _collect_turn_result(iter(events), turn_id=handle.id)
        except RuntimeError as exc:
            raise ProviderError(f"the Codex review turn did not complete: {exc}") from exc
        status = _status(getattr(result, "status", None))
        if status != "completed":
            raise ProviderError(f"the Codex review turn ended {status}: "
                                f"{getattr(result, 'error', None)}")
        text = getattr(result, "final_response", None)
        if not text:
            raise ProviderError("the Codex review turn gave no final response")
        try:
            reply = json.loads(text)
        except ValueError as exc:
            raise ProviderError(f"the Codex review reply is not JSON: {exc}") from exc
        usage = jsonable(getattr(result, "usage", None))
        return ReviewRecord(reply=reply, thread_id=thread_id, status=status, usage=usage,
                            wall_seconds=round(time.time() - started, 1),
                            raw=jsonable(result), tokens=codex_tokens(usage))


class ClaudeReviewer:
    """The reviewer session on Claude, through ``claude-agent-sdk``: a fresh read-only
    session per round. Read-only is enforced, not asked for: ``allowed_tools`` is Read,
    Grep and Glob, and a PreToolUse hook matching every tool denies any other call,
    since a permission callback never sees a call the project's ``permissions.allow``
    rules already permit. The one other call let through is the SDK's own
    ``StructuredOutput``, which carries the reply."""

    provider = limits.CLAUDE
    wants = ("usage",)

    def __init__(self, model, effort, *, cwd, usage=None, client_factory=None,
                 max_turns=REVIEW_MAX_TURNS):
        self.model = model
        self.effort = effort
        self.cwd = str(cwd)
        self.usage = usage if usage is not None else limits.ClaudeUsage()
        self.max_turns = max_turns
        self._client_factory = client_factory
        self.stderr = []
        self.refusals = []

    async def pre_tool_use(self, hook_input, tool_use_id, context):
        """Deny everything but the read tools and the reply. An allowed call gets no
        decision, so the project's own hooks and rules still run after this one."""
        name, tool_input = hook_input.get("tool_name"), hook_input.get("tool_input")
        if name == STRUCTURED_OUTPUT_TOOL:
            return {}
        reason = read_rules(name, tool_input) if name in REVIEWER_TOOLS else REVIEWER_REFUSAL
        if reason is None:
            return {}
        text = " ".join(str(tool_input).split())
        self.refusals.append({"tool": name, "reason": reason,
                              "detail": text if len(text) <= 300 else text[:297] + "..."})
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}}

    def options(self, schema):
        from claude_agent_sdk import ClaudeAgentOptions, HookMatcher
        hooks = {"PreToolUse": [HookMatcher(matcher=None, hooks=[self.pre_tool_use])]}
        return ClaudeAgentOptions(cwd=self.cwd, model=self.model, effort=self.effort,
                                  permission_mode="default",
                                  allowed_tools=list(REVIEWER_TOOLS), hooks=hooks,
                                  output_format={"type": "json_schema", "schema": schema},
                                  max_turns=self.max_turns, stderr=self.stderr.append)

    def review(self, prompt, schema):
        """Run one review round. The loop calls this in a worker thread, where no
        event loop is running, so the session runs its own."""
        return asyncio.run(self._review(prompt, schema))

    async def _review(self, prompt, schema):
        if self._client_factory is None:
            from claude_agent_sdk import ClaudeSDKClient
            factory = ClaudeSDKClient
        else:
            factory = self._client_factory
        started = time.time()
        client = factory(options=self.options(schema))
        await client.connect()
        messages, result, init_model = [], None, None
        try:
            await client.query(with_marker(prompt))
            async for message in client.receive_response():
                messages.append(jsonable(message))
                kind = type(message).__name__
                if kind == "RateLimitEvent":
                    self.usage.observe(message)
                elif kind == "SystemMessage" and getattr(message, "subtype", None) == "init":
                    init_model = (getattr(message, "data", None) or {}).get("model")
                    if init_model and init_model != self.model:
                        raise ProviderError(f"Claude started on {init_model!r}, not the "
                                            f"requested {self.model!r}; a model is never "
                                            "substituted")
                elif kind == "AssistantMessage":
                    hit = limits.claude_message_error(message)
                    if hit is not None:
                        raise hit
                elif kind == "ResultMessage":
                    result = message
        finally:
            await client.disconnect()
        if result is None:
            raise ProviderError("the Claude review session ended without a result")
        if result.is_error:
            detail = "; ".join(result.errors or []) or result.result or "no detail"
            raise ProviderError(f"the Claude review ended in error ({result.subtype}): "
                                f"{detail}")
        reply = getattr(result, "structured_output", None)
        if not reply:
            raise ProviderError("the Claude review gave no structured reply")
        return ReviewRecord(reply=reply, thread_id=result.session_id, status="completed",
                            usage=result.usage,
                            wall_seconds=round(time.time() - started, 1), raw=messages,
                            tokens=claude_tokens(result.usage), model=init_model,
                            refusals=list(self.refusals))


def _model_ids(listing):
    return [model.id for model in getattr(listing, "data", listing)]


def _drain(handle):
    """Every event of one Codex turn, in order; the stream ends with the turn."""
    stream = handle.stream()
    try:
        return list(stream)
    finally:
        stream.close()


def _failed_turn(events, turn_id):
    """The turn from this turn's completed event if it failed, else None."""
    for event in events:
        payload = getattr(event, "payload", None)
        turn = getattr(payload, "turn", None)
        if (type(payload).__name__ == "TurnCompletedNotification"
                and getattr(turn, "id", None) == turn_id
                and _status(getattr(turn, "status", None)) == "failed"):
            return turn
    return None


def _status(status):
    status = getattr(status, "value", status)
    return status if isinstance(status, str) else repr(status)


# ---------------------------------------------------------------------- registry

BUILDERS = {limits.CLAUDE: ClaudeBuilder, limits.CODEX: CodexBuilder}
REVIEWERS = {limits.CODEX: CodexReviewer, limits.CLAUDE: ClaudeReviewer}


def _wanted(cls, given):
    """Of what the loop offers a session, the parts this session takes (its ``wants``)."""
    return {name: value for name, value in given.items()
            if name in getattr(cls, "wants", ())}


def make_builder(settings, approver, *, cwd, resume=None, run_dir=None, usage=None,
                 **kwargs):
    """The builder session for the provider ``settings["roles"]["builder"]`` names.
    ``run_dir`` (the run's folder, whose name is the run id) and ``usage`` (the run's
    ``limits.ClaudeUsage``) are passed to the session that takes them."""
    provider = settings["roles"]["builder"]
    cls = BUILDERS.get(provider)
    if cls is None:
        raise ProviderError(f"no builder session exists for {provider}; only "
                            f"{', '.join(BUILDERS)} can build")
    entry = model_for(settings, provider, "builder")
    offered = {"resume": resume, "run_dir": run_dir, "usage": usage}
    return cls(entry["model"], entry["effort"], approver, cwd=cwd,
               **_wanted(cls, offered), **kwargs)


def make_reviewer(settings, *, cwd, usage=None, **kwargs):
    """The reviewer session for the provider ``settings["roles"]["reviewer"]`` names.
    ``usage`` is the run's ``limits.ClaudeUsage``, given to a Claude reviewer."""
    provider = settings["roles"]["reviewer"]
    cls = REVIEWERS.get(provider)
    if cls is None:
        raise ProviderError(f"no reviewer session exists for {provider}; only "
                            f"{', '.join(REVIEWERS)} can review")
    entry = model_for(settings, provider, "reviewer")
    return cls(entry["model"], entry["effort"], cwd=cwd,
               **_wanted(cls, {"usage": usage}), **kwargs)
