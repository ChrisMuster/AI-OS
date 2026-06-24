#!/usr/bin/env python3
"""Unit tests for the read-only ``sessions`` cross-reference command.

Covers the two pure pieces — ``session_query_terms`` (title-first derivation with
a --terms override and an id-segment fallback, all FTS5-sanitised) and
``run_session_search`` (best-effort subprocess wrapper that returns ``[]`` on any
failure and never raises) — plus a light ``cmd_sessions`` integration check that
the payload shape is right and an unknown id still exits 2. No real session index
is touched: the searcher is monkeypatched, so these tests are deterministic on
any machine, with or without a session database.
"""

import argparse
import io
import json
import sys
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run  # noqa: E402
from graph import Graph, Node  # noqa: E402


def _node(node_id, title=None):
    """A minimal stand-in for a graph Node; session_query_terms only reads
    .id and .title."""
    return types.SimpleNamespace(id=node_id, title=title)


class _FakeProc:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


# ---------------------------------------------------------------------------
# session_query_terms — term derivation (Decision: title-first + override)
# ---------------------------------------------------------------------------
class TestSessionQueryTerms(unittest.TestCase):
    def test_title_used_when_present(self):
        self.assertEqual(
            run.session_query_terms(_node("workflows/audit", "Audit")), '"Audit"'
        )

    def test_multiword_title_quoted_and_anded(self):
        # Space between bare terms is an implicit FTS5 AND; each token is quoted.
        self.assertEqual(
            run.session_query_terms(_node("workflows/kg", "Knowledge Graph")),
            '"Knowledge" "Graph"',
        )

    def test_override_takes_priority_over_title(self):
        self.assertEqual(
            run.session_query_terms(
                _node("workflows/audit", "Audit"), override="session search"
            ),
            '"session" "search"',
        )

    def test_id_segment_fallback_when_no_title(self):
        self.assertEqual(
            run.session_query_terms(_node("workflows/audit", None)), '"audit"'
        )

    def test_id_segment_fallback_when_title_blank(self):
        # Title is all whitespace → sanitises empty → falls back to the id segment.
        self.assertEqual(
            run.session_query_terms(_node("workflows/web-research", "   ")),
            '"web" "research"',
        )

    def test_fts5_sanitises_operator_characters(self):
        # Slashes, quotes, and FTS5 operators must never reach the query string.
        terms = run.session_query_terms(_node("x", 'a/b "c" OR* -d'))
        self.assertEqual(terms, '"a" "b" "c" "OR" "d"')
        self.assertNotIn("/", terms)
        self.assertNotIn("*", terms)

    def test_fts5_safe_empty_input(self):
        self.assertEqual(run._fts5_safe(""), "")
        self.assertEqual(run._fts5_safe("!!! ??? ///"), "")


# ---------------------------------------------------------------------------
# run_session_search — best-effort subprocess wrapper (never raises)
# ---------------------------------------------------------------------------
class TestRunSessionSearch(unittest.TestCase):
    def setUp(self):
        self._orig_run = run.subprocess.run

    def tearDown(self):
        run.subprocess.run = self._orig_run

    def _patch(self, returncode=0, stdout="[]"):
        captured = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            return _FakeProc(returncode, stdout)

        run.subprocess.run = fake_run
        return captured

    def test_empty_terms_short_circuits_without_spawning(self):
        def boom(*a, **k):
            raise AssertionError("subprocess.run must not run for empty terms")

        run.subprocess.run = boom
        self.assertEqual(run.run_session_search(""), [])

    def test_valid_payload_is_parsed(self):
        self._patch(0, '[{"session_id": "x", "snippet": "s"}]')
        self.assertEqual(
            run.run_session_search('"audit"'),
            [{"session_id": "x", "snippet": "s"}],
        )

    def test_nonzero_exit_returns_empty(self):
        self._patch(2, "[]")
        self.assertEqual(run.run_session_search('"audit"'), [])

    def test_unparseable_stdout_returns_empty(self):
        self._patch(0, "Searching... not json at all")
        self.assertEqual(run.run_session_search('"audit"'), [])

    def test_non_list_json_returns_empty(self):
        self._patch(0, '{"not": "a list"}')
        self.assertEqual(run.run_session_search('"audit"'), [])

    def test_subprocess_exception_returns_empty(self):
        def boom(*a, **k):
            raise FileNotFoundError("nope")

        run.subprocess.run = boom
        self.assertEqual(run.run_session_search('"audit"'), [])

    def test_argv_threads_all_filters(self):
        captured = self._patch(0, "[]")
        run.run_session_search(
            '"audit"', limit=5, since="2026-01-01", ai="Claude Code",
            source="claude-code",
        )
        argv = captured["argv"]
        self.assertEqual(argv[0], sys.executable)
        self.assertTrue(argv[1].endswith("search.py"))
        self.assertEqual(argv[2], '"audit"')
        self.assertIn("--json", argv)
        self.assertEqual(argv[argv.index("--limit") + 1], "5")
        self.assertEqual(argv[argv.index("--since") + 1], "2026-01-01")
        self.assertEqual(argv[argv.index("--ai") + 1], "Claude Code")
        self.assertEqual(argv[argv.index("--source") + 1], "claude-code")

    def test_optional_filters_omitted_when_absent(self):
        captured = self._patch(0, "[]")
        run.run_session_search('"audit"')
        argv = captured["argv"]
        self.assertNotIn("--since", argv)
        self.assertNotIn("--ai", argv)
        self.assertNotIn("--source", argv)

    def test_missing_script_real_subprocess_returns_empty(self):
        # Genuinely spawn the interpreter against a missing script (the
        # absent-search.py guarantee), using the un-patched subprocess.run.
        orig = run.SESSION_SEARCH_PY
        run.SESSION_SEARCH_PY = orig.parent / "does_not_exist_zzz.py"
        try:
            self.assertEqual(run.run_session_search('"audit"'), [])
        finally:
            run.SESSION_SEARCH_PY = orig


# ---------------------------------------------------------------------------
# cmd_sessions — payload shape + resolution behaviour (searcher monkeypatched)
# ---------------------------------------------------------------------------
class TestCmdSessions(unittest.TestCase):
    def setUp(self):
        self._orig_load = run._load_graph
        self._orig_search = run.run_session_search
        g = Graph()
        g.add_node(Node(id="workflows/audit", type="workflow", title="Audit"))
        run._load_graph = lambda args: g

    def tearDown(self):
        run._load_graph = self._orig_load
        run.run_session_search = self._orig_search

    def _args(self, **kw):
        base = dict(
            id="workflows/audit", terms=None, limit=10, since=None, ai=None,
            source=None, json=True, from_index=False, layer=None,
        )
        base.update(kw)
        return argparse.Namespace(**base)

    def test_payload_shape(self):
        run.run_session_search = lambda *a, **k: [
            {"session_id": "x", "session_title": "T", "timestamp": "2026-06-01",
             "source": "claude-code", "ai_identity": "Claude Code", "snippet": "s"}
        ]
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run.cmd_sessions(self._args())
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["id"], "workflows/audit")
        self.assertEqual(payload["terms"], '"Audit"')
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["sessions"][0]["session_id"], "x")

    def test_empty_result_is_clean(self):
        run.run_session_search = lambda *a, **k: []
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run.cmd_sessions(self._args())
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["sessions"], [])

    def test_override_terms_threaded(self):
        seen = {}

        def fake_search(terms, **kw):
            seen["terms"] = terms
            return []

        run.run_session_search = fake_search
        buf = io.StringIO()
        with redirect_stdout(buf):
            run.cmd_sessions(self._args(terms="custom override"))
        self.assertEqual(seen["terms"], '"custom" "override"')

    def test_unknown_id_exits_2(self):
        with self.assertRaises(SystemExit) as ctx:
            run.cmd_sessions(self._args(id="not-a-node"))
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
