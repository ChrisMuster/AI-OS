#!/usr/bin/env python3
"""Hermetic tests for A4's final intent check, step ``intent-final`` (plan 10.6 and the
10.5 table): a run with no reviewer or mechanical finding open goes on to it, and it
ends the run ``clean`` when both layers pass and ``intent-failed``, naming each cause,
when either fails. ``reviewed-clean`` is retired for runs started from this code.

Whole runs are driven with the fixtures' fakes in a temporary repository. No test
starts Docker, a model session, a Claude probe or a Codex app server, and none needs
``--allow-paid`` to pass.

    python workflows/review-orchestration/tests/test_intent_final.py
"""

import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_fixtures import *  # noqa: E402,F401,F403 - the loop suites' shared fixtures

ACCEPTANCE = "python workflows/demo/tests/test_demo.py"


class FinalCase(LoopCase):

    def set_brief(self, out_of_scope="- None.", extra_files=()):
        """Rewrite and commit the fixture's brief, with its Out of scope section and any
        files that must exist when the run starts."""
        for name, data in extra_files:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        text = BRIEF.replace("## Out of scope\n- None.", "## Out of scope\n" + out_of_scope)
        (self.root / "brief.md").write_bytes(text.encode("utf-8"))
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "brief")

    def run_with(self, act, *, reviews=None):
        self.h.builder_script = [act]
        self.h.reviews = reviews if reviews is not None else [[]]
        return self.start()

    def evidence(self, run):
        return run.state["stop_reasons"][-1]["evidence"]

    def report(self, run):
        return (run.run_dir / "report.md").read_text(encoding="utf-8")

    def assertFailedOn(self, run, text):
        self.assertEqual(self.reason(run), sr.INTENT_FAILED,
                         json.dumps(run.state["stop_reasons"][-1])[-1200:])
        causes = self.evidence(run)["causes"]
        self.assertTrue(any(text in cause for cause in causes), causes)
        self.assertIn(text, self.report(run))


class EndingTests(FinalCase):

    def test_positive_the_checkers_launch_readings_are_kept(self):
        self.h.intent_launch = {"self_test": "passed"}
        run = self.run_with(edit(self.root, "done\n"))
        self.assertEqual(self.reason(run), sr.CLEAN)
        self.assertEqual(run.state["live"]["codex_intent_checker_launches"],
                         [{"step": "intent-final", "self_test": "passed"}])

    def test_negative_a_checker_with_no_launch_readings_records_none(self):
        run = self.run_with(edit(self.root, "done\n"))
        self.assertNotIn("codex_intent_checker_launches", run.state["live"])

    def test_positive_both_layers_passing_end_clean(self):
        run = self.run_with(edit(self.root, "done\n"))
        self.assertEqual(self.reason(run), sr.CLEAN)
        self.assertEqual(self.evidence(run), {"step": "intent-final", "round": 1,
                                              "cannot_tell": 0})
        self.assertEqual([c["provider"] for c in self.h.intent_calls], ["codex"])
        report = self.report(run)
        self.assertIn("## Intent check (final)", report)
        self.assertIn("**Result:** passed.", report)
        self.assertIn("Changed paths outside the edit paths: none.", report)
        data = json.loads((run.run_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(data["intent"]["causes"], [])
        self.assertTrue((run.run_dir / "intent" / "final-diff.patch").is_file())
        self.assertTrue((run.run_dir / "transcripts" / "intent-final.json").is_file())
        self.assertEqual(run.state["acceptance_exits"]["R1"],
                         [{"command": ACCEPTANCE, "exit": 0}])
        # Kept out of the reviewer findings ledger.
        self.assertEqual(run.state["ledger"], [])

    def test_positive_codex_building_puts_the_check_on_claude(self):
        self.h.swapped = True
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.h.frozen = Path(folder.name).resolve() / "frozen"
        self.h.frozen.mkdir()
        (self.h.frozen / "codex_hook.py").write_bytes(b"rules\n")
        run = self.run_with(edit(self.root, "done\n"))
        self.assertEqual(self.reason(run), sr.CLEAN)
        self.assertEqual([c["provider"] for c in self.h.intent_calls], ["claude"])
        before = [r for r in run.state["readings"] if r["step"] == "intent-final"]
        self.assertEqual(len(before), 1)
        self.assertIn("usage_probe", before[0], "a Claude step sends the probe first")

    def test_positive_cannot_tell_on_every_line_ends_clean_and_is_listed(self):
        self.h.intent_replies = [lambda p: intent_reply(p, status="cannot tell")]
        run = self.run_with(edit(self.root, "done\n"))
        self.assertEqual(self.reason(run), sr.CLEAN)
        self.assertEqual(self.evidence(run)["cannot_tell"], 2)
        report = self.report(run)
        self.assertIn("- cannot tell: work/a.txt says done.", report)
        self.assertIn("Cannot tell (not a failure): 2 line(s)", report)

    def test_negative_an_appended_log_elsewhere_does_not_fail(self):
        self.set_brief(extra_files=[("docs/LOG.md", b"[t] | first\n")])

        def act(prompt):
            with open(self.root / "docs" / "LOG.md", "a", encoding="utf-8",
                      newline="\n") as handle:
                handle.write("[t] | an audit appended this\n")
            return edit(self.root, "done\n")(prompt)
        run = self.run_with(act)
        self.assertEqual(self.reason(run), sr.CLEAN)
        self.assertIn("- information: `docs/LOG.md`: lines appended to an existing LOG.md",
                      self.report(run))

    def test_negative_outstanding_findings_never_reach_the_intent_check(self):
        self.h.builder_script = [edit(self.root, "x\n"),
                                 reply(actions(("R1-1", "declined-scope")))]
        self.h.reviews = [[finding()]]
        run = self.start()
        self.assertEqual(self.reason(run), sr.OUT_OF_SCOPE)
        cap = self.run_capped()
        self.assertEqual(self.reason(cap), sr.MAX_ROUNDS)
        self.assertEqual(self.h.intent_calls, [])

    def run_capped(self):
        self.reset_work()
        packet.packet_path(self.root, "capped").unlink(missing_ok=True)
        self.h.builder_script = [edit(self.root, "y\n")]
        self.h.reviews = [[finding()]]
        return self.start(cap=1, item="capped")

    def test_positive_no_ending_of_a_new_run_is_reviewed_clean(self):
        scripts = [([edit(self.root, "a\n")], [[]], None),
                   ([edit(self.root, "b\n"), reply(actions(("R1-1", "fixed")))],
                    [[finding()], []], None)]
        for number, (builder, reviews, _x) in enumerate(scripts):
            self.reset_work()
            self.h.builder_script, self.h.reviews = builder, reviews
            run = self.start(item=f"item{number}")
            self.assertNotEqual(self.reason(run), sr.REVIEWED_CLEAN)
            self.assertIn(self.reason(run), (sr.CLEAN, sr.INTENT_FAILED))

    def test_positive_the_step_imports_nothing_from_the_project_tree(self):
        before = set(sys.modules)
        run = self.run_with(edit(self.root, "done\n"))
        self.assertEqual(self.reason(run), sr.CLEAN)
        root = str(self.root.resolve())
        loaded = [name for name in set(sys.modules) - before
                  if str(getattr(sys.modules[name], "__file__", "") or "").startswith(root)]
        self.assertEqual(loaded, [])

    def test_positive_run_py_exits_0_only_on_clean(self):
        for reason, code in ((sr.CLEAN, 0), (sr.INTENT_FAILED, 1),
                             (sr.REVIEWED_CLEAN, 1), (sr.ERROR, 1)):
            run = SimpleNamespace(state={"status": "ended", "run_id": "r",
                                         "stop_reasons": [{"reason": reason}]})
            with redirect_stdout(io.StringIO()):
                self.assertEqual(run_py._outcome(run), code, reason)


class FailureTests(FinalCase):
    """Each cause the 10.5 table names ends the run ``intent-failed``, named in the
    report."""

    def test_rejection_a_changed_path_outside_the_edit_paths(self):
        def act(prompt):
            (self.root / "other.txt").write_bytes(b"stray\n")
            return edit(self.root, "done\n")(prompt)
        run = self.run_with(act)
        self.assertFailedOn(run, "other.txt: outside the edit paths")
        self.assertIn("Changed paths outside the edit paths: `other.txt`.",
                      self.report(run))
        # The changed path is put to the model layer too, which must list it.
        self.assertIn("- other.txt", self.h.intent_calls[0]["prompt"])

    def test_rejection_a_changed_path_inside_an_out_of_scope_path(self):
        self.set_brief("- `work/secret.txt`, and `no-such-thing`.",
                       [("work/secret.txt", b"keep\n")])
        run = self.run_with(lambda p: ((self.root / "work" / "secret.txt")
                                       .write_bytes(b"changed\n"), "done")[1])
        self.assertFailedOn(run, "work/secret.txt: inside the Out of scope path "
                                 "`work/secret.txt`")

    def test_rejection_a_change_inside_an_existing_ignored_out_of_scope_folder(self):
        (self.root / ".gitignore").write_bytes(b"memory/\nruns/\n__pycache__/\n"
                                               b"*.generated\nwork/cache/\n")
        self.set_brief("- `work/cache/`.", [("work/cache/old.md", b"old\n")])
        self.h.cat_a.add("work/cache/new.md")
        run = self.run_with(lambda p: ((self.root / "work" / "cache" / "new.md")
                                       .write_bytes(b"new\n"), "done")[1])
        self.assertFailedOn(run, "work/cache/new.md: inside the Out of scope path "
                                 "`work/cache`")

    def test_rejection_an_acceptance_result_not_exiting_0(self):
        self.h.acceptance_exits = {ACCEPTANCE: 3}
        run = self.run_with(edit(self.root, "done\n"))
        self.assertFailedOn(run, f"acceptance check `{ACCEPTANCE}`: exited 3")
        self.assertEqual(len(self.h.check_calls), 1, "no acceptance check is run again")

    def test_rejection_a_missing_acceptance_result(self):
        self.h.acceptance_lines = False
        run = self.run_with(edit(self.root, "done\n"))
        self.assertFailedOn(run, f"acceptance check `{ACCEPTANCE}`: no result recorded")

    def test_rejection_a_goal_line_not_met(self):
        self.h.intent_replies = [lambda p: intent_reply(p, statuses=["not met", "met"])]
        run = self.run_with(edit(self.root, "done\n"))
        self.assertFailedOn(run, "Goal line not met: work/a.txt says done.")

    def test_rejection_a_constraint_line_not_met(self):
        self.h.intent_replies = [lambda p: intent_reply(p, statuses=["met", "not met"])]
        run = self.run_with(edit(self.root, "done\n"))
        self.assertFailedOn(run, "Constraints line not met: Touch nothing else.")

    def test_rejection_a_brief_item_not_delivered(self):
        self.h.intent_replies = [lambda p: intent_reply(p, undelivered=["the summary"])]
        run = self.run_with(edit(self.root, "done\n"))
        self.assertFailedOn(run, "not delivered: the summary")

    def test_rejection_a_change_the_brief_did_not_ask_for(self):
        self.h.intent_replies = [lambda p: intent_reply(p, unasked=["a new option"])]
        run = self.run_with(edit(self.root, "done\n"))
        self.assertFailedOn(run, "not asked for: a new option")

    def test_rejection_a_planted_out_of_scope_change_and_missed_goal_together(self):
        self.set_brief("- `work/secret.txt`.", [("work/secret.txt", b"keep\n")])
        self.h.intent_replies = [lambda p: intent_reply(p, statuses=["not met", "met"])]
        run = self.run_with(lambda p: ((self.root / "work" / "secret.txt")
                                       .write_bytes(b"changed\n"), "done")[1])
        self.assertFailedOn(run, "work/secret.txt: inside the Out of scope path")
        self.assertFailedOn(run, "Goal line not met: work/a.txt says done.")


class RefusedReplyTests(FinalCase):
    """A reply that does not match its contract goes down the error path, never a pass
    and never ``cannot tell``, and its transcript is kept."""

    def test_rejection_each_refused_reply_ends_error(self):
        cases = {
            "a line left out": lambda p: {**intent_reply(p),
                                          "lines": intent_reply(p)["lines"][1:]},
            "blank evidence": lambda p: {**intent_reply(p), "lines": [
                dict(e, evidence="") for e in intent_reply(p)["lines"]]},
            "not the schema": lambda p: {"verdict": "fine"},
            "files_reviewed incomplete": lambda p: {**intent_reply(p),
                                                    "files_reviewed": []},
        }
        for number, (label, script) in enumerate(cases.items()):
            with self.subTest(label):
                self.reset_work()
                self.h.intent_replies = [script]
                self.h.builder_script = [edit(self.root, "done\n")]
                self.h.reviews = [[]]
                run = self.start(item=f"refused{number}")
                self.assertEqual(self.reason(run), sr.ERROR)
                self.assertEqual(self.evidence(run)["step"], "intent-final")
                self.assertIn("ReplyError", self.evidence(run)["error"])
                self.assertTrue((run.run_dir / "transcripts" / "intent-final.json")
                                .is_file())
                self.assertNotIn("final", run.state["intent"])


if __name__ == "__main__":
    unittest.main()
