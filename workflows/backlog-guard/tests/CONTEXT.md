# Tests

**Last modified:** 2026-08-04

## Purpose
Hermetic tests for the backlog-guard workflow.

## Contents
- run_tests.py - `workflows/backlog-guard/tests/run_tests.py` - Discovers and runs the test suite.
- test_backlog_guard.py - `workflows/backlog-guard/tests/test_backlog_guard.py` - Unit and integration tests for parsing, snapshot rotation, count checks, oversized item detection, and restore behaviour.

## Inputs
- Temporary directories and test backlog fixtures only.

## Outputs
- Test runner pass/fail output.

## Steps
1. Build temporary backlog and snapshot fixtures.
2. Exercise parser, snapshot, check, rotation, and restore paths.
3. Assert expected pass/fail outcomes and restored content.
4. Append LOG.md with a completion or failure entry.

## Dependencies
- `workflows/backlog-guard/scripts/run.py` [[workflows/backlog-guard/scripts/CONTEXT]] - subject under test.
- Python standard library `unittest`.

## Known Issues
- None.

## Revision History
- 2026-08-04 - Initial creation.
