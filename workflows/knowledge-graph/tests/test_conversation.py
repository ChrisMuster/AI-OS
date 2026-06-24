#!/usr/bin/env python3
"""Unit tests for the Phase 8 opt-in conversation layer.

Covers both shapes the layer indexes:
  - **Standalone files** (journal-like): one ``conversation`` node per
    ``conversations/*.md``, intra-pool ``[[stem]]`` resolution, conversation->
    structural ``[[link]]`` and backtick references, and forward-reference INFO.
  - **Doc-sets** (wiki-like namespaced sub-graphs): one ``conversation-doc`` node
    per page, per-doc-set ``[[stem]]`` resolution that keeps cross-namespace links
    INFO (two doc-sets sharing a page filename do not collide), and CONTEXT.md /
    LOG.md / non-Markdown files skipped as pages.

Plus: nodes appear only when the flag is on; titles from the ``# Title`` heading;
membership from the tracked ``conversations`` node keeps a linkless conversation
off the orphan list; orphan severity INFO for both node types; the layer-order
helper includes conversation; four-layer composition; and the flag-off
byte-for-byte structural regression.

Temp trees are not git repositories, so the gitignore-aware prune falls back to
walking all directories — consistent with the other builder/validate tests. (In
the real repo the standalone files and doc-set contents are gitignored and the
conversation layer reads them directly; the flag-off regression is what
guarantees the default build is unaffected either way.) All conversation prose
and titles here are invented, never real saved-conversation content
(personal-data isolation).
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
    """A miniature project: a structural workflow a conversation cross-links, the
    tracked conversations directory (the membership source) with three invented
    standalone files, and two doc-sets that share the page filename ``shared.md``
    (the namespacing fixture)."""
    # Structural side — a workflow a conversation references.
    _write(
        root / "workflows" / "CONTEXT.md",
        "# Workflows\n\n**Last modified:** 2026-06-22\n\n## Purpose\nContainer.\n\n"
        "## Contents\n- Foo — `workflows/foo/` [[workflows/foo/CONTEXT]] — A workflow.\n",
    )
    _write(
        root / "workflows" / "foo" / "CONTEXT.md",
        "# Foo\n\n**Last modified:** 2026-06-22\n\n## Purpose\nFoo.\n",
    )
    # Conversations container (the tracked membership source). Its Contents lists
    # the two doc-set directories so they are structurally contained (no WARN).
    _write(
        root / "conversations" / "CONTEXT.md",
        "# Conversations\n\n**Last modified:** 2026-06-22\n\n## Purpose\nArchive.\n\n"
        "## Contents\n"
        "- Pack A — `conversations/pack-a/` [[conversations/pack-a/CONTEXT]] — A doc-set.\n"
        "- Pack B — `conversations/pack-b/` [[conversations/pack-b/CONTEXT]] — A doc-set.\n",
    )
    # Standalone conversation files (invented prose).
    _write(
        root / "conversations" / "2026-01-01-alpha.md",
        "# Alpha Conversation\n\n## Summary\n"
        "Explored [[workflows/foo/CONTEXT]] and looked back at [[2026-01-02-beta]]. "
        "Also poked at `workflows/foo/` for a while.\n",
    )
    _write(
        root / "conversations" / "2026-01-02-beta.md",
        "# Beta Conversation\n\n## Summary\n"
        "Left a thread to follow up in a [[missing-convo]] not yet saved.\n",
    )
    _write(
        root / "conversations" / "2026-01-03-gamma.md",
        "# Gamma Conversation\n\n## Summary\n"
        "A short chat with nothing worth cross-linking.\n",
    )
    # Doc-set pack-a: CONTEXT.md + LOG.md (skipped as pages), a non-Markdown file
    # (skipped), and two real pages. START-HERE links to its own shared page, to a
    # structural node, and to [[index]] which only exists in pack-b (stays INFO).
    _write(
        root / "conversations" / "pack-a" / "CONTEXT.md",
        "# Pack A\n\n**Last modified:** 2026-06-22\n\n## Purpose\nDoc-set.\n",
    )
    _write(
        root / "conversations" / "pack-a" / "LOG.md",
        "# Pack A — Log\n",
    )
    _write(
        root / "conversations" / "pack-a" / "notes.txt",
        "not markdown, must be skipped",
    )
    _write(
        root / "conversations" / "pack-a" / "START-HERE.md",
        "# Start Here\n\nSee [[shared]] and [[index]]. Tracks `workflows/foo/`.\n",
    )
    _write(
        root / "conversations" / "pack-a" / "shared.md",
        "# Shared (Pack A)\n\nPack A's shared page.\n",
    )
    # Doc-set pack-b: shares the page name shared.md, and owns index.md.
    _write(
        root / "conversations" / "pack-b" / "CONTEXT.md",
        "# Pack B\n\n**Last modified:** 2026-06-22\n\n## Purpose\nDoc-set.\n",
    )
    _write(
        root / "conversations" / "pack-b" / "index.md",
        "# Pack B Index\n\nSee [[shared]].\n",
    )
    _write(
        root / "conversations" / "pack-b" / "shared.md",
        "# Shared (Pack B)\n\nPack B's shared page.\n",
    )


# Convenient node-id shorthands.
S_ALPHA = "conversations/2026-01-01-alpha.md"
S_BETA = "conversations/2026-01-02-beta.md"
S_GAMMA = "conversations/2026-01-03-gamma.md"
PA_START = "conversations/pack-a/START-HERE.md"
PA_SHARED = "conversations/pack-a/shared.md"
PB_INDEX = "conversations/pack-b/index.md"
PB_SHARED = "conversations/pack-b/shared.md"
CONV = "conversations"

ALL_CONV_IDS = (S_ALPHA, S_BETA, S_GAMMA, PA_START, PA_SHARED, PB_INDEX, PB_SHARED)


class TestConversationLayer(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_tree(self.root)
        self.structural = builder.build_graph(self.root)
        self.withconv = builder.build_graph(self.root, include_conversation=True)

    def tearDown(self):
        self._tmp.cleanup()

    # -- gating ------------------------------------------------------------
    def test_no_conversation_nodes_when_flag_off(self):
        conv = [
            n for n in self.structural.nodes.values()
            if n.type in ("conversation", "conversation-doc")
        ]
        self.assertEqual(conv, [])

    def test_conversation_nodes_present_when_flag_on(self):
        for nid in (S_ALPHA, S_BETA, S_GAMMA):
            self.assertTrue(self.withconv.has_node(nid), f"missing {nid}")
            self.assertEqual(self.withconv.nodes[nid].type, "conversation")
        for nid in (PA_START, PA_SHARED, PB_INDEX, PB_SHARED):
            self.assertTrue(self.withconv.has_node(nid), f"missing {nid}")
            self.assertEqual(self.withconv.nodes[nid].type, "conversation-doc")

    def test_title_from_heading(self):
        self.assertEqual(self.withconv.nodes[S_ALPHA].title, "Alpha Conversation")
        self.assertEqual(self.withconv.nodes[PA_SHARED].title, "Shared (Pack A)")
        self.assertEqual(self.withconv.nodes[PB_SHARED].title, "Shared (Pack B)")

    # -- skipping non-conversation files ----------------------------------
    def test_context_and_log_not_conversation_nodes(self):
        for nid in (
            "conversations/CONTEXT.md",
            "conversations/LOG.md",
            "conversations/pack-a/CONTEXT.md",
            "conversations/pack-a/LOG.md",
        ):
            node = self.withconv.nodes.get(nid)
            self.assertFalse(
                node is not None and node.type in ("conversation", "conversation-doc"),
                f"{nid} should not be a conversation node",
            )

    def test_non_markdown_file_skipped(self):
        self.assertFalse(self.withconv.has_node("conversations/pack-a/notes.txt"))

    # -- standalone link resolution ---------------------------------------
    def test_intra_standalone_link_resolves(self):
        # Alpha's [[2026-01-02-beta]] resolves to Beta's standalone node.
        self.assertTrue(self._edge(S_ALPHA, S_BETA, "links_to").resolved)

    def test_standalone_structural_link_resolves(self):
        self.assertTrue(self._edge(S_ALPHA, "workflows/foo", "links_to").resolved)

    def test_standalone_cross_layer_reference(self):
        self.assertTrue(self._edge(S_ALPHA, "workflows/foo", "references").resolved)

    def test_standalone_forward_reference_kept_unresolved(self):
        e = self._edge(S_BETA, "conversations/missing-convo.md", "links_to")
        self.assertFalse(e.resolved)

    def test_standalone_forward_reference_classifies_info(self):
        findings = validate_mod.check_broken_references(self.withconv, self.root)
        match = [f for f in findings if f.subject == "conversations/missing-convo.md"]
        self.assertTrue(match)
        self.assertTrue(all(f.severity == "INFO" for f in match))

    # -- doc-set namespacing (the whole point) ----------------------------
    def test_same_named_pages_do_not_collide(self):
        self.assertNotEqual(PA_SHARED, PB_SHARED)
        self.assertTrue(self.withconv.has_node(PA_SHARED))
        self.assertTrue(self.withconv.has_node(PB_SHARED))

    def test_stem_link_resolves_within_own_docset(self):
        # pack-a START-HERE [[shared]] -> pack-a's page, NOT pack-b's.
        self.assertTrue(self._edge(PA_START, PA_SHARED, "links_to").resolved)
        self.assertIsNone(self._maybe_edge(PA_START, PB_SHARED, "links_to"))
        # pack-b index [[shared]] -> pack-b's page, NOT pack-a's.
        self.assertTrue(self._edge(PB_INDEX, PB_SHARED, "links_to").resolved)
        self.assertIsNone(self._maybe_edge(PB_INDEX, PA_SHARED, "links_to"))

    def test_cross_namespace_link_stays_info(self):
        # pack-a START-HERE [[index]] must NOT link to pack-b's index page;
        # it stays an unresolved forward reference inside pack-a's namespace.
        self.assertIsNone(self._maybe_edge(PA_START, PB_INDEX, "links_to"))
        e = self._edge(PA_START, "conversations/pack-a/index.md", "links_to")
        self.assertFalse(e.resolved)

    def test_docset_structural_link_resolves(self):
        self.assertTrue(self._edge(PA_START, "workflows/foo", "references").resolved)

    # -- membership --------------------------------------------------------
    def test_membership_edges_from_conversations_dir(self):
        for nid in ALL_CONV_IDS:
            self.assertTrue(self._edge(CONV, nid, "contains").resolved)

    def test_no_conversation_is_orphan_thanks_to_membership(self):
        findings = validate_mod.check_orphans(self.withconv)
        orphaned = [f for f in findings if f.subject in ALL_CONV_IDS]
        self.assertEqual(orphaned, [])

    # -- validation severity ----------------------------------------------
    def test_conversation_orphan_severity_is_info(self):
        # Both conversation node types, hand-built with no inbound edge, must
        # classify INFO, never WARN (membership means built nodes never orphan).
        g = Graph()
        g.add_node(Node(id="conversations/2099-12-31-lonely.md", type="conversation", title="A"))
        g.add_node(Node(id="conversations/pack-z/lonely.md", type="conversation-doc", title="B"))
        findings = validate_mod.check_orphans(g)
        self.assertEqual(len(findings), 2)
        self.assertTrue(all(f.severity == "INFO" for f in findings))
        self.assertNotIn("conversation", validate_mod._ORPHAN_WARN_TYPES)
        self.assertNotIn("conversation-doc", validate_mod._ORPHAN_WARN_TYPES)

    def test_no_new_warns_from_conversation_layer(self):
        warns = lambda g: sum(  # noqa: E731
            1 for f in validate_mod.run_checks(g, self.root) if f.severity == "WARN"
        )
        self.assertEqual(warns(self.withconv), warns(self.structural))

    # -- meta layers logic -------------------------------------------------
    def test_included_layers_helper(self):
        import run  # noqa: E402
        self.assertEqual(run._included_layers(argparse.Namespace(layer=None)), ["structural"])
        self.assertEqual(
            run._included_layers(argparse.Namespace(layer=["conversation"])),
            ["structural", "conversation"],
        )
        # Fixed order regardless of how the flags were passed.
        self.assertEqual(
            run._included_layers(
                argparse.Namespace(layer=["conversation", "journal", "wiki", "memory"])
            ),
            ["structural", "memory", "wiki", "journal", "conversation"],
        )

    # -- structural regression --------------------------------------------
    def test_flag_off_default_identical(self):
        a_nodes, a_edges = self.structural.to_dicts()
        b_nodes, b_edges = builder.build_graph(
            self.root, include_conversation=False
        ).to_dicts()
        self.assertEqual(a_nodes, b_nodes)
        self.assertEqual(a_edges, b_edges)

    def test_conversation_layer_is_additive_superset(self):
        s_nodes, s_edges = self.structural.to_dicts()
        c_nodes, c_edges = self.withconv.to_dicts()
        s_node_ids = {n["id"] for n in s_nodes}
        c_node_ids = {n["id"] for n in c_nodes}
        self.assertTrue(s_node_ids.issubset(c_node_ids))
        s_keys = {(e["source"], e["target"], e["type"]) for e in s_edges}
        c_keys = {(e["source"], e["target"], e["type"]) for e in c_edges}
        self.assertTrue(s_keys.issubset(c_keys))
        # Structural nodes are byte-identical (the layer never rewrites them).
        s_by_id = {n["id"]: n for n in s_nodes}
        for n in c_nodes:
            if n["id"] in s_by_id:
                self.assertEqual(n, s_by_id[n["id"]])

    def test_all_layers_compose(self):
        # memory + wiki + journal + conversation together is a superset, and the
        # structural base is still byte-identical.
        both = builder.build_graph(
            self.root,
            include_memory=True,
            include_wiki=True,
            include_journal=True,
            include_conversation=True,
        )
        self.assertTrue(set(ALL_CONV_IDS).issubset(set(both.nodes)))
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
        for e in self.withconv.edges:
            if e.source == source and e.target == target and e.type == etype:
                return e
        return None


if __name__ == "__main__":
    unittest.main(verbosity=2)
