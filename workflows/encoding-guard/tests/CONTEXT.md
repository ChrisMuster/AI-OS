# Tests

**Last modified:** 2026-06-24

## Purpose
Unit tests for the encoding-guard script. They exercise the pure detection and repair helpers without touching the real project tree, and assert the guard's own source stays pure ASCII so it can never flag or repair itself.

## Contents
- test_encoding_guard.py - `workflows/encoding-guard/tests/test_encoding_guard.py` [[workflows/encoding-guard/tests/CONTEXT]] - Tests for signature generation, `scan_text`, `repair_text`, `repair_bytes`, `_has_encoding_problem`, the code-pattern check, and the pure-ASCII-source guarantee.
- run_tests.py - `workflows/encoding-guard/tests/run_tests.py` [[workflows/encoding-guard/tests/CONTEXT]] - One-command unittest runner for this directory.

## Inputs
None. Tests construct their own in-memory fixtures.

## Outputs
None. Prints a unittest summary; exits non-zero on failure.

## Steps
Run from anywhere:

```
python workflows/encoding-guard/tests/run_tests.py
```

## Dependencies
- `workflows/encoding-guard/scripts/run.py` [[workflows/encoding-guard/scripts/CONTEXT]] - The module under test, imported directly.
- Python 3.9+ standard library (unittest).

## Known Issues
None.

## Revision History
- 2026-06-24 - Initial creation.
