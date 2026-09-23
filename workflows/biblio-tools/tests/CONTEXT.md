# Biblio Tools — Tests

**Last modified:** 2026-09-23

## Purpose
Standalone unit tests for the biblio-tools MCP server and setup scripts. Covers the knowledge-graph query dispatcher's pure argv helper (`build_kg_query_argv` in `server.py`: the required-argument matrix and argv construction for each read-only command) and the `_run_json_script` helper's failure handling (non-zero exit, spawn failure, and unparseable/missing stdout all becoming structured errors). Both run without spawning a subprocess or starting the server. Also covers the Python floor checks in `verify.py` and `setup.py`: the FAIL/PASS/WARN judgement, the `surface` marker reaching the summary and plain-text output, and the `.venv` interpreter check. Live protocol-level coverage of the tools is provided separately by `scripts/mcp_smoke.py`.

## Contents
- test_kg_query.py — `workflows/biblio-tools/tests/test_kg_query.py` [[workflows/biblio-tools/tests/CONTEXT]] — Tests the required-arg matrix and argv assembly of the `query_knowledge_graph` dispatcher's pure helper.
- test_run_json_script.py — `workflows/biblio-tools/tests/test_run_json_script.py` [[workflows/biblio-tools/tests/CONTEXT]] — Tests the `_run_json_script` failure paths (non-zero exit, spawn-error fallback, unparseable/missing stdout) via an async stub of `_run_script`, with no real process spawned.
- test_verify_floor.py - `workflows/biblio-tools/tests/test_verify_floor.py` [[workflows/biblio-tools/tests/CONTEXT]] - Tests the Python floor checks: FAIL/PASS/WARN at below/at/above the floor, the `surface` marker in the summary and plain text, verify.py's `.venv` version check (mocked, plus one real probe of this machine's `.venv`), and setup.py failing `--check` on and refusing to repair a below-floor `.venv`. Also checks that verify.py's report reaches a pipe as UTF-8. Standard library only.

## Inputs
None. Tests import the server module and call the pure helper directly.

## Outputs
- Test results printed to stdout. Non-zero exit code on failure.

## Steps
1. Run the dispatcher tests: `python workflows/biblio-tools/tests/test_kg_query.py`
2. Run the `_run_json_script` failure-path tests: `python workflows/biblio-tools/tests/test_run_json_script.py`
3. Run the Python floor tests: `python workflows/biblio-tools/tests/test_verify_floor.py`
4. Append LOG.md with a completion or failure entry.

## Dependencies
- `workflows/biblio-tools/scripts/server.py` [[workflows/biblio-tools/scripts/CONTEXT]] — The module under test (provides `build_kg_query_argv`).
- Python `mcp` package (>= 1.0.0) - importing `server.py` pulls in FastMCP. It is a required project dependency, so a missing package fails both files rather than skipping them.
- `workflows/biblio-tools/scripts/verify.py` and `workflows/biblio-tools/scripts/setup.py` [[workflows/biblio-tools/scripts/CONTEXT]] - The modules under test in test_verify_floor.py; setup.py is loaded under the name `bd_setup` so it cannot shadow setuptools.
- Python standard library (unittest, unittest.mock, importlib, pathlib).

## Known Issues
- The pure helpers in test_kg_query.py and test_run_json_script.py have no MCP dependency, but they live in the server module, so those two files cannot run without the `mcp` package installed. test_verify_floor.py has no such dependency.
- test_verify_floor.py proves the below-floor `.venv` path with a mocked version probe only, because this machine has no older Python to build a real one from.

## Revision History
- 2026-06-20 — Initial creation. Phase 3: added test_kg_query.py covering the knowledge-graph query dispatcher's argv helper (required-arg matrix, positional placement, neighbors direction/type, path source/target/undirected, validate flags, from_index).
- 2026-06-21 — Knowledge-graph Phase 4: added test_run_json_script.py covering the `_run_json_script` failure paths (non-zero exit, spawn-error fallback, unparseable/missing stdout).
- 2026-06-22 — Knowledge-graph Phase 5: added a test_kg_query.py case asserting `include_memory=True` appends `--layer memory` (and is absent by default).
- 2026-06-23 — Knowledge-graph Phase 6: added test_kg_query.py cases asserting `include_wiki=True` appends `--layer wiki`, and that `include_memory` + `include_wiki` together append both `--layer` pairs in order (memory before wiki).
- 2026-06-23 — Knowledge-graph Phase 8: added a test_kg_query.py case asserting `include_conversation=True` appends `--layer conversation`, and extended the all-layers case to expect `[memory, wiki, journal, conversation]` in build order (the equivalent `include_journal` case landed with Phase 7).
- 2026-06-24 — Knowledge-graph session-search cross-reference: added test_kg_query.py cases for the `sessions` command (positional id + default `--limit 10` + `--json`; `terms`/`since`/`ai`/`source` threading; unset filters omitted) and added `sessions` to the require-id matrix. 18 dispatcher tests, all green.
- 2026-09-23 - Python floor raised from 3.9 to 3.13, making `mcp` an ordinary required dependency. Both files dropped their `try`/`skipUnless` import guard and now import `server` directly, so a missing `mcp` package is a test error rather than a silent skip. No assertion changed.
- 2026-09-23 - Added test_verify_floor.py (15 tests) for the floor-review repairs PF1 and PF2, closing the gap that the floor check had no committed test. It includes the positive control for PF2: a below-floor `.venv` must fail `setup.py --check`, which the code before the repair passed.
- 2026-09-23 - test_verify_floor.py gained TestReportEncoding (suite 16): verify.py `--help` run through a pipe must decode as UTF-8 and carry its non-ASCII dash, which failed before verify.py reconfigured stdout.
