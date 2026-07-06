# Memory Diff - Tests

**Last modified:** 2026-07-06

## Purpose
Hermetic unit tests for the memory-diff workflow: the content-watermark delta
logic and the `memory/LOG.md` reader/categoriser. They use temporary fixtures
only and touch no real project state, so they are safe to run in close-out.

## Contents
- run_tests.py - `workflows/memory-diff/tests/run_tests.py` [[workflows/memory-diff/tests/CONTEXT]] - single entry point;
  discovers and runs every `test_*.py`, exiting non-zero on any failure.
- test_state.py - `workflows/memory-diff/tests/test_state.py` [[workflows/memory-diff/tests/CONTEXT]] - load/save (incl.
  the malformed-shape guard: an existing state file with a missing, non-string, or
  empty/whitespace `seen_line` raises `StateError("malformed")`; a missing file is a
  first run), latest line, and the new-entries delta (first-run baseline, watermark
  found, watermark at end, missing watermark re-baseline, duplicate-line
  last-occurrence).
- test_diff.py - `workflows/memory-diff/tests/test_diff.py` [[workflows/memory-diff/tests/CONTEXT]] - log reading (skips
  header/blanks/non-entries, preserves order, raises LogError on a missing log),
  entry parsing, and categorisation.
- test_run.py - `workflows/memory-diff/tests/test_run.py` [[workflows/memory-diff/tests/CONTEXT]] - integration tests that
  drive run.py as a subprocess against temp fixtures (via the `MEMORY_DIFF_*` path
  overrides): JSON/text status, ack writing state and logging, the loud anomaly
  paths (corrupt state, a present-but-invalid `{}` state, missing log, lost
  watermark) and their `--force-baseline` reset, the display cap, and the
  `--through` race guard.

## Inputs
- The scripts under test in `workflows/memory-diff/scripts/` [[workflows/memory-diff/scripts/CONTEXT]] (imported via a path insert).

## Outputs
- Test results to stdout; exit code 0 (pass) or 1 (fail).

## Steps
1. Run `python workflows/memory-diff/tests/run_tests.py`.
2. All tests must pass before the workflow is considered verified.
3. Append LOG.md with a completion or failure entry when the suite is changed.

## Dependencies
- `workflows/memory-diff/scripts/` [[workflows/memory-diff/scripts/CONTEXT]] - the
  modules under test.
- Python standard library `unittest`. No third-party packages.

## Known Issues
- test_run.py drives run.py as a subprocess, so it is slower than the pure-module
  tests; it stays hermetic by pointing the `MEMORY_DIFF_*` overrides at a temp
  directory, so it never reads or writes the real state.json or memory/LOG.md.

## Revision History
- 2026-07-06 - Initial creation. 20 tests across test_state.py and test_diff.py,
  plus the run_tests.py entry point.
- 2026-07-06 - Codex review fixes: added test_run.py (run.py integration tests),
  updated test_state.py to the new new_entries/StateError API plus line_token
  tests, and updated test_diff.py for the LogError-on-missing-log contract. Suite
  now 40 tests; run.py is no longer smoke-test-only.
- 2026-07-06 - Codex second-review fix: added coverage for the malformed-state
  guard - 5 unit tests in test_state.py (empty object, empty/whitespace/non-string
  `seen_line`, extra keys tolerated) and 3 run.py integration tests (a `{}` state
  warns and refuses ack, the JSON path flags the anomaly, `--force-baseline`
  resets). Suite now 48 tests.
