#!/usr/bin/env python3
"""Tests for script_loader.py, the one way the suites load a script from scripts/, a
check that no suite loads a module any other way, and a combined-import check (the
2026-11-21 tests clash: run together under ``python -m unittest discover``, the
suites' own loaders replaced each other's copies of the scripts, and 3 tests in
test_measure.py and 5 in test_crossreview.py failed that pass when each file is run
alone).

The folder check is conservative (cross-review R1-2): every direct module load in a
suite file is refused unless its name, written in the call, is private (starts with
``_``) or is one of the few named loads of files outside scripts/. The combined-import
check (cross-review R1-1) imports every suite in one child process, as discovery does,
without running a test, and refuses any script module or class a suite holds that is
not the one copy in ``sys.modules``; the full combined run takes over 20 minutes, too
long for a review round or close-out, and this is the step the clash happened in.

    python workflows/review-orchestration/tests/test_script_loader.py
"""

import ast
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "scripts"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))
import script_loader  # noqa: E402

# Files the folder check does not read: the loader itself, and this file, whose
# tests build stray loads on purpose.
EXEMPT_FILES = {"script_loader.py", "test_script_loader.py"}
# Direct loads of files outside scripts/, each by the name written in its call:
# the git fixture helper (five suites), the ai-style guard's script and a frozen
# folder's tripwire.
NAMED_LOADS = {"fixture_git", "ai_style_run", "tw_once"}
# Calls that load a module from a file, or load one again.
LOADERS = {"spec_from_file_location", "spec_from_loader", "SourceFileLoader",
           "SourcelessFileLoader", "reload", "load_module", "exec_module", "find_spec",
           "run_path", "run_module"}
# The suites that load scripts by path; each must use the loader.
LOADER_USERS = {
    "loop_fixtures.py", "test_a4_sessions.py", "test_approver.py", "test_brief.py",
    "test_check_server.py", "test_codex_rules.py", "test_container.py",
    "test_crossreview.py", "test_frozen.py", "test_intent_brief.py",
    "test_intent_reply.py", "test_limits.py", "test_providers.py", "test_runrecord.py",
    "test_start.py", "test_stopreasons.py", "test_trusted_checks.py"}


def _name_arg(node):
    if node.args:
        return node.args[0]
    return next((k.value for k in node.keywords if k.arg == "name"), None)


def _allowed_name(node):
    """A name written in the call that is private or a named load."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.startswith("_") or node.value in NAMED_LOADS
    if isinstance(node, ast.JoinedStr) and node.values:
        first = node.values[0]
        return (isinstance(first, ast.Constant) and isinstance(first.value, str)
                and first.value.startswith("_"))
    return False


def _called(node):
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def stray_loads(source, filename="<test>"):
    """Every direct module load in ``source`` that is not allowed, as
    ``line: text``. A ``spec_from_file_location`` call is allowed only when the name
    written in it is private or a named load; ``exec_module`` only on a spec made by
    an allowed call in the same file is not distinguishable here, so it is allowed
    alongside one; every other loader call, and importing a loader name directly, is
    refused."""
    tree = ast.parse(source, filename)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in (
                "importlib", "runpy"):
            for alias in node.names:
                if alias.name in LOADERS or alias.name == "*":
                    found.append(f"{node.lineno}: {ast.unparse(node)}")
        if not isinstance(node, ast.Call):
            continue
        called = _called(node)
        if called == "spec_from_file_location":
            if not _allowed_name(_name_arg(node)):
                found.append(f"{node.lineno}: {ast.unparse(node)}")
        elif called in LOADERS - {"spec_from_file_location", "exec_module"}:
            found.append(f"{node.lineno}: {ast.unparse(node)}")
    return found


COMBINED = r"""
import importlib.util, inspect, json, sys, types
from pathlib import Path
tests = Path(sys.argv[1]).resolve()
scripts = (tests.parent / "scripts").resolve()
sys.path.insert(0, str(tests))
suites = {}
for path in sorted(tests.glob("test_*.py")):
    if path.name == "test_script_loader.py":
        continue
    spec = importlib.util.spec_from_file_location("_combined_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    suites[path.name] = module
for extra in sys.argv[2:]:
    path = Path(extra)
    spec = importlib.util.spec_from_file_location("_combined_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    suites[path.name] = module

def script_stem(module):
    where = getattr(module, "__file__", None)
    if not where:
        return None
    where = Path(where).resolve()
    return where.stem if where.parent == scripts else None

problems = []
holders = dict(suites)
holders.update({n: m for n, m in list(sys.modules.items())
                if isinstance(m, types.ModuleType) and n not in holders
                and (script_stem(m) or n == "loop_fixtures")})
for name, module in list(sys.modules.items()):
    stem = script_stem(module) if isinstance(module, types.ModuleType) else None
    if stem and name != stem and not name.startswith("_"):
        problems.append(f"sys.modules[{name!r}] holds scripts/{stem}.py")
for owner, module in holders.items():
    for attr, value in list(vars(module).items()):
        if isinstance(value, types.ModuleType):
            stem = script_stem(value)
            if stem and sys.modules.get(stem) is not value:
                problems.append(f"{owner}: {attr} is not the shared {stem}")
        elif inspect.isclass(value):
            home = sys.modules.get(getattr(value, "__module__", ""))
            if (home is not None and script_stem(home)
                    and getattr(home, value.__name__, None) is not value):
                problems.append(f"{owner}: {attr} is not {home.__name__}.{value.__name__}")
print(json.dumps({"suites": len(suites), "problems": sorted(set(problems))}))
"""


def combined_import(*extra):
    """Import every suite in this folder, then each ``extra`` file, in one child
    process and return its report."""
    done = subprocess.run([sys.executable, "-c", COMBINED, str(TESTS), *map(str, extra)],
                          capture_output=True, encoding="utf-8", errors="replace",
                          timeout=600, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    if done.returncode != 0:
        raise AssertionError("the combined import failed:\n" + done.stderr[-4000:])
    return json.loads(done.stdout.strip().splitlines()[-1])


class LoadTests(unittest.TestCase):
    """``load`` reuses the copy already loaded from scripts/ and makes a new one
    otherwise."""

    def setUp(self):
        self.saved = sys.modules.pop("stopreasons", None)
        self.addCleanup(self.restore)

    def restore(self):
        sys.modules.pop("stopreasons", None)
        if self.saved is not None:
            sys.modules["stopreasons"] = self.saved

    def test_positive_a_second_load_is_the_same_module(self):
        first = script_loader.load("stopreasons")
        self.assertIs(script_loader.load("stopreasons"), first)
        self.assertIs(sys.modules["stopreasons"], first)
        # The clash itself: one class, whichever suite asks.
        self.assertIs(script_loader.load("stopreasons").ReplyError, first.ReplyError)

    def test_positive_a_normal_import_is_reused(self):
        sys.path.insert(0, str(SCRIPTS))
        self.addCleanup(sys.path.remove, str(SCRIPTS))
        import stopreasons  # noqa: E402
        self.assertIs(script_loader.load("stopreasons"), stopreasons)

    def test_negative_a_module_of_that_name_from_elsewhere_is_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp) / "stopreasons.py"
            other.write_text("ELSEWHERE = True\n", encoding="utf-8", newline="\n")
            spec = importlib.util.spec_from_file_location("stopreasons", other)
            stranger = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(stranger)
            sys.modules["stopreasons"] = stranger
            loaded = script_loader.load("stopreasons")
        self.assertIsNot(loaded, stranger)
        self.assertTrue(hasattr(loaded, "ReplyError"))
        self.assertIs(sys.modules["stopreasons"], loaded)

    def test_negative_a_name_with_no_file_is_not_reused(self):
        sys.modules["stopreasons"] = type(sys)("stopreasons")  # no __file__
        self.assertTrue(hasattr(script_loader.load("stopreasons"), "ReplyError"))

    def test_rejection_a_script_that_fails_to_run_is_not_left_registered(self):
        name = "_loader_fails"
        self.assertFalse((SCRIPTS / f"{name}.py").exists())
        with tempfile.TemporaryDirectory() as tmp:
            original = script_loader.SCRIPTS
            script_loader.SCRIPTS = Path(tmp)
            try:
                (Path(tmp) / f"{name}.py").write_text("raise RuntimeError('boom')\n",
                                                     encoding="utf-8", newline="\n")
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    script_loader.load(name)
            finally:
                script_loader.SCRIPTS = original
        self.assertNotIn(name, sys.modules)


class NoOtherLoaderTests(unittest.TestCase):
    """No suite loads a module from a file except through ``script_loader.load``,
    under a private name, or as one of the named loads (cross-review R1-2)."""

    def test_positive_every_suite_file_is_clean(self):
        stray = {}
        for path in sorted(TESTS.glob("*.py")):
            if path.name in EXEMPT_FILES:
                continue
            found = stray_loads(path.read_text(encoding="utf-8"), path.name)
            if found:
                stray[path.name] = found
        self.assertEqual(stray, {})

    def test_positive_each_suite_that_loads_scripts_uses_the_loader(self):
        users = {p.name for p in TESTS.glob("*.py")
                 if "script_loader.load" in p.read_text(encoding="utf-8")}
        self.assertEqual(LOADER_USERS - users, set())

    def test_rejection_each_load_shape_is_found(self):
        shapes = {
            "the old loader": ("def _load(name):\n"
                               "    spec = importlib.util.spec_from_file_location("
                               "name, SCRIPTS / f'{name}.py')\n"),
            "an inline load": "s = importlib.util.spec_from_file_location('brief', SCRIPTS / 'brief.py')\n",
            "keywords": "s = importlib.util.spec_from_file_location(name='loop', location=SCRIPTS / 'loop.py')\n",
            "keywords reversed": "s = importlib.util.spec_from_file_location(location=SCRIPTS / 'loop.py', name='loop')\n",
            "a path in a variable": ("path = SCRIPTS / 'loop.py'\n"
                                     "s = importlib.util.spec_from_file_location('loop', path)\n"),
            "a name in a variable": ("name = '_private'\n"
                                     "s = importlib.util.spec_from_file_location(name, p)\n"),
            "an imported loader": ("from importlib.util import spec_from_file_location as f\n"
                                   "s = f('loop', p)\n"),
            "a file loader": "m = importlib.machinery.SourceFileLoader('loop', p).load_module()\n",
            "a reload": "importlib.reload(loop)\n",
            "a spec from a loader": "s = importlib.util.spec_from_loader('loop', loader)\n",
            "a found spec": "s = importlib.util.find_spec('loop')\n",
            "a file run": "runpy.run_path(str(SCRIPTS / 'loop.py'))\n",
            "a module run": "runpy.run_module('loop')\n",
        }
        for label, source in shapes.items():
            with self.subTest(label):
                self.assertTrue(stray_loads(source), label)

    def test_negative_a_private_or_named_load_is_allowed(self):
        allowed = [
            "s = importlib.util.spec_from_file_location('_run_layout', SCRIPTS / 'run.py')\n",
            "s = importlib.util.spec_from_file_location(f'_floors_{path.stem}', path)\n",
            "s = importlib.util.spec_from_file_location('fixture_git', TESTS / 'fixture_git.py')\n",
            "s = importlib.util.spec_from_file_location('ai_style_run', p)\n",
            "s = importlib.util.spec_from_file_location('tw_once', p)\n"
            "m = importlib.util.module_from_spec(s); s.loader.exec_module(m)\n",
            # Code for a child process, written in a string, is not a load here.
            "code = \"s = importlib.util.spec_from_file_location('copy', 'x.py')\"\n",
        ]
        for source in allowed:
            with self.subTest(source):
                self.assertEqual(stray_loads(source), [])


class CombinedImportTests(unittest.TestCase):
    """Every suite imported together in one process holds the one shared copy of
    each script module and class (cross-review R1-1)."""

    def test_positive_the_suites_imported_together_share_one_copy(self):
        report = combined_import()
        self.assertGreaterEqual(report["suites"], 30)
        self.assertEqual(report["problems"], [])

    def test_rejection_a_suite_with_its_own_copy_is_found(self):
        # One more file, imported after the suites, that loads stopreasons afresh the
        # old way: the clash the check exists to catch. The suites' copies are now
        # not the one in sys.modules, and this file's ReplyError not theirs.
        with tempfile.TemporaryDirectory() as tmp:
            own = Path(tmp) / "test_zz_own_copy.py"
            own.write_text(
                "import importlib.util, sys\n"
                f"_s = importlib.util.spec_from_file_location('stopreasons', "
                f"r'{SCRIPTS / 'stopreasons.py'}')\n"
                "sr = importlib.util.module_from_spec(_s)\n"
                "sys.modules['stopreasons'] = sr\n"
                "_s.loader.exec_module(sr)\n", encoding="utf-8", newline="\n")
            report = combined_import(own)
        self.assertTrue(any("is not the shared stopreasons" in p
                            for p in report["problems"]), report["problems"])


if __name__ == "__main__":
    unittest.main()
