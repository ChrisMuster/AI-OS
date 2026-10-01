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
  reviewer  its structured reply (``REVIEW_SCHEMA``) marks a finding that raises an
            earlier one again with ``reraises: <label>``; an empty ``reraises`` means
            the finding is new. A title holding a label, or a ``reraises`` naming a
            label the run has not issued, is a malformed reply.

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
# A run made before the intent check exists (sub-stage A4) with nothing left to fix.
# It never ends ``clean``, since nothing has checked it for intent; A4 retires this.
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
    open_set = set(open_labels)
    actions = {}
    for number, entry in enumerate(entries, 1):
        where = f"builder action {number}"
        if not isinstance(entry, dict) or set(entry) != {"label", "action", "note"}:
            raise ReplyError(f"{where} must hold exactly: label, action, note")
        name, action, note = entry["label"], entry["action"], entry["note"]
        if not isinstance(name, str) or name not in open_set:
            raise ReplyError(f"{where} names {name!r}, which is not an open finding")
        if name in actions:
            raise ReplyError(f"{where}: {name} is given more than one action")
        if action not in ACTIONS:
            raise ReplyError(f"{where}: {name} has action {action!r}; the action must "
                             f"be one of {', '.join(ACTIONS)}")
        if not isinstance(note, str):
            raise ReplyError(f"{where}: {name}'s note is not text")
        actions[name] = (action, note)
    missing = [name for name in open_labels if name not in actions]
    if missing:
        raise ReplyError(f"the builder gave no action for: {', '.join(missing)}")
    return actions


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
    still coming, else None. No findings at all is not decided here: ``clean`` also
    needs the final intent check (plan section 10.6), and until that exists the loop
    ends such a run ``reviewed-clean``."""
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
