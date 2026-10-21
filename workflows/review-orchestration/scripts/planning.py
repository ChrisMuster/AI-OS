#!/usr/bin/env python3
"""
planning.py - the planning half's sessions (plan 10.3 steps 1 to 3, A3 chunk 2).

A run with a planning half plans before it builds. Its steps, each recorded in
``state.json`` under ``planning`` once it has completed:

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

After the synthesis the run writes ``plan-report.md`` and pauses
``awaiting-plan-approval`` (step 6), where chunk 1's ``--approve``, ``--amend`` and
``--stop`` act on it. The plan review (step 4) and the intent check (step 5) are
later chunks' work, so the report says no plan review and no intent check has run.

Nothing here writes outside the run's own record, ``runs/<run-id>/`` (step 6, "The run
writes nothing in the project before approval"): no LOG.md entry at a pause, at an end
or at a resume, and no packet in ``memory/``. A stop resumes from the last planning step
completed, never mid-step, and the two planners always run again together.

Every session is a read-only session of ``providers.py`` started with the reviewers'
lockdown (step 7; process table rows 16 and 17), reached through ``loop.Deps`` so the
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
LABELS = ("Plan 1", "Plan 2")
QUESTIONS_FILE = "questions.md"
PLAN_REPORT = "plan-report.md"

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
    to ``state.json`` and the report only. Nothing is written in ``memory/``."""
    sr.record_stop(run.state, reason, evidence)
    try:
        run.save()
        loop.write_report(run)
    finally:
        run._after_end(reason)


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


async def _check_usage(run, step, provider):
    """The before-step usage check (plan 10.10). Returns None, or ends the run and
    returns True."""
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
    """Step 1: the usage check for both planners, one after the other, then both
    sessions in parallel from the same prompt; then step 2's pause on any question."""
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
    """Step 3's synthesis, then ``plan-report.md`` and the approval pause."""
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
    write_plan_report(run)
    sr.enter_pause(run.state, sr.AWAITING_PLAN_APPROVAL)
    run.save()


# -------------------------------------------------------------- the plan report


def render_plan(plan):
    """A planner's plan, or the merged plan, as markdown."""
    lines = [plan.get("summary", ""), "", f"Confidence: {plan.get('confidence')}.", ""]
    for number, step in enumerate(plan.get("steps", []), 1):
        lines.append(f"{number}. {step.get('action')}")
        lines.append(f"   - Files: " + (", ".join(f"`{f}`" for f in step.get("files", []))
                                        or "none named"))
        lines.append(f"   - Verification: {step.get('verification')}")
    for title, key in (("Risks", "risks"), ("Assumptions", "assumptions"),
                       ("Open questions", "open_questions")):
        if key in plan:
            lines += ["", f"{title}:", ""]
            lines += [f"- {item}" for item in plan[key]] or ["- None."]
    return "\n".join(lines)


def write_plan_report(run):
    """``plan-report.md`` in the run record (plan 10.3, the paragraph after the chunk
    list): both plans, the synthesis, the plan-review findings section (empty until the
    plan review is built) and an intent section saying no intent check has run."""
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
    lines += ["## Plan review findings", "",
              "None: no plan review has run. The plan review (plan 10.3 step 4) is not "
              "built yet.", ""]
    lines += ["## Intent check", "",
              "No intent check has run: it is built in sub-stage A4 (plan 10.6).", ""]
    (run.run_dir / PLAN_REPORT).write_text("\n".join(lines), encoding="utf-8",
                                           newline="\n")


# ----------------------------------------------------------------- driving it


async def advance(run):
    """Take the planning steps from the last one completed until the run pauses or
    ends. A malformed identifiers file ends the run ``error`` before any session."""
    planning = planning_state(run)
    problems = preflight()
    if problems and run.state.get("status") == sr.RUNNING:
        end(run, sr.ERROR, {"step": "planning", "error": "; ".join(problems)})
        return run.state
    while run.state.get("status") == sr.RUNNING:
        done = planning["completed"]
        if PLANNERS not in done:
            await planners(run)
        elif IDENTITY not in done:
            identity(run)
        elif SYNTHESIS not in done:
            await synthesis(run)
        else:  # every step done: the run waits for the user's decision
            # A run interrupted after its synthesis was saved may lack its report
            # (code review R1-2), so it is written again, from the saved state.
            write_plan_report(run)
            sr.enter_pause(run.state, sr.AWAITING_PLAN_APPROVAL)
            run.save()
    return run.state


def resume(run, dry_run=False):
    """``--resume`` of a run in its planning half. Every check comes before any change,
    and a refusal (``loop.RunRefused``) changes nothing.

    A run paused ``awaiting-user`` for its planners' questions resumes in step 2's
    order: read the revised brief's edit paths, refusing if they cannot be read; make
    the four checks ``--approve`` makes over them, refusing the same way; check the
    whole revised brief with the checker, logging nothing; then (in ``advance``) run
    both planners again from it. An ended run whose stop reason can be resumed, or a
    record left ``running`` by an interrupted process (code review R1-2), takes a fresh
    Codex reading and credit snapshot, as a resume does, and resumes from its last
    planning step completed. Nothing is logged in any case."""
    state, run_id = run.state, run.state["run_id"]
    problems = preflight()
    if problems:
        raise loop.RunRefused(problems + ["nothing was changed"])
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
        if dry_run:
            print(f"[DRY RUN] would resume run {run_id}, paused {sr.AWAITING_USER}: the "
                  "revised brief's edit paths were read, the four approval checks passed "
                  "over them and the brief passes the check; both planners would plan "
                  "again from it. Nothing was changed.", file=sys.stderr)
            return None
        planning_state(run).pop("questions", None)
        planning_state(run)["resumed"] = planning_state(run).get("resumed", 0) + 1
        sr.continue_run(state)
        run.save()
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
        reading, credits = loop._approval_reading(run)
        done = planning_state(run)["completed"]
        nxt = next((s for s in STEPS if s not in done), "the approval pause")
        where = f"ended {last}" if status == sr.ENDED else "interrupted while running"
        if dry_run:
            print(f"[DRY RUN] would resume run {run_id} ({where}) in its planning "
                  f"half at {nxt}; {reading.summary()}; Codex credits {credits}. Nothing "
                  "was changed.", file=sys.stderr)
            return None
        state["credits"] = credits
        run.note_codex(reading)
        if status == sr.ENDED:
            sr.continue_run(state)
        else:
            planning_state(run)["interrupted_resumes"] = (
                planning_state(run).get("interrupted_resumes", 0) + 1)
        run.save()
        return run
    raise loop.RunRefused([f"run {run_id} cannot be resumed in its planning half (it is "
                           f"{status}" + (f", {state.get('pause')}" if state.get("pause")
                                          else "") + ")"])
