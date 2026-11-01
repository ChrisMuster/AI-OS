#!/usr/bin/env python3
"""Tests for the intent check's start snapshot and changed paths (plan 10.6, "What a
changed path is", A4): ``runs/<run-id>/start-snapshot.json`` written once when the run
is created, before the first planner, never rewritten, and removed at a final end only;
``worktree.file_hashes``,
``existence_listing`` and ``changed_paths`` with their three exclusions and the
append-only LOG.md rule; ``runrecord``'s reader refusing a damaged snapshot.

Every git interaction is a temporary repository the fixtures build; no test starts
Docker, a model session or a provider process.

    python workflows/review-orchestration/tests/test_intent_snapshot.py
"""

import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_fixtures import *  # noqa: E402,F401,F403 - the loop suites' shared fixtures


class ChangedPathTests(LoopCase):
    """The rule on its own, over the fixture project."""

    def hashes(self):
        return worktree.file_hashes(self.root, self.h.cat_a)

    def changes(self, before, excluded=()):
        return worktree.changed_paths(before, self.hashes(), self.root, excluded)

    def test_positive_each_kind_of_change_is_a_changed_path(self):
        (self.root / "work" / "gone.txt").write_bytes(b"x\n")
        git(self.root, "add", "work/gone.txt")
        git(self.root, "commit", "-qm", "gone")
        before = self.hashes()
        (self.root / "work" / "a.txt").write_bytes(b"edited\n")        # tracked edit
        (self.root / "work" / "new.txt").write_bytes(b"new\n")         # untracked new
        (self.root / "work" / "gone.txt").unlink()                     # deletion
        (self.root / "memory" / "note.md").write_bytes(b"changed\n")   # Category A
        found = {c["path"]: c["what"] for c in self.changes(before)["changed"]}
        self.assertEqual(found, {"work/a.txt": "changed", "work/new.txt": "added",
                                 "work/gone.txt": "removed",
                                 "memory/note.md": "changed"})

    def test_positive_a_retargeted_link_is_a_change_hashed_by_its_text(self):
        target = self.root / "work" / "link"
        try:
            os.symlink("a.txt", target)
        except (OSError, NotImplementedError):
            self.skipTest("this file system cannot make a symbolic link")
        before = self.hashes()
        self.assertEqual(before["work/link"]["size"], len(worktree.LINK_PREFIX + b"a.txt"))
        # The file it points at changing does not change the link's hash.
        (self.root / "work" / "b.txt").write_bytes(b"b\n")
        target.unlink()
        os.symlink("b.txt", target)
        found = {c["path"] for c in self.changes(before)["changed"]}
        self.assertIn("work/link", found)

    def test_negative_the_runs_own_writes_and_ignored_files_are_never_changed_paths(self):
        log = self.root / "LOG.md"
        log.write_bytes(b"[t] | entry\n")
        packet_file = packet.packet_path(self.root, "demo")
        # Classified Category A here, as a memory file is, so only the exclusion keeps
        # the packet out.
        self.h.cat_a.add(packet_file.relative_to(self.root).as_posix())
        before = self.hashes()
        log.write_bytes(b"rewritten\n")
        packet_file.write_bytes(b"packet\n")
        (self.root / "work" / "out.generated").write_bytes(b"generated\n")
        (self.root / "runs").mkdir()
        (self.root / "runs" / "r.json").write_bytes(b"{}\n")
        record = self.root / worktree.RUNS_PREFIX / "x" / "state.json"
        record.parent.mkdir(parents=True)
        record.write_bytes(b"{}\n")
        result = self.changes(before, excluded=["runs", "LOG.md",
                                                packet_file.relative_to(self.root)
                                                .as_posix()])
        self.assertEqual(result, {"changed": [], "information": []})
        # Without the exclusions the same writes are changed paths.
        unexcluded = {c["path"] for c in self.changes(before)["changed"]}
        self.assertTrue({"LOG.md", "memory/demo_review_packet.md"} <= unexcluded)

    def test_positive_an_appended_log_elsewhere_is_information_not_a_change(self):
        log = self.root / "work" / "LOG.md"
        log.write_bytes(b"one\ntwo\n")
        before = self.hashes()
        log.write_bytes(b"one\ntwo\nthree\n")
        result = self.changes(before)
        self.assertEqual(result["changed"], [])
        self.assertEqual(result["information"],
                         [{"path": "work/LOG.md",
                           "what": "lines appended to an existing LOG.md"}])

    def test_rejection_an_inserted_or_rewritten_log_line_is_a_change(self):
        log = self.root / "work" / "LOG.md"
        for after in (b"zero\none\ntwo\n", b"one\nTWO\n", b"one\n"):
            with self.subTest(after=after):
                log.write_bytes(b"one\ntwo\n")
                before = self.hashes()
                log.write_bytes(after)
                result = self.changes(before)
                self.assertEqual([c["path"] for c in result["changed"]], ["work/LOG.md"])
                self.assertEqual(result["information"], [])

    def test_negative_a_new_log_is_a_change_not_information(self):
        before = self.hashes()
        (self.root / "work" / "LOG.md").write_bytes(b"first\n")
        self.assertEqual(self.changes(before)["changed"],
                         [{"path": "work/LOG.md", "what": "added"}])

    def test_positive_the_existence_listing_holds_ignored_paths_but_not_git(self):
        (self.root / "cache").mkdir()
        (self.root / ".gitignore").write_bytes(b"memory/\nruns/\n__pycache__/\n"
                                               b"*.generated\ncache/\n")
        (self.root / "cache" / "big.bin").write_bytes(b"x")
        listing = worktree.existence_listing(self.root)
        for path in ("cache", "cache/big.bin", "work", "work/a.txt", "memory/note.md"):
            self.assertIn(path, listing)
        self.assertFalse(any(p == ".git" or p.startswith(".git/") for p in listing))
        hashed = self.hashes()
        self.assertNotIn("cache/big.bin", hashed)
        self.assertIn("work/a.txt", hashed)


class SnapshotRecordTests(LoopCase):
    """The snapshot as a run writes it."""

    def planning_run(self):
        return loop.start("brief.md", "demo", str(self.personal), root=self.root,
                          deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                          settings=self.settings, builder="claude")

    def test_positive_written_at_creation_before_any_planner(self):
        seen = []
        deps = self.h.deps()
        real = deps.make_planner

        def planner(settings, provider, cwd, usage, frozen_dir):
            seen.append(any(self.runs.glob(f"*/{runrecord.START_SNAPSHOT_FILE}")))
            return real(settings, provider, cwd, usage, frozen_dir)
        deps.make_planner = planner
        run = loop.start("brief.md", "demo", str(self.personal), root=self.root,
                         deps=deps, log_path=self.log, runs_dir=self.runs,
                         settings=self.settings, builder="claude")
        snapshot = runrecord.read_start_snapshot(run.run_dir)
        self.assertIn("work/a.txt", snapshot["files"])
        self.assertIn("work", snapshot["existence"])
        with redirect_stderr(io.StringIO()):
            asyncio.run(planning.advance(run))
        self.assertEqual(seen, [True, True])

    def test_positive_never_rewritten_by_approval_or_amendment(self):
        run = self.planning_run()
        path = run.run_dir / runrecord.START_SNAPSHOT_FILE
        first = path.read_bytes()
        with redirect_stderr(io.StringIO()):
            asyncio.run(planning.advance(run))
        (self.root / "revised.md").write_bytes(BRIEF.replace("says done", "says ok")
                                               .encode("utf-8"))
        with redirect_stderr(io.StringIO()):
            loop.amend(run, "revised.md")
            asyncio.run(planning.advance(run))
            loop.approve(run)
        self.assertEqual(path.read_bytes(), first)
        with self.assertRaises(FileExistsError):
            runrecord.write_start_snapshot(run.run_dir, {"files": {}, "existence": []})

    def test_positive_kept_at_a_resumable_end_and_removed_at_a_final_one(self):
        for reason, kept in ((sr.USAGE_LIMIT, True), (sr.ERROR, True), (sr.MAX_ROUNDS, True),
                             (sr.CLEAN, False), (sr.INTENT_FAILED, False),
                             (sr.STOPPED_BY_USER, False)):
            with self.subTest(reason=reason):
                run = self.planning_run()
                path = run.run_dir / runrecord.START_SNAPSHOT_FILE
                self.assertTrue(path.is_file())
                run.end(reason, {})
                self.assertEqual(path.exists(), kept)
                removed = [c for c in run.state.get("cleanup", [])
                           if c["what"] == "the start snapshot"]
                self.assertEqual(removed, [] if kept else [{"what": "the start snapshot",
                                                            "removed": True}])

    def test_positive_stop_abandons_a_resumable_end_and_clears_its_working_data(self):
        for reason in sr.RESUMABLE:
            with self.subTest(reason=reason):
                run = self.planning_run()
                path = run.run_dir / runrecord.START_SNAPSHOT_FILE
                run.end(reason, {})
                self.assertTrue(path.is_file())
                with redirect_stderr(io.StringIO()) as said:
                    loop.stop(run, dry_run=True)
                self.assertIn(f"ended {reason}", said.getvalue())
                self.assertTrue(path.is_file(), "a dry run changes nothing")
                self.assertEqual(self.reason(run), reason)
                loop.stop(run)
                self.assertEqual(self.reason(run), sr.STOPPED_BY_USER)
                self.assertEqual(run.state["stop_reasons"][-1]["evidence"],
                                 {"abandoned": reason})
                self.assertFalse(path.exists())
                self.assertIn(("frozen", run.state["frozen_dir"]), self.h.removed)
                with self.assertRaises(loop.RunRefused):
                    loop.resume(run)

    def ended_at_a_limit(self):
        run = self.planning_run()
        run.end(sr.USAGE_LIMIT, {})
        return run, run.run_dir / runrecord.START_SNAPSHOT_FILE, run.run_dir / loop.LOCK_FILE

    def test_rejection_stop_refuses_while_another_command_holds_the_claim(self):
        run, snapshot, lock = self.ended_at_a_limit()
        lock.write_bytes(b"held\n")
        with self.assertRaises(loop.RunRefused) as caught:
            loop.stop(run)
        self.assertIn("exists", " ".join(caught.exception.args[0]))
        self.assertEqual(lock.read_bytes(), b"held\n", "the other command's claim stands")
        self.assertTrue(snapshot.is_file())
        self.assertEqual(runrecord.read_state(run.run_dir)["status"], sr.ENDED)

    def test_rejection_stop_refuses_a_run_a_resume_took_after_it_was_read(self):
        run, snapshot, lock = self.ended_at_a_limit()
        other = loop.load(run.state["run_id"], root=self.root, deps=self.h.deps(),
                          log_path=self.log, runs_dir=self.runs)
        sr.continue_run(other.state)
        other.save()
        with self.assertRaises(loop.RunRefused) as caught:
            loop.stop(run)
        self.assertIn("changed after this command read it",
                      " ".join(caught.exception.args[0]))
        self.assertTrue(snapshot.is_file())
        self.assertFalse(lock.exists())
        self.assertEqual(runrecord.read_state(run.run_dir)["status"], sr.RUNNING)

    def test_rejection_a_resume_that_read_the_run_before_a_stop_is_refused_after_it(self):
        run, snapshot, _lock = self.ended_at_a_limit()
        seen = loop._claim_mark(run.state)
        loop.stop(run)
        self.assertFalse(snapshot.exists())
        with self.assertRaises(loop.RunRefused):
            loop.claim(run, seen)
        self.assertEqual(runrecord.read_state(run.run_dir)["status"], sr.ENDED)

    def test_positive_stop_releases_its_claim_when_it_ends_and_when_it_fails(self):
        run, _snapshot, lock = self.ended_at_a_limit()
        with mock.patch.object(run, "end", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                loop.stop(run)
        self.assertFalse(lock.exists())
        paused = self.planning_run()
        paused.state["status"], paused.state["pause"] = sr.PAUSED, sr.AWAITING_PLAN_APPROVAL
        paused.save()
        loop.stop(paused)
        self.assertEqual(self.reason(paused), sr.STOPPED_BY_USER)
        self.assertFalse((paused.run_dir / loop.LOCK_FILE).exists())

    def test_negative_a_stop_dry_run_takes_no_claim(self):
        run, _snapshot, lock = self.ended_at_a_limit()
        lock.write_bytes(b"held\n")
        with redirect_stderr(io.StringIO()):
            loop.stop(run, dry_run=True)
        self.assertEqual(lock.read_bytes(), b"held\n")

    def test_rejection_stop_refuses_a_run_that_ended_for_good(self):
        for reason in (sr.CLEAN, sr.INTENT_FAILED, sr.STOPPED_BY_USER):
            with self.subTest(reason=reason):
                run = self.planning_run()
                run.end(reason, {})
                with self.assertRaises(loop.RunRefused) as caught:
                    loop.stop(run)
                self.assertIn("only a paused run", " ".join(caught.exception.args[0]))
                self.assertEqual(self.reason(run), reason)

    def test_rejection_a_failed_removal_is_reported_and_the_end_stands(self):
        run = self.planning_run()
        with mock.patch.object(Path, "unlink", side_effect=OSError("in use")):
            run.end(sr.STOPPED_BY_USER, {})
        self.assertEqual(self.reason(run), sr.STOPPED_BY_USER)
        self.assertIn({"what": "the start snapshot", "removed": False,
                       "error": "OSError: in use"}, run.state["cleanup"])
        self.assertTrue((run.run_dir / runrecord.START_SNAPSHOT_FILE).is_file())

    def test_rejection_a_corrupt_or_missing_snapshot_is_an_error(self):
        run = self.planning_run()
        path = run.run_dir / runrecord.START_SNAPSHOT_FILE
        for text in ("not json", "[]", '{"files": {}}',
                     '{"files": {"a": {"sha256": 1, "size": 2}}, "existence": []}',
                     '{"files": {"a": {"sha256": "x", "size": true}}, "existence": []}',
                     '{"files": {}, "existence": [3]}'):
            with self.subTest(text=text):
                path.write_text(text, encoding="utf-8", newline="\n")
                with self.assertRaises(runrecord.SnapshotError):
                    runrecord.read_start_snapshot(run.run_dir)
        path.unlink()
        with self.assertRaises(runrecord.SnapshotError):
            runrecord.read_start_snapshot(run.run_dir)

    def test_rejection_a_snapshot_that_cannot_be_written_refuses_the_start(self):
        with mock.patch.object(worktree, "start_snapshot",
                               side_effect=OSError("unreadable file")):
            with self.assertRaises(loop.RunRefused) as caught:
                self.planning_run()
        self.assertIn("unreadable file", " ".join(caught.exception.args[0]))
        self.assertEqual(list(self.runs.iterdir()) if self.runs.exists() else [], [])


if __name__ == "__main__":
    unittest.main()
