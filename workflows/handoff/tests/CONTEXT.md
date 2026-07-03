# Handoff - Tests

**Last modified:** 2026-07-03

## Purpose
Hermetic unit tests for the handoff gather readers and the seen-watermark logic.

## Contents
- run_tests.py - single entry point; discovers and runs every `test_*.py`.
- test_state.py - load/save, handoff-timestamp parsing (`**Created:**` line and
  mtime fallback), and the is_unread freshness logic.
- test_gather.py - changed-directory derivation, LOG tail + entry filtering,
  Active-backlog parsing, recent-session reads (temp sqlite shard), git degrade
  paths, and packet assembly.

## Inputs
None. Tests build their own temporary fixtures (temp dirs and an in-memory-style
sqlite shard on disk).

## Outputs
Test results to stdout; exit code 0 on success, non-zero on failure.

## Steps
1. `python workflows/handoff/tests/run_tests.py`.
2. Fails the run if any test fails (used by close-out).

## Dependencies
- `workflows/handoff/scripts/` [[workflows/handoff/scripts/CONTEXT]] - the modules
  under test.
- Python standard library `unittest`, `sqlite3`, `tempfile`.

## Known Issues
- git-backed readers are only tested for the degrade-to-empty path; their happy
  path is exercised by the live smoke test rather than a fixture repo.

## Revision History
- 2026-07-03 - Initial creation. 22 tests across test_state.py and test_gather.py.
