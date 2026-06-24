#!/usr/bin/env python3
"""Unit tests for the graph validation checks.

The temporary fixture tree is deliberately not a git repository, so
``git check-ignore`` returns nothing and the gitignore branch of the broken-
reference classifier falls back to WARN. These tests therefore cover the
root-file-references -> INFO branch and the genuine -> WARN branch; the
gitignore -> INFO branch is exercised against the real repo, not here.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import builder  # noqa: E402
import validate as v  # noqa: E402
from graph import Graph, Node, Edge  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_tree(root: Path) -> None:
    """A miniature project engineered to trip each validation check exactly once:

    - workflows/foo/missingchild   -> uncontained (not in foo's Contents)
    - workflows/foo/scripts        -> contained *only* via a descendant file
                                      (`run.py`), exercising descendant-awareness
    - workflows/bar -> workflows/gone (depends_on) -> genuine broken WARN
    - AGENTS.md -> workflows/ghost  (references in a root file) -> INFO
    - lonely/                       -> a genuine orphan (no inbound edge)
    - two `# Twin` titles           -> duplicate-title INFO
    """
    _write(
        root / "AGENTS.md",
        "# Agents\n\n## Purpose\nRules.\n\n"
        "## Structure\nAll workflows live in `workflows/`.\n\n"
        "## Examples\nFor instance `workflows/ghost/` might be archived.\n",
    )
    _write(
        root / "workflows" / "CONTEXT.md",
        "# Workflows\n\n**Last modified:** 2026-06-20\n\n## Purpose\nContainer.\n\n"
        "## Contents\n- Foo — `workflows/foo/` [[workflows/foo/CONTEXT]] — wf.\n"
        "- Bar — `workflows/bar/` [[workflows/bar/CONTEXT]] — wf.\n",
    )
    # foo lists only the scripts *file*, never the scripts dir, and never
    # mentions missingchild at all.
    _write(
        root / "workflows" / "foo" / "CONTEXT.md",
        "# Twin\n\n**Last modified:** 2026-06-20\n\n## Purpose\nFoo.\n\n"
        "## Contents\n- entry — `workflows/foo/scripts/run.py` — the entry point.\n",
    )
    _write(
        root / "workflows" / "foo" / "scripts" / "CONTEXT.md",
        "# Foo Scripts\n\n**Last modified:** 2026-06-20\n\n## Purpose\nCode.\n",
    )
    _write(root / "workflows" / "foo" / "scripts" / "run.py", "print('hi')\n")
    _write(
        root / "workflows" / "foo" / "missingchild" / "CONTEXT.md",
        "# Missing Child\n\n**Last modified:** 2026-06-20\n\n## Purpose\nUnlisted.\n",
    )
    _write(
        root / "workflows" / "bar" / "CONTEXT.md",
        "# Twin\n\n**Last modified:** 2026-06-20\n\n## Purpose\nBar.\n\n"
        "## Dependencies\n- `workflows/gone/` [[workflows/gone/CONTEXT]] — absent.\n",
    )
    _write(
        root / "lonely" / "CONTEXT.md",
        "# Lonely\n\n**Last modified:** 2026-06-20\n\n## Purpose\nNobody references me.\n",
    )


class TestValidate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_tree(self.root)
        self.graph = builder.build_graph(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    # -- broken references --------------------------------------------------
    def test_genuine_broken_reference_is_warn(self):
        findings = v.check_broken_references(self.graph, self.root)
        # bar references workflows/gone both as a backtick dependency and as a
        # [[link]], so two genuine broken edges are produced — both must be WARN.
        gone = [f for f in findings if f.subject == "workflows/gone"]
        self.assertGreaterEqual(len(gone), 1)
        self.assertTrue(all(f.severity == "WARN" for f in gone))

    def test_root_file_prose_reference_is_info(self):
        findings = v.check_broken_references(self.graph, self.root)
        ghost = [f for f in findings if f.subject == "workflows/ghost"]
        self.assertEqual(len(ghost), 1)
        self.assertEqual(ghost[0].severity, "INFO")

    # -- orphans ------------------------------------------------------------
    def test_orphan_detected_with_warn_severity(self):
        findings = v.check_orphans(self.graph)
        subjects = {f.subject: f.severity for f in findings}
        self.assertIn("lonely", subjects)
        self.assertEqual(subjects["lonely"], "WARN")

    def test_non_orphans_not_flagged(self):
        findings = v.check_orphans(self.graph)
        flagged = {f.subject for f in findings}
        # foo has an inbound child edge and a contains edge -> not an orphan.
        self.assertNotIn("workflows/foo", flagged)

    # -- uncontained (descendant-aware) ------------------------------------
    def test_uncontained_directory_flagged(self):
        findings = v.check_uncontained(self.graph)
        subjects = {f.subject for f in findings}
        self.assertIn("workflows/foo/missingchild", subjects)

    def test_descendant_file_keeps_dir_contained(self):
        # foo lists workflows/foo/scripts/run.py (a descendant) but not the dir.
        # Descendant-awareness must treat workflows/foo/scripts as contained.
        findings = v.check_uncontained(self.graph)
        subjects = {f.subject for f in findings}
        self.assertNotIn("workflows/foo/scripts", subjects)

    # -- duplicate titles ---------------------------------------------------
    def test_duplicate_title_is_info(self):
        findings = v.check_duplicate_titles(self.graph)
        twins = [f for f in findings if f.subject == "Twin"]
        self.assertEqual(len(twins), 1)
        self.assertEqual(twins[0].severity, "INFO")
        self.assertIn("workflows/foo", twins[0].message)
        self.assertIn("workflows/bar", twins[0].message)

    def test_file_basenames_do_not_trigger_duplicates(self):
        # run.py is a file node; file nodes must be excluded from the title check
        # so common basenames never produce noise.
        findings = v.check_duplicate_titles(self.graph)
        self.assertFalse(any(f.subject == "run.py" for f in findings))

    # -- parse warnings (constructed graph) --------------------------------
    def test_parse_warnings_surfaced(self):
        g = Graph()
        g.add_node(Node(id="workflows/x", type="workflow", parse_warnings=["boom"]))
        findings = v.check_parse_warnings(g)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "WARN")
        self.assertIn("boom", findings[0].message)

    # -- back-reference gaps ------------------------------------------------
    def test_backref_gap_is_info(self):
        g = Graph()
        g.add_node(Node(id="workflows/a", type="workflow"))
        g.add_node(Node(id="workflows/b", type="workflow"))
        g.add_edge(Edge("workflows/a", "workflows/b", "depends_on"))
        findings = v.check_backref_gaps(g)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "INFO")
        self.assertEqual(findings[0].subject, "workflows/a")

    def test_backref_present_not_flagged(self):
        g = Graph()
        g.add_node(Node(id="workflows/a", type="workflow"))
        g.add_node(Node(id="workflows/b", type="workflow"))
        g.add_edge(Edge("workflows/a", "workflows/b", "depends_on"))
        g.add_edge(Edge("workflows/b", "workflows/a", "references"))
        self.assertEqual(v.check_backref_gaps(g), [])

    # -- orchestration ------------------------------------------------------
    def test_run_checks_can_skip_backrefs(self):
        with_back = v.run_checks(self.graph, self.root, include_backrefs=True)
        without = v.run_checks(self.graph, self.root, include_backrefs=False)
        self.assertGreaterEqual(len(with_back), len(without))


if __name__ == "__main__":
    unittest.main(verbosity=2)
