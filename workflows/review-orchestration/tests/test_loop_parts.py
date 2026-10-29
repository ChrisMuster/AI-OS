#!/usr/bin/env python3
"""Hermetic tests for the parts of the build-and-review loop, part of the split of the
old single loop file (plan 10.3, the chunk list): the price alarm (plan 10.10 item 6),
the run record's escaping, the suites' fixture helper, the review packet (packet.py),
the brief reader (brief.read_brief), the working-tree records (worktree.py), the
mechanical checks (checks.py) and the prompts.

The shared fixtures (``Harness``, the fake sessions, ``LoopCase``) are in
``loop_fixtures.py``. No test makes a model call. Same three controls as the other
suites: a positive control must work, a rejection control must end or refuse with the
reason named, a negative control must not.

    python workflows/review-orchestration/tests/test_loop_parts.py
"""

import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_fixtures import *  # noqa: E402,F401,F403 - the loop suites' shared fixtures


class PriceAlarmTests(LoopCase):

    def test_positive_a_step_over_twice_the_median_is_flagged(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        first = self.start(item="one")
        steps = first.state["steps"]
        # An earlier builder step far cheaper than 110 (A3 chunk 1: the alarm compares
        # uncached_tokens, plan 10.10 item 6).
        steps[0].update(tokens=10, uncached_tokens=10)
        runrecord.write_state(first.run_dir, first.state)
        self.reset_work()
        self.h.builder_script = [edit(self.root, "y\n")]
        self.h.reviews = [[]]
        second = self.start(item="two")
        flags = loop.price_alarm(second)
        self.assertEqual([(f["step"], f["role"]) for f in flags], [("build", "builder")])

    def test_negative_a_run_that_started_later_is_not_counted(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        other = self.start(item="one")
        other.state["steps"][0].update(tokens=10, uncached_tokens=10)
        other.state["started_ns"] = 2 ** 62  # started after, whatever its ID
        runrecord.write_state(other.run_dir, other.state)
        self.reset_work()
        self.h.builder_script = [edit(self.root, "y\n")]
        self.h.reviews = [[]]
        self.assertEqual(loop.price_alarm(self.start(item="two")), [])

    def test_negative_runs_started_in_the_same_second_are_ordered_in_both_orders(self):
        # Two runs whose created times are the same second: only started_ns orders them.
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        a = self.start(item="one")
        self.reset_work()
        self.h.builder_script = [edit(self.root, "y\n")]
        self.h.reviews = [[]]
        b = self.start(item="two")
        for early, late in ((a, b), (b, a)):
            early.state["created"] = late.state["created"] = "2026-09-30T10:00:00+01:00"
            early.state["started_ns"], late.state["started_ns"] = 1_000, 2_000
            early.state["steps"][0].update(tokens=10, uncached_tokens=10)
            late.state["steps"][0].update(tokens=110, uncached_tokens=110)
            for run in (early, late):
                runrecord.write_state(run.run_dir, run.state)
            self.assertEqual([f["step"] for f in loop.price_alarm(late)], ["build"])
            self.assertEqual(loop.price_alarm(early), [])  # the later run never counts
        # Records from before started_ns, in the same second, cannot be ordered: neither
        # counts the other, rather than one being guessed earlier.
        for run in (a, b):
            run.state.pop("started_ns")
            value = 10 if run is a else 110
            run.state["steps"][0].update(tokens=value, uncached_tokens=value)
            runrecord.write_state(run.run_dir, run.state)
        self.assertEqual(loop.price_alarm(b), [])
        self.assertEqual(loop.price_alarm(a), [])

    def test_negative_no_earlier_runs_means_no_alarm(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        self.assertEqual(loop.price_alarm(self.start()), [])

    def two_runs(self):
        """An earlier run whose build step is cheap, and a later one to judge."""
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        first = self.start(item="one")
        self.reset_work()
        self.h.builder_script = [edit(self.root, "y\n")]
        self.h.reviews = [[]]
        second = self.start(item="two")
        return first, second

    def test_negative_the_other_providers_steps_in_the_same_role_are_not_compared(self):
        # The chunk (d) short plan, 11.3: like with like, provider and role together.
        first, second = self.two_runs()
        first.state["steps"][0].update(tokens=10, uncached_tokens=10, provider="codex",
                                       thread_total_tokens=10)
        runrecord.write_state(first.run_dir, first.state)
        self.assertEqual(loop.price_alarm(second), [])  # second's builder is Claude
        first.state["steps"][0]["provider"] = "claude"
        runrecord.write_state(first.run_dir, first.state)
        self.assertEqual([f["step"] for f in loop.price_alarm(second)], ["build"])

    def test_negative_a_codex_builder_step_with_no_uncached_figure_is_never_compared(self):
        # Plan 10.10 item 6: an earlier Codex builder step with no uncached_tokens is
        # always left out, since its usage is the thread's running total, not the
        # step's share; a thread_total_tokens (R9-1's fix for tokens) does not change
        # that.
        first, second = self.two_runs()
        shaped = {"tokens": 10, "provider": "codex"}  # as 20261003-035437-0ec4 records
        first.state["steps"][0].update(shaped)
        first.state["steps"][0].pop("thread_total_tokens", None)
        first.state["steps"][0].pop("uncached_tokens", None)
        runrecord.write_state(first.run_dir, first.state)
        second.state["steps"][0].update(provider="codex", tokens=110,
                                        thread_total_tokens=110, uncached_tokens=110)
        self.assertEqual(loop.price_alarm(second), [])
        first.state["steps"][0]["thread_total_tokens"] = 10  # recorded under fix 3
        runrecord.write_state(first.run_dir, first.state)
        self.assertEqual(loop.price_alarm(second), [])
        first.state["steps"][0]["uncached_tokens"] = 10  # recorded since A3 chunk 1
        runrecord.write_state(first.run_dir, first.state)
        flags = loop.price_alarm(second)
        self.assertEqual([(f["step"], f["provider"]) for f in flags], [("build", "codex")])

    def test_negative_a_step_that_names_no_provider_is_not_compared(self):
        first, second = self.two_runs()
        first.state["steps"][0].update(tokens=10, uncached_tokens=10)
        first.state["steps"][0].pop("provider")
        runrecord.write_state(first.run_dir, first.state)
        self.assertEqual(loop.price_alarm(second), [])

    def test_negative_cache_reads_are_never_compared(self):
        # Plan 10.10 item 6: a step whose total is mostly cache reads is not flagged,
        # however large its total; the total stays in the step and the flag.
        first, second = self.two_runs()
        first.state["steps"][0].update(tokens=100, uncached_tokens=100)
        runrecord.write_state(first.run_dir, first.state)
        second.state["steps"][0].update(
            tokens=100_000, uncached_tokens=110,
            usage={"input_tokens": 100, "output_tokens": 10,
                   "cache_read_input_tokens": 99_890})
        self.assertEqual(loop.price_alarm(second), [])
        # The other way round: a small total, but uncached tokens over twice the median.
        second.state["steps"][0].update(tokens=50, uncached_tokens=250)
        flag, = loop.price_alarm(second)
        self.assertEqual((flag["uncached_tokens"], flag["tokens"], flag["median"]),
                         (250, 50, 100))

    def test_positive_an_earlier_step_with_no_uncached_figure_is_computed_from_usage(self):
        first, second = self.two_runs()
        step = first.state["steps"][0]
        step.pop("uncached_tokens", None)
        # 5 + 3 + 2 = 10: input, cache creation and output, cache reads left out.
        step.update(tokens=5000, usage={"input_tokens": 5, "output_tokens": 2,
                                        "cache_creation_input_tokens": 3,
                                        "cache_read_input_tokens": 4990})
        runrecord.write_state(first.run_dir, first.state)
        self.assertEqual([f["step"] for f in loop.price_alarm(second)], ["build"])
        # Without an output count the step has no figure and is left out.
        step["usage"] = {"input_tokens": 5, "cache_creation_input_tokens": 3}
        runrecord.write_state(first.run_dir, first.state)
        self.assertEqual(loop.price_alarm(second), [])

    def test_positive_an_earlier_codex_reviewer_step_is_computed_from_its_usage(self):
        first, second = self.two_runs()
        review = first.state["steps"][1]
        self.assertEqual((review["role"], review["provider"]), ("reviewer", "codex"))
        self.assertIsNone(review["uncached_tokens"])  # the fake's usage has no counts
        review.pop("uncached_tokens")
        # 50 input less 40 cached, plus 5 output: 15.
        review["usage"] = {"total": {"inputTokens": 50, "cachedInputTokens": 40,
                                     "outputTokens": 5, "totalTokens": 95}}
        runrecord.write_state(first.run_dir, first.state)
        self.assertEqual(loop.price_alarm(second), [])  # second's review has no figure
        second.state["steps"][1]["uncached_tokens"] = 100
        self.assertEqual([f["step"] for f in loop.price_alarm(second)], ["review-R1"])

    def test_positive_each_step_records_its_uncached_tokens(self):
        _first, second = self.two_runs()
        build, review = second.state["steps"]
        self.assertEqual((build["tokens"], build["uncached_tokens"]), (110, 110))
        self.assertIsNone(review["uncached_tokens"])
        self.assertEqual(review["tokens"], 1000)  # tokens stays in the record
        report = (second.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("- build (builder, claude, " + MODEL + "): 110 tokens", report)


class RunRecordEscapingTests(LoopCase):
    """The chunk (d) short plan, 11.4: every JSON file of a run record is written with
    non-ASCII characters escaped, so a garbled tool output kept verbatim cannot fail the
    encoding guard, and reads back as the same values."""

    # Built with chr() so this source file stays ASCII: an em dash, the three characters
    # an em dash becomes when UTF-8 is read as Windows-1252, and a pound sign.
    GARBLED = ("em dash " + chr(0x2014) + ", garbled " + chr(0xE2) + chr(0x20AC)
               + chr(0x201D) + ", pound " + chr(0xA3))

    def test_positive_write_json_escapes_and_reads_back_equal(self):
        path = self.root / "out" / "x.json"
        value = {"text": self.GARBLED, "list": [self.GARBLED]}
        loop._write_json(path, value)
        raw = path.read_bytes()
        self.assertTrue(raw.isascii(), raw)
        self.assertEqual(json.loads(raw.decode("utf-8")), value)

    def test_positive_every_json_file_a_run_writes_is_ascii(self):
        write = edit(self.root, "x\n")

        def build(prompt):
            write(prompt)
            return "Changed work/a.txt. " + self.GARBLED
        self.h.builder_script = [build,
                                 reply(self.GARBLED + "\n" + actions(("R1-1", "fixed")))]
        self.h.reviews = [[finding(title="A defect in " + self.GARBLED)], []]
        run = self.start()
        files = [p for p in run.run_dir.rglob("*") if p.suffix in (".json", ".jsonl")]
        self.assertTrue(files)
        for path in files:
            with self.subTest(path=path.relative_to(run.run_dir).as_posix()):
                self.assertTrue(path.read_bytes().isascii())
        self.assertIn(self.GARBLED, json.dumps(runrecord.read_state(run.run_dir),
                                               ensure_ascii=False))


class FixtureHelperTests(unittest.TestCase):
    """The suite's own fixture helper (code review R18-1)."""

    def test_rejection_a_failing_git_command_names_gits_own_error(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        with self.assertRaises(FixtureGitError) as caught:
            git(Path(folder.name), "add", "missing.txt")
        text = str(caught.exception)
        self.assertIn("`git add missing.txt` exited 128", text)
        self.assertIn("not a git repository", text.lower())


class PacketTests(unittest.TestCase):

    def ledger(self):
        ledger = sr.Ledger()
        ledger.add_round(1, [{"title": "check failed", "severity": "blocker",
                              "file": "x", "evidence": "e", "fix": "f"}],
                         [dict(finding(), title="bad\n## Open\n- R9-9 fake")])
        ledger.apply_actions({"R1-1": ("fixed", "done"),
                              "R1-2": ("declined-merits", "wrong")})
        return ledger

    def test_positive_the_handoff_reader_counts_the_packet(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        (root / "memory").mkdir()
        packet.write(root, "demo", "20260929-120000-abcd", self.ledger())
        sys.path.insert(0, str(PROJECT / "workflows" / "handoff" / "scripts"))
        import gather
        found = [p for p in gather.review_packets(root)
                 if p["path"].replace("\\", "/").endswith("demo_review_packet.md")]
        self.assertEqual(found[0]["open"], ["R1-2"])
        self.assertEqual(found[0]["addressed"], ["R1-1"])
        self.assertEqual(found[0]["labels_noncanonical"], [])

    def test_rejection_provider_text_cannot_break_the_structure(self):
        text = packet.render("demo", "20260929-120000-abcd", self.ledger())
        self.assertEqual(text.count("\n## Open"), 1)
        self.assertNotIn("\n- R9-9", text)
        self.assertEqual(packet.clean("- x"), "\\- x")
        self.assertEqual(packet.clean("# x"), "\\# x")
        self.assertEqual(packet.clean("a" + chr(0x2014) + "b"), "a - b")  # an em dash

    def test_positive_a_declined_finding_stays_open_with_the_builder_action(self):
        text = packet.render("demo", "20260929-120000-abcd", self.ledger())
        open_part = text.split("## Addressed")[0]
        self.assertIn("R1-2", open_part)
        self.assertIn("Builder: declined-merits. wrong", open_part)

    def test_positive_an_accepted_decline_moves_to_addressed(self):
        ledger = self.ledger()
        ledger.findings["R1-2"]["decline_accepted"] = 2
        text = packet.render("demo", "20260929-120000-abcd", ledger)
        open_part, addressed = text.split("## Addressed")
        self.assertNotIn("R1-2", open_part)
        self.assertIn("### R1-2", addressed)
        self.assertIn("Reviewer: not raised again in round 2", addressed)

    def test_negative_a_scope_decline_is_never_addressed(self):
        self.assertFalse(packet.is_addressed({"action": "declined-scope",
                                              "decline_accepted": 2}))


class BriefReaderTests(unittest.TestCase):

    def test_positive_edit_paths_and_acceptance_come_from_a_passing_brief(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        (root / "work").mkdir()
        (root / "b.md").write_bytes(BRIEF.encode("utf-8"))
        read = brief.read_brief("b.md", root)
        self.assertEqual(read["edit_paths"], ["work/"])
        self.assertEqual(read["acceptance"], ["python workflows/demo/tests/test_demo.py"])
        self.assertEqual(read["sections"]["Goal"], ["- work/a.txt says done."])

    def test_rejection_a_failing_brief_is_never_read(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        (Path(folder.name) / "b.md").write_bytes(b"## Goal\n")
        with self.assertRaises(ValueError):
            brief.read_brief("b.md", folder.name)


class WorktreeTests(unittest.TestCase):

    def setUp(self):
        self.root, self.personal = make_project(self)
        self.heads = {"public": worktree.git(self.root, "rev-parse", "HEAD").strip(),
                      "personal": worktree.git(self.root, "rev-parse", "HEAD",
                                               git_dir=str(self.personal)).strip()}

    def diff(self, paths):
        return worktree.review_diff(self.root, paths, self.heads["public"],
                                    self.heads["personal"], str(self.personal),
                                    category_a=CAT_A)

    def test_positive_tracked_new_and_ignored_changes_are_all_shown(self):
        (self.root / "work" / "a.txt").write_bytes(b"changed\n")
        (self.root / "work" / "new.txt").write_bytes(b"fresh\n")
        (self.root / "memory" / "note.md").write_bytes(b"personal edit\n")
        (self.root / "memory" / "other.md").write_bytes(b"new personal\n")
        text = self.diff(["work/", "memory/"])
        self.assertIn("+changed", text)
        self.assertIn("--- new file work/new.txt ---\nfresh", text)
        self.assertIn("+personal edit", text)  # against the personal commit
        self.assertIn("--- new file memory/other.md ---", text)

    def test_positive_a_deleted_personal_file_shows_as_a_deletion(self):
        (self.root / "memory" / "note.md").unlink()
        self.assertIn("deleted file", self.diff(["memory/"]))

    def test_negative_no_change_is_an_empty_diff_and_an_empty_change_list(self):
        self.assertEqual(self.diff(["work/"]), "")
        before = worktree.snapshot(self.root, ["work/"], CAT_A)
        self.assertEqual(worktree.changed(before, worktree.snapshot(self.root, ["work/"],
                                                                    CAT_A)), [])

    def test_rejection_env_files_never_reach_a_diff_a_snapshot_or_an_archive(self):
        (self.root / "work" / ".env").write_bytes(b"SECRET=untracked\n")
        (self.root / "work" / "sub").mkdir()
        (self.root / "work" / "sub" / ".ENV.local").write_bytes(b"SECRET=case\n")
        (self.root / "memory" / ".env").write_bytes(b"SECRET=ignored\n")
        (self.root / "work" / "a.txt").write_bytes(b"real change\n")
        text = self.diff(["work/", "memory/"])
        self.assertNotIn("SECRET", text)
        self.assertIn("+real change", text)  # the rest of the diff is still there
        self.assertEqual(worktree.list_files(self.root, ["work/", "memory/"], CAT_A),
                         ["memory/note.md", "work/a.txt"])
        folder = self.root / "runs" / "r"
        worktree.record_round(folder, 1, self.root, ["work/"], "", category_a=CAT_A)
        with zipfile.ZipFile(folder / "rounds/R1/files.zip") as bundle:
            self.assertEqual(bundle.namelist(), ["work/a.txt"])

    def test_rejection_a_tracked_env_file_is_kept_out_of_git_diffs(self):
        (self.root / "work" / ".env").write_bytes(b"SECRET=one\n")
        git(self.root, "add", "work/.env")
        git(self.root, "commit", "-qm", "tracked env")
        head = worktree.git(self.root, "rev-parse", "HEAD").strip()
        (self.root / "work" / ".env").write_bytes(b"SECRET=two\n")
        text = worktree.review_diff(self.root, ["work/"], head, category_a=CAT_A)
        self.assertNotIn("SECRET", text)
        folder = self.root / "runs" / "s"
        worktree.record_start(folder, self.root, ["work/"], str(self.personal))
        self.assertNotIn("SECRET", (folder / "start/diff.patch").read_text(encoding="utf-8"))

    def test_rejection_generated_ignored_files_are_never_work(self):
        # A generated file (Category C), there before the run and rewritten after.
        (self.root / "work" / "last-result.generated").write_bytes(b"old\n")
        before = worktree.snapshot(self.root, ["work/"], CAT_A)
        (self.root / "work" / "last-result.generated").write_bytes(b"rewritten\n")
        (self.root / "memory" / "scratch.md").write_bytes(b"not category A\n")
        self.assertEqual(worktree.changed(before, worktree.snapshot(self.root, ["work/"],
                                                                    CAT_A)), [])
        text = self.diff(["work/", "memory/"])
        self.assertNotIn("last-result.generated", text)
        self.assertNotIn("scratch.md", text)
        self.assertEqual(worktree.list_files(self.root, ["work/", "memory/"], CAT_A),
                         ["memory/note.md", "work/a.txt"])
        folder = self.root / "runs" / "g"
        worktree.record_round(folder, 1, self.root, ["work/"], "", category_a=CAT_A)
        with zipfile.ZipFile(folder / "rounds/R1/files.zip") as bundle:
            self.assertEqual(bundle.namelist(), ["work/a.txt"])

    def test_positive_a_new_category_a_file_is_work_and_reviewed(self):
        (self.root / "memory" / "new.md").write_bytes(b"a new memory file\n")
        self.assertIn("memory/new.md", worktree.list_files(self.root, ["memory/"], CAT_A))
        self.assertIn("--- new file memory/new.md ---", self.diff(["memory/"]))

    def test_rejection_a_missing_classification_is_an_error_not_a_guess(self):
        with self.assertRaises(worktree.GitError):
            worktree.category_a(self.root, allowlist=self.root / "no-such" / "allowlist.py")
        # A classification whose plan or block is missing is an error too.
        fake = self.root / "fake_allowlist.py"
        fake.write_bytes(b"def read_specs():\n    return None\n")
        with self.assertRaises(worktree.GitError):
            worktree.category_a(self.root, allowlist=fake)

    def test_positive_the_classification_is_loaded_from_beside_this_code(self):
        # Orchestrator isolation plan 7.0 item 4: in a run's trusted copy, beside the
        # code is the starting commit's; it is never taken from the project root given.
        self.assertEqual(worktree.ALLOWLIST,
                         SCRIPTS.parent.parent / "sync-architecture" / "scripts"
                         / "allowlist.py")
        planted = self.root / "workflows" / "sync-architecture" / "scripts"
        planted.mkdir(parents=True)
        (planted / "allowlist.py").write_bytes(
            b"raise SystemExit('the project root copy was loaded')\n")
        fake = self.root / "beside.py"
        fake.write_bytes(b"def read_specs():\n    return ['x']\n"
                         b"def select(specs):\n    return {'final': ['memory/a.md']}\n")
        original = worktree.ALLOWLIST
        worktree.ALLOWLIST = fake
        try:
            self.assertEqual(worktree.category_a(self.root), {"memory/a.md"})
        finally:
            worktree.ALLOWLIST = original

    def test_rejection_a_run_record_is_never_the_builder_work(self):
        workflow = self.root / "workflows" / "review-orchestration"
        (workflow / "runs" / "r1").mkdir(parents=True)
        (workflow / "runs" / "r1" / "state.json").write_bytes(b"{}\n")
        (workflow / "notes.md").write_bytes(b"workflow file\n")
        paths = ["workflows/review-orchestration/"]
        self.assertEqual(worktree.list_files(self.root, paths, CAT_A),
                         ["workflows/review-orchestration/notes.md"])
        text = self.diff(paths)
        self.assertIn("workflows/review-orchestration/notes.md", text)
        self.assertNotIn("state.json", text)

    def test_negative_caches_are_skipped(self):
        (self.root / "work" / "__pycache__").mkdir()
        (self.root / "work" / "__pycache__" / "a.pyc").write_bytes(b"x")
        self.assertEqual(worktree.list_files(self.root, ["work/"], CAT_A), ["work/a.txt"])

    def test_positive_the_round_record_holds_every_file_in_an_archive(self):
        folder = self.root / "runs" / "r"
        worktree.record_round(folder, 1, self.root, ["work/"], "the diff",
                              category_a=CAT_A)
        with zipfile.ZipFile(folder / "rounds/R1/files.zip") as bundle:
            self.assertEqual(bundle.namelist(), ["work/a.txt"])
            self.assertEqual(bundle.read("work/a.txt"), b"start\n")
        self.assertEqual((folder / "rounds/R1/diff.patch").read_text(encoding="utf-8"),
                         "the diff")
        # No live copy of the project is left for project tools to walk into.
        self.assertEqual(sorted(p.name for p in (folder / "rounds/R1").iterdir()),
                         sorted(["diff.patch", "files.zip"]))

    def test_positive_a_round_written_again_replaces_its_record(self):
        folder = self.root / "runs" / "r"
        worktree.record_round(folder, 1, self.root, ["work/"], "first", category_a=CAT_A)
        (self.root / "work" / "a.txt").write_bytes(b"later\n")
        worktree.record_round(folder, 1, self.root, ["work/"], "second", category_a=CAT_A)
        with zipfile.ZipFile(folder / "rounds/R1/files.zip") as bundle:
            self.assertEqual(bundle.read("work/a.txt"), b"later\n")
        self.assertEqual((folder / "rounds/R1/diff.patch").read_text(encoding="utf-8"),
                         "second")


class ChecksTests(unittest.TestCase):

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def script(self, relative, body):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body.encode("utf-8"))

    def test_positive_a_passing_acceptance_check_is_not_a_finding(self):
        found, lines = checks.acceptance(self.root, sys.executable, ['python -c "print(1)"'])
        self.assertEqual(found, [])
        self.assertIn("PASS", lines[0])

    def test_rejection_a_failing_acceptance_check_is_a_finding(self):
        found, _ = checks.acceptance(self.root, sys.executable,
                                     ['python -c "import sys; sys.exit(4)"'])
        self.assertEqual(found[0]["severity"], "blocker")
        self.assertIn("exit 4", found[0]["evidence"])

    def test_rejection_a_check_that_cannot_run_is_a_finding(self):
        found, _ = checks.close_out(self.root, sys.executable)
        self.assertEqual(found[0]["title"], "close-out verifier did not run")
        found, _ = checks.acceptance(self.root, sys.executable, ["no-such-program-xyz"])
        self.assertEqual(len(found), 1)

    def close_out_script(self, gates, degraded=()):
        self.script("workflows/close-out/scripts/run.py",
                    "import json\nprint(json.dumps({'status': 'fail', 'gates': "
                    + repr(gates) + ", 'degraded': " + repr(list(degraded)) + "}))\n")

    PASSED = [{"name": "tests", "passed": True, "detail": "ok", "files": []},
              {"name": "link audit", "passed": True, "detail": "0 dead link(s)"}]

    def test_positive_a_clean_tree_may_start(self):
        self.close_out_script(self.PASSED)
        start = checks.close_out_start(self.root, sys.executable)
        self.assertEqual((start["clean"], start["problems"]), (True, []))

    def test_rejection_a_failing_skipped_or_unreadable_check_at_the_start_is_not_clean(self):
        failing = [dict(self.PASSED[0], passed=False, detail="1 failed",
                        files=[{"file": "other/test_x.py", "passed": False}])]
        for gates, degraded in ((failing, ()),
                                (self.PASSED, ["structural audit: a guard skipped"])):
            self.close_out_script(gates, degraded)
            start = checks.close_out_start(self.root, sys.executable)
            self.assertFalse(start["clean"])
            self.assertTrue(start["problems"])
        self.script("workflows/close-out/scripts/run.py", "print('not json')\n")
        self.assertFalse(checks.close_out_start(self.root, sys.executable)["clean"])

    def test_rejection_every_failed_gate_during_a_run_is_a_finding(self):
        # The run started clean, so nothing failing now can predate it: the old
        # comparison of test names, audit entries and link counts is gone (R5-3).
        self.close_out_script([
            {"name": "tests", "passed": False, "detail": "1 failed",
             "files": [{"file": "other/test_x.py", "passed": False}]},
            {"name": "link audit", "passed": False, "detail": "2 dead link(s)"},
            {"name": "structural audit", "passed": False,
             "detail": "could not run audit: boom"}])
        found, _ = checks.close_out(self.root, sys.executable)
        self.assertEqual([f["title"] for f in found],
                         ["close-out gate failed: tests", "close-out gate failed: link audit",
                          "close-out gate failed: structural audit"])
        self.assertIn("other/test_x.py", found[0]["evidence"])

    def test_rejection_a_skipped_scope_is_a_finding_even_with_every_gate_passed(self):
        self.close_out_script(self.PASSED, ["structural audit: encoding guard skipped"])
        found, lines = checks.close_out(self.root, sys.executable)
        self.assertEqual([f["title"] for f in found], ["close-out skipped part of a check"])
        self.assertIn("encoding guard skipped", found[0]["evidence"])

    def test_rejection_a_doc_sync_scan_that_did_not_run_is_a_finding_anywhere(self):
        self.script("workflows/doc-sync-guard/scripts/run.py",
                    "import json\nprint(json.dumps({'findings': ["
                    "{'severity': 'WARN', 'message': 'scan skipped - git failed'},"
                    "{'severity': 'DEGRADED', 'message': 'inventory: could not load'}"
                    "]}))\n")
        found, _ = checks.doc_sync(self.root, sys.executable, ["work/"])
        self.assertEqual(len(found), 2)
        self.assertTrue(all(f["title"].startswith("doc-sync did not run in full")
                            for f in found))

    def test_positive_a_failed_close_out_gate_is_a_finding(self):
        self.script("workflows/close-out/scripts/run.py",
                    "import json\nprint(json.dumps({'status': 'fail', 'gates': ["
                    "{'name': 'tests', 'passed': False, 'detail': '1 failed'},"
                    "{'name': 'link audit', 'passed': True, 'detail': 'ok'}]}))\n")
        found, _ = checks.close_out(self.root, sys.executable)
        self.assertEqual([f["title"] for f in found], ["close-out gate failed: tests"])

    def test_negative_a_doc_sync_warn_outside_the_edit_paths_is_not_a_finding(self):
        self.script("workflows/doc-sync-guard/scripts/run.py",
                    "import json\nprint(json.dumps({'findings': ["
                    "{'severity': 'WARN', 'message': 'work/sub: CONTEXT.md not updated'},"
                    "{'severity': 'WARN', 'message': 'workshop: CONTEXT.md not updated'}"
                    "]}))\n")
        found, lines = checks.doc_sync(self.root, sys.executable, ["work/"])
        self.assertEqual([f["file"] for f in found], ["work/sub"])
        self.assertTrue(any("outside this run's edit paths" in line for line in lines))

    def test_rejection_an_audit_that_crashes_before_reporting_is_a_finding(self):
        self.script("workflows/audit/scripts/run.py",
                    "import sys\nsys.stderr.write('Traceback: boom')\nsys.exit(1)\n")
        found, lines = checks.audit(self.root, sys.executable, ["work"])
        self.assertEqual([f["title"] for f in found], ["targeted audit failed (exit 1)"])
        self.assertIn("boom", found[0]["evidence"])
        self.assertFalse(any("clean" in line for line in lines))

    def test_negative_a_clean_audit_exiting_0_is_not_a_finding(self):
        self.script("workflows/audit/scripts/run.py",
                    "print('**Failures:** 0\\n\\nAll structural checks passed.')\n")
        found, lines = checks.audit(self.root, sys.executable, ["work"])
        self.assertEqual(found, [])
        self.assertIn("clean", lines[-1])

    def test_positive_audit_failures_and_warnings_are_findings(self):
        self.script("workflows/audit/scripts/run.py",
                    "print('## Failures\\n\\n- `work` missing CONTEXT.md\\n\\n"
                    "## Warnings\\n\\n- `work` Revision History over cap\\n\\n"
                    "## Info\\n\\n- `work` fine')\n")
        found, _ = checks.audit(self.root, sys.executable, ["work"])
        self.assertEqual([f["severity"] for f in found], ["blocker", "major"])
        self.assertEqual(found[0]["file"], "work")


class PromptTests(unittest.TestCase):

    def test_positive_every_prompt_renders_with_its_fields(self):
        loop.render("builder", edit_paths="a/", tool_rules="  - x", brief="B",
                    plan="PL")
        text = loop.render("reviewer", round=1, brief="B", plan="PL", mechanical="M",
                           prior="P", diff="D", changed_files="- a",
                           check_written="None.", reviewer_tools="T")
        self.assertIn("round\n1.", text)
        fix = loop.render("fix", round=2, findings="F", tool_rules="", brief="BR",
                          plan="PL")
        self.assertIn('"label": "R2-1"', fix)

    def test_rejection_a_missing_field_fails_loudly(self):
        with self.assertRaises(KeyError):
            loop.render("reviewer", round=1)

    def test_positive_the_builder_prompt_lists_the_verification_commands(self):
        rules = loop.tool_rules(limits.CLAUDE, approver.load_verify_commands())
        text = loop.render("builder", edit_paths="a/", tool_rules=rules, brief="B",
                           plan="PL")
        self.assertIn("doc-sync-guard", text)
        self.assertIn("No git commands", text)
        self.assertNotIn("$", text.split("THE BRIEF")[0].replace("(?!-)", ""),
                         "no field is left unfilled")

    # A3 proof run follow-ups: Claude sessions passed a `glob` to Grep nine times
    # across the two runs, in four roles, and a Codex builder logged as "Codex".

    GLOB_CLAUDE = "A Grep call carrying the `glob` option is refused whatever the pattern"
    GLOB_NEUTRAL = "(a `glob` option, such as `*.py`) is refused whatever the pattern"

    def test_positive_every_claude_role_is_told_a_glob_is_refused(self):
        rules = loop.tool_rules(limits.CLAUDE, approver.load_verify_commands())
        builder = loop.render("builder", edit_paths="a/", tool_rules=rules, brief="B",
                              plan="PL")
        self.assertIn(self.GLOB_CLAUDE, " ".join(builder.split()))
        self.assertIn(self.GLOB_CLAUDE, loop.reviewer_tools(limits.CLAUDE))

    def test_positive_the_shared_planning_prompts_say_it_without_naming_a_tool(self):
        # Given to both providers, so they name no tool: the identity check refuses a
        # plan naming one in backticks, and a prompt naming one invites the plan to.
        texts = {
            "planner": loop.render("planner", brief="B", public_head="P",
                                   personal_head="Q"),
            "plan-reviewer": loop.render("plan-reviewer", round=1, brief="B", plan="PL",
                                         prior="None."),
            "synthesiser": loop.render("synthesiser", brief="B", plan_1="P1",
                                       plan_2="P2"),
            "revision": loop.render("revision", round=1, brief="B", plan_1="P1",
                                    plan_2="P2", synthesis="S", findings="F"),
        }
        for name, text in texts.items():
            with self.subTest(prompt=name):
                self.assertIn(self.GLOB_NEUTRAL, " ".join(text.split()))
                self.assertNotIn("Grep", text)
                self.assertNotIn("`Glob`", text)

    def test_positive_the_builder_prompt_names_the_log_actor_for_both_builders(self):
        for provider, patterns in ((limits.CLAUDE, approver.load_verify_commands()),
                                   (limits.CODEX, None)):
            with self.subTest(builder=provider):
                text = loop.render("builder", edit_paths="a/", brief="B", plan="PL",
                                   tool_rules=loop.tool_rules(provider, patterns))
                self.assertIn("writing every entry with `Actor: Biblio`",
                              " ".join(text.split()))


if __name__ == "__main__":
    unittest.main()
