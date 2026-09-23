# Link Check - Tests

**Last modified:** 2026-09-23

## Purpose
Tests for link-check's `--link` mode, and specifically for the regions it must not rewrite. The suite exists because `add_links_to_file` mutates CONTEXT.md files in place and had no test coverage at all, which is how it went on inserting links into dated Revision History entries across three live files before anyone noticed.

`add_links_to_file` transforms rather than counts or validates, so the three controls are stated against **the subject** - a backtick path reference that resolves to a real project target:

- **Positive control** - a legal instance: an eligible reference in a walkable region, which must be found and linked. This is the control that catches a skip written too wide, where a guard against rewriting history quietly stops the tool doing its job at all.
- **Rejection control** - an illegal instance: an eligible reference inside a dated Revision History entry, which must be refused. This is the defect the suite was written for.
- **Negative control** - not an instance of the subject: a document with no Revision History section, so a clean result is known not to be silence from the skip over-matching.

The boundary tests sit outside the three controls on purpose. The skipped section is defined as ending at the next `## ` heading, matching the definition the audit's own section parser uses, and the stage's stated goal is that the two tools agree on whether history is editable, so the boundary is a property under test rather than an implementation detail.

## Contents
- test_link_check.py - 19 tests in six classes. The original three: `RevisionHistorySkipTests` (the positive, rejection, and negative controls for the region skip); `SectionBoundaryTests` (the section ends at the next level-two heading so a later section is still processed, a `###` sub-heading does not close it, and the heading line itself survives being skipped); and `ExistingBehaviourTests` (properties that predate the skip and must survive it: a fenced code block is still skipped and is evaluated before the new flag, a file whose only candidate sat in history is not rewritten at all, and `--dry-run` reports without writing). Added 2026-09-22: `SelfLinkSkipTests` (the three controls for not linking a file to itself, asserted against `process_line` directly since it is pure); `SelfLinkRemovalTests` (the three controls for removing an existing self-link, plus the two helper-level cases and the proof that the region guards cover the removal as well as the insertion); and `OwnLinkTargetTests` (a CONTEXT.md path maps to its own link target).
- run_tests.py - Local one-command runner. A convenience only; close-out never reads it.

## Inputs
- `workflows/link-check/scripts/run.py` [[workflows/link-check/scripts/CONTEXT]], imported directly by file path. Every fixture is built inline and written into a `tempfile` directory.

## Outputs
- Test results on stdout (unittest). Exit code 0 when all pass, non-zero on failure. No files are written outside the temporary directory.

## Steps
1. Run `python workflows/link-check/tests/test_link_check.py`, or `python workflows/link-check/tests/run_tests.py` for the discovered form.
2. Confirm every test passes (exit 0).
3. When changing what `--link` walks or skips, add all three controls for the change here in the same task.
4. Append LOG.md with a completion or failure entry.

## Dependencies
- `workflows/link-check/scripts/run.py` [[workflows/link-check/scripts/CONTEXT]] - the module under test, loaded by `importlib` from a file path.
- Python 3.13+ standard library only (`unittest`, `tempfile`, `importlib`, `sys`).
- `workflows/close-out/` [[workflows/close-out/CONTEXT]] discovers and runs this suite as part of the close-out verifier, by globbing `tests/test_*.py`.
- `workflows/audit/` [[workflows/audit/CONTEXT]] - not imported, but its section-parser boundary is the definition these tests pin. If the audit changes where it thinks Revision History ends, the two tools stop agreeing and these tests will not notice.

## Known Issues
- The suite is not fully hermetic, and cannot be. `resolve_link_target` answers "does this path exist" against the real project root by design, so fixtures must cite a real directory; they use `workflows/audit/` [[workflows/audit/CONTEXT]], which is long-lived, but a rename would break the suite for reasons unrelated to what it tests.
- Nothing enforces the control naming. A test added without a `positive_control` / `rejection_control` / `negative_control` prefix is simply an unlabelled test, and a defect fixture mislabelled as a positive control passes exactly as well as a correct one. The convention is held by review and by the class docstrings alone.
- Assertions that anchor on a line prefix spanning the backtick reference break when the tool does its job, because the inserted link lands immediately after the reference. Use `line_containing` against trailing prose for any line the tool may rewrite. This bit one test in the pass that created this suite.
- The suite covers `--link` mode only. `--audit` and `--fix` remain untested, so a regression in dead-link detection or auto-resolution would not be caught here.
- **The self-link tests force the file's own link target rather than deriving it.** Fixtures live in a `tempfile` directory, so their real own-target is an absolute path that no resolved reference can ever equal, which would make the behaviour untestable end to end. `link_file_owning` patches `own_link_target` for the duration, so a fixture citing a real project directory is treated as citing itself. Everything else in that path stays real, including `resolve_link_target`'s existence checks against the project root. The trade is that the mapping from a path to its own target is proved separately, by `OwnLinkTargetTests`, rather than inside the end-to-end cases.

## Revision History
- 2026-08-11 - Initial creation as stage 4a of the guard-coverage plan. 9 tests across three classes, with a positive, rejection, and negative control for the Revision History skip plus the boundary and pre-existing-behaviour cases. Written and run against the unfixed script first, where 4 of the 9 failed and the positive and negative controls passed, so the suite was shown to fail for the stated reason before the fix made it pass.
- 2026-09-05 - An Obsidian link inserted by the link pass, run as the close-out step of unrelated rule-hooks work. Recorded because the file changed rather than because the change is interesting. Worth one note here specifically, since this is the link-check workflow's own test directory: the pass modifying CONTEXT.md files in directories nobody touched is what turns a routine close-out step into documentation obligations elsewhere, which is the same family as the open backlog item about `--link` inserting a link to the file it is already in.
- 2026-09-22 - Added `SelfLinkSkipTests`, `SelfLinkRemovalTests` and `OwnLinkTargetTests` (9 to 19 tests) for that backlog item, now closed. Both new behaviours carry the three controls this directory requires of any change to what `--link` walks or skips, and the two self-links this file itself carried were removed in the same pass, which is the cleanup policy applied to the file documenting it. The suite was shown to fail for the stated reason before it passed: with the skip disabled, three tests fail, matching the standard set when this directory was created. The mutation pass ran seven mutants over module attributes with no file in the tree edited, and each fired exactly what it should - the pre-fix state fires the skip's rejection control and two removal tests, an always-skip mutant fires eleven including both positive controls and the pre-existing behaviour tests, a strip that removes every link fires only the leaves-another-alone case, a mutant letting a removal earn a write fires only the control pinning the lazy policy, a no-op strip fires the two helper cases, a wrong own-target mapping fires its own class, and the no-op rebuild fires nothing.
- 2026-09-23 - Dependencies line says Python 3.13+, following the project floor raised from 3.9 to 3.13. No behaviour change.
