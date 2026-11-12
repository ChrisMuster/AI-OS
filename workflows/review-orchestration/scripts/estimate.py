#!/usr/bin/env python3
"""
estimate.py - the size estimate's calculation (plan 10.10 item 10, R67-1 to R69-2).

At the approval pause a run's ``plan-report.md`` gains a "Size estimate" section; this
module works out what goes in it, and writes nothing. It makes no model call and reads
only the run records under the runs folder it is given.

  plan size     ``len(loop.render_plan(merged_plan))``, the text the builder is given
                as the approved plan, counted in code points and worked out the same way
                for the current run and every earlier one, never read from a figure an
                earlier run stored (R68-2).
  usable runs   an earlier run counts only with the same builder as the current run, a
                merged plan and a build step whose every segment is known: the completed
                step's record carries ``segments``, or the build ran as one segment (a
                ``build.json``, no ``build-partial.json`` and no stop reason naming the
                build). Turns and window use are judged apart (R68-1): turns need every
                segment's ``num_turns``, window use every segment's five-hour readings,
                so a one-segment Claude run from before segment records counts for turns
                only. Every run left out of either is listed with why.
  Codex runs    (part (e)) window use only, since a Codex turn is the whole step and
                has no turn count or limit (R71-5). A segment counts when its first
                reading is the answered before-step one, its last the answered turn-end
                one (the stopping reading for a turn the watch stopped and interrupted),
                every answered reading has its percentage, read time and reset time (or
                a reset could not be ruled out), and ``limits.codex_reset_between``
                finds no reset between each two answered readings, worked out again here
                rather than taken from the stored lists; a failed reading between two
                answered ones is passed over (R72-2). Its use is its last percentage less its first. A
                run from before segment records counts when its build ran as one segment
                and the summary lines of the readings before the build and before the
                next step are both five-hour readings with the same reset time and a
                percentage that did not fall (R71-3). Any reset leaves the run out
                (R71-4).
  comparison    for each measure, the three usable runs nearest in size by
                ``abs(ln a - ln b)``, ties to the newer run; the range is the smallest to
                the largest of the three. Under three usable runs the measure has no
                estimate, only its count.
  fit           "fits a fresh window" when the top of the window range is below
                ``usage_ceiling_percent``; "fits now" when that top plus the saved
                reading's percentage is below it, unknown with no saved reading, one for
                another window than the five-hour one (the user's option B, 2026-11-11)
                or an expired one (its reset time passed or unknown, R68-3). The saved
                reading is ``claude_last_reading`` for a Claude builder and
                ``codex_last_five_hour``, always a five-hour one, for a Codex builder.
                Dated with when it was worked out and the reading it used (R69-1).
  turn limit    the top of the turn range rounded up to the next 10, never below the
                run's current limit; a whole ten stays as it is.
  accuracy      for each run in a measure's comparison set, the range the same method
                gives from the other usable runs and whether the run's own figure fell
                inside it; with fewer than three others, not yet available (R69-2).

A Claude run is compared only with earlier Claude runs and a Codex run only with earlier
Codex runs. The estimate is advice: nothing here stops, refuses or changes a run. ``report_lines`` is the "Size estimate" section
``planning.write_plan_report`` writes at the approval pause, and ``fit_lines`` the fit
advice ``loop.approve`` prints again for ``--approve --dry-run`` (part (d2)).
"""

import json
import math
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import limits
import loop

TURNS = "turns"
WINDOW = "window"
MEASURES = (TURNS, WINDOW)
COMPARISON_SIZE = 3
BUILD = "build"
FIVE_HOUR = "five_hour"
BUILDERS = {limits.CLAUDE: "Claude", limits.CODEX: "Codex"}
# A Codex reading's summary line, as ``limits.Reading.summary`` writes it for a reported
# reading whose highest window has a known length (the runs before segment records).
CODEX_SUMMARY = re.compile(r"Codex usage (?P<percent>\d+(?:\.\d+)?)% \((?P<name>[^,()]+), "
                           r"(?P<minutes>\d+) min\), resets at (?P<resets>\d+)")
NO_CODEX_TURNS = "a Codex turn has no turn count"


def plan_size(merged_plan):
    """The plan's size: the rendered approved plan's length in code points."""
    return len(loop.render_plan(merged_plan))


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _merged_plan(state):
    synthesis = (state.get("planning") or {}).get("synthesis")
    merged = synthesis.get("merged_plan") if isinstance(synthesis, dict) else None
    return merged if isinstance(merged, dict) and merged else None


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool)


def window_rise(five_hour):
    """One segment's window use: its rise from first reading to last, with a drop
    between readings (a reset) starting a new piece of the sum. None unless the
    readings are all there and numbers."""
    if not isinstance(five_hour, dict):
        return None
    first, last, drops = five_hour.get("first"), five_hour.get("last"), five_hour.get("drops")
    if not (_number(first) and _number(last) and isinstance(drops, list)):
        return None
    total, start = 0.0, first
    for drop in drops:
        if not (isinstance(drop, dict) and _number(drop.get("from"))
                and _number(drop.get("to"))):
            return None
        total += drop["from"] - start
        start = drop["to"]
    return round(total + last - start, 1)


def codex_segment_rise(segment):
    """One Codex segment's window use, ``(rise, None)``, or ``(None, why)`` where the
    segment cannot be used: see the module docstring's "Codex runs"."""
    five_hour = segment.get("five_hour") if isinstance(segment, dict) else None
    readings = five_hour.get("readings") if isinstance(five_hour, dict) else None
    if not (isinstance(readings, list) and len(readings) >= 2
            and all(isinstance(reading, dict) for reading in readings)):
        return None, "a segment of its build has no Codex five-hour readings"
    first, last = readings[0], readings[-1]
    if first.get("at") != "before-step" or "failed" in first:
        return None, "a segment of its build has no answered reading from before the step"
    if segment.get("watch_interrupt_failed"):
        # The turn ran on past the stopping reading, with nothing read after it.
        return None, "a segment of its build ran on after the watch could not stop it"
    ending = "watch" if segment.get("watch_stop") else "turn-end"
    if last.get("at") != ending or "failed" in last:
        return None, "a segment of its build has no answered reading at its end"
    answered = [reading for reading in readings if "failed" not in reading]
    if not all(_number(reading.get(key)) for reading in answered
               for key in ("percent", "read_at", "resets_at")):
        # ``codex_reset_between`` would answer None here: a reset it cannot rule out.
        return None, ("a segment of its build has a reading with no known percentage, "
                      "read time or reset time, so a reset cannot be ruled out")
    for earlier, later in zip(answered, answered[1:]):
        if limits.codex_reset_between(earlier, later):
            return None, "a segment of its build crossed a five-hour reset"
    return round(last["percent"] - first["percent"], 1), None


def _codex_summary(entry):
    """A ``readings`` entry's Codex summary line read back, ``(reading, None)`` with the
    percentage, window length and reset time, or ``(None, why)``."""
    text = entry.get("codex") if isinstance(entry, dict) else None
    if not isinstance(text, str):
        return None, "a Codex reading around its build could not be read"
    if text.startswith("Codex usage not reported"):
        return None, "a Codex reading around its build was not reported"
    match = CODEX_SUMMARY.fullmatch(text)
    if match is None:
        return None, "a Codex reading around its build could not be read"
    return {"percent": float(match["percent"]), "minutes": int(match["minutes"]),
            "resets_at": int(match["resets"])}, None


def codex_one_segment_rise(state):
    """A Codex build that ran as one segment, from before segment records: the window
    use between the reading before the build and the one before the next step,
    ``(rise, None)``, or ``(None, why)`` (R71-3)."""
    entries = [entry for entry in state.get("readings") or [] if isinstance(entry, dict)]
    before = [index for index, entry in enumerate(entries) if entry.get("step") == BUILD]
    if not before:
        return None, "it recorded no Codex reading before its build"
    if before[-1] + 1 >= len(entries):
        return None, "it recorded no Codex reading after its build"
    pair = []
    for entry in (entries[before[-1]], entries[before[-1] + 1]):
        reading, why = _codex_summary(entry)
        if reading is None:
            return None, why
        if limits.CODEX_WINDOWS.get(reading["minutes"]) != FIVE_HOUR:
            window = limits.CODEX_WINDOWS.get(reading["minutes"]) or (
                f"{reading['minutes']}-minute")
            return None, (f"a Codex reading around its build was for the {window} "
                          "window, not the five-hour one")
        pair.append(reading)
    first, second = pair
    if second["resets_at"] != first["resets_at"]:
        return None, "its five-hour reset time moved during its build"
    if second["percent"] < first["percent"]:
        return None, "its five-hour percentage fell during its build"
    return round(second["percent"] - first["percent"], 1), None


def _build_step(state):
    for step in state.get("steps") or []:
        if isinstance(step, dict) and step.get("step") == BUILD and step.get("role") == "builder":
            return step
    return None


def _stopped_in_build(state):
    for stop in state.get("stop_reasons") or []:
        evidence = stop.get("evidence") if isinstance(stop, dict) else None
        if isinstance(evidence, dict) and evidence.get("step") == BUILD:
            return True
    return False


def _one_segment_turns(run_dir):
    """The turns of a build that ran as one segment, from ``build.json``'s result
    message; None where there is none."""
    path = run_dir / "transcripts" / f"{BUILD}.json"
    if not path.is_file():
        return None
    messages = _read_json(path)
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("_type") == "ResultMessage":
            value = message.get("num_turns")
            return value if _count(value) else None
    return None


def _one_segment_why(run_dir, state):
    """Why a build with no segment records cannot be taken as one segment, or None."""
    if (run_dir / "transcripts" / f"{BUILD}-partial.json").exists():
        return "its build was interrupted before segment records were kept"
    if _stopped_in_build(state):
        return "a stop reason names its build step"
    if not (run_dir / "transcripts" / f"{BUILD}.json").is_file():
        return "it has no completed build"
    return None


def _codex_figures(run_dir, state, figures, leave_out):
    """A Codex run's window use (part (e)); it is never usable for turns."""
    leave_out(NO_CODEX_TURNS, (TURNS,))
    step = _build_step(state)
    segments = step.get("segments") if step else None
    if isinstance(segments, list) and segments:
        rises = []
        for segment in segments:
            rise, why = codex_segment_rise(segment)
            if rise is None:
                return leave_out(why, (WINDOW,))
            rises.append(rise)
        figures[WINDOW] = round(sum(rises), 1)
        return figures
    why = _one_segment_why(run_dir, state)
    if why is not None:
        return leave_out(why, (WINDOW,))
    rise, why = codex_one_segment_rise(state)
    if rise is None:
        return leave_out(why, (WINDOW,))
    figures[WINDOW] = rise
    return figures


def run_figures(run_dir, builder=limits.CLAUDE):
    """One earlier run's figures: ``{"run", "size", "turns", "window", "why"}``, where
    ``turns`` or ``window`` is None when the run cannot be used for that measure and
    ``why`` names, for each measure it is left out of, the reason. Only a run built by
    ``builder`` is used."""
    run_dir = Path(run_dir)
    figures = {"run": run_dir.name, "size": None, TURNS: None, WINDOW: None, "why": {}}

    def leave_out(reason, measures=MEASURES):
        for measure in measures:
            figures["why"].setdefault(measure, reason)
        return figures

    try:
        state = _read_json(run_dir / "state.json")
        settings = _read_json(run_dir / "settings.json")
    except (OSError, ValueError) as exc:
        return leave_out(f"its record could not be read ({type(exc).__name__})")
    built_by = ((settings.get("roles") if isinstance(settings, dict) else None) or {}).get("builder")
    if built_by != builder:
        return leave_out(f"its builder was {built_by or 'not recorded'}, not "
                         f"{BUILDERS.get(builder, builder)}")
    merged = _merged_plan(state)
    if merged is None:
        return leave_out("it recorded no merged plan")
    figures["size"] = plan_size(merged)
    if figures["size"] <= 0:
        return leave_out("its plan renders as no text")
    if builder == limits.CODEX:
        return _codex_figures(run_dir, state, figures, leave_out)
    step = _build_step(state)
    segments = step.get("segments") if step else None
    if isinstance(segments, list) and segments:
        turns = [segment.get("num_turns") if isinstance(segment, dict) else None
                 for segment in segments]
        if all(_count(value) for value in turns):
            figures[TURNS] = sum(turns)
        else:
            leave_out("a segment of its build has no result's turn count", (TURNS,))
        rises = [window_rise(segment.get("five_hour")) if isinstance(segment, dict)
                 else None for segment in segments]
        if all(rise is not None for rise in rises):
            figures[WINDOW] = round(sum(rises), 1)
        else:
            leave_out("a segment of its build has no five-hour readings", (WINDOW,))
        return figures
    why = _one_segment_why(run_dir, state)
    if why is not None:
        return leave_out(why)
    try:
        turns = _one_segment_turns(run_dir)
    except (OSError, ValueError) as exc:
        return leave_out(f"its build transcript could not be read ({type(exc).__name__})")
    if turns is None:
        return leave_out("its build has no result message")
    figures[TURNS] = turns
    return leave_out("it ran as one segment with no five-hour readings", (WINDOW,))


def earlier_runs(runs_dir, run_id):
    """Every run folder under ``runs_dir`` older than ``run_id`` (run ids sort by the
    time they were made), a measurement's folder left out, oldest first."""
    runs_dir = Path(runs_dir)
    if not runs_dir.is_dir():
        return []
    return sorted(path for path in runs_dir.iterdir()
                  if path.is_dir() and not path.name.startswith("measure-")
                  and (path / "state.json").is_file() and path.name < run_id)


def nearest(size, candidates, measure):
    """The comparison set: the ``COMPARISON_SIZE`` candidates nearest ``size`` by the
    absolute difference of the natural logarithms, ties going to the newer run."""
    newest_first = sorted(candidates, key=lambda figures: figures["run"], reverse=True)
    ranked = sorted(newest_first,
                    key=lambda figures: abs(math.log(size) - math.log(figures["size"])))
    return [{"run": figures["run"], "size": figures["size"], "value": figures[measure]}
            for figures in ranked[:COMPARISON_SIZE]]


def value_range(comparison):
    values = [entry["value"] for entry in comparison]
    return [min(values), max(values)]


def accuracy(comparison, usable, measure):
    """For each run in the comparison set, the range from the other usable runs and
    whether its own figure fell inside; not available with fewer than three others."""
    others_count = len(usable) - 1
    if others_count < COMPARISON_SIZE:
        return {"available": False, "others": others_count}
    checks = []
    for entry in comparison:
        others = [figures for figures in usable if figures["run"] != entry["run"]]
        low, high = value_range(nearest(entry["size"], others, measure))
        checks.append({"run": entry["run"], "value": entry["value"], "range": [low, high],
                       "inside": low <= entry["value"] <= high})
    return {"available": True, "others": others_count, "checks": checks}


def turn_limit(top, current):
    """The recommended ``--max-turns``: ``top`` rounded up to the next 10 (a whole ten
    stays), never below ``current``."""
    recommended = max(math.ceil(top / 10) * 10, current)
    return {"recommended": recommended, "current": current,
            "current_enough": recommended == current}


def _saved_name(provider):
    return "Codex five-hour reading" if provider == limits.CODEX else "Claude reading"


def fit(top, state, settings, now, provider=limits.CLAUDE):
    """The fit advice for a window range whose top is ``top``, dated (R69-1), from the
    builder ``provider``'s saved reading. "Fits now" is None, with why, when there is
    no saved reading or it has expired (its reset time passed or unknown, R68-3)."""
    ceiling = settings["usage_ceiling_percent"]
    key = "codex_last_five_hour" if provider == limits.CODEX else "claude_last_reading"
    saved = state.get(key) or None
    advice = {"worked_out_at": now, "ceiling": ceiling, "top": top,
              "fresh_window": top < ceiling, "now": None, "now_unknown": None,
              "provider": provider,
              "reading_percent": None, "read_at": None, "resets_at": None}
    if not isinstance(saved, dict):
        advice["now_unknown"] = f"there is no saved {_saved_name(provider)}"
        return advice
    advice.update(reading_percent=saved.get("percent"), read_at=saved.get("read_at"),
                  resets_at=saved.get("resets_at"))
    resets = saved.get("resets_at")
    # A saved Codex reading is the five-hour window's by how it is kept (part (e)).
    if provider == limits.CLAUDE and saved.get("window") != FIVE_HOUR:
        # The rise being added is the five-hour window's, so only a five-hour reading
        # can be added to it; another window's percentage would mean nothing here.
        advice["now_unknown"] = (f"the saved reading is for the "
                                 f"{saved.get('window') or 'unnamed'} window, not the "
                                 "five-hour one")
    elif not _number(resets):
        advice["now_unknown"] = "the saved reading's reset time is unknown"
    elif resets <= now:
        advice["now_unknown"] = "the saved reading's reset time has passed"
    elif not _number(saved.get("percent")):
        advice["now_unknown"] = "the saved reading has no percentage"
    else:
        advice["now"] = top + saved["percent"] < ceiling
    return advice


def estimate(runs_dir, run_id, state, settings, *, now=None):
    """The size estimate for run ``run_id``, whose ``state.json`` and resolved settings
    are ``state`` and ``settings``, from the earlier runs under ``runs_dir``. ``now`` is
    seconds since the epoch, the clock the saved readings use."""
    now = time.time() if now is None else now
    builder = (settings.get("roles") or {}).get("builder")
    result = {"run": run_id, "builder": builder, "made": False, "reason": None,
              "worked_out_at": now}
    if builder not in BUILDERS:
        result["reason"] = (f"the run is built by {builder or 'an unnamed builder'}, and "
                            "only a Claude or Codex builder is estimated")
        return result
    merged = _merged_plan(state)
    if merged is None:
        result["reason"] = "the run has no merged plan"
        return result
    size = plan_size(merged)
    figures = [run_figures(path, builder) for path in earlier_runs(runs_dir, run_id)]
    current = loop.builder_turn_limit(SimpleNamespace(state=state, settings=settings))
    # A Codex turn is the whole step, with no turn count or limit (R71-5).
    measures = MEASURES if builder == limits.CLAUDE else (WINDOW,)
    result.update(made=True, plan_size=size, earlier_runs=len(figures),
                  measures=list(measures))
    for measure in measures:
        usable = [entry for entry in figures if entry[measure] is not None]
        left_out = [{"run": entry["run"], "why": entry["why"][measure]}
                    for entry in figures if entry[measure] is None]
        section = {"usable": len(usable), "left_out": left_out, "estimate": False}
        if len(usable) >= COMPARISON_SIZE:
            comparison = nearest(size, usable, measure)
            low, high = value_range(comparison)
            section.update(estimate=True, comparison=comparison, range=[low, high],
                           accuracy=accuracy(comparison, usable, measure))
            if measure == TURNS:
                section["turn_limit"] = turn_limit(high, current)
            else:
                section["fit"] = fit(high, state, settings, now, builder)
        elif measure == TURNS:
            section["turn_limit"] = {"recommended": None, "current": current,
                                     "current_enough": None}
        result[measure] = section
    return result


# ------------------------------------------------------------------ the report


STARTER = "python workflows/review-orchestration/scripts/start.py"
ADVICE_ONLY = "The estimate is advice: it never stops, refuses or changes a run."
CODEX_TURNS_LINE = ("- A Codex turn is the whole build step: it has no turn count and "
                    "no turn limit, so there is no turn range and no `--max-turns` "
                    "advice.")


def when(seconds):
    """A time in seconds since the epoch as local time with its offset, or "unknown"."""
    if not _number(seconds):
        return "unknown"
    return datetime.fromtimestamp(seconds).astimezone().isoformat(timespec="minutes")


def _figure(measure, value):
    return f"{value} turns" if measure == TURNS else f"{value}% of the five-hour window"


def _left_out_lines(section):
    reasons = Counter(entry["why"] for entry in section["left_out"])
    if not reasons:
        return ["- Left out: none."]
    return [f"- Left out: {len(section['left_out'])} earlier run(s): "
            + "; ".join(f"{why} ({count})" for why, count in sorted(reasons.items())) + "."]


def _accuracy_lines(measure, accuracy):
    if not accuracy["available"]:
        return [f"- Accuracy: not yet available ({accuracy['others']} other usable run(s) "
                f"for each compared run; {COMPARISON_SIZE} needed)."]
    lines = ["- Accuracy, each compared run against the range the other usable runs give:"]
    for check in accuracy["checks"]:
        low, high = check["range"]
        lines.append(f"  - {check['run']}: {_figure(measure, check['value'])} against "
                     f"{low} to {high}, {'inside' if check['inside'] else 'outside'}.")
    return lines


def _measure_lines(result, measure, title):
    section = result[measure]
    lines = [f"### {title}", ""]
    if not section["estimate"]:
        lines.append(f"- No estimate: {section['usable']} usable earlier run(s), "
                     f"{COMPARISON_SIZE} needed.")
    else:
        low, high = section["range"]
        lines.append(f"- Range: {low} to {high}, from the {COMPARISON_SIZE} usable runs "
                     "nearest in plan size:")
        lines += [f"  - {entry['run']}: plan size {entry['size']}, "
                  f"{_figure(measure, entry['value'])}." for entry in section["comparison"]]
        lines += _accuracy_lines(measure, section["accuracy"])
    return lines + _left_out_lines(section)


def fit_lines(advice, run_id):
    """The fit advice in words, with the split or wait recommendation and its date."""
    name = _saved_name(advice.get("provider", limits.CLAUDE))
    reading = (f"no saved {name}" if advice["read_at"] is None and
               advice["reading_percent"] is None else
               f"the saved {name} of {advice['reading_percent']}%, read "
               f"{when(advice['read_at'])}, resetting {when(advice['resets_at'])}")
    lines = [f"- Worked out {when(advice['worked_out_at'])} from {reading}, against the "
             f"run's usage ceiling of {advice['ceiling']}%."]
    if not advice["fresh_window"]:
        lines.append(f"- It does not fit a fresh window: the top of the range, "
                     f"{advice['top']}%, is not below the ceiling. Split the brief into "
                     "chunks that each end tested and committable, each its own brief and "
                     f"run: replace this run with the first chunk (`{STARTER} --amend "
                     f"{run_id} <first-chunk-brief>`), or stop it (`{STARTER} --stop "
                     f"{run_id}`).")
    elif advice["now"] is None:
        lines.append(f"- It fits a fresh window. Whether it fits now is unknown: "
                     f"{advice['now_unknown']}, so no wait is recommended.")
    elif advice["now"]:
        lines.append("- It fits a fresh window, and it fits now.")
    else:
        lines.append("- It fits a fresh window but not now: approve after the saved "
                     f"reading's reset time, {when(advice['resets_at'])}.")
    lines.append("- This covers the five-hour window only. The ceiling also stops a run "
                 "on any other window's reading, the weekly one included, which no "
                 "estimate covers.")
    return lines


def report_lines(result):
    """The plan report's "Size estimate" section for an ``estimate`` result."""
    lines = ["## Size estimate", ""]
    if not result["made"]:
        return lines + [f"No estimate is made: {result['reason']}.", "", ADVICE_ONLY, ""]
    run_id = result["run"]
    lines += [f"Plan size: {result['plan_size']} characters of the approved plan's text. "
              f"Earlier runs looked at: {result['earlier_runs']}. Worked out "
              f"{when(result['worked_out_at'])}.", ""]
    if TURNS not in result.get("measures", MEASURES):
        lines += ["### Turns", "", CODEX_TURNS_LINE]
    else:
        lines += _turn_lines(result, run_id)
    lines += [""] + _measure_lines(result, WINDOW, "Five-hour window use")
    if result[WINDOW]["estimate"]:
        lines += fit_lines(result[WINDOW]["fit"], run_id)
    else:
        lines.append("- No fit or split advice without a window range.")
    return lines + ["", ADVICE_ONLY, ""]


def _turn_lines(result, run_id):
    lines = _measure_lines(result, TURNS, "Turns")
    limit = result[TURNS]["turn_limit"]
    if limit["recommended"] is None:
        lines.append(f"- The turn limit stays at {limit['current']} unless you give "
                     "`--max-turns N`.")
    elif limit["current_enough"]:
        lines.append(f"- The current turn limit, {limit['current']}, is enough.")
    else:
        lines.append(f"- Recommended turn limit: {limit['recommended']} (now "
                     f"{limit['current']}): `{STARTER} --approve {run_id} --max-turns "
                     f"{limit['recommended']}`.")
    return lines


def dry_run_lines(result):
    """What ``--approve --dry-run`` prints: the fit advice worked out again now."""
    if not result["made"]:
        return [f"Size estimate: none is made: {result['reason']}."]
    if not result[WINDOW]["estimate"]:
        return [f"Size estimate: no fit advice: {result[WINDOW]['usable']} usable earlier "
                f"run(s) for window use, {COMPARISON_SIZE} needed."]
    return ["Size estimate, the fit advice worked out again now:"] + fit_lines(
        result[WINDOW]["fit"], result["run"])
