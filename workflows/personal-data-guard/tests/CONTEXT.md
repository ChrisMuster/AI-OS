# Tests

**Last modified:** 2026-10-07

## Purpose
Unit tests for the personal-data guard. They exercise the pure detection, allowlist, and marker-derivation helpers with in-memory fixtures that use invented names, so the tests never touch the real project tree. A dedicated test asserts the guard's own `run.py` source carries no real personal markers (every email and path literal in it is a placeholder its allowlist accepts), guaranteeing the guard can never flag itself. Trigger strings in the fixtures are assembled from fragments so the personal-looking literals never appear verbatim in this tracked source - the same self-exemption trick encoding-guard uses for its corruption signatures.

## Contents
- test_personal_data_guard.py - `workflows/personal-data-guard/tests/test_personal_data_guard.py` [[workflows/personal-data-guard/tests/CONTEXT]] - Tests for marker derivation, the email allowlist, the username-shape check, home-path detection, `scan_text` severities (FAIL vs WARN), word-boundary matching, de-duplication, and the pure-source guarantee.
- test_json_fields.py - `workflows/personal-data-guard/tests/test_json_fields.py` [[workflows/personal-data-guard/tests/CONTEXT]] - Tests for the `file` and `kind` fields: one case per kind covers all five, each hit carries its own file and kind both on the finding and in `--json`, the message is unchanged, a finding still equals and unpacks as the plain three-item tuple, a run-level INFO note carries neither field, and an unknown kind is refused.
- test_root_override.py - `workflows/personal-data-guard/tests/test_root_override.py` [[workflows/personal-data-guard/tests/CONTEXT]] - Tests for the project-root override, over a temporary git repository with an invented name in its own gitignored USER.md and an invented term in its own gitignored denylist, each written into one committable file: with `BOOK_DRAGON_ROOT` set, the guard reports exactly those two hits by file and kind (so the files scanned, USER.md and the denylist all came from the fixture); every project path follows the root; a missing folder, a folder without AGENTS.md and an empty value each exit 2 before scanning. Also `scanned` in `--json`: true for a completed scan, false (exit 0, a "scan skipped" note) for a project folder git cannot list files for, and the skip note still the plain three-item tuple.
- run_tests.py - `workflows/personal-data-guard/tests/run_tests.py` [[workflows/personal-data-guard/tests/CONTEXT]] - One-command unittest runner for this directory.

## Inputs
None. Tests construct their own fixtures: in memory, and for the root override a temporary git repository (the `git` CLI is needed for that file).

## Outputs
None. Prints a unittest summary; exits non-zero on failure.

## Steps
Run from anywhere:

```
python workflows/personal-data-guard/tests/run_tests.py
```

## Dependencies
- `workflows/personal-data-guard/scripts/run.py` [[workflows/personal-data-guard/scripts/CONTEXT]] - The module under test, imported directly.
- Python 3.13+ standard library (unittest).

## Known Issues
None.

## Revision History
- 2026-06-25 - Initial creation. Covers detectors, allowlists, marker derivation, severities, and the pure-source guarantee; fixtures assemble trigger strings from fragments so the suite never self-flags.
- 2026-09-23 - Dependencies line says Python 3.13+, following the project floor raised from 3.9 to 3.13. No behaviour change.
- 2026-10-07 - Added test_json_fields.py (7 tests) for the findings' `file` and `kind` fields (orchestrator isolation S2). Planted one at a time in memory, a mislabelled kind, a missing file, the fields dropped from JSON, and an unknown kind accepted each fail a test.
- 2026-10-07 - Added test_root_override.py (3 tests) for the project-root override (orchestrator isolation S3). Planted one change at a time in the script, the override ignored, the denylist read beside the script, and AGENTS.md not required each fail a test. Inputs updated for the git fixture. Then, for code review R9-1, three tests for `scanned` (3 to 6); scanned always true, and each skip site left unflagged, each fail a test.
