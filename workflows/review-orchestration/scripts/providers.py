#!/usr/bin/env python3
"""
providers.py - the builder and reviewer sessions a run drives (Stage A2).

Roles are named by what they do, not by who does them. A run asks for a builder
session and a reviewer session (``make_builder``, ``make_reviewer``), and the
settings file's ``roles`` says which provider holds each. Today one pairing is built:

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

Asking for the other pairing (Codex builds, Claude reviews) raises ``ProviderError``
naming what is missing, rather than quietly running the default pairing. Adding it is
two classes registered in ``BUILDERS`` and ``REVIEWERS``; the loop does not change.

Both sessions start with the orchestrated-session marker as the first line of their
first prompt, so the session skips the AGENTS.md startup sequence (which would
otherwise acknowledge the user's handoff and memory watermarks). A requested model
the provider does not have is an error, never a substitution (plan section 3).

The SDKs are imported inside the methods that need them, and every SDK entry point
can be replaced by a factory argument, so the tests drive both sessions with fakes
and no model call.
"""

import dataclasses
import json
import time

import limits
from settings import model_for

MARKER = "Orchestrated session: skip the AGENTS.md session-startup sequence."

EDIT_MAX_TURNS = 80


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


@dataclasses.dataclass
class ReviewRecord:
    """One review round's session, for the run record and report."""

    reply: dict
    thread_id: str | None
    status: str | None
    usage: object
    wall_seconds: float
    raw: object


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
                          wall_seconds=round(time.time() - started, 1), messages=messages)

    async def close(self):
        if self._client is not None:
            await self._client.disconnect()
            self._client = None


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
        return ReviewRecord(reply=reply, thread_id=thread_id, status=status,
                            usage=jsonable(getattr(result, "usage", None)),
                            wall_seconds=round(time.time() - started, 1),
                            raw=jsonable(result))


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

BUILDERS = {limits.CLAUDE: ClaudeBuilder}
REVIEWERS = {limits.CODEX: CodexReviewer}


def make_builder(settings, approver, *, cwd, **kwargs):
    """The builder session for the provider ``settings["roles"]["builder"]`` names."""
    provider = settings["roles"]["builder"]
    cls = BUILDERS.get(provider)
    if cls is None:
        raise ProviderError(f"no builder session exists for {provider} yet; only "
                            f"{', '.join(BUILDERS)} can build")
    entry = model_for(settings, provider, "builder")
    return cls(entry["model"], entry["effort"], approver, cwd=cwd, **kwargs)


def make_reviewer(settings, *, cwd, **kwargs):
    """The reviewer session for the provider ``settings["roles"]["reviewer"]`` names."""
    provider = settings["roles"]["reviewer"]
    cls = REVIEWERS.get(provider)
    if cls is None:
        raise ProviderError(f"no reviewer session exists for {provider} yet; only "
                            f"{', '.join(REVIEWERS)} can review")
    entry = model_for(settings, provider, "reviewer")
    return cls(entry["model"], entry["effort"], cwd=cwd, **kwargs)
