#!/usr/bin/env python3
"""Tests for search.py's --json output mode.

--json is the contract consumed by the knowledge-graph `sessions` command: a
bare JSON list on stdout and nothing else, with an absent index yielding an
empty list rather than an error. These tests exercise the CLI as a subprocess
(the real contract) and stay deterministic on any machine — a nonsense query
matches nothing whether or not a session database is present, so stdout is an
empty JSON list either way. Each result dict (when present) carries the
documented search() fields.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

SEARCH_PY = Path(__file__).resolve().parent.parent / "scripts" / "search.py"

# A token that will not appear in any transcript, so the result is [] regardless
# of whether this machine has a session index — keeping the test deterministic
# and touching no real personal content.
NONSENSE = "zzqxnonmatchtoken1234567890"

_EXPECTED_KEYS = {
    "snippet", "hostname", "source", "session_id", "session_title",
    "ai_identity", "timestamp", "role", "rank",
}


def _run_json(query, *extra):
    return subprocess.run(
        [sys.executable, str(SEARCH_PY), query, "--json", *extra],
        capture_output=True, text=True, encoding="utf-8",
    )


class TestJsonOutput(unittest.TestCase):
    def test_emits_a_bare_json_list(self):
        proc = _run_json(NONSENSE, "--limit", "5")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)  # raises if stdout is not pure JSON
        self.assertIsInstance(data, list)

    def test_nonsense_query_is_empty(self):
        proc = _run_json(NONSENSE)
        self.assertEqual(json.loads(proc.stdout), [])

    def test_no_human_text_leaks_to_stdout(self):
        # The human path prints a "Searching N shard(s)" banner; JSON mode must not.
        proc = _run_json(NONSENSE)
        self.assertNotIn("Searching", proc.stdout)
        self.assertNotIn("No session database", proc.stdout)

    def test_result_shape_when_present(self):
        # A broad query; if this machine has an index with matches, every result
        # must carry the documented search() keys. Skips cleanly when empty.
        proc = _run_json("the", "--limit", "3")
        data = json.loads(proc.stdout)
        if not data:
            self.skipTest("no session index / no matches on this machine")
        for row in data:
            self.assertTrue(_EXPECTED_KEYS.issubset(row.keys()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
