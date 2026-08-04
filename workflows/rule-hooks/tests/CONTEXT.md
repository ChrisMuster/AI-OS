# Rule Hooks - Tests

**Last modified:** 2026-08-04

## Purpose
Synthetic-event tests for the rule-hooks evaluator: prove each rule blocks what it should and allows what it should (the false-positive guards), and prove each adapter parses its AI's payload and emits the right block contract. Verification discipline: the build is not done until the suite passes and a live block is confirmed.

## Contents
- test_rule_hooks.py - `workflows/rule-hooks/tests/test_rule_hooks.py` [[workflows/rule-hooks/tests/CONTEXT]] - The full suite (65 tests): per-rule block/allow cases via the in-process Context pipeline, adapter input-parsing tests, subprocess tests asserting the real output contracts (Claude exit 2 + stderr; Codex `hookSpecificOutput` JSON deny), `TestPrecommitDocSync` (the pre-commit doc-sync advisory: the pure `format_doc_sync_advisory` text, that a personal-data block short-circuits before the advisory, that a clean personal-data check runs the advisory and still allows the commit, and that a doc-sync guard crash is swallowed), `TestPrecommitDocSyncSeverity` (the advisory's severity filter: drift alone prints the advisory - the positive control - while a DEGRADED finding alone prints nothing and a degrade alongside real drift neither prints itself nor masks the drift), and `TestCwdIndependence` (the hook launches `run.py` from a project-root-anchored command, so evaluation must survive a drifted working directory: Claude invocations from a temp directory outside the repo still block a `.env` write and still allow a normal write; shipped `.claude/settings.json` hook commands anchor `run.py` to `$CLAUDE_PROJECT_DIR`; shipped `.codex/config.toml` hook commands resolve the git root; and a Codex hook command blocks from a project subdirectory). Destructive cases use safe synthetic strings only; the B3 detection email is assembled at runtime so the literal never appears in the committed source.

## Inputs
- None beyond the workflow scripts under test and the live git repo (the B3 gitignored-vs-committable test uses real `git check-ignore`).

## Outputs
- Test pass/fail results on stdout.

## Steps
1. Run: `python workflows/rule-hooks/tests/test_rule_hooks.py`
2. All tests must pass before the work is ready.

## Dependencies
- `workflows/rule-hooks/scripts/` [[workflows/rule-hooks/scripts/CONTEXT]] - The code under test.
- `workflows/personal-data-guard/` [[workflows/personal-data-guard/CONTEXT]] - Reached via the B3 rule in the personal-data tests.

## Known Issues
- The subprocess tests spawn `run.py`, so the suite shells out; it stays fast (about 2 seconds) because the evaluator is light at import time.

## Revision History
- 2026-06-30 - Initial creation: 33 tests across A7/A4/A6/B3 rules, A2/A3 trials, both adapters, and the subprocess exit-code contracts.
- 2026-07-01 - Added Codex regression coverage for the live hook failure: apply_patch payloads using `tool_input.command` and the supported `hookSpecificOutput.permissionDecision="deny"` contract.
- 2026-07-07 - Added `TestPrecommitDocSync` (4 tests) for the pre-commit doc-sync advisory: the advisory text, personal-data-block short-circuit, clean-then-advisory-and-allow, and swallowed guard crash. 53 -> 57 tests.
- 2026-07-25 - Added `TestCwdIndependence` (3 tests) guarding the cwd-drift fix: `run.py` launched by absolute path from a temp directory outside the repo still blocks a `.env` write and still allows a normal write, and the shipped `.claude/settings.json` hook commands are asserted to anchor `run.py` to `$CLAUDE_PROJECT_DIR`. 57 -> 60 tests.
- 2026-07-31 - Added Codex cwd-drift regression coverage: config inspection asserts `.codex/config.toml` hook launchers resolve the git project root, and a shipped Codex PreToolUse command blocks from the `workflows/` subdirectory. 60 -> 62 tests.
- 2026-08-04 - Added `TestPrecommitDocSyncSeverity` (3 tests) pinning the advisory's WARN-only filter, so the DEGRADED-invisible-at-commit-time boundary documented in three CONTEXT.md files rests on a test rather than on a code read. The guard is stubbed at `_load_guard` and stderr captured, with drift-alone as the positive control that the stub reaches the real formatter. 62 -> 65 tests.
