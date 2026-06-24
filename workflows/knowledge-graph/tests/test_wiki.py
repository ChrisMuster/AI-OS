#!/usr/bin/env python3
"""Unit tests for the Phase 6 opt-in wiki sub-graph layer.

Covers: wiki-page nodes appearing only when the flag is on; per-wiki namespacing
(two wikis that share a page filename do not collide, and a ``[[stem]]`` resolves
within its own wiki); cross-wiki links staying INFO (not linked across wikis);
intra-wiki forward references kept unresolved and classified INFO; wiki-page
orphan severity INFO; CONTEXT.md / LOG.md / operations-log.md skipped as pages;
membership edges from each wikis/<name> node; wiki->structural cross-layer
references; the meta-layers helper; and the flag-off structural regression
(identical output, superset when the layer is added).

Temp trees are not git repositories, so the gitignore-aware prune falls back to
walking all directories — consistent with the other builder/validate tests. (In
the real repo each wikis/<name>/ subtree is gitignored and the wiki layer reads
the pages directly; the flag-off regression is what guarantees the default build
is unaffected either way.)
"""

import argparse
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import builder  # noqa: E402
import validate as validate_mod  # noqa: E402
from graph import Graph, Node  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_tree(root: Path) -> None:
    """A miniature project: a structural workflow plus two wikis that share the
    page name ``common-topic.md`` (the namespacing fixture). Each wiki has an
    ``index.md`` and intra-wiki links; ``alpha`` also has a forward reference, a
    cross-wiki link, a wiki->structural backtick path, and two non-page files
    (CONTEXT.md, operations-log.md) that must be skipped."""
    # Structural side — a workflow a wiki page cross-references.
    _write(
        root / "workflows" / "CONTEXT.md",
        "# Workflows\n\n**Last modified:** 2026-06-22\n\n## Purpose\nContainer.\n\n"
        "## Contents\n- Foo — `workflows/foo/` [[workflows/foo/CONTEXT]] — A workflow.\n",
    )
    _write(
        root / "workflows" / "foo" / "CONTEXT.md",
        "# Foo\n\n**Last modified:** 2026-06-22\n\n## Purpose\nFoo.\n",
    )
    # Wikis container.
    _write(
        root / "wikis" / "CONTEXT.md",
        "# Wikis\n\n**Last modified:** 2026-06-22\n\n## Purpose\nContainer.\n\n"
        "## Contents\n"
        "- Alpha — `wikis/alpha/` [[wikis/alpha/CONTEXT]] — First wiki.\n"
        "- Beta — `wikis/beta/` [[wikis/beta/CONTEXT]] — Second wiki.\n",
    )
    # Wiki alpha.
    _write(
        root / "wikis" / "alpha" / "CONTEXT.md",
        "# Alpha\n\n**Last modified:** 2026-06-22\n\n## Purpose\nAlpha wiki.\n\n"
        "## Contents\n- Pages — `wikis/alpha/wiki/` — Page folder.\n",
    )
    _write(root / "wikis" / "alpha" / "wiki" / "CONTEXT.md", "# Alpha Pages\n\n## Purpose\nPages.\n")
    _write(
        root / "wikis" / "alpha" / "wiki" / "index.md",
        "# Alpha Index\n\nSee [[overview]] and [[common-topic]].\n",
    )
    _write(
        root / "wikis" / "alpha" / "wiki" / "overview.md",
        "# Overview\n\nBack to [[index]]. Tracks `workflows/foo/`.\n"
        "A not-yet-written [[missing]] page, and a [[beta-only]] page that lives "
        "only in the other wiki.\n",
    )
    _write(root / "wikis" / "alpha" / "wiki" / "common-topic.md", "# Common Topic (Alpha)\n\nAlpha.\n")
    _write(root / "wikis" / "alpha" / "wiki" / "operations-log.md", "# Ops Log\n\nSkip me.\n")
    # Wiki beta — shares the page name common-topic.md, and owns beta-only.md.
    _write(
        root / "wikis" / "beta" / "CONTEXT.md",
        "# Beta\n\n**Last modified:** 2026-06-22\n\n## Purpose\nBeta wiki.\n\n"
        "## Contents\n- Pages — `wikis/beta/wiki/` — Page folder.\n",
    )
    _write(root / "wikis" / "beta" / "wiki" / "CONTEXT.md", "# Beta Pages\n\n## Purpose\nPages.\n")
    _write(
        root / "wikis" / "beta" / "wiki" / "index.md",
        "# Beta Index\n\nSee [[common-topic]] and [[beta-only]].\n",
    )
    _write(root / "wikis" / "beta" / "wiki" / "common-topic.md", "# Common Topic (Beta)\n\nBeta.\n")
    _write(root / "wikis" / "beta" / "wiki" / "beta-only.md", "# Beta Only\n\nOnly in beta.\n")


# Convenient page-id shorthands.
A_INDEX = "wikis/alpha/wiki/index.md"
A_OVERVIEW = "wikis/alpha/wiki/overview.md"
A_COMMON = "wikis/alpha/wiki/common-topic.md"
B_INDEX = "wikis/beta/wiki/index.md"
B_COMMON = "wikis/beta/wiki/common-topic.md"
B_ONLY = "wikis/beta/wiki/beta-only.md"


class TestWikiLayer(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_tree(self.root)
        self.structural = builder.build_graph(self.root)
        self.withwiki = builder.build_graph(self.root, include_wiki=True)

    def tearDown(self):
        self._tmp.cleanup()

    # -- gating ------------------------------------------------------------
    def test_no_wiki_page_nodes_when_flag_off(self):
        pages = [n for n in self.structural.nodes.values() if n.type == "wiki-page"]
        self.assertEqual(pages, [])

    def test_wiki_page_nodes_present_when_flag_on(self):
        for nid in (A_INDEX, A_OVERVIEW, A_COMMON, B_INDEX, B_COMMON, B_ONLY):
            self.assertTrue(self.withwiki.has_node(nid), f"missing {nid}")
            self.assertEqual(self.withwiki.nodes[nid].type, "wiki-page")

    def test_page_title_from_heading(self):
        self.assertEqual(self.withwiki.nodes[A_COMMON].title, "Common Topic (Alpha)")
        self.assertEqual(self.withwiki.nodes[B_COMMON].title, "Common Topic (Beta)")

    # -- skipping non-page files ------------------------------------------
    def test_context_log_and_opslog_not_pages(self):
        for nid in (
            "wikis/alpha/wiki/CONTEXT.md",
            "wikis/alpha/wiki/operations-log.md",
            "wikis/beta/wiki/CONTEXT.md",
        ):
            node = self.withwiki.nodes.get(nid)
            self.assertFalse(
                node is not None and node.type == "wiki-page",
                f"{nid} should not be a wiki-page node",
            )

    # -- namespacing (the whole point) ------------------------------------
    def test_same_named_pages_do_not_collide(self):
        self.assertNotEqual(A_COMMON, B_COMMON)
        self.assertTrue(self.withwiki.has_node(A_COMMON))
        self.assertTrue(self.withwiki.has_node(B_COMMON))

    def test_stem_link_resolves_within_own_wiki(self):
        # alpha/index [[common-topic]] -> alpha's page, NOT beta's.
        self.assertTrue(self._edge(A_INDEX, A_COMMON, "links_to").resolved)
        self.assertIsNone(self._maybe_edge(A_INDEX, B_COMMON, "links_to"))
        # beta/index [[common-topic]] -> beta's page, NOT alpha's.
        self.assertTrue(self._edge(B_INDEX, B_COMMON, "links_to").resolved)
        self.assertIsNone(self._maybe_edge(B_INDEX, A_COMMON, "links_to"))

    def test_intra_wiki_link_resolves(self):
        self.assertTrue(self._edge(A_INDEX, A_OVERVIEW, "links_to").resolved)
        self.assertTrue(self._edge(A_OVERVIEW, A_INDEX, "links_to").resolved)

    # -- cross-wiki links stay INFO ---------------------------------------
    def test_cross_wiki_link_not_resolved_across_wikis(self):
        # alpha/overview [[beta-only]] must NOT link to beta's beta-only page.
        self.assertIsNone(self._maybe_edge(A_OVERVIEW, B_ONLY, "links_to"))
        # It stays an unresolved forward reference inside alpha's namespace.
        e = self._edge(A_OVERVIEW, "wikis/alpha/wiki/beta-only.md", "links_to")
        self.assertFalse(e.resolved)
        # The same stem DOES resolve inside beta, where the page exists.
        self.assertTrue(self._edge(B_INDEX, B_ONLY, "links_to").resolved)

    # -- forward reference -------------------------------------------------
    def test_forward_reference_kept_unresolved(self):
        e = self._edge(A_OVERVIEW, "wikis/alpha/wiki/missing.md", "links_to")
        self.assertFalse(e.resolved)

    def test_forward_reference_classifies_info(self):
        findings = validate_mod.check_broken_references(self.withwiki, self.root)
        match = [f for f in findings if f.subject == "wikis/alpha/wiki/missing.md"]
        self.assertTrue(match)
        self.assertTrue(all(f.severity == "INFO" for f in match))

    # -- cross-layer + membership -----------------------------------------
    def test_cross_layer_reference_to_structural_node(self):
        e = self._edge(A_OVERVIEW, "workflows/foo", "references")
        self.assertTrue(e.resolved)

    def test_membership_edges_from_each_wiki(self):
        for src, target in (
            ("wikis/alpha", A_INDEX),
            ("wikis/alpha", A_OVERVIEW),
            ("wikis/alpha", A_COMMON),
            ("wikis/beta", B_INDEX),
            ("wikis/beta", B_COMMON),
        ):
            self.assertTrue(self._edge(src, target, "contains").resolved)

    def test_no_page_is_orphan_thanks_to_membership(self):
        findings = validate_mod.check_orphans(self.withwiki)
        orphaned_pages = [
            f for f in findings if f.subject.startswith("wikis/") and "/wiki/" in f.subject
        ]
        self.assertEqual(orphaned_pages, [])

    # -- validation severity ----------------------------------------------
    def test_wiki_page_orphan_severity_is_info(self):
        # A wiki page with no inbound edge (hand-built, since membership means
        # built pages never orphan) must classify INFO, never WARN.
        g = Graph()
        g.add_node(Node(id="wikis/x/wiki/lonely.md", type="wiki-page", title="Lonely"))
        findings = validate_mod.check_orphans(g)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "INFO")
        self.assertNotIn("wiki-page", validate_mod._ORPHAN_WARN_TYPES)

    def test_no_new_warns_from_wiki_layer(self):
        warns = lambda g: sum(  # noqa: E731
            1 for f in validate_mod.run_checks(g, self.root) if f.severity == "WARN"
        )
        self.assertEqual(warns(self.withwiki), warns(self.structural))

    # -- meta layers logic -------------------------------------------------
    def test_included_layers_helper(self):
        import run  # noqa: E402
        self.assertEqual(run._included_layers(argparse.Namespace(layer=None)), ["structural"])
        self.assertEqual(
            run._included_layers(argparse.Namespace(layer=["wiki"])),
            ["structural", "wiki"],
        )
        # Fixed order regardless of how the flags were passed.
        self.assertEqual(
            run._included_layers(argparse.Namespace(layer=["wiki", "memory"])),
            ["structural", "memory", "wiki"],
        )

    # -- structural regression --------------------------------------------
    def test_flag_off_default_identical(self):
        a_nodes, a_edges = self.structural.to_dicts()
        b_nodes, b_edges = builder.build_graph(self.root, include_wiki=False).to_dicts()
        self.assertEqual(a_nodes, b_nodes)
        self.assertEqual(a_edges, b_edges)

    def test_wiki_layer_is_additive_superset(self):
        s_nodes, s_edges = self.structural.to_dicts()
        w_nodes, w_edges = self.withwiki.to_dicts()
        s_node_ids = {n["id"] for n in s_nodes}
        w_node_ids = {n["id"] for n in w_nodes}
        self.assertTrue(s_node_ids.issubset(w_node_ids))
        s_keys = {(e["source"], e["target"], e["type"]) for e in s_edges}
        w_keys = {(e["source"], e["target"], e["type"]) for e in w_edges}
        self.assertTrue(s_keys.issubset(w_keys))
        # Structural nodes are byte-identical (the layer never rewrites them).
        s_by_id = {n["id"]: n for n in s_nodes}
        for n in w_nodes:
            if n["id"] in s_by_id:
                self.assertEqual(n, s_by_id[n["id"]])

    def test_both_layers_compose(self):
        # memory + wiki together is a superset of each alone, and the structural
        # base is still byte-identical.
        both = builder.build_graph(self.root, include_memory=True, include_wiki=True)
        b_ids = set(both.nodes)
        self.assertTrue({A_INDEX, B_COMMON}.issubset(b_ids))
        s_nodes, _ = self.structural.to_dicts()
        s_by_id = {n["id"]: n for n in s_nodes}
        bo_nodes, _ = both.to_dicts()
        for n in bo_nodes:
            if n["id"] in s_by_id:
                self.assertEqual(n, s_by_id[n["id"]])

    # -- helpers -----------------------------------------------------------
    def _edge(self, source, target, etype):
        e = self._maybe_edge(source, target, etype)
        if e is None:
            self.fail(f"missing edge {source} --{etype}--> {target}")
        return e

    def _maybe_edge(self, source, target, etype):
        for e in self.withwiki.edges:
            if e.source == source and e.target == target and e.type == etype:
                return e
        return None


if __name__ == "__main__":
    unittest.main(verbosity=2)
