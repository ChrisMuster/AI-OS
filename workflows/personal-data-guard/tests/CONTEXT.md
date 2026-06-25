# Tests

**Last modified:** 2026-06-25

## Purpose
Unit tests for the personal-data guard. They exercise the pure detection, allowlist, and marker-derivation helpers with in-memory fixtures that use invented names, so the tests never touch the real project tree. A dedicated test asserts the guard's own `run.py` source carries no real personal markers (every email and path literal in it is a placeholder its allowlist accepts), guaranteeing the guard can never flag itself. Trigger strings in the fixtures are assembled from fragments so the personal-looking literals never appear verbatim in this tracked source - the same self-exemption trick encoding-guard uses for its corruption signatures.

## Contents
- test_personal_data_guard.py - `workflows/personal-data-guard/tests/test_personal_data_guard.py` [[workflows/personal-data-guard/tests/CONTEXT]] - Tests for marker derivation, the email allowlist, the username-shape check, home-path detection, `scan_text` severities (FAIL vs WARN), word-boundary matching, de-duplication, and the pure-source guarantee.
- run_tests.py - `workflows/personal-data-guard/tests/run_tests.py` [[workflows/personal-data-guard/tests/CONTEXT]] - One-command unittest runner for this directory.

## Inputs
None. Tests construct their own in-memory fixtures.

## Outputs
None. Prints a unittest summary; exits non-zero on failure.

## Steps
Run from anywhere:

```
python workflows/personal-data-guard/tests/run_tests.py
```

## Dependencies
- `workflows/personal-data-guard/scripts/run.py` [[workflows/personal-data-guard/scripts/CONTEXT]] - The module under test, imported directly.
- Python 3.9+ standard library (unittest).

## Known Issues
None.

## Revision History
- 2026-06-25 - Initial creation. Covers detectors, allowlists, marker derivation, severities, and the pure-source guarantee; fixtures assemble trigger strings from fragments so the suite never self-flags.
