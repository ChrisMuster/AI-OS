#!/usr/bin/env python3
"""Regression checks for scripts that need project-only packages at runtime.

Book Dragon scripts run under the canonical ``.venv``, which carries the
third-party packages declared across ``requirements.txt`` (and its ``-r``
includes). A script launched with a plain ``python`` may not have those
packages importable, so it dies with an ``ImportError``. Any *entry-point*
script (one with an ``if __name__ == "__main__"`` block) that imports a
project-only package must therefore signal how it copes:

  * call ``ensure_project_runtime()`` to re-exec under the ``.venv``, or
  * carry ``# runtime-guard: degrades without <pkg>`` and genuinely fall back
    when the package is missing (an optional dependency), or
  * carry ``# runtime-guard: launched via <mechanism>`` when an external
    caller always launches it under the ``.venv`` (e.g. ``launch.py`` for the
    MCP server, or ``verify.py`` / ``lifecycle_check.py`` for the smoke test).

Pure helper modules (no ``__main__``) are exempt: they never run under a plain
``python`` on their own; they inherit the runtime of the entry point that
imports them, and that entry point must itself comply.

This module is standard-library only so it runs under a plain ``python`` (it is
part of the close-out gate, whose whole point is to not depend on the ``.venv``).
"""
import ast
import re
import tempfile
import textwrap
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Distribution name -> import name, for the handful in the project set that
# differ. Anything not listed here is assumed to import under its own name.
DIST_TO_IMPORT = {
    "pyyaml": "yaml",
    "beautifulsoup4": "bs4",
    "python-dotenv": "dotenv",
    "tavily-python": "tavily",
}

# Split a requirement line at the first version specifier, environment marker,
# or extras bracket, leaving just the distribution name.
_REQUIREMENT_SPLIT = re.compile(r"[<>=!~;\[]")
_ENTRY_POINT_RE = re.compile(r'^\s*if\s+__name__\s*==\s*["\']__main__["\']', re.M)
_DEGRADE_MARKER_RE = re.compile(r"#\s*runtime-guard:\s*degrades without\s+\S+")
_LAUNCHED_MARKER_RE = re.compile(r"#\s*runtime-guard:\s*launched via\s+\S+")


def _parse_requirements(path: Path, seen=None) -> set:
    """Distribution names declared by *path*, following ``-r`` includes."""
    seen = seen if seen is not None else set()
    path = path.resolve()
    if path in seen or not path.is_file():
        return set()
    seen.add(path)
    dists = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-r") or line.startswith("--requirement"):
            include = line.split(None, 1)[1].strip() if " " in line else line[2:].strip()
            dists |= _parse_requirements(path.parent / include, seen)
            continue
        name = _REQUIREMENT_SPLIT.split(line, 1)[0].strip().lower()
        if name:
            dists.add(name)
    return dists


def project_import_names(root: Path) -> set:
    """Import names for every distribution in the canonical requirements set."""
    dists = _parse_requirements(root / "requirements.txt")
    return {DIST_TO_IMPORT.get(d, d) for d in dists}


def _top_level_imports(tree: ast.AST) -> set:
    """Top-level module names imported anywhere in *tree* (absolute imports)."""
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # level > 0 is a relative (local) import; module None is "from . import x"
            if node.level == 0 and node.module:
                mods.add(node.module.split(".")[0])
    return mods


def _is_entry_point(text: str) -> bool:
    return _ENTRY_POINT_RE.search(text) is not None


def _calls_ensure_runtime(tree: ast.AST) -> bool:
    """True if the module actually *calls* ensure_project_runtime().

    A real ``ast.Call`` is required, so a bare mention in a comment or docstring
    (which this guard exists to prevent being trusted) does not count. Both the
    bare ``ensure_project_runtime()`` form and an attribute form such as
    ``runtime.ensure_project_runtime()`` are accepted.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "ensure_project_runtime":
            return True
        if isinstance(func, ast.Attribute) and func.attr == "ensure_project_runtime":
            return True
    return False


def _has_runtime_signal(text: str, tree: ast.AST) -> bool:
    # The runtime call is detected via AST (a real call, not a commented-out or
    # docstringed mention); the two marker forms are intentionally documentary
    # comments, so those stay literal-text checks.
    return (
        _calls_ensure_runtime(tree)
        or _DEGRADE_MARKER_RE.search(text) is not None
        or _LAUNCHED_MARKER_RE.search(text) is not None
    )


def in_scope_scripts(root: Path) -> list:
    """Entry-point candidate scripts: workflows/skills scripts, minus tests dirs."""
    scripts = []
    for pattern in ("workflows/*/scripts/*.py", "skills/*/scripts/*.py"):
        scripts.extend(root.glob(pattern))
    return sorted(
        p for p in scripts if "tests" not in p.relative_to(root).parts
    )


def offending_scripts(root: Path, project_imports=None) -> list:
    """Entry-point scripts importing a project-only package with no runtime signal."""
    if project_imports is None:
        project_imports = project_import_names(root)
    offenders = []
    for path in in_scope_scripts(root):
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        if not _is_entry_point(text):
            continue  # pure helper module; inherits its caller's runtime
        if not (_top_level_imports(tree) & project_imports):
            continue  # only stdlib / local imports
        if not _has_runtime_signal(text, tree):
            offenders.append(path.relative_to(root).as_posix())
    return offenders


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


class RequirementParsingTests(unittest.TestCase):
    def test_follows_includes_and_maps_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "requirements.txt", """\
                # canonical env
                -r nested/base.txt
                mcp>=1.0.0; python_version >= "3.10"
            """)
            _write(root / "nested" / "base.txt", """\
                pyyaml>=6.0.0
                beautifulsoup4>=4.12.0
                requests>=2.31.0
            """)
            self.assertEqual(
                project_import_names(root),
                {"yaml", "bs4", "requests", "mcp"},
            )


class OffenderDetectionTests(unittest.TestCase):
    def _offenders_for(self, script_src, rel="workflows/foo/scripts/run.py"):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "requirements.txt", "requests>=2.31.0\npyyaml>=6.0.0\n")
            _write(root / rel, script_src)
            return offending_scripts(root)

    def test_entry_point_without_signal_is_flagged(self):
        offenders = self._offenders_for("""\
            import requests

            def main():
                requests.get("https://example.com")

            if __name__ == "__main__":
                main()
        """)
        self.assertEqual(offenders, ["workflows/foo/scripts/run.py"])

    def test_ensure_project_runtime_satisfies(self):
        offenders = self._offenders_for("""\
            from runtime import ensure_project_runtime
            ensure_project_runtime()
            import requests

            if __name__ == "__main__":
                requests.get("https://example.com")
        """)
        self.assertEqual(offenders, [])

    def test_attribute_form_call_satisfies(self):
        offenders = self._offenders_for("""\
            import runtime
            runtime.ensure_project_runtime()
            import requests

            if __name__ == "__main__":
                requests.get("https://example.com")
        """)
        self.assertEqual(offenders, [])

    def test_commented_out_call_does_not_satisfy(self):
        # A guard that trusts a bare text match could be fooled by a comment.
        # The real call is gone, so the script must still be flagged.
        offenders = self._offenders_for("""\
            # ensure_project_runtime()  # oops, commented out
            import requests

            if __name__ == "__main__":
                requests.get("https://example.com")
        """)
        self.assertEqual(offenders, ["workflows/foo/scripts/run.py"])

    def test_docstring_mention_does_not_satisfy(self):
        offenders = self._offenders_for('''\
            """This module ought to call ensure_project_runtime() but never does."""
            import requests

            if __name__ == "__main__":
                requests.get("https://example.com")
        ''')
        self.assertEqual(offenders, ["workflows/foo/scripts/run.py"])

    def test_degrade_marker_satisfies(self):
        offenders = self._offenders_for("""\
            try:
                import yaml  # runtime-guard: degrades without pyyaml
            except ImportError:
                yaml = None

            if __name__ == "__main__":
                print(yaml)
        """)
        self.assertEqual(offenders, [])

    def test_launched_marker_satisfies(self):
        offenders = self._offenders_for("""\
            import requests  # runtime-guard: launched via launch.py

            if __name__ == "__main__":
                requests.get("https://example.com")
        """)
        self.assertEqual(offenders, [])

    def test_helper_module_without_main_is_exempt(self):
        offenders = self._offenders_for(
            "import requests\n\n\ndef fetch(url):\n    return requests.get(url)\n",
            rel="workflows/foo/scripts/client.py",
        )
        self.assertEqual(offenders, [])

    def test_stdlib_only_entry_point_not_flagged(self):
        offenders = self._offenders_for("""\
            import json
            import sys

            if __name__ == "__main__":
                json.dump({}, sys.stdout)
        """)
        self.assertEqual(offenders, [])

    def test_tests_directory_is_out_of_scope(self):
        # A file under a tests/ segment is never treated as an entry-point script.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "requirements.txt", "requests>=2.31.0\n")
            _write(root / "workflows/foo/scripts/tests/test_thing.py", """\
                import requests

                if __name__ == "__main__":
                    requests.get("https://example.com")
            """)
            self.assertEqual(offending_scripts(root), [])


class RepoRegressionTests(unittest.TestCase):
    def test_no_offenders_in_repo(self):
        offenders = offending_scripts(PROJECT_ROOT)
        self.assertEqual(
            offenders,
            [],
            "Entry-point scripts that import a project-only package must call "
            "ensure_project_runtime(), carry '# runtime-guard: degrades without "
            "<pkg>' and degrade, or carry '# runtime-guard: launched via "
            "<mechanism>'. Offenders: " + ", ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
