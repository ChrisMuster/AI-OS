#!/usr/bin/env python3
"""Tests for frozen.py (a run's frozen folder) and tripwire.py (orchestrator isolation
plan 6 item 1, 7.0, 7.1 item 5 and 7.8; stage S6a).

``frozen.setup`` is run for real against a fixture git repository holding this working
tree's tracked files of every workflow ``trusted/`` takes, committed, so the archive is
git's own; a change made after the commit shows what is the commit's and what is the
live tree's. The tripwire is tested in child processes, since an audit hook cannot be
removed from the process that installs it. Expected values are stated by the tests.

    python workflows/review-orchestration/tests/test_frozen.py
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

WORKFLOW = Path(__file__).resolve().parent.parent
SCRIPTS = WORKFLOW / "scripts"
PROJECT = WORKFLOW.parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


brief = _load("brief")
approver = _load("approver")
codex_rules = _load("codex_rules")
checks = _load("checks")
container = _load("container")
frozen = _load("frozen")

RUN_ID = "20261008-140000-ab12"

# What trusted/ must hold, written out here rather than read from frozen.TRUSTED_CODE,
# so a folder dropped from that tuple is still expected (code review R22-2): the four
# host checks (plan 4.4), the orchestrator and the sync classification's scripts (7.0
# item 1), and the rule hooks (7.0 item 6).
REQUIRED_TRUSTED = ("workflows/audit", "workflows/doc-sync-guard",
                    "workflows/personal-data-guard", "workflows/link-check",
                    "workflows/review-orchestration", "workflows/sync-architecture/scripts",
                    "workflows/rule-hooks")


_spec = importlib.util.spec_from_file_location(
    "fixture_git", Path(__file__).resolve().parent / "fixture_git.py")
fixture_git = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture_git)
FixtureGitError = fixture_git.FixtureGitError


def git(root, *args):
    """Run a git command for a test's fixture and return its output, through the
    suites' one fixture runner (`fixture_git.py`: git's own text on a failure, an
    object-write refusal retried)."""
    return fixture_git.run_git(["git", "-C", str(root), *args], root,
                               shown=f"git {' '.join(args)}").stdout


def tracked(paths):
    """This working tree's tracked and unignored files under ``paths``, project-
    relative: the files a commit of the tree would hold."""
    out = git(PROJECT, "ls-files", "-c", "-o", "--exclude-standard", "-z", "--", *paths)
    return [p for p in out.split("\0") if p]


class FixtureCase(unittest.TestCase):
    """A git repository holding every workflow trusted/ takes, committed, an ignored
    folder, an untracked .env and a tracked .env.example."""

    @classmethod
    def setUpClass(cls):
        cls.files = tracked(REQUIRED_TRUSTED)

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        self.root = base / "project"
        self.temp = base / "temp"
        for rel in self.files:
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((PROJECT / rel).read_bytes())
        (self.root / "AGENTS.md").write_bytes(b"# agents\n")
        (self.root / ".gitignore").write_bytes(b"memory/\n")
        (self.root / ".env.example").write_bytes(b"KEY=\n")
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "t@example.invalid")
        git(self.root, "config", "user.name", "t")
        git(self.root, "config", "core.autocrlf", "false")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "start")
        self.head = git(self.root, "rev-parse", "HEAD").strip()
        (self.root / "memory").mkdir()
        (self.root / "memory" / "note.md").write_bytes(b"personal\n")
        (self.root / ".env").write_bytes(b"SECRET=1\n")

    def setup_run(self, **changes):
        options = {"public_head": self.head, "edit_paths": ["notes/"], "builder": "codex",
                   "temp_root": self.temp}
        options.update(changes)
        return frozen.setup(self.root, RUN_ID, **options)


class SetupTests(FixtureCase):

    def test_positive_the_folder_holds_the_commits_code_and_the_runs_settings(self):
        # A live edit after the commit is the builder's kind of change: none of it may
        # reach the frozen folder.
        live = self.root / "workflows" / "review-orchestration" / "scripts" / "loop.py"
        committed = live.read_bytes()
        live.write_bytes(committed + b"\nraise SystemExit('live edit')\n")
        hook_live = self.root / "workflows" / "review-orchestration" / "scripts" / \
            "codex_hook.py"
        hook_committed = hook_live.read_bytes()
        hook_live.write_bytes(b"# a builder's version\n")
        folder = self.setup_run()
        self.assertEqual(folder, self.temp / "book-dragon-orchestration" / RUN_ID)
        trusted = folder / "trusted"
        self.assertEqual(sorted(frozen.TRUSTED_CODE), sorted(REQUIRED_TRUSTED))
        for workflow in REQUIRED_TRUSTED:
            self.assertTrue((trusted / workflow).is_dir(), workflow)
        self.assertEqual((trusted / "workflows/review-orchestration/scripts/loop.py")
                         .read_bytes(), committed)
        self.assertEqual((folder / "codex_hook.py").read_bytes(), hook_committed,
                         "the frozen modules are the commit's, from trusted/")
        for name in codex_rules.FROZEN_MODULES:
            self.assertEqual((folder / name).read_bytes(),
                             (trusted / "workflows/review-orchestration/scripts" / name)
                             .read_bytes(), name)
        for name in codex_rules.FROZEN_LISTS:
            self.assertEqual((folder / name).read_bytes(),
                             (trusted / "workflows/review-orchestration/config" / name)
                             .read_bytes(), name)
        self.assertEqual((folder / "tripwire" / "sitecustomize.py").read_bytes(),
                         (trusted / "workflows/review-orchestration/scripts/tripwire.py")
                         .read_bytes())
        self.assertEqual(json.loads((folder / "hook.json").read_text(encoding="utf-8")),
                         {"run_id": RUN_ID, "edit_paths": ["notes/"],
                          "project_root": str(self.root), "builder_provider": "codex",
                          "public_head": self.head})
        floor = container.read_start_ignored(folder / "start-ignored.txt")
        self.assertIn("memory/", floor)
        self.assertIn(".env", floor)
        self.assertNotIn(".env.example", floor)
        # No gitignored file of the project is in trusted/.
        self.assertEqual([p for p in trusted.rglob("*") if p.name in ("LOG.md", ".env")],
                         [])

    def test_positive_a_claude_run_gets_the_same_folder(self):
        folder = self.setup_run(builder="claude")
        self.assertEqual(json.loads((folder / "hook.json").read_text(encoding="utf-8"))
                         ["builder_provider"], "claude")
        self.assertTrue((folder / "check_server.py").is_file())

    def test_rejection_a_folder_that_already_exists_is_left_alone(self):
        folder = self.temp / "book-dragon-orchestration" / RUN_ID
        folder.mkdir(parents=True)
        (folder / "codex_hook.py").write_bytes(b"someone else's\n")
        with self.assertRaisesRegex(frozen.FrozenError, "already exists"):
            self.setup_run()
        self.assertEqual([p.name for p in folder.iterdir()], ["codex_hook.py"])
        self.assertEqual((folder / "codex_hook.py").read_bytes(), b"someone else's\n")

    @staticmethod
    def refuse_mkdir_of(refused):
        """A patch of Path.mkdir raising PermissionError for ``refused``, as a locked
        or unwritable temp folder would, and working as usual for every other path."""
        original = Path.mkdir

        def mkdir(path, *args, **kwargs):
            if Path(path) == refused:
                raise PermissionError(13, "Access is denied", str(path))
            return original(path, *args, **kwargs)
        return mock.patch.object(Path, "mkdir", mkdir)

    def test_rejection_an_unwritable_temp_folder_is_a_refusal_naming_it(self):
        # Code review R19-2: a failure other than "exists" at either step.
        parent = self.temp / "book-dragon-orchestration"
        for label, refused, reason in (("the parent", parent, "parent"),
                                       ("the run's folder", parent / RUN_ID,
                                        "frozen folder")):
            with self.subTest(label):
                with self.refuse_mkdir_of(refused), \
                        self.assertRaises(frozen.FrozenError) as caught:
                    self.setup_run()
                text = str(caught.exception)
                self.assertIn(reason, text)
                self.assertIn("cannot be made", text)
                self.assertIn("Access is denied", text)
                self.assertFalse((parent / RUN_ID).exists(), "nothing left behind")

    def test_rejection_a_commit_without_any_one_required_folder_leaves_nothing(self):
        # Each of the seven, not one (code review R22-2). One fixture, each folder
        # dropped on its own branch of the start commit, so the work tree is built once.
        start_branch = git(self.root, "rev-parse", "--abbrev-ref", "HEAD").strip()
        for n, workflow in enumerate(REQUIRED_TRUSTED):
            with self.subTest(workflow):
                git(self.root, "checkout", "-q", "-b", f"drop-{n}", self.head)
                git(self.root, "rm", "-rq", workflow)
                git(self.root, "commit", "-qm", "drop")
                head = git(self.root, "rev-parse", "HEAD").strip()
                git(self.root, "checkout", "-q", start_branch)
                with self.assertRaises(frozen.FrozenError) as caught:
                    self.setup_run(public_head=head)
                self.assertIn(workflow, str(caught.exception))
                self.assertFalse((self.temp / "book-dragon-orchestration" / RUN_ID).exists())

    def test_rejection_a_commit_that_does_not_exist_leaves_nothing(self):
        with self.assertRaises(frozen.FrozenError):
            self.setup_run(public_head="f" * 40)
        self.assertFalse((self.temp / "book-dragon-orchestration" / RUN_ID).exists())

    def test_rejection_a_malformed_run_id(self):
        for run_id in ("../x", "20261008-140000-AB12", "x"):
            with self.subTest(run_id=run_id):
                with self.assertRaisesRegex(frozen.FrozenError, "not a run id"):
                    frozen.setup(self.root, run_id, public_head=self.head,
                                 edit_paths=["notes/"], builder="codex",
                                 temp_root=self.temp)
        self.assertFalse((self.temp / "book-dragon-orchestration").exists())

    def test_positive_remove_clears_read_only_files(self):
        folder = self.setup_run()
        os.chmod(folder / "hook.json", 0o444)
        frozen.remove(folder)
        self.assertFalse(folder.exists())

    def test_positive_the_trusted_run_py_path(self):
        folder = self.setup_run()
        self.assertTrue(frozen.trusted_run_py(folder).is_file())
        self.assertEqual(frozen.trusted_run_py(folder),
                         folder / "trusted/workflows/review-orchestration/scripts/run.py")


class TripwireTests(unittest.TestCase):
    """Each check runs in a child process with the tripwire installed over a fixture
    project root."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        self.root = base / "project"
        (self.root / "pkg").mkdir(parents=True)
        (self.root / ".venv" / "lib").mkdir(parents=True)
        self.marker = base / "ran"
        for path in (self.root / "pkg" / "live.py", self.root / ".venv" / "lib" / "dep.py",
                     base / "outside.py"):
            path.write_text(f"open(r'{self.marker}-{path.stem}', 'w').close()\n",
                            encoding="utf-8", newline="\n")
        self.base = base
        self.frozen = base / "frozen"
        (self.frozen / "tripwire").mkdir(parents=True)
        (self.frozen / "tripwire" / "sitecustomize.py").write_bytes(
            (SCRIPTS / "tripwire.py").read_bytes())
        (self.frozen / "hook.json").write_text(json.dumps({"project_root": str(self.root)}),
                                               encoding="utf-8", newline="\n")

    def child(self, code, env=None):
        prelude = ("import importlib.util, sys\n"
                   f"s = importlib.util.spec_from_file_location('tw', r'{SCRIPTS / 'tripwire.py'}')\n"
                   "tw = importlib.util.module_from_spec(s); s.loader.exec_module(tw)\n"
                   f"tw.install(r'{self.root}')\n")
        environ = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        environ.update(env or {})
        return subprocess.run([sys.executable, "-B", "-c", prelude + code],
                              capture_output=True, encoding="utf-8", errors="replace",
                              timeout=60, cwd=str(self.base), env=environ)

    def ran(self):
        return sorted(p.name.split("-", 1)[1] for p in self.base.glob("ran-*"))

    def test_rejection_importing_a_live_file_fails_and_never_runs_it(self):
        done = self.child(f"sys.path.insert(0, r'{self.root / 'pkg'}')\nimport live\n")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("TripwireError", done.stderr)
        self.assertEqual(self.ran(), [])

    def test_rejection_exec_module_of_a_live_file_fails(self):
        done = self.child(
            f"s2 = importlib.util.spec_from_file_location('m', r'{self.root / 'pkg' / 'live.py'}')\n"
            "s2.loader.exec_module(importlib.util.module_from_spec(s2))\n")
        self.assertIn("TripwireError", done.stderr)
        self.assertEqual(self.ran(), [])

    def test_rejection_a_live_module_loaded_from_its_bytecode_cache_fails(self):
        # With a .pyc already present, an import compiles nothing: only the exec
        # event fires, so it alone must trip.
        subprocess.run([sys.executable, "-m", "py_compile", str(self.root / "pkg" / "live.py")],
                       check=True, capture_output=True, cwd=str(self.base))
        self.assertTrue(list((self.root / "pkg" / "__pycache__").glob("live.*.pyc")))
        done = self.child(f"sys.path.insert(0, r'{self.root / 'pkg'}')\nimport live\n")
        self.assertIn("TripwireError", done.stderr)
        self.assertEqual(self.ran(), [])

    def test_rejection_compiling_a_live_file_by_name_fails(self):
        done = self.child(f"compile('x = 1', r'{self.root / 'pkg' / 'other.py'}', 'exec')\n")
        self.assertIn("TripwireError", done.stderr)

    def test_negative_reading_a_live_file_as_data_is_allowed(self):
        done = self.child(f"print(len(open(r'{self.root / 'pkg' / 'live.py'}').read()))\n")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.ran(), [])

    def test_negative_the_venv_and_files_outside_the_project_run(self):
        done = self.child(f"sys.path[:0] = [r'{self.root / '.venv' / 'lib'}', r'{self.base}']\n"
                          "import dep, outside\n")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.ran(), ["dep", "outside"])

    def test_negative_a_name_that_is_not_a_python_file_is_not_checked(self):
        done = self.child(f"exec(compile('y = 2', r'{self.root / 'pkg' / 'data.txt'}', 'exec'))\n"
                          "exec('z = 3')\n")
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_positive_as_sitecustomize_it_arms_itself_from_hook_json(self):
        # S7 puts the folder on PYTHONPATH (decision 29); the file already works so.
        environ = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        environ["PYTHONPATH"] = str(self.frozen / "tripwire")
        done = subprocess.run([sys.executable, "-B", "-c",
                               f"import sys; sys.path.insert(0, r'{self.root / 'pkg'}'); "
                               "import live"],
                              capture_output=True, encoding="utf-8", errors="replace",
                              timeout=60, cwd=str(self.base), env=environ)
        self.assertIn("TripwireError", done.stderr)
        self.assertEqual(self.ran(), [])

    def test_positive_install_is_once_only_and_reads_hook_json(self):
        spec = importlib.util.spec_from_file_location(
            "tw_once", self.frozen / "tripwire" / "sitecustomize.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.configured_root(self.frozen), str(self.root))
        # tripped() is the decision itself, with no hook installed here.
        root = module._base(self.root)
        venv = os.path.join(root, ".venv")
        self.assertTrue(module.tripped(str(self.root / "a.py"), root, venv))
        self.assertTrue(module.tripped(str(self.root / "a.PY"), root, venv))
        self.assertFalse(module.tripped(str(self.root / ".venv" / "a.py"), root, venv))
        self.assertFalse(module.tripped(str(self.root) + "x" + os.sep + "a.py", root, venv),
                         "a sibling folder sharing the name's start is not inside")
        self.assertFalse(module.tripped("<string>", root, venv))
        self.assertFalse(module.tripped(None, root, venv))


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
