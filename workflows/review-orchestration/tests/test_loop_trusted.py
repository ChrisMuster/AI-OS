#!/usr/bin/env python3
"""Hermetic tests for the build-and-review loop, part of the split of the old single
loop file (plan 10.3, the chunk list): the run's trusted copy and the hand-over to it
(orchestrator isolation plan 7.0 and 7.8), and the command line of ``run.py``.

The shared fixtures (``Harness``, the fake sessions, ``LoopCase``) are in
``loop_fixtures.py``. No test makes a model call. Same three controls as the other
suites: a positive control must work, a rejection control must end or refuse the run
with the reason named, a negative control must not.

    python workflows/review-orchestration/tests/test_loop_trusted.py
"""

import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_fixtures import *  # noqa: E402,F401,F403 - the loop suites' shared fixtures


class TrustedCopyTests(LoopCase):
    """Orchestrator isolation plan 7.0 item 3 and 7.8 (S6a): a run started in the live
    tree is handed to its frozen folder's trusted run.py, and a resume or stop runs
    that copy, which arms the tripwire and takes the project root from hook.json before
    it imports anything. The run record and the log live under the project root."""

    def setUp(self):
        super().setUp()
        workflow = self.root / "workflows" / "review-orchestration"
        self.runs = workflow / "runs"
        self.log = workflow / "LOG.md"
        (self.root / "AGENTS.md").write_bytes(b"# agents\n")
        self.temp = self.root.parent / "temp"
        self.h.frozen_parent = self.temp / frozen.FROZEN_PARENT
        self.h.on_frozen = make_trusted
        self.elsewhere = self.root.parent / "elsewhere"
        self.elsewhere.mkdir()
        # Side effects planted in the live tree's orchestrator: none may ever fire.
        self.markers = self.root.parent / "fired"
        live = workflow / "scripts"
        live.mkdir(parents=True)
        for name in ("run", "brief", "loop", "worktree"):
            (live / f"{name}.py").write_text(
                f"open(r'{self.markers}-{name}', 'w').close()\n", encoding="utf-8",
                newline="\n")

    def started(self):
        run = loop.start("brief.md", "demo", str(self.personal), root=self.root,
                         deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                         settings=self.settings, builder="claude")
        return run, Path(run.state["frozen_dir"])

    def paused(self):
        run, folder = self.started()
        run.state.update({"status": sr.PAUSED, "pause": "awaiting-user"})
        run.save()
        return run, folder

    def trusted(self, folder, *argv, env=None):
        # Without PYTHONDONTWRITEBYTECODE, so the trusted copy must turn bytecode
        # writing off itself (code review R20-1).
        environ = {k: v for k, v in os.environ.items()
                   if k not in ("BOOK_DRAGON_ROOT", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE")}
        environ.update(env or {})
        return subprocess.run([sys.executable, str(frozen.trusted_run_py(folder)), *argv],
                              capture_output=True, encoding="utf-8", errors="replace",
                              timeout=300, cwd=str(self.elsewhere), env=environ)

    def fired(self):
        return sorted(p.name for p in self.markers.parent.glob(self.markers.name + "-*"))

    def test_positive_a_stop_runs_the_trusted_copy_against_the_project(self):
        run, folder = self.paused()
        done = self.trusted(folder, "--stop", run.state["run_id"])
        self.assertIn(f"ended {sr.STOPPED_BY_USER}", done.stdout, done.stderr)
        self.assertEqual(self.fired(), [], "nothing of the live tree ran")
        self.assertIn(f"Run {run.state['run_id']} ended stopped-by-user",
                      self.log.read_text(encoding="utf-8"))
        state = runrecord.read_state(self.runs / run.state["run_id"])
        self.assertEqual(state["stop_reasons"][-1]["reason"], sr.STOPPED_BY_USER)
        self.assertTrue((self.runs / run.state["run_id"] / "report.md").is_file())
        # Nothing of the run's data lands in the frozen folder.
        self.assertEqual([p for p in folder.rglob("*")
                          if p.name in ("LOG.md", "runs", "report.md")], [])

    def test_positive_a_dry_stop_with_a_matching_root_changes_nothing(self):
        run, folder = self.paused()
        before = (self.runs / run.state["run_id"] / "state.json").read_bytes()
        done = self.trusted(folder, "--stop", run.state["run_id"], "--dry-run",
                            env={"BOOK_DRAGON_ROOT": str(self.root)})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("[DRY RUN] would end run", done.stderr)
        self.assertEqual((self.runs / run.state["run_id"] / "state.json").read_bytes(),
                         before)
        self.assertEqual(self.fired(), [])

    def test_rejection_the_trusted_copy_refuses_what_it_cannot_trust(self):
        run, folder = self.paused()
        run_id = run.state["run_id"]
        other = self.root.parent / "other"
        other.mkdir()
        (other / "AGENTS.md").write_bytes(b"# agents\n")
        cases = [("a root that disagrees with hook.json", ["--stop", run_id],
                  {"BOOK_DRAGON_ROOT": str(other)}, "disagree|names"),
                 ("another run's id", ["--stop", "20261008-000000-ffff"], {},
                  "belongs to run")]
        for label, argv, env, reason in cases:
            with self.subTest(label):
                done = self.trusted(folder, *argv, env=env)
                self.assertEqual(done.returncode, loop_refused(), done.stdout + done.stderr)
                self.assertRegex(done.stdout + done.stderr, reason)
        done = self.trusted(folder, "--run", "brief.md", "--builder", "claude",
                            "--item", "x", "--git-dir", "x")
        self.assertEqual(done.returncode, 2, "a run never starts from a trusted copy")
        self.assertEqual(self.fired(), [])
        self.assertEqual(runrecord.read_state(self.runs / run_id)["status"], sr.PAUSED)

    def test_rejection_a_hook_json_root_without_agents_md(self):
        run, folder = self.paused()
        (self.root / "AGENTS.md").unlink()
        done = self.trusted(folder, "--stop", run.state["run_id"])
        self.assertEqual(done.returncode, loop_refused())
        self.assertIn("not a folder holding AGENTS.md", done.stderr)

    def test_rejection_a_missing_tripwire_loads_nothing(self):
        run, folder = self.paused()
        (folder / "tripwire" / "sitecustomize.py").unlink()
        done = self.trusted(folder, "--stop", run.state["run_id"])
        self.assertEqual(done.returncode, loop_refused())
        self.assertIn("sitecustomize.py", done.stderr)
        self.assertEqual(runrecord.read_state(self.runs / run.state["run_id"])["status"],
                         sr.PAUSED)

    def test_rejection_the_tripwire_is_armed_before_the_first_project_import(self):
        # A live-tree file loaded from inside the trusted copy fails on the tripwire,
        # and its code never runs.
        run, folder = self.paused()
        planted = self.root / "planted.py"
        planted.write_text(f"open(r'{self.markers}-planted', 'w').close()\n",
                           encoding="utf-8", newline="\n")
        loop_copy = frozen.trusted_run_py(folder).parent / "loop.py"
        with open(loop_copy, "a", encoding="utf-8", newline="\n") as handle:
            handle.write("\nimport importlib.util as _u\n"
                         f"_s = _u.spec_from_file_location('planted', r'{planted}')\n"
                         "_s.loader.exec_module(_u.module_from_spec(_s))\n")
        done = self.trusted(folder, "--stop", run.state["run_id"])
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("TripwireError", done.stderr)
        self.assertEqual(self.fired(), [])

    def test_positive_advance_takes_a_started_runs_steps(self):
        # Code review R19-1: the trusted copy's --advance mode, given a started run,
        # takes its steps to their end (here with the suite's fake sessions).
        run, folder = self.started()
        self.h.builder_script = [edit(self.root, "done\n")]
        self.h.reviews = [[]]
        with redirect_stdout(io.StringIO()):
            code = run_py.main(["--advance", run.state["run_id"]], root=self.root,
                               log_path=self.log, deps=self.h.deps(), runs_dir=self.runs,
                               frozen=folder)
        self.assertEqual(code, 0)
        state = runrecord.read_state(self.runs / run.state["run_id"])
        self.assertEqual(state["stop_reasons"][-1]["reason"], sr.REVIEWED_CLEAN)
        self.assertEqual([s["step"] for s in state["steps"]], ["build", "review-R1"])
        self.assertEqual((self.root / "work" / "a.txt").read_bytes(), b"done\n")

    def test_positive_advance_as_a_process_reaches_the_runs_steps(self):
        # Code review R19-1: the same mode as the real process a start hands over to,
        # with the trusted copy's step-taking replaced by a stub that records it was
        # reached for this run and ends the run, so no session is opened.
        run, folder = self.started()
        reached = self.root.parent / "advanced"
        loop_copy = frozen.trusted_run_py(folder).parent / "loop.py"
        with open(loop_copy, "a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n\nasync def _stub_advance(self):\n"
                         f"    with open(r'{reached}', 'w', encoding='utf-8') as out:\n"
                         "        out.write(self.state['run_id'])\n"
                         "    self.end(sr.REVIEWED_CLEAN, {'stub': True})\n\n\n"
                         "Run.advance = _stub_advance\n")
        done = self.trusted(folder, "--advance", run.state["run_id"])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(reached.read_text(encoding="utf-8"), run.state["run_id"])
        state = runrecord.read_state(self.runs / run.state["run_id"])
        self.assertEqual(state["stop_reasons"][-1]["reason"], sr.REVIEWED_CLEAN)
        self.assertIn(f"Run {run.state['run_id']} ended reviewed-clean",
                      self.log.read_text(encoding="utf-8"))
        self.assertEqual(self.fired(), [], "nothing of the live tree ran")

    def end_as_the_trusted_copy_would(self, run, reason=sr.REVIEWED_CLEAN):
        again = loop.load(run.state["run_id"], root=self.root, deps=self.h.deps(),
                          log_path=self.log, runs_dir=self.runs)
        again.end(reason, {"by": "the trusted copy"})

    @staticmethod
    def folder_state(folder):
        """A SHA-256 for every file in a frozen folder, bytecode caches included."""
        return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(Path(folder).rglob("*")) if p.is_file()}

    def test_negative_a_trusted_advance_writes_nothing_in_the_frozen_folder(self):
        # Plan 7.0 item 3 and code review R20-1: after setup only the hook's decisions
        # file and the helper's calls file may be written there, so an advance ending
        # on a stop reason that keeps the folder (error, plan 7.3) must leave it
        # byte-identical, no bytecode cache included. (Rewritten for S6b: a stop now
        # removes the folder, tested below.)
        run, folder = self.started()
        loop_copy = frozen.trusted_run_py(folder).parent / "loop.py"
        with open(loop_copy, "a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n\nasync def _stub_advance(self):\n"
                         "    self.end(sr.ERROR, {'stub': True})\n\n\n"
                         "Run.advance = _stub_advance\n")
        before = self.folder_state(folder)
        done = self.trusted(folder, "--advance", run.state["run_id"])
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(self.folder_state(folder), before)
        self.assertEqual(list(folder.rglob("__pycache__")), [])

    def test_positive_a_trusted_stop_removes_the_frozen_folder_it_runs_from(self):
        # Plan 7.3 (S6b): stopped-by-user cannot be resumed, so the run removes its
        # frozen folder, from the trusted copy running inside it: the removal the plan
        # asks to be tested on Windows. The run record and log stay under the project.
        run, folder = self.paused()
        done = self.trusted(folder, "--stop", run.state["run_id"])
        self.assertIn("ended stopped-by-user", done.stdout, done.stderr)
        self.assertFalse(folder.exists(), done.stdout + done.stderr)
        state = runrecord.read_state(self.runs / run.state["run_id"])
        self.assertEqual(state["cleanup"][0], {"what": "the frozen folder",
                                               "removed": True})
        self.assertIn("- removed the frozen folder",
                      (self.runs / run.state["run_id"] / "report.md")
                      .read_text(encoding="utf-8"))

    def test_positive_a_start_hands_the_run_to_its_trusted_copy(self):
        run, folder = self.started()
        calls = []

        def runner(argv, cwd=None, env=None):
            calls.append((argv, cwd, env))
            self.end_as_the_trusted_copy_would(run)
            return subprocess.CompletedProcess(argv, 7)
        environ = {"PATH": os.environ.get("PATH", ""), run_py.STARTER_MARKER: "1",
                   "BOOK_DRAGON_ROOT": "stale"}
        code = run_py.hand_over(run, root=self.root, wait=True, environ=environ,
                                runner=runner, python=sys.executable)
        self.assertEqual(code, 7, "the trusted copy's exit code is the start's")
        (argv, cwd, env), = calls
        self.assertEqual(argv, [sys.executable, str(frozen.trusted_run_py(folder)),
                                "--advance", run.state["run_id"], "--wait"])
        self.assertEqual(cwd, str(self.root))
        self.assertEqual(env["BOOK_DRAGON_ROOT"], str(self.root))
        self.assertEqual(env[run_py.REEXEC_MARKER], "1")
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
        # Plan 7.8 (S7): the trusted copy and every process it starts get the tripwire
        # folder first on PYTHONPATH.
        self.assertEqual(env["PYTHONPATH"].split(os.pathsep)[0], str(folder / "tripwire"))
        self.assertNotIn(run_py.STARTER_MARKER, env)
        # The trusted copy ended the run, so the hand-over leaves it as it is.
        state = runrecord.read_state(self.runs / run.state["run_id"])
        self.assertEqual(state["stop_reasons"][-1]["reason"], sr.REVIEWED_CLEAN)
        self.assertEqual(len(state["stop_reasons"]), 1)

    def test_positive_the_trusted_copys_set_up_hands_the_tripwire_to_its_children(self):
        # Plan 7.8 and section 8 ("Tripwire in a resumed orchestrator"): set_up_trusted
        # puts the run's tripwire folder first on PYTHONPATH, keeping what was there,
        # and sets the run's fire log; a child started with that environment loads the
        # tripwire as sitecustomize. Run in a subprocess, so the audit hook it arms
        # never reaches this test process.
        run, folder = self.started()
        # The entry already there, a folder in the test's own temporary directory
        # (decision 38): no drive letter on Linux, so `os.pathsep` splits it as on
        # Windows.
        kept = str(self.elsewhere / "kept")
        code = (
            "import importlib.util, json, os, subprocess, sys\n"
            f"s = importlib.util.spec_from_file_location('run_under_test', r'{SCRIPTS / 'run.py'}')\n"
            "m = importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
            "env = {k: v for k, v in os.environ.items() if k != 'BOOK_DRAGON_ROOT'}\n"
            f"env['PYTHONPATH'] = r'{kept}'\n"
            f"problem = m.set_up_trusted(r'{folder}', env)\n"
            "child = subprocess.run([sys.executable, '-c', \"import sys; "
            "m = sys.modules.get('sitecustomize'); print(getattr(m, '__file__', ''))\"],"
            " env=env, capture_output=True, encoding='utf-8')\n"
            "print(json.dumps({'problem': problem, 'pythonpath': env['PYTHONPATH'],"
            " 'fire_log': env.get('BOOK_DRAGON_FIRE_LOG'),"
            " 'child': child.stdout.strip(), 'child_err': child.stderr}))\n")
        done = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True,
                              encoding="utf-8", errors="replace", timeout=120,
                              cwd=str(self.elsewhere),
                              env={k: v for k, v in os.environ.items()
                                   if k not in ("PYTHONPATH", "BOOK_DRAGON_ROOT")})
        self.assertEqual(done.returncode, 0, done.stderr)
        result = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertIsNone(result["problem"])
        self.assertEqual(result["pythonpath"].split(os.pathsep),
                         [str(folder / "tripwire"), kept])
        self.assertTrue(os.path.samefile(result["child"],
                                         folder / "tripwire" / "sitecustomize.py"),
                        result["child_err"])
        # The fire log is the one codex_rules names for this run (the two agree).
        self.assertEqual(Path(result["fire_log"]),
                         codex_rules.fire_log_path(run.state["run_id"],
                                                   folder.parent.parent))
        self.assertEqual(run_py.FIRE_LOG_ENV, codex_rules.FIRE_LOG_ENV)
        self.assertEqual(run_py.RUNLOGS_PARENT, codex_rules.RUNLOGS_PARENT)

    def handed(self, run, **kwargs):
        out = io.StringIO()
        with redirect_stdout(out):
            code = run_py.hand_over(run, root=self.root, python=sys.executable,
                                    environ={"PATH": os.environ.get("PATH", "")},
                                    **kwargs)
        return code, out.getvalue(), runrecord.read_state(self.runs / run.state["run_id"])

    def ended_error(self, state, why):
        """The run ended `error` naming ``why``, with its report and failed entry."""
        self.assertEqual(state["status"], sr.ENDED)
        stop = state["stop_reasons"][-1]
        self.assertEqual(stop["reason"], sr.ERROR)
        self.assertIn(why, stop["evidence"]["hand_over"])
        self.assertIn(sr.ERROR, sr.RESUMABLE, "so the starter can resume it")
        self.assertTrue((self.runs / state["run_id"] / "report.md").is_file())
        last = self.log.read_text(encoding="utf-8").splitlines()[-1]
        self.assertIn("| Action: failed |", last)
        self.assertIn(f"Run {state['run_id']} ended error", last)

    def test_rejection_a_trusted_copy_that_cannot_be_started_ends_the_run_error(self):
        # Code review R19-3: the run is not left marked running.
        run, folder = self.started()

        def runner(argv, cwd=None, env=None):
            raise FileNotFoundError(2, "No such file", argv[0])
        code, out, state = self.handed(run, runner=runner)
        self.assertEqual(code, 1)
        self.ended_error(state, "could not be started (FileNotFoundError")
        self.assertIn(f"start.py --resume {run.state['run_id']}", out)

    def test_rejection_a_trusted_copy_that_exits_without_ending_the_run(self):
        for exit_code, expected in ((3, 3), (0, 1)):
            with self.subTest(exit_code=exit_code):
                self.setUp()
                run, folder = self.started()
                code, out, state = self.handed(
                    run, runner=lambda argv, cwd=None, env=None, c=exit_code:
                    subprocess.CompletedProcess(argv, c))
                self.assertEqual(code, expected)
                self.ended_error(state, f"exited {exit_code} without ending the run")

    def test_rejection_a_tripwire_that_cannot_be_armed_ends_the_run_error(self):
        run, folder = self.started()
        launched = []

        def arm(folder, root):
            raise RuntimeError("the tripwire is missing")
        code, out, state = self.handed(
            run, arm=arm, runner=lambda *a, **k: launched.append(a) or
            subprocess.CompletedProcess([], 0))
        self.assertEqual((code, launched), (1, []), "nothing is launched unarmed")
        self.ended_error(state, "the tripwire could not be armed")

    def test_negative_an_interrupt_is_left_to_the_trusted_copy(self):
        # Ctrl+C reaches both processes; the parent does not race the child to end
        # the run.
        run, folder = self.started()

        def runner(argv, cwd=None, env=None):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.handed(run, runner=runner)
        state = runrecord.read_state(self.runs / run.state["run_id"])
        self.assertEqual(state["status"], sr.RUNNING)

    def test_positive_the_command_line_start_hands_over_instead_of_advancing(self):
        handed = []
        code = run_py.main(["--run", "brief.md", "--builder", "claude", "--item", "demo",
                            "--git-dir", str(self.personal)],
                           root=self.root, log_path=self.log, deps=self.h.deps(),
                           runs_dir=self.runs, frozen=None,
                           handover=lambda run, root, wait: handed.append(run) or 5)
        self.assertEqual(code, 5)
        run, = handed
        self.assertEqual(run.state["step"], "preflight", "no step was taken here")
        self.assertEqual(self.h.builders, [])

    def test_rejection_advance_is_the_trusted_copy_mode_only(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            run_py.main(["--advance", "20261008-000000-ffff"], root=self.root,
                        log_path=self.log, runs_dir=self.runs, frozen=None)
        self.assertEqual(caught.exception.code, 2)

    def test_positive_the_layout_names_the_frozen_folder(self):
        run, folder = self.started()
        self.assertEqual(run_py.trusted_frozen_folder(frozen.trusted_run_py(folder)),
                         folder)
        self.assertIsNone(run_py.trusted_frozen_folder(SCRIPTS / "run.py"))
        odd = self.temp / "book-dragon-orchestration" / "not-a-run" / "trusted" / \
            "workflows" / "review-orchestration" / "scripts" / "run.py"
        self.assertIsNone(run_py.trusted_frozen_folder(odd))


class CommandLineTests(LoopCase):

    RUN = ("--run", "brief.md", "--builder", "claude")

    def call(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = run_py.main(list(argv), root=self.root, log_path=self.log,
                               deps=self.h.deps(), runs_dir=self.runs)
        return code, out.getvalue()

    def test_positive_run_exits_0_on_reviewed_clean(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        code, out = self.call(*self.RUN, "--item", "demo", "--git-dir",
                              str(self.personal))
        self.assertEqual(code, 0, out)
        self.assertIn("ended reviewed-clean", out)

    def test_rejection_a_refused_run_exits_3_and_other_ends_exit_1(self):
        self.h.readings = [reading(95)]
        code, out = self.call(*self.RUN, "--item", "demo", "--git-dir",
                              str(self.personal))
        self.assertEqual(code, 3)
        self.assertIn("REFUSED", out)
        self.h.builder_script = [reply("nothing")]
        code, _ = self.call(*self.RUN, "--item", "other", "--git-dir",
                            str(self.personal))
        self.assertEqual(code, 1)

    def test_rejection_stop_refuses_a_run_that_is_not_paused(self):
        self.h.builder_script = [reply("nothing")]
        self.call(*self.RUN, "--item", "demo", "--git-dir", str(self.personal))
        run_id = next(self.runs.iterdir()).name
        code, out = self.call("--stop", run_id)
        self.assertEqual(code, 3)
        self.assertIn("only a paused run", out)

    def usage_error(self, *argv):
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as caught:
            self.call(*argv)
        self.assertEqual(caught.exception.code, 2)
        return err.getvalue()

    def test_rejection_run_with_no_builder_is_a_usage_error(self):
        # The chunk (d) short plan, 11.1: no default; the AI asks, never picks.
        text = self.usage_error("--run", "brief.md", "--item", "demo", "--git-dir",
                                str(self.personal))
        self.assertIn("--run needs --builder claude or --builder codex", text)
        self.assertFalse(self.runs.exists() and any(self.runs.iterdir()))

    def test_rejection_a_builder_that_is_not_a_provider(self):
        text = self.usage_error("--run", "brief.md", "--builder", "gemini", "--item",
                                "demo", "--git-dir", str(self.personal))
        self.assertIn("invalid choice: 'gemini'", text)

    def test_rejection_builder_with_resume_stop_or_check(self):
        for argv in (("--resume", "20261004-000000-abcd"), ("--stop", "20261004-000000-abcd"),
                     ("--check-brief", "brief.md")):
            with self.subTest(mode=argv[0]):
                text = self.usage_error(*argv, "--builder", "codex")
                self.assertIn("--builder is given only with --run", text)

    def test_positive_the_named_builder_is_kept_in_the_record_for_a_resume(self):
        self.h.builder_script = [reply("nothing")]
        self.call(*self.RUN, "--item", "demo", "--git-dir", str(self.personal))
        run_dir = next(self.runs.iterdir())
        saved = json.loads((run_dir / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["roles"], {"builder": "claude", "reviewer": "codex"})
        again = loop.load(run_dir.name, root=self.root, deps=self.h.deps(),
                          log_path=self.log, runs_dir=self.runs)
        self.assertEqual(again.settings["roles"], saved["roles"])

    def test_rejection_preflight_with_no_builder_and_no_roles(self):
        bare = settings_mod.load_settings()
        with self.assertRaises(loop.RunRefused) as caught:
            loop.preflight("brief.md", "demo", str(self.personal), root=self.root,
                           deps=self.h.deps(), settings=bare)
        self.assertIn("no builder named", " ".join(caught.exception.args[0]))

    def test_positive_a_named_builder_overrides_nothing_else(self):
        ready = loop.preflight("brief.md", "demo", str(self.personal), root=self.root,
                               deps=self.h.deps(), settings=settings_mod.load_settings(),
                               builder="codex")
        self.assertEqual(ready["settings"]["roles"], {"builder": "codex", "reviewer": "claude"})
        self.assertEqual(ready["settings"]["models"], settings_mod.DEFAULT_MODELS)

    def bytes_of(self, run_dir):
        return ({p.name: p.read_bytes() for p in run_dir.iterdir() if p.is_file()},
                self.log.read_bytes())

    def test_positive_resume_dry_run_checks_and_changes_nothing(self):
        self.h.builder_script = [edit(self.root, "x\n"), reply(actions(("R1-1", "fixed")))]
        self.h.reviews = [[finding()]]
        settings = json.loads(json.dumps(self.settings))
        settings["round_caps"]["build_review"] = 1
        run = loop.start("brief.md", "demo", str(self.personal), root=self.root,
                         deps=self.h.deps(), log_path=self.log, runs_dir=self.runs,
                         settings=settings)
        asyncio.run(run.advance())
        before = self.bytes_of(run.run_dir)
        err = io.StringIO()
        with redirect_stderr(err):
            code, out = self.call("--resume", run.state["run_id"], "--rounds", "1",
                                  "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("[DRY RUN] would resume run", err.getvalue())
        self.assertIn("next step fix-R1", err.getvalue())
        self.assertEqual(self.bytes_of(run.run_dir), before)
        self.assertEqual(len(self.h.builders), 1)  # no session was started

    def test_rejection_resume_dry_run_still_refuses_what_resume_refuses(self):
        self.h.builder_script = [edit(self.root, "x\n")]
        self.h.reviews = [[]]
        run = self.start()
        code, out = self.call("--resume", run.state["run_id"], "--dry-run")
        self.assertEqual(code, 3)
        self.assertIn("cannot be resumed", out)

    def test_positive_stop_dry_run_changes_nothing(self):
        self.h.builder_script = [reply("nothing")]
        self.call(*self.RUN, "--item", "demo", "--git-dir", str(self.personal))
        run_dir = next(self.runs.iterdir())
        state = runrecord.read_state(run_dir)
        state.update(status="paused", pause="awaiting-user")
        runrecord.write_state(run_dir, state)
        before = self.bytes_of(run_dir)
        err = io.StringIO()
        with redirect_stderr(err):
            code, _ = self.call("--stop", run_dir.name, "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("[DRY RUN] would end run", err.getvalue())
        self.assertEqual(self.bytes_of(run_dir), before)

    def test_positive_stop_ends_a_paused_run_stopped_by_user(self):
        self.h.builder_script = [reply("nothing")]
        self.call(*self.RUN, "--item", "demo", "--git-dir", str(self.personal))
        run_dir = next(self.runs.iterdir())
        state = runrecord.read_state(run_dir)
        state.update(status="paused", pause="awaiting-user")
        runrecord.write_state(run_dir, state)
        code, out = self.call("--stop", run_dir.name)
        self.assertEqual(runrecord.read_state(run_dir)["stop_reasons"][-1]["reason"],
                         sr.STOPPED_BY_USER)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
