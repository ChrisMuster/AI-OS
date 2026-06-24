#!/usr/bin/env python3
"""Unit tests for the Phase 5 opt-in memory layer.

Covers: the stdlib frontmatter reader (top-level and metadata-nested type, and a
missing block); memory nodes appearing only when the flag is on; link resolution
by both filename stem and frontmatter name: slug; forward references kept
unresolved and classified INFO; memory orphan severity INFO; MEMORY.md
membership edges; memory->structural cross-layer references; and the flag-off
structural regression (identical output, superset when the layer is added).

Temp trees are not git repositories, so the gitignore-aware prune falls back to
walking all directories — consistent with the other builder/validate tests.
"""

import argparse
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import builder  # noqa: E402
import memory as memory_layer  # noqa: E402
import validate as validate_mod  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_tree(root: Path) -> None:
    """A miniature project with a structural workflow plus a memory store
    exercising stem links, slug links, a forward reference, a cross-layer
    backtick path, a MEMORY.md index, and an unlisted orphan memory."""
    # Structural side — a workflow that a memory cross-references.
    _write(
        root / "workflows" / "CONTEXT.md",
        "# Workflows\n\n**Last modified:** 2026-06-21\n\n## Purpose\nContainer.\n\n"
        "## Contents\n- Foo — `workflows/foo/` [[workflows/foo/CONTEXT]] — A workflow.\n",
    )
    _write(
        root / "workflows" / "foo" / "CONTEXT.md",
        "# Foo\n\n**Last modified:** 2026-06-21\n\n## Purpose\nFoo.\n",
    )
    # Memory side.
    _write(root / "memory" / "CONTEXT.md", "# Memory\n\n## Purpose\nStore.\n")
    _write(
        root / "memory" / "MEMORY.md",
        "# Memory Index\n"
        "- [Alpha](feedback_alpha.md) -- alpha hook\n"
        "- [Beta](project_beta.md) -- beta hook\n",
    )
    _write(
        root / "memory" / "feedback_alpha.md",
        "---\n"
        "name: alpha-slug\n"
        "description: Alpha description.\n"
        "type: feedback\n"
        "---\n\n"
        "Alpha links to [[project_beta]] by stem and to a not-yet-written "
        "[[feedback_missing]] memory.\n",
    )
    _write(
        root / "memory" / "project_beta.md",
        "---\n"
        "name: beta-slug\n"
        "description: Beta description.\n"
        "metadata:\n"
        "  type: project\n"
        "---\n\n"
        "Beta links back to [[alpha-slug]] by name slug and tracks "
        "`workflows/foo/`.\n",
    )
    # An orphan memory: not in MEMORY.md, nothing links to it.
    _write(
        root / "memory" / "reference_orphan.md",
        "---\nname: orphan-slug\ndescription: Lonely.\ntype: reference\n---\n\nNo links.\n",
    )


class TestFrontmatter(unittest.TestCase):
    def test_top_level_type(self):
        name, desc, mtype, body = memory_layer.parse_frontmatter(
            "---\nname: a\ndescription: d\ntype: feedback\n---\n\nBody.\n"
        )
        self.assertEqual((name, desc, mtype), ("a", "d", "feedback"))
        self.assertEqual(body.strip(), "Body.")

    def test_metadata_nested_type(self):
        name, desc, mtype, _ = memory_layer.parse_frontmatter(
            "---\nname: a\ndescription: d\nmetadata:\n  type: project\n---\nBody.\n"
        )
        self.assertEqual((name, desc, mtype), ("a", "d", "project"))

    def test_description_with_colon_kept_whole(self):
        _, desc, _, _ = memory_layer.parse_frontmatter(
            "---\nname: a\ndescription: x: y z\ntype: user\n---\nBody.\n"
        )
        self.assertEqual(desc, "x: y z")

    def test_missing_block_is_all_body(self):
        name, desc, mtype, body = memory_layer.parse_frontmatter("No frontmatter here.\n")
        self.assertEqual((name, desc, mtype), ("", "", ""))
        self.assertEqual(body.strip(), "No frontmatter here.")


class TestMemoryLayer(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_tree(self.root)
        self.structural = builder.build_graph(self.root)
        self.withmem = builder.build_graph(self.root, include_memory=True)

    def tearDown(self):
        self._tmp.cleanup()

    # -- gating ------------------------------------------------------------
    def test_no_memory_nodes_when_flag_off(self):
        mem_nodes = [n for n in self.structural.nodes.values() if n.type == "memory"]
        self.assertEqual(mem_nodes, [])

    def test_memory_nodes_present_when_flag_on(self):
        for nid in ("memory/feedback_alpha.md", "memory/project_beta.md", "memory/reference_orphan.md"):
            self.assertTrue(self.withmem.has_node(nid), f"missing {nid}")
            self.assertEqual(self.withmem.nodes[nid].type, "memory")

    def test_node_carries_name_and_description(self):
        node = self.withmem.nodes["memory/feedback_alpha.md"]
        self.assertEqual(node.title, "alpha-slug")
        self.assertEqual(node.purpose, "Alpha description.")

    # -- link resolution ---------------------------------------------------
    def test_stem_link_resolves(self):
        e = self._edge("memory/feedback_alpha.md", "memory/project_beta.md", "links_to")
        self.assertTrue(e.resolved)

    def test_name_slug_link_resolves(self):
        # project_beta links [[alpha-slug]] (the name: slug, not the stem).
        e = self._edge("memory/project_beta.md", "memory/feedback_alpha.md", "links_to")
        self.assertTrue(e.resolved)

    def test_forward_reference_kept_unresolved(self):
        e = self._edge("memory/feedback_alpha.md", "memory/feedback_missing.md", "links_to")
        self.assertFalse(e.resolved)

    # -- cross-layer + membership -----------------------------------------
    def test_cross_layer_reference_to_structural_node(self):
        e = self._edge("memory/project_beta.md", "workflows/foo", "references")
        self.assertTrue(e.resolved)

    def test_membership_edges_from_memory_dir(self):
        for target in ("memory/feedback_alpha.md", "memory/project_beta.md"):
            e = self._edge("memory", target, "contains")
            self.assertTrue(e.resolved)
        # The orphan memory is not in MEMORY.md, so it has no membership edge.
        self.assertIsNone(self._maybe_edge("memory", "memory/reference_orphan.md", "contains"))

    # -- validation severity ----------------------------------------------
    def test_forward_reference_classifies_info(self):
        findings = validate_mod.check_broken_references(self.withmem, self.root)
        match = [f for f in findings if f.subject == "memory/feedback_missing.md"]
        self.assertTrue(match)
        self.assertTrue(all(f.severity == "INFO" for f in match))

    def test_no_new_warns_from_memory_layer(self):
        warns = lambda g: sum(  # noqa: E731
            1 for f in validate_mod.run_checks(g, self.root) if f.severity == "WARN"
        )
        self.assertEqual(warns(self.withmem), warns(self.structural))

    def test_memory_orphan_is_info(self):
        findings = validate_mod.check_orphans(self.withmem)
        match = [f for f in findings if f.subject == "memory/reference_orphan.md"]
        self.assertTrue(match)
        self.assertEqual(match[0].severity, "INFO")

    # -- structural regression --------------------------------------------
    def test_flag_off_default_identical(self):
        a_nodes, a_edges = self.structural.to_dicts()
        b_nodes, b_edges = builder.build_graph(self.root, include_memory=False).to_dicts()
        self.assertEqual(a_nodes, b_nodes)
        self.assertEqual(a_edges, b_edges)

    def test_memory_layer_is_additive_superset(self):
        s_nodes, s_edges = self.structural.to_dicts()
        m_nodes, m_edges = self.withmem.to_dicts()
        s_node_ids = {n["id"] for n in s_nodes}
        m_node_ids = {n["id"] for n in m_nodes}
        self.assertTrue(s_node_ids.issubset(m_node_ids))
        s_keys = {(e["source"], e["target"], e["type"]) for e in s_edges}
        m_keys = {(e["source"], e["target"], e["type"]) for e in m_edges}
        self.assertTrue(s_keys.issubset(m_keys))
        # Structural nodes are byte-identical (the layer never rewrites them).
        s_by_id = {n["id"]: n for n in s_nodes}
        for n in m_nodes:
            if n["id"] in s_by_id:
                self.assertEqual(n, s_by_id[n["id"]])

    # -- meta layers logic -------------------------------------------------
    def test_included_layers_helper(self):
        import run  # noqa: E402
        self.assertEqual(run._included_layers(argparse.Namespace(layer=None)), ["structural"])
        self.assertEqual(
            run._included_layers(argparse.Namespace(layer=["memory"])),
            ["structural", "memory"],
        )

    # -- helpers -----------------------------------------------------------
    def _edge(self, source, target, etype):
        e = self._maybe_edge(source, target, etype)
        if e is None:
            self.fail(f"missing edge {source} --{etype}--> {target}")
        return e

    def _maybe_edge(self, source, target, etype):
        for e in self.withmem.edges:
            if e.source == source and e.target == target and e.type == etype:
                return e
        return None


if __name__ == "__main__":
    unittest.main(verbosity=2)
