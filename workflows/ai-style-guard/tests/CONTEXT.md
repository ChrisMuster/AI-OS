# Tests

**Last modified:** 2026-07-05

## Purpose
Unit tests for the AI-style guard. They exercise the fiddly diff hunk parser with fixture diffs, the tier-1 and tier-2 detectors, the config loader, and the self-exemption guarantee. Trigger characters are built from code points rather than written literally, so this tracked source never carries the markers it tests for.

## Contents
- test_ai_style_guard.py - `workflows/ai-style-guard/tests/test_ai_style_guard.py` [[workflows/ai-style-guard/tests/CONTEXT]] - Tests for `parse_diff` (single and multi-hunk, multi-file, deletions, file-deletion skip, header handling), `scan_line` (typographic, phrase, and word detection with severity mapping), `_eligible` (self-directory and binary exclusion), `load_config` (code-point parsing, phrase compilation, missing-file degrade, and that the shipped config parses), and the bootstrap guarantee (the guard hands off to the `.venv` via `ensure_project_runtime()` rather than degrading on missing PyYAML).
- run_tests.py - `workflows/ai-style-guard/tests/run_tests.py` [[workflows/ai-style-guard/tests/CONTEXT]] - One-command unittest discovery runner; bootstraps into the project `.venv` via `ensure_project_runtime()` before discovery (the config-loader tests read the tells config through PyYAML), then exits non-zero on any failure.

## Inputs
- The guard module at `workflows/ai-style-guard/scripts/run.py` [[workflows/ai-style-guard/scripts/CONTEXT]], imported directly.
- The shipped `workflows/ai-style-guard/config/ai-tells.yaml` [[workflows/ai-style-guard/config/CONTEXT]], for the loadability test.

## Outputs
- Test results on stdout. Exit 0 when all pass, 1 otherwise. No files written.

## Steps
1. Run `python workflows/ai-style-guard/tests/run_tests.py`.
2. All tests must pass before the work is considered ready.

## Dependencies
- `workflows/ai-style-guard/scripts/run.py` [[workflows/ai-style-guard/scripts/CONTEXT]] - The module under test.
- `workflows/ai-style-guard/config/ai-tells.yaml` [[workflows/ai-style-guard/config/CONTEXT]] - Loaded by one test to confirm the shipped config is valid.
- Python 3.9+ standard library (`unittest`); PyYAML via the module under test.

## Known Issues
- None.

## Revision History
- 2026-06-25 - Initial creation. 19 tests covering the hunk parser, detectors, eligibility, and config loader.
- 2026-07-04 - Added BootstrapTests: the guard must bootstrap into the `.venv` (contains `ensure_project_runtime()`) and must not carry a PyYAML degrade marker, since a guard that silently skips reports a false clean. 19 -> 20 tests.
- 2026-07-05 - `run_tests.py` now bootstraps into the project `.venv` via `ensure_project_runtime()` before discovery, so the documented `python workflows/ai-style-guard/tests/run_tests.py` command passes under a plain interpreter that lacks PyYAML (the config-loader tests import the guard and call `load_config`, which reads the config through PyYAML). No test-count change. Codex review-fix pass on the runtime-bootstrap work.
