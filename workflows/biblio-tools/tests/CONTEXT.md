# Biblio Tools — Tests

**Last modified:** 2026-06-24

## Purpose
Standalone unit tests for the biblio-tools MCP server. Covers the knowledge-graph query dispatcher's pure argv helper (`build_kg_query_argv` in `server.py`) — the required-argument matrix and argv construction for each read-only command — and the `_run_json_script` helper's failure handling (non-zero exit, spawn failure, and unparseable/missing stdout all becoming structured errors). Both run without spawning a subprocess or starting the server. Live protocol-level coverage of the tools is provided separately by `scripts/mcp_smoke.py`.

## Contents
- test_kg_query.py — `workflows/biblio-tools/tests/test_kg_query.py` [[workflows/biblio-tools/tests/CONTEXT]] — Tests the required-arg matrix and argv assembly of the `query_knowledge_graph` dispatcher's pure helper.
- test_run_json_script.py — `workflows/biblio-tools/tests/test_run_json_script.py` [[workflows/biblio-tools/tests/CONTEXT]] — Tests the `_run_json_script` failure paths (non-zero exit, spawn-error fallback, unparseable/missing stdout) via an async stub of `_run_script`, with no real process spawned.

## Inputs
None. Tests import the server module and call the pure helper directly.

## Outputs
- Test results printed to stdout. Non-zero exit code on failure.

## Steps
1. Run the dispatcher tests: `python workflows/biblio-tools/tests/test_kg_query.py`
2. Run the `_run_json_script` failure-path tests: `python workflows/biblio-tools/tests/test_run_json_script.py`
3. Append LOG.md with a completion or failure entry.

## Dependencies
- `workflows/biblio-tools/scripts/server.py` [[workflows/biblio-tools/scripts/CONTEXT]] — The module under test (provides `build_kg_query_argv`).
- Python `mcp` package (>= 1.0.0, Python 3.10+) — importing `server.py` pulls in FastMCP. On Python 3.9 the tests skip rather than fail.
- Python standard library (unittest, pathlib).

## Known Issues
- Importing `server.py` requires the `mcp` package; on Python 3.9 (where MCP is unavailable) every test in the file is skipped, consistent with the rest of the MCP layer. The pure helper itself has no MCP dependency, but it lives in the server module.

## Revision History
- 2026-06-20 — Initial creation. Phase 3: added test_kg_query.py covering the knowledge-graph query dispatcher's argv helper (required-arg matrix, positional placement, neighbors direction/type, path source/target/undirected, validate flags, from_index).
- 2026-06-21 — Knowledge-graph Phase 4: added test_run_json_script.py covering the `_run_json_script` failure paths (non-zero exit, spawn-error fallback, unparseable/missing stdout).
- 2026-06-22 — Knowledge-graph Phase 5: added a test_kg_query.py case asserting `include_memory=True` appends `--layer memory` (and is absent by default).
- 2026-06-23 — Knowledge-graph Phase 6: added test_kg_query.py cases asserting `include_wiki=True` appends `--layer wiki`, and that `include_memory` + `include_wiki` together append both `--layer` pairs in order (memory before wiki).
- 2026-06-23 — Knowledge-graph Phase 8: added a test_kg_query.py case asserting `include_conversation=True` appends `--layer conversation`, and extended the all-layers case to expect `[memory, wiki, journal, conversation]` in build order (the equivalent `include_journal` case landed with Phase 7).
- 2026-06-24 — Knowledge-graph session-search cross-reference: added test_kg_query.py cases for the `sessions` command (positional id + default `--limit 10` + `--json`; `terms`/`since`/`ai`/`source` threading; unset filters omitted) and added `sessions` to the require-id matrix. 18 dispatcher tests, all green.
