#!/usr/bin/env python3
"""
stopreasons.py - pauses, stop reasons and the loop-exit rules (plan section 10.5).

A run is running, paused or ended. A pause is not a stop reason; a run that has ended
carries exactly one stop reason, recorded with its evidence. The script decides the
stop reason from structured fields alone, never from finding titles or prose:

  builder   its reply to each fix pass ends with a fenced ``json`` block listing every
            open finding exactly once, each with one ``action`` (``fixed``,
            ``declined-scope`` or ``declined-merits``) and a ``note``. A reply that
            omits an open finding, lists one twice, names one that is not open, or
            uses any other action ends the run with ``error`` (``ReplyError``).
  synthesiser  in the planning half's plan review (plan 10.3 step 4, A3 chunk 3) the
            synthesiser is in the builder's place: its revision reply carries an
            ``actions`` list read by ``parse_plan_actions``, each ``fixed`` or
            ``declined-merits``; ``declined-scope`` has no meaning before a build.
  reviewer  its structured reply (``REVIEW_SCHEMA``) marks a finding that raises an
            earlier one again with ``reraises: <label>``; an empty ``reraises`` means
            the finding is new. A title holding a label, or a ``reraises`` naming a
            label the run has not issued, is a malformed reply. A ``reraises`` naming a
            finding on a plan an amendment replaced makes the finding new, the label kept
            in ``names_replaced``.

The three rules, applied in this order after every pass (``decide``):

  1. Any ``declined-scope`` ends the run as ``out-of-scope``.
  2. Any ``reraises`` of a finding marked ``declined-merits`` ends it as
     ``disagreement``.
  3. Follow each ``reraises`` back to the first label in its chain. If that first
     finding is raised again in two consecutive review rounds, each time of a finding
     the builder had marked ``fixed``, the run ends as ``reopened``.

``Ledger`` holds every finding's label, round, source, ``reraises`` and ``action``,
which is exactly what ``state.json`` records per round (plan section 10.7).
"""

import json
import re
from datetime import datetime

# ------------------------------------------------------------------ vocabulary

RUNNING = "running"
PAUSED = "paused"
ENDED = "ended"

AWAITING_PLAN_APPROVAL = "awaiting-plan-approval"
AWAITING_USER = "awaiting-user"
PAUSES = (AWAITING_PLAN_APPROVAL, AWAITING_USER)

CLEAN = "clean"
# Retired by A4 (plan 10.5 and 10.6): a run started from this code with nothing left to
# fix goes on to the final intent check and ends ``clean`` or ``intent-failed``, and
# never produces this. It stays in the vocabulary only so that the record of a run
# started earlier, which keeps its own frozen copy and is not migrated, still reads.
REVIEWED_CLEAN = "reviewed-clean"
INTENT_FAILED = "intent-failed"
NO_CHANGE_MADE = "no-change-made"
MAX_ROUNDS = "max-rounds"
REOPENED = "reopened"
OUT_OF_SCOPE = "out-of-scope"
DISAGREEMENT = "disagreement"
USAGE_LIMIT = "usage-limit"
STOPPED_BY_USER = "stopped-by-user"
ERROR = "error"
STOP_REASONS = (CLEAN, REVIEWED_CLEAN, INTENT_FAILED, NO_CHANGE_MADE, MAX_ROUNDS,
                REOPENED, OUT_OF_SCOPE, DISAGREEMENT, USAGE_LIMIT, STOPPED_BY_USER, ERROR)

# The only ended runs --resume may continue (plan section 10.5).
RESUMABLE = (MAX_ROUNDS, USAGE_LIMIT, ERROR)

FIXED = "fixed"
DECLINED_SCOPE = "declined-scope"
DECLINED_MERITS = "declined-merits"
ACTIONS = (FIXED, DECLINED_SCOPE, DECLINED_MERITS)

MECHANICAL = "mechanical"
REVIEWER = "reviewer"

LABEL = re.compile(r"^R([1-9][0-9]*)-([1-9][0-9]*)$")
# A label anywhere in a title: R, a round, a hyphen (or a dash), a number. The dashes
# are U+2010 to U+2015, built by code point so the source stays plain ASCII.
_DASHES = "-" + "".join(chr(point) for point in range(0x2010, 0x2016))
_LABEL_IN_TEXT = re.compile(r"(?<![A-Za-z0-9])R[0-9]+\s*[" + re.escape(_DASHES)
                            + r"]\s*[0-9]+(?![0-9])")
_FENCE = re.compile(r"```json[ \t]*\r?\n(.*?)\r?\n[ \t]*```", re.DOTALL)

SEVERITIES = ("blocker", "major", "minor")

# The reviewer's reply. Every field is required and nothing else is allowed, as a
# strict structured-output schema needs. The title pattern refuses a label at the
# source where the provider enforces it; ``parse_review`` checks it whatever the
# provider does, so the rule holds either way.
TITLE_PATTERN = r"^(?!.*(?<![A-Za-z0-9])R[0-9]+\s*-\s*[0-9]).*$"
REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "findings"],
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "severity", "file", "evidence", "fix", "reraises"],
                "properties": {
                    "title": {"type": "string", "pattern": TITLE_PATTERN},
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "file": {"type": "string"},
                    "evidence": {"type": "string"},
                    "fix": {"type": "string"},
                    "reraises": {"type": "string"},
                },
            },
        },
    },
}


class ReplyError(Exception):
    """A builder or reviewer reply broke the loop-exit fields. The run ends ``error``."""


def label(round_no, number):
    return f"R{round_no}-{number}"


def title_has_label(title):
    return bool(_LABEL_IN_TEXT.search(title))


# --------------------------------------------------------------------- ledger


class Ledger:
    """Every finding the run has issued, keyed by label, in the order issued."""

    def __init__(self, findings=None):
        self.findings = {}
        for finding in findings or ():
            self.findings[finding["label"]] = dict(finding)

    def open_labels(self):
        """Findings with no builder action yet, in the order issued."""
        return [name for name, f in self.findings.items() if f.get("action") is None]

    def current(self):
        """Findings about the plan in force, in the order issued: every finding but
        those an amendment replaced, which stay on record but are no longer about
        anything a reviewer is reading, so no reviewer is shown one as a prior finding."""
        return [f for f in self.findings.values() if not f.get("replaced_by_amendment")]

    def add_round(self, round_no, mechanical, reviewer):
        """Issue labels for one review round: the script's own findings first, then
        the reviewer's, numbered from 1. Returns the new findings.

        ``mechanical`` and ``reviewer`` are lists of dicts; reviewer findings must
        already have passed ``parse_review``. A finding issued in an earlier round
        and not raised again keeps whatever action the builder gave it.
        """
        if any(f["round"] >= round_no for f in self.findings.values()):
            raise ValueError(f"round {round_no} is not after the last round recorded")
        new = []
        for source, items in ((MECHANICAL, mechanical), (REVIEWER, reviewer)):
            for item in items:
                reraises = (item.get("reraises") or None) if source == REVIEWER else None
                finding = {**item, "label": label(round_no, len(new) + 1),
                           "round": round_no, "source": source,
                           "reraises": reraises, "action": None, "note": None}
                new.append(finding)
        for finding in new:
            self.findings[finding["label"]] = finding
        return new

    def apply_actions(self, actions):
        """Record the builder's parsed actions (from ``parse_builder_reply``)."""
        for name, (action, note) in actions.items():
            self.findings[name]["action"] = action
            self.findings[name]["note"] = note

    def root(self, name):
        """The first label in a finding's ``reraises`` chain."""
        seen = set()
        while True:
            parent = self.findings[name].get("reraises")
            if parent is None or parent in seen:
                return name
            seen.add(name)
            name = parent

    def state_rounds(self):
        """The per-round record ``state.json`` keeps: every finding's label, source,
        action and reraises, grouped by round in order."""
        rounds = {}
        for f in self.findings.values():
            rounds.setdefault(f["round"], []).append(
                {"label": f["label"], "source": f["source"], "action": f["action"],
                 "reraises": f["reraises"]})
        return [{"round": n, "findings": rounds[n]} for n in sorted(rounds)]


# --------------------------------------------------------------------- replies


def parse_review(reply, ledger):
    """Check a reviewer's structured reply against the schema and the run's labels.

    ``reply`` is the parsed JSON object (or its text). Returns the findings list.
    Raises ``ReplyError`` on anything the loop-exit fields forbid.
    """
    if isinstance(reply, str):
        try:
            reply = json.loads(reply)
        except ValueError as exc:
            raise ReplyError(f"the reviewer's reply is not JSON: {exc}") from exc
    if not isinstance(reply, dict):
        raise ReplyError("the reviewer's reply is not a JSON object")
    required = REVIEW_SCHEMA["required"]
    if set(reply) != set(required):
        raise ReplyError(f"the reviewer's reply must hold exactly: {', '.join(required)}")
    if not isinstance(reply["summary"], str):
        raise ReplyError("the reviewer's summary is not text")
    findings = reply["findings"]
    if not isinstance(findings, list):
        raise ReplyError("the reviewer's findings are not a list")
    item_schema = REVIEW_SCHEMA["properties"]["findings"]["items"]
    fields = item_schema["required"]
    for number, finding in enumerate(findings, 1):
        where = f"reviewer finding {number}"
        if not isinstance(finding, dict) or set(finding) != set(fields):
            raise ReplyError(f"{where} must hold exactly: {', '.join(fields)}")
        for field in fields:
            if not isinstance(finding[field], str):
                raise ReplyError(f"{where}: {field} is not text")
        if finding["severity"] not in SEVERITIES:
            raise ReplyError(f"{where}: severity {finding['severity']!r} is not one of "
                             f"{', '.join(SEVERITIES)}")
        if title_has_label(finding["title"]):
            raise ReplyError(f"{where}: the title holds a finding label; labels are "
                             "issued by the script, and a repeat goes in reraises")
        target = finding["reraises"].strip()
        if target and target not in ledger.findings:
            raise ReplyError(f"{where}: reraises {target!r}, which this run has not "
                             "issued")
        if target and ledger.findings[target].get("replaced_by_amendment"):
            # A reviewer can still read the old label in the packet's diff. What it found
            # is about the plan in force, so it counts as new, the label kept as a note.
            finding["names_replaced"] = target
            target = ""
        finding["reraises"] = target
    return findings


def parse_builder_reply(text, open_labels):
    """Read the builder's action list: the last fenced ``json`` block in its reply.

    Every label in ``open_labels`` must appear exactly once, with one action from
    ``ACTIONS`` and a text ``note``; nothing else may appear. Returns
    ``{label: (action, note)}``. Raises ``ReplyError`` otherwise.
    """
    blocks = _FENCE.findall(text or "")
    if not blocks:
        raise ReplyError("the builder's reply has no fenced json block of actions")
    try:
        entries = json.loads(blocks[-1])
    except ValueError as exc:
        raise ReplyError(f"the builder's action block is not valid JSON: {exc}") from exc
    if not isinstance(entries, list):
        raise ReplyError("the builder's action block is not a JSON list")
    return _check_actions(entries, open_labels, ACTIONS, "builder")


# The synthesiser's actions on plan-review findings (plan 10.3 step 4): the builder's
# list without ``declined-scope``, which has no meaning before a build.
PLAN_ACTIONS = (FIXED, DECLINED_MERITS)


def parse_plan_actions(entries, open_labels):
    """Read the synthesiser's action list from its revision reply (plan 10.3 step 4):
    every open plan-review finding exactly once, each ``fixed`` or ``declined-merits``
    with a text ``note``, and nothing else. ``declined-scope``, any other value, a
    missing finding, a repeated one or one that is not open raises ``ReplyError``, and
    the run ends ``error``. Returns ``{label: (action, note)}``."""
    if not isinstance(entries, list):
        raise ReplyError("the synthesiser's actions are not a JSON list")
    for number, entry in enumerate(entries, 1):
        if isinstance(entry, dict) and entry.get("action") == DECLINED_SCOPE:
            raise ReplyError(f"synthesiser action {number}: {entry.get('label')} has "
                             f"action {DECLINED_SCOPE!r}, which has no meaning before a "
                             f"build; the action must be one of {', '.join(PLAN_ACTIONS)}")
    return _check_actions(entries, open_labels, PLAN_ACTIONS, "synthesiser")


def _check_actions(entries, open_labels, allowed, who):
    """Every open finding exactly once, with one action from ``allowed`` and a text
    note; nothing else."""
    open_set = set(open_labels)
    actions = {}
    for number, entry in enumerate(entries, 1):
        where = f"{who} action {number}"
        if not isinstance(entry, dict) or set(entry) != {"label", "action", "note"}:
            raise ReplyError(f"{where} must hold exactly: label, action, note")
        name, action, note = entry["label"], entry["action"], entry["note"]
        if not isinstance(name, str) or name not in open_set:
            raise ReplyError(f"{where} names {name!r}, which is not an open finding")
        if name in actions:
            raise ReplyError(f"{where}: {name} is given more than one action")
        if action not in allowed:
            raise ReplyError(f"{where}: {name} has action {action!r}; the action must "
                             f"be one of {', '.join(allowed)}")
        if not isinstance(note, str):
            raise ReplyError(f"{where}: {name}'s note is not text")
        actions[name] = (action, note)
    missing = [name for name in open_labels if name not in actions]
    if missing:
        raise ReplyError(f"the {who} gave no action for: {', '.join(missing)}")
    return actions


# ------------------------------------------------------- the intent reply (A4)

# The intent checker's reply (plan 10.6 item 2; the keys and the status wording are the
# builder's choice). Every field required and nothing else allowed, as every reply
# schema here is. ``lines`` holds one entry per Goal and Constraint line, in the
# brief's order, the line repeated verbatim; ``unasked_changes`` and ``undelivered``
# are the two lists, each item with its evidence. The final check's reply also lists
# ``files_reviewed`` (``intent_schema(final=True)``).
MET, NOT_MET, CANNOT_TELL = "met", "not met", "cannot tell"
INTENT_STATUSES = (MET, NOT_MET, CANNOT_TELL)
_INTENT_TEXT = {"type": "string"}
_EVIDENCED = {"type": "object", "additionalProperties": False,
              "required": ["item", "evidence"],
              "properties": {"item": _INTENT_TEXT, "evidence": _INTENT_TEXT}}
INTENT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["lines", "unasked_changes", "undelivered"],
    "properties": {
        "lines": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["line", "status", "evidence"],
            "properties": {"line": _INTENT_TEXT, "evidence": _INTENT_TEXT,
                           "status": {"type": "string",
                                      "enum": list(INTENT_STATUSES)}}}},
        "unasked_changes": {"type": "array", "items": _EVIDENCED},
        "undelivered": {"type": "array", "items": _EVIDENCED}}}


def intent_schema(final=False):
    """The intent checker's reply schema: ``INTENT_SCHEMA``, plus a required
    ``files_reviewed`` list for the final check, which must cover every changed path as
    the build reviewer's must (``parse_intent_reply``)."""
    schema = json.loads(json.dumps(INTENT_SCHEMA))
    if final:
        schema["required"].append("files_reviewed")
        schema["properties"]["files_reviewed"] = {"type": "array",
                                                  "items": {"type": "string"}}
    return schema


def _collapsed(text):
    return " ".join(str(text).split())


def _review_path(path):
    """A path as the build reviewer's coverage check reads it (``loop.Run._covered``)."""
    return path.strip().replace("\\", "/").removeprefix("./")


def parse_intent_reply(reply, expected_lines, changed_files=None):
    """Check an intent checker's reply against its schema and the brief's lines.

    ``expected_lines`` are the Goal and Constraint line texts in the brief's order
    (``brief.intent_lines``). Every one must have exactly one entry, in that order, its
    ``line`` the same text once runs of whitespace are collapsed; nothing else may be
    there. Every evidence and every list item must be non-blank text, and a status one
    of ``INTENT_STATUSES``. With ``changed_files`` given (the final check), the reply
    must hold ``files_reviewed`` naming every one, by the build reviewer's rule.

    Returns the reply with ``files_reviewed`` left out. Anything else raises
    ``ReplyError``: a refused reply is never a pass and never ``cannot tell``, and the
    run goes down the error path (plan 10.6 item 2)."""
    who = "the intent checker's reply"
    if isinstance(reply, str):
        try:
            reply = json.loads(reply)
        except ValueError as exc:
            raise ReplyError(f"{who} is not JSON: {exc}") from exc
    schema = intent_schema(final=changed_files is not None)
    if not isinstance(reply, dict):
        raise ReplyError(f"{who} is not a JSON object")
    if set(reply) != set(schema["required"]):
        raise ReplyError(f"{who} must hold exactly: {', '.join(schema['required'])}")
    entries = reply["lines"]
    if not isinstance(entries, list):
        raise ReplyError(f"{who}: lines is not a list")
    fields = ("line", "status", "evidence")
    for number, entry in enumerate(entries, 1):
        where = f"{who}: line entry {number}"
        if not isinstance(entry, dict) or set(entry) != set(fields):
            raise ReplyError(f"{where} must hold exactly: {', '.join(fields)}")
        if not all(isinstance(entry[name], str) for name in fields):
            raise ReplyError(f"{where}: every field must be text")
        if entry["status"] not in INTENT_STATUSES:
            raise ReplyError(f"{where}: status {entry['status']!r} is not one of "
                             f"{', '.join(INTENT_STATUSES)}")
        if not entry["evidence"].strip():
            raise ReplyError(f"{where} gives no evidence")
    expected = [_collapsed(text) for text in expected_lines]
    given = [_collapsed(entry["line"]) for entry in entries]
    if given != expected:
        missing = [text for text in expected if text not in given]
        extra = [text for text in given if text not in expected]
        detail = ("leaves out: " + "; ".join(missing[:3]) if missing
                  else "adds: " + "; ".join(extra[:3]) if extra
                  else "does not give the lines once each in the brief's order")
        raise ReplyError(f"{who} does not repeat the brief's {len(expected)} Goal and "
                         f"Constraint lines one entry each, in order: it {detail}")
    for key in ("unasked_changes", "undelivered"):
        items = reply[key]
        if not isinstance(items, list):
            raise ReplyError(f"{who}: {key} is not a list")
        for number, item in enumerate(items, 1):
            where = f"{who}: {key} item {number}"
            if not isinstance(item, dict) or set(item) != {"item", "evidence"}:
                raise ReplyError(f"{where} must hold exactly: item, evidence")
            if not all(isinstance(item[name], str) and item[name].strip()
                       for name in ("item", "evidence")):
                raise ReplyError(f"{where} must give the item and its evidence as text")
    result = {key: reply[key] for key in INTENT_SCHEMA["required"]}
    if changed_files is not None:
        listed = reply["files_reviewed"]
        if not isinstance(listed, list) or not all(isinstance(p, str) for p in listed):
            raise ReplyError(f"{who}: files_reviewed is not a list of paths")
        seen = {_review_path(path) for path in listed}
        missing = [path for path in changed_files if path not in seen]
        if missing:
            raise ReplyError(f"{who} did not list these changed paths as reviewed: "
                             + ", ".join(missing[:20]) + (" ..." if len(missing) > 20
                                                          else ""))
    return result


# ----------------------------------------------------------------------- rules


def decide(ledger):
    """Apply the three rules, in order, to everything recorded so far.

    Returns ``(stop_reason, evidence)`` or None to carry on. Called after every pass.
    """
    scoped = [f["label"] for f in ledger.findings.values() if f["action"] == DECLINED_SCOPE]
    if scoped:
        return OUT_OF_SCOPE, {"declined_scope": scoped}

    disputed = [(f["label"], f["reraises"]) for f in ledger.findings.values()
                if f["reraises"]
                and ledger.findings[f["reraises"]]["action"] == DECLINED_MERITS]
    if disputed:
        return DISAGREEMENT, {"reraises_of_declined_merits":
                              [{"finding": a, "reraises": b} for a, b in disputed]}

    # For each round, the roots raised again that round by a finding whose direct
    # target the builder had marked fixed.
    by_round = {}
    for f in ledger.findings.values():
        target = f["reraises"]
        if target and ledger.findings[target]["action"] == FIXED:
            by_round.setdefault(f["round"], set()).add(ledger.root(f["label"]))
    for round_no in sorted(by_round):
        repeated = by_round[round_no] & by_round.get(round_no - 1, set())
        if repeated:
            return REOPENED, {"rounds": [round_no - 1, round_no],
                              "first_findings": sorted(repeated)}
    return None


def after_first_pass(diff_is_empty):
    """B2's empty-diff stop: the builder's first pass changed nothing."""
    return (NO_CHANGE_MADE, {"diff": "empty"}) if diff_is_empty else None


def after_review(round_no, cap, new_findings):
    """After a review round: ``max-rounds`` when the cap is reached with findings
    still coming, else None. No findings at all is not decided here: the loop then goes
    on to the final intent check (plan section 10.6), which ends the run ``clean`` or
    ``intent-failed``."""
    if new_findings and round_no >= cap:
        return MAX_ROUNDS, {"round": round_no, "cap": cap,
                            "open": [f["label"] for f in new_findings]}
    return None


# ---------------------------------------------------------------- run state


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def record_rounds(state, ledger):
    """Write the ledger's per-round record into ``state``."""
    state["rounds"] = ledger.state_rounds()
    return state


def record_stop(state, reason, evidence=None, *, now=None):
    """End the run with one stop reason. Earlier stop reasons stay in the list."""
    if reason not in STOP_REASONS:
        raise ValueError(f"not a stop reason: {reason!r}")
    if state.get("status") == ENDED:
        raise ValueError("the run has already ended; resume it before stopping again")
    state["stop_reasons"].append({"reason": reason, "evidence": evidence,
                                  "at": now or _now()})
    state["status"] = ENDED
    state["pause"] = None
    return state


def enter_pause(state, pause):
    """Pause a running run. A pause writes no stop reason."""
    if pause not in PAUSES:
        raise ValueError(f"not a pause: {pause!r}")
    if state.get("status") != RUNNING:
        raise ValueError(f"only a running run can pause (this one is {state.get('status')})")
    state["status"] = PAUSED
    state["pause"] = pause
    return state


def continue_run(state):
    """Continue a paused run, or resume an ended one whose stop reason allows it."""
    status = state.get("status")
    if status == PAUSED:
        state["status"], state["pause"] = RUNNING, None
        return state
    if status == ENDED:
        last = state["stop_reasons"][-1]["reason"] if state["stop_reasons"] else None
        if last not in RESUMABLE:
            raise ValueError(f"a run that ended {last} cannot be resumed; only "
                             f"{', '.join(RESUMABLE)} can")
        state["status"] = RUNNING
        return state
    raise ValueError("the run is already running")
