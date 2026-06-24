# Session Search — Tests

**Last modified:** 2026-06-24

## Purpose
Standalone unit tests for the session-search scripts. Currently covers `search.py`'s `--json` output mode — the machine-readable contract consumed by the knowledge-graph `sessions` cross-reference command — verifying that it emits a bare JSON list on stdout, never leaks the human-readable banner, returns an empty list for a non-matching query regardless of whether a session index is present, and that each result dict carries the documented `search()` fields when matches exist.

## Contents
- test_json_output.py — `workflows/session-search/tests/test_json_output.py` [[workflows/session-search/tests/CONTEXT]] — Subprocess-level tests of `search.py --json`: bare JSON list on stdout, no human text, empty list for a nonsense query (deterministic on any machine), and result-shape validation when an index has matches.

## Inputs
None. The tests invoke `search.py` as a subprocess with a deliberately non-matching query, so they touch no real session content and need no fixtures.

## Outputs
- Test results printed to stdout. Non-zero exit code on failure.

## Steps
1. Run the tests: `python workflows/session-search/tests/test_json_output.py`
2. Append LOG.md with a completion or failure entry.

## Dependencies
- `workflows/session-search/scripts/search.py` [[workflows/session-search/scripts/CONTEXT]] — The script under test (provides the `--json` mode).
- Python 3.9+ standard library only (json, subprocess, unittest, pathlib).

## Known Issues
- `test_result_shape_when_present` depends on machine state: on a machine with no session index (or no matches for the broad query), it skips cleanly rather than failing. The other three tests are deterministic everywhere.

## Revision History
- 2026-06-24 — Initial creation. Added test_json_output.py covering search.py's new `--json` mode (the knowledge-graph session-search cross-reference contract).
