#!/usr/bin/env python3
"""Tests for A4's intent prompts (plan 10.6 item 2 and 10.2): ``prompts/intent-first.md``
and ``prompts/intent-final.md`` carry only the brief and the merged plan or the final
diff, never the individual plans, the synthesis, reviewer reasoning, findings or
transcripts; both say the required CONTEXT.md, LOG.md and test updates are not unasked
changes; a final diff too large for the prompt is shown as the reviewer's is, saved
whole with the read-in-parts instruction; and the brief template's Out of scope comment
says what 10.2 says about backticked paths, the template still being refused.

Whole runs are driven with the fixtures' fakes in a temporary repository; no session
or process is started.

    python workflows/review-orchestration/tests/test_intent_prompts.py
"""

import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_fixtures import *  # noqa: E402,F401,F403 - the loop suites' shared fixtures

PROMPTS = WORKFLOW / "prompts"
EXEMPTION = ("The `CONTEXT.md`, `LOG.md` and test updates the\nproject's rules require "
             "for a change the brief asked for are part of that change, not")


class TemplateTests(unittest.TestCase):

    def test_positive_both_prompts_carry_the_exemption_and_the_line_rule(self):
        for name in ("intent-first", "intent-final"):
            text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
            with self.subTest(name):
                self.assertIn(" ".join(EXEMPTION.split()), " ".join(text.split()))
                self.assertIn("one reply entry per line, in the order given", text)
                self.assertIn('"met", "not met" or "cannot tell"', text)
                flat = " ".join(text.split())
                self.assertIn("put the line's number in `number` and its first eight "
                              "words in `opening`", flat)
                self.assertIn("do not copy the rest of the line", flat)
                self.assertIn("an `opening` that does not match the start of the line "
                              "with that number is refused", flat)
                self.assertEqual(sr.OPENING_WORDS, 8)
        final = (PROMPTS / "intent-final.md").read_text(encoding="utf-8")
        self.assertIn("files_reviewed", final)

    def test_negative_no_prompt_name_matches_the_ignored_plan_pattern(self):
        # A prompt named like a design document (`*-PLAN.md`) would be gitignored.
        self.assertFalse([p.name for p in PROMPTS.glob("*.md")
                          if p.name.lower().endswith("-plan.md")])

    def test_positive_the_brief_comment_names_backticked_paths_and_both_checks(self):
        text = (PROMPTS / "brief.md").read_text(encoding="utf-8")
        comment = text.split("## Out of scope", 1)[1].split("## Acceptance checks")[0]
        flat = " ".join(comment.split())
        self.assertIn("written in backticks", flat)
        self.assertIn("both intent checks", flat)
        self.assertIn("exists when the run starts", flat)
        self.assertIn("read by the intent checker only", flat)
        _headings, problems = brief.check_text(text, PROJECT)
        self.assertTrue(problems, "the empty template is still refused")

    def test_positive_a_large_diff_becomes_a_pointer_never_a_cut(self):
        small = "x" * 10
        self.assertEqual(loop.shown_diff(small, "runs/r/intent/final-diff.patch"), small)
        self.assertEqual(loop.shown_diff("", "p"), "(empty)")
        large = "y" * (loop.DIFF_LIMIT + 1)
        shown = loop.shown_diff(large, "runs/r/intent/final-diff.patch")
        self.assertNotIn("yyyy", shown)
        self.assertIn("saved whole at `runs/r/intent/final-diff.patch`", shown)
        self.assertIn("files_reviewed", shown)


class PayloadTests(LoopCase):

    def test_positive_the_final_payload_holds_the_brief_and_the_diff_only(self):
        self.h.builder_script = [edit(self.root, "PLANTED-CHANGE\n"),
                                 reply(actions(("R1-1", "fixed")))]
        self.h.reviews = [[finding(title="PLANTED-FINDING-TITLE")], []]
        run = self.start()
        self.assertEqual(self.reason(run), sr.CLEAN)
        prompt = self.h.intent_calls[0]["prompt"]
        self.assertIn("## Goal\n- work/a.txt says done.", prompt)
        self.assertIn("+PLANTED-CHANGE", prompt)
        self.assertIn("1. (Goal) work/a.txt says done.", prompt)
        self.assertIn("- work/a.txt", prompt)
        for excluded in ("PLANTED-FINDING-TITLE", "seen", "fix it",  # the finding
                         "The merged plan.", "Both change work/a.txt.",  # plan, synthesis
                         "Changed work/a.txt.", "builder action"):  # transcripts, prior
            self.assertNotIn(excluded, prompt)

    def test_positive_a_large_final_diff_is_saved_and_every_changed_file_listed(self):
        def big(prompt):
            (self.root / "work" / "big.txt").write_text("z" * (loop.DIFF_LIMIT + 10),
                                                        encoding="utf-8", newline="\n")
            return edit(self.root, "done\n")(prompt)
        self.h.builder_script = [big]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.CLEAN)
        prompt = self.h.intent_calls[0]["prompt"]
        saved = run.run_dir / "intent" / "final-diff.patch"
        self.assertIn(f"saved whole at `{saved.relative_to(self.root).as_posix()}`",
                      prompt)
        self.assertNotIn("z" * 100, prompt)
        self.assertIn("z" * 100, saved.read_text(encoding="utf-8"))
        self.assertIn("- work/a.txt", prompt)
        self.assertIn("- work/big.txt", prompt)


if __name__ == "__main__":
    unittest.main()
