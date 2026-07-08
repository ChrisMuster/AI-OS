# Tests

**Last modified:** 2026-07-07

## Purpose
Standalone unit tests for the audit workflow's close-out hooks and structural helpers. Verifies that the pure `graph_findings`, `encoding_findings`, `personal_findings`, `ai_style_findings`, and `doc_sync_findings` merge helpers keep actionable severities and label findings correctly; that `run_graph_validation`, `run_encoding_check`, `run_personal_data_check`, `run_ai_style_check`, and `run_doc_sync_check` degrade gracefully (never raising, returning a first-class DEGRADED finding that carries a remediation) when their CLI is missing, crashes, or returns unparseable output; that doc-sync scan-skipped WARN findings are surfaced rather than treated as clean; that the `degraded()` helper and `format_report` count and surface DEGRADED distinctly from a pass; that immediate subdirectory filtering suppresses gitignored child directories; and that the breadth-first `collect_dirs` walk preserves its pruning semantics (skip/hidden/gitignore/no-recurse) and the non-Git fast path.

## Contents
- test_audit_graph_hook.py — `workflows/audit/tests/test_audit_graph_hook.py` [[workflows/audit/tests/CONTEXT]] — Tests for the knowledge-graph merge helper (severity filtering, field remap, label, message format, empty/missing-key cases) and the graceful-skip paths of the validator invocation (missing CLI, unparseable output, subprocess exception, and a valid-payload merge).
- test_audit_encoding_hook.py - `workflows/audit/tests/test_audit_encoding_hook.py` [[workflows/audit/tests/CONTEXT]] - Tests for the encoding-guard merge helper (`encoding_findings`) and the graceful-skip paths of `run_encoding_check` (missing CLI, unparseable output, subprocess exception, and a valid-payload merge).
- test_audit_personal_data_hook.py - `workflows/audit/tests/test_audit_personal_data_hook.py` [[workflows/audit/tests/CONTEXT]] - Tests for the personal-data-guard merge helper (`personal_findings`) and the graceful-skip paths of `run_personal_data_check` (missing CLI, unparseable output, subprocess exception, and a valid-payload merge).
- test_audit_ai_style_hook.py - `workflows/audit/tests/test_audit_ai_style_hook.py` [[workflows/audit/tests/CONTEXT]] - Tests for the ai-style-guard merge helper (`ai_style_findings`, WARN-only) and the graceful-skip paths of `run_ai_style_check` (missing CLI, unparseable output, subprocess exception, and a valid-payload merge).
- test_audit_doc_sync_hook.py - `workflows/audit/tests/test_audit_doc_sync_hook.py` [[workflows/audit/tests/CONTEXT]] - Tests for the doc-sync-guard merge helper (`doc_sync_findings`, WARN-only, including scan-skipped WARN payloads) and the graceful-skip paths of `run_doc_sync_check` (missing CLI, unparseable output, subprocess exception, and a valid-payload merge).
- test_audit_degraded.py - `workflows/audit/tests/test_audit_degraded.py` [[workflows/audit/tests/CONTEXT]] - Tests the DEGRADED status: the `degraded()` helper (severity, repairable messages naming setup.py, non-repairable messages flagging a missing workflow) and `format_report` rendering (count line, its own section, not counted as a failure or a clean pass).
- test_audit_subdir_filter.py - `workflows/audit/tests/test_audit_subdir_filter.py` [[workflows/audit/tests/CONTEXT]] - Tests that immediate subdirectory discovery filters gitignored child directories while keeping normal children; that `audit_directory` reuses a passed-in subdirs list without spawning git and falls back to `get_immediate_subdirs` when none is supplied; and that the parent-indexed children map matches `get_immediate_subdirs` per directory.
- test_collect_dirs.py - `workflows/audit/tests/test_collect_dirs.py` [[workflows/audit/tests/CONTEXT]] - Tests the breadth-first batched `collect_dirs` walk: `_is_git_worktree` detection, the non-Git fast path (git never spawned), gitignore pruning of an ignored dir and its children, skip/hidden exclusion, no-recurse dirs recorded but not descended, one batched check per level, and sorted output.
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
- `workflows/audit/scripts/run.py` [[workflows/audit/scripts/CONTEXT]] - The module under test (`graph_findings`/`run_graph_validation`, `encoding_findings`/`run_encoding_check`, `personal_findings`/`run_personal_data_check`, `ai_style_findings`/`run_ai_style_check`, `doc_sync_findings`/`run_doc_sync_check`, `get_immediate_subdirs`, and `collect_dirs`/`_is_git_worktree`/`_git_check_ignored_batch`).
- Python 3.9+ standard library only (unittest, unittest.mock, pathlib).

## Known Issues
- The tests patch the `subprocess.run` boundary rather than spawning the real knowledge-graph validator, so the live `validate --json` contract is covered by the close-out audit run, not here. This keeps the suite fast and hermetic.

## Revision History
- 2026-06-23 — Initial creation. Tests for the knowledge-graph audit-hook merge helper and graceful-degradation paths.
- 2026-06-24 - Added test_audit_encoding_hook.py covering the encoding-guard merge helper and graceful-skip paths.
- 2026-06-25 - Added test_audit_subdir_filter.py covering gitignored child-directory filtering in immediate subdirectory discovery.
- 2026-06-25 - Added test_audit_personal_data_hook.py covering the personal-data-guard merge helper and graceful-skip paths.
- 2026-06-25 - Added test_audit_ai_style_hook.py covering the ai-style-guard merge helper (WARN-only) and graceful-skip paths.
- 2026-06-26 - Added test_collect_dirs.py (8 tests) covering the breadth-first batched `collect_dirs` walk: worktree detection, non-Git fast path, gitignore/skip/hidden/no-recurse pruning, one batched check per level, and sorted output.
- 2026-06-26 - Extended test_audit_subdir_filter.py (+3 tests): `audit_directory` reuses an explicit subdirs list without spawning git, falls back to `get_immediate_subdirs` when none is passed, and the parent-indexed children map equals `get_immediate_subdirs` per directory.
- 2026-07-04 - Added test_audit_degraded.py for the new DEGRADED status; updated the four hook tests to assert a DEGRADED finding (was INFO) on the missing-CLI / unparseable / exception paths.
- 2026-07-06 - Added test_audit_doc_sync_hook.py covering the doc-sync-guard merge helper (`doc_sync_findings`, WARN-only) and the graceful-skip paths of `run_doc_sync_check`.
- 2026-07-07 - Updated doc-sync hook coverage for the guard's scan-skipped contract: a git-discovery skip now arrives as a WARN payload and is mapped into the audit report rather than being dropped as INFO.
