# Check For Updates - Tests

**Last modified:** 2026-06-27

## Purpose
The unittest suite for the check-for-updates workflow. Covers version classification, the registry resolvers, both checkers, the landscape watch, and report formatting, all without touching the network or the `.venv` (subprocess, HTTP, and the web-research call are mocked or injected).

## Contents
- run_tests.py - `workflows/check-for-updates/tests/run_tests.py` [[workflows/check-for-updates/tests/CONTEXT]] - Single entry point that discovers and runs every `test_*.py`; exits non-zero on failure.
- test_versions.py - semver parse and change classification.
- test_registries.py - the npm/GitHub resolvers and their failure handling.
- test_checkers.py - the Python-deps and CLI-tool checkers, with subprocess and resolver calls mocked.
- test_report.py - report formatting, the update count, and `--suggest-commands`.
- test_landscape.py - the landscape scan and config handling (with an injected research function), plus the advisory landscape report section.

## Inputs
- The workflow's scripts, imported by adding `workflows/check-for-updates/scripts` [[workflows/check-for-updates/scripts/CONTEXT]] to `sys.path`.

## Outputs
- Test results on stdout; a non-zero exit code on any failure.

## Steps
1. Run the suite: `python workflows/check-for-updates/tests/run_tests.py`.
2. Fix any failure and re-run until green.

## Dependencies
- `workflows/check-for-updates/scripts/` [[workflows/check-for-updates/scripts/CONTEXT]] - the code under test.
- The Python standard library `unittest` and `unittest.mock`; no third-party test runner.

## Known Issues
- Tests mock the network and subprocess layers, so they validate logic and wiring, not live registry responses. The live path is exercised by running the workflow itself.

## Revision History
- 2026-06-26 - Initial creation. 21 tests across versions, sources, checkers, and report, with a unittest run_tests.py runner.
- 2026-06-27 - Renamed test_sources.py to test_registries.py (tracks the sources.py to registries.py rename) and added test_landscape.py for Phase 2. Suite grown to 36 tests.
