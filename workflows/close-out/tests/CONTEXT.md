# Close-out - Tests

**Last modified:** 2026-08-04

## Purpose
Holds the test suite for the close-out verifier.

## Contents
- `test_close_out.py` - hermetic tests for scope selection (affected / all / name / git-unavailable fallback / cross-cutting escalation), the interpreter re-exec helpers, the subprocess test runner, gate aggregation, report formatting, the import coupling to the audit and link-check scripts, DEGRADED surfacing (collected across gates, shown non-blocking, verdict annotated, repair note rendered), the `--repair` path (`run_repair` invoking setup.py and its missing-setup fallback), the doc-sync teeth (`DocSyncTeethTests`: a `doc-sync`-labelled WARN hard-fails `gate_audit` while other guards' WARNs stay advisory, a `doc-sync` finding at DEGRADED severity is non-blocking and surfaces via the DEGRADED path rather than as drift, a structural FAIL still fails, a clean audit passes, and the report surfaces the drift messages), and the skill-hardening teeth (`SkillHardeningTeethTests`: the same set of assertions for the `skill-hardening` label - a WARN hard-fails the gate, a missing field also fails, non-blocking WARNs stay advisory, a DEGRADED finding is not a gap, a clean audit passes, and the report surfaces the gap messages), and the blocking-label documentation guard (`BlockingLabelsAreDocumentedTests`: every key of `BLOCKING_LABELS` is named in the workflow's own CONTEXT.md, and that CONTEXT.md still states that only a WARN blocks - so adding a blocking label cannot silently leave the prose describing the old one, which is how `skill-hardening` went a month undocumented).
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
- 2026-07-06 - Added `DocSyncTeethTests` to `test_close_out.py` for the doc-sync
  close-out teeth (build part 3): a `doc-sync`-labelled finding hard-fails
  `gate_audit`, LOG drift also hard-fails, non-doc-sync WARNs (ai-style,
  personal-data) stay advisory, a structural FAIL still fails, a clean audit
  passes, and `build_report` surfaces the drift messages (27 -> 33 tests).
- 2026-07-07 - Codex review fix (finding 3): added `test_degraded_doc_sync_is_not_drift` to `DocSyncTeethTests`, asserting a `doc-sync` finding at DEGRADED severity leaves the structural-audit gate passing and surfaces through the DEGRADED path instead of counting as drift (33 -> 34 tests).
- 2026-07-09 - Added `SkillHardeningTeethTests` for the skill-hardening close-out teeth (umbrella Bucket-1 child #6): a `skill-hardening`-labelled WARN hard-fails `gate_audit`, a missing field also fails, non-blocking WARNs stay advisory, a DEGRADED finding is not a gap, a clean audit passes, and `build_report` surfaces the gap messages (34 -> 40 tests).
- 2026-07-09 - Review-fix (child #6): updated both teeth classes to read the generalised `gate["blocking"][label]` dict and to pass `"blocking": {...}` report fixtures, following the `BLOCKING_LABELS` refactor in the gate. Assertion-only change; still 40 tests.
- 2026-08-04 - Added `BlockingLabelsAreDocumentedTests` (40 -> 42 tests): every `BLOCKING_LABELS` key must be named in the workflow's CONTEXT.md, and that file must still state that only a WARN blocks. Adding a blocking label is deliberately a one-line change in `run.py`, which is exactly how `skill-hardening` stayed undocumented for a month while the prose still called `doc-sync` "the one exception". Both assertions were verified against positive controls (an undocumented third label, and a CONTEXT.md with the severity claim removed). Also corrected two stale comments in `DocSyncTeethTests` that described the gate as keying on the label alone.
- 2026-08-04 - The `_write` fixture helper in `TestRunnerTests` converted from `Path.write_text(..., encoding="utf-8")` to `write_bytes`, so it stops writing CRLF fixtures on Windows and stops breaking the newline half of the AGENTS.md text-I/O rule. One call site, every fixture in that class behind it. `write_bytes` rather than `Path.write_text(newline=...)`, which is a 3.10 API against the project's stated 3.9 floor. Test count unchanged at 42; no assertion touched.
