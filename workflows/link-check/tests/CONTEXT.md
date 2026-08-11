# Link Check - Tests

**Last modified:** 2026-08-11

## Purpose
Tests for link-check's `--link` mode, and specifically for the regions it must not rewrite. The suite exists because `add_links_to_file` mutates CONTEXT.md files in place and had no test coverage at all, which is how it went on inserting links into dated Revision History entries across three live files before anyone noticed.

`add_links_to_file` transforms rather than counts or validates, so the three controls are stated against **the subject** - a backtick path reference that resolves to a real project target:

- **Positive control** - a legal instance: an eligible reference in a walkable region, which must be found and linked. This is the control that catches a skip written too wide, where a guard against rewriting history quietly stops the tool doing its job at all.
- **Rejection control** - an illegal instance: an eligible reference inside a dated Revision History entry, which must be refused. This is the defect the suite was written for.
- **Negative control** - not an instance of the subject: a document with no Revision History section, so a clean result is known not to be silence from the skip over-matching.

The boundary tests sit outside the three controls on purpose. The skipped section is defined as ending at the next `## ` heading, matching the definition the audit's own section parser uses, and the stage's stated goal is that the two tools agree on whether history is editable, so the boundary is a property under test rather than an implementation detail.

## Contents
- test_link_check.py - `workflows/link-check/tests/test_link_check.py` - 9 tests in three classes: `RevisionHistorySkipTests` (the positive, rejection, and negative controls for the region skip); `SectionBoundaryTests` (the section ends at the next level-two heading so a later section is still processed, a `###` sub-heading does not close it, and the heading line itself survives being skipped); and `ExistingBehaviourTests` (properties that predate the skip and must survive it: a fenced code block is still skipped and is evaluated before the new flag, a file whose only candidate sat in history is not rewritten at all, and `--dry-run` reports without writing).
- run_tests.py - `workflows/link-check/tests/run_tests.py` - Local one-command runner. A convenience only; close-out never reads it.

## Inputs
- `workflows/link-check/scripts/run.py`, imported directly by file path. Every fixture is built inline and written into a `tempfile` directory.

## Outputs
- Test results on stdout (unittest). Exit code 0 when all pass, non-zero on failure. No files are written outside the temporary directory.

## Steps
1. Run `python workflows/link-check/tests/test_link_check.py`, or `python workflows/link-check/tests/run_tests.py` for the discovered form.
2. Confirm every test passes (exit 0).
3. When changing what `--link` walks or skips, add all three controls for the change here in the same task.
4. Append LOG.md with a completion or failure entry.

## Dependencies
- `workflows/link-check/scripts/run.py` [[workflows/link-check/scripts/CONTEXT]] - the module under test, loaded by `importlib` from a file path.
- Python 3.9+ standard library only (`unittest`, `tempfile`, `importlib`, `sys`).
- `workflows/close-out/` [[workflows/close-out/CONTEXT]] discovers and runs this suite as part of the close-out verifier, by globbing `tests/test_*.py`.
- `workflows/audit/` [[workflows/audit/CONTEXT]] - not imported, but its section-parser boundary is the definition these tests pin. If the audit changes where it thinks Revision History ends, the two tools stop agreeing and these tests will not notice.

## Known Issues
- The suite is not fully hermetic, and cannot be. `resolve_link_target` answers "does this path exist" against the real project root by design, so fixtures must cite a real directory; they use `workflows/audit/`, which is long-lived, but a rename would break the suite for reasons unrelated to what it tests.
- Nothing enforces the control naming. A test added without a `positive_control` / `rejection_control` / `negative_control` prefix is simply an unlabelled test, and a defect fixture mislabelled as a positive control passes exactly as well as a correct one. The convention is held by review and by the class docstrings alone.
- Assertions that anchor on a line prefix spanning the backtick reference break when the tool does its job, because the inserted link lands immediately after the reference. Use `line_containing` against trailing prose for any line the tool may rewrite. This bit one test in the pass that created this suite.
- The suite covers `--link` mode only. `--audit` and `--fix` remain untested, so a regression in dead-link detection or auto-resolution would not be caught here.

## Revision History
- 2026-08-11 - Initial creation as stage 4a of the guard-coverage plan. 9 tests across three classes, with a positive, rejection, and negative control for the Revision History skip plus the boundary and pre-existing-behaviour cases. Written and run against the unfixed script first, where 4 of the 9 failed and the positive and negative controls passed, so the suite was shown to fail for the stated reason before the fix made it pass.
