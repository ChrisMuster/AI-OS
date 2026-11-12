#!/usr/bin/env python3
"""Tests for the size estimate's calculation (plan 10.10 item 10, ``estimate.py``), on
fake run folders in a temporary directory: which earlier runs are usable for turns and
for window use and why the rest are not, a segment's window rise, the comparison set,
the ranges, the turn limit, the fit advice and its expiry rule, the accuracy check, and
that nothing is written; and the Codex half (part (e)): a Codex segment's window use,
the older Codex runs read from their summary lines, the builders kept apart, the fit
from the saved Codex five-hour reading, and a report with no turn part.

    python workflows/review-orchestration/tests/test_estimate.py
"""

import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_fixtures import *  # noqa: E402,F401,F403 - the loop suites' shared fixtures

# The module planning.py and loop.py use, so a patch here reaches their calls.
estimate = planning.estimate

NOW = 1_800_000_000
CURRENT = "20261201-000000-ffff"
SETTINGS = {"roles": {"builder": "claude", "reviewer": "codex"},
            "usage_ceiling_percent": 80, "max_turns": {"builder": 80, "reviewer": 200}}


def plan(size_hint):
    """A merged plan whose rendered text grows with ``size_hint``."""
    return {"summary": "x" * size_hint, "confidence": "high",
            "steps": [{"action": "Do it.", "files": ["work/a.txt"], "verification": "v"}],
            "risks": [], "assumptions": []}


def segment(turns=10, first=10.0, last=20.0, drops=(), number=1):
    five_hour = None if first is None else {"first": first, "last": last,
                                             "readings": 2, "drops": list(drops),
                                             "resets_at": NOW + 3600}
    return {"segment": number, "ended": "completed", "num_turns": turns,
            "assistant_messages": 12, "five_hour": five_hour}


CODEX_SETTINGS = {**SETTINGS, "roles": {"builder": "codex", "reviewer": "claude"}}
RESETS = NOW + 3600


def codex_reading(at, percent, read_at=NOW - 600, resets_at=RESETS):
    """One reading as a Codex segment keeps it (``loop._codex_segment_reading``)."""
    return {"at": at, "read_at": read_at, "percent": percent, "resets_at": resets_at}


def codex_segment(first=10.0, last=20.0, *, middle=(), number=1, **extra):
    """A Codex segment from its before-step reading to its turn-end one, with any
    ``middle`` readings between; the reset time holds, so no reset is found."""
    readings = [codex_reading("before-step", first, NOW - 600), *middle,
                codex_reading("turn-end", last, NOW)]
    return {"segment": number, "ended": "completed", "num_turns": None,
            "assistant_messages": None,
            "five_hour": {"readings": readings, "resets": [], "unknown_resets": []},
            **extra}


def summary(percent, minutes=300, resets=RESETS, source=limits.REPORTED, reason=None):
    """A ``readings`` entry's Codex summary line as ``limits.Reading`` writes it."""
    return limits.Reading(limits.CODEX, source, percent=percent, resets_at=resets,
                          window=f"primary, {minutes} min", reason=reason).summary()


def state_for(merged, *, segments=None, stops=(), reading=None, **extra):
    state = {"planning": {"synthesis": {"merged_plan": merged}} if merged else {},
             "steps": [], "stop_reasons": list(stops), **extra}
    if segments is not None:
        state["steps"].append({"step": "build", "role": "builder", "provider": "claude",
                               "segments": segments})
    if reading is not None:
        state["claude_last_reading"] = reading
    return state


def make_fake_run(runs, run_id, merged, *, builder="claude", segments=None,
                  build_turns=None, partial=False, stops=(), **extra):
    """A fake earlier run folder under ``runs``. ``segments`` gives the completed build
    step's segment records; ``build_turns`` writes a one-segment ``build.json`` whose
    result message has that ``num_turns`` (``False`` for one with no result message)."""
    folder = Path(runs) / run_id
    (folder / "transcripts").mkdir(parents=True)
    (folder / "settings.json").write_text(json.dumps(
        {**SETTINGS, "roles": {"builder": builder}}), encoding="utf-8", newline="\n")
    state = state_for(merged, segments=segments, stops=stops, **extra)
    if build_turns is not None:
        state["steps"].append({"step": "build", "role": "builder", "provider": builder})
        messages = [{"_type": "AssistantMessage"}]
        if build_turns is not False:
            messages.append({"_type": "ResultMessage", "num_turns": build_turns})
        (folder / "transcripts" / "build.json").write_text(
            json.dumps(messages), encoding="utf-8", newline="\n")
    if partial:
        (folder / "transcripts" / "build-partial.json").write_text(
            "[]", encoding="utf-8", newline="\n")
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8", newline="\n")
    return folder


class EstimateCase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.runs = Path(self._tmp.name) / "runs"
        self.runs.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def make_run(self, run_id, merged, **kwargs):
        return make_fake_run(self.runs, run_id, merged, **kwargs)

    def run_estimate(self, merged=None, *, reading=None, **extra):
        state = state_for(plan(1000) if merged is None else merged, reading=reading,
                          **extra)
        return estimate.estimate(self.runs, CURRENT, state, SETTINGS, now=NOW)


class WindowRiseTests(unittest.TestCase):

    def test_positive_a_rise_with_no_drop_is_last_less_first(self):
        self.assertEqual(estimate.window_rise({"first": 12.0, "last": 31.5, "drops": []}),
                         19.5)

    def test_positive_a_drop_starts_a_new_piece_of_the_sum(self):
        # 10 to 60, a reset to 5, then 5 to 20: 50 + 15, not 20 - 10.
        rise = estimate.window_rise({"first": 10.0, "last": 20.0,
                                     "drops": [{"from": 60.0, "to": 5.0}]})
        self.assertEqual(rise, 65.0)

    def test_rejection_missing_or_malformed_readings_give_none(self):
        self.assertIsNone(estimate.window_rise(None))
        self.assertIsNone(estimate.window_rise({"first": 10.0, "drops": []}))
        self.assertIsNone(estimate.window_rise({"first": True, "last": 20.0, "drops": []}))
        self.assertIsNone(estimate.window_rise({"first": 10.0, "last": 20.0,
                                                "drops": [{"from": 60.0}]}))


class RunFiguresTests(EstimateCase):

    def test_positive_segments_add_up_turns_and_window_use(self):
        folder = self.make_run("20261101-000000-a001", plan(500), segments=[
            segment(turns=30, first=10.0, last=50.0, number=1),
            segment(turns=25, first=2.0, last=30.0, drops=[{"from": 40.0, "to": 1.0}],
                    number=2)])
        figures = estimate.run_figures(folder)
        self.assertEqual(figures["turns"], 55)
        self.assertEqual(figures["window"], 40.0 + 38.0 + 29.0)
        self.assertEqual(figures["why"], {})

    def test_rejection_a_segment_without_turns_leaves_turns_out_only(self):
        folder = self.make_run("20261101-000000-a001", plan(500), segments=[
            segment(turns=30), segment(turns=None, number=2)])
        figures = estimate.run_figures(folder)
        self.assertIsNone(figures["turns"])
        self.assertIn("turn count", figures["why"]["turns"])
        self.assertEqual(figures["window"], 20.0)

    def test_rejection_a_segment_without_readings_leaves_window_out_only(self):
        folder = self.make_run("20261101-000000-a001", plan(500), segments=[
            segment(turns=30), segment(turns=5, first=None, number=2)])
        figures = estimate.run_figures(folder)
        self.assertEqual(figures["turns"], 35)
        self.assertIsNone(figures["window"])
        self.assertIn("five-hour readings", figures["why"]["window"])

    def test_positive_one_segment_run_counts_for_turns_only(self):
        folder = self.make_run("20261101-000000-a001", plan(500), build_turns=82)
        figures = estimate.run_figures(folder)
        self.assertEqual(figures["turns"], 82)
        self.assertIsNone(figures["window"])
        self.assertEqual(figures["why"]["window"],
                         "it ran as one segment with no five-hour readings")

    def test_rejection_an_interrupted_build_without_segments_is_left_out(self):
        folder = self.make_run("20261101-000000-a001", plan(500), build_turns=82,
                               partial=True)
        figures = estimate.run_figures(folder)
        self.assertEqual((figures["turns"], figures["window"]), (None, None))
        self.assertIn("interrupted", figures["why"]["turns"])

    def test_rejection_a_stop_naming_the_build_leaves_the_run_out(self):
        folder = self.make_run("20261101-000000-a001", plan(500), build_turns=82,
                               stops=[{"reason": "usage-limit",
                                       "evidence": {"step": "build"}}])
        figures = estimate.run_figures(folder)
        self.assertIsNone(figures["turns"])
        self.assertEqual(figures["why"]["turns"], "a stop reason names its build step")

    def test_negative_a_stop_naming_another_step_does_not(self):
        folder = self.make_run("20261101-000000-a001", plan(500), build_turns=82,
                               stops=[{"reason": "clean",
                                       "evidence": {"step": "intent-final"}},
                                      {"reason": "max-rounds", "evidence": {"round": 3}}])
        self.assertEqual(estimate.run_figures(folder)["turns"], 82)

    def test_rejection_a_build_without_a_result_message_is_left_out(self):
        folder = self.make_run("20261101-000000-a001", plan(500), build_turns=False)
        figures = estimate.run_figures(folder)
        self.assertEqual(figures["why"]["turns"], "its build has no result message")

    def test_rejection_a_run_that_never_built_says_so(self):
        folder = self.make_run("20261101-000000-a001", plan(500))
        self.assertEqual(estimate.run_figures(folder)["why"]["turns"],
                         "it has no completed build")

    def test_rejection_a_codex_builder_or_no_merged_plan_is_left_out(self):
        codex = estimate.run_figures(self.make_run("20261101-000000-a001", plan(500),
                                                   builder="codex", build_turns=40))
        self.assertEqual(codex["why"]["turns"], "its builder was codex, not Claude")
        none = estimate.run_figures(self.make_run("20261101-000000-a002", None,
                                                  build_turns=40))
        self.assertEqual(none["why"]["window"], "it recorded no merged plan")

    def test_positive_plan_size_is_worked_out_never_read_from_a_stored_figure(self):
        folder = self.make_run("20261101-000000-a001", plan(500), build_turns=40,
                               plan_size=7)
        self.assertEqual(estimate.run_figures(folder)["size"],
                         len(loop.render_plan(plan(500))))


class ComparisonTests(EstimateCase):

    def test_positive_only_older_runs_count_and_measurements_are_skipped(self):
        self.make_run("20261101-000000-a001", plan(500), build_turns=40)
        self.make_run(CURRENT, plan(500), build_turns=40)
        self.make_run("20261202-000000-a002", plan(500), build_turns=40)
        (self.runs / "measure-20261101-000000-a003").mkdir()
        (self.runs / "measure-20261101-000000-a003" / "state.json").write_text(
            "{}", encoding="utf-8", newline="\n")
        self.assertEqual([p.name for p in estimate.earlier_runs(self.runs, CURRENT)],
                         ["20261101-000000-a001"])

    def test_positive_nearest_is_by_logarithm_not_by_difference(self):
        # Against 1000, by difference the nearest three are 500, 1600 and 100; by the
        # logarithm they are 1600, 500 and 3000.
        candidates = [{"run": f"r{size}", "size": size, "turns": size}
                      for size in (100, 500, 1600, 3000)]
        chosen = estimate.nearest(1000, candidates, "turns")
        self.assertEqual([entry["size"] for entry in chosen], [1600, 500, 3000])

    def test_positive_ties_go_to_the_newer_run(self):
        candidates = [{"run": name, "size": 1000, "turns": 1}
                      for name in ("20261101-a", "20261103-c", "20261102-b", "20261104-d")]
        chosen = estimate.nearest(1000, candidates, "turns")
        self.assertEqual([entry["run"] for entry in chosen],
                         ["20261104-d", "20261103-c", "20261102-b"])


class EstimateTests(EstimateCase):

    def three_turn_runs(self, turns=(40, 70, 55)):
        for number, value in enumerate(turns, 1):
            self.make_run(f"20261101-000000-a00{number}", plan(900 + number),
                          build_turns=value)

    def test_positive_a_codex_builder_is_estimated_for_window_use_only(self):
        # Part (e): Claude runs are left out of a Codex run's estimate, and it has no
        # turn part at all.
        self.three_turn_runs()
        result = estimate.estimate(self.runs, CURRENT, state_for(plan(1000)),
                                   CODEX_SETTINGS, now=NOW)
        self.assertTrue(result["made"])
        self.assertEqual(result["measures"], ["window"])
        self.assertNotIn("turns", result)
        self.assertEqual({entry["why"] for entry in result["window"]["left_out"]},
                         {"its builder was claude, not Codex"})

    def test_rejection_another_builder_gets_no_estimate(self):
        result = estimate.estimate(self.runs, CURRENT, state_for(plan(1000)),
                                   {**SETTINGS, "roles": {"builder": "gemini"}}, now=NOW)
        self.assertFalse(result["made"])
        self.assertIn("only a Claude or Codex builder is estimated", result["reason"])

    def test_rejection_too_few_runs_gives_no_range_and_its_count(self):
        self.three_turn_runs(turns=(40, 70))
        result = self.run_estimate()
        self.assertTrue(result["made"])
        self.assertEqual((result["turns"]["estimate"], result["turns"]["usable"]),
                         (False, 2))
        self.assertNotIn("range", result["turns"])
        self.assertIsNone(result["turns"]["turn_limit"]["recommended"])
        self.assertEqual(result["turns"]["turn_limit"]["current"], 80)
        self.assertEqual((result["window"]["estimate"], result["window"]["usable"]),
                         (False, 0))
        self.assertNotIn("fit", result["window"])
        self.assertEqual(len(result["window"]["left_out"]), 2)

    def test_positive_three_usable_runs_give_the_smallest_to_largest(self):
        self.three_turn_runs()
        result = self.run_estimate()
        self.assertEqual(result["turns"]["range"], [40, 70])
        self.assertEqual(len(result["turns"]["comparison"]), 3)
        self.assertEqual(result["plan_size"], len(loop.render_plan(plan(1000))))

    def test_positive_a_run_is_never_given_a_figure_it_lacks(self):
        # Three runs with turns only and three with both: the window comparison takes
        # only the three with readings, though the others are nearer in size.
        self.three_turn_runs()
        for number, rise in enumerate((20.0, 35.0, 30.0), 4):
            self.make_run(f"20261101-000000-a00{number}", plan(3000 * number),
                          segments=[segment(turns=50, first=10.0, last=10.0 + rise)])
        result = self.run_estimate()
        self.assertEqual(result["window"]["range"], [20.0, 35.0])
        self.assertEqual({entry["run"] for entry in result["window"]["comparison"]},
                         {"20261101-000000-a004", "20261101-000000-a005",
                          "20261101-000000-a006"})
        self.assertEqual(result["turns"]["usable"], 6)

    def test_positive_turn_limit_rounds_up_to_the_next_ten(self):
        self.three_turn_runs(turns=(40, 82, 55))
        self.assertEqual(self.run_estimate()["turns"]["turn_limit"],
                         {"recommended": 90, "current": 80, "current_enough": False})

    def test_positive_a_whole_ten_stays_and_the_current_limit_is_enough(self):
        self.three_turn_runs(turns=(40, 80, 55))
        self.assertEqual(self.run_estimate()["turns"]["turn_limit"]["recommended"], 80)
        self.assertTrue(self.run_estimate()["turns"]["turn_limit"]["current_enough"])

    def test_positive_never_below_the_current_limit_given_by_max_turns(self):
        self.three_turn_runs(turns=(40, 82, 55))
        result = self.run_estimate(max_turns_builder={"value": 150, "command": "--run",
                                                      "at": "x"})
        self.assertEqual(result["turns"]["turn_limit"],
                         {"recommended": 150, "current": 150, "current_enough": True})

    def test_positive_turn_limit_helper_bounds(self):
        self.assertEqual(estimate.turn_limit(81, 80)["recommended"], 90)
        self.assertEqual(estimate.turn_limit(90, 80)["recommended"], 90)
        self.assertEqual(estimate.turn_limit(12, 80)["recommended"], 80)


class FitTests(unittest.TestCase):

    def reading(self, percent=30.0, resets_at=NOW + 600):
        return {"source": "reported", "percent": percent, "window": "five_hour",
                "resets_at": resets_at, "read_at": NOW - 60}

    def advice(self, top, reading):
        return estimate.fit(top, state_for(plan(10), reading=reading), SETTINGS, NOW)

    def test_positive_fits_a_fresh_window_and_now(self):
        advice = self.advice(45.0, self.reading(30.0))
        self.assertEqual((advice["fresh_window"], advice["now"]), (True, True))

    def test_rejection_fits_a_fresh_window_but_not_now(self):
        advice = self.advice(45.0, self.reading(40.0))
        self.assertEqual((advice["fresh_window"], advice["now"]), (True, False))

    def test_rejection_the_top_at_the_ceiling_does_not_fit_a_fresh_window(self):
        self.assertFalse(self.advice(80.0, self.reading(0.0))["fresh_window"])

    def test_rejection_fits_now_is_unknown_without_a_live_reading(self):
        weekly = {**self.reading(), "window": "seven_day"}
        cases = {"there is no saved Claude reading": None,
                 "the saved reading is for the seven_day window, not the five-hour one":
                     weekly,
                 "the saved reading's reset time has passed": self.reading(resets_at=NOW),
                 "the saved reading's reset time is unknown": self.reading(resets_at=None)}
        for why, reading in cases.items():
            with self.subTest(why=why):
                advice = self.advice(10.0, reading)
                self.assertIsNone(advice["now"])
                self.assertEqual(advice["now_unknown"], why)

    def test_positive_the_advice_is_dated_with_the_reading_it_used(self):
        advice = self.advice(45.0, self.reading(30.0))
        self.assertEqual({key: advice[key] for key in
                          ("worked_out_at", "reading_percent", "read_at", "resets_at")},
                         {"worked_out_at": NOW, "reading_percent": 30.0,
                          "read_at": NOW - 60, "resets_at": NOW + 600})


class AccuracyTests(EstimateCase):

    def test_rejection_not_yet_available_with_fewer_than_three_others(self):
        for number, value in enumerate((40, 70, 55), 1):
            self.make_run(f"20261101-000000-a00{number}", plan(900 + number),
                          build_turns=value)
        accuracy = self.run_estimate()["turns"]["accuracy"]
        self.assertEqual(accuracy, {"available": False, "others": 2})

    def test_positive_each_compared_run_is_checked_against_the_others(self):
        sizes_turns = [(100, 40), (200, 50), (400, 60), (800, 200)]
        for number, (size, turns) in enumerate(sizes_turns, 1):
            self.make_run(f"20261101-000000-a00{number}", plan(size), build_turns=turns)
        result = self.run_estimate(plan(400))
        accuracy = result["turns"]["accuracy"]
        self.assertTrue(accuracy["available"])
        by_run = {check["run"]: check for check in accuracy["checks"]}
        self.assertEqual(set(by_run), {entry["run"] for entry in result["turns"]["comparison"]})
        # Each run's range is the min to max of the three others, here all of them.
        self.assertEqual(by_run["20261101-000000-a003"]["range"], [40, 200])
        self.assertTrue(by_run["20261101-000000-a003"]["inside"])
        self.assertEqual(by_run["20261101-000000-a004"]["range"], [40, 60])
        self.assertFalse(by_run["20261101-000000-a004"]["inside"])


class ReadOnlyTests(EstimateCase):

    def test_positive_an_estimate_writes_nothing(self):
        for number, value in enumerate((40, 70, 55), 1):
            self.make_run(f"20261101-000000-a00{number}", plan(900 + number),
                          segments=[segment(turns=value)])

        def listing():
            return {path: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in self.runs.rglob("*") if path.is_file()}

        before = listing()
        self.assertTrue(self.run_estimate()["made"])
        self.assertEqual(listing(), before)


# ----------------------------------------------------- the Codex half (part (e))


class CodexSegmentRiseTests(unittest.TestCase):

    def rise(self, segment):
        return estimate.codex_segment_rise(segment)

    def test_positive_a_segment_is_its_last_percentage_less_its_first(self):
        self.assertEqual(self.rise(codex_segment(12.0, 31.5)), (19.5, None))

    def test_positive_watch_readings_between_are_checked_and_passed(self):
        middle = [codex_reading("watch", 15.0, NOW - 300)]
        self.assertEqual(self.rise(codex_segment(10.0, 22.0, middle=middle)), (12.0, None))

    def test_positive_a_failed_reading_in_the_middle_is_tolerated(self):
        middle = [{"at": "watch", "read_at": NOW - 300, "failed": "a timeout"}]
        self.assertEqual(self.rise(codex_segment(10.0, 22.0, middle=middle)), (12.0, None))

    def test_positive_a_watch_stopped_segment_ends_on_its_stopping_reading(self):
        segment = codex_segment(10.0, 70.0, watch_stop={"step": "build"})
        segment["five_hour"]["readings"][-1]["at"] = "watch"
        self.assertEqual(self.rise(segment), (60.0, None))

    def test_rejection_a_reset_by_a_falling_percentage_or_a_moved_reset_time(self):
        fell = codex_segment(10.0, 22.0, middle=[codex_reading("watch", 40.0, NOW - 300)])
        moved = codex_segment(10.0, 22.0, middle=[
            codex_reading("watch", 15.0, NOW - 300, resets_at=RESETS + 5 * 3600)])
        for segment in (fell, moved):
            with self.subTest(segment=segment):
                self.assertEqual(self.rise(segment),
                                 (None, "a segment of its build crossed a five-hour reset"))

    def test_rejection_an_unknown_time_leaves_a_reset_that_cannot_be_ruled_out(self):
        for key in ("read_at", "resets_at", "percent"):
            with self.subTest(key=key):
                segment = codex_segment(10.0, 22.0, middle=[codex_reading("watch", 15.0)])
                segment["five_hour"]["readings"][1][key] = None
                rise, why = self.rise(segment)
                self.assertIsNone(rise)
                self.assertIn("a reset cannot be ruled out", why)

    def test_rejection_a_missing_or_failed_first_reading(self):
        missing = codex_segment(10.0, 22.0)
        missing["five_hour"]["readings"][0]["at"] = "watch"
        failed = codex_segment(10.0, 22.0)
        failed["five_hour"]["readings"][0] = {"at": "before-step", "read_at": NOW,
                                              "failed": "no five-hour window"}
        for segment in (missing, failed):
            with self.subTest(segment=segment):
                self.assertEqual(self.rise(segment)[1], "a segment of its build has no "
                                 "answered reading from before the step")

    def test_rejection_a_missing_or_failed_last_reading(self):
        # A watch reading last with no stop is a turn killed before its turn-end reading.
        killed = codex_segment(10.0, 22.0)
        killed["five_hour"]["readings"][-1]["at"] = "watch"
        failed = codex_segment(10.0, 22.0)
        failed["five_hour"]["readings"][-1] = {"at": "turn-end", "read_at": NOW,
                                               "failed": "a timeout"}
        for segment in (killed, failed):
            with self.subTest(segment=segment):
                self.assertEqual(self.rise(segment)[1], "a segment of its build has no "
                                 "answered reading at its end")

    def test_rejection_a_turn_the_watch_could_not_interrupt(self):
        segment = codex_segment(10.0, 70.0, watch_stop={"step": "build"},
                                watch_interrupt_failed=[{"error": "RuntimeError: gone",
                                                         "stop": {"step": "build"}}])
        segment["five_hour"]["readings"][-1]["at"] = "watch"
        self.assertEqual(self.rise(segment)[1], "a segment of its build ran on after the "
                         "watch could not stop it")

    def test_rejection_too_few_or_no_readings(self):
        lone = codex_segment(10.0, 22.0)
        lone["five_hour"]["readings"] = lone["five_hour"]["readings"][:1]
        for segment in (lone, {"five_hour": None}, None):
            with self.subTest(segment=segment):
                self.assertEqual(self.rise(segment), (None, "a segment of its build has "
                                 "no Codex five-hour readings"))


class CodexRunFiguresTests(EstimateCase):

    def codex_run(self, run_id="20261101-000000-e001", **kwargs):
        return estimate.run_figures(self.make_run(run_id, plan(500), builder="codex",
                                                  **kwargs), "codex")

    def readings(self, before, after):
        return [{"step": "plan-review", "codex": summary(1.0)},
                {"step": "build", "codex": before}, {"step": "review-1", "codex": after}]

    def test_positive_segments_add_up_window_use_and_turns_are_never_counted(self):
        figures = self.codex_run(segments=[codex_segment(10.0, 30.0),
                                           codex_segment(30.0, 45.5, number=2)])
        self.assertEqual(figures["window"], 35.5)
        self.assertIsNone(figures["turns"])
        self.assertEqual(figures["why"], {"turns": estimate.NO_CODEX_TURNS})

    def test_rejection_one_bad_segment_leaves_the_run_out_with_its_reason(self):
        bad = codex_segment(10.0, 22.0, number=2,
                            middle=[codex_reading("watch", 40.0, NOW - 300)])
        figures = self.codex_run(segments=[codex_segment(), bad])
        self.assertIsNone(figures["window"])
        self.assertEqual(figures["why"]["window"],
                         "a segment of its build crossed a five-hour reset")

    def test_positive_an_older_run_uses_the_readings_either_side_of_its_build(self):
        figures = self.codex_run(build_turns=1,
                                 readings=self.readings(summary(10.0), summary(27.5)))
        self.assertEqual((figures["window"], figures["turns"]), (17.5, None))

    def test_positive_a_resumed_build_uses_the_last_reading_before_it(self):
        # A resume reads again before the build; the earlier attempt never started it.
        readings = [{"step": "build", "codex": summary(5.0)},
                    *self.readings(summary(10.0), summary(27.5))[1:]]
        self.assertEqual(self.codex_run(build_turns=1, readings=readings)["window"], 17.5)

    def test_rejection_an_older_run_with_a_reading_it_cannot_use(self):
        limit = summary(10.0, source=limits.LIMIT_REPORTED,
                        reason="ordinaryUsageAllowed is false")
        cases = {
            "a Codex reading around its build was for the weekly window, not the "
            "five-hour one": (summary(10.0), summary(30.0, minutes=10080)),
            "its five-hour reset time moved during its build":
                (summary(10.0), summary(30.0, resets=RESETS + 5 * 3600)),
            "its five-hour percentage fell during its build":
                (summary(30.0), summary(10.0)),
            "a Codex reading around its build could not be read": (limit, summary(30.0)),
            "a Codex reading around its build was not reported":
                (summary(10.0), limits.Reading(limits.CODEX, limits.UNAVAILABLE,
                                               reason="a timeout").summary()),
        }
        for number, (why, (before, after)) in enumerate(cases.items(), 1):
            with self.subTest(why=why):
                figures = self.codex_run(f"20261101-000000-f00{number}", build_turns=1,
                                         readings=self.readings(before, after))
                self.assertIsNone(figures["window"])
                self.assertEqual(figures["why"]["window"], why)

    def test_rejection_an_older_run_with_no_reading_after_its_build(self):
        figures = self.codex_run(build_turns=1, readings=[
            {"step": "build", "codex": summary(10.0)}])
        self.assertEqual(figures["why"]["window"],
                         "it recorded no Codex reading after its build")

    def test_rejection_an_older_run_whose_build_a_stop_names(self):
        figures = self.codex_run(build_turns=1,
                                 readings=self.readings(summary(10.0), summary(27.5)),
                                 stops=[{"reason": "usage-limit",
                                         "evidence": {"step": "build"}}])
        self.assertEqual(figures["why"]["window"], "a stop reason names its build step")

    def test_rejection_the_builders_are_kept_apart_both_ways(self):
        claude = self.make_run("20261101-000000-e002", plan(500),
                               segments=[segment(turns=30)])
        self.assertEqual(estimate.run_figures(claude, "codex")["why"]["window"],
                         "its builder was claude, not Codex")
        codex = self.make_run("20261101-000000-e003", plan(500), builder="codex",
                              segments=[codex_segment()])
        self.assertEqual(estimate.run_figures(codex)["why"]["window"],
                         "its builder was codex, not Claude")


class CodexFitTests(unittest.TestCase):

    def advice(self, top, saved, **extra):
        state = state_for(plan(10), codex_last_five_hour=saved, **extra)
        return estimate.fit(top, state, SETTINGS, NOW, "codex")

    def saved(self, percent=30.0, resets_at=NOW + 600):
        return {"percent": percent, "resets_at": resets_at, "read_at": NOW - 60}

    def test_positive_fits_now_from_the_saved_codex_reading(self):
        advice = self.advice(45.0, self.saved(30.0))
        self.assertEqual((advice["fresh_window"], advice["now"]), (True, True))
        self.assertEqual((advice["reading_percent"], advice["resets_at"]),
                         (30.0, NOW + 600))

    def test_rejection_not_now_and_never_from_the_claude_reading(self):
        claude = {"source": "reported", "percent": 0.0, "window": "five_hour",
                  "resets_at": NOW + 600, "read_at": NOW - 60}
        advice = self.advice(45.0, self.saved(40.0), claude_last_reading=claude)
        self.assertEqual((advice["now"], advice["reading_percent"]), (False, 40.0))

    def test_rejection_fits_now_is_unknown_without_a_live_codex_reading(self):
        cases = {"there is no saved Codex five-hour reading": None,
                 "the saved reading's reset time has passed": self.saved(resets_at=NOW),
                 "the saved reading's reset time is unknown": self.saved(resets_at=None)}
        for why, saved in cases.items():
            with self.subTest(why=why):
                advice = self.advice(10.0, saved)
                self.assertIsNone(advice["now"])
                self.assertEqual(advice["now_unknown"], why)


class CodexReportTests(EstimateCase):

    def codex_runs(self, rises=(20.0, 35.0, 30.0)):
        for number, rise in enumerate(rises, 1):
            self.make_run(f"20261101-000000-g00{number}", plan(900 + number),
                          builder="codex", segments=[codex_segment(10.0, 10.0 + rise)])

    def text(self, saved=None):
        state = state_for(plan(1000), codex_last_five_hour=saved)
        return "\n".join(estimate.report_lines(
            estimate.estimate(self.runs, CURRENT, state, CODEX_SETTINGS, now=NOW)))

    def test_positive_a_codex_report_has_no_turn_part_and_a_window_range(self):
        self.codex_runs()
        text = self.text({"percent": 10.0, "resets_at": NOW + 600, "read_at": NOW - 60})
        self.assertIn("### Turns\n\n" + estimate.CODEX_TURNS_LINE, text)
        self.assertEqual(text.count("No estimate"), 0)
        self.assertNotIn("--max-turns N", text)
        self.assertIn("Range: 20.0 to 35.0", text)
        self.assertIn(f"Worked out {estimate.when(NOW)} from the saved Codex five-hour "
                      "reading of 10.0%", text)
        self.assertIn("It fits a fresh window, and it fits now.", text)

    def test_rejection_too_few_codex_runs_give_no_window_advice(self):
        self.codex_runs(rises=(20.0, 35.0))
        text = self.text()
        self.assertIn(estimate.CODEX_TURNS_LINE, text)
        self.assertIn("No estimate: 2 usable earlier run(s), 3 needed.", text)
        self.assertEqual(text.count("No estimate"), 1)
        self.assertIn("No fit or split advice without a window range.", text)


# ------------------------------------------------------------- the report (d2)


def window_runs(case, rises=(20.0, 35.0, 30.0), turns=(40, 82, 55)):
    """Three earlier runs usable for both measures."""
    for number, (rise, value) in enumerate(zip(rises, turns), 1):
        case.make_run(f"20261101-000000-b00{number}", plan(900 + number),
                      segments=[segment(turns=value, first=10.0, last=10.0 + rise)])


class ReportLineTests(EstimateCase):

    def reading(self, percent=30.0, resets_at=NOW + 600, window="five_hour"):
        return {"source": "reported", "percent": percent, "window": window,
                "resets_at": resets_at, "read_at": NOW - 60}

    def text(self, reading=None, settings=SETTINGS, **extra):
        state = state_for(plan(1000), reading=reading, **extra)
        return "\n".join(estimate.report_lines(
            estimate.estimate(self.runs, CURRENT, state, settings, now=NOW)))

    def test_rejection_another_builder_says_no_estimate_is_made(self):
        text = self.text(settings={**SETTINGS, "roles": {"builder": "gemini"}})
        self.assertIn("## Size estimate", text)
        self.assertIn("No estimate is made: the run is built by gemini", text)
        self.assertIn(estimate.ADVICE_ONLY, text)

    def test_rejection_too_few_runs_name_the_counts_and_give_no_advice(self):
        self.make_run("20261101-000000-a001", plan(900), build_turns=40)
        self.make_run("20261101-000000-a002", plan(901), build_turns=70)
        text = self.text()
        self.assertIn("No estimate: 2 usable earlier run(s), 3 needed.", text)
        self.assertIn("No estimate: 0 usable earlier run(s), 3 needed.", text)
        self.assertIn("The turn limit stays at 80 unless you give `--max-turns N`.", text)
        self.assertIn("No fit or split advice without a window range.", text)
        self.assertNotIn("Recommended turn limit", text)

    def test_positive_a_recommended_limit_names_the_approve_command(self):
        window_runs(self)
        text = self.text(reading=self.reading())
        self.assertIn(f"Recommended turn limit: 90 (now 80): `{estimate.STARTER} --approve "
                      f"{CURRENT} --max-turns 90`.", text)
        self.assertIn("Range: 40 to 82", text)
        self.assertIn("Range: 20.0 to 35.0", text)

    def test_positive_the_current_limit_is_enough(self):
        window_runs(self, turns=(40, 80, 55))
        self.assertIn("The current turn limit, 80, is enough.", self.text())

    def test_positive_left_out_runs_are_counted_by_reason(self):
        self.make_run("20261101-000000-a001", None, build_turns=40)
        self.make_run("20261101-000000-a002", None, build_turns=40)
        self.make_run("20261101-000000-a003", plan(900), builder="codex", build_turns=40)
        self.assertIn("Left out: 3 earlier run(s): it recorded no merged plan (2); its "
                      "builder was codex, not Claude (1).", self.text())

    def test_rejection_not_fitting_a_fresh_window_recommends_a_split(self):
        window_runs(self, rises=(20.0, 85.0, 30.0))
        text = self.text(reading=self.reading())
        self.assertIn("It does not fit a fresh window", text)
        self.assertIn(f"--amend {CURRENT} <first-chunk-brief>", text)
        self.assertIn(f"--stop {CURRENT}", text)

    def test_rejection_fitting_fresh_but_not_now_recommends_waiting(self):
        window_runs(self)
        text = self.text(reading=self.reading(percent=50.0))
        self.assertIn("It fits a fresh window but not now: approve after the saved "
                      f"reading's reset time, {estimate.when(NOW + 600)}.", text)

    def test_rejection_fits_now_unknown_gives_no_wait_advice(self):
        window_runs(self)
        text = self.text(reading=self.reading(window="seven_day"))
        self.assertIn("Whether it fits now is unknown: the saved reading is for the "
                      "seven_day window, not the five-hour one, so no wait is "
                      "recommended.", text)
        self.assertNotIn("approve after", text)

    def test_positive_fits_now_is_dated_and_names_the_weekly_limit(self):
        window_runs(self)
        text = self.text(reading=self.reading(percent=10.0))
        self.assertIn("It fits a fresh window, and it fits now.", text)
        self.assertIn(f"Worked out {estimate.when(NOW)} from the saved Claude reading of "
                      f"10.0%, read {estimate.when(NOW - 60)}, resetting "
                      f"{estimate.when(NOW + 600)}", text)
        self.assertIn("the weekly one included", text)


class PlanReportTests(LoopCase):
    """The section in ``plan-report.md`` at the approval pause, and ``--approve
    --dry-run`` working the fit advice out again (plan 10.10 item 10, 10.3 step 6)."""

    def paused(self, builder="claude"):
        run = loop.start("brief.md", "demo", str(self.personal), root=self.root,
                         deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                         settings=self.settings, builder=builder)
        asyncio.run(planning.advance(run))
        self.assertEqual(run.state["pause"], sr.AWAITING_PLAN_APPROVAL)
        return run

    def report(self, run):
        return (run.run_dir / planning.PLAN_REPORT).read_text(encoding="utf-8")

    def earlier(self):
        for number, value in enumerate((40, 82, 55), 1):
            make_fake_run(self.runs, f"20200101-000000-c00{number}", plan(900 + number),
                          segments=[segment(turns=value, first=10.0, last=30.0)])

    def test_positive_the_pause_writes_the_section_after_the_intent_check(self):
        text = self.report(self.paused())
        self.assertIn("## Size estimate", text)
        self.assertLess(text.index("## Intent check"), text.index("## Size estimate"))
        self.assertIn("No estimate: 0 usable earlier run(s), 3 needed.", text)

    def test_positive_earlier_runs_beside_the_run_give_a_range(self):
        self.earlier()
        run = self.paused()
        text = self.report(run)
        self.assertIn("Range: 40 to 82", text)
        self.assertIn(f"--approve {run.state['run_id']} --max-turns 90", text)

    def earlier_codex(self):
        for number, rise in enumerate((20.0, 35.0, 30.0), 1):
            make_fake_run(self.runs, f"20200101-000000-d00{number}", plan(900 + number),
                          builder="codex", segments=[codex_segment(10.0, 10.0 + rise)])

    def test_positive_a_codex_builder_gets_its_own_estimate_in_the_report(self):
        # Earlier Claude runs beside it are left out; earlier Codex runs give the range.
        self.earlier()
        self.earlier_codex()
        text = self.report(self.paused(builder="codex"))
        self.assertIn(estimate.CODEX_TURNS_LINE, text)
        self.assertIn("Range: 20.0 to 35.0", text)
        self.assertIn("its builder was claude, not Codex (3)", text)
        self.assertNotIn("Recommended turn limit", text)
        self.assertNotIn("The turn limit stays", text)

    def test_positive_approve_dry_run_gives_a_codex_builder_its_fit_advice(self):
        self.earlier_codex()
        out = self.dry_approve(self.paused(builder="codex"))
        self.assertIn("Size estimate, the fit advice worked out again now:", out)
        self.assertIn("Codex five-hour reading", out)
        self.assertNotIn("Claude reading", out)

    def test_rejection_an_estimate_that_fails_never_stops_the_pause(self):
        with mock.patch.object(estimate, "estimate", side_effect=RuntimeError("boom")):
            run = self.paused()
        self.assertIn("The size estimate could not be worked out: RuntimeError: boom.",
                      self.report(run))
        self.assertEqual(run.state["pause"], sr.AWAITING_PLAN_APPROVAL)

    def dry_approve(self, run):
        again = loop.load(run.state["run_id"], root=self.root, deps=self.h.deps(),
                          log_path=self.log, runs_dir=self.runs)
        state_before = (again.run_dir / "state.json").read_bytes()
        report_before = self.report(again)
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertIsNone(loop.approve(again, dry_run=True))
        self.assertEqual((again.run_dir / "state.json").read_bytes(), state_before)
        self.assertEqual(self.report(again), report_before)
        return err.getvalue()

    def test_positive_approve_dry_run_works_the_fit_advice_out_again(self):
        self.earlier()
        run = self.paused()
        out = self.dry_approve(run)
        self.assertIn("[DRY RUN] the four approval checks passed", out)
        self.assertIn("Size estimate, the fit advice worked out again now:", out)
        self.assertIn("This covers the five-hour window only.", out)

    def test_rejection_approve_dry_run_with_too_few_runs_says_so(self):
        out = self.dry_approve(self.paused())
        self.assertIn("Size estimate: no fit advice: 0 usable earlier run(s) for window "
                      "use, 3 needed.", out)

    def test_rejection_approve_dry_run_survives_a_failing_estimate(self):
        run = self.paused()
        with mock.patch.object(estimate, "estimate", side_effect=RuntimeError("boom")):
            out = self.dry_approve(run)
        self.assertIn("Size estimate: could not be worked out: RuntimeError: boom.", out)


class TemplateSizeTests(unittest.TestCase):

    def test_positive_the_brief_template_asks_for_one_window(self):
        text = (WORKFLOW / "prompts" / "brief.md").read_text(encoding="utf-8")
        self.assertIn("fit within one five-hour usage window", text)
        self.assertIn("The plan report's size estimate is the check", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
