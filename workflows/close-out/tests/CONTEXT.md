# Close-out - Tests

**Last modified:** 2026-07-05

## Purpose
Holds the test suite for the close-out verifier.

## Contents
- `test_close_out.py` - hermetic tests for scope selection (affected / all / name / git-unavailable fallback / cross-cutting escalation), the interpreter re-exec helpers, the subprocess test runner, gate aggregation, report formatting, the import coupling to the audit and link-check scripts, DEGRADED surfacing (collected across gates, shown non-blocking, verdict annotated, repair note rendered), and the `--repair` path (`run_repair` invoking setup.py and its missing-setup fallback).
- `test_runtime_bootstrap.py` - regression check that every entry-point script
  (one with a `__main__` block) in a workflow or skill `scripts/` directory
  importing a project-only package (any distribution in the
  `requirements.txt` set, resolved by following its `-r` includes) either
  bootstraps into the project `.venv` via `ensure_project_runtime()`, carries a
  `# runtime-guard: degrades without <pkg>` marker and degrades, or carries a
  `# runtime-guard: launched via <mechanism>` marker. Pure helper modules
  (no `__main__`) are exempt. Standard-library only, plus hermetic fixture tests
  for the parser and each acceptance signal.

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
- 2026-07-03 - Generalised `test_runtime_bootstrap.py` from PyYAML-only to the
  whole `requirements.txt` package set (following `-r` includes), extended scope
  to `skills/*/scripts/` and narrowed it to entry-point scripts, and replaced
  the brittle degrade-string check with `# runtime-guard:` markers (degrade and
  launched-via). 1 -> 9 tests.
- 2026-07-04 - Added DegradedSurfacingTests and RepairTests to `test_close_out.py`
  for the new DEGRADED status and opt-in `--repair` flow (21 -> 26 tests).
- 2026-07-05 - `test_runtime_bootstrap.py` now detects the `ensure_project_runtime()`
  call via AST (a real call, not a bare text match), so a commented-out or
  docstringed mention no longer satisfies the guard; added attribute-form,
  commented-out, and docstring-mention fixtures (9 -> 12 tests). `test_close_out.py`
  gained `overall_status` coverage and now asserts the verdict reads `RESULT:
  DEGRADED` (distinct from a clean pass) rather than an annotated PASS (26 -> 27
  tests). Codex review-fix pass on the runtime-bootstrap work.
