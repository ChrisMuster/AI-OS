#!/usr/bin/env python3
"""Unit tests for the knowledge-graph query dispatcher's pure helper.

build_kg_query_argv (in server.py) validates the required-argument matrix and
assembles the run.py argv for query_knowledge_graph. It performs no I/O, so the
matrix and argv construction are tested directly here without spawning a
subprocess or starting the MCP server.

Importing server.py requires the `mcp` package (Python 3.10+); on 3.9 these
tests are skipped, consistent with the rest of the MCP layer.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

try:
    import server  # noqa: E402
    _HAVE_SERVER = True
except Exception:  # pragma: no cover - exercised only on 3.9 / missing mcp
    _HAVE_SERVER = False


@unittest.skipUnless(_HAVE_SERVER, "server.py requires the mcp package (Python 3.10+)")
class TestBuildKgQueryArgv(unittest.TestCase):
    def _argv(self, command, **kwargs):
        argv, error = server.build_kg_query_argv(command, **kwargs)
        self.assertIsNone(error, f"unexpected error for {command}: {error}")
        self.assertIsNotNone(argv)
        # Every argv begins with [interpreter, run.py, command] ...
        self.assertEqual(argv[0], server.PYTHON)
        self.assertTrue(argv[1].endswith("run.py"))
        self.assertEqual(argv[2], command)
        # ... and every read-only command requests JSON output.
        self.assertIn("--json", argv)
        return argv

    # -- required-arg matrix: commands that need nothing --------------------
    def test_argless_commands_build_without_id(self):
        for command in ("validate", "stats", "orphans", "broken"):
            argv = self._argv(command)
            self.assertEqual(argv[3:], ["--json"])

    # -- required-arg matrix: commands that need an id ---------------------
    def test_id_commands_require_id(self):
        for command in ("node", "neighbors", "impact", "subtree", "path", "sessions"):
            argv, error = server.build_kg_query_argv(command)
            self.assertIsNone(argv)
            self.assertIsNotNone(error)
            self.assertIn("id", error)

    def test_id_command_places_positional_before_json(self):
        argv = self._argv("node", id="workflows/audit")
        self.assertEqual(argv[3], "workflows/audit")

    # -- path needs both source and target --------------------------------
    def test_path_requires_target(self):
        argv, error = server.build_kg_query_argv("path", id="AGENTS.md")
        self.assertIsNone(argv)
        self.assertIn("target", error)

    def test_path_builds_both_positionals(self):
        argv = self._argv("path", id="AGENTS.md", target="workflows/audit")
        self.assertEqual(argv[3], "AGENTS.md")
        self.assertEqual(argv[4], "workflows/audit")

    def test_path_undirected_flag(self):
        argv = self._argv("path", id="a", target="b", undirected=True)
        self.assertIn("--undirected", argv)
        argv_default = self._argv("path", id="a", target="b")
        self.assertNotIn("--undirected", argv_default)

    # -- neighbors direction + type --------------------------------------
    def test_neighbors_direction(self):
        self.assertIn("--in", self._argv("neighbors", id="x", direction="in"))
        self.assertIn("--out", self._argv("neighbors", id="x", direction="out"))
        both = self._argv("neighbors", id="x", direction="both")
        self.assertNotIn("--in", both)
        self.assertNotIn("--out", both)

    def test_neighbors_edge_type(self):
        argv = self._argv("neighbors", id="x", edge_type="depends_on")
        self.assertIn("--type", argv)
        self.assertEqual(argv[argv.index("--type") + 1], "depends_on")

    # -- validate flags ---------------------------------------------------
    def test_validate_flags(self):
        argv = self._argv("validate", no_backrefs=True, save=True)
        self.assertIn("--no-backrefs", argv)
        self.assertIn("--save", argv)

    # -- from_index shared flag ------------------------------------------
    def test_from_index_flag(self):
        self.assertIn("--from-index", self._argv("stats", from_index=True))
        self.assertNotIn("--from-index", self._argv("stats"))

    # -- include_memory shared flag --------------------------------------
    def test_include_memory_flag(self):
        argv = self._argv("stats", include_memory=True)
        self.assertIn("--layer", argv)
        self.assertEqual(argv[argv.index("--layer") + 1], "memory")
        self.assertNotIn("--layer", self._argv("stats"))

    # -- include_wiki shared flag ----------------------------------------
    def test_include_wiki_flag(self):
        argv = self._argv("stats", include_wiki=True)
        self.assertIn("--layer", argv)
        self.assertEqual(argv[argv.index("--layer") + 1], "wiki")
        self.assertNotIn("--layer", self._argv("stats"))

    # -- include_journal shared flag -------------------------------------
    def test_include_journal_flag(self):
        argv = self._argv("stats", include_journal=True)
        self.assertIn("--layer", argv)
        self.assertEqual(argv[argv.index("--layer") + 1], "journal")
        self.assertNotIn("--layer", self._argv("stats"))

    # -- include_conversation shared flag --------------------------------
    def test_include_conversation_flag(self):
        argv = self._argv("stats", include_conversation=True)
        self.assertIn("--layer", argv)
        self.assertEqual(argv[argv.index("--layer") + 1], "conversation")
        self.assertNotIn("--layer", self._argv("stats"))

    # -- sessions command -------------------------------------------------
    def test_sessions_positional_id_and_default_limit(self):
        argv = self._argv("sessions", id="workflows/audit")
        self.assertEqual(argv[3], "workflows/audit")
        # --limit is always emitted for sessions (default 10).
        self.assertEqual(argv[argv.index("--limit") + 1], "10")
        self.assertIn("--json", argv)

    def test_sessions_threads_terms_and_filters(self):
        argv = self._argv(
            "sessions", id="workflows/audit", terms="custom", limit=5,
            since="2026-01-01", ai="Claude Code", source="claude-code",
        )
        self.assertEqual(argv[argv.index("--limit") + 1], "5")
        self.assertEqual(argv[argv.index("--terms") + 1], "custom")
        self.assertEqual(argv[argv.index("--since") + 1], "2026-01-01")
        self.assertEqual(argv[argv.index("--ai") + 1], "Claude Code")
        self.assertEqual(argv[argv.index("--source") + 1], "claude-code")

    def test_sessions_omits_unset_filters(self):
        argv = self._argv("sessions", id="workflows/audit")
        self.assertNotIn("--terms", argv)
        self.assertNotIn("--since", argv)
        self.assertNotIn("--ai", argv)
        self.assertNotIn("--source", argv)

    def test_both_content_layers_flag(self):
        # All flags requested → every --layer pair present, in build order.
        argv = self._argv(
            "stats", include_memory=True, include_wiki=True, include_journal=True,
            include_conversation=True,
        )
        layer_values = [argv[i + 1] for i, a in enumerate(argv) if a == "--layer"]
        self.assertEqual(layer_values, ["memory", "wiki", "journal", "conversation"])


if __name__ == "__main__":
    unittest.main()
