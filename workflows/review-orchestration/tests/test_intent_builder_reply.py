#!/usr/bin/env python3
"""Hermetic tests for the builder's last reply in the final-diff intent checks (plan
10.6, D5 option A) and for the prompt's sentence on where the change stands.

Both final-diff checks, ``intent-final`` and ``intent-info``, are given the final text
of the last completed build or fix step as a section of its own, which the checker may
use only for a line about what that reply says. The reply is kept whole in
``transcripts/<step>-reply.json``; a run recorded before that file was kept falls back
to the step record's 4,000-character ``summary``, and a reply over ``REPLY_LIMIT`` is
never cut: it is saved whole under ``intent/`` and the prompt points to it
(intent_reply R1-1). The final check tells the checker its result decides the run's end;
the information check tells it the review ended with findings open and its result is
information only.

Whole runs are driven with the fixtures' fakes in a temporary repository. No test
starts Docker, a model session, a Claude probe or a Codex app server.

    python workflows/review-orchestration/tests/test_intent_builder_reply.py
"""

import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_fixtures import *  # noqa: E402,F401,F403 - the loop suites' shared fixtures

PROMPTS = WORKFLOW / "prompts"
HEADING = "THE BUILDER'S LAST REPLY ("
USE_RULE = "Use it only to judge a line about what that reply says or must say."


class ReplyCase(LoopCase):

    def writing(self, text):
        """A builder action that writes work/a.txt and replies with ``text``."""
        def act(prompt):
            (self.root / "work" / "a.txt").write_bytes(b"done\n")
            return text
        return act

    def report(self, run):
        return (run.run_dir / "report.md").read_text(encoding="utf-8")

    def final_prompt(self):
        [call] = self.h.intent_calls
        return call["prompt"]

    def section(self, prompt):
        """The reply section: from its heading to the closing instructions."""
        return prompt.split(HEADING, 1)[1].split("\nReturn JSON only", 1)[0]


class FinalCheckTests(ReplyCase):

    def test_positive_the_last_fix_reply_reaches_the_final_check(self):
        self.h.builder_script = [self.writing("BUILD-REPLY-MARKER"),
                                 reply("FIX-REPLY-MARKER\n" + actions(("R1-1", "fixed")))]
        self.h.reviews = [[finding()], []]
        run = self.start()
        self.assertEqual(self.reason(run), sr.CLEAN)
        prompt = self.final_prompt()
        section = self.section(prompt)
        self.assertTrue(section.startswith("step fix-R1, in full)\n"), section[:80])
        self.assertIn("FIX-REPLY-MARKER", section)
        self.assertNotIn("BUILD-REPLY-MARKER", prompt, "only the last reply is given")
        self.assertIn(USE_RULE, prompt)
        [fix] = [s for s in run.state["steps"] if s["step"] == "fix-R1"]
        self.assertEqual(run.state["intent"]["final"]["reply"],
                         {"step": "fix-R1", "source": "file", "chars": len(fix["summary"]),
                          "cut": False, "saved": None})
        self.assertIn("Builder's reply given to the checker: step fix-R1, in full.",
                      self.report(run))

    def test_positive_a_build_only_run_gives_the_build_reply(self):
        self.h.builder_script = [self.writing("BUILD-ONLY-MARKER")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.CLEAN)
        section = self.section(self.final_prompt())
        self.assertTrue(section.startswith("step build, in full)\n"))
        self.assertIn("BUILD-ONLY-MARKER", section)

    def test_positive_the_reply_is_kept_whole_beyond_the_summary(self):
        long = "S" + "x" * (loop.SUMMARY_LIMIT + 500) + "END-MARKER"
        self.h.builder_script = [self.writing(long)]
        self.h.reviews = [[]]
        run = self.start()
        kept = json.loads((run.run_dir / "transcripts" / "build-reply.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(kept, {"step": "build", "text": long})
        self.assertEqual(len(run.state["steps"][0]["summary"]), loop.SUMMARY_LIMIT)
        section = self.section(self.final_prompt())
        self.assertIn("S" + "x" * 50, section, "the whole reply, its start included")
        self.assertTrue(section.startswith("step build, in full)\n"))

    def test_positive_a_reply_over_the_limit_is_saved_whole_and_pointed_to(self):
        # intent_reply R1-1: a cut reply could drop the text a Constraint is about.
        long = "START-MARKER-é" + "y" * loop.REPLY_LIMIT + "END-MARKER"
        self.h.builder_script = [self.writing(long)]
        self.h.reviews = [[]]
        run = self.start()
        section = self.section(self.final_prompt())
        self.assertNotIn("y" * 100, section, "never a cut-down reply in the prompt")
        saved = run.run_dir / "intent" / "final-reply.txt"
        shown = saved.relative_to(self.root).as_posix()
        self.assertTrue(section.startswith(
            f"step build, in full, {len(long):,} characters, saved to a file)\n"),
            section[:120])
        self.assertIn(f"saved whole at `{shown}`", section)
        self.assertIn("read all of it there", section)
        self.assertEqual(saved.read_text(encoding="utf-8"), long, "the start included")
        record = run.state["intent"]["final"]["reply"]
        self.assertEqual((record["chars"], record["cut"], record["saved"]),
                         (len(long), False, shown))
        self.assertIn("Builder's reply given to the checker: step build, in full, "
                      f"{len(long):,} characters, saved to a file.", self.report(run))

    def test_positive_a_reply_at_the_limit_is_given_whole_in_the_prompt(self):
        exact = "A" + "y" * (loop.REPLY_LIMIT - 2) + "Z"
        self.h.builder_script = [self.writing(exact)]
        self.h.reviews = [[]]
        run = self.start()
        section = self.section(self.final_prompt())
        self.assertIn(exact, section)
        self.assertFalse((run.run_dir / "intent" / "final-reply.txt").exists())
        self.assertIsNone(run.state["intent"]["final"]["reply"]["saved"])

    def test_positive_an_older_run_falls_back_to_the_summary(self):
        long = "z" * (loop.SUMMARY_LIMIT + 10) + "TAIL-MARKER"
        self.h.builder_script = [self.writing(long)]

        def drop():
            # As a run recorded before the reply file was kept.
            for path in self.root.rglob("build-reply.json"):
                path.unlink()
        self.h.during_review = drop
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.CLEAN)
        section = self.section(self.final_prompt())
        self.assertIn("TAIL-MARKER", section)
        self.assertTrue(section.startswith(
            f"step build, its last {loop.SUMMARY_LIMIT:,} characters only; this run did "
            "not keep the whole reply)\n"))
        self.assertEqual(run.state["intent"]["final"]["reply"]["source"], "summary")

    def test_negative_an_unreadable_reply_file_ends_the_run_error(self):
        self.h.builder_script = [self.writing("x")]

        def spoil():
            for path in self.root.rglob("build-reply.json"):
                path.write_text("not json", encoding="utf-8", newline="\n")
        self.h.during_review = spoil
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertEqual(self.h.intent_calls, [], "no session on a reply it cannot read")
        self.assertNotEqual(run.state.get("step"), loop.INTENT_FINAL)

    def test_positive_the_final_check_says_its_result_decides_the_end(self):
        self.h.builder_script = [self.writing("x")]
        self.h.reviews = [[]]
        self.start()
        flat = " ".join(self.final_prompt().split())
        self.assertIn(loop.FINAL_STANDING, flat)
        self.assertNotIn(loop.INFO_STANDING, flat)


class InfoCheckTests(ReplyCase):

    def test_positive_the_information_check_says_it_is_information(self):
        self.h.builder_script = [self.writing("CAPPED-BUILD-MARKER")]
        self.h.reviews = [[finding()]]
        run = self.start(cap=1)
        self.assertEqual(self.reason(run), sr.MAX_ROUNDS)
        prompt = self.final_prompt()
        flat = " ".join(prompt.split())
        self.assertIn(loop.INFO_STANDING, flat)
        self.assertNotIn(loop.FINAL_STANDING, flat)
        self.assertNotIn("no finding left open", flat)
        self.assertNotIn("decides how the run ends", flat)
        section = self.section(prompt)
        self.assertTrue(section.startswith("step build, in full)\n"))
        self.assertIn("CAPPED-BUILD-MARKER", section)
        [entry] = run.state["intent"]["info"]
        self.assertEqual(entry["reply"]["step"], "build")
        self.assertIn("Builder's reply given to the checker: step build, in full.",
                      self.report(run))

    def test_negative_an_unreadable_reply_file_is_a_note_and_the_stop_stands(self):
        self.h.builder_script = [self.writing("x")]

        def spoil():
            for path in self.root.rglob("build-reply.json"):
                path.write_text("not json", encoding="utf-8", newline="\n")
        self.h.during_review = spoil
        self.h.reviews = [[finding()]]
        run = self.start(cap=1)
        self.assertEqual(self.reason(run), sr.MAX_ROUNDS)
        self.assertEqual(self.h.intent_calls, [])
        [entry] = run.state["intent"]["info"]
        self.assertEqual(entry["not_run"], sr.ERROR)

    def test_positive_a_long_reply_at_info_is_saved_under_its_round(self):
        long = "INFO-START" + "q" * loop.REPLY_LIMIT
        self.h.builder_script = [self.writing(long)]
        self.h.reviews = [[finding()]]
        run = self.start(cap=1)
        self.assertEqual(self.reason(run), sr.MAX_ROUNDS)
        saved = run.run_dir / "intent" / "info-R1-reply.txt"
        self.assertEqual(saved.read_text(encoding="utf-8"), long)
        section = self.section(self.final_prompt())
        self.assertIn(f"saved whole at `{saved.relative_to(self.root).as_posix()}`",
                      section)
        self.assertNotIn("q" * 100, section)

    def test_positive_at_reopened_the_last_fix_reply_is_given(self):
        self.h.builder_script = [self.writing("done"),
                                 reply("FIRST-FIX\n" + actions(("R1-1", "fixed"))),
                                 reply("SECOND-FIX\n" + actions(("R2-1", "fixed")))]
        self.h.reviews = [[finding()], [finding(reraises="R1-1")],
                          [finding(reraises="R2-1")]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REOPENED)
        section = self.section(self.final_prompt())
        self.assertTrue(section.startswith("step fix-R2, in full)\n"))
        self.assertIn("SECOND-FIX", section)
        self.assertNotIn("FIRST-FIX", section)


class UnitTests(unittest.TestCase):

    def test_positive_no_builder_step_gives_none(self):
        with tempfile.TemporaryDirectory() as folder:
            record, text = loop.builder_reply({"steps": [
                {"step": "review-R1", "role": "reviewer"}]}, Path(folder))
        self.assertEqual((record["source"], record["step"], text), ("none", None, ""))
        self.assertEqual(loop.reply_note(record),
                         "none: no build or fix step had completed")

    def test_positive_a_short_summary_is_whole(self):
        with tempfile.TemporaryDirectory() as folder:
            record, text = loop.builder_reply({"steps": [
                {"step": "build", "role": "builder", "summary": "short"}]}, Path(folder))
        self.assertEqual((record["source"], record["cut"], text), ("summary", False, "short"))
        self.assertEqual(loop.reply_note(record), "step build, in full")

    def test_positive_the_last_builder_step_wins_over_later_other_steps(self):
        with tempfile.TemporaryDirectory() as folder:
            record, text = loop.builder_reply({"steps": [
                {"step": "build", "role": "builder", "summary": "one"},
                {"step": "fix-R1", "role": "builder", "summary": "two"},
                {"step": "review-R2", "role": "reviewer"},
                {"step": "intent-info", "role": "intent_checker"}]}, Path(folder))
        self.assertEqual((record["step"], text), ("fix-R1", "two"))

    def test_positive_a_long_reply_becomes_a_pointer_never_a_cut(self):
        self.assertEqual(loop.shown_reply("", "p"), "(empty)")
        self.assertEqual(loop.shown_reply("short", "p"), "short")
        shown = loop.shown_reply("w" * (loop.REPLY_LIMIT + 1), "runs/r/intent/x.txt")
        self.assertNotIn("wwww", shown)
        self.assertIn("saved whole at `runs/r/intent/x.txt`", shown)

    def test_positive_the_template_takes_the_standing_and_the_reply(self):
        text = (PROMPTS / "intent-final.md").read_text(encoding="utf-8")
        for field in ("$standing", "$reply_note", "$builder_reply"):
            self.assertIn(field, text)
        flat = " ".join(text.split())
        self.assertNotIn("no finding left open", flat, "the standing is a field now")
        self.assertIn(USE_RULE, flat)
        self.assertIn("For every other line it is not evidence", flat)


if __name__ == "__main__":
    unittest.main()
