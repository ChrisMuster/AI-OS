#!/usr/bin/env python3
"""Tests that ``close-out --read-only`` writes nothing it should not.

Two halves:

- ``ReadOnlySwitchTests`` (fast, in-process): the bytecode setting is in the
  environment before the ``.venv`` re-exec, and read-only mode reaches the audit
  as ``run_audit(read_only=True)``, which is what keeps the knowledge-graph
  validation from writing its LOG.md entries.
- ``BytecodeFixtureTests`` (slow, real runs): the live tree's tracked and
  unignored files are copied into a temporary git repository with no
  ``__pycache__`` anywhere, and the copy's own close-out is run on one small
  suite. Read-only, no file may be created or changed, ``__pycache__`` and
  LOG.md included; the same run without ``--read-only`` must create a cache
  and write the logs (the positive control). Both modes must run every gate
  to the same verdict (code review R4-1). Each run gets an
  environment without ``PYTHONDONTWRITEBYTECODE``, so the control still works
  when this suite is itself run by a read-only close-out. The copy has no
  ``.venv``, so the re-exec path is covered by the switch test, not here.
"""
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run.py"
PROJECT_ROOT = SCRIPT.parents[3]


def load_run():
    spec = importlib.util.spec_from_file_location("closeout_run_readonly", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


run = load_run()


class ReadOnlySwitchTests(unittest.TestCase):

    def setUp(self):
        saved_env = dict(os.environ)
        saved_flag = sys.dont_write_bytecode
        os.environ.pop("PYTHONDONTWRITEBYTECODE", None)
        sys.dont_write_bytecode = False

        def restore():
            os.environ.clear()
            os.environ.update(saved_env)
            sys.dont_write_bytecode = saved_flag
        self.addCleanup(restore)

    def _main(self, *args):
        """Run main() with every gate stubbed; return what the re-exec saw and
        the arguments gate_audit was called with."""
        seen = {}

        def reexec():
            seen["env"] = os.environ.get("PYTHONDONTWRITEBYTECODE")
            seen["flag"] = sys.dont_write_bytecode

        gate = {"name": "g", "passed": True, "detail": "ok"}
        with mock.patch.object(run.sys, "argv", [str(SCRIPT), *args]), \
                mock.patch.object(run, "reexec_under_venv", side_effect=reexec), \
                mock.patch.object(run, "select_suites", return_value=([], "name=x")), \
                mock.patch.object(run, "gate_audit", return_value=gate) as audit, \
                mock.patch.object(run, "gate_link", return_value=gate), \
                mock.patch.object(run, "gate_tests", return_value=gate), \
                mock.patch.object(run, "append_log"), \
                mock.patch.object(run, "RESULT_FILE", Path(tempfile.gettempdir()) / "x.json"), \
                redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                run.main()
        return seen, audit.call_args

    def test_positive_control_read_only_sets_it_before_the_reexec(self):
        seen, _ = self._main("--read-only", "--scope", "x")
        self.assertEqual(seen, {"env": "1", "flag": True})

    def test_rejection_control_the_default_leaves_bytecode_alone(self):
        seen, _ = self._main("--scope", "x")
        self.assertEqual(seen, {"env": None, "flag": False})

    def test_positive_control_read_only_reaches_the_audit_gate(self):
        _, call = self._main("--read-only", "--scope", "x")
        self.assertEqual(call.kwargs, {"read_only": True})

    def test_rejection_control_the_default_audit_gate_logs(self):
        _, call = self._main("--scope", "x")
        self.assertEqual(call.kwargs, {"read_only": False})

    def test_positive_control_gate_audit_passes_read_only_to_the_audit(self):
        fake = mock.Mock()
        fake.run_audit.return_value = ([], 1)
        with mock.patch.object(run, "load_module", return_value=fake):
            run.gate_audit(read_only=True)
        fake.run_audit.assert_called_once_with(with_graph=True, read_only=True)

    def test_rejection_control_gate_audit_defaults_to_logging(self):
        fake = mock.Mock()
        fake.run_audit.return_value = ([], 1)
        with mock.patch.object(run, "load_module", return_value=fake):
            run.gate_audit()
        fake.run_audit.assert_called_once_with(with_graph=True, read_only=False)


def _copy_live_tree(dest):
    """Copy the live tree's tracked and unignored files (no gitignored file, so
    no __pycache__), then commit them so git-based checks have a HEAD."""
    listed = subprocess.run(
        ["git", "ls-files", "-c", "-o", "--exclude-standard", "-z"],
        cwd=str(PROJECT_ROOT), capture_output=True, check=True).stdout
    for raw in listed.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8")
        src = PROJECT_ROOT / rel
        if src.is_symlink() or not src.is_file():
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "core.hooksPath="]
    for argv in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "fixture"]):
        # On Windows a just-written object file is sometimes locked for a moment
        # (measured: "unable to write file .git/objects/...: Permission denied",
        # about one run in four). The step is repeatable, so that one error is
        # retried; any other error, or a fourth lock, fails the test.
        for attempt in range(4):
            done = subprocess.run(git + argv, cwd=str(dest), capture_output=True,
                                  encoding="utf-8", errors="replace")
            if done.returncode == 0 or "Permission denied" not in done.stderr:
                break
            time.sleep(1)
        if done.returncode:
            raise RuntimeError(f"git {argv[0]} failed in the fixture: "
                               f"{done.stderr.strip()[-800:]}")


def _caches(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("__pycache__"))


# LOG.md files are gitignored, so the copy has none, and every logger here
# skips a log that does not exist: without these seeds a run that forgot
# --no-log would still write nothing, and the byte check would prove nothing.
SEEDED_LOGS = ("LOG.md", "workflows/close-out/LOG.md", "workflows/audit/LOG.md",
               "workflows/knowledge-graph/LOG.md", "workflows/link-check/LOG.md")
SEED = b"[2026-01-01T00:00:00+00:00] | Actor: Biblio | Action: created | Note: seed\n"


def _snapshot(root):
    """Every file under the tree but .git, with its bytes."""
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(root).parts}


def _run_close_out(*flags):
    """Run a fresh copy's close-out with --json. Return the copy's root, the tree
    before and after, the parsed result and the exit code. The caller removes
    the copy (the root's parent)."""
    root = Path(tempfile.mkdtemp(prefix="closeout-ro-")) / "tree"
    _copy_live_tree(root)
    for rel in SEEDED_LOGS:
        (root / rel).write_bytes(SEED)
    if _caches(root):
        raise AssertionError("the fixture must start with no cache")
    before = _snapshot(root)
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONDONTWRITEBYTECODE", run.REEXEC_MARKER)}
    done = subprocess.run(
        [sys.executable, str(root / "workflows/close-out/scripts/run.py"),
         "--scope", "link-check", "--json", *flags],
        cwd=str(root), env=env, capture_output=True, encoding="utf-8",
        errors="replace", timeout=500)
    try:
        result = json.loads(done.stdout)
    except ValueError:
        result = None
    return root, before, _snapshot(root), result, done.returncode


class BytecodeFixtureTests(unittest.TestCase):
    """One real run in each mode, each in its own fresh copy, shared by the
    tests below.

    The copy cannot pass: its LOG.md files and the gitignored targets of some
    [[links]] are absent (the audit and link gates fail; S3's clean-copy mode
    is what handles that). So a run's verdict is checked against the other
    mode's rather than against a pass: read-only must run every gate and reach
    the same verdict as a normal run (code review R4-1), and write nothing.
    """

    @classmethod
    def setUpClass(cls):
        cls.default = _run_close_out()
        cls.read_only = _run_close_out("--read-only")

    @classmethod
    def tearDownClass(cls):
        for root, *_ in (cls.default, cls.read_only):
            shutil.rmtree(root.parent, ignore_errors=True)

    def test_positive_control_a_default_run_writes_caches_and_logs(self):
        root, before, after, _, _ = self.default
        self.assertNotEqual(_caches(root), [])
        changed = {rel for rel in SEEDED_LOGS if after[rel] != before[rel]}
        # The knowledge-graph log is the one --no-log protects.
        self.assertIn("workflows/knowledge-graph/LOG.md", changed)
        self.assertIn("workflows/close-out/LOG.md", changed)

    def test_rejection_control_a_read_only_run_writes_nothing_at_all(self):
        root, before, after, _, _ = self.read_only
        self.assertEqual(_caches(root), [])
        self.assertEqual(sorted(set(after) - set(before)), [], "files created")
        self.assertEqual(sorted(rel for rel in before if after.get(rel) != before[rel]),
                         [], "files changed")

    def test_positive_control_both_modes_run_every_gate_to_one_verdict(self):
        verdicts = []
        for _, _, _, result, code in (self.default, self.read_only):
            self.assertIsNotNone(result, "close-out printed no JSON result")
            gates = {g["name"]: (g["passed"], g["detail"]) for g in result["gates"]}
            self.assertEqual(sorted(gates),
                             ["link audit", "structural audit", "tests"])
            # The selected suite ran and passed, and the audit walked the tree.
            self.assertTrue(gates["tests"][0], gates["tests"])
            self.assertRegex(gates["structural audit"][1], r"^[1-9]\d* dirs checked")
            verdicts.append((code, result["status"], gates))
        self.assertEqual(verdicts[0], verdicts[1])


if __name__ == "__main__":
    unittest.main()
