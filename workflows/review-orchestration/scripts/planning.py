#!/usr/bin/env python3
"""
planning.py - the planning half (plan 10.3 steps 1 to 4 and 6; A3 chunks 2 and 3).

Every run plans before it builds: since A3 chunk 3 ``start.py --run`` begins here and
builds only after ``--approve``. Its steps, each recorded in ``state.json`` under
``planning`` once it has completed:

  planners   the before-step usage check of plan 10.10 for both planners, one after
             the other, then two read-only planner sessions, one per provider, started
             in parallel from the same prompt: the whole brief, the two starting
             commits and an instruction to read the project, and no file content. Each
             reply must match ``PLANNER_SCHEMA`` exactly. A plan holding any open
             question pauses the run ``awaiting-user`` (step 2); the script matches
             nothing itself.
  identity   every string value of each plan is checked for text that identifies its
             provider (step 3), with real project paths masked for the check only. A
             plan with any match is refused, never scrubbed: the run ends ``error`` with
             ``identity_refusal`` in the report.
  synthesis  the synthesiser, on the builder's provider and model, receives the brief
             and both plans as Plan 1 and Plan 2, in an order drawn at random and
             recorded, with no provider names, and replies in ``SYNTHESIS_SCHEMA``.

  plan-review-R<n>  the plan reviewer (step 4, A3 chunk 3), on the provider that is not
             the builder's and its ``plan_reviewer`` entry, reviews the merged plan
             against the brief in the packet's one label form. Its findings go into the
             run's one ledger, labelled ``R<n>-<k>``; the labels run on into the build.
  plan-revise-R<n>  the synthesiser revises: the synthesis's own fields revised, plus
             the 10.5 action list, every open finding exactly once, each ``fixed`` or
             ``declined-merits`` with a note (``stopreasons.parse_plan_actions``).

The plan review loops, the 10.5 loop-exit rules applying with the synthesiser in the
builder's place, until a round finds nothing, when the run writes ``plan-report.md``
and pauses ``awaiting-plan-approval`` (step 6), or until the plan-review round cap
(``round_caps.plan_review``), when it ends ``max-rounds``; ``--resume --rounds N`` then
keeps both plans and the synthesis, the synthesiser acts on the open findings, and up
to N more rounds follow. An amendment restarts at step 1 with the round count reset,
its labels carrying on. The intent check (step 5) is A4's, so the report says no
intent check has run.

Nothing here writes outside the run's own record, ``runs/<run-id>/`` (step 6, "The run
writes nothing in the project before approval"): no LOG.md entry at a pause, at an end
that can be resumed or at a resume, and no packet in ``memory/``. Plan-review findings
are rendered to ``runs/<run-id>/plan-review/packet.md`` until approval writes the item's
packet. A stop resumes from the last planning step completed, never mid-step, and the
two planners always run again together.

Every session is a read-only session of ``providers.py`` started with the reviewers'
lockdown (step 7; process table rows 16 to 18), reached through ``loop.Deps`` so the
tests drive the whole half with fakes and no model call.
"""

import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import brief as brief_mod
import limits
import loop
import providers
import stopreasons as sr
import worktree

PROVIDERS = (limits.CLAUDE, limits.CODEX)
IDENTIFIERS_PATH = (Path(__file__).resolve().parent.parent / "config"
                    / "provider-identifiers.txt")
SECTIONS = ("names", "distinctive", "common", "prefixes")

PLANNERS, IDENTITY, SYNTHESIS = "planners", "identity", "synthesis"
STEPS = (PLANNERS, IDENTITY, SYNTHESIS)
# The plan review's two kinds of step (A3 chunk 3), and the run's next move once every
# step above is done: a review round, the synthesiser's revision, or the pause.
REVIEW, REVISE, PAUSE = "review", "revise", "pause"
LABELS = ("Plan 1", "Plan 2")
QUESTIONS_FILE = "questions.md"
PLAN_REPORT = "plan-report.md"
PLAN_REVIEW_PACKET = Path("plan-review") / "packet.md"

# Plan 10.3 step 1: the planner's reply. Every field required and nothing else
# allowed, enforced as the reviewer's reply schema is (``check_reply``).
_TEXT = {"type": "string"}
_TEXTS = {"type": "array", "items": _TEXT}
_STEP = {"type": "object", "additionalProperties": False,
         "required": ["action", "files", "verification"],
         "properties": {"action": _TEXT, "files": _TEXTS, "verification": _TEXT}}
_CONFIDENCE = {"type": "string", "enum": ["low", "medium", "high"]}
PLANNER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "steps", "risks", "assumptions", "open_questions",
                 "confidence"],
    "properties": {"summary": _TEXT, "steps": {"type": "array", "items": _STEP},
                   "risks": _TEXTS, "assumptions": _TEXTS, "open_questions": _TEXTS,
                   "confidence": _CONFIDENCE}}
# The merged plan: the planner schema without ``open_questions`` (the user's option A,
# 2026-10-15), since every question is settled before synthesis.
MERGED_PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": [name for name in PLANNER_SCHEMA["required"] if name != "open_questions"],
    "properties": {name: value for name, value in PLANNER_SCHEMA["properties"].items()
                   if name != "open_questions"}}
SYNTHESIS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["agreements", "disagreements", "resolutions", "retained_risks",
                 "merged_plan"],
    "properties": {
        "agreements": _TEXTS, "disagreements": _TEXTS, "retained_risks": _TEXTS,
        "resolutions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["disagreement", "resolution", "reason"],
            "properties": {"disagreement": _TEXT, "resolution": _TEXT,
                           "reason": _TEXT}}},
        "merged_plan": MERGED_PLAN_SCHEMA}}
# The synthesiser's revision reply (plan 10.3 step 4): the synthesis's own fields,
# revised, plus the 10.5 action list. The schema names the two actions allowed; the
# script's own check (``stopreasons.parse_plan_actions``) holds whatever a provider did,
# and names a ``declined-scope`` as having no meaning before a build.
_ACTION = {"type": "object", "additionalProperties": False,
           "required": ["label", "action", "note"],
           "properties": {"label": _TEXT, "note": _TEXT,
                          "action": {"type": "string", "enum": list(sr.PLAN_ACTIONS)}}}
REVISION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": [*SYNTHESIS_SCHEMA["required"], "actions"],
    "properties": {**SYNTHESIS_SCHEMA["properties"],
                   "actions": {"type": "array", "items": _ACTION}}}

# The tool-metadata rule (step 3), a whole-word match on each, with case.
TOOL_METADATA = ("session_id", "sessionId", "thread_id", "threadId", "turn_id", "turnId",
                 "tool_use", "tool_use_id", "tool_result", "call_id")
# A session ID shorter than this is not matched: real ones are long, and a short one
# would match ordinary words.
MIN_SESSION_ID = 8
_UUID = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
                   r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}(?![0-9A-Fa-f])")
_METADATA_PATH = re.compile(r"~[/\\]\.codex(?!\w)|(?<![\w.])\.(?:claude|codex)[/\\]",
                            re.IGNORECASE)
# A path-shaped token for the mask: a run of letters, digits and `_ . / -`.
_TOKEN = re.compile(r"[A-Za-z0-9_./-]+")
_NEVER_MASKED = (".claude", ".codex")
_SPAN = re.compile(r"`([^`\n]*)`")


class PlanningError(Exception):
    """The planning half cannot go on; the run ends ``error`` naming why."""


class IdentifierError(Exception):
    """``config/provider-identifiers.txt`` is missing, unreadable or malformed."""


# ------------------------------------------------------------------ the identifiers


def load_identifiers(path=None):
    """The four lists of ``provider-identifiers.txt``. Blank lines and lines starting
    ``#`` are ignored; a line outside a section, a section named otherwise, a section
    given twice, an identifier holding whitespace, and a section missing or empty each
    raise ``IdentifierError`` naming the line or the section."""
    path = IDENTIFIERS_PATH if path is None else path
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise IdentifierError(f"{Path(path).name} cannot be read: {exc}") from exc
    found, section = {}, None
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        heading = re.fullmatch(r"\[(.*)\]", line)
        if heading:
            section = heading.group(1).strip()
            if section not in SECTIONS:
                raise IdentifierError(f"line {no}: section [{section}] is not one of "
                                      + ", ".join(f"[{s}]" for s in SECTIONS))
            if section in found:
                raise IdentifierError(f"line {no}: section [{section}] is given twice")
            found[section] = []
            continue
        if section is None:
            raise IdentifierError(f"line {no}: {line!r} is outside a section")
        if any(char.isspace() for char in line):
            raise IdentifierError(f"line {no}: {line!r} holds whitespace; one "
                                  "identifier per line")
        found[section].append(line)
    for name in SECTIONS:
        if not found.get(name):
            raise IdentifierError(f"section [{name}] is missing or empty")
    return found


def preflight(path=None):
    """The planning half's preflight refusals, as a list (empty when it may start):
    a malformed identifiers file refuses the run before any session (step 3)."""
    try:
        load_identifiers(path)
    except IdentifierError as exc:
        return [f"config/provider-identifiers.txt: {exc}"]
    return []


def model_names(settings):
    """Every model name in the settings, for both providers and every role, the usage
    probe's included (step 3's names rule)."""
    names = {entry["model"] for roles in settings.get("models", {}).values()
             for entry in roles.values() if isinstance(entry, dict) and entry.get("model")}
    if settings.get("usage_probe_model"):
        names.add(settings["usage_probe_model"])
    return sorted(names)


def session_ids(state):
    """Every session ID the run has recorded: any string under a ``session_id``,
    ``thread_id``, ``thread`` or ``builder_session`` key anywhere in ``state.json``, and
    the planning half's own sessions."""
    found = set()

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("session_id", "thread_id", "thread", "builder_session") \
                        and isinstance(item, str):
                    found.add(item)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(state)
    found |= {v for v in ((state.get("planning") or {}).get("sessions") or {}).values()
              if isinstance(v, str)}
    return sorted(i for i in found if len(i) >= MIN_SESSION_ID)


# ---------------------------------------------------------------------- the mask


class KnownPaths:
    """What the mask counts as a real project path: every file of the public and
    personal trees at the run's starting commits, every folder above one, the file
    names, and the brief's edit paths."""

    def __init__(self, files, edit_paths=()):
        self.files = {f for f in files if f}
        self.folders = {"/".join(f.split("/")[:n]) for f in self.files
                        for n in range(1, f.count("/") + 1)}
        self.names = {f.rsplit("/", 1)[-1] for f in self.files}
        self.edit_paths = list(edit_paths)

    def maskable(self, token):
        """Whether a path-shaped token (its trailing full stops already dropped) is a
        real project path, and so left out of the check."""
        if "/" not in token:
            return token in self.names
        path = token[2:] if token.startswith("./") else token
        path = path.rstrip("/")
        parts = [p.lower() for p in path.split("/")]
        if any(part in _NEVER_MASKED for part in parts):
            return False
        if path in self.files or path in self.folders:
            return True
        return bool(path) and worktree.under_edit_paths(path, self.edit_paths)


def mask(text, known):
    """``text`` with every real project path replaced by spaces of the same length, so
    positions and line numbers are kept. For the check only."""
    def replace(match):
        token = match.group(0)
        core = token.rstrip(".,")
        if core and known.maskable(core):
            return " " * len(core) + token[len(core):]
        return token
    return _TOKEN.sub(replace, text)


# ------------------------------------------------------------- the identity check


def _word(name, flags=0):
    return re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", flags)


def identity_rules(identifiers, models=(), sessions=()):
    """The compiled rules of step 3: ``(rule, kind, pattern)`` triples, where ``kind``
    is ``anywhere`` or ``as-tool`` (the common-word names)."""
    rules = [("names", "anywhere", _word(name, re.IGNORECASE))
             for name in [*identifiers["names"], *models]]
    rules += [("tool-names", "anywhere", _word(name))
              for name in identifiers["distinctive"]]
    rules += [("tool-names", "anywhere", re.compile(r"(?<!\w)" + re.escape(prefix)
                                                    + r"(?:\w|[.-](?=\w))*"))
              for prefix in identifiers["prefixes"]]
    rules += [("tool-names", "as-tool", _word(name)) for name in identifiers["common"]]
    rules += [("session-metadata", "anywhere", _word(i)) for i in sessions]
    rules += [("session-metadata", "anywhere", _UUID),
              ("session-metadata", "anywhere", _METADATA_PATH)]
    rules += [("tool-metadata", "anywhere", _word(name)) for name in TOOL_METADATA]
    return rules


def _strings(value, where=""):
    """Every string value in a reply, with the field it is in."""
    if isinstance(value, str):
        yield where or "(reply)", value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(item, f"{where}.{key}" if where else key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _strings(item, f"{where}[{index}]")


def check_identity(reply, rules, known):
    """Every match of the rules in every string value of a reply, as a list of
    ``{"rule", "text", "field", "line"}``, ``line`` counted within that value. Matching
    runs on the masked text; the matched text is read from the original."""
    found = []
    for field, original in _strings(reply):
        masked = mask(original, known)
        spans = []
        for rule, kind, pattern in rules:
            if kind == "anywhere":
                spans += [(rule, m.start(), m.end()) for m in pattern.finditer(masked)]
                continue
            # A common word written as a tool: inside a backtick span on one line, or
            # immediately followed by `(`.
            for span in _SPAN.finditer(masked):
                inner = span.group(1)
                spans += [(rule, span.start(1) + m.start(), span.start(1) + m.end())
                          for m in pattern.finditer(inner)]
            spans += [(rule, m.start(), m.end()) for m in pattern.finditer(masked)
                      if masked[m.end():m.end() + 1] == "("]
        seen = set()
        for rule, start, end in sorted(spans, key=lambda s: (s[1], s[2], s[0])):
            if (rule, start, end) in seen:
                continue
            seen.add((rule, start, end))
            found.append({"rule": rule, "text": original[start:end], "field": field,
                          "line": original.count("\n", 0, start) + 1})
    return found


# ------------------------------------------------------------------ the replies


def check_reply(value, schema, who, where="the reply"):
    """Check a reply against one of this module's schemas: every field required, no
    other field, every type and enum as given. Raises ``stopreasons.ReplyError``."""
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, dict):
            raise sr.ReplyError(f"{who}: {where} is not a JSON object")
        if set(value) != set(schema["required"]):
            raise sr.ReplyError(f"{who}: {where} must hold exactly: "
                                + ", ".join(schema["required"]))
        for name in schema["required"]:
            check_reply(value[name], schema["properties"][name], who, f"{where}.{name}")
    elif kind == "array":
        if not isinstance(value, list):
            raise sr.ReplyError(f"{who}: {where} is not a list")
        for index, item in enumerate(value):
            check_reply(item, schema["items"], who, f"{where}[{index}]")
    elif kind == "string":
        if not isinstance(value, str):
            raise sr.ReplyError(f"{who}: {where} is not text")
        if "enum" in schema and value not in schema["enum"]:
            raise sr.ReplyError(f"{who}: {where} is {value!r}, not one of "
                                + ", ".join(schema["enum"]))
    else:  # pragma: no cover - the schemas above use no other type
        raise sr.ReplyError(f"{who}: {where} has an unknown schema type {kind}")


# ----------------------------------------------------------------- the run's state


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def planning_state(run):
    return run.state.setdefault("planning", {"completed": []})


def in_planning(run):
    """Whether a run is in its planning half: it has a planning record and has not been
    approved. A resume of such a run goes to ``resume`` here, never ``loop.resume``."""
    state = getattr(run, "state", None)
    return (isinstance(state, dict) and isinstance(state.get("planning"), dict)
            and not state.get("approval"))


def end(run, reason, evidence=None):
    """End a run in its planning half. ``error``, ``usage-limit`` and ``max-rounds`` can
    be resumed, so the end writes no LOG.md entry (step 6, R20-2): the stop reason goes
    to ``state.json`` and the report only. Nothing is written in ``memory/``. A paid-usage
    permission belongs to one attempt, so it is cleared before the end is saved, as the
    build's step does."""
    run.state.pop("paid_usage_pending", None)
    run.claude_usage.paid_allowed = False
    sr.record_stop(run.state, reason, evidence)
    try:
        run.save()
        loop.write_report(run)
    finally:
        run._after_end(reason)
    if reason not in sr.RESUMABLE:
        # An end no approval can follow (``disagreement`` or ``reopened`` in the plan
        # review) writes its entry, as ``stopped-by-user`` does (step 6, R20-2); the
        # packet in memory/ is still never written.
        loop._log("completed", f"Run {run.state['run_id']} ended {reason} after step "
                               f"{run.state['step']}, before approval.", run.log_path)


def _complete(run, step):
    planning = planning_state(run)
    if step not in planning["completed"]:
        planning["completed"].append(step)
    run.state["step"] = f"plan-{step}"
    run.state.pop("interrupted", None)
    run.save()


def _record_session(run, step, role, provider, session, record):
    """A planning session's step record, transcript, refusals and launch readings."""
    state = run.state
    run._record_model(provider, record.model)
    for refusal in record.refusals or ():
        state.setdefault("refusals", []).append({"step": step, **refusal})
    if record.launch is not None:
        state.setdefault("live", {}).setdefault(f"codex_{role}_launches", []).append(
            {"step": step, **record.launch})
    state.setdefault("steps", []).append(
        {"step": step, "role": role, "provider": provider,
         "model": getattr(session, "model", None), "tokens": record.tokens,
         "uncached_tokens": providers.uncached_tokens(provider, record.usage),
         "usage": record.usage, "wall_seconds": record.wall_seconds,
         "thread_id": record.thread_id})
    loop._write_json(run.run_dir / "transcripts" / f"{step}.json",
                     {"reply": record.reply, "raw": record.raw})


def _usage_evidence(exc, step):
    return {"step": step, "provider": exc.provider, "detail": exc.detail,
            "resets_at": exc.resets_at, "paid_usage": "paid overage" in exc.detail}


def _permitted(run, group):
    """Whether the paid-usage permission pending covers this planning step (plan 10.10
    item 8); the two planners are one step, ``planners``. A Claude session of a
    permitted step is not interrupted on paid overage (item 7's in-step watch)."""
    permitted = loop._permits(run.state.get("paid_usage_pending"), group)
    run.claude_usage.paid_allowed = permitted
    return permitted


def _group_providers(run, group):
    """The provider or providers a planning step uses: both for the planners, the
    reviewer's for a plan review, the builder's (the synthesiser's) otherwise."""
    roles = run.settings["roles"]
    if group == PLANNERS:
        return PROVIDERS
    if group.startswith("plan-review"):
        return (roles["reviewer"],)
    return (roles["builder"],)


def _finish(run, group, permitted):
    """After a planning session step, completed or not: the permission is spent, the
    unknown-reset allowance of a plain resume is used, and after a permitted step that
    left the run running a fresh Codex credit snapshot is what later steps compare with,
    as after a permitted build step (``loop.Run.take``)."""
    run.state.pop("paid_usage_pending", None)
    # A plain resume's unknown-reset allowance is the first Claude step's (plan 10.10
    # item 7): a plan review on Codex keeps it for the Claude step after (R3-2).
    if limits.CLAUDE in _group_providers(run, group):
        run.state["claude_unknown_reset_resume"] = False
    run.claude_usage.paid_allowed = False
    if permitted and run.state.get("status") == sr.RUNNING:
        try:
            fresh = run.note_codex(run.deps.read_codex(run.root,
                                                       run.state.get("frozen_dir")))
            run.state["credits"] = loop.credit_snapshot(fresh)
        except Exception as exc:  # noqa: BLE001 - a later step stops on it
            run.state["credits"] = None
            run.state.setdefault("paid_usage_notes", []).append(
                f"credit reading after {group} failed: {type(exc).__name__}: {exc}")
    if run.state.get("status") != sr.ENDED:
        run.save()


async def _check_usage(run, step, provider):
    """The before-step usage check (plan 10.10), against the one provider the step
    uses. Returns None, or ends the run and returns True."""
    try:
        stop = await asyncio.to_thread(run.before_step, step, provider)
    except Exception as exc:  # the reading itself failed: nothing started
        end(run, sr.ERROR, {"step": step, "error": "the usage reading before the step "
                            f"failed: {type(exc).__name__}: {exc}"})
        return True
    if stop is not None:
        end(run, sr.USAGE_LIMIT, stop)
        return True
    return None


# ------------------------------------------------------------------- the planners


def planner_prompt(run):
    """The one prompt both planners are given: the whole brief and the two starting
    commits, no file content and nothing of the other planner (step 1)."""
    start = run.state.get("start") or {}
    return loop.render("planner", brief=run.brief_text(),
                       public_head=start.get("public_head") or "(none recorded)",
                       personal_head=start.get("personal_head") or "(none recorded)")


async def planners(run):
    """Step 1: the usage check for both planners, one after the other, each against its
    own provider, then both sessions in parallel from the same prompt; then step 2's
    pause on any question. The two count as one step for a paid-usage permission."""
    permitted = _permitted(run, PLANNERS)
    try:
        await _planners(run)
    finally:
        _finish(run, PLANNERS, permitted)


async def _planners(run):
    deps, state = run.deps, run.state
    for provider in PROVIDERS:
        if await _check_usage(run, f"planner-{provider}", provider):
            return
    prompt = planner_prompt(run)
    try:
        sessions = [deps.make_planner(run.settings, provider, run.root, run.claude_usage,
                                      state["frozen_dir"]) for provider in PROVIDERS]
    except Exception as exc:  # noqa: BLE001 - a session that cannot be made
        end(run, sr.ERROR, {"step": PLANNERS, "error": f"{type(exc).__name__}: {exc}"})
        return
    results = await asyncio.gather(
        *(asyncio.to_thread(session.review, prompt, PLANNER_SCHEMA)
          for session in sessions), return_exceptions=True)
    for provider, session, result in zip(PROVIDERS, sessions, results):
        if isinstance(result, BaseException):
            for refusal in getattr(session, "refusals", None) or ():
                state.setdefault("refusals", []).append(
                    {"step": f"planner-{provider}", **refusal})
    limited = [(p, r) for p, r in zip(PROVIDERS, results)
               if isinstance(r, limits.UsageLimitError)]
    if limited:
        provider, exc = limited[0]
        end(run, sr.USAGE_LIMIT, _usage_evidence(exc, f"planner-{provider}"))
        return
    failed = [(p, r) for p, r in zip(PROVIDERS, results) if isinstance(r, BaseException)]
    if failed:
        end(run, sr.ERROR, {"step": PLANNERS, "error": "; ".join(
            f"the {p} planner: {type(r).__name__}: {r}" for p, r in failed)})
        return
    plans, ids = {}, {}
    for provider, session, record in zip(PROVIDERS, sessions, results):
        _record_session(run, f"planner-{provider}", "planner", provider, session, record)
        ids[provider] = record.thread_id
        plans[provider] = record.reply
    planning = planning_state(run)
    planning.setdefault("sessions", {}).update(
        {p: i for p, i in ids.items() if isinstance(i, str)})
    planning["attempts"] = planning.get("attempts", 0) + 1
    try:
        for provider in PROVIDERS:
            check_reply(plans[provider], PLANNER_SCHEMA, f"the {provider} planner")
    except sr.ReplyError as exc:
        end(run, sr.ERROR, {"step": PLANNERS, "error": str(exc)})
        return
    questions = {p: plans[p]["open_questions"] for p in PROVIDERS
                 if plans[p]["open_questions"]}
    if questions:
        # Step 2: any question pauses the run. The planners' step is not completed, so
        # a resume runs both again from the revised brief.
        planning["questions"] = questions
        (run.run_dir / QUESTIONS_FILE).write_text(questions_text(run, questions),
                                                  encoding="utf-8", newline="\n")
        sr.enter_pause(state, sr.AWAITING_USER)
        run.save()
        return
    planning.pop("questions", None)
    planning["plans"] = plans
    _complete(run, PLANNERS)


def questions_text(run, questions):
    lines = [f"# Run {run.state['run_id']}: questions from the planners", "",
             "The run is paused `awaiting-user`. Answer each question in the Open "
             "questions section of this run's own `brief.md`, written `- <question> | "
             "Answer: <answer>`, then resume: "
             f"`{loop.STARTER} --resume {run.state['run_id']}`. Both planners then plan "
             "again from the revised brief.", ""]
    for provider, asked in questions.items():
        lines += [f"## From the {provider} planner", ""]
        lines += [f"- {' '.join(q.split())}" for q in asked] + [""]
    return "\n".join(lines)


# ------------------------------------------------------------------- the identity


def identity(run):
    """Step 3's check, before synthesis. The labels' random order is drawn here and
    recorded, so a refusal names the plan's label. A refused plan ends the run
    ``error``; its plans are discarded from the record (their transcripts are kept) and
    the planners' step is no longer completed, so a resume plans again rather than
    refusing the same plans a second time."""
    planning = planning_state(run)
    order = list(run.deps.choose_order(list(PROVIDERS)))
    if sorted(order) != sorted(PROVIDERS):
        end(run, sr.ERROR, {"step": IDENTITY, "error": f"the label order {order} is not "
                            "the two providers"})
        return
    planning["labels"] = dict(zip(LABELS, order))
    try:
        identifiers = load_identifiers()
        start = run.state.get("start") or {}
        files = run.deps.tree_paths(run.root, start.get("public_head"),
                                    start.get("personal_head"), run.state.get("git_dir"))
        # The edit paths of the brief the planners planned from, the run's own
        # brief.md, which a resume from awaiting-user may have revised (code review
        # R1-1); state.json's edit_paths are the start's until approval rebuilds them.
        edit_paths = brief_mod.read_edit_paths(loop._run_brief_arg(run), run.root)
    except Exception as exc:  # noqa: BLE001 - a check that cannot be made refuses
        end(run, sr.ERROR, {"step": IDENTITY, "error": "the identity check cannot be "
                            f"made: {type(exc).__name__}: {exc}"})
        return
    known = KnownPaths(files, edit_paths)
    rules = identity_rules(identifiers, model_names(run.settings),
                           session_ids(run.state))
    refused = []
    for label, provider in planning["labels"].items():
        matches = check_identity(planning["plans"][provider], rules, known)
        if matches:
            refused.append({"label": label, "provider": provider, "matches": matches,
                            "transcript": f"transcripts/planner-{provider}.json"})
    if refused:
        run.state["identity_refusal"] = {"at": _now(), "plans": refused}
        planning.pop("plans", None)
        planning["completed"] = [s for s in planning["completed"] if s != PLANNERS]
        end(run, sr.ERROR, {"step": IDENTITY, "error": "a plan names its provider: "
                            + ", ".join(f"{p['label']} ({p['provider']})"
                                        for p in refused)})
        return
    run.state.pop("identity_refusal", None)
    _complete(run, IDENTITY)


# ------------------------------------------------------------------ the synthesis


def synthesiser_prompt(run):
    """The brief and the two plans, labelled as recorded, with no provider names."""
    planning = planning_state(run)
    plans = [json.dumps(planning["plans"][planning["labels"][label]], indent=1,
                        ensure_ascii=False) for label in LABELS]
    return loop.render("synthesiser", brief=run.brief_text(), plan_1=plans[0],
                       plan_2=plans[1])


async def synthesis(run):
    """Step 3's synthesis. The plan review follows (step 4)."""
    permitted = _permitted(run, SYNTHESIS)
    try:
        await _synthesis(run)
    finally:
        _finish(run, SYNTHESIS, permitted)


async def _synthesis(run):
    provider = run.settings["roles"]["builder"]
    if await _check_usage(run, SYNTHESIS, provider):
        return
    try:
        session = run.deps.make_synthesiser(run.settings, run.root, run.claude_usage,
                                            run.state["frozen_dir"])
        record = await asyncio.to_thread(session.review, synthesiser_prompt(run),
                                         SYNTHESIS_SCHEMA)
    except limits.UsageLimitError as exc:
        end(run, sr.USAGE_LIMIT, _usage_evidence(exc, SYNTHESIS))
        return
    except Exception as exc:  # noqa: BLE001 - a provider failure
        end(run, sr.ERROR, {"step": SYNTHESIS, "error": f"{type(exc).__name__}: {exc}"})
        return
    _record_session(run, SYNTHESIS, "synthesiser", provider, session, record)
    try:
        check_reply(record.reply, SYNTHESIS_SCHEMA, "the synthesiser")
    except sr.ReplyError as exc:
        end(run, sr.ERROR, {"step": SYNTHESIS, "error": str(exc)})
        return
    planning_state(run)["synthesis"] = record.reply
    _complete(run, SYNTHESIS)


# ------------------------------------------------------------- the plan review


def next_label(run):
    """The label round the next plan-review round takes: one past the highest label
    round issued so far in the run (plan 10.3 step 4: labels run on, an amendment's
    rounds included)."""
    return loop.label_rounds_used(run) + 1


def plan_review_schema(with_pattern=True):
    """The plan reviewer's reply schema: the build reviewer's (``REVIEW_SCHEMA``), in the
    packet's one label form with ``reraises``. A plan has no changed files, so there is
    no ``files_reviewed``. Without the title pattern for a Claude session, or once Codex
    has refused it, as for the build reviewer."""
    schema = json.loads(json.dumps(sr.REVIEW_SCHEMA))
    if not with_pattern:
        schema["properties"]["findings"]["items"]["properties"]["title"].pop("pattern",
                                                                            None)
    return schema


def _review_once(run, session, prompt):
    """One plan review, with the build reviewer's title-pattern rule (``loop.Run.
    _review_once``): a Claude session is never sent the pattern, and a Codex one that
    refuses it is asked again without it, the refusal recorded for the run."""
    live = run.state.setdefault("live", {})
    if session.provider == limits.CLAUDE or live.get("title_pattern",
                                                      "").startswith("refused"):
        return session.review(prompt, plan_review_schema(with_pattern=False))
    try:
        record = session.review(prompt, plan_review_schema())
    except providers.ProviderError as exc:
        if "pattern" not in str(exc).lower():
            raise
        live["title_pattern"] = f"refused: {exc}"
        run.save()
        return session.review(prompt, plan_review_schema(with_pattern=False))
    live.setdefault("title_pattern", "accepted")
    return record


def _prior(run):
    return "\n".join(
        f"- {f['label']} ({f['source']}) {f.get('title')}: synthesiser action "
        f"{f.get('action') or 'none yet'}"
        for f in run.ledger.findings.values()) or "None."


def plan_reviewer_prompt(run, label):
    """The brief, the merged plan and the prior findings with the synthesiser's action
    on each: not the two plans, not the synthesiser's notes."""
    merged = (planning_state(run).get("synthesis") or {}).get("merged_plan") or {}
    return loop.render("plan-reviewer", round=label, brief=run.brief_text(),
                       plan=loop.render_plan(merged), prior=_prior(run))


def render_review_packet(run):
    """The ledger rendered to ``runs/<run-id>/plan-review/packet.md`` during planning
    (plan 10.3 step 6, R20-1), in the review packet's layout; nothing in ``memory/``."""
    loop.render_record_packet(run)


async def review_round(run):
    """One plan-review round (step 4)."""
    label = next_label(run)
    step = f"plan-review-R{label}"
    permitted = _permitted(run, step)
    try:
        await _review_round(run, label, step)
    finally:
        _finish(run, step, permitted)


async def _review_round(run, label, step):
    state, planning = run.state, planning_state(run)
    provider = run.settings["roles"]["reviewer"]
    if await _check_usage(run, step, provider):
        return
    session = None
    try:
        session = run.deps.make_plan_reviewer(run.settings, run.root, run.claude_usage,
                                              state["frozen_dir"])
        record = await asyncio.to_thread(_review_once, run, session,
                                         plan_reviewer_prompt(run, label))
    except limits.UsageLimitError as exc:
        end(run, sr.USAGE_LIMIT, _usage_evidence(exc, step))
        return
    except Exception as exc:  # noqa: BLE001 - a provider failure
        for refusal in getattr(session, "refusals", None) or ():
            state.setdefault("refusals", []).append({"step": step, **refusal})
        end(run, sr.ERROR, {"step": step, "error": f"{type(exc).__name__}: {exc}"})
        return
    _record_session(run, step, "plan_reviewer", provider, session, record)
    try:
        findings = sr.parse_review(record.reply, run.ledger)
    except sr.ReplyError as exc:
        end(run, sr.ERROR, {"step": step, "error": str(exc)})
        return
    new = run.ledger.add_round(label, [], findings)
    run._accept_declines(label, new)
    state["label_rounds"] = label
    planning["review_rounds"] = planning.get("review_rounds", 0) + 1
    planning["reviewed"] = not new
    planning["completed"].append(step)
    state["step"] = step
    state.pop("interrupted", None)
    run.save()
    render_review_packet(run)
    stop = sr.decide(run.ledger)
    if stop is not None:
        end(run, *stop)
        return
    if not new:
        return  # the next move is the approval pause
    cap = planning.get("round_cap") or run.settings["round_caps"]["plan_review"]
    if planning["review_rounds"] >= cap:
        end(run, sr.MAX_ROUNDS, {"round": label, "plan_review_rounds":
                                 planning["review_rounds"], "cap": cap, "half": "planning",
                                 "open": [f["label"] for f in new]})


def revision_prompt(run, label):
    planning = planning_state(run)
    plans = [json.dumps(planning["plans"][planning["labels"][name]], indent=1,
                        ensure_ascii=False) for name in LABELS]
    blocks = []
    for name in run.ledger.open_labels():
        f = run.ledger.findings[name]
        blocks.append(f"{name} ({f.get('severity')}) {f.get('title')}\nFile: "
                      f"{f.get('file')}\nEvidence: {f.get('evidence')}\nSuggested fix: "
                      f"{f.get('fix')}"
                      + (f"\nRe-raises: {f['reraises']}" if f.get("reraises") else ""))
    return loop.render("revision", round=label, brief=run.brief_text(),
                       plan_1=plans[0], plan_2=plans[1],
                       synthesis=json.dumps(planning.get("synthesis"), indent=1,
                                            ensure_ascii=False),
                       findings="\n\n".join(blocks))


async def revise(run):
    """The synthesiser's revision after a plan-review round with findings (step 4)."""
    label = loop.label_rounds_used(run)
    step = f"plan-revise-R{label}"
    permitted = _permitted(run, step)
    try:
        await _revise(run, label, step)
    finally:
        _finish(run, step, permitted)


async def _revise(run, label, step):
    state, planning = run.state, planning_state(run)
    provider = run.settings["roles"]["builder"]
    if await _check_usage(run, step, provider):
        return
    open_labels = run.ledger.open_labels()
    try:
        session = run.deps.make_synthesiser(run.settings, run.root, run.claude_usage,
                                            state["frozen_dir"])
        record = await asyncio.to_thread(session.review, revision_prompt(run, label),
                                         REVISION_SCHEMA)
    except limits.UsageLimitError as exc:
        end(run, sr.USAGE_LIMIT, _usage_evidence(exc, step))
        return
    except Exception as exc:  # noqa: BLE001 - a provider failure
        end(run, sr.ERROR, {"step": step, "error": f"{type(exc).__name__}: {exc}"})
        return
    _record_session(run, step, "synthesiser", provider, session, record)
    reply = record.reply
    try:
        if not isinstance(reply, dict):
            raise sr.ReplyError("the synthesiser: the reply is not a JSON object")
        actions = sr.parse_plan_actions(reply.get("actions"), open_labels)
        revised = {k: v for k, v in reply.items() if k != "actions"}
        check_reply(revised, SYNTHESIS_SCHEMA, "the synthesiser")
    except sr.ReplyError as exc:
        end(run, sr.ERROR, {"step": step, "error": str(exc)})
        return
    run.ledger.apply_actions(actions)
    planning["synthesis"] = revised
    planning["completed"].append(step)
    state["step"] = step
    state.pop("interrupted", None)
    run.save()
    render_review_packet(run)
    stop = sr.decide(run.ledger)
    if stop is not None:
        end(run, *stop)


async def pause(run):
    """Every planning step done and the last plan review clean: write
    ``plan-report.md`` (again, for a run interrupted after its last step, code review
    R1-2) and pause ``awaiting-plan-approval`` (step 6)."""
    write_plan_report(run)
    sr.enter_pause(run.state, sr.AWAITING_PLAN_APPROVAL)
    run.save()


# -------------------------------------------------------------- the plan report


render_plan = loop.render_plan


def write_plan_report(run):
    """``plan-report.md`` in the run record (plan 10.3 step 6 and the paragraph after
    the chunk list): both plans, the synthesis, the plan-review findings from the run's
    ledger, and an intent section saying no intent check has run (A4's)."""
    planning = planning_state(run)
    synthesis_reply = planning.get("synthesis") or {}
    lines = [f"# Plan report, run {run.state['run_id']}", ""]
    for label in LABELS:
        provider = planning["labels"][label]
        lines += [f"## {label} (by the {provider} planner)", "",
                  render_plan(planning["plans"][provider]), ""]
    lines += ["## Synthesis", "", "Agreements:", ""]
    lines += [f"- {a}" for a in synthesis_reply.get("agreements", [])] or ["- None."]
    # Every disagreement the synthesiser listed, whether or not it gave a resolution
    # for it (code review R1-3), then the resolutions it gave.
    lines += ["", "Disagreements:", ""]
    lines += [f"- {d}" for d in synthesis_reply.get("disagreements", [])] or ["- None."]
    lines += ["", "Resolutions:", ""]
    lines += [f"- {r.get('disagreement')}: {r.get('resolution')} ({r.get('reason')})"
              for r in synthesis_reply.get("resolutions", [])] or ["- None."]
    lines += ["", "Retained risks:", ""]
    lines += [f"- {r}" for r in synthesis_reply.get("retained_risks", [])] or ["- None."]
    lines += ["", "### The merged plan", "",
              render_plan(synthesis_reply.get("merged_plan") or {}), ""]
    lines += ["## Plan review findings", ""] + review_lines(run) + [""]
    lines += ["## Intent check", "",
              "No intent check has run: it is built in sub-stage A4 (plan 10.6).", ""]
    (run.run_dir / PLAN_REPORT).write_text("\n".join(lines), encoding="utf-8",
                                           newline="\n")


def review_lines(run):
    """The plan report's plan-review section: the rounds this plan was reviewed in and
    every finding in the run's ledger, open or addressed, each with its label, the
    synthesiser's action and note, and any reraise. Text from a session goes through the
    packet's ``clean``."""
    planning = planning_state(run)
    clean = loop.packet_mod.clean
    rounds = planning.get("review_rounds", 0)
    lines = [f"Plan-review rounds for this plan: {rounds} of "
             f"{planning.get('round_cap') or run.settings['round_caps']['plan_review']}; "
             f"the findings are also in `{PLAN_REVIEW_PACKET.as_posix()}`.", ""]
    findings = list(run.ledger.findings.values())
    if not findings:
        return lines + ["None: the plan review found nothing."]
    for f in findings:
        status = "addressed" if loop.packet_mod.is_addressed(f) else "open"
        lines.append(f"- {f['label']} ({status}, {clean(f.get('severity'))}) "
                     f"{clean(f.get('title'), 300)}. File: {clean(f.get('file'), 300)}. "
                     f"Evidence: {clean(f.get('evidence'))}"
                     + (f" Re-raises: {f['reraises']}." if f.get("reraises") else "")
                     + (" Superseded by amendment." if f.get("superseded_by_amendment")
                        else f" Synthesiser: {f['action']}. {clean(f.get('note'))}"
                        if f.get("action") else ""))
    return lines


# ----------------------------------------------------------------- driving it


def next_step(run):
    """The planning half's next move: the first of the planners, the identity check and
    the synthesis not completed; then the synthesiser's revision while any plan-review
    finding is open (``revise``), the approval pause once the last plan review found
    nothing (``pause``), and otherwise a plan-review round (``review``)."""
    planning = planning_state(run)
    done = planning.get("completed") or []
    for step in STEPS:
        if step not in done:
            return step
    if run.ledger.open_labels():
        return REVISE
    if planning.get("reviewed"):
        return PAUSE
    return REVIEW


def next_step_name(run):
    """The next step's name as a paid-usage permission names it: ``planners`` (one step
    for both planners), ``synthesis`` (also after the identity check, which calls no
    provider), ``plan-review-R<n>``, ``plan-revise-R<n>``, or ``pause``, which takes no
    step."""
    step = next_step(run)
    if step == IDENTITY:
        return SYNTHESIS
    if step == REVIEW:
        return f"plan-review-R{next_label(run)}"
    if step == REVISE:
        return f"plan-revise-R{loop.label_rounds_used(run)}"
    return step


def next_providers(run):
    """The provider or providers of the planning step the run starts next, the only
    ones a preflight checks (plan 10.10 item 8): both for the planners, the reviewer's
    for a plan review, none for the pause, and the builder's (the synthesiser's) for
    the rest."""
    step = next_step(run)
    roles = run.settings["roles"]
    if step == PLANNERS:
        return PROVIDERS
    if step == REVIEW:
        return (roles["reviewer"],)
    if step == PAUSE:
        return ()
    return (roles["builder"],)


async def advance(run):
    """Take the planning steps from the last one completed until the run pauses or
    ends. A malformed identifiers file ends the run ``error`` before any session. With
    ``--wait``, a ``usage-limit`` end with a known reset sleeps until it and resumes,
    as the build does (``loop.Run.wait_then_reload``), unless the run changed while the
    process slept."""
    while True:
        await _advance(run)
        if not run.wait_then_reload():
            break
        resume(run, user_started=False)
    return run.state


async def _advance(run):
    problems = preflight()
    if problems and run.state.get("status") == sr.RUNNING:
        end(run, sr.ERROR, {"step": "planning", "error": "; ".join(problems)})
        return
    while run.state.get("status") == sr.RUNNING:
        step = next_step(run)
        if step == PLANNERS:
            await planners(run)
        elif step == IDENTITY:
            identity(run)
        elif step == SYNTHESIS:
            await synthesis(run)
        elif step == REVISE:
            await revise(run)
        elif step == REVIEW:
            await review_round(run)
        else:  # the last plan review found nothing: the run waits for the user
            await pause(run)


def resume(run, dry_run=False, *, allow_paid=False, rounds=None, user_started=True):
    """``--resume`` of a run in its planning half. Every check comes before any change,
    and a refusal (``loop.RunRefused``) changes nothing.

    A run paused ``awaiting-user`` for its planners' questions resumes in step 2's
    order: read the revised brief's edit paths, refusing if they cannot be read; make
    the four checks ``--approve`` makes over them, refusing the same way; check the
    whole revised brief with the checker, logging nothing; then the usage preflight
    for the planners, and (in ``advance``) both planners again from it. An ended run
    whose stop reason can be resumed, or a record left ``running`` by an interrupted
    process (code review R1-2), takes a fresh Codex reading and credit snapshot, as a
    resume does, and resumes from its last planning step completed. Nothing is logged
    in any case.

    Since A3 chunk 3 the build's options apply here too (the chunk 3 bullet's first
    follow-on): ``allow_paid`` permits the next planning step (the two planners as one)
    on paid usage, ``rounds`` allows N more plan-review rounds after those this plan has
    had, and the run's ``wait`` makes the preflight's checks those of a waiting resume,
    a Claude paid-overage reading with an unknown reset not cleared (plan 10.10 items
    5, 7 and 8). The usage preflight checks only the next step's provider or providers.
    ``user_started`` false is an automatic ``--wait`` resume, which makes no usage check
    here, the next step's own check standing. Every resume claims the run with
    ``resume.lock`` before its first change (item 5)."""
    state, run_id = run.state, run.state["run_id"]
    problems = preflight()
    if problems:
        raise loop.RunRefused(problems + ["nothing was changed"])
    if allow_paid and run.wait:
        raise loop.RunRefused(["--allow-paid cannot be used with --wait"])
    if rounds is not None and rounds < 1:
        raise loop.RunRefused(["--rounds must be at least 1"])
    loop._refuse_if_claimed(run)
    seen = loop._claim_mark(state)
    plain = user_started and not run.wait
    status = state.get("status")
    if status == sr.PAUSED and state.get("pause") == sr.AWAITING_USER:
        arg = loop._run_brief_arg(run)
        try:
            edit_paths = brief_mod.read_edit_paths(arg, run.root)
        except ValueError as exc:
            raise loop.RunRefused([f"the revised brief's edit paths cannot be read: {exc}",
                                   "nothing was changed"]) from exc
        moved = loop.approval_problems(run, edit_paths)
        if moved:
            raise loop.RunRefused(moved + ["nothing was changed and the run stays paused; "
                                           f"the way on is {loop.STARTER} --stop {run_id}, "
                                           "a capture, and a new run"])
        lines, code = brief_mod.check_brief(arg, run.root)
        if code != 0:
            raise loop.RunRefused(["the revised brief does not pass the check: "
                                   + "; ".join(lines), "nothing was changed"])
        reading, credits = loop._approval_reading(run, allow_paid=allow_paid,
                                                  providers_=PROVIDERS,
                                                  user_resume=plain, check=user_started)
        if dry_run:
            print(f"[DRY RUN] would resume run {run_id}, paused {sr.AWAITING_USER}: the "
                  "revised brief's edit paths were read, the four approval checks passed "
                  "over them and the brief passes the check; both planners would plan "
                  f"again from it ({reading.summary()}; Codex credits {credits})."
                  + (" --allow-paid would permit the planners only."
                     if allow_paid else "") + " Nothing was changed.", file=sys.stderr)
            return None
        lock = loop.claim(run, seen)
        try:
            planning_state(run).pop("questions", None)
            planning_state(run)["resumed"] = planning_state(run).get("resumed", 0) + 1
            sr.continue_run(state)
            _resumed(run, reading, credits, allow_paid=allow_paid, rounds=rounds,
                     plain=plain)
            run.save()
        finally:
            loop.release(lock)
        return run
    if status in (sr.ENDED, sr.RUNNING):
        # A planning record left ``running`` is one whose process was interrupted
        # between or during steps (code review R1-2): every completed step is saved
        # before the next begins, so it resumes from the last one completed, as an
        # ended run does. Nothing guards against a second process still driving it;
        # the user resumes a run only once its own process has gone.
        last = state["stop_reasons"][-1]["reason"] if state["stop_reasons"] else None
        if status == sr.ENDED and last not in sr.RESUMABLE:
            raise loop.RunRefused([f"a run that ended {last} cannot be resumed; only "
                                   f"{', '.join(sr.RESUMABLE)} can"])
        if status == sr.ENDED and last == sr.MAX_ROUNDS and rounds is None:
            # More plan-review rounds are the user's to authorise (plan 10.3 step 4,
            # code review R1-1): a plain resume would revise and review past the cap.
            raise loop.RunRefused([
                f"run {run_id} stopped at the plan-review cap; more rounds are given "
                f"with {loop.STARTER} --resume {run_id} --rounds N, or the plan is "
                f"amended ({loop.STARTER} --amend) or the run stopped. Nothing was "
                "changed"])
        reading, credits = loop._approval_reading(run, allow_paid=allow_paid,
                                                  user_resume=plain, check=user_started)
        nxt = {PAUSE: "the approval pause"}.get(next_step(run), next_step_name(run))
        where = f"ended {last}" if status == sr.ENDED else "interrupted while running"
        if dry_run:
            print(f"[DRY RUN] would resume run {run_id} ({where}) in its planning "
                  f"half at {nxt}; {reading.summary()}; Codex credits {credits}"
                  + (f"; {rounds} more plan-review round(s)" if rounds else "")
                  + ("; --allow-paid would permit that step only"
                     if allow_paid else "") + ". Nothing was changed.", file=sys.stderr)
            return None
        lock = loop.claim(run, seen)
        try:
            if status == sr.ENDED:
                sr.continue_run(state)
            else:
                planning_state(run)["interrupted_resumes"] = (
                    planning_state(run).get("interrupted_resumes", 0) + 1)
            _resumed(run, reading, credits, allow_paid=allow_paid, rounds=rounds,
                     plain=plain)
            run.save()
        finally:
            loop.release(lock)
        return run
    raise loop.RunRefused([f"run {run_id} cannot be resumed in its planning half (it is "
                           f"{status}" + (f", {state.get('pause')}" if state.get("pause")
                                          else "") + ")"])


def _resumed(run, reading, credits, *, allow_paid, rounds, plain):
    """A planning resume's changes to the state, under its claim: the fresh Codex
    reading and credit snapshot, a permission left from an earlier attempt cleared and
    a new one granted, the unknown-reset allowance of a plain resume, and the round
    cap for ``--rounds``."""
    state, planning = run.state, planning_state(run)
    state["credits"] = credits
    run.note_codex(reading)
    state.pop("paid_usage_pending", None)
    last = loop._last_claude(state)
    state["claude_unknown_reset_resume"] = bool(
        plain and loop._paid_overage(last) and last.resets_at is None)
    if rounds is not None:
        planning["round_cap"] = planning.get("review_rounds", 0) + rounds
    if allow_paid:
        loop._grant_paid(run, reading)
