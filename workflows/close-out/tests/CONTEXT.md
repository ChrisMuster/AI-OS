# Close-out - Tests

**Last modified:** 2026-07-03

## Purpose
Holds the test suite for the close-out verifier.

## Contents
- `test_close_out.py` - hermetic tests for scope selection (affected / all / name / git-unavailable fallback / cross-cutting escalation), the interpreter re-exec helpers, the subprocess test runner, gate aggregation, report formatting, and the import coupling to the audit and link-check scripts.
- `test_runtime_bootstrap.py` - regression check that workflow scripts importing
  PyYAML either bootstrap into the project `.venv` runtime or explicitly degrade
  when PyYAML is unavailable.

## Inputs
- Run as `python workflows/close-out/tests/test_close_out.py`. Imports the verifier from `workflows/close-out/scripts/run.py` [[workflows/close-out/scripts/CONTEXT]].

## Outputs
- Standard `unittest` pass/fail output. Exit code 0 on success, non-zero on failure.

## Steps
N/A - this is a test directory, not a workflow.

## Dependencies
- `workflows/close-out/scripts/run.py` [[workflows/close-out/scripts/CONTEXT]] - the module under test.
- Python standard library only (unittest, importlib, tempfile, pathlib).

## Known Issues
- None. The tests are hermetic (they create throwaway test files in a temp dir) and do not run the full structural audit, so they stay fast and independent of repo state.

## Revision History
- 2026-07-02 - Initial creation with `test_close_out.py`.
- 2026-07-02 - Added tests for cross-cutting scope escalation and the `.venv` re-exec helpers (13 -> 21 tests). Review-fix pass on the verification-discipline work.
- 2026-07-03 - Added `test_runtime_bootstrap.py` to catch workflow scripts that
  import project-only PyYAML without the project `.venv` runtime handoff or a
  documented degrade path.
