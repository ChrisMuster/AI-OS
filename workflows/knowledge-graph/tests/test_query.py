#!/usr/bin/env python3
"""Unit tests for graph traversal (neighbors, impact, path, subtree) and the
run.py unknown-id handling.

Traversal is tested on small hand-built graphs for precise, deterministic
control over edges; one test also runs against build_graph to confirm the
loader/round-trip path agrees with a live build.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import builder  # noqa: E402
import run  # noqa: E402
from graph import Graph, Node, Edge  # noqa: E402


def _chain_graph() -> Graph:
    """a --depends_on--> b --depends_on--> c, plus a child hierarchy
    p -> p/q -> p/q/r, and a links_to from a to c (to test path/undirected)."""
    g = Graph()
    for nid in ("a", "b", "c", "p", "p/q", "p/q/r"):
        g.add_node(Node(id=nid, type="workflow"))
    g.add_edge(Edge("a", "b", "depends_on"))
    g.add_edge(Edge("b", "c", "depends_on"))
    g.add_edge(Edge("p", "p/q", "child"))
    g.add_edge(Edge("p/q", "p/q/r", "child"))
    return g


class TestTraversal(unittest.TestCase):
    def setUp(self):
        self.g = _chain_graph()

    # -- neighbors ----------------------------------------------------------
    def test_out_edges_filtered_by_type(self):
        self.assertEqual([e.target for e in self.g.out_edges("a", "depends_on")], ["b"])
        self.assertEqual(self.g.out_edges("a", "child"), [])

    def test_in_edges(self):
        self.assertEqual([e.source for e in self.g.in_edges("c")], ["b"])

    # -- impact (reverse reachability) -------------------------------------
    def test_impact_multi_hop(self):
        reach = self.g.reverse_reachable("c", {"depends_on"})
        self.assertEqual(reach["b"], (1, "depends_on"))
        self.assertEqual(reach["a"], (2, "depends_on"))

    def test_impact_respects_edge_type_filter(self):
        # No contains/links_to inbound to c, so filtering those yields nothing.
        self.assertEqual(self.g.reverse_reachable("c", {"contains"}), {})

    def test_impact_first_hop_is_preserved_across_distance(self):
        reach = self.g.reverse_reachable("c", {"depends_on"})
        # both reached via the same first hop (b --depends_on--> c)
        self.assertEqual(reach["a"][1], "depends_on")

    def test_impact_cycle_safe(self):
        g = Graph()
        for nid in ("x", "y"):
            g.add_node(Node(id=nid, type="workflow"))
        g.add_edge(Edge("x", "y", "depends_on"))
        g.add_edge(Edge("y", "x", "depends_on"))
        reach = g.reverse_reachable("x", {"depends_on"})
        self.assertIn("y", reach)
        self.assertNotIn("x", reach)  # start node never re-enters

    # -- shortest path ------------------------------------------------------
    def test_shortest_path_directed(self):
        self.assertEqual(self.g.shortest_path("a", "c"), ["a", "b", "c"])

    def test_shortest_path_none_when_unreachable(self):
        self.assertIsNone(self.g.shortest_path("c", "a"))

    def test_shortest_path_undirected(self):
        self.assertEqual(self.g.shortest_path("c", "a", undirected=True), ["c", "b", "a"])

    def test_shortest_path_self(self):
        self.assertEqual(self.g.shortest_path("a", "a"), ["a"])

    # -- subtree ------------------------------------------------------------
    def test_subtree_via_child_edges(self):
        self.assertEqual(self.g.subtree("p"), ["p/q", "p/q/r"])

    def test_subtree_leaf(self):
        self.assertEqual(self.g.subtree("p/q/r"), [])

    def test_subtree_ignores_non_child_edges(self):
        # depends_on edges must never appear in a subtree.
        self.assertEqual(self.g.subtree("a"), [])


class TestAdjacencyCacheInvalidation(unittest.TestCase):
    """The adjacency indexes are cached after the first traversal. Adding an edge
    afterwards must invalidate the cache (the ``_dirty`` flag) so later queries
    reflect it. Normal builds never add edges post-traversal, but this guards the
    invariant against regressions."""

    def test_edge_added_after_traversal_is_reflected(self):
        g = _chain_graph()
        # First traversal builds and caches the adjacency indexes.
        self.assertEqual([e.target for e in g.out_edges("c")], [])
        # Add a new edge after the cache is warm.
        g.add_edge(Edge("c", "a", "depends_on"))
        # The new edge must show up in both directions.
        self.assertEqual([e.target for e in g.out_edges("c")], ["a"])
        self.assertEqual([e.source for e in g.in_edges("a")], ["c"])

    def test_duplicate_edge_does_not_falsely_invalidate(self):
        g = _chain_graph()
        g.out_edges("a")  # warm the cache
        before = g._out
        g.add_edge(Edge("a", "b", "depends_on"))  # duplicate, deduped by add_edge
        # A de-duplicated edge is dropped before the dirty flag is set, so the
        # cache stays valid and is reused on the next traversal.
        self.assertFalse(g._dirty)
        self.assertIs(g._out, before)


class TestLoaderRoundTrip(unittest.TestCase):
    def test_from_dicts_round_trip(self):
        g = _chain_graph()
        nodes, edges = g.to_dicts()
        g2 = Graph.from_dicts(nodes, edges)
        self.assertEqual(g2.subtree("p"), ["p/q", "p/q/r"])
        self.assertEqual(g2.shortest_path("a", "c"), ["a", "b", "c"])


class TestUnknownId(unittest.TestCase):
    def test_resolve_known_id(self):
        g = _chain_graph()
        self.assertEqual(run._resolve_id(g, "a"), "a")

    def test_resolve_unknown_id_exits(self):
        g = _chain_graph()
        with self.assertRaises(SystemExit) as ctx:
            run._resolve_id(g, "nope")
        self.assertEqual(ctx.exception.code, 2)

    def test_resolve_suggests_close_match(self):
        g = Graph()
        g.add_node(Node(id="workflows/audit", type="workflow"))
        # difflib should still raise, but the suggestion path is exercised.
        with self.assertRaises(SystemExit):
            run._resolve_id(g, "workflows/audti")


class TestQueryIntegration(unittest.TestCase):
    """A small build_graph-based check so traversal is verified against the real
    builder output, not only hand-built graphs."""

    def test_subtree_against_built_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workflows" / "foo" / "scripts").mkdir(parents=True)
            (root / "workflows" / "CONTEXT.md").write_bytes("# W\n\n## Purpose\nc.\n\n## Contents\n- `workflows/foo/`\n".encode("utf-8"))
            (root / "workflows" / "foo" / "CONTEXT.md").write_bytes("# Foo\n\n## Purpose\nf.\n\n## Contents\n- `workflows/foo/scripts/`\n".encode("utf-8"))
            (root / "workflows" / "foo" / "scripts" / "CONTEXT.md").write_bytes("# S\n\n## Purpose\ns.\n".encode("utf-8"))
            g = builder.build_graph(root)
            self.assertEqual(
                g.subtree("workflows"), ["workflows/foo", "workflows/foo/scripts"]
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
