#!/usr/bin/env python3
"""Unit tests for the MCP layer's `_run_json_script` failure paths.

`_run_json_script` (in server.py) wraps `_run_script`, parsing the wrapped
script's stdout as JSON. It is the helper behind `build_knowledge_graph` and
`query_knowledge_graph`. The argv assembly is covered by test_kg_query.py; this
file covers the error handling that turns a failed or unparseable run into a
structured `{"success": False, ...}` result.

`_run_script` itself spawns a subprocess, so it is monkeypatched here with an
async stub: every case feeds `_run_json_script` a controlled `_run_script`
return value and asserts the structured result, with no real process spawned.

Importing server.py requires the `mcp` package (Python 3.10+); on 3.9 these
tests are skipped, consistent with the rest of the MCP layer.
"""

import asyncio
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
class TestRunJsonScript(unittest.TestCase):
    def setUp(self):
        self._original = server._run_script

    def tearDown(self):
        server._run_script = self._original

    def _stub(self, value):
        """Replace server._run_script with an async stub returning ``value``."""
        async def fake(cmd):
            return value
        server._run_script = fake

    def _run(self, cmd=None):
        return asyncio.run(server._run_json_script(cmd or ["echo"]))

    # -- happy path --------------------------------------------------------
    def test_valid_json_returns_parsed_result(self):
        self._stub({"success": True, "stdout": '{"a": 1, "b": [2, 3]}', "return_code": 0})
        out = self._run()
        self.assertTrue(out["success"])
        self.assertEqual(out["result"], {"a": 1, "b": [2, 3]})

    # -- non-zero exit -----------------------------------------------------
    def test_non_zero_exit_returns_structured_error(self):
        self._stub({"success": False, "stderr": "unknown node id", "return_code": 2})
        out = self._run()
        self.assertFalse(out["success"])
        self.assertEqual(out["stderr"], "unknown node id")
        self.assertEqual(out["return_code"], 2)

    def test_spawn_failure_falls_back_to_error_field(self):
        # _run_script returns {"success": False, "error": ...} (no stderr key)
        # when the subprocess cannot be spawned; the message must still surface.
        self._stub({"success": False, "error": "No such file or directory"})
        out = self._run()
        self.assertFalse(out["success"])
        self.assertEqual(out["stderr"], "No such file or directory")

    # -- unparseable stdout -----------------------------------------------
    def test_unparseable_stdout_returns_structured_error(self):
        self._stub({"success": True, "stdout": "not json at all", "return_code": 0})
        out = self._run()
        self.assertFalse(out["success"])
        self.assertIn("could not parse JSON output", out["stderr"])
        self.assertEqual(out["return_code"], 0)

    def test_missing_stdout_key_returns_structured_error(self):
        # success True but no stdout key -> KeyError is caught, not raised.
        self._stub({"success": True, "return_code": 0})
        out = self._run()
        self.assertFalse(out["success"])
        self.assertIn("could not parse JSON output", out["stderr"])


if __name__ == "__main__":
    unittest.main()
