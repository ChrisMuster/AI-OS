#!/usr/bin/env python3
"""Hermetic tests for the build-and-review loop (loop.py): whole runs, from the start
to their end. The run's steps and stop reasons, the usage and credit checks before
each step, the clean stop and resume, the preflight, the frozen folder, Docker and the
image at the start, the constraints file, the end of a run, the sessions' trusted
settings as the loop gives them, and Docker at a resume.

This is the first of the loop files, split by subject before A3 chunk 2's tests were
added (plan 10.3, the chunk list): ``test_loop_trusted.py`` holds the trusted copy and
the command line, ``test_loop_pairing.py`` the reverse pairing and the check after every
Codex builder turn, ``test_loop_usage.py`` the usage guard, and ``test_loop_parts.py``
the loop's parts (the price alarm, the run record's escaping, the packet, the brief
reader, the working-tree records, the mechanical checks and the prompts). The shared
fixtures (the loaded modules, ``Harness``, the fake sessions, ``LoopCase`` and
``SwappedCase``) are in ``loop_fixtures.py``, which is not a suite.

No test makes a model call. Whole runs are driven through ``loop.Deps`` with a fake
builder session, a fake reviewer session, scripted usage readings and scripted
mechanical checks, in a temporary project that is a real git repository with a
second repository standing in for the personal one. What is under test is the loop:
the order of steps, the stop reasons, the usage and credit checks before each step,
the clean stop and resume, the records it writes, and what each role is shown.

Same three controls as the other suites:

    positive control  a run the plan expects to work. It must work.
    rejection control a condition the plan says ends or refuses the run. It must,
                      with the reason named.
    negative control  something that looks like such a condition but is not. It
                      must not end the run.

    python workflows/review-orchestration/tests/test_loop.py
    python -m unittest discover -s workflows/review-orchestration/tests -p "test_loop*.py"
"""

import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_fixtures import *  # noqa: E402,F401,F403 - the loop suites' shared fixtures


class RunTests(LoopCase):

    def test_positive_a_clean_review_ends_reviewed_clean_never_clean(self):
        self.h.builder_script = [edit(self.root, "done\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        self.assertEqual(run.state["step"], "review-R1")
        self.assertEqual([s["step"] for s in run.state["steps"]], ["build", "review-R1"])
        self.assertEqual(self.log_actions(), ["started", "completed"])
        folder = run.run_dir
        for name in ("brief.md", "settings.json", "state.json", "report.md", "report.json",
                     "start/heads.json", "start/diff.patch", "rounds/R1/diff.patch",
                     "rounds/R1/files.zip", "transcripts/build.json",
                     "transcripts/review-R1.json"):
            self.assertTrue((folder / name).is_file(), name)
        self.assertIn("+done", (folder / "rounds/R1/diff.patch").read_text(encoding="utf-8"))
        saved = json.loads((folder / "settings.json").read_text(encoding="utf-8"))
        self.assertIn("claude-agent-sdk", saved["sdk_versions"])
        self.assertEqual(run.state["live"]["claude_init_model"], MODEL)
        self.assertEqual(run.state["live"]["title_pattern"], "accepted")

    def test_positive_a_finding_fixed_then_a_clean_round(self):
        self.h.builder_script = [edit(self.root, "one\n"),
                                 lambda p: (edit(self.root, "two\n")(p),
                                            actions(("R1-1", "fixed"), ("R1-2", "fixed")))[1]]
        self.h.mechanical = [[{"title": "doc-sync: work: CONTEXT.md not updated",
                               "severity": "major", "file": "work", "evidence": "e",
                               "fix": "f"}], []]
        self.h.reviews = [[finding()], []]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        ledger = run.ledger.findings
        self.assertEqual(ledger["R1-1"]["source"], "mechanical")  # checks first
        self.assertEqual(ledger["R1-2"]["source"], "reviewer")
        text = (self.root / "memory" / "demo_review_packet.md").read_text(encoding="utf-8")
        self.assertIn("## Open\n\nNone.", text)
        self.assertIn("### R1-1 - doc-sync", text)
        fix_prompt = self.h.builders[0].prompts[1][1]
        self.assertIn("R1-1 (mechanical", fix_prompt)
        self.assertIn("R1-2 (reviewer", fix_prompt)

    def test_rejection_an_empty_first_pass_ends_no_change_made(self):
        self.h.builder_script = [reply("I changed nothing.")]
        run = self.start()
        self.assertEqual(self.reason(run), sr.NO_CHANGE_MADE)
        self.assertEqual(self.h.review_calls, [])

    def test_negative_rewriting_a_generated_file_is_not_a_change(self):
        (self.root / "work" / "out.generated").write_bytes(b"before\n")

        def regenerate(prompt):
            (self.root / "work" / "out.generated").write_bytes(b"after\n")
            return "ran a tool"
        self.h.builder_script = [regenerate]
        self.assertEqual(self.reason(self.start()), sr.NO_CHANGE_MADE)

    def test_negative_a_bytecode_cache_is_not_a_change(self):
        def cache(prompt):
            (self.root / "work" / "__pycache__").mkdir()
            (self.root / "work" / "__pycache__" / "a.cpython-313.pyc").write_bytes(b"x")
            return "ran the tests"
        self.h.builder_script = [cache]
        self.assertEqual(self.reason(self.start()), sr.NO_CHANGE_MADE)

    def test_rejection_declined_scope_ends_out_of_scope_at_once(self):
        self.h.builder_script = [edit(self.root, "x\n"),
                                 reply(actions(("R1-1", "declined-scope")))]
        self.h.reviews = [[finding()]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.OUT_OF_SCOPE)
        self.assertEqual(len(self.h.review_calls), 1)

    def test_rejection_a_reraise_of_a_declined_merits_finding_is_disagreement(self):
        self.h.builder_script = [edit(self.root, "x\n"),
                                 reply(actions(("R1-1", "declined-merits")))]
        self.h.reviews = [[finding()], [finding(reraises="R1-1")]]
        self.assertEqual(self.reason(self.start()), sr.DISAGREEMENT)

    def test_positive_a_merits_decline_not_raised_again_is_accepted(self):
        self.h.builder_script = [edit(self.root, "x\n"),
                                 reply(actions(("R1-1", "declined-merits")))]
        self.h.reviews = [[finding()], []]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        self.assertEqual(run.ledger.findings["R1-1"]["decline_accepted"], 2)
        text = (self.root / "memory" / "demo_review_packet.md").read_text(encoding="utf-8")
        self.assertIn("## Open\n\nNone.", text)  # the end reason and the packet agree
        self.assertIn("not raised again in round 2, so the decline stands", text)
        report = json.loads((run.run_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual((report["open"], report["addressed"]), ([], ["R1-1"]))

    def test_positive_a_decline_is_accepted_even_in_a_round_with_other_findings(self):
        self.h.builder_script = [edit(self.root, "x\n"),
                                 reply(actions(("R1-1", "declined-merits"))),
                                 reply(actions(("R2-1", "fixed")))]
        self.h.reviews = [[finding()], [finding(title="Another defect")], []]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        self.assertEqual(run.ledger.findings["R1-1"]["decline_accepted"], 2)

    def test_negative_a_decline_raised_again_is_never_accepted(self):
        self.h.builder_script = [edit(self.root, "x\n"),
                                 reply(actions(("R1-1", "declined-merits")))]
        self.h.reviews = [[finding()], [finding(reraises="R1-1")]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.DISAGREEMENT)
        self.assertNotIn("decline_accepted", run.ledger.findings["R1-1"])
        report = json.loads((run.run_dir / "report.json").read_text(encoding="utf-8"))
        self.assertIn("R1-1", report["open"])

    def test_rejection_a_malformed_builder_reply_ends_error(self):
        self.h.builder_script = [edit(self.root, "x\n"), reply("no action list")]
        self.h.reviews = [[finding()]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertEqual(self.log_actions()[-1], "failed")

    def test_rejection_a_label_in_a_reviewer_title_ends_error(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[finding(title="R1-1 again")]]
        self.assertEqual(self.reason(self.start()), sr.ERROR)

    def test_rejection_max_rounds_then_resume_with_more_rounds(self):
        self.h.builder_script = [edit(self.root, "x\n"), reply(actions(("R1-1", "fixed")))]
        self.h.reviews = [[finding()], []]
        run = self.start(cap=1)
        self.assertEqual(self.reason(run), sr.MAX_ROUNDS)
        again = self.resume(run, rounds=1)
        self.assertEqual(self.reason(again), sr.REVIEWED_CLEAN)
        self.assertEqual(again.state["round_cap"], 2)
        self.assertEqual([s["reason"] for s in again.state["stop_reasons"]],
                         [sr.MAX_ROUNDS, sr.REVIEWED_CLEAN])
        self.assertEqual(self.h.builders[-1].resume, "s-1")  # same builder session

    def test_rejection_a_reviewed_clean_run_cannot_be_resumed(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        again = loop.load(run.state["run_id"], root=self.root, deps=self.h.deps(),
                          log_path=self.log, runs_dir=self.runs)
        with self.assertRaises(loop.RunRefused):
            loop.resume(again)

    def test_negative_reviewer_is_shown_actions_but_never_the_builder_note(self):
        self.h.builder_script = [edit(self.root, "x\n"),
                                 reply(actions(("R1-1", "fixed")).replace(
                                     '"note": "n"', '"note": "SECRET-ACCOUNT"'))]
        self.h.reviews = [[finding()], []]
        self.start()
        second = self.h.review_calls[1][0]
        self.assertIn("R1-1 (reviewer) A defect: builder action fixed", second)
        self.assertNotIn("SECRET-ACCOUNT", second)
        self.assertNotIn("Changed work/a.txt", second)

    def test_positive_the_reviewer_is_given_the_brief_the_checks_and_the_diff(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        self.start()
        prompt = self.h.review_calls[0][0]
        self.assertIn("work/a.txt says done.", prompt)
        self.assertIn("checked work", prompt)
        self.assertIn("+x", prompt)

    def test_rejection_a_reviewer_that_skips_a_changed_file_ends_error(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        self.h.files_reviewed = []  # read nothing, yet claims clean
        run = self.start()
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertIn("work/a.txt", run.state["stop_reasons"][-1]["evidence"]["error"])

    def test_positive_every_changed_file_is_named_and_the_schema_requires_the_list(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        self.start()
        prompt, schema = self.h.review_calls[0]
        self.assertIn("THE CHANGED FILES", prompt)
        self.assertIn("- work/a.txt", prompt)
        self.assertIn("files_reviewed", schema["required"])

    def test_rejection_a_diff_too_large_for_the_prompt_is_never_cut(self):
        big = "line of text\n" * 40_000  # about 520,000 characters
        self.h.builder_script = [lambda p: ((self.root / "work" / "big.txt").write_bytes(
            big.encode("utf-8")), "wrote a big file")[1]]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        prompt = self.h.review_calls[0][0]
        self.assertIn("too large for this prompt", prompt)
        self.assertNotIn("line of text", prompt)  # no part of it is passed off as whole
        saved = (run.run_dir / "rounds/R1/diff.patch").read_text(encoding="utf-8")
        self.assertIn(f"rounds/R1/diff.patch", prompt)
        self.assertEqual(saved.count("line of text"), 40_000)  # the saved diff is whole

    def test_positive_the_checks_run_first_and_their_writes_are_named(self):
        def append_log():
            path = self.root / "work" / "a.txt"
            path.write_bytes(path.read_bytes() + b"check wrote this\n")
        self.h.check_writes = append_log
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        prompt = self.h.review_calls[0][0]
        written = prompt.split("FILES THE ORCHESTRATOR'S OWN CHECKS CHANGED")[1]
        written = written.split("THE DIFF")[0]
        # Exactly the check's line is named as the check's; the builder's own edit to
        # the same file is not (R5-2).
        self.assertIn("+check wrote this", written)
        self.assertNotIn("+x", written)
        # Approval's own writes (its LOG.md entry and the packet) come first (A3 chunk 3:
        # every run is approved before it builds).
        self.assertEqual(run.state["check_writes"],
                         ["approval/check-writes.patch", "rounds/R1/check-writes.patch"])
        # The reviewed diff and the archive both hold the state the checks left.
        diff = (run.run_dir / "rounds/R1/diff.patch").read_text(encoding="utf-8")
        self.assertIn("+check wrote this", diff)
        with zipfile.ZipFile(run.run_dir / "rounds/R1/files.zip") as bundle:
            self.assertIn(b"check wrote this", bundle.read("work/a.txt"))

    def test_positive_the_start_check_is_read_only_and_no_start_capture_is_kept(self):
        # Rewritten for orchestrator isolation S6b (plan 7.1 items 2 and 3): the start
        # check runs the verifier read-only, so it writes nothing, and the start-of-run
        # check-writes capture it once needed is gone. The per-round capture stays.
        self.h.builder_script = [lambda p: ((self.root / "work" / "b.txt").write_bytes(
            b"builder\n"), "wrote b")[1]]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.h.start_check_calls, [{"read_only": True}])
        self.assertFalse((run.run_dir / "start/check-writes.patch").exists())
        # Only approval's own writes are recorded; nothing from the start (A3 chunk 3).
        self.assertEqual(run.state["check_writes"], ["approval/check-writes.patch"])
        self.assertNotIn("start-of-run", self.h.review_calls[0][0])

    def test_rejection_a_failure_while_saving_a_failed_step_still_ends_the_run(self):
        # The classification reads at preflight, then breaks after the builder's turn,
        # as a builder editing the sync-architecture code mid-way could break it. The
        # build fails on it, and the partial diff, which reads it again, fails too: the
        # run must still end error, with its report and LOG.md entry, and resume (R7-1).
        calls = []

        def classification(root):
            calls.append(1)
            # Reads 1 and 2 are the preflight's and approval's (A3 chunk 3).
            if len(calls) > 2 and not self.fixed:
                raise SyntaxError("invalid syntax (allowlist.py, line 1)")
            return CAT_A
        self.fixed = False
        deps = self.h.deps()
        deps.category_a = classification
        self.h.builder_script = [edit(self.root, "x\n")]
        run = start_built("brief.md", "demo", str(self.personal), root=self.root,
                          deps=deps, log_path=self.log, runs_dir=self.runs,
                          settings=self.settings)
        asyncio.run(run.advance())
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertEqual(run.state["status"], sr.ENDED)
        self.assertIn("SyntaxError", run.state["stop_reasons"][-1]["evidence"]["error"])
        self.assertTrue((run.run_dir / "report.md").is_file())
        self.assertEqual(self.log_actions()[-1], "failed")
        patch = (run.run_dir / "partial" / "build.patch").read_text(encoding="utf-8")
        self.assertIn("could not be taken: SyntaxError", patch)
        # The diff's failure is noted in state.json too, not only in the patch (R8-1).
        self.assertTrue(any("build: the partial diff could not be taken: SyntaxError" in n
                            for n in run.state["interrupted_notes"]))
        # Fixed, the run resumes from its last completed step.
        self.fixed = True
        self.h.builder_script = [edit(self.root, "y\n")]
        self.h.reviews = [[]]
        again = loop.load(run.state["run_id"], root=self.root, deps=deps,
                          log_path=self.log, runs_dir=self.runs)
        loop.resume(again)
        asyncio.run(again.advance())
        self.assertEqual(self.reason(again), sr.REVIEWED_CLEAN)

    def test_rejection_two_distinct_failures_are_both_recorded(self):
        # The step fails for one reason (the reviewer) and the partial diff for another
        # (the classification): state.json holds both (R8-1).
        calls = []

        def classification(root):
            calls.append(1)
            # Reads 1 to 5: preflight, approval, after the build, and the review's two
            # (either side of its checks). The sixth is the partial diff's, after the
            # reviewer failed.
            if len(calls) > 5:
                raise OSError("classification unreadable")
            return CAT_A
        deps = self.h.deps()
        deps.category_a = classification
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [providers.ProviderError("Codex failed")]
        run = start_built("brief.md", "demo", str(self.personal), root=self.root,
                          deps=deps, log_path=self.log, runs_dir=self.runs,
                          settings=self.settings)
        asyncio.run(run.advance())
        self.assertEqual(self.reason(run), sr.ERROR)
        error = run.state["stop_reasons"][-1]["evidence"]["error"]
        self.assertIn("Codex failed", error)  # the step's own failure
        self.assertNotIn("classification", error)
        self.assertTrue(any("review-R1: the partial diff could not be taken: OSError" in n
                            for n in run.state["interrupted_notes"]))

    def test_rejection_a_provider_failure_ends_error_and_resumes_at_that_step(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [providers.ProviderError("Codex failed"), []]
        run = self.start()
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertEqual(run.state["step"], "build")
        again = self.resume(run)
        self.assertEqual(self.reason(again), sr.REVIEWED_CLEAN)

    def test_rejection_codex_refusing_the_title_pattern_drops_it_and_goes_on(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [providers.ProviderError("invalid schema:\n  pattern not supported"),
                          []]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        self.assertTrue(run.state["live"]["title_pattern"].startswith("refused"))
        report = (run.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("- title_pattern: refused: invalid schema: pattern not supported\n",
                      report)
        second = self.h.review_calls[1][1]
        title = second["properties"]["findings"]["items"]["properties"]["title"]
        self.assertNotIn("pattern", title)
        self.assertIn("pattern", sr.REVIEW_SCHEMA["properties"]["findings"]["items"]
                      ["properties"]["title"])  # the shared schema is untouched


class UsageTests(LoopCase):

    def test_rejection_the_ceiling_stops_before_the_step(self):
        # Since A3 chunk 3 a step is stopped only by its own provider's reading: Codex
        # over the ceiling stops the Codex review, never the Claude build before it.
        self.h.readings = [reading(30), reading(85), reading(85)]
        self.h.builder_script = [edit(self.root, "x\n")]
        run = self.start()
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT, run.state["stop_reasons"][-1])
        self.assertEqual(run.state["step"], "build")  # the build ran; no review turn
        self.assertEqual(self.h.review_calls, [])
        self.assertIn("ceiling", run.state["stop_reasons"][-1]["evidence"]["detail"])
        self.assertEqual(run.state["stop_reasons"][-1]["evidence"]["step"], "review-R1")

    def test_rejection_the_ceiling_at_preflight_refuses_the_run(self):
        self.h.readings = [reading(90)]
        with self.assertRaises(loop.RunRefused):
            self.start()
        self.assertFalse(self.runs.exists())

    def test_rejection_claude_paid_overage_stops_before_the_next_step(self):
        # The reading arrives as the builder's session reports it, during the build.
        def build(prompt):
            self.h.claude_usage = limits.Reading(
                limits.CLAUDE, limits.LIMIT_REPORTED, percent=50, resets_at=5,
                window="five_hour", reason="using paid overage")
            return edit(self.root, "x\n")(prompt)
        self.h.builder_script = [build]
        self.h.reviews = [[finding()]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT, run.state["stop_reasons"][-1])
        # Since A3 chunk 3 Claude's overage does not stop the Codex review; it stops the
        # next Claude step, the fix pass, before it starts.
        self.assertEqual(run.state["step"], "review-R1")
        self.assertEqual(run.state["stop_reasons"][-1]["evidence"]["step"], "fix-R1")
        self.assertIn("paid overage", run.state["stop_reasons"][-1]["evidence"]["detail"])

    def test_rejection_a_changed_credit_balance_stops_the_codex_step(self):
        changed = dict(CREDITS, balance="4.50")
        self.h.readings = [reading(), reading(), reading(credits=changed)]
        self.h.builder_script = [edit(self.root, "x\n")]
        run = self.start()
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        detail = run.state["stop_reasons"][-1]["evidence"]["detail"]
        self.assertIn("balance '5.00' -> '4.50'", detail)
        self.assertEqual(self.h.review_calls, [])

    def test_rejection_a_top_up_also_stops_it_and_a_resume_rebaselines(self):
        topped = dict(CREDITS, balance="25.00")
        self.h.readings = [reading(), reading(), reading(credits=topped),
                           reading(credits=topped), reading(credits=topped)]
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        again = self.resume(run)
        self.assertEqual(self.reason(again), sr.REVIEWED_CLEAN)
        self.assertEqual(again.state["credits"]["balance"], "25.00")

    def test_rejection_a_missing_credit_snapshot_stops_the_codex_step(self):
        self.h.readings = [reading(), reading(), reading(credits=None)]
        self.h.builder_script = [edit(self.root, "x\n")]
        run = self.start()
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertIn("missing", run.state["stop_reasons"][-1]["evidence"]["detail"])

    def test_negative_a_credit_change_does_not_stop_a_builder_step(self):
        changed = dict(CREDITS, balance="4.50")
        self.h.readings = [reading(), reading(credits=changed), reading()]
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        self.assertEqual(self.reason(self.start()), sr.REVIEWED_CLEAN)

    def test_rejection_allowance_reached_with_credits_next_stops_the_codex_step(self):
        self.h.readings = [reading(), reading(), reading(reached="rate_limit_reached")]
        self.h.builder_script = [edit(self.root, "x\n")]
        run = self.start()
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertIn("credits next", run.state["stop_reasons"][-1]["evidence"]["detail"])

    def test_rejection_untrackable_credits_refuse_the_run(self):
        for credits in (None, {"hasCredits": True, "unlimited": False}):
            self.h.readings = [reading(credits=credits)]
            with self.assertRaises(loop.RunRefused):
                self.start()

    def test_rejection_a_refusal_for_credits_names_what_codex_answered(self):
        """A reading with no credit snapshot is usually an unavailable one, and the
        refusal must carry its reason rather than only "no credits snapshot" (proof run
        2's resume from Codex was refused twice with nothing to go on)."""
        self.h.readings = [reading(credits=None, source="unavailable")]
        with self.assertRaises(loop.RunRefused) as caught:
            self.start()
        self.assertIn("Codex usage not reported: x", caught.exception.args[0][0])
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        # The next step is the Codex review, so untrackable credits refuse the resume
        # (a resume whose next step is a Claude one records them as read, A3 chunk 3).
        run = self.start(inject="review-R1")
        self.h.readings = [reading(credits=None, source="unavailable")]
        with self.assertRaises(loop.RunRefused) as caught:
            loop.resume(run)
        self.assertIn("Codex usage not reported: x", caught.exception.args[0][0])
        self.assertIn("the run is unchanged", caught.exception.args[0][0])

    SANDBOXED = ("InvalidRequestError: JSON-RPC error -32600: codex account "
                 "authentication required to read rate limits")

    def sandboxed_reading(self, reason=SANDBOXED):
        return limits.Reading(limits.CODEX, limits.UNAVAILABLE, reason=reason,
                              raw={"rateLimits": {}})

    def test_rejection_a_start_inside_codex_sandbox_is_refused_saying_so(self):
        """The reading Codex answers inside its sandbox, in a process a Codex session
        started (probed 2026-10-12), refuses the start naming the sandbox and the way
        out, with nothing created, instead of "credits cannot be tracked". Docker is
        stopped, and since A3 chunk 3 a start only asks its status (code review R1-1's
        stop is approval's now), so nothing is started."""
        self.h.docker_up = False
        self.h.readings = [self.sandboxed_reading()]
        with mock.patch.dict(os.environ, {loop.CODEX_SESSION_ENV: "thread-1"}):
            with self.assertRaises(loop.RunRefused) as caught:
                self.start()
        message = caught.exception.args[0][0]
        self.assertIn("inside Codex's sandbox", message)
        self.assertIn("run the same command again outside the sandbox", message)
        self.assertIn("authentication required to read rate limits", message)
        self.assertNotIn("credits cannot be tracked", message)
        self.assertEqual(self.h.docker_calls, ["status"])
        self.assertFalse(self.h.docker_up)
        self.assertFalse(self.runs.exists())

    def test_rejection_a_resume_inside_codex_sandbox_is_refused_unchanged(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start(inject="build")
        state = (run.run_dir / "state.json").read_bytes()
        self.h.readings = [self.sandboxed_reading()]
        with mock.patch.dict(os.environ, {loop.CODEX_SESSION_ENV: "thread-1"}):
            with self.assertRaises(loop.RunRefused) as caught:
                loop.resume(run)
        message = caught.exception.args[0][0]
        self.assertIn("inside Codex's sandbox", message)
        self.assertTrue(message.endswith("; the run is unchanged"))
        self.assertEqual((run.run_dir / "state.json").read_bytes(), state)

    def test_negative_the_same_answer_outside_a_codex_session_keeps_the_old_refusal(self):
        """Without a Codex session the error is some other login problem, so the
        ordinary refusal, which names what Codex answered, stands."""
        self.h.readings = [self.sandboxed_reading()]
        with mock.patch.dict(os.environ):
            os.environ.pop(loop.CODEX_SESSION_ENV, None)
            with self.assertRaises(loop.RunRefused) as caught:
                self.start()
        message = caught.exception.args[0][0]
        self.assertIn("credits cannot be tracked", message)
        self.assertNotIn("sandbox", message)

    def test_negative_another_failure_inside_a_codex_session_keeps_the_old_refusal(self):
        self.h.readings = [self.sandboxed_reading(reason="TimeoutError: no answer")]
        with mock.patch.dict(os.environ, {loop.CODEX_SESSION_ENV: "thread-1"}):
            with self.assertRaises(loop.RunRefused) as caught:
                self.start()
        self.assertIn("credits cannot be tracked", caught.exception.args[0][0])
        self.assertNotIn("sandbox", caught.exception.args[0][0])

    def test_negative_sandbox_refusal_needs_an_unavailable_reading(self):
        env = {loop.CODEX_SESSION_ENV: "thread-1"}
        self.assertIsNotNone(loop.sandbox_refusal(self.sandboxed_reading(), env))
        self.assertIsNone(loop.sandbox_refusal(reading(), env))
        reported = limits.Reading(limits.CODEX, limits.REPORTED, percent=30,
                                  reason=self.SANDBOXED)
        self.assertIsNone(loop.sandbox_refusal(reported, env))
        self.assertIsNone(loop.sandbox_refusal(self.sandboxed_reading(), {}))
        self.assertIsNone(loop.sandbox_refusal(
            self.sandboxed_reading(), {loop.CODEX_SESSION_ENV: ""}))

    def test_negative_no_credits_and_not_unlimited_needs_no_balance(self):
        self.h.readings = [reading(credits={"hasCredits": False, "unlimited": False})] * 3
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        self.assertEqual(self.reason(self.start()), sr.REVIEWED_CLEAN)

    def test_rejection_a_failed_reading_before_a_step_ends_error_not_a_crash(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        calls = []

        def read(root, folder=None):
            calls.append(1)
            if len(calls) == 3:
                raise RuntimeError("transport closed")
            return reading()
        deps = self.h.deps()
        deps.read_codex = read
        run = start_built("brief.md", "demo", str(self.personal), root=self.root,
                          deps=deps, log_path=self.log, runs_dir=self.runs,
                          settings=self.settings)
        asyncio.run(run.advance())
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertIn("usage reading", run.state["stop_reasons"][-1]["evidence"]["error"])


class LimitStopTests(LoopCase):

    def test_positive_an_injected_builder_limit_stops_cleanly_and_resumes(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start(inject="build")
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(run.state["step"], loop.APPROVED_STEP)  # the last completed step
        self.assertTrue(run.state["inject"]["fired"])
        self.assertIn("injected", run.state["stop_reasons"][-1]["evidence"]["detail"])
        again = self.resume(run)
        self.assertEqual(self.reason(again), sr.REVIEWED_CLEAN)
        self.assertEqual(self.log_actions(), ["started", "completed", "started",
                                              "completed"])

    def test_positive_an_injected_review_limit_stops_cleanly_and_resumes(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start(inject="review-R1")
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(run.state["step"], "build")
        self.assertEqual(self.h.review_calls, [])
        again = self.resume(run)
        self.assertEqual(self.reason(again), sr.REVIEWED_CLEAN)
        self.assertEqual(len(self.h.review_calls), 1)

    def test_positive_a_builder_cut_off_mid_edit_resumes_in_its_session(self):
        def partial(prompt):
            (self.root / "work" / "a.txt").write_bytes(b"half\n")
            raise limits.UsageLimitError(limits.CLAUDE, "rate_limit", resets_at=None)
        self.h.builder_script = [partial, edit(self.root, "whole\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(run.state["builder_session"], "s-1")
        patch = (run.run_dir / "partial" / "build.patch").read_text(encoding="utf-8")
        self.assertIn("+half", patch)
        again = self.resume(run)
        self.assertEqual(self.reason(again), sr.REVIEWED_CLEAN)
        self.assertEqual(self.h.builders[-1].resume, "s-1")
        self.assertIn("interrupted", self.h.builders[-1].prompts[0][1])

    def test_positive_wait_sleeps_to_the_reset_and_resumes_itself(self):
        def limited(prompt):
            raise limits.UsageLimitError(limits.CLAUDE, "rate_limit",
                                         resets_at=self.h.clock + 600)
        self.h.builder_script = [limited, edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start(wait=True)
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        self.assertEqual(self.h.slept, [600 + loop.WAIT_MARGIN_SECONDS])

    def test_wait_continues_through_two_codex_windows_above_the_ceiling(self):
        first = self.h.clock + 600
        second = self.h.clock + 1200
        credits = {"rateLimits": {"credits": dict(CREDITS)}}
        primary = limits.Reading(limits.CODEX, limits.REPORTED, percent=90,
                                 window="primary", resets_at=first, raw=credits)
        secondary = limits.Reading(limits.CODEX, limits.REPORTED, percent=85,
                                   window="secondary", resets_at=second, raw=credits)
        # The first window resets while the second remains over the ceiling.
        # The resumed preflight must reach before_step to record the later reset. Since
        # A3 chunk 3 Codex's readings stop only a Codex step, so the stops come before
        # the review, after a build read at 30%.
        self.h.readings = [reading(), reading(), primary, secondary, secondary]
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        deps = self.h.deps()

        def sleep(seconds):
            self.h.slept.append(seconds)
            self.h.clock += seconds

        deps.sleep = sleep
        run = start_built("brief.md", "demo", str(self.personal), root=self.root,
                          deps=deps, log_path=self.log, runs_dir=self.runs,
                          settings=self.settings, builder="claude", wait=True)
        asyncio.run(run.advance())
        self.assertEqual(self.h.slept, [600 + loop.WAIT_MARGIN_SECONDS, 600])
        self.assertEqual([stop["reason"] for stop in run.state["stop_reasons"]],
                         [sr.USAGE_LIMIT, sr.USAGE_LIMIT, sr.REVIEWED_CLEAN])
        self.assertEqual(len(self.h.builders), 1)
        self.assertFalse(run.state.get("paid_usage"))

    def test_negative_wait_does_nothing_without_a_reset_time(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        run = self.start(inject="build", wait=True)
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(self.h.slept, [])
        report = (run.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("not reported", report)

    def test_rejection_an_injection_that_is_not_a_step_is_refused(self):
        with self.assertRaises(loop.RunRefused):
            self.start(inject="review")


class PreflightTests(LoopCase):

    def test_rejection_every_refusal_is_named_and_nothing_is_created(self):
        (self.root / "memory" / "demo_review_packet.md").write_bytes(b"old\n")
        deps = self.h.deps()
        deps.capture_problems = lambda root, git_dir: ["not captured"]
        with self.assertRaises(loop.RunRefused) as caught:
            loop.preflight("brief.md", "demo", str(self.personal), root=self.root,
                           deps=deps, settings=self.settings)
        text = " ".join(caught.exception.args[0])
        self.assertIn("already exists", text)
        self.assertIn("not captured", text)
        self.assertFalse(self.runs.exists())

    def test_rejection_an_unreadable_classification_refuses_the_run(self):
        deps = self.h.deps()

        def missing(root):
            raise worktree.GitError("the sync plan is missing")
        deps.category_a = missing
        with self.assertRaises(loop.RunRefused) as caught:
            loop.preflight("brief.md", "demo", str(self.personal), root=self.root,
                           deps=deps, settings=self.settings)
        self.assertIn("sync classification", " ".join(caught.exception.args[0]))

    def test_rejection_uncommitted_work_under_the_edit_paths_refuses_the_run(self):
        (self.root / "work" / "a.txt").write_bytes(b"work already here\n")
        (self.root / "work" / "new.txt").write_bytes(b"untracked\n")
        with self.assertRaises(loop.RunRefused) as caught:
            self.start()
        text = " ".join(caught.exception.args[0])
        self.assertIn("work/a.txt", text)
        self.assertIn("work/new.txt", text)
        self.assertIn("commit them first", text)
        self.assertFalse(self.runs.exists())

    def test_negative_env_and_uncommitted_work_elsewhere_do_not_refuse_the_run(self):
        (self.root / "work" / ".env").write_bytes(b"SECRET=x\n")  # never part of a run
        # Ignored, as a real one must be: a run does not start while a .env-named file
        # is neither tracked nor ignored (chunk (d), short plan 5.1 rule 3).
        with open(self.root / ".git" / "info" / "exclude", "ab") as handle:
            handle.write(b".env\n")
        (self.root / "brief.md").write_bytes(BRIEF.encode("utf-8") + b"\n")  # outside
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        self.assertEqual(self.reason(self.start()), sr.REVIEWED_CLEAN)

    def test_positive_the_clean_start_check_is_recorded(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(run.state["baseline"], self.h.baseline)

    def test_rejection_a_tree_whose_checks_are_not_clean_refuses_the_run(self):
        self.h.baseline = {"clean": False, "status": "fail",
                           "problems": ["close-out gate failed: tests: 1 failed"]}
        with self.assertRaises(loop.RunRefused) as caught:
            self.start()
        text = " ".join(caught.exception.args[0])
        self.assertIn("not clean", text)
        self.assertIn("close-out gate failed: tests", text)
        self.assertFalse(self.runs.exists())
        self.assertEqual(self.h.builders, [])
        self.assertEqual(self.h.frozen_calls, [], "no frozen folder for a refused start")

    def test_rejection_a_refused_resume_leaves_the_run_exactly_as_it_was(self):
        self.h.builder_script = [edit(self.root, "x\n"), reply(actions(("R1-1", "fixed")))]
        self.h.reviews = [[finding()]]
        run = self.start(cap=1)
        self.assertEqual(self.reason(run), sr.MAX_ROUNDS)
        before = {name: (run.run_dir / name).read_bytes()
                  for name in ("state.json", "report.md", "report.json")}
        log_before = self.log.read_bytes()
        # The next step is the Claude fix pass, so since A3 chunk 3 untrackable credits
        # no longer refuse this resume (they are recorded as read); a claim already held
        # (resume.lock, plan 10.10 item 5) is the second refusal tried instead.
        lock = run.run_dir / loop.LOCK_FILE
        for trouble in ("a claim already held", RuntimeError("transport closed")):
            deps = self.h.deps()
            if isinstance(trouble, Exception):
                deps.read_codex = (lambda root, folder=None, r=trouble:
                                   (_ for _ in ()).throw(r))
            else:
                lock.write_bytes(b"held\n")
            again = loop.load(run.state["run_id"], root=self.root, deps=deps,
                              log_path=self.log, runs_dir=self.runs)
            with self.assertRaises(loop.RunRefused):
                loop.resume(again, rounds=1)
            lock.unlink(missing_ok=True)
            for name, content in before.items():
                self.assertEqual((run.run_dir / name).read_bytes(), content, name)
            self.assertEqual(self.log.read_bytes(), log_before)

    def test_rejection_an_unavailable_codex_reading_is_reported_as_the_plan_asks(self):
        no_windows = limits.Reading(limits.CODEX, limits.UNAVAILABLE,
                                    reason="the response carried neither usage window",
                                    raw={"rateLimits": {"credits": dict(CREDITS)}})
        self.h.readings = [no_windows, reading(), reading()]
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        report = json.loads((run.run_dir / "report.json").read_text(encoding="utf-8"))
        source = report["usage_source"]
        self.assertEqual(source["codex"], "unavailable")
        self.assertEqual(source["codex_reason"], "the response carried neither usage window")
        self.assertEqual(source["codex_raw"], no_windows.raw)
        first = (run.run_dir / "report.md").read_text(encoding="utf-8").splitlines()[2]
        self.assertTrue(first.startswith("Codex usage not reported: the response"))
        # It holds for the rest of the run: later readings are not trusted for the ceiling.
        self.assertTrue(all("Codex usage not reported" in r["codex"]
                            for r in run.state["readings"]))

    def test_negative_a_reported_codex_reading_is_named_as_reported(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        report = json.loads((run.run_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["usage_source"], {"codex": "reported"})
        self.assertNotIn("not reported", (run.run_dir / "report.md").read_text(
            encoding="utf-8").splitlines()[2])

    def test_rejection_a_bad_item_or_brief_is_refused(self):
        (self.root / "bad.md").write_bytes(b"## Goal\n")
        with self.assertRaises(loop.RunRefused) as caught:
            loop.preflight("bad.md", "Bad Item", str(self.personal), root=self.root,
                           deps=self.h.deps(), settings=self.settings)
        text = " ".join(caught.exception.args[0])
        self.assertIn("--item", text)
        self.assertIn("does not pass the check", text)

    def test_positive_a_dry_run_creates_nothing(self):
        with redirect_stdout(io.StringIO()):
            result = loop.start("brief.md", "demo", str(self.personal), root=self.root,
                                deps=self.h.deps(), log_path=self.log,
                                runs_dir=self.runs, dry_run=True, settings=self.settings)
        self.assertIsNone(result)
        self.assertFalse(self.runs.exists())
        self.assertFalse(self.log.exists())

    def test_rejection_a_dry_run_makes_the_start_check_and_refuses_on_it(self):
        # Rewritten for orchestrator isolation S6b (plan 7.1 item 2; it replaces R6-1's
        # "NOT checked" preview): the read-only verifier lets a dry run make the check,
        # so an unclean tree refuses a dry run exactly as it refuses a real start, and
        # the image step is never reached.
        self.h.baseline = {"clean": False, "status": "fail", "problems": ["x"]}
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(loop.RunRefused) as caught:
            loop.start("brief.md", "demo", str(self.personal), root=self.root,
                       deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                       dry_run=True, settings=self.settings)
        self.assertIn("not clean", " ".join(caught.exception.args[0]))
        self.assertEqual(self.h.start_check_calls, [{"read_only": True}])
        self.assertEqual(self.h.image_calls, [])
        self.assertNotIn("NOT checked", err.getvalue())
        self.assertNotIn("preflight passed", err.getvalue())
        self.assertFalse(self.runs.exists())

    def test_positive_the_capture_check_on_a_clean_repository(self):
        self.assertEqual(worktree.git(self.root, "status", "--short",
                                      "--untracked-files=no", git_dir=str(self.personal)),
                         "")

    def test_rejection_the_capture_check_sees_an_uncommitted_personal_file(self):
        (self.root / "memory" / "note.md").write_bytes(b"changed\n")
        problems = loop.capture_problems(self.root, str(self.personal))
        self.assertTrue(any("uncommitted" in p for p in problems))


class FrozenFolderStartTests(LoopCase):
    """Orchestrator isolation plan 6 item 1 and 7.1 item 5 (S6a): the orchestrator, not
    a builder session, makes the run's frozen folder, for either builder."""

    def test_positive_a_start_makes_the_frozen_folder_for_its_own_run(self):
        for builder in ("claude", "codex"):
            with self.subTest(builder=builder):
                self.setUp()
                self.h.swapped = builder == "codex"
                if self.h.swapped:
                    self.h.frozen = self.root.parent / "codex-frozen"
                    self.h.frozen.mkdir()
                self.h.builder_script = [edit(self.root, "x\n")]
                self.h.reviews = [[]]
                run = self.start()
                head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(self.root),
                                      capture_output=True, encoding="utf-8").stdout.strip()
                call, = self.h.frozen_calls
                self.assertEqual(call, {"root": self.root, "run_id": run.state["run_id"],
                                        "public_head": head, "builder": builder})
                self.assertTrue(run.state["frozen_dir"])
                self.assertTrue(Path(run.state["frozen_dir"]).is_dir())
                # A3 chunk 1: the edit paths and the image tag are in approval.json.
                self.assertEqual(codex_rules.read_approval(run.state["frozen_dir"]),
                                 {"edit_paths": ["work/"], "image_tag": self.h.image_tag})

    def test_rejection_a_frozen_folder_that_cannot_be_made_refuses_the_start(self):
        self.h.frozen_error = frozen.FrozenError("the commit holds no workflows/audit run.py")
        with self.assertRaises(loop.RunRefused) as caught:
            self.start()
        self.assertIn("the commit holds no workflows/audit run.py",
                      " ".join(caught.exception.args[0]))
        self.assertFalse(self.runs.exists(), "no run record")
        self.assertFalse(self.log.exists(), "no started entry")
        self.assertEqual(self.h.builders, [])

    def test_rejection_an_unwritable_temp_folder_refuses_the_start(self):
        # Code review R19-2: the real frozen.setup, its parent folder refused by the
        # file system, reaches the start as a refusal, with nothing of the run made.
        temp = self.root.parent / "temp"
        refused = temp / frozen.FROZEN_PARENT
        original = Path.mkdir

        def mkdir(path, *args, **kwargs):
            if Path(path) == refused:
                raise PermissionError(13, "Access is denied", str(path))
            return original(path, *args, **kwargs)
        deps = self.h.deps()
        deps.setup_frozen = lambda root, run_id, **kw: frozen.setup(
            root, run_id, temp_root=temp, **kw)
        settings = json.loads(json.dumps(self.settings))
        with mock.patch.object(Path, "mkdir", mkdir), \
                self.assertRaises(loop.RunRefused) as caught:
            loop.start("brief.md", "demo", str(self.personal), root=self.root, deps=deps,
                       log_path=self.log, runs_dir=self.runs, settings=settings,
                       builder="claude")
        text = " ".join(caught.exception.args[0])
        self.assertIn("cannot be made", text)
        self.assertIn("Access is denied", text)
        self.assertFalse(self.runs.exists(), "no run record")
        self.assertFalse(self.log.exists(), "no started entry")

    def test_rejection_a_run_record_that_cannot_be_made_removes_the_frozen_folder(self):
        # Since code review R29-1 a failing create_run is a refusal, not a traceback.
        def refuse(*args, **kwargs):
            raise ValueError("refused")
        original = runrecord.create_run
        runrecord.create_run = refuse
        try:
            with self.assertRaises(loop.RunRefused) as caught:
                self.start()
        finally:
            runrecord.create_run = original
        self.assertIn("the run record could not be made: ValueError: refused",
                      " ".join(caught.exception.args[0]))
        call, = self.h.frozen_calls
        self.assertFalse((self.root.parent / "frozen" / call["run_id"]).exists())

    def test_rejection_a_first_state_write_that_fails_removes_the_half_made_folder(self):
        # Code review R29-1: create_run made the folder, then its first state.json
        # write failed. The folder this attempt made goes, with the frozen folder; the
        # start is refused. Since A3 chunk 3 a start makes no Docker command but the
        # status query, so there is no Docker of its own to stop.
        self.h.docker_up = False
        original = runrecord.write_state

        def refuse(run_dir, state):
            raise PermissionError(13, "Access is denied", str(run_dir))
        runrecord.write_state = refuse
        try:
            with redirect_stderr(io.StringIO()), \
                    self.assertRaises(loop.RunRefused) as caught:
                self.start()
        finally:
            runrecord.write_state = original
        self.assertIn("the run record could not be made: PermissionError",
                      " ".join(caught.exception.args[0]))
        call, = self.h.frozen_calls
        self.assertFalse((self.runs / call["run_id"]).exists())
        self.assertFalse((self.root.parent / "frozen" / call["run_id"]).exists())
        self.assertEqual(self.h.docker_calls, ["status"])
        self.assertFalse(self.h.docker_up)

    def test_negative_a_run_folder_that_was_already_there_is_never_removed(self):
        # The run id's folder existing before the attempt: create_run refuses it, and
        # the start refuses without removing what it did not make.
        clash = "20261009-120000-abcd"
        (self.runs / clash).mkdir(parents=True)
        (self.runs / clash / "keep.txt").write_bytes(b"someone else's\n")
        with mock.patch.object(loop.runrecord, "new_run_id", return_value=clash), \
                redirect_stderr(io.StringIO()), self.assertRaises(loop.RunRefused) as caught:
            self.start()
        self.assertIn("FileExistsError", " ".join(caught.exception.args[0]))
        self.assertEqual((self.runs / clash / "keep.txt").read_bytes(), b"someone else's\n")

    def test_negative_a_dry_run_makes_no_frozen_folder(self):
        with redirect_stderr(io.StringIO()):
            self.assertIsNone(loop.start("brief.md", "demo", str(self.personal),
                                         root=self.root, deps=self.h.deps(),
                                         log_path=self.log, runs_dir=self.runs,
                                         dry_run=True, settings=self.settings))
        self.assertEqual(self.h.frozen_calls, [])

    def test_positive_resume_and_stop_commands_name_the_starter(self):
        # Plan 7.0 item 3: the report and the pause message print the starter, with the
        # run's own id, never the live run.py.
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[finding()]]
        run = self.start(cap=1)
        self.assertEqual(self.reason(run), sr.MAX_ROUNDS)
        run_id = run.state["run_id"]
        report = (run.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn(f"python workflows/review-orchestration/scripts/start.py --resume "
                      f"{run_id} --rounds N", report)
        self.assertNotIn("run.py --resume", report)
        self.assertEqual(loop.pause_commands(run_id),
                         f"Resume it with: python workflows/review-orchestration/scripts/"
                         f"start.py --resume {run_id}; or end it with: python workflows/"
                         f"review-orchestration/scripts/start.py --stop {run_id}")
        for reason, text in loop.RECOMMEND.items():
            self.assertNotIn("run.py", text, reason)


class DockerStartTests(LoopCase):
    """Orchestrator isolation S6b, plan 7.1 items 1 to 4 and section 8 ("Dry run",
    "Refused start and Docker"), as A3 chunk 3 moved them (plan 10.3 step 6, "Docker and
    the check image wait for approval"): a start makes the read-only start check and
    Docker's status query and nothing else of Docker; Docker and the image step are
    approval's, and every refusal there stops a Docker that approval started. Against
    the harness's fake Docker (no test touches the real one)."""

    def paused(self):
        """A run at the approval pause, its start's Docker calls cleared."""
        run = loop.start("brief.md", "demo", str(self.personal), root=self.root,
                         deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                         settings=self.settings, builder="claude")
        pause_planned(run)
        self.h.docker_calls.clear()
        return run

    def approve_refused(self, run):
        with redirect_stderr(io.StringIO()) as err, \
                self.assertRaises(loop.RunRefused) as caught:
            loop.approve(run)
        self.err = err.getvalue()
        return " ".join(caught.exception.args[0])

    def approve_command(self, run):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = run_py.main(["--approve", run.state["run_id"]], root=self.root,
                               log_path=self.log, deps=self.h.deps(), runs_dir=self.runs)
        return code, out.getvalue()

    def dry(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertIsNone(loop.start("brief.md", "demo", str(self.personal),
                                         root=self.root, deps=self.h.deps(),
                                         log_path=self.log, runs_dir=self.runs,
                                         dry_run=True, settings=self.settings))
        return err.getvalue()

    def refused(self, **kwargs):
        with redirect_stderr(io.StringIO()), self.assertRaises(loop.RunRefused) as caught:
            loop.start("brief.md", "demo", str(self.personal), root=self.root,
                       deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                       settings=self.settings, builder="claude", **kwargs)
        return " ".join(caught.exception.args[0])

    def test_positive_a_clean_dry_run_previews_the_check_passed_and_a_reuse(self):
        text = self.dry()
        self.assertIn("the close-out verifier's clean-start check passed", text)
        self.assertIn("Docker is running", text)
        self.assertIn("Docker and the check image wait for --approve", text)
        self.assertEqual(self.h.start_check_calls, [{"read_only": True}])
        self.assertEqual(self.h.docker_calls, ["status"])
        self.assertEqual(self.h.image_calls, [], "no image step before approval")
        self.assertFalse(self.runs.exists())
        self.assertFalse(self.log.exists())

    def test_positive_a_dry_run_previews_a_build_when_the_tag_is_absent(self):
        # Since A3 chunk 3 the image waits for approval, so even a missing tag is
        # neither asked about nor built by a start's dry run.
        self.h.image_exists = False
        text = self.dry()
        self.assertNotIn("build it", text)
        self.assertNotIn("inspect", self.h.docker_calls)
        self.assertNotIn("build", self.h.docker_calls)

    def test_rejection_a_dry_run_with_docker_stopped_starts_nothing(self):
        self.h.docker_up = False
        text = self.dry()
        self.assertIn("Docker is not running; approval will start it", text)
        self.assertEqual(self.h.docker_calls, ["status"])
        self.assertFalse(self.h.docker_up)

    def test_rejection_a_failing_real_start_check_never_reaches_the_image_step(self):
        self.h.baseline = {"clean": False, "status": "fail", "problems": ["x"]}
        self.assertIn("not clean", self.refused())
        self.assertEqual(self.h.image_calls, [])
        self.assertNotIn("stop", self.h.docker_calls)  # Docker was already running
        self.assertFalse(self.runs.exists())

    def test_rejection_a_start_that_started_docker_stops_it_once_when_the_check_refuses(self):
        # A start no longer starts Docker at all: a refused start check leaves a
        # stopped Docker stopped, with the status query its one Docker command.
        self.h.docker_up = False
        self.h.baseline = {"clean": False, "status": "fail", "problems": ["x"]}
        self.refused()
        self.assertEqual(self.h.docker_calls, ["status"])
        self.assertFalse(self.h.docker_up)

    def test_rejection_a_start_that_started_docker_stops_it_once_when_the_image_refuses(self):
        # The image step is approval's: an approval that started Docker stops it once
        # when the image refuses, leaves no temporary folder and stays approvable.
        self.h.docker_up = False
        run = self.paused()
        self.h.image_error = container.ContainerError("build failed")
        text = self.approve_refused(run)
        self.assertIn("the check image: build failed", text)
        self.assertEqual(self.h.docker_calls.count("stop"), 1)
        self.assertEqual(self.h.docker_calls[-1], "stop")
        call, = self.h.image_calls
        self.assertFalse(call["work_dir"].exists(), "no temporary folder left")
        self.assertEqual(runrecord.read_state(run.run_dir)["pause"],
                         sr.AWAITING_PLAN_APPROVAL)

    def test_rejection_a_start_that_started_docker_stops_it_when_the_usage_refuses(self):
        self.h.docker_up = False
        self.h.readings = [reading(percent=95)]
        self.assertIn("usage", self.refused())
        self.assertEqual(self.h.docker_calls, ["status"])
        self.assertFalse(self.runs.exists())

    def test_rejection_a_file_error_in_the_image_step_is_a_refusal(self):
        # Code review R30-1, at approval since A3 chunk 3: a constraints or
        # build-context write failing is the image step's refusal through run.py (exit
        # 3), not a traceback; the temporary folder goes and the Docker the approval
        # started is stopped.
        self.h.docker_up = False
        run = self.paused()
        self.h.image_error = PermissionError(13, "Access is denied")
        code, out = self.approve_command(run)
        self.assertEqual(code, run_py.REFUSED)
        self.assertIn("the check image: PermissionError", out)
        call, = self.h.image_calls
        self.assertFalse(call["work_dir"].exists())
        self.assertEqual(self.h.docker_calls.count("stop"), 1)

    def keep_image_folder(self):
        """Code review R31-1: the image step's temporary folder cannot be removed;
        every other removal works as usual."""
        original = container.remove_copy

        def remove(folder):
            if Path(folder).name.startswith("book-dragon-image-"):
                raise PermissionError(13, "Access is denied", str(folder))
            return original(folder)
        return mock.patch.object(loop.container, "remove_copy", remove)

    def test_rejection_an_image_folder_left_after_a_start_is_reported_not_hidden(self):
        # At approval since A3 chunk 3: the approval goes ahead and says so.
        run = self.paused()
        err = io.StringIO()
        with self.keep_image_folder(), redirect_stderr(err):
            loop.approve(run)
        self.assertEqual(run.state["status"], sr.RUNNING, "the approval went ahead")
        self.assertIn("WARNING: the image step's temporary folder", err.getvalue())
        self.assertIn("delete it by hand", err.getvalue())
        shutil.rmtree(self.h.image_calls[0]["work_dir"], ignore_errors=True)

    def test_rejection_an_image_folder_left_after_a_refusal_is_in_the_refusal(self):
        run = self.paused()
        self.h.image_error = container.ContainerError("build failed")
        with self.keep_image_folder():
            text = self.approve_refused(run)
        self.assertIn("the check image: build failed", text)
        self.assertIn("the image step's temporary folder", self.err)
        self.assertIn("could not be removed", self.err)
        shutil.rmtree(self.h.image_calls[0]["work_dir"], ignore_errors=True)

    def test_rejection_a_temporary_folder_that_cannot_be_made_is_a_refusal(self):
        self.h.docker_up = False
        run = self.paused()
        with mock.patch.object(loop.tempfile, "mkdtemp",
                               side_effect=PermissionError(13, "Access is denied")):
            text = self.approve_refused(run)
        self.assertIn("its temporary folder could not be made", text)
        self.assertEqual(self.h.image_calls, [])
        self.assertEqual(self.h.docker_calls.count("stop"), 1)

    def test_negative_docker_already_running_is_left_running_after_a_refusal(self):
        run = self.paused()
        self.h.image_error = container.ContainerError("build failed")
        self.approve_refused(run)
        self.assertNotIn("stop", self.h.docker_calls)
        self.assertTrue(self.h.docker_up)

    def test_rejection_a_failing_stop_is_printed_with_the_refusal_and_keeps_its_code(self):
        self.h.docker_up = False
        run = self.paused()
        self.h.docker_stop_error = container.ContainerError("stop refused")
        self.h.image_error = container.ContainerError("build failed")
        code, text = self.approve_command(run)
        self.assertEqual(code, run_py.REFUSED)
        self.assertIn("the check image: build failed", text)
        self.assertIn("could not be stopped again: stop refused", text)

    def test_rejection_a_dry_run_whose_docker_status_fails_is_refused(self):
        # Code review R27-1: a status query that cannot run refuses a dry start as it
        # refuses a real one, rather than escaping as a traceback.
        self.h.docker_status_error = container.ContainerError(
            "`docker desktop status` did not answer within 60 s")
        with redirect_stderr(io.StringIO()), self.assertRaises(loop.RunRefused) as caught:
            loop.start("brief.md", "demo", str(self.personal), root=self.root,
                       deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                       dry_run=True, settings=self.settings)
        self.assertIn("Docker: `docker desktop status` did not answer",
                      " ".join(caught.exception.args[0]))
        self.assertEqual(self.h.start_check_calls, [])
        self.assertEqual(self.h.image_calls, [])
        self.assertFalse(self.runs.exists())

    def test_rejection_docker_that_cannot_be_started_refuses_with_nothing_to_stop(self):
        self.h.docker_up = False
        run = self.paused()
        self.h.docker_start_error = container.ContainerError("no engine")
        self.assertIn("Docker: no engine", self.approve_refused(run))
        self.assertNotIn("stop", self.h.docker_calls)
        self.assertEqual(self.h.image_calls, [])
        self.assertFalse((Path(run.state["frozen_dir"])
                          / codex_rules.APPROVAL_FILE).exists())


class ConstraintsFileTests(LoopCase):
    """Decision 32 and R24-3 (plan 5.2, 7.1 item 4), as A3 chunk 3 moved the image step
    to approval: approval writes the constraints file to a temporary folder, the run
    keeps it as runs/<run-id>/image/constraints.txt and the folder is removed; a start
    makes no image step, and one that fails once its record exists removes the record
    and the frozen folder (R28-1)."""

    def broken_record_start(self):
        """``worktree.record_start`` failing, a write made once runs/<run-id>/ exists."""
        def refuse(*args, **kwargs):
            raise FileNotFoundError(2, "No such file", "start")
        return mock.patch.object(loop.worktree, "record_start", refuse)

    def test_positive_the_file_is_copied_into_the_run_record_once_it_exists(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        call, = self.h.image_calls
        self.assertFalse(call["dry_run"])
        self.assertEqual(call["runs_existed"], [run.state["run_id"]],
                         "the image step runs at approval, after the record")
        self.assertEqual((run.run_dir / "image" / "constraints.txt").read_bytes(),
                         b"pkg==1.0\n")
        self.assertFalse(call["work_dir"].exists(), "the temporary folder is removed")
        self.assertEqual(codex_rules.read_approval(run.state["frozen_dir"])["image_tag"],
                         self.h.image_tag)
        self.assertEqual(run.state["image"], {"tag": self.h.image_tag, "built": False})

    def test_rejection_a_start_refused_after_the_image_leaves_no_folder_and_no_record(self):
        self.h.frozen_error = frozen.FrozenError("no folder")
        with redirect_stderr(io.StringIO()), self.assertRaises(loop.RunRefused):
            loop.start("brief.md", "demo", str(self.personal), root=self.root,
                       deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                       settings=self.settings, builder="claude")
        self.assertEqual(self.h.image_calls, [], "a start makes no image step")
        self.assertFalse(self.runs.exists())

    def test_rejection_a_failure_after_the_record_is_made_removes_it_and_refuses(self):
        # Code review R28-1: a write failing once runs/<run-id>/ exists (here the start
        # record) removes the new record and frozen folder, refuses the start through
        # run.py (exit 3, no traceback), and appends no started entry. Since A3 chunk 3
        # a start makes no Docker command but the status query.
        self.h.docker_up = False
        out = io.StringIO()
        with self.broken_record_start(), redirect_stdout(out), \
                redirect_stderr(io.StringIO()):
            code = run_py.main(["--run", "brief.md", "--builder", "claude", "--item",
                                "demo", "--git-dir", str(self.personal)],
                               root=self.root, log_path=self.log, deps=self.h.deps(),
                               runs_dir=self.runs)
        self.assertEqual(code, run_py.REFUSED)
        self.assertIn("the run record could not be completed: FileNotFoundError",
                      out.getvalue())
        call, = self.h.frozen_calls
        self.assertEqual(list(self.runs.glob("*")), [], "no half-made run record")
        self.assertFalse((self.root.parent / "frozen" / call["run_id"]).exists())
        self.assertEqual(self.h.docker_calls, ["status"])
        self.assertFalse(self.log.exists(), "no started entry")

    def test_rejection_a_half_made_record_that_cannot_be_removed_is_named(self):
        original = container.remove_copy

        def refuse(folder):
            if Path(folder).parent == self.runs:
                raise PermissionError(13, "Access is denied", str(folder))
            return original(folder)
        with mock.patch.object(loop.container, "remove_copy", refuse), \
                self.broken_record_start(), \
                redirect_stderr(io.StringIO()), self.assertRaises(loop.RunRefused) as caught:
            loop.start("brief.md", "demo", str(self.personal), root=self.root,
                       deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                       settings=self.settings, builder="claude")
        text = " ".join(caught.exception.args[0])
        self.assertIn("the half-made run record", text)
        self.assertIn("could not be removed", text)
        self.assertIn("delete it by hand", text)
        call, = self.h.frozen_calls
        self.assertFalse((self.root.parent / "frozen" / call["run_id"]).exists(),
                         "the frozen folder is still removed")

    def test_positive_a_built_image_is_recorded_as_built(self):
        self.h.image_exists = False
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(run.state["image"]["built"], True)
        self.assertIn("build", self.h.docker_calls)
        self.assertIn(f"image {self.h.image_tag}: built at approval",
                      (run.run_dir / "report.md").read_text(encoding="utf-8"))


class EndOfRunTests(LoopCase):
    """Plan 7.3: after the report, Docker is stopped if this run started it, and at a
    stop reason a resume cannot continue the frozen folder and the run's clean copies
    are removed; on max-rounds, usage-limit and error both are kept."""

    def test_positive_a_run_that_cannot_resume_removes_its_folder_and_copies(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        self.assertEqual(self.h.removed, [("frozen", run.state["frozen_dir"]),
                                          ("copies", run.state["run_id"])])
        report = (run.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("- removed the frozen folder", report)
        self.assertIn("- removed the clean copies", report)

    def test_negative_a_resumable_end_keeps_both(self):
        for name, setup in (
                ("max-rounds", lambda: setattr(self.h, "reviews", [[finding()]])),
                ("error", lambda: setattr(self.h, "check_error",
                                          container.ContainerError("engine gone"))),
                # Codex over the ceiling stops the Codex review (A3 chunk 3: each step
                # is stopped only by its own provider's reading).
                ("usage-limit", lambda: setattr(self.h, "readings",
                                                [reading(), reading(),
                                                 reading(percent=95)]))):
            with self.subTest(reason=name):
                self.reset_work()
                self.h = Harness(self.root)
                setup()
                self.h.builder_script = [edit(self.root, "x\n")]
                run = self.start(cap=1, item=name.replace("-", "_"))
                self.assertEqual(self.reason(run), name)
                self.assertEqual(self.h.removed, [])

    def test_rejection_a_docker_failure_after_a_turn_ends_the_run_error(self):
        self.h.check_error = container.ContainerError("engine gone")
        self.h.builder_script = [edit(self.root, "x\n")]
        run = self.start()
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertIn("engine gone", json.dumps(run.state["stop_reasons"][-1]))

    def test_positive_a_run_that_started_docker_stops_it_after_the_report(self):
        self.h.docker_up = False
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.h.docker_calls.count("start"), 1)
        self.assertEqual(self.h.docker_calls[-1], "stop")
        self.assertEqual(self.h.reports_at_stop, [True], "the report came first (R30-3)")
        self.assertFalse(run.state["docker"]["started_by_run"])
        report = (run.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("Docker, started by this run, was stopped", report)

    def test_rejection_a_failing_stop_is_reported_and_the_stop_reason_stands(self):
        self.h.docker_up = False
        self.h.docker_stop_error = container.ContainerError("stop refused")
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        report = (run.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("could not be stopped: stop refused", report)
        self.assertIn("`reviewed-clean`", report)

    def test_rejection_a_failing_end_write_still_stops_docker_and_removes(self):
        # Code review R30-2: the report cannot be written at the end; Docker this run
        # started is still stopped and a run that cannot resume still removes its
        # folders, and the write's error is still raised.
        self.h.docker_up = False
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        with mock.patch.object(loop, "write_report",
                               side_effect=PermissionError(13, "Access is denied")), \
                redirect_stderr(io.StringIO()), self.assertRaises(Exception):
            self.start()
        self.assertEqual(self.h.docker_calls.count("stop"), 1)
        self.assertFalse(self.h.docker_up)
        self.assertEqual([what for what, _ in self.h.removed], ["frozen", "copies"])

    def test_negative_docker_already_running_is_never_stopped(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        self.start()
        self.assertNotIn("stop", self.h.docker_calls)

    def test_positive_the_checks_are_given_the_runs_folder_head_image_and_round(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        call, = self.h.check_calls
        self.assertEqual(call["run_id"], run.state["run_id"])
        self.assertEqual(call["frozen_dir"], run.state["frozen_dir"])
        self.assertEqual(call["public_head"], run.state["start"]["public_head"])
        self.assertEqual(call["image"], self.h.image_tag)
        self.assertEqual(call["round_dir"], run.run_dir / "rounds" / "R1")


class TrustedSessionsLoopTests(LoopCase):
    """Orchestrator isolation stage S7, the loop's part: a usage reading never loads the
    project's configuration (plan 7.6 item 4); a reviewer is given the run's frozen
    folder for its tripwire, and a Codex reviewer's launch readings are kept (live check
    4b); the run's rule-hook fire log is copied into the run record at every end and its
    folder removed with the frozen folder (7.6 item 3)."""

    def test_positive_each_usage_reading_is_given_the_frozen_folder_once_it_exists(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        first, *later = self.h.reading_folders
        self.assertIsNone(first, "the preflight's reading: no frozen folder yet")
        self.assertTrue(later)
        self.assertEqual(set(later), {run.state["frozen_dir"]})
        self.assertEqual(self.h.reviewer_frozen, [run.state["frozen_dir"]])

    def test_positive_a_resumes_reading_is_given_the_frozen_folder(self):
        self.h.builder_script = [edit(self.root, "x\n"), reply(actions(("R1-1", "fixed")))]
        self.h.reviews = [[finding()], []]
        run = self.start(cap=1)
        self.assertEqual(self.reason(run), sr.MAX_ROUNDS)
        before = len(self.h.reading_folders)
        self.resume(run, rounds=1)
        self.assertEqual(set(self.h.reading_folders[before:]), {run.state["frozen_dir"]})

    def test_positive_a_codex_reviewers_launch_readings_are_kept(self):
        self.h.review_launch = {"shell_pythonpath": "C:/t/tripwire",
                                "self_test": {"exitCode": 0}}
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(run.state["live"]["codex_reviewer_launches"],
                         [{"step": "review-R1", "shell_pythonpath": "C:/t/tripwire",
                           "self_test": {"exitCode": 0}}])

    def write_fire_log(self):
        """During the review, the trusted rule hooks' fire log as they write it:
        UTF-8 JSON with non-ASCII kept, and a line that is not JSON."""
        def write():
            run_id = self.h.frozen_calls[-1]["run_id"]
            log = codex_rules.fire_log_path(run_id)
            log.parent.mkdir(parents=True, exist_ok=True)
            self.addCleanup(shutil.rmtree, log.parent.parent / run_id, True)
            log.write_text('{"event": "block", "file": "café.md"}\nnot json\n',
                           encoding="utf-8", newline="\n")
        self.h.during_review = write

    def test_positive_the_fire_log_is_copied_escaped_and_its_folder_removed(self):
        self.write_fire_log()
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        raw = (run.run_dir / "fire-log.jsonl").read_bytes()
        self.assertTrue(raw.isascii(), raw)
        lines = [json.loads(line) for line in raw.decode("ascii").splitlines()]
        self.assertEqual(lines, [{"event": "block", "file": "café.md"}, "not json"])
        self.assertFalse(codex_rules.fire_log_path(run.state["run_id"]).parent.exists())
        self.assertIn({"what": "the rule-hook fire log", "removed": True},
                      run.state["cleanup"])

    def test_negative_a_resumable_end_copies_the_fire_log_and_keeps_its_folder(self):
        self.write_fire_log()
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[finding()]]
        run = self.start(cap=1)
        self.assertEqual(self.reason(run), sr.MAX_ROUNDS)
        self.assertTrue((run.run_dir / "fire-log.jsonl").is_file())
        self.assertTrue(codex_rules.fire_log_path(run.state["run_id"]).is_file())

    def test_negative_no_fire_log_writes_nothing(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertFalse((run.run_dir / "fire-log.jsonl").exists())

    def live_reading(self, servers, keep_counting=(), folder="C:/frozen/run"):
        """_read_codex_live with the SDK stood in for: each client records its
        settings, lists ``servers`` as Codex would under its overrides (a server
        switched off by name has no tools unless ``keep_counting`` names it, and
        `codex_apps` is gone with apps off), and the usage request is recorded."""
        seen, asked = [], []

        class FakeRpc:
            def __init__(self, overrides):
                self.overrides = overrides

            def _request_raw(self, method, params=None):
                assert method == "mcpServerStatus/list", method
                out = []
                for name, tools in servers:
                    if name == "codex_apps" and "features.apps=false" in self.overrides:
                        continue
                    off = (f"mcp_servers.{name}.enabled=false" in self.overrides
                           and name not in keep_counting)
                    out.append({"name": name, "tools": {} if off else dict.fromkeys(tools, {}),
                                "toolsError": None})
                return {"data": out}

        class FakeCodex:
            def __init__(self, config):
                seen.append(config)
                self._client = FakeRpc(tuple(config.config_overrides))

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                pass
        import openai_codex

        def read(client):
            asked.append(client.overrides)
            return "reading"
        with mock.patch.object(openai_codex, "Codex", FakeCodex), \
                mock.patch.object(limits, "read_codex", read):
            result = loop._read_codex_live(self.root, folder)
        return result, seen, asked

    def test_positive_the_live_reading_never_uses_the_project(self):
        # Plan 7.6 item 4 and R38-1: the frozen folder as both clients' cwd when given,
        # else one empty temporary folder removed afterwards; plugins and apps off on
        # both; every counting server switched off by name on the second, the one the
        # usage is read on; biblio-tools, with no tools, never named.
        servers = [("biblio-tools", ()), ("codex_apps", ("a",)), ("node_repl", ("js",))]
        result, seen, asked = self.live_reading(servers)
        self.assertEqual(result, "reading")
        first, second = seen
        self.assertEqual({first.cwd, second.cwd}, {"C:/frozen/run"})
        self.assertEqual(tuple(first.config_overrides),
                         ("features.plugins=false", "features.apps=false"))
        self.assertEqual(tuple(second.config_overrides),
                         ("features.plugins=false", "features.apps=false",
                          "mcp_servers.node_repl.enabled=false"))
        self.assertEqual(asked, [tuple(second.config_overrides)])
        _, seen, _ = self.live_reading(servers, folder=None)
        empty = {config.cwd for config in seen}
        self.assertEqual(len(empty), 1)
        empty = Path(empty.pop())
        self.assertNotEqual(empty, self.root)
        self.assertFalse(empty.exists(), "the empty folder is removed")
        self.assertIn("book-dragon-usage-", empty.name)

    def test_rejection_a_server_still_counting_fails_the_reading_before_usage(self):
        servers = [("node_repl", ("js",))]
        with self.assertRaisesRegex(providers.ProviderError,
                                    "usage reading's Codex client has an MCP server in "
                                    "force: 'node_repl'"):
            self.live_reading(servers, keep_counting=("node_repl",))
        with self.assertRaisesRegex(providers.ProviderError, "cannot be written"):
            self.live_reading([("bad name!", ("t",))])


class ResumeDockerTests(LoopCase):
    """Decision 31 and R24-2: a resume starts Docker as a start does and records it,
    so the run's next end stops it again; a dry resume only reports."""

    def ended_at_max_rounds(self):
        self.h.docker_up = False
        self.h.builder_script = [edit(self.root, "x\n"), reply(actions(("R1-1", "fixed")))]
        self.h.reviews = [[finding()], []]
        run = self.start(cap=1)
        self.assertEqual(self.reason(run), sr.MAX_ROUNDS)
        self.assertEqual(self.h.docker_calls.count("stop"), 1)
        self.assertFalse(self.h.docker_up)
        return run

    def test_positive_a_resume_starts_docker_and_its_end_stops_it_again(self):
        run = self.ended_at_max_rounds()
        again = self.resume(run, rounds=1)
        self.assertEqual(self.reason(again), sr.REVIEWED_CLEAN)
        self.assertEqual(self.h.docker_calls.count("start"), 2)
        self.assertEqual(self.h.docker_calls.count("stop"), 2)
        self.assertEqual(self.h.docker_calls[-1], "stop")
        whats = [e["what"] for e in again.state["docker"]["events"]]
        self.assertIn("Docker was started by this resume", whats)

    def test_negative_a_resume_with_docker_running_starts_and_stops_nothing(self):
        run = self.ended_at_max_rounds()
        self.h.docker_up = True  # started by someone else since
        again = self.resume(run, rounds=1)
        self.assertEqual(self.reason(again), sr.REVIEWED_CLEAN)
        self.assertEqual(self.h.docker_calls.count("start"), 1)
        self.assertEqual(self.h.docker_calls.count("stop"), 1)

    def test_rejection_a_dry_resume_with_docker_stopped_starts_nothing(self):
        run = self.ended_at_max_rounds()
        before = list(self.h.docker_calls)
        state = (run.run_dir / "state.json").read_bytes()
        again = loop.load(run.state["run_id"], root=self.root, deps=self.h.deps(),
                          log_path=self.log, runs_dir=self.runs)
        err = io.StringIO()
        with redirect_stderr(err):
            loop.resume(again, dry_run=True)
        self.assertIn("Docker is not running; a real resume would start it", err.getvalue())
        self.assertEqual(self.h.docker_calls[len(before):], ["status"])
        self.assertFalse(self.h.docker_up)
        self.assertEqual((run.run_dir / "state.json").read_bytes(), state)

    def test_rejection_a_resume_whose_state_cannot_be_saved_stops_its_docker(self):
        # Code review R29-2: the resume started Docker, then its state save failed.
        # It stops the Docker it started and refuses; the saved run is unchanged and so
        # still resumable.
        run = self.ended_at_max_rounds()
        state = (run.run_dir / "state.json").read_bytes()
        again = loop.load(run.state["run_id"], root=self.root, deps=self.h.deps(),
                          log_path=self.log, runs_dir=self.runs)
        original = runrecord.write_state

        def refuse(run_dir, saved):
            raise PermissionError(13, "Access is denied", str(run_dir))
        runrecord.write_state = refuse
        try:
            with self.assertRaises(loop.RunRefused) as caught:
                loop.resume(again, rounds=1)
        finally:
            runrecord.write_state = original
        self.assertIn("the resume could not be recorded: PermissionError",
                      " ".join(caught.exception.args[0]))
        self.assertEqual(self.h.docker_calls.count("start"), 2)
        self.assertEqual(self.h.docker_calls.count("stop"), 2)
        self.assertFalse(self.h.docker_up)
        self.assertEqual((run.run_dir / "state.json").read_bytes(), state)

    def test_rejection_a_resume_whose_log_line_fails_saves_nothing_and_stops_docker(self):
        # The R30 exception-path audit: the started line is written before the state,
        # so a log that cannot be written leaves the run saved as it was (still
        # resumable) rather than saved as running, and the Docker it started stops.
        run = self.ended_at_max_rounds()
        state = (run.run_dir / "state.json").read_bytes()
        again = loop.load(run.state["run_id"], root=self.root, deps=self.h.deps(),
                          log_path=self.log, runs_dir=self.runs)
        with mock.patch.object(loop, "_log", side_effect=PermissionError(13, "denied")), \
                self.assertRaises(loop.RunRefused) as caught:
            loop.resume(again, rounds=1)
        self.assertIn("the resume could not be recorded: PermissionError",
                      " ".join(caught.exception.args[0]))
        self.assertEqual((run.run_dir / "state.json").read_bytes(), state)
        self.assertEqual(self.h.docker_calls.count("stop"), 2)
        self.assertFalse(self.h.docker_up)

    def test_negative_a_resume_save_failure_with_docker_already_running_stops_nothing(self):
        run = self.ended_at_max_rounds()
        self.h.docker_up = True
        again = loop.load(run.state["run_id"], root=self.root, deps=self.h.deps(),
                          log_path=self.log, runs_dir=self.runs)
        original = runrecord.write_state
        runrecord.write_state = lambda run_dir, saved: (_ for _ in ()).throw(
            PermissionError(13, "Access is denied"))
        try:
            with self.assertRaises(loop.RunRefused):
                loop.resume(again, rounds=1)
        finally:
            runrecord.write_state = original
        self.assertEqual(self.h.docker_calls.count("stop"), 1)
        self.assertTrue(self.h.docker_up)

    def test_rejection_a_dry_resume_whose_docker_status_fails_is_refused(self):
        # Code review R27-1, for the dry resume, which asks the same question.
        run = self.ended_at_max_rounds()
        state = (run.run_dir / "state.json").read_bytes()
        self.h.docker_status_error = container.ContainerError("docker could not be run")
        again = loop.load(run.state["run_id"], root=self.root, deps=self.h.deps(),
                          log_path=self.log, runs_dir=self.runs)
        with redirect_stderr(io.StringIO()), self.assertRaises(loop.RunRefused) as caught:
            loop.resume(again, dry_run=True)
        self.assertIn("Docker: docker could not be run", " ".join(caught.exception.args[0]))
        self.assertEqual((run.run_dir / "state.json").read_bytes(), state)

    def test_rejection_a_resume_whose_docker_cannot_start_leaves_the_run_unchanged(self):
        run = self.ended_at_max_rounds()
        state = (run.run_dir / "state.json").read_bytes()
        self.h.docker_start_error = container.ContainerError("no engine")
        again = loop.load(run.state["run_id"], root=self.root, deps=self.h.deps(),
                          log_path=self.log, runs_dir=self.runs)
        with self.assertRaises(loop.RunRefused) as caught:
            loop.resume(again, rounds=1)
        self.assertIn("Docker: no engine", " ".join(caught.exception.args[0]))
        self.assertEqual((run.run_dir / "state.json").read_bytes(), state)


if __name__ == "__main__":
    unittest.main()
