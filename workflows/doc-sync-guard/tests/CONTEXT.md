# Tests

**Last modified:** 2026-07-07

## Purpose
Unit and integration tests for the doc-sync guard. The pure helpers (CONTEXT.md parsing, LOG.md timestamp parsing, ownership resolution, and diff parsing) are exercised with fixtures; the end-to-end behaviour is exercised against real throwaway git repositories built in a temp directory, so scope collection, ownership, and the mtime-based LOG check run the way they do for real.

## Contents
- test_doc_sync_guard.py - `workflows/doc-sync-guard/tests/test_doc_sync_guard.py` [[workflows/doc-sync-guard/tests/CONTEXT]] - Tests for `context_parse` (Last modified, Revision History dates, archive-line skipping, bracketed-entry counting, and the section-aware `gained_rh_entry` including the same-day second-entry, dated-bullet-outside-the-section, edited-entry-date-forward, edited-entry-text, flat-count archive-plus-add, indented-sub-bullet, and reorder-edit-with-fake-archive cases), `logtime` (newest entry, legacy date-only, behind/tolerance/ahead), ownership and diff parsing (`owner_dir`, `committable_context_dirs`, `deleted_context_dirs`, `path_under_dir`, `_parse_name_status`), and the integration scenarios (clean pass, CONTEXT not updated, missing Revision History entry, editing an old entry forward flagged, Last modified mismatch, same-day edit pass, untracked coverage, new-directory-owns-itself, parent propagation, move between two documented directories flagging both source and destination, removed child directory flagging the parent without an impossible deleted-child warning, git failure surfacing as WARN, LOG behind, trailing source edit after the LOG entry, deleted-file skip, missing LOG, gitignore carve-out, the `--json` contract, staged mode, and staged-blob reads under a divergent working tree).
- run_tests.py - `workflows/doc-sync-guard/tests/run_tests.py` [[workflows/doc-sync-guard/tests/CONTEXT]] - One-command unittest discovery runner (no `.venv` bootstrap needed - the guard has no third-party dependencies); exits non-zero on any failure.

## Inputs
- The guard modules at `workflows/doc-sync-guard/scripts/` [[workflows/doc-sync-guard/scripts/CONTEXT]], imported directly.
- The `git` CLI, used to build the throwaway repositories the integration tests run against.

## Outputs
- Test results on stdout. Exit 0 when all pass, 1 otherwise. No project files written (temp repositories are created under the system temp directory and cleaned up).

## Steps
1. Run `python workflows/doc-sync-guard/tests/run_tests.py`.
2. All tests must pass before the work is considered ready.

## Dependencies
- `workflows/doc-sync-guard/scripts/run.py` [[workflows/doc-sync-guard/scripts/CONTEXT]] and its `context_parse` / `logtime` helpers - the modules under test.
- The `git` CLI - the integration tests build real repositories; without git those tests cannot run.
- Python 3.9+ standard library (`unittest`, `tempfile`, `subprocess`).

## Known Issues
- The integration tests shell out to `git` and create temp repositories, so they are slower than the pure-helper tests and require git on PATH.

## Revision History
- 2026-07-06 - Initial creation. 38 tests: pure-helper unit tests for `context_parse`, `logtime`, ownership, and diff parsing, plus integration tests against throwaway git repositories covering the plan's CONTEXT, LOG, untracked, ownership, and parent-propagation scenarios.
- 2026-07-07 - Codex review-fix coverage (42 tests). Replaced the `added_lines_have_rh_entry` / `_parse_added_lines` unit tests with `gained_rh_entry` tests (count grows, count same, same-day second entry, dated bullet outside the section, brand-new untracked file, and an add-while-archiving change that keeps the count flat but advances the newest date) plus a bracketed-entry `revision_history_dates` test; added two integration tests: a move between two documented directories flags both source and destination (finding 1), and a removed child directory flags the parent (coarse propagation).
- 2026-07-07 - Second-review `gained_rh_entry` regression coverage (46 tests). Added three unit tests (editing an old entry's date forward is not a gain, editing an old entry's text is not a gain, and a flat-count archive-plus-add is a gain) and one integration test (a real content change whose only Revision History edit moves the existing entry's date forward is flagged as gaining no entry).
- 2026-07-07 - Third-review `gained_rh_entry` archive-branch coverage (50 tests). Added three unit tests (an archive reference line masking an in-place edit is not a gain, archiving every old entry biases to not-gained, and a genuine archive that keeps a suffix and appends is still a gain) and one integration test (a real content change whose only Revision History edit is an in-place edit plus a bogus archive line is flagged as gaining no entry).
- 2026-07-07 - Adversarial-review coverage (55 tests). Three unit tests: an indented dated sub-bullet is not counted by `revision_history_dates`, adding only an indented sub-bullet is not a `gained_rh_entry` gain, and an old entry edited and moved to the bottom with a bogus archive line is not a gain. Two integration tests for `--staged` mode reading the staged blob: a divergent working tree does not cause a false warning when the staged CONTEXT.md is correct, and an entry present only in the working tree (not staged) is correctly warned.
- 2026-07-07 - Codex review regression coverage (58 tests). Added ownership tests for `deleted_context_dirs` and `path_under_dir`, tightened the removed-child-directory integration test to assert no impossible deleted-child own-directory warning appears, and added a git-failure integration test proving an unrun scan is a doc-sync WARN rather than INFO.
