#!/usr/bin/env python3
"""Unit tests for graph construction against a temporary project tree."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import builder  # noqa: E402
from graph import Graph, Node, Edge, atomic_write_json  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_tree(root: Path) -> None:
    """A miniature project: a container, two workflows, a nested scripts dir,
    a root file, and one deliberately broken dependency."""
    _write(
        root / "AGENTS.md",
        "# Agents\n\n## Purpose\nRules.\n\n"
        "## Structure\nAll workflows live in `workflows/`.\n",
    )
    _write(
        root / "workflows" / "CONTEXT.md",
        "# Workflows\n\n**Last modified:** 2026-06-19\n\n## Purpose\nContainer.\n\n"
        "## Contents\n- Foo — `workflows/foo/` [[workflows/foo/CONTEXT]] — A workflow.\n",
    )
    _write(
        root / "workflows" / "foo" / "CONTEXT.md",
        "# Foo\n\n**Last modified:** 2026-06-19\n\n## Purpose\nFoo does things.\n\n"
        "## Contents\n- scripts/ — `workflows/foo/scripts/` [[workflows/foo/scripts/CONTEXT]] — code.\n\n"
        "## Dependencies\n- `AGENTS.md` [[AGENTS]] (root) — rules.\n",
    )
    _write(
        root / "workflows" / "foo" / "scripts" / "CONTEXT.md",
        "# Foo Scripts\n\n**Last modified:** 2026-06-19\n\n## Purpose\nCode.\n\n"
        "## Steps\nSee `workflows/foo/scripts/run.py`.\n",
    )
    _write(root / "workflows" / "foo" / "scripts" / "run.py", "print('hi')\n")
    # SKILL.md plus an extension-less link to it, and a placeholder reference.
    _write(root / "workflows" / "foo" / "SKILL.md", "# Foo Skill\n")
    _write(
        root / "workflows" / "qux" / "CONTEXT.md",
        "# Qux\n\n**Last modified:** 2026-06-19\n\n## Purpose\nQux.\n\n"
        "## Contents\nSee the skill [[workflows/foo/SKILL]] for usage.\n\n"
        "## Outputs\nCreates `wikis/<wiki-name>/` and `journal/entries/YYYY-MM.md`.\n",
    )
    _write(
        root / "workflows" / "bar" / "CONTEXT.md",
        "# Bar\n\n**Last modified:** 2026-06-19\n\n## Purpose\nBar.\n\n"
        "## Dependencies\n- `workflows/missing/` [[workflows/missing/CONTEXT]] — gone.\n",
    )


class TestBuilder(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_tree(self.root)
        self.graph = builder.build_graph(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_expected_nodes(self):
        for nid in ("workflows", "workflows/foo", "workflows/foo/scripts", "workflows/bar", "AGENTS.md"):
            self.assertTrue(self.graph.has_node(nid), f"missing node {nid}")

    def test_node_types(self):
        self.assertEqual(self.graph.nodes["workflows"].type, "container")
        self.assertEqual(self.graph.nodes["workflows/foo"].type, "workflow")
        self.assertEqual(self.graph.nodes["AGENTS.md"].type, "root-file")

    def test_child_edge(self):
        self._assert_edge("workflows", "workflows/foo", "child")

    def test_contains_edge(self):
        self._assert_edge("workflows", "workflows/foo", "contains")

    def test_depends_on_resolved(self):
        e = self._assert_edge("workflows/foo", "AGENTS.md", "depends_on")
        self.assertTrue(e.resolved)

    def test_links_to_edge(self):
        self._assert_edge("workflows/foo", "workflows/foo/scripts", "links_to")

    def test_broken_reference_detected(self):
        e = self._assert_edge("workflows/bar", "workflows/missing", "depends_on")
        self.assertFalse(e.resolved)

    def test_file_reference_creates_file_node(self):
        # run.py is referenced from the scripts CONTEXT Steps section.
        self._assert_edge("workflows/foo/scripts", "workflows/foo/scripts/run.py", "references")
        self.assertEqual(self.graph.nodes["workflows/foo/scripts/run.py"].type, "file")

    def test_extensionless_md_link_resolves(self):
        e = self._assert_edge("workflows/qux", "workflows/foo/SKILL.md", "links_to")
        self.assertTrue(e.resolved)
        self.assertEqual(self.graph.nodes["workflows/foo/SKILL.md"].type, "file")

    def test_placeholders_ignored(self):
        for e in self.graph.edges:
            self.assertNotIn("<", e.target)
            self.assertNotIn("YYYY", e.target)

    def test_bare_top_level_dir_reference_resolves(self):
        # AGENTS.md references the `workflows/` directory by its bare name; this
        # must produce a resolved edge so top-level dirs are not false orphans.
        e = self._assert_edge("AGENTS.md", "workflows", "references")
        self.assertTrue(e.resolved)

    def test_no_self_edges(self):
        for e in self.graph.edges:
            self.assertNotEqual(e.source, e.target)

    def test_edges_deduped(self):
        keys = [(e.source, e.target, e.type) for e in self.graph.edges]
        self.assertEqual(len(keys), len(set(keys)))

    def test_serialisation_sorted_and_stable(self):
        nodes_a, edges_a = self.graph.to_dicts()
        nodes_b, edges_b = builder.build_graph(self.root).to_dicts()
        self.assertEqual(nodes_a, nodes_b)
        self.assertEqual(edges_a, edges_b)
        self.assertEqual([n["id"] for n in nodes_a], sorted(n["id"] for n in nodes_a))

    def test_atomic_write_roundtrip(self):
        nodes, edges = self.graph.to_dicts()
        out = self.root / "index" / "nodes.json"
        atomic_write_json(out, nodes)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8")), nodes)

    def _assert_edge(self, source, target, etype) -> Edge:
        for e in self.graph.edges:
            if e.source == source and e.target == target and e.type == etype:
                return e
        self.fail(f"missing edge {source} --{etype}--> {target}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
