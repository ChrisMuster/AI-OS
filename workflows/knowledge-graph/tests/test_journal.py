#!/usr/bin/env python3
"""Unit tests for the Phase 7 opt-in journal (light) layer.

Covers: journal-entry nodes appearing only when the flag is on; one node per
month file with the title taken from the ``# Journal — …`` heading; CONTEXT.md /
LOG.md skipped as entries; intra-journal ``[[stem]]`` resolution; journal->
structural ``[[link]]`` and backtick cross-layer references; forward references
kept unresolved and classified INFO; a linkless entry kept off the orphan list by
membership; journal-entry orphan severity INFO; the meta-layers helper ordering;
and the flag-off byte-for-byte structural regression (identical output, superset
when the layer is added).

Temp trees are not git repositories, so the gitignore-aware prune falls back to
walking all directories — consistent with the other builder/validate tests. (In
the real repo the month files journal/entries/YYYY-MM.md are gitignored and the
journal layer reads them directly; the flag-off regression is what guarantees the
default build is unaffected either way.) Entry prose here is invented, never real
journal content (personal-data isolation).
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
    """A miniature project: a structural workflow a journal entry cross-links,
    plus the journal/entries directory (a tracked structural node) holding three
    invented month files — one with a structural [[link]] and a backtick path,
    one with an intra-journal link plus a forward reference, and one linkless."""
    # Structural side — a workflow a journal entry references.
    _write(
        root / "workflows" / "CONTEXT.md",
        "# Workflows\n\n**Last modified:** 2026-06-22\n\n## Purpose\nContainer.\n\n"
        "## Contents\n- Foo — `workflows/foo/` [[workflows/foo/CONTEXT]] — A workflow.\n",
    )
    _write(
        root / "workflows" / "foo" / "CONTEXT.md",
        "# Foo\n\n**Last modified:** 2026-06-22\n\n## Purpose\nFoo.\n",
    )
    # Journal container + the tracked entries directory (the membership source).
    _write(
        root / "journal" / "CONTEXT.md",
        "# Journal\n\n**Last modified:** 2026-06-22\n\n## Purpose\nContainer.\n\n"
        "## Contents\n- Entries — `journal/entries/` [[journal/entries/CONTEXT]] — Month files.\n",
    )
    _write(
        root / "journal" / "entries" / "CONTEXT.md",
        "# Journal Entries\n\n**Last modified:** 2026-06-22\n\n## Purpose\nMonth files.\n",
    )
    # Month files (invented prose).
    _write(
        root / "journal" / "entries" / "2026-01.md",
        "# Journal — January 2026\n\n## 1 January\n"
        "Tidied up the project and linked to [[workflows/foo/CONTEXT]]. "
        "Also poked at `workflows/foo/` for a bit.\n",
    )
    _write(
        root / "journal" / "entries" / "2026-02.md",
        "# Journal — February 2026\n\n## 2 February\n"
        "Looked back at [[2026-01]] and made a note about a [[missing-thing]] "
        "to write up later.\n",
    )
    _write(
        root / "journal" / "entries" / "2026-03.md",
        "# Journal — March 2026\n\n## 3 March\n"
        "A quiet day with nothing worth cross-linking.\n",
    )


# Convenient entry-id shorthands.
JAN = "journal/entries/2026-01.md"
FEB = "journal/entries/2026-02.md"
MAR = "journal/entries/2026-03.md"
ENTRIES = "journal/entries"


class TestJournalLayer(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_tree(self.root)
        self.structural = builder.build_graph(self.root)
        self.withjournal = builder.build_graph(self.root, include_journal=True)

    def tearDown(self):
        self._tmp.cleanup()

    # -- gating ------------------------------------------------------------
    def test_no_journal_nodes_when_flag_off(self):
        entries = [n for n in self.structural.nodes.values() if n.type == "journal-entry"]
        self.assertEqual(entries, [])

    def test_journal_nodes_present_when_flag_on(self):
        for nid in (JAN, FEB, MAR):
            self.assertTrue(self.withjournal.has_node(nid), f"missing {nid}")
            self.assertEqual(self.withjournal.nodes[nid].type, "journal-entry")

    def test_title_from_heading(self):
        self.assertEqual(self.withjournal.nodes[JAN].title, "Journal — January 2026")
        self.assertEqual(self.withjournal.nodes[FEB].title, "Journal — February 2026")

    # -- skipping non-entry files -----------------------------------------
    def test_context_and_log_not_entries(self):
        for nid in ("journal/entries/CONTEXT.md", "journal/entries/LOG.md"):
            node = self.withjournal.nodes.get(nid)
            self.assertFalse(
                node is not None and node.type == "journal-entry",
                f"{nid} should not be a journal-entry node",
            )

    # -- link resolution ---------------------------------------------------
    def test_intra_journal_link_resolves(self):
        # February's [[2026-01]] resolves to January's entry node.
        self.assertTrue(self._edge(FEB, JAN, "links_to").resolved)

    def test_structural_link_resolves(self):
        # January's [[workflows/foo/CONTEXT]] resolves to the structural node.
        self.assertTrue(self._edge(JAN, "workflows/foo", "links_to").resolved)

    def test_cross_layer_reference_to_structural_node(self):
        e = self._edge(JAN, "workflows/foo", "references")
        self.assertTrue(e.resolved)

    # -- forward reference -------------------------------------------------
    def test_forward_reference_kept_unresolved(self):
        e = self._edge(FEB, "journal/entries/missing-thing.md", "links_to")
        self.assertFalse(e.resolved)

    def test_forward_reference_classifies_info(self):
        findings = validate_mod.check_broken_references(self.withjournal, self.root)
        match = [f for f in findings if f.subject == "journal/entries/missing-thing.md"]
        self.assertTrue(match)
        self.assertTrue(all(f.severity == "INFO" for f in match))

    # -- membership --------------------------------------------------------
    def test_membership_edges_from_entries_dir(self):
        for nid in (JAN, FEB, MAR):
            self.assertTrue(self._edge(ENTRIES, nid, "contains").resolved)

    def test_no_entry_is_orphan_thanks_to_membership(self):
        findings = validate_mod.check_orphans(self.withjournal)
        orphaned = [f for f in findings if f.subject.startswith("journal/entries/2026-")]
        self.assertEqual(orphaned, [])

    # -- validation severity ----------------------------------------------
    def test_journal_entry_orphan_severity_is_info(self):
        # A journal entry with no inbound edge (hand-built, since membership means
        # built entries never orphan) must classify INFO, never WARN.
        g = Graph()
        g.add_node(Node(id="journal/entries/2099-12.md", type="journal-entry", title="Lonely"))
        findings = validate_mod.check_orphans(g)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "INFO")
        self.assertNotIn("journal-entry", validate_mod._ORPHAN_WARN_TYPES)

    def test_no_new_warns_from_journal_layer(self):
        warns = lambda g: sum(  # noqa: E731
            1 for f in validate_mod.run_checks(g, self.root) if f.severity == "WARN"
        )
        self.assertEqual(warns(self.withjournal), warns(self.structural))

    # -- meta layers logic -------------------------------------------------
    def test_included_layers_helper(self):
        import run  # noqa: E402
        self.assertEqual(run._included_layers(argparse.Namespace(layer=None)), ["structural"])
        self.assertEqual(
            run._included_layers(argparse.Namespace(layer=["journal"])),
            ["structural", "journal"],
        )
        # Fixed order regardless of how the flags were passed.
        self.assertEqual(
            run._included_layers(argparse.Namespace(layer=["journal", "wiki", "memory"])),
            ["structural", "memory", "wiki", "journal"],
        )

    # -- structural regression --------------------------------------------
    def test_flag_off_default_identical(self):
        a_nodes, a_edges = self.structural.to_dicts()
        b_nodes, b_edges = builder.build_graph(self.root, include_journal=False).to_dicts()
        self.assertEqual(a_nodes, b_nodes)
        self.assertEqual(a_edges, b_edges)

    def test_journal_layer_is_additive_superset(self):
        s_nodes, s_edges = self.structural.to_dicts()
        j_nodes, j_edges = self.withjournal.to_dicts()
        s_node_ids = {n["id"] for n in s_nodes}
        j_node_ids = {n["id"] for n in j_nodes}
        self.assertTrue(s_node_ids.issubset(j_node_ids))
        s_keys = {(e["source"], e["target"], e["type"]) for e in s_edges}
        j_keys = {(e["source"], e["target"], e["type"]) for e in j_edges}
        self.assertTrue(s_keys.issubset(j_keys))
        # Structural nodes are byte-identical (the layer never rewrites them).
        s_by_id = {n["id"]: n for n in s_nodes}
        for n in j_nodes:
            if n["id"] in s_by_id:
                self.assertEqual(n, s_by_id[n["id"]])

    def test_all_layers_compose(self):
        # memory + wiki + journal together is a superset, and the structural base
        # is still byte-identical.
        both = builder.build_graph(
            self.root, include_memory=True, include_wiki=True, include_journal=True
        )
        self.assertTrue({JAN, FEB, MAR}.issubset(set(both.nodes)))
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
        for e in self.withjournal.edges:
            if e.source == source and e.target == target and e.type == etype:
                return e
        return None


if __name__ == "__main__":
    unittest.main(verbosity=2)
