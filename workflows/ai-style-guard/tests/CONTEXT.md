# Tests

**Last modified:** 2026-06-25

## Purpose
Unit tests for the AI-style guard. They exercise the fiddly diff hunk parser with fixture diffs, the tier-1 and tier-2 detectors, the config loader, and the self-exemption guarantee. Trigger characters are built from code points rather than written literally, so this tracked source never carries the markers it tests for.

## Contents
- test_ai_style_guard.py - `workflows/ai-style-guard/tests/test_ai_style_guard.py` [[workflows/ai-style-guard/tests/CONTEXT]] - Tests for `parse_diff` (single and multi-hunk, multi-file, deletions, file-deletion skip, header handling), `scan_line` (typographic, phrase, and word detection with severity mapping), `_eligible` (self-directory and binary exclusion), and `load_config` (code-point parsing, phrase compilation, missing-file degrade, and that the shipped config parses).
- run_tests.py - `workflows/ai-style-guard/tests/run_tests.py` [[workflows/ai-style-guard/tests/CONTEXT]] - One-command unittest discovery runner; exits non-zero on any failure.

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
