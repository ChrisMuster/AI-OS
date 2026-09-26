#!/usr/bin/env python3
"""
limits.py - usage-limit readings for review-orchestration runs (Stage A2).

A run must stop cleanly when either provider runs out, and must never spend the
allowance the user needs for interactive work. This module turns what each SDK
reports into one ``Reading`` per provider, decides whether a run may start its next
step (``check_ceiling``), and recognises a limit hit mid-session so the loop can
catch it as its own class (``UsageLimitError``) rather than as a generic failure.

Three kinds of reading, named by ``Reading.source``:

  reported        the provider gave a usage percentage (and, where it has one, the
                  reset time of the window that percentage belongs to)
  limit-reported  the provider says ordinary usage is not allowed now. The run stops
                  whatever any percentage says.
  unavailable     the provider reported nothing usable. The ceiling is not applied
                  to it for this run, and the mid-session fallback is its only
                  detection. Never carried over to a later run.

Codex is read through the app-server request ``account/rateLimits/read`` on an
initialised client, which makes no model call. Claude reports its limits only as
rate-limit events inside a session, so its reading is built up from those events by
``ClaudeUsage`` and is ``unavailable`` until the first one arrives.

The SDK imports are made inside the functions that need them, so the pure parts of
this module load without either SDK installed.

The rules are plan section 10.10; the choices made where it is silent are recorded
in ``workflows/review-orchestration/scripts/CONTEXT.md``.
"""

from dataclasses import dataclass
from typing import Any

REPORTED = "reported"
LIMIT_REPORTED = "limit-reported"
UNAVAILABLE = "unavailable"

CODEX = "codex"
CLAUDE = "claude"

CODEX_METHOD = "account/rateLimits/read"
CODEX_PARAMS = {"excludeResetCreditDetails": True}

# Codex's turn-error code for an exhausted plan allowance. ``rateLimitExceeded`` is
# short-term throttling of requests, not the allowance, and is deliberately not here.
_CODEX_LIMIT_CODES = {"usageLimitExceeded"}
# Claude's assistant-message error for a hit limit.
_CLAUDE_LIMIT_ERRORS = {"rate_limit"}


@dataclass(frozen=True)
class Reading:
    """One provider's usage at one moment.

    ``percent`` is the highest usage across the windows the provider reported, and
    ``resets_at`` (Unix seconds) and ``window`` belong to that same window. Both are
    None where the provider gave none. ``reason`` says why a reading is
    ``limit-reported`` or ``unavailable``. ``raw`` is what the provider sent, for the
    run report.
    """

    provider: str
    source: str
    percent: float | None = None
    resets_at: int | None = None
    window: str | None = None
    reason: str | None = None
    raw: Any = None

    def summary(self):
        """One line for the run report."""
        if self.source == UNAVAILABLE:
            return f"{_title(self.provider)} usage not reported: {self.reason}"
        usage = "unavailable" if self.percent is None else f"{_format_percent(self.percent)}%"
        reset = "unavailable" if self.resets_at is None else str(self.resets_at)
        where = f" ({self.window})" if self.window else ""
        text = f"{_title(self.provider)} usage {usage}{where}, resets at {reset}"
        if self.source == LIMIT_REPORTED:
            text += f"; limit reached: {self.reason}"
        return text


@dataclass(frozen=True)
class LimitStop:
    """Why a run must stop as ``usage-limit`` before its next step."""

    provider: str
    reading: Reading
    reason: str


class UsageLimitError(Exception):
    """A provider hit its usage limit mid-session. Caught as its own class, so the run
    ends as ``usage-limit`` rather than ``error``."""

    def __init__(self, provider, detail, resets_at=None):
        super().__init__(f"{_title(provider)} usage limit reached: {detail}")
        self.provider = provider
        self.detail = detail
        self.resets_at = resets_at


def _title(provider):
    return {CODEX: "Codex", CLAUDE: "Claude"}.get(provider, provider)


def _format_percent(value):
    return f"{value:g}"


# ------------------------------------------------------------------------- Codex


def read_codex(client):
    """Read Codex's usage through an initialised ``CodexClient``. No model call.

    A JSON-RPC error, or a response that is not a valid ``GetAccountRateLimitsResponse``,
    is an ``unavailable`` reading carrying the error text. A closed transport is not a
    usage answer at all and is raised for the caller to treat as a provider failure.
    """
    from openai_codex.errors import CodexError, TransportClosedError
    from openai_codex.generated.v2_all import GetAccountRateLimitsResponse
    from pydantic import ValidationError

    try:
        response = client.request(CODEX_METHOD, dict(CODEX_PARAMS),
                                  response_model=GetAccountRateLimitsResponse)
    except TransportClosedError:
        raise
    except CodexError as exc:
        # JsonRpcError is a CodexError, and so is the client's own refusal of a
        # response that is not a JSON object.
        return Reading(CODEX, UNAVAILABLE, reason=f"{type(exc).__name__}: {exc}")
    except ValidationError as exc:
        # The whole error, flattened to one line: its first line only names the model,
        # and the field that failed, with the value it got, is on the lines after it.
        detail = " ".join(str(exc).split()) or type(exc).__name__
        return Reading(CODEX, UNAVAILABLE,
                       reason=f"the response failed validation: {detail}")
    return classify_codex(response)


def classify_codex(response):
    """Turn a validated ``GetAccountRateLimitsResponse`` into a Reading.

    Checked in the plan's order: ``ordinaryUsageAllowed`` false first, whatever the
    windows say; then the highest window present; otherwise no usable answer.
    """
    raw = response.model_dump(by_alias=True, mode="json", exclude_none=True)
    snapshot = response.rate_limits
    windows = []
    for name, window in (("primary", snapshot.primary), ("secondary", snapshot.secondary)):
        if window is not None:
            label = name
            if window.window_duration_mins is not None:
                label = f"{name}, {window.window_duration_mins} min"
            windows.append((label, window.used_percent, window.resets_at))
    highest = _highest(windows)

    if response.ordinary_usage_allowed is False:
        label, percent, resets = highest if highest else (None, None, None)
        return Reading(CODEX, LIMIT_REPORTED, percent=percent, resets_at=resets,
                       window=label, reason="ordinaryUsageAllowed is false", raw=raw)
    if highest:
        label, percent, resets = highest
        return Reading(CODEX, REPORTED, percent=percent, resets_at=resets,
                       window=label, raw=raw)
    return Reading(CODEX, UNAVAILABLE,
                   reason="the response carried neither usage window", raw=raw)


class RunUsage:
    """The Codex readings of one run. Create one per run and pass every Codex reading
    through ``codex``.

    Plan section 10.10: a negative result is recorded per run and, for that run, the
    ceiling is not applied to Codex. So the first ``unavailable`` reading is kept, and
    every later ``reported`` reading in the same run comes back as ``unavailable``,
    naming that first failure and still carrying its own figures for the report, so
    ``check_ceiling`` skips it. A ``limit-reported`` reading is never softened: Codex
    saying ordinary usage is not allowed stops the run whatever came before. A new run
    starts from a new object, so nothing is carried over.
    """

    def __init__(self):
        self.codex_negative = None

    def codex(self, reading):
        if reading.source == UNAVAILABLE:
            if self.codex_negative is None:
                self.codex_negative = reading
            return reading
        if reading.source == REPORTED and self.codex_negative is not None:
            return Reading(CODEX, UNAVAILABLE, percent=reading.percent,
                           resets_at=reading.resets_at, window=reading.window,
                           reason=("an earlier reading in this run was unavailable "
                                   f"({self.codex_negative.reason}), so the ceiling is "
                                   "not applied to Codex for the rest of the run"),
                           raw=reading.raw)
        return reading


def codex_turn_error(error):
    """Return a UsageLimitError if a Codex turn error is a usage limit, else None.

    Accepts the SDK's ``TurnError`` or its JSON form (a dict with ``codexErrorInfo``).
    """
    if error is None:
        return None
    if isinstance(error, dict):
        info = error.get("codexErrorInfo")
        message = error.get("message", "")
    else:
        info = getattr(error, "codex_error_info", None)
        message = getattr(error, "message", "")
    code = _error_code(info)
    if code in _CODEX_LIMIT_CODES:
        return UsageLimitError(CODEX, f"{code}: {message}".rstrip(": "))
    return None


def _error_code(info):
    """The string code inside a CodexErrorInfo, its enum, or its JSON form."""
    info = getattr(info, "root", info)
    info = getattr(info, "value", info)
    return info if isinstance(info, str) else None


# ------------------------------------------------------------------------ Claude


class ClaudeUsage:
    """Claude's usage, built up from the rate-limit events of a session.

    Each event carries a status for the account as a whole and, in its raw form, a
    ``unifiedWindows`` map of every window's utilisation and reset time. The top-level
    ``utilization`` field is often empty (it was in the B1 transcripts), so the windows
    in the raw form are read first and the top-level field is the fallback. The latest
    event's status is the current status.

    **Paid overage stops the run** (the user's rule, 2026-09-25). Overage is there to
    let work in progress reach a safe state when the included allowance runs out, not
    to carry on spending. So an event saying the account is using overage (raw
    ``isUsingOverage`` true, or an event for the ``overage`` window itself) makes the
    reading ``limit-reported``: the run stops before its next step, and the reset time
    reported is the included window's, which is when the run can resume.
    """

    def __init__(self):
        self._windows = {}
        self._status = None
        self._status_window = None
        self._status_resets = None
        self._overage = False
        self._raw = None

    def observe(self, event):
        """Record one ``RateLimitEvent`` (or its ``RateLimitInfo``)."""
        info = getattr(event, "rate_limit_info", event)
        raw = getattr(info, "raw", None) or {}
        self._raw = raw
        self._status = getattr(info, "status", None)
        self._status_window = getattr(info, "rate_limit_type", None)
        self._status_resets = getattr(info, "resets_at", None)
        using_overage = raw.get("isUsingOverage") if isinstance(raw, dict) else None
        self._overage = using_overage is True or self._status_window == "overage"

        unified = raw.get("unifiedWindows") if isinstance(raw, dict) else None
        if isinstance(unified, dict) and unified:
            for name, window in unified.items():
                if isinstance(window, dict):
                    self._windows[name] = (window.get("utilization"), window.get("resetsAt"))
        elif self._status_window is not None:
            self._windows[self._status_window] = (getattr(info, "utilization", None),
                                                  self._status_resets)

    def reading(self):
        if self._status is None:
            return Reading(CLAUDE, UNAVAILABLE, reason="no rate-limit event received yet")
        # The overage window is not part of the included allowance, so it never sets
        # the percentage or the time the run can resume.
        windows = [(name, round(util * 100, 1), resets)
                   for name, (util, resets) in self._windows.items()
                   if name != "overage"
                   and isinstance(util, (int, float)) and not isinstance(util, bool)]
        highest = _highest(windows)
        # Overage is checked before a rejection: a rejected event on the overage window
        # still resumes when the included allowance resets, not when overage does.
        if self._overage:
            window, resets = (highest[0], highest[2]) if highest else (None, None)
            # The event's own reset stands in only for the fullest window itself; another
            # window's reset would not free it, so the resume time is left unknown.
            if resets is None and highest and self._status_window == highest[0]:
                resets = self._status_resets
            return Reading(CLAUDE, LIMIT_REPORTED,
                           percent=highest[1] if highest else None, resets_at=resets,
                           window=window, reason="using paid overage", raw=self._raw)
        if self._status == "rejected":
            percent = highest[1] if highest else None
            resets = self._status_resets
            window = self._status_window
            if resets is None and highest:
                window, resets = highest[0], highest[2]
            return Reading(CLAUDE, LIMIT_REPORTED, percent=percent, resets_at=resets,
                           window=window, reason="rate-limit status is rejected",
                           raw=self._raw)
        if highest:
            label, percent, resets = highest
            return Reading(CLAUDE, REPORTED, percent=percent, resets_at=resets,
                           window=label, raw=self._raw)
        return Reading(CLAUDE, UNAVAILABLE,
                       reason="the rate-limit events carried no utilisation", raw=self._raw)


def claude_message_error(message):
    """Return a UsageLimitError if a Claude assistant message reports a hit limit."""
    error = getattr(message, "error", None)
    if error in _CLAUDE_LIMIT_ERRORS:
        return UsageLimitError(CLAUDE, f"assistant message error {error}")
    return None


# ----------------------------------------------------------------------- ceiling


def _highest(windows):
    """The (label, percent, resets_at) with the highest percent.

    On a tie the later reset wins, since waiting for the earlier one would not free
    the other window; an unknown reset counts as earlier than any known one.
    """
    if not windows:
        return None
    return max(windows, key=lambda w: (w[1], -1 if w[2] is None else w[2]))


def check_ceiling(readings, ceiling):
    """Decide whether the next step may start. Returns a LimitStop, or None to go on.

    Any ``limit-reported`` reading stops the run first; then any ``reported`` reading
    at or above the ceiling does. An ``unavailable`` reading is never a stop: for that
    provider the mid-session fallback is the only detection. Readings are checked in
    the order given.
    """
    readings = list(readings)
    for reading in readings:
        if reading.source == LIMIT_REPORTED:
            return LimitStop(reading.provider, reading,
                             f"{_title(reading.provider)} reports its usage limit "
                             f"reached ({reading.reason})")
    for reading in readings:
        if (reading.source == REPORTED and reading.percent is not None
                and reading.percent >= ceiling):
            return LimitStop(reading.provider, reading,
                             f"{_title(reading.provider)} usage "
                             f"{_format_percent(reading.percent)}% is at or above the "
                             f"{_format_percent(ceiling)}% ceiling")
    return None
