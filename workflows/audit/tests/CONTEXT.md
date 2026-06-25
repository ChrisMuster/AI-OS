# Tests

**Last modified:** 2026-06-25

## Purpose
Standalone unit tests for the audit workflow's close-out hooks and structural helpers. Verifies that the pure `graph_findings`, `encoding_findings`, `personal_findings`, and `ai_style_findings` merge helpers keep only actionable severities, drop INFO, and label findings correctly; that `run_graph_validation`, `run_encoding_check`, `run_personal_data_check`, and `run_ai_style_check` degrade gracefully (never raising) when their CLI is missing, crashes, or returns unparseable output; and that immediate subdirectory filtering suppresses gitignored child directories.

## Contents
- test_audit_graph_hook.py — `workflows/audit/tests/test_audit_graph_hook.py` [[workflows/audit/tests/CONTEXT]] — Tests for the knowledge-graph merge helper (severity filtering, field remap, label, message format, empty/missing-key cases) and the graceful-skip paths of the validator invocation (missing CLI, unparseable output, subprocess exception, and a valid-payload merge).
- test_audit_encoding_hook.py - `workflows/audit/tests/test_audit_encoding_hook.py` [[workflows/audit/tests/CONTEXT]] - Tests for the encoding-guard merge helper (`encoding_findings`) and the graceful-skip paths of `run_encoding_check` (missing CLI, unparseable output, subprocess exception, and a valid-payload merge).
- test_audit_personal_data_hook.py - `workflows/audit/tests/test_audit_personal_data_hook.py` [[workflows/audit/tests/CONTEXT]] - Tests for the personal-data-guard merge helper (`personal_findings`) and the graceful-skip paths of `run_personal_data_check` (missing CLI, unparseable output, subprocess exception, and a valid-payload merge).
- test_audit_ai_style_hook.py - `workflows/audit/tests/test_audit_ai_style_hook.py` [[workflows/audit/tests/CONTEXT]] - Tests for the ai-style-guard merge helper (`ai_style_findings`, WARN-only) and the graceful-skip paths of `run_ai_style_check` (missing CLI, unparseable output, subprocess exception, and a valid-payload merge).
- test_audit_subdir_filter.py - `workflows/audit/tests/test_audit_subdir_filter.py` [[workflows/audit/tests/CONTEXT]] - Tests that immediate subdirectory discovery filters gitignored child directories while keeping normal children.
- run_tests.py — `workflows/audit/tests/run_tests.py` [[workflows/audit/tests/CONTEXT]] — One-command runner that discovers and runs every `test_*.py` here; exits non-zero on any failure.

## Inputs
None. Tests build their own payloads and patch subprocess or gitignore boundaries; they never spawn the real validator.

## Outputs
- Test results printed to stdout. Non-zero exit code on failure.

## Steps
1. Run the whole suite in one command: `python workflows/audit/tests/run_tests.py`
   (or run the module directly: `python workflows/audit/tests/test_audit_graph_hook.py`).
2. Append LOG.md with a completion or failure entry.

## Dependencies
- `workflows/audit/scripts/run.py` [[workflows/audit/scripts/CONTEXT]] - The module under test (`graph_findings`/`run_graph_validation`, `encoding_findings`/`run_encoding_check`, `personal_findings`/`run_personal_data_check`, `ai_style_findings`/`run_ai_style_check`, and `get_immediate_subdirs`).
- Python 3.9+ standard library only (unittest, unittest.mock, pathlib).

## Known Issues
- The tests patch the `subprocess.run` boundary rather than spawning the real knowledge-graph validator, so the live `validate --json` contract is covered by the close-out audit run, not here. This keeps the suite fast and hermetic.

## Revision History
- 2026-06-23 — Initial creation. Tests for the knowledge-graph audit-hook merge helper and graceful-degradation paths.
- 2026-06-24 - Added test_audit_encoding_hook.py covering the encoding-guard merge helper and graceful-skip paths.
- 2026-06-25 - Added test_audit_subdir_filter.py covering gitignored child-directory filtering in immediate subdirectory discovery.
- 2026-06-25 - Added test_audit_personal_data_hook.py covering the personal-data-guard merge helper and graceful-skip paths.
- 2026-06-25 - Added test_audit_ai_style_hook.py covering the ai-style-guard merge helper (WARN-only) and graceful-skip paths.
