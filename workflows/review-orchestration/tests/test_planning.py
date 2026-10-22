#!/usr/bin/env python3
"""Hermetic tests for A3 chunk 2, the planning sessions (plan 10.3 steps 1 to 3, step
6's paragraph "The run writes nothing in the project before approval", and "Resuming
the planning half").

What is under test: ``planning.py`` (the two planners, the ``awaiting-user`` pause and
its resume, the identity check and its path mask, the synthesiser, ``plan-report.md``),
the planner and synthesiser session factories in ``providers.py``, the edit-path reader
in ``brief.py``, and ``run.py --resume`` routing a run in its planning half.

Every provider, Docker and image step is a fake (``loop_fixtures.Harness`` and the fake
sessions below), in a temporary project that is a real git repository with a second
repository standing in for the personal one. No test starts Docker, a model session, a
Claude probe or a Codex app server. A run in its planning half is made by hand, as
chunk 1's tests make a run at the approval pause: a run record and a frozen folder with
no ``approval.json``, no ``started`` entry in the workflow log, and a ``planning``
record with nothing completed.

Same three controls as the other suites: a positive control must work, a rejection
control must end or refuse with the reason named and change nothing it should not, a
negative control must not.

    python workflows/review-orchestration/tests/test_planning.py
"""

import asyncio
import io
import json
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))
import loop_fixtures as suite  # noqa: E402

loop, sr, limits, codex_rules = suite.loop, suite.sr, suite.limits, suite.codex_rules
providers, runrecord, brief_mod = suite.providers, suite.runrecord, suite.brief
planning = suite.planning

UUID = "123e4567-e89b-12d3-a456-426614174000"


def plan(summary="Write done into the work file.", **changes):
    """A planner's reply that passes the schema and the identity check. Its prose uses
    the common words read and write as English, which is no tool form."""
    reply = {"summary": summary,
             "steps": [{"action": "Write the new text into work/a.txt.",
                        "files": ["work/a.txt"],
                        "verification": "Read it back and compare."}],
             "risks": ["None worth naming."], "assumptions": ["The brief is complete."],
             "open_questions": [], "confidence": "high"}
    reply.update(changes)
    return reply


def synthesis(**changes):
    merged = {k: v for k, v in plan(summary="The merged plan.").items()
              if k != "open_questions"}
    reply = {"agreements": ["Both change work/a.txt."], "disagreements": ["Wording."],
             "resolutions": [{"disagreement": "Wording.", "resolution": "Use done.",
                              "reason": "The brief says done."}],
             "retained_risks": ["None."], "merged_plan": merged}
    reply.update(changes)
    return reply


class FakeSession:
    """A read-only planning session: records its prompt and schema, waits on the
    case's barrier when one is set (so two that ran one after the other would fail),
    and returns its scripted reply or raises its scripted error."""

    def __init__(self, case, provider, role):
        self.case, self.provider, self.role = case, provider, role
        self.model = f"{provider}-{role}-model"
        self.refusals = []

    def review(self, prompt, schema):
        case = self.case
        case.events.append(f"review-{self.role}-{self.provider}")
        case.calls.append({"role": self.role, "provider": self.provider,
                           "prompt": prompt, "schema": schema})
        if self.role == "planner" and case.barrier is not None:
            case.barrier.wait()
        if self.role == "plan_reviewer":
            # The plan review of A3 chunk 3: clean unless a test scripts findings.
            answer = {"summary": "s", "findings": (case.plan_reviews.pop(0)
                                                   if case.plan_reviews else [])}
        else:
            answer = (case.plans if self.role == "planner" else case.synth)[self.provider]
        if isinstance(answer, BaseException):
            raise answer
        usage = ({"input_tokens": 10, "output_tokens": 5} if self.provider == "claude"
                 else {"total": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}})
        return providers.ReviewRecord(
            reply=json.loads(json.dumps(answer)),
            thread_id=f"{self.role}-session-{self.provider}-0001", status="completed",
            usage=usage, wall_seconds=0.1, raw={"r": self.provider}, tokens=15,
            model=self.model)


class PlanningCase(suite.LoopCase):

    def setUp(self):
        super().setUp()
        self.events, self.calls = [], []
        self.barrier = threading.Barrier(2, timeout=30)
        self.plans = {"claude": plan("Plan from the first planner."),
                      "codex": plan("Plan from the second planner.")}
        self.synth = {"claude": synthesis(), "codex": synthesis()}
        self.plan_reviews = []
        self.order = ["codex", "claude"]

    def deps(self):
        deps = self.h.deps()
        read, probe = deps.read_codex, deps.probe_claude

        def reading(root, folder=None):
            self.events.append("reading")
            return read(root, folder)

        def probing(model, cwd, on_event):
            self.events.append("probe")
            return probe(model, cwd, on_event)

        def planner(settings, provider, cwd, usage, frozen_dir):
            self.events.append(f"make-planner-{provider}")
            return FakeSession(self, provider, "planner")

        def synthesiser(settings, cwd, usage, frozen_dir):
            self.events.append("make-synthesiser")
            return FakeSession(self, settings["roles"]["builder"], "synthesiser")

        def plan_reviewer(settings, cwd, usage, frozen_dir):
            self.events.append("make-plan-reviewer")
            return FakeSession(self, settings["roles"]["reviewer"], "plan_reviewer")
        deps.read_codex, deps.probe_claude = reading, probing
        deps.make_planner, deps.make_synthesiser = planner, synthesiser
        deps.make_plan_reviewer = plan_reviewer
        deps.choose_order = lambda items: list(self.order)
        deps.uncaptured_category_a = lambda root, git_dir: set()
        return deps

    def load(self, run_id):
        return loop.load(run_id, root=self.root, deps=self.deps(), log_path=self.log,
                         runs_dir=self.runs)

    def planning_run(self):
        """A run at the start of its planning half, as a start leaves one since A3
        chunk 3: no approval.json, no LOG.md entry, nothing of its planning done."""
        run = loop.start("brief.md", "demo", str(self.personal), root=self.root,
                         deps=self.deps(), log_path=self.log, runs_dir=self.runs,
                         settings=self.settings, builder="claude")
        self.assertFalse((Path(run.state["frozen_dir"]) / codex_rules.APPROVAL_FILE)
                         .exists())
        self.assertFalse(self.log.exists())
        self.assertEqual(run.state["planning"]["completed"], [])
        self.events.clear()
        return self.load(run.state["run_id"])

    def advance(self, run):
        asyncio.run(planning.advance(run))
        return run

    def reason(self, run):
        return run.state["stop_reasons"][-1]["reason"]

    def outside(self):
        """Every file of the project outside the run records, and of the run's frozen
        folder, by path, for the no-write check."""
        files = {}
        for path in sorted(self.root.rglob("*")):
            rel = path.relative_to(self.root).as_posix()
            if path.is_file() and not rel.startswith(("runs/", ".git/")):
                files[rel] = path.read_bytes()
        return files

    def frozen_files(self, run):
        folder = Path(run.state["frozen_dir"])
        return {p.relative_to(folder).as_posix(): p.read_bytes()
                for p in sorted(folder.rglob("*")) if p.is_file()}

    def record(self, run):
        return {p.relative_to(run.run_dir).as_posix(): p.read_bytes()
                for p in sorted(run.run_dir.rglob("*")) if p.is_file()}


# -------------------------------------------------------------------- the planners


class PlannerTests(PlanningCase):
    """Plan 10.3 step 1, with "Usage before they start"."""

    def test_positive_both_planners_from_one_prompt_in_parallel_after_both_checks(self):
        run = self.advance(self.planning_run())
        self.assertEqual((run.state["status"], run.state["pause"]),
                         (sr.PAUSED, sr.AWAITING_PLAN_APPROVAL))
        # Both usage checks, one after the other (a Codex reading and the Claude probe
        # for the Claude planner, a Codex reading for the Codex one), before either
        # session is made or started.
        first_make = self.events.index("make-planner-claude")
        self.assertEqual(self.events[:first_make], ["reading", "probe", "reading"])
        self.assertEqual([r["step"] for r in run.state["readings"][:2]],
                         ["planner-claude", "planner-codex"])
        planners = [c for c in self.calls if c["role"] == "planner"]
        self.assertEqual(sorted(c["provider"] for c in planners), ["claude", "codex"])
        self.assertEqual(planners[0]["prompt"], planners[1]["prompt"])
        self.assertEqual(planners[0]["schema"], planning.PLANNER_SCHEMA)
        prompt = planners[0]["prompt"]
        self.assertIn("work/a.txt says done.", prompt)
        self.assertIn(run.state["start"]["public_head"], prompt)
        self.assertIn(run.state["start"]["personal_head"], prompt)
        self.assertIn("do not name yourself, your model or your tools", prompt)
        # No file content and nothing of the other planner.
        self.assertNotIn("start\n", prompt.split("THE BRIEF")[0])
        for other in self.plans.values():
            self.assertNotIn(other["summary"], prompt)
        # The barrier proves the two ran at the same time: one after the other, the
        # first would have waited alone and failed.
        self.assertFalse(self.barrier.broken)
        # Then one clean plan-review round (A3 chunk 3) before the pause.
        self.assertEqual(planning.planning_state(run)["completed"],
                         ["planners", "identity", "synthesis", "plan-review-R1"])

    def test_positive_each_planner_takes_its_providers_planner_entry(self):
        settings = json.loads(json.dumps(self.settings))
        settings["models"]["codex"]["planner"] = {"model": "astra-x", "effort": "low"}
        for provider, model, effort in (("claude", "claude-opus-5-5", "medium"),
                                        ("codex", "astra-x", "low")):
            session = providers.make_planner(settings, provider, cwd=self.root,
                                             frozen_dir=self.root)
            self.assertIs(type(session), providers.REVIEWERS[provider])
            self.assertEqual((session.model, session.effort), (model, effort))
        claude = providers.make_planner(settings, "claude", cwd=self.root)
        # The reviewers' lockdown: strict MCP with no server, every hook off.
        options = claude.options(planning.PLANNER_SCHEMA)
        self.assertEqual((options.mcp_servers, options.strict_mcp_config),
                         ({}, True))
        self.assertIn("disableAllHooks", options.settings)
        self.assertEqual(options.max_turns, settings["max_turns"]["reviewer"])

    def test_positive_the_synthesiser_is_the_builders_provider_and_model(self):
        for builder in ("claude", "codex"):
            settings = suite.settings_mod.assign_roles(
                suite.settings_mod.load_settings(), builder)
            session = providers.make_synthesiser(settings, cwd=self.root,
                                                 frozen_dir=self.root)
            entry = settings["models"][builder]["builder"]
            self.assertIs(type(session), providers.REVIEWERS[builder])
            self.assertEqual((session.model, session.effort),
                             (entry["model"], entry["effort"]))

    def test_rejection_a_limit_at_the_first_check_stops_both_before_any_session(self):
        run = self.planning_run()
        self.h.probe_readings = [limits.Reading(
            limits.CLAUDE, limits.LIMIT_REPORTED, reason="using paid overage",
            resets_at=5)]
        self.advance(run)
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(run.state["stop_reasons"][-1]["evidence"]["step"],
                         "planner-claude")
        self.assertFalse([e for e in self.events if e.startswith("make-")])
        self.assertFalse(self.log.exists(), "a resumable end writes no LOG.md entry")

    def test_rejection_a_limit_at_the_second_check_stops_both_before_any_session(self):
        run = self.planning_run()
        self.h.readings = [suite.reading(), suite.reading(95)]
        self.advance(run)
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(run.state["stop_reasons"][-1]["evidence"]["step"],
                         "planner-codex")
        self.assertFalse([e for e in self.events if e.startswith(("make-", "review-"))])
        self.assertFalse(self.log.exists())

    def test_rejection_a_limit_in_either_session_ends_usage_limit_and_replans_both(self):
        self.barrier = None
        run = self.planning_run()
        self.plans["codex"] = limits.UsageLimitError("codex", "usageLimitExceeded", 99)
        self.advance(run)
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(run.state["stop_reasons"][-1]["evidence"]["provider"], "codex")
        self.assertEqual(planning.planning_state(run)["completed"], [])
        self.assertFalse(self.log.exists())
        # The resume runs both planners again together, never one.
        self.plans["codex"] = plan("Plan from the second planner.")
        self.calls.clear()
        again = self.load(run.state["run_id"])
        planning.resume(again)
        self.advance(again)
        self.assertEqual(sorted(c["provider"] for c in self.calls
                                if c["role"] == "planner"), ["claude", "codex"])
        self.assertEqual(again.state["pause"], sr.AWAITING_PLAN_APPROVAL)
        self.assertFalse(self.log.exists(), "a resume in planning logs nothing")

    def test_rejection_a_planner_reply_breaking_the_schema_ends_error(self):
        broken = {"a missing field": {k: v for k, v in plan().items() if k != "risks"},
                  "an extra field": plan(extra="x"),
                  "a confidence not allowed": plan(confidence="certain"),
                  "a step with no files": plan(steps=[{"action": "a",
                                                       "verification": "v"}])}
        for label, reply in broken.items():
            with self.subTest(label):
                self.setUp()
                run = self.planning_run()
                self.plans["codex"] = reply
                self.advance(run)
                self.assertEqual(self.reason(run), sr.ERROR)
                self.assertIn("the codex planner",
                              run.state["stop_reasons"][-1]["evidence"]["error"])
                self.assertFalse([c for c in self.calls if c["role"] == "synthesiser"])
                self.assertFalse(self.log.exists())

    def test_negative_a_failed_planner_ends_error_with_both_named_where_both_fail(self):
        self.barrier = None
        run = self.planning_run()
        self.plans = {"claude": providers.ProviderError("one"),
                      "codex": providers.ProviderError("two")}
        self.advance(run)
        error = run.state["stop_reasons"][-1]["evidence"]["error"]
        self.assertIn("the claude planner: ProviderError: one", error)
        self.assertIn("the codex planner: ProviderError: two", error)


# ------------------------------------------------------------- questions and resume


class QuestionTests(PlanningCase):
    """Plan 10.3 step 2: any question pauses the run ``awaiting-user``; the resume
    reads the revised brief's edit paths, makes the four approval checks, checks the
    whole brief, then runs both planners again."""

    def paused(self, provider="claude"):
        run = self.planning_run()
        self.plans[provider] = plan(open_questions=["Which wording?"])
        self.advance(run)
        self.plans[provider] = plan("Plan again, the question answered.")
        return run

    def answered(self, run, answer=" | Answer: done"):
        path = run.run_dir / "brief.md"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "## Open questions\nNone", f"## Open questions\n- Which wording?{answer}"),
            encoding="utf-8", newline="\n")

    def test_positive_a_question_from_either_planner_pauses_the_run(self):
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                self.setUp()
                run = self.paused(provider)
                self.assertEqual((run.state["status"], run.state["pause"]),
                                 (sr.PAUSED, sr.AWAITING_USER))
                self.assertEqual(planning.planning_state(run)["questions"],
                                 {provider: ["Which wording?"]})
                self.assertEqual(planning.planning_state(run)["completed"], [])
                text = (run.run_dir / planning.QUESTIONS_FILE).read_text(encoding="utf-8")
                self.assertIn("Which wording?", text)
                self.assertIn(f"--resume {run.state['run_id']}", text)
                self.assertFalse([c for c in self.calls if c["role"] == "synthesiser"])
                self.assertFalse(self.log.exists(), "a pause writes nothing to LOG.md")

    def test_negative_a_question_the_brief_answers_still_pauses(self):
        # The script does no matching of its own (simulation gap 11).
        run = self.planning_run()
        self.answered(run)
        self.plans["codex"] = plan(open_questions=["Which wording?"])
        self.advance(run)
        self.assertEqual(run.state["pause"], sr.AWAITING_USER)

    def test_positive_the_resume_order_then_both_planners_again(self):
        run = self.paused()
        self.answered(run)
        order = []
        with mock.patch.object(planning.brief_mod, "read_edit_paths",
                               side_effect=lambda *a, real=brief_mod.read_edit_paths:
                               order.append("edit paths") or real(*a)), \
                mock.patch.object(loop, "approval_problems",
                                  side_effect=lambda r, p, real=loop.approval_problems:
                                  order.append(("checks", tuple(p))) or real(r, p)), \
                mock.patch.object(planning.brief_mod, "check_brief",
                                  side_effect=lambda *a, real=brief_mod.check_brief:
                                  order.append("brief check") or real(*a)):
            again = self.load(run.state["run_id"])
            self.calls.clear()
            planning.resume(again)
        self.assertEqual(order, ["edit paths", ("checks", ("work/",)), "brief check"])
        self.advance(again)
        self.assertEqual(sorted(c["provider"] for c in self.calls
                                if c["role"] == "planner"), ["claude", "codex"])
        self.assertIn("Which wording? | Answer: done",
                      [c for c in self.calls if c["role"] == "planner"][0]["prompt"])
        self.assertEqual(again.state["pause"], sr.AWAITING_PLAN_APPROVAL)
        self.assertFalse(self.log.exists(), "neither the resume nor its end logs")

    def refused(self, run, *named, absent=()):
        before = (self.record(run), self.outside(), self.frozen_files(run))
        again = self.load(run.state["run_id"])
        with redirect_stderr(io.StringIO()), \
                self.assertRaises(loop.RunRefused) as caught:
            planning.resume(again)
        text = " ".join(caught.exception.args[0])
        for name in named:
            self.assertIn(name, text)
        for name in absent:
            self.assertNotIn(name, text)
        self.assertEqual((self.record(run), self.outside(), self.frozen_files(run)),
                         before, "a refusal changes nothing")
        self.assertEqual(runrecord.read_state(run.run_dir)["pause"], sr.AWAITING_USER)
        self.assertFalse([c for c in self.calls if c["role"] == "planner"
                          and "Plan again" in json.dumps(c)])
        return text

    def test_rejection_edit_paths_that_cannot_be_read_refuse_first(self):
        run = self.paused()
        path = run.run_dir / "brief.md"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "- work/ | Measure: git ls-files work", "- ../work/ | Measure: x"),
            encoding="utf-8", newline="\n")
        # The working tree has moved too, which the checks would name, and the open
        # question is unanswered, which the brief check would name: neither is reached.
        (self.root / "work" / "a.txt").write_bytes(b"moved\n")
        self.refused(run, "edit paths cannot be read", "nothing was changed",
                     absent=("check 2", "does not pass the check"))

    def test_rejection_a_failed_approval_check_refuses_before_the_brief_check(self):
        run = self.paused()  # its question is still unanswered: the brief check fails
        (self.root / "work" / "a.txt").write_bytes(b"moved during the pause\n")
        self.refused(run, "check 2 (the public repository)", "work/a.txt",
                     absent=("does not pass the check",))

    def test_rejection_a_revised_brief_failing_the_check_refuses_logging_nothing(self):
        run = self.paused()
        self.answered(run, answer="")  # still no answer
        text = self.refused(run, "does not pass the check", "question has no answer")
        self.assertNotIn("check 1", text)
        self.assertFalse(self.log.exists(), "the brief check logs nothing")

    def test_positive_a_dry_resume_checks_and_changes_nothing(self):
        run = self.paused()
        self.answered(run)
        before = (self.record(run), self.outside())
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertIsNone(planning.resume(self.load(run.state["run_id"]),
                                              dry_run=True))
        self.assertIn("[DRY RUN] would resume run", err.getvalue())
        self.assertEqual((self.record(run), self.outside()), before)

    def test_positive_run_py_resume_routes_a_planning_run_to_its_planning_half(self):
        run = self.paused()
        self.answered(run)
        out = io.StringIO()
        with redirect_stdout(out):
            code = suite.run_py.main(["--resume", run.state["run_id"]], root=self.root,
                                     log_path=self.log, deps=self.deps(),
                                     runs_dir=self.runs)
        self.assertEqual(code, 1, out.getvalue())
        self.assertIn(f"is paused ({sr.AWAITING_PLAN_APPROVAL})", out.getvalue())
        self.assertIn("--approve", out.getvalue())
        self.assertFalse([s for s in runrecord.read_state(run.run_dir)["steps"]
                          if s["role"] == "builder"], "no build was started")

    def test_positive_run_py_passes_build_options_to_a_planning_resume(self):
        # Since A3 chunk 3 (the chunk 3 bullet's first follow-on) run.py no longer
        # refuses --rounds, --allow-paid or --wait for a run in its planning half: it
        # passes them on, and --rounds sets the plan-review cap from the rounds held.
        run = self.paused()
        self.answered(run)
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = suite.run_py.main(["--resume", run.state["run_id"], "--rounds", "2"],
                                     root=self.root, log_path=self.log, deps=self.deps(),
                                     runs_dir=self.runs)
        self.assertEqual(code, 1, out.getvalue())
        self.assertNotIn("do not apply to a run in its planning half", out.getvalue())
        self.assertIn(f"is paused ({sr.AWAITING_PLAN_APPROVAL})", out.getvalue())
        saved = runrecord.read_state(run.run_dir)
        self.assertEqual(saved["planning"]["round_cap"], 2)

    def test_negative_a_run_with_no_planning_half_resumes_as_before(self):
        self.assertFalse(planning.in_planning(object()))
        self.h.builder_script = [suite.edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        self.assertFalse(planning.in_planning(run))


# ------------------------------------------------------------- the identity check


class IdentityRuleTests(unittest.TestCase):
    """Plan 10.3 step 3: every rule matches, on every string value of a reply."""

    def setUp(self):
        self.identifiers = planning.load_identifiers()
        self.rules = planning.identity_rules(
            self.identifiers, ["orion-reviewer-9"], ["planner-session-codex-0001"])
        self.known = planning.KnownPaths(
            {"workflows/review-orchestration/prompts/builder-tools-claude.md",
             "workflows/review-orchestration/config/codex-verify-commands.txt",
             "workflows/codex-tools/README.md", ".claude/settings.json",
             ".codex/rules/review-orchestration.rules"},
            ["workflows/review-orchestration/"])

    def found(self, text):
        return planning.check_identity({"summary": text}, self.rules, self.known)

    def rules_of(self, text):
        return {(m["rule"], m["text"]) for m in self.found(text)}

    def test_positive_every_name_without_case_as_a_whole_word(self):
        for name in self.identifiers["names"]:
            for spelt in (name, name.lower(), name.upper()):
                with self.subTest(spelt):
                    self.assertIn(("names", spelt), self.rules_of(f"As {spelt} would."))

    def test_positive_a_model_name_from_the_settings_for_another_role(self):
        settings = suite.settings_mod.assign_roles(suite.settings_mod.load_settings(),
                                                    "claude")
        settings["models"]["codex"]["intent_checker"] = {"model": "orion-reviewer-9",
                                                         "effort": "medium"}
        self.assertIn("orion-reviewer-9", planning.model_names(settings))
        self.assertIn(settings["usage_probe_model"], planning.model_names(settings))
        self.assertIn(("names", "orion-reviewer-9"),
                      self.rules_of("Run it on orion-reviewer-9 first."))

    def test_negative_a_name_inside_a_longer_word_is_not_a_match(self):
        self.assertEqual(self.found("The codexes and claudet are not names."), [])

    def test_positive_each_distinctive_tool_name_anywhere(self):
        for name in self.identifiers["distinctive"]:
            with self.subTest(name):
                self.assertIn(("tool-names", name),
                              self.rules_of(f"Then use {name} for it."))

    def test_negative_a_distinctive_name_is_matched_with_case_and_whole(self):
        self.assertEqual(self.found("A websearch and WebSearches are prose."), [])

    def test_positive_any_identifier_beginning_with_a_prefix(self):
        self.assertIn(("tool-names", "mcp__checks__run_check"),
                      self.rules_of("Call mcp__checks__run_check."))
        self.assertIn(("tool-names", "mcp__"), self.rules_of("Any mcp__ tool."))

    def test_positive_each_common_word_written_as_a_tool(self):
        for name in self.identifiers["common"]:
            for text in (f"Use `{name}` on it.", f"Use `then {name} here` on it.",
                         f"Call {name}(path)."):
                with self.subTest(text):
                    self.assertIn(("tool-names", name), self.rules_of(text))

    def test_negative_a_common_word_in_prose_or_across_lines_is_not_a_tool(self):
        for name in self.identifiers["common"]:
            with self.subTest(name):
                self.assertEqual(self.found(f"{name} the file. `{name}s` and "
                                            f"{name.lower()}(x)"), [])
        self.assertEqual(self.found("A span `that\nends` here holds Read only."), [])

    def test_positive_session_metadata(self):
        for text, matched in (("See planner-session-codex-0001 there.",
                               "planner-session-codex-0001"),
                              (f"Session {UUID.upper()} ended.", UUID.upper()),
                              ("Edit .claude/settings.json first.", ".claude/"),
                              ("Read .codex/rules/review-orchestration.rules.", ".codex/"),
                              ("Read ~/.codex for it.", "~/.codex")):
            with self.subTest(text):
                self.assertIn(("session-metadata", matched), self.rules_of(text))

    def test_positive_tool_metadata(self):
        for word in planning.TOOL_METADATA:
            with self.subTest(word):
                self.assertIn(("tool-metadata", word), self.rules_of(f"Keep {word} so."))
        self.assertEqual(self.found("A my_session_id_x is not one."), [])

    def test_positive_every_string_value_with_its_field_and_line(self):
        reply = plan(steps=[{"action": "First line.\nThen ask Codex.",
                             "files": ["work/a.txt", "notes/claude.txt"],
                             "verification": "v"}])
        found = planning.check_identity(reply, self.rules, self.known)
        self.assertIn({"rule": "names", "text": "Codex", "field": "steps[0].action",
                       "line": 2}, found)
        self.assertIn({"rule": "names", "text": "claude", "field": "steps[0].files[1]",
                       "line": 1}, found)

    def test_negative_a_clean_plan_has_no_match(self):
        self.assertEqual(planning.check_identity(plan(), self.rules, self.known), [])


class PathMaskTests(IdentityRuleTests):
    """Plan 10.3 step 3, "Real project paths are left out of the check"."""

    def test_positive_a_real_path_and_a_folder_in_the_trees_are_masked(self):
        for text in ("Change workflows/review-orchestration/config/"
                     "codex-verify-commands.txt.",
                     "Change ./workflows/review-orchestration/prompts/"
                     "builder-tools-claude.md, then stop.",
                     "Look in workflows/codex-tools/ and workflows/codex-tools."):
            with self.subTest(text):
                self.assertEqual(self.found(text), [])

    def test_positive_a_bare_file_name_of_a_real_file_is_masked(self):
        self.assertEqual(self.found("Edit builder-tools-claude.md."), [])

    def test_positive_a_new_path_under_an_edit_path_is_masked(self):
        self.assertEqual(self.found("Add workflows/review-orchestration/prompts/"
                                    "planner-claude.md."), [])

    def test_rejection_a_path_outside_the_trees_and_edit_paths_is_not_masked(self):
        self.assertIn(("names", "claude"), self.rules_of("Add docs/claude-notes.md."))
        self.assertIn(("names", "claude"), self.rules_of("Edit claude-notes.md."))

    def test_rejection_claude_and_codex_folders_are_never_masked(self):
        # Both are files of the tree, and still never masked.
        for text, matched in ((".claude/settings.json", ".claude/"),
                              (".codex/rules/review-orchestration.rules", ".codex/"),
                              ("workflows/.codex/x", ".codex/")):
            with self.subTest(text):
                self.assertIn(("session-metadata", matched),
                              self.rules_of(f"Change {text} now."))

    def test_positive_the_mask_keeps_positions_and_never_changes_the_plan(self):
        text = "Edit builder-tools-claude.md.\nThen Codex."
        masked = planning.mask(text, self.known)
        self.assertEqual(len(masked), len(text))
        self.assertEqual(masked.count("\n"), 1)
        reply = {"summary": text}
        planning.check_identity(reply, self.rules, self.known)
        self.assertEqual(reply, {"summary": text})


class IdentifierFileTests(unittest.TestCase):
    """The file's four sections and its preflight refusal."""

    def write(self, text):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "ids.txt"
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    GOOD = "# c\n\n[names]\nClaude\n[distinctive]\nWebFetch\n[common]\nRead\n" \
           "[prefixes]\nmcp__\n"

    def test_positive_the_shipped_file_holds_the_lists_step_3_names(self):
        found = planning.load_identifiers()
        self.assertEqual(found["names"], ["Claude", "Anthropic", "Codex", "OpenAI", "GPT",
                                          "ChatGPT"])
        self.assertEqual(found["distinctive"], ["ToolSearch", "NotebookEdit", "WebFetch",
                                                "WebSearch", "PowerShell", "apply_patch"])
        self.assertEqual(found["common"], ["Bash", "Read", "Edit", "Write", "Glob",
                                           "Grep", "Agent", "Skill"])
        self.assertEqual(found["prefixes"], ["mcp__"])
        self.assertEqual(planning.preflight(), [])

    def test_rejection_a_malformed_file_is_refused_naming_why(self):
        cases = {"outside a section": "Claude\n" + self.GOOD,
                 "is not one of": self.GOOD + "[tools]\nX\n",
                 "given twice": self.GOOD + "[names]\nY\n",
                 "holds whitespace": self.GOOD.replace("Claude", "Open AI"),
                 "missing or empty": self.GOOD.replace("[prefixes]\nmcp__\n", "")}
        for reason, text in cases.items():
            with self.subTest(reason):
                path = self.write(text)
                with self.assertRaisesRegex(planning.IdentifierError, reason):
                    planning.load_identifiers(path)
                self.assertTrue(planning.preflight(path))
        self.assertEqual(planning.preflight(self.write(self.GOOD)), [])


class IdentityRunTests(PlanningCase):
    """The identity check in a run: a refused plan never reaches the synthesiser."""

    def test_rejection_a_refused_plan_ends_error_and_never_reaches_the_synthesiser(self):
        run = self.planning_run()
        self.plans["codex"] = plan(summary="As Codex I would use `Read` here.")
        self.advance(run)
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertFalse([c for c in self.calls if c["role"] == "synthesiser"])
        report = json.loads((run.run_dir / "report.json").read_text(encoding="utf-8"))
        refused, = report["identity_refusal"]["plans"]
        self.assertEqual((refused["label"], refused["provider"]), ("Plan 1", "codex"))
        self.assertEqual({(m["rule"], m["text"], m["line"]) for m in refused["matches"]},
                         {("names", "Codex", 1), ("tool-names", "Read", 1)})
        lines = (run.run_dir / "report.md").read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines[2].startswith("**Identity check refused Plan 1 (codex)"))
        self.assertIn("rule names: `Codex` in summary, line 1", lines[3])
        raw = json.loads((run.run_dir / "transcripts" / "planner-codex.json")
                         .read_text(encoding="utf-8"))
        self.assertEqual(raw["reply"]["summary"], "As Codex I would use `Read` here.")
        self.assertFalse(self.log.exists())

    def test_positive_a_refusal_discards_the_plans_so_a_resume_plans_again(self):
        run = self.planning_run()
        self.plans["claude"] = plan(summary="See .claude/settings.json.")
        self.advance(run)
        planning_record = planning.planning_state(run)
        self.assertNotIn("plans", planning_record)
        self.assertEqual(planning_record["completed"], [])
        self.plans["claude"] = plan("A clean plan.")
        again = self.load(run.state["run_id"])
        planning.resume(again)
        self.advance(again)
        self.assertEqual(again.state["pause"], sr.AWAITING_PLAN_APPROVAL)
        self.assertNotIn("identity_refusal", again.state)

    def test_rejection_a_planners_recorded_session_id_in_the_other_plan(self):
        run = self.planning_run()
        self.plans["claude"] = plan(summary="Unlike planner-session-codex-0001.")
        self.advance(run)
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertIn("session-metadata", json.dumps(run.state["identity_refusal"]))

    def test_positive_real_paths_in_a_run_are_masked_by_the_trees(self):
        # The fixture's trees hold work/a.txt and memory/note.md; a tracked file named
        # for a provider is masked too.
        (self.root / "work" / "codex_rules.txt").write_bytes(b"x\n")
        suite.git(self.root, "add", "work/codex_rules.txt")
        suite.git(self.root, "commit", "-qm", "named")
        run = self.planning_run()
        self.plans["claude"] = plan(summary="Read memory/note.md and codex_rules.txt.")
        self.advance(run)
        self.assertEqual(run.state["pause"], sr.AWAITING_PLAN_APPROVAL)


# ------------------------------------------------------------------ the synthesis


class SynthesisTests(PlanningCase):

    def test_positive_labels_in_a_random_order_recorded_and_no_provider_names(self):
        for order in (["codex", "claude"], ["claude", "codex"]):
            with self.subTest(order=order):
                self.setUp()
                self.order = order
                run = self.advance(self.planning_run())
                labels = planning.planning_state(run)["labels"]
                self.assertEqual(labels, {"Plan 1": order[0], "Plan 2": order[1]})
                saved = runrecord.read_state(run.run_dir)["planning"]["labels"]
                self.assertEqual(saved, labels)
                call, = [c for c in self.calls if c["role"] == "synthesiser"]
                prompt = call["prompt"]
                first = self.plans[order[0]]["summary"]
                second = self.plans[order[1]]["summary"]
                self.assertLess(prompt.index("PLAN 1"), prompt.index(first))
                self.assertLess(prompt.index(first), prompt.index("PLAN 2"))
                self.assertLess(prompt.index("PLAN 2"), prompt.index(second))
                self.assertIn("work/a.txt says done.", prompt)
                for name in ("claude", "codex", "anthropic", "openai"):
                    self.assertNotIn(name, prompt.lower())

    def test_positive_the_default_order_is_a_random_permutation(self):
        seen = {tuple(loop._random_order(["claude", "codex"])) for _ in range(200)}
        self.assertEqual(seen, {("claude", "codex"), ("codex", "claude")})

    def test_positive_the_synthesiser_is_the_builders_provider_with_the_schema(self):
        run = self.advance(self.planning_run())
        call, = [c for c in self.calls if c["role"] == "synthesiser"]
        self.assertEqual(call["provider"], "claude")
        self.assertEqual(call["schema"], planning.SYNTHESIS_SCHEMA)
        merged = planning.SYNTHESIS_SCHEMA["properties"]["merged_plan"]
        self.assertEqual(set(merged["required"]),
                         set(planning.PLANNER_SCHEMA["required"]) - {"open_questions"})
        step = [s for s in run.state["steps"] if s["step"] == "synthesis"][0]
        self.assertEqual((step["role"], step["provider"]), ("synthesiser", "claude"))
        self.assertEqual(planning.planning_state(run)["synthesis"], self.synth["claude"])

    def test_rejection_a_synthesis_breaking_its_schema_ends_error(self):
        merged_with_questions = dict(synthesis()["merged_plan"], open_questions=[])
        broken = {"open questions in the merged plan":
                  synthesis(merged_plan=merged_with_questions),
                  "a missing field": {k: v for k, v in synthesis().items()
                                      if k != "agreements"},
                  "a resolution with no reason": synthesis(resolutions=[
                      {"disagreement": "d", "resolution": "r"}])}
        for label, reply in broken.items():
            with self.subTest(label):
                self.setUp()
                self.synth["claude"] = reply
                run = self.advance(self.planning_run())
                self.assertEqual(self.reason(run), sr.ERROR)
                self.assertIn("the synthesiser",
                              run.state["stop_reasons"][-1]["evidence"]["error"])
                self.assertFalse((run.run_dir / planning.PLAN_REPORT).exists())

    def test_rejection_a_limit_before_the_synthesis_resumes_at_the_synthesis(self):
        run = self.planning_run()
        # The planners' two readings pass; the synthesis step's probe reports overage.
        self.h.probe_readings = [limits.Reading(limits.CLAUDE, limits.REPORTED, percent=1),
                                 limits.Reading(limits.CLAUDE, limits.LIMIT_REPORTED,
                                                reason="using paid overage",
                                                resets_at=self.h.clock - 1)]
        self.advance(run)
        self.assertEqual(self.reason(run), sr.USAGE_LIMIT)
        self.assertEqual(planning.planning_state(run)["completed"],
                         ["planners", "identity"])
        self.calls.clear()
        again = self.load(run.state["run_id"])
        planning.resume(again)
        self.advance(again)
        self.assertEqual([c["role"] for c in self.calls], ["synthesiser", "plan_reviewer"],
                         "never mid-step, and the planners are not run again")
        self.assertEqual(again.state["pause"], sr.AWAITING_PLAN_APPROVAL)


# --------------------------------------------------------- the report and the writes


class PlanReportTests(PlanningCase):

    def test_positive_the_plan_report_holds_every_part(self):
        run = self.advance(self.planning_run())
        text = (run.run_dir / planning.PLAN_REPORT).read_text(encoding="utf-8")
        for part in ("## Plan 1 (by the codex planner)", "## Plan 2 (by the claude planner)",
                     self.plans["codex"]["summary"], self.plans["claude"]["summary"],
                     "## Synthesis", "Both change work/a.txt.", "Wording.: Use done.",
                     "### The merged plan", "The merged plan.", "`work/a.txt`",
                     "## Plan review findings", "None: the plan review found nothing",
                     "## Intent check", "No intent check has run"):
            self.assertIn(part, text)

    def test_positive_nothing_is_written_outside_the_run_record(self):
        run = self.planning_run()
        before, frozen_before = self.outside(), self.frozen_files(run)
        memory = sorted(p.name for p in (self.root / "memory").iterdir())
        self.advance(run)
        self.assertEqual(run.state["pause"], sr.AWAITING_PLAN_APPROVAL)
        self.assertEqual(self.outside(), before)
        self.assertEqual(self.frozen_files(run), frozen_before)
        self.assertEqual(sorted(p.name for p in (self.root / "memory").iterdir()), memory)
        self.assertFalse(self.log.exists())
        self.assertEqual([r.name for r in self.runs.iterdir()], [run.state["run_id"]])

    def test_positive_an_error_end_is_recorded_in_state_and_report_only(self):
        self.barrier = None
        run = self.planning_run()
        before = self.outside()
        self.plans["claude"] = providers.ProviderError("down")
        self.advance(run)
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertTrue((run.run_dir / "report.md").is_file())
        self.assertEqual(runrecord.read_state(run.run_dir)["stop_reasons"][-1]["reason"],
                         sr.ERROR)
        self.assertEqual(self.outside(), before)
        self.assertFalse(self.log.exists())

    def test_positive_the_approval_pause_is_one_chunk_1_approves(self):
        run = self.advance(self.planning_run())
        self.h.builder_script = [suite.edit(self.root, "done\n")]
        self.h.reviews = [[]]
        again = self.load(run.state["run_id"])
        loop.approve(again)
        # The step it paused on is the clean plan review that follows the synthesis.
        self.assertEqual(again.state["approval"]["planning_step"], "plan-review-R1")
        asyncio.run(again.advance())
        self.assertEqual(again.state["stop_reasons"][-1]["reason"], sr.REVIEWED_CLEAN)

    def test_rejection_a_malformed_identifiers_file_ends_before_any_session(self):
        run = self.planning_run()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        bad = Path(folder.name) / "ids.txt"
        bad.write_text("Claude\n", encoding="utf-8", newline="\n")
        with mock.patch.object(planning, "IDENTIFIERS_PATH", bad):
            self.advance(run)
            with self.assertRaises(loop.RunRefused):
                planning.resume(self.load(run.state["run_id"]))
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertIn("outside a section", run.state["stop_reasons"][-1]["evidence"]
                      ["error"])
        self.assertEqual(self.events, [])


class EditPathReaderTests(unittest.TestCase):
    """``brief.read_edit_paths``: the edit paths alone, the rest unchecked."""

    def brief(self, text):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        (root / "b.md").write_text(text, encoding="utf-8", newline="\n")
        return root

    def test_positive_paths_are_read_while_another_section_fails(self):
        text = suite.BRIEF.replace("## Open questions\nNone",
                                   "## Open questions\n- Unanswered?")
        root = self.brief(text)
        self.assertNotEqual(brief_mod.check_brief("b.md", root)[1], 0)
        self.assertEqual(brief_mod.read_edit_paths("b.md", root), ["work/"])

    def test_rejection_unreadable_edit_paths(self):
        for label, text in (("a refused path", suite.BRIEF.replace("- work/ |", "- .git/x |")),
                            ("no section", suite.BRIEF.replace("## Edit paths\n", "")),
                            ("an empty section", suite.BRIEF.replace(
                                "- work/ | Measure: git ls-files work\n", ""))):
            with self.subTest(label), self.assertRaises(ValueError):
                brief_mod.read_edit_paths("b.md", self.brief(text))
        with self.assertRaises(ValueError):
            brief_mod.read_edit_paths("missing.md", self.brief(suite.BRIEF))


class ReviewRoundOneTests(PlanningCase):
    """Code review R1: the revised brief's edit paths in the mask (R1-1), a planning run
    interrupted while running resumed from its last completed step (R1-2), and every
    disagreement in the plan report (R1-3)."""

    NAMED = "Add notes/claude-notes.md for the record."

    def question_pause(self):
        run = self.planning_run()
        self.plans["claude"] = plan(open_questions=["Where do notes go?"])
        self.advance(run)
        self.assertEqual(run.state["pause"], sr.AWAITING_USER)
        self.plans["claude"] = plan(summary=self.NAMED)
        return run

    def revise(self, run, extra_path):
        path = run.run_dir / "brief.md"
        text = path.read_text(encoding="utf-8").replace(
            "## Open questions\nNone",
            "## Open questions\n- Where do notes go? | Answer: notes/")
        if extra_path:
            text = text.replace("- work/ | Measure: git ls-files work",
                                "- work/ | Measure: git ls-files work\n"
                                "- notes/ | Measure: git ls-files notes")
        path.write_text(text, encoding="utf-8", newline="\n")

    def test_positive_a_new_path_under_a_revised_edit_path_is_masked(self):
        run = self.question_pause()
        self.revise(run, extra_path=True)
        again = self.load(run.state["run_id"])
        planning.resume(again)
        self.advance(again)
        self.assertEqual(again.state["pause"], sr.AWAITING_PLAN_APPROVAL,
                         again.state.get("identity_refusal"))
        self.assertEqual(again.state["edit_paths"], ["work/"],
                         "state.json's scope waits for approval to rebuild it")

    def test_rejection_the_same_path_without_the_revised_edit_path_is_refused(self):
        run = self.question_pause()
        self.revise(run, extra_path=False)
        again = self.load(run.state["run_id"])
        planning.resume(again)
        self.advance(again)
        self.assertEqual(self.reason(again), sr.ERROR)
        self.assertIn('"claude"', json.dumps(again.state["identity_refusal"]))

    def interrupted_after_identity(self):
        run = self.planning_run()
        self.synth["claude"] = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.advance(run)
        saved = runrecord.read_state(run.run_dir)
        self.assertEqual(saved["status"], sr.RUNNING)
        self.assertEqual(saved["planning"]["completed"], ["planners", "identity"])
        self.synth["claude"] = synthesis()
        return run

    def test_positive_a_run_interrupted_between_steps_resumes_at_the_next(self):
        run = self.interrupted_after_identity()
        self.calls.clear()
        again = self.load(run.state["run_id"])
        planning.resume(again)
        self.advance(again)
        self.assertEqual([c["role"] for c in self.calls], ["synthesiser", "plan_reviewer"])
        self.assertEqual(again.state["pause"], sr.AWAITING_PLAN_APPROVAL)
        self.assertEqual(planning.planning_state(again)["interrupted_resumes"], 1)
        self.assertFalse(self.log.exists())

    def test_positive_a_dry_resume_of_an_interrupted_run_changes_nothing(self):
        run = self.interrupted_after_identity()
        before = self.record(run)
        err = io.StringIO()
        with redirect_stderr(err):
            planning.resume(self.load(run.state["run_id"]), dry_run=True)
        self.assertIn("interrupted while running", err.getvalue())
        self.assertIn("at synthesis", err.getvalue())
        self.assertEqual(self.record(run), before)

    def test_positive_a_run_interrupted_after_its_synthesis_was_saved(self):
        run = self.advance(self.planning_run())
        run.state.update(status=sr.RUNNING, pause=None)
        run.save()
        (run.run_dir / planning.PLAN_REPORT).unlink()
        self.calls.clear()
        again = self.load(run.state["run_id"])
        planning.resume(again)
        self.advance(again)
        self.assertEqual(self.calls, [], "no session is opened again")
        self.assertEqual(again.state["pause"], sr.AWAITING_PLAN_APPROVAL)
        self.assertTrue((again.run_dir / planning.PLAN_REPORT).is_file())

    def test_positive_every_disagreement_is_in_the_report_resolved_or_not(self):
        self.synth["claude"] = synthesis(disagreements=["Wording.", "An unresolved point."])
        run = self.advance(self.planning_run())
        text = (run.run_dir / planning.PLAN_REPORT).read_text(encoding="utf-8")
        listed = text.split("Disagreements:")[1].split("Resolutions:")[0]
        self.assertIn("- Wording.", listed)
        self.assertIn("- An unresolved point.", listed)
        self.assertIn("Wording.: Use done.", text.split("Resolutions:")[1])


class FixtureGitQuietTests(unittest.TestCase):
    """``fixture_git.quiet_maintenance``: no detached git maintenance in a test process,
    whose orphans the check container's first process never reaps (A3 chunk 2)."""

    def test_positive_the_settings_are_added_once(self):
        environ = suite.fixture_git.quiet_maintenance({})
        self.assertEqual(environ["GIT_CONFIG_COUNT"], "4")
        self.assertEqual([(environ[f"GIT_CONFIG_KEY_{n}"], environ[f"GIT_CONFIG_VALUE_{n}"])
                          for n in range(4)], list(suite.fixture_git.QUIET_MAINTENANCE))
        self.assertEqual(suite.fixture_git.quiet_maintenance(dict(environ)), environ)

    def test_negative_existing_git_configuration_is_kept(self):
        environ = suite.fixture_git.quiet_maintenance(
            {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.name",
             "GIT_CONFIG_VALUE_0": "t"})
        self.assertEqual((environ["GIT_CONFIG_COUNT"], environ["GIT_CONFIG_KEY_0"],
                          environ["GIT_CONFIG_KEY_1"]), ("5", "user.name",
                                                         "maintenance.auto"))

    def test_positive_a_git_this_process_starts_sees_it(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        suite.git(Path(folder.name), "init", "-q")
        done = suite.fixture_git.run_git(["git", "config", "--get", "maintenance.auto"],
                                         folder.name)
        self.assertEqual(done.stdout.strip(), "false")


if __name__ == "__main__":
    unittest.main()
