#!/usr/bin/env python3
"""Hermetic tests for the build-and-review loop, part of the split of the old single
loop file (plan 10.3, the chunk list): the reverse pairing and today's pairing (the
chunk (d) short plan, section 6), the check after every Codex builder turn (plan 10.4,
chunk (d)), and the start's refusal of a loose ``.env`` file (short plan 5.1 rule 3).

The shared fixtures (``Harness``, the fake sessions, ``LoopCase`` and ``SwappedCase``)
are in ``loop_fixtures.py``. No test makes a model call. Same three controls as the
other suites: a positive control must work, a rejection control must end or refuse the
run with the reason named, a negative control must not.

    python workflows/review-orchestration/tests/test_loop_pairing.py
"""

import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_fixtures import *  # noqa: E402,F401,F403 - the loop suites' shared fixtures


class ReversePairingTests(SwappedCase):
    """Short plan section 6: every row of the table of what the loop assumed, with the
    roles swapped. The same rows with the roles as they are today are the tests in
    ``test_loop.py`` and ``TodaysPairingTests`` below."""

    def test_positive_a_whole_run_records_by_provider_not_by_role(self):
        self.h.builder_script = [edit(self.root, "done\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        steps = run.state["steps"]
        self.assertEqual([(s["role"], s["provider"]) for s in steps],
                         [("builder", "codex"), ("reviewer", "claude")])
        # Tokens come from the session that knows its own usage shape.
        self.assertEqual([s["tokens"] for s in steps], [700, 450])
        self.assertIsNone(steps[0]["cost_usd_equivalent"])
        self.assertEqual(steps[1]["thread_id"], "claude-session")
        live = run.state["live"]
        self.assertEqual(live["codex_model"], "gpt-6-sol")
        self.assertEqual(live["claude_init_model"], MODEL)
        # The builder is given the run folder and the run's one Claude usage object;
        # so is every fresh Claude reviewer.
        self.assertEqual(self.h.given[0], ("builder", run.run_dir, self.h.usage))
        self.assertEqual(self.h.given[1], ("reviewer", None, self.h.usage))
        report = (run.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("## Check after every builder turn", report)
        self.assertIn("- build: ", report)
        self.assertIn("nothing found", report)

    def builder_steps(self, run):
        return [s for s in run.state["steps"] if s["role"] == "builder"]

    def test_positive_each_codex_builder_step_records_its_share(self):
        # The chunk (d) short plan, 11.3 and live check 9: the thread reports a running
        # total, and a second fix is measured from the first fix, not the build.
        self.h.codex_totals = [1000, 1600, 2500]
        self.h.builder_script = [edit(self.root, "x\n"), reply(actions(("R1-1", "fixed"))),
                                 reply(actions(("R2-1", "fixed")))]
        self.h.reviews = [[finding()], [finding(title="Another defect")], []]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        steps = self.builder_steps(run)
        self.assertEqual([s["step"] for s in steps], ["build", "fix-R1", "fix-R2"])
        self.assertEqual([s["tokens"] for s in steps], [1000, 600, 900])
        self.assertEqual([s["thread_total_tokens"] for s in steps], [1000, 1600, 2500])
        self.assertEqual({s["thread"] for s in steps}, {"s-1"})

    def test_positive_each_codex_builder_step_records_its_uncached_share(self):
        # Plan 10.10 item 6: a Codex builder step's figure without cache reads is the
        # increase in the thread's figure during the step, as for tokens; the thread's
        # figure is input less cached input, plus output, in the SDK's spelling.
        def usage(given, cached, made):
            return {"total": {"inputTokens": given, "cachedInputTokens": cached,
                              "outputTokens": made, "totalTokens": given + made}}
        self.h.codex_usages = [usage(1000, 800, 50), usage(3000, 2500, 100)]
        self.h.codex_totals = [1050, 3100]
        self.h.builder_script = [edit(self.root, "x\n"), reply(actions(("R1-1", "fixed")))]
        self.h.reviews = [[finding()], []]
        run = self.start()
        steps = self.builder_steps(run)
        self.assertEqual([s["thread_uncached_tokens"] for s in steps], [250, 600])
        self.assertEqual([s["uncached_tokens"] for s in steps], [250, 350])
        self.assertEqual([s["tokens"] for s in steps], [1050, 2050])

    def test_negative_a_codex_share_with_no_earlier_uncached_figure_is_none(self):
        # An earlier step on the thread with no running figure without cache reads (one
        # recorded before A3 chunk 1) gives the next step none, never a guessed share.
        self.h.codex_usages = [{"total": {"totalTokens": 1000}},
                               {"total": {"inputTokens": 1500, "outputTokens": 100,
                                          "totalTokens": 1600}}]
        self.h.codex_totals = [1000, 1600]
        self.h.builder_script = [edit(self.root, "x\n"), reply(actions(("R1-1", "fixed")))]
        self.h.reviews = [[finding()], []]
        run = self.start()
        steps = self.builder_steps(run)
        self.assertEqual([s["thread_uncached_tokens"] for s in steps], [None, 1600])
        self.assertEqual([s["uncached_tokens"] for s in steps], [None, None])

    def test_positive_the_forced_stop_and_resume_of_live_check_3(self):
        # R8-1's repair: --inject-limit fix-R1 stops the run before the fix turn, after
        # the builder session is open; the resume opens a new session on the same
        # thread, whose launch record is stored with its step, and the share still holds.
        self.h.codex_totals = [1000, 1600]
        self.h.launch_records = [{"self_test": "passed", "thread": {"method": "start"}},
                                 {"self_test": "passed", "thread": {"method": "resume"}}]
        self.h.builder_script = [edit(self.root, "x\n"), reply(actions(("R1-1", "fixed")))]
        self.h.reviews = [[finding()], []]
        run = self.start(inject="fix-R1")
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(run.state["step"], "review-R1")
        self.assertEqual([s["step"] for s in self.builder_steps(run)], ["build"])
        self.assertTrue(self.h.builders[0].closed)
        again = self.resume(run)
        self.assertEqual(self.reason(again), sr.REVIEWED_CLEAN)
        self.assertEqual(self.h.builders[1].resume, "s-1")
        launches = again.state["live"]["codex_builder_launches"]
        self.assertEqual([(entry["step"], entry["thread"]["method"]) for entry in launches],
                         [("build", "start"), ("fix-R1", "resume")])
        self.assertEqual([s["tokens"] for s in self.builder_steps(again)], [1000, 600])

    def test_negative_a_session_with_no_launch_record_adds_nothing(self):
        self.h.builder_script = [edit(self.root, "done\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertNotIn("codex_builder_launches", run.state["live"])

    def test_positive_the_title_pattern_is_not_sent_to_a_claude_reviewer(self):
        self.h.builder_script = [edit(self.root, "done\n")]
        self.h.reviews = [[]]
        run = self.start()
        schema = self.h.review_calls[0][1]
        title = schema["properties"]["findings"]["items"]["properties"]["title"]
        self.assertNotIn("pattern", title)
        self.assertIn("files_reviewed", schema["required"])
        self.assertEqual(run.state["live"]["title_pattern"], "not sent")

    def test_rejection_the_ceiling_on_a_reading_from_an_earlier_reviewer_session(self):
        # The run holds one Claude usage object, so what round 1's reviewer session
        # reported is still there before the next step, with that session gone.
        def reported():
            self.h.claude_usage = limits.Reading(
                limits.CLAUDE, limits.REPORTED, percent=85, resets_at=5,
                window="five_hour")
        self.h.during_review = reported
        self.h.builder_script = [edit(self.root, "x\n"), reply(actions(("R1-1", "fixed")))]
        self.h.reviews = [[finding()]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT, run.state["stop_reasons"][-1])
        # Since A3 chunk 3 Claude's reading never stops the Codex fix pass; it stops the
        # next Claude step, the second review, before it starts.
        self.assertEqual(run.state["step"], "fix-R1")
        self.assertEqual(len(self.h.builders[0].prompts), 2)
        stop = run.state["stop_reasons"][-1]["evidence"]
        self.assertEqual(stop["provider"], "claude")
        self.assertEqual(stop["step"], "review-R2")
        self.assertIn("ceiling", stop["detail"])

    def test_rejection_the_credit_rule_runs_before_a_codex_builder_step(self):
        changed = dict(CREDITS, balance="4.50")
        self.h.readings = [reading(), reading(credits=changed)]
        self.h.builder_script = [edit(self.root, "x\n")]
        run = self.start()
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(run.state["step"], loop.APPROVED_STEP)
        self.assertEqual(self.h.builders, [])
        self.assertIn("balance '5.00' -> '4.50'", self.evidence(run))

    def test_negative_the_credit_rule_does_not_run_before_a_claude_review_step(self):
        changed = dict(CREDITS, balance="4.50")
        self.h.readings = [reading(), reading(), reading(credits=changed)]
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        self.assertEqual(self.reason(self.start()), sr.REVIEWED_CLEAN)

    def test_positive_refusals_from_each_kind_of_session_reach_the_report(self):
        self.h.builder_refusals = [{"tool": "Bash", "detail": "curl example.com",
                                    "reason": "Refused by the orchestrator: no."}]
        self.h.review_refusals = [{"tool": "Write", "detail": "x.txt",
                                   "reason": "Refused by the orchestrator: the reviewer "
                                             "is read-only."}]
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual([(r["step"], r["tool"]) for r in run.state["refusals"]],
                         [("build", "Bash"), ("review-R1", "Write")])
        report = (run.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("## Refused calls", report)
        self.assertIn("- build: Bash curl example.com", report)
        self.assertIn("- review-R1: Write x.txt", report)

    def test_positive_refusals_are_kept_when_the_step_fails(self):
        self.h.builder_refusals = [{"tool": "webrun", "detail": "", "reason": "r"}]

        def fails(prompt):
            raise RuntimeError("the turn was ended")
        self.h.builder_script = [fails]
        run = self.start()
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertEqual([(r["step"], r["tool"]) for r in run.state["refusals"]],
                         [("build", "webrun")])

    def test_positive_a_failed_review_keeps_what_the_reviewer_was_refused(self):
        self.h.review_refusals = [{"tool": "Bash", "detail": "ls", "reason": "r"}]
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [providers.ProviderError("no structured reply")]
        run = self.start()
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertEqual([(r["step"], r["tool"]) for r in run.state["refusals"]],
                         [("review-R1", "Bash")])

    def test_positive_both_prompts_take_the_form_for_their_provider(self):
        self.h.builder_script = [edit(self.root, "x\n"),
                                 reply(actions(("R1-1", "fixed")))]
        self.h.reviews = [[finding()], []]
        self.assertEqual(self.reason(self.start()), sr.REVIEWED_CLEAN)
        build, fix = (prompt for _, prompt in self.h.builders[0].prompts)
        for text in (build, fix):
            self.assertIn("apply_patch", text)
            self.assertIn('Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz"', text)
            self.assertIn("Get-Content -LiteralPath PATH", text)
            # Nothing a Codex builder does not have.
            for absent in ("Biblio Tools", "append_log"):
                self.assertNotIn(absent, text)
            # Its own verification list, run only through the check helper (S7): the
            # audit and the close-out verifier in their read-only forms only.
            self.assertIn("ai-style-guard|backlog-guard", text)
            self.assertIn("check helper's `run_check` tool, never in the shell", text)
            self.assertIn("workflows/close-out/scripts/run\\.py --read-only", text)
            self.assertIn("workflows/audit/scripts/run\\.py --context( (?!-)[\\w./\\-]+)+ "
                          "--read-only", text)
            self.assertNotIn("$", text.replace("`$`", ""))
        review = self.h.review_calls[0][0]
        self.assertIn("you cannot run anything", review)
        self.assertIn("A file wildcard in a search is refused", review)
        self.assertNotIn("You may run read-only commands", review)

    def test_positive_the_lists_printed_are_the_frozen_folders(self):
        # The frozen copies judge the builder, so they are what its prompt shows.
        (self.h.frozen / "codex-verify-commands.txt").write_bytes(b"python frozen-verify\n")
        (self.h.frozen / "codex-read-commands.txt").write_bytes(b"frozen-read PATH\n")
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        self.start()
        build = self.h.builders[0].prompts[0][1]
        self.assertIn("python frozen-verify", build)
        self.assertIn("frozen-read PATH", build)
        # The project's own read list is not printed. The prompt's literal Get-Content
        # example (fix 5) names a real path; the list's line names the PATH token.
        self.assertNotIn("Get-Content -LiteralPath PATH", build)
        self.assertNotIn("rg( -n", build)

    def test_positive_an_injected_limit_at_a_codex_builder_step(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start(inject="build")
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(run.state["stop_reasons"][-1]["evidence"]["provider"], "codex")
        self.assertEqual(self.reason(self.resume(run)), sr.REVIEWED_CLEAN)

    def test_positive_an_injected_limit_at_a_claude_review_step(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start(inject="review-R1")
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(run.state["stop_reasons"][-1]["evidence"]["provider"], "claude")
        self.assertEqual(self.h.review_calls, [])
        self.assertEqual(self.reason(self.resume(run)), sr.REVIEWED_CLEAN)


class TodaysPairingTests(LoopCase):
    """The same rows of the table with Claude building and Codex reviewing."""

    def test_positive_tokens_models_and_sessions_by_provider(self):
        self.h.builder_script = [edit(self.root, "done\n")]
        self.h.reviews = [[]]
        run = self.start()
        steps = run.state["steps"]
        self.assertEqual([(s["role"], s["provider"]) for s in steps],
                         [("builder", "claude"), ("reviewer", "codex")])
        self.assertEqual([s["tokens"] for s in steps], [110, 1000])
        self.assertEqual(steps[0]["cost_usd_equivalent"], 0.01)
        self.assertEqual(steps[1]["thread_id"], "t")
        self.assertEqual(run.state["live"]["claude_init_model"], MODEL)
        self.assertNotIn("codex_model", run.state["live"])
        self.assertEqual(run.state["live"]["title_pattern"], "accepted")
        self.assertEqual(self.h.given[0], ("builder", run.run_dir, self.h.usage))
        self.assertEqual(self.h.given[1], ("reviewer", None, self.h.usage))

    def test_negative_the_check_after_every_turn_is_not_run_for_a_claude_builder(self):
        def stray(prompt):
            (self.root / "other.txt").write_bytes(b"outside the edit paths\n")
            return edit(self.root, "x\n")(prompt)
        self.h.builder_script = [stray]
        self.h.reviews = [[]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        self.assertEqual(self.h.inventories, 0)
        self.assertNotIn("turn_checks", run.state)
        report = (run.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertNotIn("Check after every builder turn", report)

    def test_positive_both_prompts_take_the_form_for_their_provider(self):
        self.h.builder_script = [edit(self.root, "x\n"),
                                 reply(actions(("R1-1", "fixed")))]
        self.h.reviews = [[finding()], []]
        self.assertEqual(self.reason(self.start()), sr.REVIEWED_CLEAN)
        build, fix = (prompt for _, prompt in self.h.builders[0].prompts)
        # Since S7 (plan 7.4): checks through the helper, its clock for LOG.md, and no
        # shell or Biblio Tools.
        self.assertIn("Run checks only with the check helper's `run_check` tool", build)
        self.assertIn("check\n    helper's `get_timestamp` tool", build)
        self.assertIn("The shell\n    is not available", build)
        self.assertIn("The Biblio Tools tools are not\n    available", build)
        self.assertIn("workflows/audit/scripts/run", build)
        for text in (build, fix):
            # Nothing a Claude builder does not have.
            for absent in ("apply_patch", "Get-Date", "Get-Content"):
                self.assertNotIn(absent, text)
        review = self.h.review_calls[0][0]
        # Decision 34: the Codex reviewer runs no project code.
        self.assertIn("You may read the code, but run no project code", review)
        self.assertNotIn("such as the tests", review)
        self.assertNotIn("cannot run anything", review)

    # Decision 39: the Claude builder's callback record, one entry per step.
    BUILD_RECORD = {"judged": [{"tool": "Write", "id": "b1", "decision": "allow"}],
                    "tool_uses": [{"tool": "Write", "id": "b1"}], "not_judged": [],
                    "not_in_transcript": [], "duplicate_ids": []}
    FIX_RECORD = {"judged": [{"tool": "Edit", "id": "f1", "decision": "allow"}],
                  "tool_uses": [{"tool": "Edit", "id": "f1"}], "not_judged": [],
                  "not_in_transcript": [], "duplicate_ids": []}

    def test_positive_each_claude_turn_stores_its_callback_record_with_its_step(self):
        self.h.callback_records = [dict(self.BUILD_RECORD), dict(self.FIX_RECORD)]
        self.h.builder_script = [edit(self.root, "x\n"), reply(actions(("R1-1", "fixed")))]
        self.h.reviews = [[finding()], []]
        run = self.start()
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        self.assertEqual(run.state["live"]["claude_builder_callbacks"],
                         [{"step": "build", **self.BUILD_RECORD},
                          {"step": "fix-R1", **self.FIX_RECORD}])
        report = (run.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("- claude_builder_callbacks: ", report)

    def test_positive_a_failed_claude_turn_still_stores_its_record(self):
        def fails(prompt):
            raise providers.ProviderError("the builder turn ended in error")
        self.h.callback_records = [dict(self.BUILD_RECORD)]
        self.h.builder_script = [fails]
        run = self.start()
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertEqual(run.state["live"]["claude_builder_callbacks"],
                         [{"step": "build", **self.BUILD_RECORD}])

    def test_negative_a_builder_with_no_callback_record_adds_nothing(self):
        self.h.builder_script = [edit(self.root, "done\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertNotIn("claude_builder_callbacks", run.state["live"])


class TurnCheckTests(SwappedCase):
    """The check after every Codex builder turn (plan 10.4, chunk (d); short plan 4)."""

    def planted(self, action):
        """A run whose builder does ``action`` during its turn, as well as its work."""
        def turn(prompt):
            action()
            return edit(self.root, "x\n")(prompt)
        self.h.builder_script = [turn]
        self.h.reviews = [[]]
        return self.start()

    def refused(self, action, *named):
        run = self.planted(action)
        self.assertEqual(self.reason(run), sr.ERROR)
        text = self.evidence(run)
        self.assertIn("check after the builder's turn", text)
        for name in named:
            self.assertIn(name, text)
        self.assertEqual(self.h.review_calls, [], "no review of a turn that failed it")
        # The failed step's partial record is saved before the run ends.
        self.assertTrue((run.run_dir / "partial" / "build.patch").is_file())
        self.assertEqual(run.state["interrupted"], "build")
        self.assertTrue(run.state["turn_checks"][-1]["changes"])
        return run

    def personal_git(self, *args):
        git(self.root, "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args,
            git_dir=str(self.personal))

    def test_positive_a_turn_inside_the_edit_paths_passes(self):
        run = self.planted(lambda: None)
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        check, = run.state["turn_checks"]
        self.assertEqual((check["step"], check["changes"]), ("build", []))
        self.assertIsInstance(check["seconds"], float)
        self.assertEqual(self.h.inventories, 2, "one before the turn and one after")

    def test_rejection_a_write_to_a_tracked_file_outside_the_edit_paths(self):
        self.refused(lambda: (self.root / "brief.md").write_bytes(b"changed\n"),
                     "brief.md was changed outside the edit paths")

    def test_rejection_a_new_file_outside_the_edit_paths(self):
        self.refused(lambda: (self.root / "other.txt").write_bytes(b"new\n"),
                     "other.txt was added outside the edit paths")

    def test_rejection_a_new_category_a_file(self):
        self.refused(lambda: (self.root / "memory" / "new.md").write_bytes(b"new\n"),
                     "memory/new.md was added")

    def test_rejection_a_change_to_an_existing_category_a_file(self):
        self.refused(lambda: (self.root / "memory" / "note.md").write_bytes(b"other\n"),
                     "memory/note.md was changed")

    def test_rejection_a_deleted_tracked_file(self):
        self.refused((self.root / "brief.md").unlink, "brief.md was removed")

    def test_rejection_a_deleted_category_a_file(self):
        self.refused((self.root / "memory" / "note.md").unlink,
                     "memory/note.md was removed")

    def test_rejection_a_git_add_in_the_public_repository(self):
        def add():
            (self.root / "work" / "a.txt").write_bytes(b"staged\n")
            git(self.root, "add", "work/a.txt")
        self.refused(add, "the staged contents changed in the public repository")

    def test_rejection_a_new_branch_in_the_public_repository(self):
        self.refused(lambda: git(self.root, "branch", "sneaky"),
                     "a ref changed in the public repository")

    def test_rejection_a_git_add_in_the_personal_repository(self):
        def add():
            (self.root / "memory" / "note.md").write_bytes(b"staged\n")
            self.personal_git("add", "-f", "memory/note.md")
        self.refused(add, "the staged contents changed in the personal repository")

    def test_rejection_a_new_branch_in_the_personal_repository(self):
        self.refused(lambda: self.personal_git("branch", "sneaky"),
                     "a ref changed in the personal repository")

    def test_negative_a_change_inside_the_run_record_is_the_runs_own(self):
        # The session copies its hook's decisions into the run record during a turn.
        def record():
            folder, = self.runs.iterdir()
            (folder / "hook-decisions.jsonl").write_bytes(b"{}\n")
            self.h.cat_a.add(f"runs/{folder.name}/hook-decisions.jsonl")
        run = self.planted(record)
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        self.assertEqual(run.state["turn_checks"][0]["changes"], [])

    def test_rejection_any_other_change_inside_the_run_record(self):
        # Code review finding R3-1: only the decisions copy is the orchestrator's own;
        # a listed test that rewrote the run's saved roles must end the run.
        for name in ("settings.json", "state.json", "brief.md", "new.txt"):
            with self.subTest(name):
                self.setUp()

                def record(name=name):
                    folder, = self.runs.iterdir()
                    (folder / name).write_bytes(b"{}\n")
                    self.h.cat_a.add(f"runs/{folder.name}/{name}")
                self.refused(record, f"/{name} was ")

    def test_positive_the_exempt_record_file_is_the_one_the_session_copies(self):
        self.assertEqual(worktree.TURN_RECORD_WRITES, (codex_rules.DECISIONS_FILE,))

    def test_rejection_a_run_record_change_under_an_edit_path_that_covers_it(self):
        # Code review finding R5-1: an edit path over this workflow covers runs/ too,
        # and run records are judged before edit paths.
        root = self.root
        base = "workflows/review-orchestration"
        run_dir = root / base / "runs" / "20261004-000000-abcd"

        def inventory(files):
            return {"files": files, "git": {"public": {}, "personal": {}}}
        record = f"{base}/runs/20261004-000000-abcd"
        before = inventory({f"{record}/settings.json": "a", f"{record}/hook-decisions.jsonl": "a",
                            f"{base}/runs/20261003-000000-dcba/state.json": "a",
                            f"{base}/scripts/x.py": "a"})
        after = inventory({f"{record}/settings.json": "b", f"{record}/hook-decisions.jsonl": "b",
                           f"{base}/runs/20261003-000000-dcba/state.json": "b",
                           f"{base}/scripts/x.py": "b", f"{record}/new.txt": "b"})
        for edit_paths in ([f"{base}/"], [f"{base}/runs/"], ["workflows/"]):
            with self.subTest(edit_paths=edit_paths):
                problems = worktree.compare_inventory(before, after, edit_paths, run_dir,
                                                      root)
                expected = [f"{record}/new.txt was added in a run record",
                            f"{record}/settings.json was changed in a run record",
                            f"{base}/runs/20261003-000000-dcba/state.json was changed "
                            "in a run record"]
                if edit_paths == [f"{base}/runs/"]:
                    expected.append(f"{base}/scripts/x.py was changed outside the edit "
                                    "paths")
                self.assertEqual(sorted(problems), sorted(expected))

    def test_rejection_a_changed_added_or_removed_file_in_the_frozen_folder(self):
        for label in ("changed", "added", "removed"):
            with self.subTest(label):
                self.setUp()  # a fresh project and frozen folder for each case
                hook = self.h.frozen / "codex_hook.py"
                run = self.planted({"changed": lambda: hook.write_bytes(b"other\n"),
                                    "added": lambda: (self.h.frozen / "extra.py")
                                    .write_bytes(b"x\n"),
                                    "removed": hook.unlink}[label])
                self.assertEqual(self.reason(run), sr.ERROR)
                self.assertIn("the frozen hook folder changed during the turn",
                              self.evidence(run))
                self.assertIn({"changed": "codex_hook.py", "added": "extra.py",
                               "removed": "codex_hook.py"}[label], self.evidence(run))

    def test_negative_the_decisions_file_is_meant_to_change(self):
        decisions = self.h.frozen / "hook-decisions.jsonl"
        run = self.planted(lambda: decisions.write_bytes(b'{"decision": "allow"}\n'))
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)

    def test_negative_the_check_calls_file_is_meant_to_change(self):
        # The check helper writes it during a turn (isolation plan 6, item 5).
        calls = self.h.frozen / "check-calls.jsonl"
        run = self.planted(lambda: calls.write_bytes(b'{"allowed": true}\n'))
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)

    def test_rejection_another_frozen_file_changing_beside_the_calls_file(self):
        # Control for the test above: the calls file's exemption is that file only.
        def both():
            (self.h.frozen / "check-calls.jsonl").write_bytes(b"{}\n")
            (self.h.frozen / "start-ignored.txt").write_bytes(b".env\n")
        run = self.planted(both)
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertIn("start-ignored.txt", self.evidence(run))
        self.assertNotIn("check-calls.jsonl", self.evidence(run))

    def test_rejection_a_bytecode_cache_in_the_frozen_folder_is_a_change(self):
        # Code review R20-1: nothing run from the folder writes one any more, so a
        # cache appearing there during a turn is a change like any other.
        def cache():
            (self.h.frozen / "__pycache__").mkdir()
            (self.h.frozen / "__pycache__" / "codex_rules.cpython-313.pyc").write_bytes(b"x")
        run = self.planted(cache)
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertIn("__pycache__/codex_rules.cpython-313.pyc", self.evidence(run))

    def test_rejection_the_check_runs_when_the_turn_raised(self):
        def fails(prompt):
            (self.root / "other.txt").write_bytes(b"left behind\n")
            raise RuntimeError("the turn was ended")
        self.h.builder_script = [fails]
        run = self.start()
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertIn("other.txt was added outside the edit paths", self.evidence(run))
        self.assertEqual(self.h.inventories, 2)

    def test_negative_a_turn_that_raised_and_changed_nothing_keeps_its_own_error(self):
        def fails(prompt):
            raise RuntimeError("the turn was ended")
        self.h.builder_script = [fails]
        run = self.start()
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertIn("RuntimeError: the turn was ended", self.evidence(run))
        self.assertNotIn("check after the builder's turn", self.evidence(run))
        self.assertEqual(run.state["turn_checks"][0]["changes"], [])

    def test_positive_the_check_runs_after_a_fix_pass_too(self):
        def fix(prompt):
            (self.root / "other.txt").write_bytes(b"new\n")
            return actions(("R1-1", "fixed"))
        self.h.builder_script = [edit(self.root, "x\n"), fix]
        self.h.reviews = [[finding()]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertEqual([c["step"] for c in run.state["turn_checks"]],
                         ["build", "fix-R1"])
        self.assertIn("other.txt was added", self.evidence(run))


class EnvPreflightTests(LoopCase):
    """Short plan 5.1 rule 3: no run of either pairing starts while a .env-named file
    is neither tracked nor ignored."""

    def loose(self):
        (self.root / ".env").write_bytes(b"SECRET=1\n")
        (self.root / "sub").mkdir()
        (self.root / "sub" / ".env.local").write_bytes(b"SECRET=2\n")

    def test_rejection_an_untracked_unignored_env_file_each_named(self):
        self.loose()
        for swapped in (False, True):
            with self.subTest(swapped=swapped):
                self.h.swapped = swapped
                with self.assertRaises(loop.RunRefused) as caught:
                    self.start()
                text = " ".join(caught.exception.args[0])
                self.assertIn(".env is a `.env`-named file that git neither tracks",
                              text)
                self.assertIn("sub/.env.local is a `.env`-named file", text)
                self.assertIn("add it to .gitignore", text)
                self.assertFalse(self.runs.exists())

    def test_positive_the_run_starts_once_they_are_ignored(self):
        self.loose()
        with open(self.root / ".git" / "info" / "exclude", "ab") as handle:
            handle.write(b".env\n.env.*\n")
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        self.assertEqual(self.reason(self.start()), sr.REVIEWED_CLEAN)

    def test_negative_a_tracked_env_named_file_does_not_refuse_the_run(self):
        # A tracked file is already published, so it is not a secret; the read rules
        # still stop a read that names it.
        (self.root / ".env.example").write_bytes(b"KEY=placeholder\n")
        git(self.root, "add", ".env.example")
        git(self.root, "commit", "-qm", "example")
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        self.assertEqual(self.reason(self.start()), sr.REVIEWED_CLEAN)


if __name__ == "__main__":
    unittest.main()
