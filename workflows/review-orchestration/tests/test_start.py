#!/usr/bin/env python3
"""Tests for start.py, the starter (orchestrator isolation plan 7.0 items 2 and 3,
stage S6a), and for run.py sending a start, resume or stop straight from the project
to it.

The starter is called in-process against a fixture git repository, with its runner
replaced by a recorder, so what it would run and with which environment is read
directly; nothing it would run is started. Two tests run it as a subprocess: one
proves it imports nothing from the project, under the tripwire, and one that run.py
run straight from the project prints the starter command.

    python workflows/review-orchestration/tests/test_start.py
"""

import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent
SCRIPTS = WORKFLOW / "scripts"
PROJECT = WORKFLOW.parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


start = _load("start")
brief = _load("brief")
run_py = _load("run")

RUN_ID = "20261008-150000-ab12"
RUN_ARGS = ["--run", "brief.md", "--builder", "claude", "--item", "demo", "--git-dir",
            "C:/personal.git"]


_spec = importlib.util.spec_from_file_location(
    "fixture_git", Path(__file__).resolve().parent / "fixture_git.py")
fixture_git = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture_git)
FixtureGitError = fixture_git.FixtureGitError


def git(root, *args):
    """Run a git command for a test's fixture, through the suites' one fixture runner
    (`fixture_git.py`)."""
    return fixture_git.run_git(["git", "-C", str(root), *args], root,
                               shown=f"git {' '.join(args)}").stdout


class StarterCase(unittest.TestCase):
    """A committed fixture project with the orchestrator's folders, a close-out script
    and an ignored folder, a temp folder for frozen folders, and a recording runner."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        self.root = base / "project"
        self.temp = base / "temp"
        for rel in ("workflows/review-orchestration/scripts/run.py",
                    "workflows/review-orchestration/scripts/brief.py",
                    "workflows/sync-architecture/scripts/allowlist.py",
                    "workflows/close-out/scripts/run.py",
                    "workflows/close-out/tests/test_close_out.py", "brief.md"):
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"# committed\n")
        (self.root / ".gitignore").write_bytes(b"memory/\n")
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "t@example.invalid")
        git(self.root, "config", "user.name", "t")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "start")
        self.python = base / "venv-python.exe"
        self.python.write_bytes(b"")
        self.calls = []

    def runner(self, command, cwd=None, env=None):
        self.calls.append((command, cwd, env))
        return subprocess.CompletedProcess(command, 0)

    def main(self, argv, environ=None):
        return start.main(argv, root=self.root, temp_root=self.temp,
                          python=self.python, runner=self.runner,
                          environ=environ if environ is not None else {"PATH": "x"})

    def refused(self, argv, reason, environ=None):
        from contextlib import redirect_stdout
        import io
        out = io.StringIO()
        with redirect_stdout(out):
            code = self.main(argv, environ)
        self.assertEqual(code, start.REFUSED, out.getvalue())
        self.assertIn(reason, out.getvalue())
        self.assertEqual(self.calls, [], "nothing is run")
        return out.getvalue()

    def handed(self, argv):
        self.assertEqual(self.main(argv), 0)
        (command, cwd, env), = self.calls
        self.calls = []
        self.assertEqual(cwd, str(self.root))
        self.assertEqual(env[start.STARTER_MARKER], "1")
        return command

    def make_frozen(self, run_id=RUN_ID, project=None):
        folder = self.temp / "book-dragon-orchestration" / run_id
        trusted = folder.joinpath(*start.TRUSTED_RUN_PY)
        trusted.parent.mkdir(parents=True)
        trusted.write_bytes(b"# trusted\n")
        (folder / "hook.json").write_text(
            json.dumps({"project_root": str(project or self.root)}), encoding="utf-8",
            newline="\n")
        return trusted


class StartTests(StarterCase):

    def test_positive_a_committed_tree_hands_every_start_mode_to_run_py(self):
        for argv in (RUN_ARGS, RUN_ARGS + ["--dry-run"], ["--check-brief", "brief.md"]):
            with self.subTest(mode=argv[0], dry=("--dry-run" in argv)):
                command = self.handed(argv)
                self.assertEqual(command, [str(self.python), str(
                    self.root / "workflows/review-orchestration/scripts/run.py"), *argv])

    def test_rejection_any_uncommitted_change_refuses_a_start(self):
        cases = {
            "a side effect in the live run.py":
                "workflows/review-orchestration/scripts/run.py",
            "a side effect in brief.py": "workflows/review-orchestration/scripts/brief.py",
            "a side effect in the close-out verifier": "workflows/close-out/scripts/run.py",
            "a side effect in a test the verifier selects":
                "workflows/close-out/tests/test_close_out.py",
        }
        for label, rel in cases.items():
            for dry in (False, True):
                with self.subTest(label, dry=dry):
                    path = self.root / rel
                    kept = path.read_bytes()
                    path.write_bytes(b"raise SystemExit('planted')\n")
                    try:
                        text = self.refused(RUN_ARGS + (["--dry-run"] if dry else []),
                                            "uncommitted changes")
                    finally:
                        path.write_bytes(kept)
                    self.assertIn(rel, text, "the file is named")

    def test_rejection_an_untracked_file_refuses_a_start(self):
        (self.root / "workflows/review-orchestration/scripts/new.py").write_bytes(b"x\n")
        self.refused(RUN_ARGS, "workflows/review-orchestration/scripts/new.py")

    def test_negative_a_gitignored_change_does_not_refuse(self):
        (self.root / "memory").mkdir()
        (self.root / "memory" / "note.md").write_bytes(b"personal\n")
        self.handed(RUN_ARGS)

    def test_positive_a_brief_check_looks_only_at_the_folders_the_checker_loads(self):
        (self.root / "workflows/close-out/scripts/run.py").write_bytes(b"changed\n")
        self.handed(["--check-brief", "brief.md"])
        for rel in ("workflows/review-orchestration/scripts/brief.py",
                    "workflows/sync-architecture/scripts/new.py"):
            with self.subTest(rel):
                path = self.root / rel
                kept = path.read_bytes() if path.exists() else None
                path.write_bytes(b"planted\n")
                try:
                    self.refused(["--check-brief", "brief.md"], rel)
                finally:
                    if kept is None:
                        path.unlink()
                    else:
                        path.write_bytes(kept)

    def test_rejection_a_git_that_cannot_answer(self):
        # A folder that is not a repository: git status exits non-zero.
        self.root = self.root.parent / "not-a-repository"
        self.root.mkdir()
        self.refused(RUN_ARGS, "git status failed")

    def test_rejection_the_mode_must_come_first_and_be_known(self):
        self.refused([], "usage")
        self.refused(["--builder", "claude", "--run", "brief.md"], "first argument")
        self.refused(["--advance", RUN_ID], "first argument")

    def test_rejection_a_root_override_naming_another_project(self):
        # Code review R22-1: the starter checked its own project, so a run or brief
        # check may not be pointed at another one, committed or not.
        other = self.root.parent / "other-project"
        other.mkdir()
        (other / "AGENTS.md").write_bytes(b"# agents\n")
        for argv in (RUN_ARGS, RUN_ARGS + ["--dry-run"], ["--check-brief", "brief.md"]):
            for value in (str(other), ""):
                with self.subTest(mode=argv[0], value=value):
                    text = self.refused(argv, "BOOK_DRAGON_ROOT",
                                        environ={"PATH": "x", "BOOK_DRAGON_ROOT": value})
                    self.assertIn(self.root.as_posix(), text)

    def test_negative_a_root_override_naming_this_project(self):
        for value in (str(self.root), str(self.root) + os.sep, str(self.root).upper()
                      if os.name == "nt" else str(self.root)):
            with self.subTest(value=value):
                self.assertEqual(self.main(RUN_ARGS, {"PATH": "x",
                                                      "BOOK_DRAGON_ROOT": value}), 0)
                (command, cwd, env), = self.calls
                self.calls = []
                self.assertEqual(env["BOOK_DRAGON_ROOT"], value)

    def test_rejection_a_missing_project_interpreter(self):
        self.python.unlink()
        self.refused(RUN_ARGS, "interpreter")


class ResumeStopTests(StarterCase):

    def test_positive_resume_and_stop_run_the_trusted_copy_with_their_options(self):
        trusted = self.make_frozen()
        # Uncommitted builder edits are expected at a resume: no git check.
        (self.root / "workflows/review-orchestration/scripts/run.py").write_bytes(b"x\n")
        for argv in (["--resume", RUN_ID], ["--resume", RUN_ID, "--rounds", "2"],
                     ["--resume", RUN_ID, "--wait"], ["--resume", RUN_ID, "--dry-run"],
                     ["--stop", RUN_ID], ["--stop", RUN_ID, "--dry-run"],
                     ["--resume", RUN_ID, "--no-such-option"]):
            with self.subTest(argv=argv):
                self.assertEqual(self.handed(argv), [str(self.python), str(trusted), *argv])

    def test_positive_resume_and_stop_ask_for_no_bytecode_and_a_start_does_not(self):
        # Code review R20-1: the trusted copy runs from the frozen folder, where no
        # bytecode cache may be written; the live run.py at a start is not there.
        self.make_frozen()
        for argv, wanted in ((["--resume", RUN_ID], "1"), (["--stop", RUN_ID], "1"),
                             (RUN_ARGS, None)):
            with self.subTest(mode=argv[0]):
                self.assertEqual(self.main(argv), 0)
                (command, cwd, env), = self.calls
                self.calls = []
                self.assertEqual(env.get("PYTHONDONTWRITEBYTECODE"), wanted)

    def test_negative_resume_and_stop_leave_the_root_to_the_trusted_copy(self):
        # The trusted copy refuses a BOOK_DRAGON_ROOT that disagrees with its
        # hook.json (tested in test_loop.py), so the starter passes it on.
        self.make_frozen()
        other = str(self.root.parent / "elsewhere")
        for argv in (["--resume", RUN_ID], ["--stop", RUN_ID]):
            with self.subTest(mode=argv[0]):
                self.assertEqual(self.main(argv, {"PATH": "x", "BOOK_DRAGON_ROOT": other}), 0)
                (command, cwd, env), = self.calls
                self.calls = []
                self.assertEqual(env["BOOK_DRAGON_ROOT"], other)

    def test_rejection_a_run_id_not_in_the_run_record_form(self):
        for run_id in ("../x", "a/b", "20261008-150000-AB12", "20261008-150000-ab12/..",
                       ""):
            with self.subTest(run_id=run_id):
                self.refused(["--resume", run_id], "not a run id")
        self.refused(["--stop"], "needs a run id")

    def test_rejection_a_missing_frozen_folder_or_trusted_copy(self):
        self.refused(["--resume", RUN_ID], "no frozen folder")
        trusted = self.make_frozen()
        trusted.unlink()
        self.refused(["--stop", RUN_ID], "no frozen folder")
        trusted.write_bytes(b"# trusted\n")
        (trusted.parents[4] / "hook.json").unlink()
        self.refused(["--stop", RUN_ID], "no frozen folder")

    def test_rejection_a_run_of_another_project(self):
        other = self.root.parent / "other"
        other.mkdir()
        self.make_frozen(project=other)
        self.refused(["--resume", RUN_ID], "belongs to another project")

    def test_rejection_an_unreadable_hook_json(self):
        trusted = self.make_frozen()
        (trusted.parents[4] / "hook.json").write_bytes(b"{")
        self.refused(["--resume", RUN_ID], "cannot be read")


class StandaloneTests(unittest.TestCase):

    def test_positive_the_starter_imports_only_the_standard_library(self):
        tree = ast.parse((SCRIPTS / "start.py").read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add((node.module or "").split(".")[0])
        self.assertTrue(names)
        self.assertEqual(names - set(sys.stdlib_module_names), set())

    def test_positive_the_starter_runs_under_the_tripwire_over_the_project(self):
        # A copy outside the project, run with the tripwire armed over this project:
        # any import of a project module would fail it.
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        copy = base / "a" / "b" / "c" / "start.py"
        copy.parent.mkdir(parents=True)
        shutil.copyfile(SCRIPTS / "start.py", copy)
        code = ("import importlib.util, runpy, sys\n"
                f"s = importlib.util.spec_from_file_location('tw', r'{SCRIPTS / 'tripwire.py'}')\n"
                "tw = importlib.util.module_from_spec(s); s.loader.exec_module(tw)\n"
                f"tw.install(r'{PROJECT}')\n"
                "sys.argv = ['start.py', '--no-such-mode']\n"
                f"runpy.run_path(r'{copy}', run_name='__main__')\n")
        environ = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        done = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True,
                              encoding="utf-8", errors="replace", timeout=60,
                              cwd=str(base), env=environ)
        self.assertEqual(done.returncode, start.REFUSED, done.stderr)
        self.assertIn("first argument", done.stdout)
        self.assertNotIn("TripwireError", done.stderr)

    def test_positive_the_run_id_form_is_the_run_records(self):
        runrecord = _load("runrecord")
        self.assertEqual(start.RUN_ID.pattern, runrecord.RUN_ID.pattern)

    def test_positive_the_starter_names_itself_as_brief_does(self):
        self.assertEqual(start.ROOT, PROJECT)
        self.assertEqual((SCRIPTS / "start.py").resolve(),
                         (PROJECT / brief.STARTER_PATH).resolve())
        self.assertEqual(start.STARTER_MARKER, run_py.STARTER_MARKER)
        # run.py restates the path, since nothing of the project is imported before its
        # gate (code review R18-2).
        self.assertEqual(run_py.STARTER_PATH, brief.STARTER_PATH)


MODES = {"a brief check": ["--check-brief", "brief.md"], "a start": RUN_ARGS,
         "a dry start": RUN_ARGS + ["--dry-run"], "a resume": ["--resume", RUN_ID],
         "a stop": ["--stop", RUN_ID], "a resume, one argument": ["--resume=" + RUN_ID]}


class RunPySendsToTheStarterTests(unittest.TestCase):
    """Plan 7.0 item 2 and code review R18-2: run.py run straight from the project for
    any of the four modes prints the starter command and loads nothing of the project
    first."""

    def test_rejection_every_mode_straight_from_the_project(self):
        for label, argv in MODES.items():
            with self.subTest(label):
                message = run_py.needs_starter(argv, environ={}, frozen=None)
                self.assertIn("python workflows/review-orchestration/scripts/start.py "
                              + " ".join(argv), message)

    def test_negative_the_starter_the_trusted_copy_and_no_mode(self):
        self.assertIsNone(run_py.needs_starter(RUN_ARGS, frozen=None,
                                               environ={run_py.STARTER_MARKER: "1"}))
        self.assertIsNone(run_py.needs_starter(["--check-brief", "x.md"], frozen=None,
                                               environ={run_py.STARTER_MARKER: "1"}))
        self.assertIsNone(run_py.needs_starter(["--resume", RUN_ID], environ={},
                                               frozen=Path("x") / RUN_ID))
        self.assertIsNone(run_py.needs_starter(["--help"], environ={}, frozen=None))

    def planted_copy(self):
        """A copy of run.py beside a brief.py whose import writes a marker file."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        scripts = base / "workflows" / "review-orchestration" / "scripts"
        scripts.mkdir(parents=True)
        shutil.copyfile(SCRIPTS / "run.py", scripts / "run.py")
        marker = base / "fired"
        (scripts / "brief.py").write_text(f"open(r'{marker}', 'w').close()\n",
                                          encoding="utf-8", newline="\n")
        return scripts / "run.py", marker, base

    def direct(self, script, argv, cwd, marked=False):
        environ = {k: v for k, v in os.environ.items() if k != run_py.STARTER_MARKER}
        if marked:
            environ[run_py.STARTER_MARKER] = "1"
        return subprocess.run([sys.executable, str(script), *argv], capture_output=True,
                              encoding="utf-8", errors="replace", timeout=60,
                              cwd=str(cwd), env=environ)

    def test_rejection_a_planted_brief_py_never_runs_before_the_redirect(self):
        script, marker, base = self.planted_copy()
        for label, argv in MODES.items():
            with self.subTest(label):
                done = self.direct(script, argv, base)
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertIn("python workflows/review-orchestration/scripts/start.py",
                              done.stdout)
                self.assertFalse(marker.exists(), "the live brief.py ran first")

    def test_positive_control_the_planted_brief_py_runs_when_the_gate_lets_it(self):
        # Without this, the test above could pass because the plant never runs at all.
        script, marker, base = self.planted_copy()
        self.direct(script, ["--check-brief", "brief.md"], base, marked=True)
        self.assertTrue(marker.exists())

    def test_positive_run_py_from_the_command_line_prints_the_starter(self):
        done = self.direct(SCRIPTS / "run.py", ["--stop", RUN_ID], PROJECT)
        self.assertEqual(done.returncode, 2)
        self.assertIn(f"python workflows/review-orchestration/scripts/start.py --stop "
                      f"{RUN_ID}", done.stdout)


STUB_BRIEF = """\
import os
from pathlib import Path
PROJECT_ROOT = Path(os.environ["STUB_ROOT"])
LOG_PATH = PROJECT_ROOT / "LOG.md"
def _reconfigure_streams():
    pass
def run_check(*args, **kwargs):
    return 0
"""

STUB_LOOP = """\
import json, os
from pathlib import Path
SHARED = Path(os.environ["STUB_SHARED"])
class RunRefused(Exception):
    pass
class sr:
    RUNNING = "running"
    ERROR = "error"
class Run:
    def __init__(self, state):
        self.state, self.root, self.deps = state, Path(os.environ["STUB_ROOT"]), None
        self.log_path, self.run_dir = None, SHARED / "runs" / state["run_id"]
    async def advance(self):
        (SHARED / "advanced-in-the-live-process").write_text("x", encoding="utf-8")
def start(*args, **kwargs):
    return Run({"run_id": os.environ["STUB_RUN_ID"], "status": "running",
                "frozen_dir": os.environ["STUB_FROZEN"], "step": "preflight"})
def load(run_id, **kwargs):
    return Run(json.loads((SHARED / "state.json").read_text(encoding="utf-8")))
def pause_commands(run_id):
    return ""
"""

STUB_RUNRECORD = "RUNS_DIR = None\n"

STUB_FROZEN = """\
from pathlib import Path
def trusted_run_py(folder):
    return Path(folder) / "trusted" / "workflows" / "review-orchestration" / "scripts" / "run.py"
"""

# The trusted copy the start hands over to: it records how it was started and ends
# the run, as the real one does when it takes the run's steps.
STUB_TRUSTED = """\
import json, os, sys
from pathlib import Path
shared = Path(os.environ["STUB_SHARED"])
(shared / "launched.json").write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
(shared / "state.json").write_text(json.dumps({"run_id": sys.argv[2], "status": "ended",
                                               "frozen_dir": "", "step": "review-R1"}),
                                   encoding="utf-8")
sys.exit(5)
"""


class CommandLineStartTests(unittest.TestCase):
    """Code review R21-2: a start entered as the starter enters it, through run.py's
    own ``__main__`` and the real ``hand_over``, hands the run to the trusted copy's
    ``--advance`` and takes no step in the live process. The loop, the run record and
    the frozen-folder module are stand-ins, so no preflight, provider or git runs."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        self.root = base / "project"
        scripts = self.root / "workflows" / "review-orchestration" / "scripts"
        scripts.mkdir(parents=True)
        shutil.copyfile(SCRIPTS / "run.py", scripts / "run.py")
        for name, text in (("brief", STUB_BRIEF), ("loop", STUB_LOOP),
                           ("runrecord", STUB_RUNRECORD), ("frozen", STUB_FROZEN)):
            (scripts / f"{name}.py").write_text(text, encoding="utf-8", newline="\n")
        self.script = scripts / "run.py"
        self.frozen = base / "temp" / "book-dragon-orchestration" / RUN_ID
        trusted = self.frozen / "trusted" / "workflows" / "review-orchestration" / "scripts"
        trusted.mkdir(parents=True)
        (trusted / "run.py").write_text(STUB_TRUSTED, encoding="utf-8", newline="\n")
        (self.frozen / "tripwire").mkdir()
        shutil.copyfile(SCRIPTS / "tripwire.py", self.frozen / "tripwire" / "sitecustomize.py")
        self.shared = base / "shared"
        self.shared.mkdir()

    def start(self, *extra):
        environ = {k: v for k, v in os.environ.items()
                   if k not in ("BOOK_DRAGON_ROOT", "PYTHONPATH")}
        environ.update({run_py.STARTER_MARKER: "1", run_py.REEXEC_MARKER: "1",
                        "STUB_ROOT": str(self.root), "STUB_SHARED": str(self.shared),
                        "STUB_FROZEN": str(self.frozen), "STUB_RUN_ID": RUN_ID})
        return subprocess.run([sys.executable, str(self.script), *RUN_ARGS, *extra],
                              capture_output=True, encoding="utf-8", errors="replace",
                              timeout=120, cwd=str(self.root), env=environ)

    def test_positive_a_command_line_start_hands_over_to_advance(self):
        done = self.start("--wait")
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        self.assertEqual(json.loads((self.shared / "launched.json").read_text(encoding="utf-8")),
                         ["--advance", RUN_ID, "--wait"])
        self.assertFalse((self.shared / "advanced-in-the-live-process").exists(),
                         "the live process took a step")


REFUSED_OBJECT = ("error: unable to write file .git/objects/63/30d46d: Permission denied\n"
                  "error: x.py: failed to insert into database\nfatal: adding files failed\n")


class FixtureRetryTests(unittest.TestCase):
    """The suites' shared fixture runner, `fixture_git.run_git` (after code review R22:
    the intermittent fixture failure named itself as git refused permission to write
    one of its own object files). Driven with a scripted runner and no real sleep."""

    def run_with(self, answers):
        calls, sleeps = [], []

        def runner(command, **kwargs):
            calls.append(command)
            code, err = answers.pop(0)
            return subprocess.CompletedProcess(command, code, "", err)
        try:
            done = fixture_git.run_git(["git", "add", "-A"], "C:/fixture", shown="git add -A",
                                       runner=runner, sleep=sleeps.append)
        except FixtureGitError as exc:
            return exc, calls, sleeps
        return done, calls, sleeps

    def test_positive_an_object_write_refusal_is_retried_until_it_passes(self):
        done, calls, sleeps = self.run_with([(128, REFUSED_OBJECT), (128, REFUSED_OBJECT),
                                             (0, "")])
        self.assertEqual(done.returncode, 0)
        self.assertEqual((len(calls), sleeps), (3, [0.5, 0.5]))

    def test_rejection_a_refusal_every_time_fails_saying_how_often_it_was_tried(self):
        error, calls, sleeps = self.run_with([(128, REFUSED_OBJECT)] * 3)
        self.assertIsInstance(error, FixtureGitError)
        self.assertIn("`git add -A` exited 128 after 3 attempts", str(error))
        self.assertIn("Permission denied", str(error))
        self.assertEqual(len(calls), 3)

    def test_negative_any_other_failure_is_not_retried(self):
        for err in ("fatal: not a git repository", "error: Permission denied\n",
                    "error: unable to write file x: No space left on device\n"):
            with self.subTest(err=err):
                error, calls, sleeps = self.run_with([(128, err), (0, "")])
                self.assertIsInstance(error, FixtureGitError)
                self.assertNotIn("attempts", str(error))
                self.assertEqual((len(calls), sleeps), (1, []))


class FixtureHelperTests(unittest.TestCase):
    """The suite's own fixture helper (code review R21-1)."""

    def test_rejection_a_failing_git_command_names_gits_own_error(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        with self.assertRaises(FixtureGitError) as caught:
            git(Path(folder.name), "add", "-A")
        self.assertIn("`git add -A` exited 128", str(caught.exception))
        self.assertIn("not a git repository", str(caught.exception).lower())


if __name__ == "__main__":
    unittest.main()
