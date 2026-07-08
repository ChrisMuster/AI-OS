# Triggers - Tests

**Last modified:** 2026-07-07

## Purpose
Hermetic unit tests for the trigger-registry loader and renderer, plus a
data-integrity guard for the shipped registry.

## Contents
- run_tests.py - single entry point; switches into the project `.venv` if needed,
  then discovers and runs every `test_*.py`.
- test_registry.py - loader tests (valid, missing, malformed, entries without a
  name), category filtering, rendering, and a check that the real triggers.yaml is
  well-formed (every category has a summary, a runs value, and phrases).

## Inputs
None. Tests build temporary YAML fixtures; the integrity test reads the shipped
config/triggers.yaml.

## Outputs
Test results to stdout; exit code 0 on success, non-zero on failure.

## Steps
1. `python workflows/triggers/tests/run_tests.py`.
2. Fails the run if any test fails (used by close-out).

## Dependencies
- `workflows/triggers/scripts/` [[workflows/triggers/scripts/CONTEXT]] - the module
  under test.
- `workflows/triggers/config/triggers.yaml` [[workflows/triggers/config/CONTEXT]] -
  read by the integrity test.
- `workflows/biblio-tools/scripts/runtime.py` [[workflows/biblio-tools/scripts/CONTEXT]]
  - switches plain `python` invocations into the canonical project `.venv`.
- PyYAML.

## Known Issues
None.

## Revision History
- 2026-07-03 - Initial creation. 10 tests in test_registry.py.
- 2026-07-03 - Added project-runtime handoff to run_tests.py so the documented
  plain-`python` test command can import PyYAML-backed registry code.
- 2026-07-07 - The shipped-registry integrity test now also asserts the `doc-sync`
  category is present (doc-sync-guard build part 5).
