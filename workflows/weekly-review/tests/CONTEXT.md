# Weekly Review - Tests

**Last modified:** 2026-10-05

## Purpose
Hermetic unit tests for the weekly-review gather logic. They cover the parts
whose correctness matters most: the journal backfill mechanism (so a backfilled
day is never silently lost) and the deterministic readers that build the packet.

## Contents
- run_tests.py - `workflows/weekly-review/tests/run_tests.py` [[workflows/weekly-review/tests/CONTEXT]] - Single entry
  point; discovers and runs every `test_*.py`, exits non-zero on failure (used by
  close-out).
- test_state.py - `workflows/weekly-review/tests/test_state.py` [[workflows/weekly-review/tests/CONTEXT]] - Window
  computation (watermark, fallback, cap, thin span), the run day left out for
  every source (the window and watermark ending the day before, the run day
  opening the next window, and `run.py`'s own window against a fixture state and
  journal) and the backfill logic (catch-up of a backfilled day, horizon drop,
  corrupt-input tolerance, load/save roundtrip).
- test_gather.py - `workflows/weekly-review/tests/test_gather.py` [[workflows/weekly-review/tests/CONTEXT]] - Journal
  section parsing and content detection, LOG/memory date filtering, session
  summary and prior-review selection (excluding CONTEXT.md/LOG.md and the current
  week), ISO week label, and packet assembly.

## Inputs
None. Tests build temporary fixtures in-process; they do not read the live
project stores.

## Outputs
None. Pass/fail via exit code.

## Steps
1. Run `python workflows/weekly-review/tests/run_tests.py`.
2. All tests must pass before a review is trusted or the work is closed out.

## Dependencies
- `workflows/weekly-review/scripts/` [[workflows/weekly-review/scripts/CONTEXT]]
  - the modules under test.
- Python standard library `unittest` only.

## Known Issues
- The tests cover `state.py` and `gather.py` directly, and of `run.py` its window
  (`_resolve`) and `--record`'s refusals, with its paths pointed at a fixture. The rest of `run.py`
  (which binds the modules to the live project paths) is exercised by the manual
  end-to-end run during close-out, since it is thin glue over the tested modules.

## Revision History
- 2026-07-02 - Initial creation. 28 tests across state and gather.
- 2026-07-03 - Added three tests for the run-day rule (excluded from inclusion,
  deferred when empty, caught by the next review). 28 -> 31.
- 2026-08-04 - Maintenance, no test change: the two entries above were reordered
  into the newest-at-bottom order the schema requires, after the audit's new
  Revision History ordering check flagged them. Recorded rather than left silent,
  for the reason given in the parent workflow's matching entry.
- 2026-08-04 - The eleven fixture writes across `test_gather.py` (10) and
  `test_state.py` converted from `Path.write_text(..., encoding="utf-8")` to
  `write_bytes`, so they stop writing CRLF fixtures on Windows and stop breaking
  the newline half of the AGENTS.md text-I/O rule. `test_gather.py` held the
  single largest concentration in the project, and the fixtures are journal
  sections and `LOG.md` bodies that the gather readers parse line by line, so
  CRLF endings were feeding them a different input here than they see in the
  real tree. `write_bytes` rather than `Path.write_text(newline=...)`, which is
  a 3.10 API against the stated 3.9 floor. Part of the pass clearing the last 50
  sites project-wide; `encoding` became a close-out blocking label in the same
  change. Suite unchanged at 31; no assertion touched.
- 2026-10-05 - `TestRunDayLeftOut` in `test_state.py`: the run day left out of its
  own review for every source, including a test of `run.py`'s window. 31 -> 35;
  with the old window end restored in a scratch copy, the suite failed. Then, from
  Codex's review: git's whole-day bounds against a fixture repository, a pending
  day after the window, a session crossing midnight counted once, and
  `TestRecordRefusals` (a due review stating its window recorded; the gather and
  `--record` refused while this week's review is done; a file not stating its
  window refused). 35 -> 41; each of the six fixes undone in a scratch copy made
  the suite fail. From its third round: a UTC session start and an offset-stamped
  commit dated on the local calendar, and the weekly gate allowing the morning of
  the due date. 41 -> 44; nine fixes each undone in a scratch copy made it fail.
