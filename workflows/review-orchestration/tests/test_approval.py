#!/usr/bin/env python3
"""Hermetic tests for A3 chunk 1, the approval and amendment plumbing (plan 10.3 steps
6 and 8, and the chunk list at the end of 10.3).

What is under test: ``loop.approve`` (the four read-only checks, then Docker and the
image, the run's own writes in the project, the scope rebuild and the write-once
``approval.json``, in that order, with the plan's two kinds of failure), ``loop.amend``,
both modes' dry runs, the readers that take the edit paths and the image tag from
``approval.json`` alone (the Codex hook, the Claude builder's approver and the check
helper), the start's interim approval sequence, and the two new modes of ``run.py``.

Every provider, Docker and image step is the loop suites' fake
(``loop_fixtures.Harness``), in a temporary project that is a real git repository with a
second repository standing in for the personal one. No test starts Docker, a model
session or a Codex app server. A run at the approval pause is made the way A3's planning
half will leave one: a run record and a frozen folder with no ``approval.json``, no
``started`` entry in the workflow log, and the state paused ``awaiting-plan-approval``.

Same three controls as the other suites:

    positive control  what the plan says works. It must work.
    rejection control what the plan says refuses. It must, with the reason named, and
                      change what the plan says it changes and nothing else.
    negative control  something that looks like such a case but is not.

    python workflows/review-orchestration/tests/test_approval.py
"""

import asyncio
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent

# The loop suites' harness, fixtures and loaded modules, used as they are, imported
# from the one helper module they all share (plan 10.3, the chunk list: the split of
# the old single loop file), never by loading a loop file.
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))
import loop_fixtures as suite  # noqa: E402

loop, sr, worktree, frozen = suite.loop, suite.sr, suite.worktree, suite.frozen
codex_rules, container, packet, runrecord = (suite.codex_rules, suite.container,
                                             suite.packet, suite.runrecord)
run_py = suite.run_py
codex_hook = suite._load("codex_hook")
check_server = suite._load("check_server")
SCRIPTS, CONFIG = suite.SCRIPTS, suite.WORKFLOW / "config"

REVISED = suite.BRIEF.replace("- work/a.txt says done.", "- work/a.txt says done twice.") \
    .replace('- python -c "print(1)"', '- python -c "print(2)"\n- python -c "print(3)"') \
    .replace("- work/ | Measure: git ls-files work", "- work/a.txt | Measure: git ls-files work/a.txt")


def brief_locked():
    """A stand-in for ``os.replace`` that refuses only the replacement of a run's
    ``brief.md``, as a Windows file lock would, and replaces everything else, the atomic
    ``state.json`` write included."""
    real = suite.os.replace

    def replace(src, dst):
        if Path(dst).name == "brief.md":
            raise PermissionError(13, "The file is in use", str(dst))
        return real(src, dst)
    return replace


def tree_bytes(folder):
    """Every file under a folder, by its path relative to it."""
    folder = Path(folder)
    return {p.relative_to(folder).as_posix(): p.read_bytes()
            for p in sorted(folder.rglob("*")) if p.is_file()}


class ApprovalCase(suite.LoopCase):

    def setUp(self):
        super().setUp()
        self.uncaptured = set()
        self.uncaptured_calls = []

    def deps(self):
        deps = self.h.deps()

        def uncaptured(root, git_dir):
            self.uncaptured_calls.append(git_dir)
            if isinstance(self.uncaptured, Exception):
                raise self.uncaptured
            return set(self.uncaptured)
        deps.uncaptured_category_a = uncaptured
        return deps

    def load(self, run_or_id):
        run_id = run_or_id if isinstance(run_or_id, str) else run_or_id.state["run_id"]
        return loop.load(run_id, root=self.root, deps=self.deps(), log_path=self.log,
                         runs_dir=self.runs)

    def paused(self):
        """A run at the approval pause, as the planning half will leave one."""
        run = loop.start("brief.md", "demo", str(self.personal), root=self.root,
                         deps=self.deps(), log_path=self.log, runs_dir=self.runs,
                         settings=self.settings, builder="claude")
        (Path(run.state["frozen_dir"]) / codex_rules.APPROVAL_FILE).unlink()
        self.log.unlink()
        run.state.update(status=sr.PAUSED, pause=sr.AWAITING_PLAN_APPROVAL)
        run.state["docker"] = {"started_by_run": False, "events": []}
        run.save()
        self.h.docker_calls.clear()
        self.h.image_calls.clear()
        self.h.builder_script = [suite.edit(self.root, "done\n")]
        self.h.reviews = [[]]
        return self.load(run)

    def approval_file(self, run):
        return Path(run.state["frozen_dir"]) / codex_rules.APPROVAL_FILE

    def refused(self, call):
        with redirect_stderr(io.StringIO()), self.assertRaises(loop.RunRefused) as caught:
            call()
        return " ".join(caught.exception.args[0])

    def unchanged(self, run, before_record, before_frozen):
        """Nothing in the run record, the frozen folder or the project's log changed,
        and the run is still approvable."""
        self.assertEqual(tree_bytes(run.run_dir), before_record)
        self.assertEqual(tree_bytes(run.state["frozen_dir"]), before_frozen)
        self.assertFalse(self.log.exists(), "no LOG.md entry")
        self.assertFalse(packet.packet_path(self.root, "demo").exists(), "no packet")
        state = runrecord.read_state(run.run_dir)
        self.assertEqual((state["status"], state["pause"]),
                         (sr.PAUSED, sr.AWAITING_PLAN_APPROVAL))
        self.assertNotIn("approval_failure", state)

    def snapshot(self, run):
        return tree_bytes(run.run_dir), tree_bytes(run.state["frozen_dir"])


# ---------------------------------------------------------------- the four checks


class ApprovalCheckTests(ApprovalCase):
    """Plan 10.3 step 6, "``--approve`` refuses if the project moved during the pause":
    each check refuses on its own, names itself, and changes nothing."""

    def test_positive_all_four_pass_and_the_run_builds(self):
        run = self.paused()
        loop.approve(run)
        self.assertEqual(self.uncaptured_calls, [str(self.personal)])
        self.assertEqual(run.state["status"], sr.RUNNING)
        asyncio.run(run.advance())
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        self.assertEqual(self.log_actions(), ["started", "completed"])

    def test_rejection_the_public_head_moved(self):
        run = self.paused()
        before = self.snapshot(run)
        (self.root / "other.txt").write_bytes(b"x\n")
        suite.git(self.root, "add", "other.txt")
        suite.git(self.root, "commit", "-qm", "moved")
        text = self.refused(lambda: loop.approve(run))
        self.assertIn("check 1 (the HEADs): the public HEAD", text)
        for other in ("check 2", "check 3", "check 4"):
            self.assertNotIn(other, text)
        self.assertIn("--stop", text)
        self.unchanged(run, *before)
        self.assertEqual(self.h.docker_calls, [])

    def test_rejection_the_personal_head_moved(self):
        run = self.paused()
        before = self.snapshot(run)
        (self.root / "memory" / "other.md").write_bytes(b"later\n")
        suite.git(self.root, "-c", "user.email=t@example.invalid", "-c", "user.name=t",
                  "add", "-f", "memory/other.md", git_dir=str(self.personal))
        suite.git(self.root, "-c", "user.email=t@example.invalid", "-c", "user.name=t",
                  "commit", "-qm", "captured later", git_dir=str(self.personal))
        text = self.refused(lambda: loop.approve(run))
        self.assertIn("check 1 (the HEADs): the personal HEAD", text)
        self.assertNotIn("the public HEAD", text)
        self.unchanged(run, *before)

    def test_rejection_an_uncommitted_change_under_the_paths(self):
        run = self.paused()
        before = self.snapshot(run)
        (self.root / "work" / "a.txt").write_bytes(b"edited during the pause\n")
        text = self.refused(lambda: loop.approve(run))
        self.assertIn("check 2 (the public repository)", text)
        self.assertIn("work/a.txt", text)
        for other in ("check 1", "check 3", "check 4"):
            self.assertNotIn(other, text)
        self.unchanged(run, *before)

    def test_negative_a_change_outside_the_paths_is_not_seen(self):
        run = self.paused()
        (self.root / "brief.md").write_bytes(b"changed outside the edit paths\n")
        loop.approve(run)
        self.assertEqual(run.state["status"], sr.RUNNING)

    def test_rejection_a_captured_personal_file_changed_under_the_paths(self):
        run = self.paused()
        (self.root / "memory" / "note.md").write_bytes(b"changed since the capture\n")
        problems = loop.approval_problems(run, ["memory/"])
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("check 3 (the personal repository)", problems[0])
        self.assertIn("memory/note.md", problems[0])
        # The real approval sees it the same way once the paths cover the file.
        self.assertEqual(loop.approval_problems(run, ["work/"]), [])

    def test_negative_an_untracked_personal_file_is_not_check_3s(self):
        # Every publicly tracked file is untracked in the personal repository, so check 3
        # leaves untracked files out; a new personal file is check 4's.
        run = self.paused()
        (self.root / "memory" / "new.md").write_bytes(b"new\n")
        self.assertEqual(loop.approval_problems(run, ["memory/", "work/"]), [])

    def test_rejection_an_uncaptured_category_a_file_under_the_paths(self):
        run = self.paused()
        before = self.snapshot(run)
        self.uncaptured = {"work/notes.md", "memory/elsewhere.md"}
        text = self.refused(lambda: loop.approve(run))
        self.assertIn("check 4 (Category A)", text)
        self.assertIn("work/notes.md", text)
        self.assertNotIn("memory/elsewhere.md", text)  # outside the paths
        for other in ("check 1", "check 2", "check 3"):
            self.assertNotIn(other, text)
        self.unchanged(run, *before)

    def test_positive_the_runs_own_record_is_left_out_by_name(self):
        # A folder edit path covering the run records covers this run's own record,
        # uncaptured Category A content the run wrote itself; it is left out by name,
        # and an uncaptured file in any other run's record still refuses.
        run = self.paused()
        own = run.run_dir.relative_to(self.root).as_posix()
        other = "runs/20261001-000000-abcd/brief.md"
        self.uncaptured = {f"{own}/brief.md", f"{own}/state.json"}
        self.assertEqual(loop.approval_problems(run, ["runs/", "work/"]), [])
        self.uncaptured = {f"{own}/brief.md", other}
        problem, = loop.approval_problems(run, ["runs/", "work/"])
        self.assertIn(other, problem)
        self.assertNotIn(f"{own}/brief.md", problem)

    def test_positive_paths_are_judged_as_the_check_after_every_turn_judges_them(self):
        # Without case, and a folder edit path covers everything below it, by the one
        # implementation (worktree.under_edit_paths) the turn check uses.
        run = self.paused()
        self.uncaptured = {"WORK/Deep/New.md"}
        problem, = loop.approval_problems(run, ["work/"])
        self.assertIn("WORK/Deep/New.md", problem)
        self.uncaptured = {"workshop/x.md"}  # a prefix of the name is not inside it
        self.assertEqual(loop.approval_problems(run, ["work/"]), [])
        for path, inside in (("work/a.txt", True), ("Work/a.txt", True),
                             ("workx/a.txt", False), ("work", True)):
            with self.subTest(path=path):
                self.assertEqual(worktree.under_edit_paths(path, ["work/"]), inside)
                before = {"files": {}, "git": {"public": {}, "personal": {}}}
                after = {"files": {path: "h"}, "git": {"public": {}, "personal": {}}}
                self.assertEqual(not worktree.compare_inventory(before, after, ["work/"],
                                                                run.run_dir, self.root),
                                 inside)

    def test_rejection_a_check_that_cannot_be_made_refuses(self):
        run = self.paused()
        self.uncaptured = worktree.GitError("git did not answer")
        text = self.refused(lambda: loop.approve(run))
        self.assertIn("check 4 (Category A): could not be made: git did not answer", text)

    def test_rejection_every_failed_check_is_named_together(self):
        run = self.paused()
        (self.root / "work" / "a.txt").write_bytes(b"edited\n")
        self.uncaptured = {"work/new.md"}
        text = self.refused(lambda: loop.approve(run))
        self.assertIn("check 2", text)
        self.assertIn("check 4", text)


class UncapturedSelectionTests(unittest.TestCase):
    """``worktree.uncaptured_category_a``: the shared selection with the personal
    repository's git dir, or a refusal, never an empty answer read as a pass."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.allowlist = Path(temp.name) / "allowlist.py"

    def write(self, specs, final):
        self.allowlist.write_text(
            "def read_specs():\n"
            f"    return {specs!r}\n"
            "def select(specs, git_dir=None, project=None):\n"
            f"    final = {final!r}\n"
            "    if final is None:\n"
            "        return {'final': None}\n"
            "    return {'final': [f'{path}|{git_dir}' for path in final]}\n",
            encoding="utf-8", newline="\n")

    def test_positive_the_personal_git_dir_is_passed(self):
        self.write(["memory/"], ["memory/new.md"])
        self.assertEqual(worktree.uncaptured_category_a(".", "personal.git",
                                                        allowlist=self.allowlist),
                         {"memory/new.md|personal.git"})

    def test_rejection_an_unanswered_selection_or_no_classification(self):
        self.write(["memory/"], None)
        with self.assertRaisesRegex(worktree.GitError, "did not answer"):
            worktree.uncaptured_category_a(".", "personal.git", allowlist=self.allowlist)
        self.write(None, ["x"])
        with self.assertRaisesRegex(worktree.GitError, "cannot be read"):
            worktree.uncaptured_category_a(".", "personal.git", allowlist=self.allowlist)
        with self.assertRaisesRegex(worktree.GitError, "missing"):
            worktree.uncaptured_category_a(".", "personal.git",
                                           allowlist=self.allowlist.parent / "none.py")


# ---------------------------------------------------------------- the order and failures


class ApprovalOrderTests(ApprovalCase):
    """Plan 10.3 step 6, "The order of approval's work", and its two kinds of failure."""

    def test_approval_discards_a_leftover_paid_permission(self):
        run = self.paused()
        run.state["paid_usage_pending"] = "build"
        run.save()
        loop.approve(run)
        saved = runrecord.read_state(run.run_dir)
        self.assertNotIn("paid_usage_pending", saved)
        self.assertEqual(self.h.probe_calls, [])

    def test_positive_the_five_steps_run_in_the_plans_order(self):
        run = self.paused()
        self.h.docker_up = False
        order = []
        deps = run.deps
        for name, label in (("uncaptured_category_a", "checks"),
                            ("ensure_docker", "docker"), ("prepare_image", "image")):
            def wrapped(*args, _real=getattr(deps, name), _label=label, **kwargs):
                order.append(_label)
                return _real(*args, **kwargs)
            setattr(deps, name, wrapped)
        real_log, real_packet = loop._log, loop.Run.write_packet
        real_record, real_approval = worktree.record_start, frozen.write_approval
        real_continue = sr.continue_run

        def log(action, note, path):
            order.append(f"log {action}")
            return real_log(action, note, path)

        def write_packet(self_):
            order.append("packet")
            return real_packet(self_)

        def record_start(*args, **kwargs):
            order.append("scope rebuild")
            return real_record(*args, **kwargs)

        def write_approval(*args, **kwargs):
            order.append("approval.json")
            return real_approval(*args, **kwargs)

        def continue_run(state):
            order.append("running")
            return real_continue(state)
        with mock.patch.object(loop, "_log", log), \
                mock.patch.object(loop.Run, "write_packet", write_packet), \
                mock.patch.object(worktree, "record_start", record_start), \
                mock.patch.object(frozen, "write_approval", write_approval), \
                mock.patch.object(sr, "continue_run", continue_run):
            loop.approve(run)
        self.assertEqual(order, ["checks", "docker", "image", "log started", "packet",
                                 "scope rebuild", "approval.json", "running"])
        # What each step left: Docker started by the approval, recorded for the run's
        # end to stop; the packet written; approval.json; the run running.
        self.assertEqual(self.h.docker_calls[:2], ["status", "start"])
        self.assertTrue(run.state["docker"]["started_by_run"])
        self.assertTrue(packet.packet_path(self.root, "demo").is_file())
        self.assertEqual(codex_rules.read_approval(run.state["frozen_dir"]),
                         {"edit_paths": ["work/"], "image_tag": self.h.image_tag})
        saved = runrecord.read_state(run.run_dir)
        self.assertEqual((saved["status"], saved["pause"]), (sr.RUNNING, None))
        self.assertEqual(saved["approval"]["edit_paths"], ["work/"])
        self.assertTrue((run.run_dir / "image" / container.CONSTRAINTS_NAME).is_file())

    def test_positive_the_runs_writes_are_recorded_as_the_orchestrators(self):
        # The workflow LOG.md under the edit paths: its started line is the
        # orchestrator's, in check_writes, and the rebuilt snapshot holds it, so it is
        # never counted as the builder's change.
        self.log = self.root / "work" / "LOG.md"
        run = self.paused()
        loop.approve(run)
        self.assertEqual(run.state["check_writes"], ["approval/check-writes.patch"])
        written = (run.run_dir / "approval" / "check-writes.patch").read_text(
            encoding="utf-8")
        self.assertIn("approved; its build starts.", written)
        self.assertIn("work/LOG.md", run.state["start"]["snapshot"])
        asyncio.run(run.advance())
        self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
        prompt = self.h.review_calls[0][0]
        changed = prompt.split("THE CHANGED FILES", 1)[1].split("FILES THE ORCHESTRATOR")[0]
        self.assertIn("work/a.txt", changed)
        self.assertNotIn("work/LOG.md", changed)
        self.assertIn("approved; its build starts.",
                      prompt.split("FILES THE ORCHESTRATOR", 1)[1])

    def test_positive_both_writes_are_recorded_when_the_paths_cover_neither(self):
        # Review finding R1-1: with ordinary edit paths (work/), neither the workflow
        # LOG.md nor the packet in memory/ is under them, and both writes are still
        # recorded in check_writes as the orchestrator's.
        run = self.paused()
        self.assertEqual(run.state["edit_paths"], ["work/"])
        loop.approve(run)
        self.assertEqual(run.state["check_writes"], ["approval/check-writes.patch"])
        written = (run.run_dir / "approval" / "check-writes.patch").read_text(
            encoding="utf-8")
        self.assertIn("LOG.md (after the approval)", written)
        self.assertIn("approved; its build starts.", written)
        self.assertIn("memory/demo_review_packet.md (after the approval)", written)
        self.assertNotIn("work/", written)
        saved = runrecord.read_state(run.run_dir)
        self.assertEqual(saved["check_writes"], ["approval/check-writes.patch"])

    def test_positive_an_approved_runs_report_says_the_image_was_prepared_at_approval(self):
        # Code review R3-2: the report names where the image step really ran.
        run = self.paused()
        loop.approve(run)
        asyncio.run(run.advance())
        report = (run.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn(f"- image {self.h.image_tag}: reused at approval", report)
        self.assertNotIn("at the start", report)

    def test_positive_approval_after_a_planning_step_starts_the_build(self):
        # Code review R4-1: the ordinary pause comes after planning, so the saved step
        # is a planning step, not preflight. Approval sets the build's entry step and
        # keeps the planning step in its record.
        for planning_step in ("plan-review-R1", "synthesis"):
            with self.subTest(planning_step=planning_step):
                self.setUp()
                run = self.paused()
                run.state["step"] = planning_step
                run.save()
                run = self.load(run)
                loop.approve(run)
                saved = runrecord.read_state(run.run_dir)
                self.assertEqual(saved["step"], loop.APPROVED_STEP)
                self.assertEqual(saved["approval"]["planning_step"], planning_step)
                self.assertEqual(self.load(run).next_step(), "build")
                asyncio.run(run.advance())
                self.assertEqual(self.reason(run), sr.REVIEWED_CLEAN)
                self.assertEqual([s["step"] for s in run.state["steps"]],
                                 ["build", "review-R1"])
                self.assertEqual(len(self.h.builders), 1)

    def test_positive_a_build_cut_off_after_approval_resumes_at_the_build(self):
        run = self.paused()
        run.state["step"] = "plan-review-R2"
        run.save()
        run = self.load(run)
        loop.approve(run)
        self.h.builder_script = [lambda prompt: (_ for _ in ()).throw(
            RuntimeError("the session dropped"))]
        asyncio.run(run.advance())
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertEqual(run.state["step"], loop.APPROVED_STEP)
        self.h.builder_script = [suite.edit(self.root, "done\n")]
        self.h.reviews = [[]]
        again = self.resume(run)
        self.assertEqual(self.reason(again), sr.REVIEWED_CLEAN)
        self.assertEqual([s["step"] for s in again.state["steps"]], ["build", "review-R1"])

    def test_positive_the_scope_is_rebuilt_from_the_approved_brief(self):
        run = self.paused()
        (self.root / "revised.md").write_bytes(REVISED.encode("utf-8"))
        loop.amend(run, "revised.md")
        recorded = dict(run.state["start"])
        loop.approve(run)
        self.assertEqual(run.state["edit_paths"], ["work/a.txt"])
        self.assertEqual(run.state["acceptance"], ['python -c "print(2)"',
                                                   'python -c "print(3)"'])
        self.assertEqual({k: run.state["start"][k] for k in ("public_head", "personal_head")},
                         {k: recorded[k] for k in ("public_head", "personal_head")})
        self.assertEqual(set(run.state["start"]["snapshot"]), {"work/a.txt"})
        heads = json.loads((run.run_dir / "start" / "heads.json").read_text(encoding="utf-8"))
        self.assertEqual(heads, {k: recorded[k] for k in ("public_head", "personal_head")})
        self.assertEqual(codex_rules.read_approval(run.state["frozen_dir"])["edit_paths"],
                         ["work/a.txt"])

    def test_rejection_a_docker_failure_writes_nothing_and_a_retry_succeeds(self):
        run = self.paused()
        before = self.snapshot(run)
        self.h.docker_up = False
        self.h.docker_start_error = container.ContainerError("no engine")
        text = self.refused(lambda: loop.approve(run))
        self.assertIn("Docker: no engine", text)
        self.unchanged(run, *before)
        self.h.docker_start_error = None
        again = self.load(run)
        loop.approve(again)
        self.assertEqual(again.state["status"], sr.RUNNING)

    def test_rejection_an_image_failure_stops_the_docker_it_started(self):
        run = self.paused()
        before = self.snapshot(run)
        self.h.docker_up = False
        self.h.image_error = container.ContainerError("the build failed")
        text = self.refused(lambda: loop.approve(run))
        self.assertIn("the check image: the build failed", text)
        self.assertIn("--approve can be run again", text)
        self.assertEqual(self.h.docker_calls[-1], "stop")
        self.assertFalse(self.h.docker_up)
        self.unchanged(run, *before)
        self.h.image_error = None
        loop.approve(self.load(run))

    def test_rejection_a_failure_after_the_first_project_write_refuses_for_good(self):
        run = self.paused()
        self.h.docker_up = False
        recorded = json.loads(json.dumps(run.state))

        def broken(self_):
            raise OSError("disk full")
        with mock.patch.object(loop.Run, "write_packet", broken):
            text = self.refused(lambda: loop.approve(run))
        self.assertIn("step 3: OSError: disk full", text)
        self.assertIn("cannot be approved again", text)
        self.assertIn("--stop", text)
        saved = runrecord.read_state(run.run_dir)
        self.assertEqual(saved["approval_failure"]["step"], 3)
        self.assertEqual((saved["status"], saved["pause"]),
                         (sr.PAUSED, sr.AWAITING_PLAN_APPROVAL))
        self.assertEqual(saved["edit_paths"], recorded["edit_paths"])
        self.assertEqual(saved["start"], recorded["start"])
        self.assertFalse(self.approval_file(run).exists())
        self.assertEqual(self.log_actions(), ["started", "failed"])
        self.assertEqual(self.h.docker_calls[-1], "stop", "the approval's Docker stopped")
        # Nothing is excepted to let a retry through: approve and amend both refuse,
        # and stop still ends the run.
        again = self.load(run)
        text = self.refused(lambda: loop.approve(again))
        self.assertIn("cannot be approved", text)
        (self.root / "revised.md").write_bytes(REVISED.encode("utf-8"))
        self.assertIn("cannot be amended", self.refused(lambda: loop.amend(again,
                                                                           "revised.md")))
        loop.stop(again)
        self.assertEqual(self.reason(again), sr.STOPPED_BY_USER)

    def test_rejection_a_failure_at_approval_json_refuses_for_good(self):
        run = self.paused()

        def refuse(*args, **kwargs):
            raise frozen.FrozenError("approval.json could not be written: denied")
        with mock.patch.object(frozen, "write_approval", refuse):
            text = self.refused(lambda: loop.approve(run))
        self.assertIn("step 5", text)
        saved = runrecord.read_state(run.run_dir)
        self.assertEqual(saved["approval_failure"]["step"], 5)
        self.assertEqual(saved["status"], sr.PAUSED)
        self.assertIn("cannot be approved", self.refused(lambda: loop.approve(self.load(run))))

    def test_rejection_an_approval_json_that_exists_refuses(self):
        run = self.paused()
        frozen.write_approval(run.state["frozen_dir"], ["work/"], "x:y")
        before = self.snapshot(run)
        text = self.refused(lambda: loop.approve(run))
        self.assertIn("approval.json already exists", text)
        self.assertEqual(self.snapshot(run), before)
        self.assertEqual(self.h.docker_calls, [])
        self.assertEqual(self.uncaptured_calls, [], "refused before the checks")

    def test_positive_approval_takes_a_fresh_credit_snapshot_before_docker(self):
        # The builder's choice: as a resume does, so the build's before-step check
        # compares against approval's snapshot, not a start's that may be days old.
        run = self.paused()
        topped_up = {"hasCredits": True, "unlimited": False, "balance": "9.00"}
        self.h.readings = [suite.reading(credits=topped_up)]
        loop.approve(run)
        self.assertEqual(run.state["credits"], topped_up)

    def test_rejection_untrackable_credits_refuse_before_docker(self):
        run = self.paused()
        before = self.snapshot(run)
        self.h.readings = [suite.reading(credits=None)]
        text = self.refused(lambda: loop.approve(run))
        self.assertIn("Codex credits cannot be tracked", text)
        self.assertEqual(self.h.docker_calls, [])
        self.unchanged(run, *before)

    def test_rejection_only_a_run_paused_for_plan_approval_is_approved(self):
        run = self.paused()
        for status, pause in ((sr.PAUSED, sr.AWAITING_USER), (sr.RUNNING, None)):
            with self.subTest(status=status, pause=pause):
                run.state.update(status=status, pause=pause)
                text = self.refused(lambda: loop.approve(run))
                self.assertIn(f"only a run paused {sr.AWAITING_PLAN_APPROVAL}", text)
        self.assertFalse(self.approval_file(run).exists())


# ---------------------------------------------------------------- approval.json's readers


class ApprovalReaderTests(ApprovalCase):
    """Plan 10.3 step 6: approval.json is the one source of the edit paths and the image
    tag, and each reader refuses every call without it."""

    READ = ("Read", {"file_path": "work/a.txt"})
    EDIT = ("Edit", {"file_path": "work/a.txt", "old_string": "a", "new_string": "b"})

    def test_rejection_the_claude_approver_refuses_every_call_without_it(self):
        run = self.paused()
        for tool, tool_input in (self.READ, self.EDIT):
            with self.subTest(tool=tool):
                decision = run.approver.decide(tool, tool_input)
                self.assertFalse(decision.allowed)
                self.assertIn("approval record cannot be used", decision.reason)
        self.assertEqual(len(run.approver.refusals), 2)

    def test_positive_after_approval_the_approver_reads_approval_json_not_state(self):
        run = self.paused()
        self.assertFalse(run.approver.decide(*self.READ).allowed)
        loop.approve(run)
        self.assertTrue(run.approver.decide(*self.READ).allowed)
        self.assertTrue(run.approver.decide(*self.EDIT).allowed)
        # A wider scope written into state.json alone gives no wider approver.
        run.state["edit_paths"] = ["work/", "memory/"]
        fresh = self.load(run)
        fresh.state["edit_paths"] = ["work/", "memory/"]
        self.assertEqual(fresh.approver.edit_paths, ["work/"])
        self.assertFalse(fresh.approver.decide("Write", {"file_path": "memory/x.md",
                                                         "content": "x"}).allowed)

    def test_rejection_an_approver_already_made_refuses_once_the_file_goes(self):
        # Code review R2-1: the approver reads approval.json before every decision, so
        # an allowed call earlier is no licence for a later one once the file is gone,
        # unreadable, or names other edit paths.
        run = self.paused()
        loop.approve(run)
        approver_ = run.approver
        self.assertTrue(approver_.decide(*self.READ).allowed)
        path = self.approval_file(run)
        original = path.read_bytes()
        for label, change in (
                ("removed", lambda: None),
                ("not JSON", lambda: path.write_bytes(b"{")),
                ("other edit paths", lambda: path.write_text(json.dumps(
                    {"edit_paths": ["work/", "memory/"], "image_tag": "t"}),
                    encoding="utf-8", newline="\n"))):
            with self.subTest(label):
                path.unlink(missing_ok=True)
                change()
                for tool, tool_input in (self.READ, self.EDIT):
                    decision = approver_.decide(tool, tool_input)
                    self.assertFalse(decision.allowed)
                    self.assertIn("approval record", decision.reason)
                path.unlink(missing_ok=True)
                path.write_bytes(original)
                self.assertTrue(approver_.decide(*self.READ).allowed)

    def test_rejection_the_codex_hook_refuses_without_it(self):
        folder = self.populated()
        event = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git status --short"},
                            "tool_use_id": "c1", "session_id": "s1"})
        _event, allowed, reason, _detail = codex_hook.judge(
            [folder.name], event, folder, rule_hook=lambda *a, **k: None)
        self.assertFalse(allowed)
        self.assertIn("approval record cannot be used", reason)
        frozen.write_approval(folder, ["work/"], "bookdragon-checks:0123456789ab")
        _event, allowed, reason, _detail = codex_hook.judge(
            [folder.name], event, folder, rule_hook=lambda *a, **k: None)
        self.assertTrue(allowed, reason)

    def test_rejection_the_check_helper_refuses_without_it(self):
        folder = self.populated()
        command = "python -m py_compile work/a.py"
        reply = check_server.run_check(command, folder)
        self.assertTrue(reply.startswith("Refused by the orchestrator:"), reply)
        self.assertIn("approval record cannot be used", reply)
        self.assertIn("approval record cannot be used", check_server.get_timestamp(folder))

    def test_rejection_a_run_with_no_approval_json_ends_error_at_its_review(self):
        # The round's checks take the image tag from approval.json alone.
        run = loop.start("brief.md", "demo", str(self.personal), root=self.root,
                         deps=self.deps(), log_path=self.log, runs_dir=self.runs,
                         settings=self.settings, builder="claude")
        self.h.builder_script = [suite.edit(self.root, "done\n")]
        self.h.reviews = [[]]
        run.state["image"]["tag"] = "stale-tag-from-state"
        asyncio.run(run.advance())
        self.assertEqual(self.h.check_calls[0]["image"], self.h.image_tag)
        self.reset_work()
        run = loop.start("brief.md", "other", str(self.personal), root=self.root,
                         deps=self.deps(), log_path=self.log, runs_dir=self.runs,
                         settings=self.settings, builder="claude")
        self.approval_file(run).unlink()
        run._approver = None
        self.h.builder_script = [suite.edit(self.root, "done\n")]
        self.h.reviews = [[]]
        asyncio.run(run.advance())
        self.assertEqual(self.reason(run), sr.ERROR)
        self.assertIn("approval.json is missing",
                      json.dumps(run.state["stop_reasons"][-1]["evidence"]))

    def populated(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        folder = Path(temp.name) / "20261015-120000-ab12"
        folder.mkdir()
        frozen.populate(folder, SCRIPTS, CONFIG,
                        frozen.hook_config(folder.name, self.root, "claude", "0" * 40))
        return folder


# ---------------------------------------------------------------- amendment


class AmendTests(ApprovalCase):
    """Plan 10.3 step 6, ``--amend``: the brief replaced once the check passes, every
    open plan-review finding moved to Addressed, and nothing else changed."""

    def with_findings(self, run):
        run.ledger.add_round(1, [], [suite.finding(), suite.finding(title="Another"),
                                     suite.finding(title="Third")])
        run.ledger.apply_actions({"R1-3": (sr.FIXED, "done in the plan")})
        run.save()
        return self.load(run)

    def test_positive_the_brief_is_replaced_and_open_findings_superseded(self):
        run = self.with_findings(self.paused())
        (self.root / "revised.md").write_bytes(REVISED.encode("utf-8"))
        recorded = {key: json.loads(json.dumps(run.state[key]))
                    for key in ("edit_paths", "acceptance", "start")}
        project = {p: p.read_bytes() for p in (self.root / "work").rglob("*")}
        superseded = loop.amend(run, "revised.md")
        self.assertEqual(superseded, ["R1-1", "R1-2"])
        self.assertEqual((run.run_dir / "brief.md").read_bytes(), REVISED.encode("utf-8"))
        saved = runrecord.read_state(run.run_dir)
        ledger = {f["label"]: f for f in saved["ledger"]}
        for label in ("R1-1", "R1-2"):
            self.assertEqual((ledger[label]["action"], ledger[label]["note"]),
                             (sr.FIXED, "superseded by amendment"))
            self.assertTrue(packet.is_addressed(ledger[label]))
        self.assertEqual(ledger["R1-3"]["note"], "done in the plan")
        for key, value in recorded.items():
            self.assertEqual(saved[key], value, key)
        self.assertEqual((saved["status"], saved["pause"]),
                         (sr.PAUSED, sr.AWAITING_PLAN_APPROVAL))
        self.assertFalse(self.log.exists(), "the planning half writes no LOG.md entry")
        self.assertFalse(packet.packet_path(self.root, "demo").exists())
        self.assertFalse(self.approval_file(run).exists())
        self.assertEqual({p: p.read_bytes() for p in (self.root / "work").rglob("*")},
                         project)
        self.assertFalse((run.run_dir / "brief.md.amend").exists())

    def test_positive_a_rendered_plan_review_packet_is_rendered_again(self):
        run = self.with_findings(self.paused())
        rendered = run.run_dir / "plan-review" / "packet.md"
        rendered.parent.mkdir()
        rendered.write_text("stale\n", encoding="utf-8", newline="\n")
        (self.root / "revised.md").write_bytes(REVISED.encode("utf-8"))
        loop.amend(run, "revised.md")
        text = rendered.read_text(encoding="utf-8")
        self.assertIn("## Open\n\nNone.", text)
        self.assertIn("superseded by amendment", text)

    def test_rejection_a_revised_brief_that_fails_the_check_changes_nothing(self):
        run = self.with_findings(self.paused())
        (self.root / "revised.md").write_bytes(b"## Goal\n- half a brief\n")
        before = self.snapshot(run)
        text = self.refused(lambda: loop.amend(run, "revised.md"))
        self.assertIn("does not pass the check", text)
        self.assertIn("nothing was changed", text)
        self.assertEqual(self.snapshot(run), before)
        self.assertIn("does not exist", self.refused(lambda: loop.amend(run, "absent.md")))
        self.assertEqual(self.snapshot(run), before)

    def test_rejection_a_failed_brief_replacement_puts_the_ledger_back(self):
        # Code review R2-2: the ledger and state are saved before brief.md is replaced;
        # a replacement that fails restores both, so no finding is left superseded under
        # the old brief and the run is still approvable.
        run = self.with_findings(self.paused())
        (self.root / "revised.md").write_bytes(REVISED.encode("utf-8"))
        before_state = (run.run_dir / "state.json").read_bytes()
        before_brief = (run.run_dir / "brief.md").read_bytes()
        with mock.patch.object(loop.os, "replace", brief_locked()):
            text = self.refused(lambda: loop.amend(run, "revised.md"))
        self.assertIn("The file is in use", text)
        self.assertIn("put back", text)
        self.assertEqual((run.run_dir / "brief.md").read_bytes(), before_brief)
        self.assertEqual(json.loads((run.run_dir / "state.json").read_bytes()),
                         json.loads(before_state))
        self.assertEqual(run.ledger.open_labels(), ["R1-1", "R1-2"])
        self.assertFalse((run.run_dir / "brief.md.amend").exists())
        self.assertNotIn("approval_failure", runrecord.read_state(run.run_dir))
        loop.amend(self.load(run), "revised.md")  # a retry works

    def test_rejection_a_ledger_that_cannot_be_put_back_blocks_approval(self):
        run = self.with_findings(self.paused())
        (self.root / "revised.md").write_bytes(REVISED.encode("utf-8"))
        real_save, calls = loop.Run.save, []

        def save(self_):
            calls.append(1)
            if len(calls) > 1:
                raise OSError("disk full")
            return real_save(self_)
        with mock.patch.object(loop.os, "replace", brief_locked()), \
                mock.patch.object(loop.Run, "save", save):
            text = self.refused(lambda: loop.amend(run, "revised.md"))
        self.assertIn("could not be put back", text)
        self.assertIn("--stop", text)
        self.assertIn("cannot be approved", self.refused(lambda: loop.approve(self.load(run))))

    def test_rejection_an_approved_run_cannot_be_amended(self):
        run = self.paused()
        loop.approve(run)
        (self.root / "revised.md").write_bytes(REVISED.encode("utf-8"))
        text = self.refused(lambda: loop.amend(run, "revised.md"))
        self.assertIn(f"only a run paused {sr.AWAITING_PLAN_APPROVAL}", text)


# ---------------------------------------------------------------- dry runs


class DryRunTests(ApprovalCase):
    """Plan 10.3 step 8: both modes take --dry-run, which starts no Docker, writes
    nothing in the project or the run record, and leaves the run's state alone."""

    def test_positive_approve_dry_run_makes_the_checks_and_changes_nothing(self):
        run = self.paused()
        before = self.snapshot(run)
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertIsNone(loop.approve(run, dry_run=True))
        self.assertIn("[DRY RUN] the four approval checks passed", err.getvalue())
        self.assertEqual(self.uncaptured_calls, [str(self.personal)])
        self.assertEqual((self.h.docker_calls, self.h.image_calls), ([], []))
        self.unchanged(run, *before)

    def test_rejection_approve_dry_run_still_refuses_what_approve_refuses(self):
        run = self.paused()
        (self.root / "work" / "a.txt").write_bytes(b"edited\n")
        self.assertIn("check 2", self.refused(lambda: loop.approve(run, dry_run=True)))

    def test_positive_amend_dry_run_checks_and_changes_nothing(self):
        run = self.paused()
        run.ledger.add_round(1, [], [suite.finding()])
        run.save()
        run = self.load(run)
        (self.root / "revised.md").write_bytes(REVISED.encode("utf-8"))
        before = self.snapshot(run)
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertIsNone(loop.amend(run, "revised.md", dry_run=True))
        self.assertIn("would replace", err.getvalue())
        self.assertIn("R1-1", err.getvalue())
        self.unchanged(run, *before)
        self.assertEqual(self.h.docker_calls, [])

    def test_rejection_amend_dry_run_still_refuses_a_failing_brief(self):
        run = self.paused()
        (self.root / "revised.md").write_bytes(b"not a brief\n")
        self.assertIn("does not pass", self.refused(
            lambda: loop.amend(run, "revised.md", dry_run=True)))


# ---------------------------------------------------------------- run.py's two modes


class CommandLineTests(ApprovalCase):

    def call(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = run_py.main(list(argv), root=self.root, log_path=self.log,
                               deps=self.deps(), runs_dir=self.runs)
        return code, out.getvalue()

    def test_positive_approve_builds_and_exits_as_a_run_does(self):
        run = self.paused()
        code, out = self.call("--approve", run.state["run_id"])
        self.assertEqual(code, 0, out)
        self.assertIn("ended reviewed-clean", out)

    def test_positive_the_dry_runs_exit_0(self):
        run = self.paused()
        (self.root / "revised.md").write_bytes(REVISED.encode("utf-8"))
        before = self.snapshot(run)
        self.assertEqual(self.call("--approve", run.state["run_id"], "--dry-run")[0], 0)
        self.assertEqual(self.call("--amend", run.state["run_id"], "revised.md",
                                   "--dry-run")[0], 0)
        self.unchanged(run, *before)

    def test_positive_amend_says_the_run_stays_paused(self):
        run = self.paused()
        (self.root / "revised.md").write_bytes(REVISED.encode("utf-8"))
        code, out = self.call("--amend", run.state["run_id"], "revised.md")
        self.assertEqual(code, 0, out)
        self.assertIn("amended", out)
        self.assertIn(f"--approve {run.state['run_id']}", out)

    def test_rejection_a_refused_approval_exits_3(self):
        run = self.paused()
        (self.root / "work" / "a.txt").write_bytes(b"edited\n")
        code, out = self.call("--approve", run.state["run_id"])
        self.assertEqual(code, run_py.REFUSED)
        self.assertIn("REFUSED: check 2", out)

    def test_rejection_both_modes_are_the_starters(self):
        self.assertIn("--approve", run_py.STARTER_MODES)
        self.assertIn("--amend", run_py.STARTER_MODES)
        for argv in (["--approve", "20261015-120000-ab12"],
                     ["--amend", "20261015-120000-ab12", "revised.md"]):
            with self.subTest(argv=argv):
                message = run_py.needs_starter(argv, environ={}, frozen=None)
                self.assertIn("start.py " + " ".join(argv), message)

    def test_rejection_a_trusted_copy_acts_only_on_its_own_run(self):
        for argv in (["--approve", "20261015-120000-ab12"],
                     ["--amend", "20261015-120000-ab12", "revised.md"]):
            with self.subTest(argv=argv), redirect_stdout(io.StringIO()):
                code = run_py.main(argv, root=self.root, log_path=self.log,
                                   deps=self.deps(), runs_dir=self.runs,
                                   frozen=Path("x") / "20261015-120000-ffff")
                self.assertEqual(code, run_py.REFUSED)

    def test_negative_the_pause_message_names_approve_and_amend(self):
        text = loop.pause_commands("20261015-120000-ab12", sr.AWAITING_PLAN_APPROVAL)
        self.assertIn("--approve 20261015-120000-ab12", text)
        self.assertIn("--amend 20261015-120000-ab12 <revised-brief>", text)
        self.assertIn("--resume", loop.pause_commands("20261015-120000-ab12"))


# ---------------------------------------------------------------- the interim start


class InterimStartTests(ApprovalCase):
    """The chunk 1 paragraph at the end of plan 10.3: until chunk 3 joins the planning
    half, a start does approval's sequence itself straight after its host start check,
    Docker and the image, then approval.json from the brief, with no pause and no
    checks."""

    def test_positive_a_start_writes_approval_json_and_runs_on(self):
        self.h.on_frozen = suite.make_trusted
        run = loop.start("brief.md", "demo", str(self.personal), root=self.root,
                         deps=self.deps(), log_path=self.log, runs_dir=self.runs,
                         settings=self.settings, builder="claude")
        folder = Path(run.state["frozen_dir"])
        self.assertEqual(codex_rules.read_approval(folder),
                         {"edit_paths": ["work/"], "image_tag": self.h.image_tag})
        hook = json.loads((folder / "hook.json").read_text(encoding="utf-8"))
        self.assertNotIn("edit_paths", hook)
        self.assertNotIn("image_tag", hook)
        self.assertEqual((run.state["status"], run.state["pause"]), (sr.RUNNING, None))
        self.assertEqual(self.uncaptured_calls, [], "no approval checks at the start")
        self.assertEqual(run.approver.edit_paths, ["work/"])
        # Docker and the image came first, as today.
        self.assertEqual(len(self.h.image_calls), 1)

    def test_rejection_an_approval_json_that_cannot_be_written_refuses_the_start(self):
        def refuse(*args, **kwargs):
            raise frozen.FrozenError("approval.json could not be written: denied")
        with mock.patch.object(frozen, "write_approval", refuse), \
                self.assertRaises(loop.RunRefused) as caught:
            loop.start("brief.md", "demo", str(self.personal), root=self.root,
                       deps=self.deps(), log_path=self.log, runs_dir=self.runs,
                       settings=self.settings, builder="claude")
        self.assertIn("could not be written: denied", " ".join(caught.exception.args[0]))
        self.assertFalse(self.runs.exists(), "no run record")
        self.assertFalse(self.log.exists(), "no started entry")
        frozen_parent = self.root.parent / "frozen"
        self.assertEqual(list(frozen_parent.iterdir()) if frozen_parent.exists() else [],
                         [], "the frozen folder is removed")


if __name__ == "__main__":
    unittest.main()
