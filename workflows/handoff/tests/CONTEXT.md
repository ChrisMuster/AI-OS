# Handoff - Tests

**Last modified:** 2026-08-04

## Purpose
Hermetic unit tests for the handoff gather readers and the seen-watermark logic.

## Contents
- run_tests.py - single entry point; discovers and runs every `test_*.py`.
- test_state.py - load/save, handoff-timestamp parsing (`**Created:**` line and
  mtime fallback), and the is_unread freshness logic.
- test_gather.py - changed-directory derivation, LOG tail + entry filtering,
  Active-backlog parsing, recent-session reads (temp sqlite shard), git degrade
  paths, the doc-sync drift reader's degrade-to-empty path and its WARN-only
  severity filter (a stubbed guard payload: drift alone is returned, a DEGRADED
  finding alone is not, and a degrade never masks real drift), and packet
  assembly (including the "CONTEXT/LOG drift (doc-sync)" section, clean and
  populated).

## Inputs
None. Tests build their own temporary fixtures (temp dirs and an in-memory-style
sqlite shard on disk).

## Outputs
Test results to stdout; exit code 0 on success, non-zero on failure.

## Steps
1. `python workflows/handoff/tests/run_tests.py`.
2. Fails the run if any test fails (used by close-out).

## Dependencies
- `workflows/handoff/scripts/` [[workflows/handoff/scripts/CONTEXT]] - the modules
  under test.
- Python standard library `unittest`, `sqlite3`, `tempfile`.

## Known Issues
- git-backed readers are only tested for the degrade-to-empty path; their happy
  path is exercised by the live smoke test rather than a fixture repo.

## Revision History
- 2026-07-03 - Initial creation. 22 tests across test_state.py and test_gather.py.
- 2026-07-07 - Added coverage for the doc-sync drift reader (degrade-to-empty) and
  the packet's "CONTEXT/LOG drift (doc-sync)" section (clean and populated). 22 ->
  24 tests (doc-sync-guard build part 5).
- 2026-08-04 - Added `TestDocSyncDriftSeverity` (3 tests) pinning the reader's
  WARN-only filter, so the boundary documented in the workflow's and the scripts
  directory's CONTEXT.md rests on a test rather than on a code read. The guard is
  stubbed at the subprocess boundary, with drift alone as the positive control.
  24 -> 27 tests.
- 2026-08-04 - The three fixture writes in `test_gather.py` converted from
  `Path.write_text(..., encoding="utf-8")` to `write_bytes`, so the suite stops
  writing CRLF fixtures on Windows and stops breaking the newline half of the
  AGENTS.md text-I/O rule. These fixtures are `LOG.md` and `backlog.md` bodies
  parsed line by line, so CRLF endings were feeding the parser a different input
  here than it sees in the real tree. `write_bytes` rather than
  `Path.write_text(newline=...)`, which is a 3.10 API against the stated 3.9
  floor. Test count unchanged at 27; no assertion touched.
- 2026-08-04 - The three fixture writes in `test_state.py` converted to
  `write_bytes` on the same reasoning (the earlier pass covered `test_gather.py`
  only). One of them is a `HANDOVER.md` body whose `**Created:**` line the
  parser reads, so a CRLF ending was feeding it a different input here than it
  sees in the real tree. Part of the pass clearing the last 50 sites
  project-wide; `encoding` became a close-out blocking label in the same change.
  Test count unchanged at 27; no assertion touched.
