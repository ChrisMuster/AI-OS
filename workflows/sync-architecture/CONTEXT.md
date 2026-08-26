# Sync Architecture

**Last modified:** 2026-08-26

## Purpose

Read-only verification for the sync architecture design documents and for the file
classification they define. It exists because that classification is the thing the
build acts on: it decides which files a second, private git repository will track
permanently, and a mistake in it is discovered only after a permanent commit.

The classification used to live as prose in two documents in two notations, and a
decision taken in one of them failed to reach the patterns in the other. This workflow
replaces "the documents agree" as an assertion with a check that can fail.

## Contents

- `scripts/` - `workflows/sync-architecture/scripts/` [[workflows/sync-architecture/scripts/CONTEXT]] - Holds the workflow's entry point, the shared selection module, and their documentation.
- `scripts/run.py` - the entry point. Runs two read-only checks, together by default.
  `--allowlist` extracts the executable classification block from the architecture
  plan and proves what it actually selects against the real working tree.
  `--consistency` checks both design documents for the defect classes that have
  recurred: a superseded pathspec spelling left standing, a second copy of the
  classification, an instruction to run a measuring script that is not in the tree,
  and a stale review baseline.
- `scripts/allowlist.py` - the single executable copy of the selection rule, shared by
  this workflow's checks and by the Stage A build so the two cannot resolve the same
  pathspecs differently. Parses the plan's block, resolves it in the personal
  repository's git context, subtracts the public repository's tracked set, and checks
  the invariant that makes those two contexts agree. `--self-test` builds a tree where
  they must disagree and proves it can tell them apart. Pure Python over the git CLI
  with no shell pipeline, so it runs on any AI in the roster.
- `tests/` - `workflows/sync-architecture/tests/` [[workflows/sync-architecture/tests/CONTEXT]] - The
  test suite for the selection module, discovered and run by the close-out verifier.
  Deliberately not hermetic: the subject under test is the difference between two git
  contexts, so every fixture is a real repository built under `tempfile`.

## Inputs

- The architecture plan and its Stage A build spec at the project root. Both are
  design-time documents and therefore gitignored, so on a fresh clone they are absent.
  Their absence is reported as SKIPPED rather than as a pass or a failure.
- The architecture plan must carry a fenced ` ```allowlist ` block. That block is the
  authority for the classification, and the check reads it from the document rather
  than from a copy, so the two cannot drift apart.
- A working `git`, used read-only.

## Outputs

Nothing on disk from the checks: a report on stdout and an exit code, non-zero if any
check failed. `scripts/allowlist.py --stage` is the one writing path in the workflow,
and it writes only to the personal repository's index, never to the working tree and
never to the public repository. It honours `--dry-run`, and it refuses to stage at all
while any file is both publicly tracked and matched by an ignore rule.

## Steps

1. Read the architecture plan and extract the fenced classification block.
2. Resolve that block's pathspecs in the same git context the Stage A build uses: an
   empty personal repository looking at this working tree, via `--git-dir` and
   `--work-tree`. Subtract the public repository's tracked set from what comes back.
   The subtraction is what prevents an overlap. Resolving the pathspecs does not,
   because in this context "untracked" means untracked by the personal repository,
   whose index is empty until the seed commit.
3. Assert the positive controls: properties that must be TRUE of the selection, such
   as an authored personal folder contributing a non-markdown file and a file from a
   subfolder. A suite of exclusions alone would pass on an empty allowlist.
4. Assert the category sweeps: kinds of file that must not appear at all.
5. Assert the invariant the design rests on: no file is both publicly tracked and
   matched by an ignore rule. While that holds, the two git contexts return the same
   answer. It is held up by the negation lines in `.gitignore` and nothing else.
6. Assert that the uncorrected selection contained no publicly tracked file, and that
   the corrected one excludes them.
7. Check both design documents for the recurring defect classes.
8. Compare each review baseline against its live document by SHA-256.
9. Print the report and exit non-zero on any failure.
10. Append LOG.md with a completion or failure entry.

## Dependencies

- The two design documents at the project root, which are gitignored. This workflow
  degrades to SKIPPED without them rather than failing.
- `git`. Used read-only by the checks; `scripts/allowlist.py --stage` also writes to the
  personal repository's index.
- The fenced ` ```allowlist ` block in the architecture plan. Removing or renaming that
  block breaks the allowlist check, which is intended: the check is meaningless without
  an authority to read.
- `tests/` [[workflows/sync-architecture/tests/CONTEXT]], which the close-out verifier
  discovers under this workflow's name and runs as one of its three gates.
- `workflows/close-out/` [[workflows/close-out/CONTEXT]], the runner for that suite. It
  discovers a suite by finding a tests directory inside a workflow and reading the files
  in it whose names begin with "test_" and end in ".py", so the tests must keep that
  naming and stay in that directory or they stop being run without anything reporting it.
- The Stage A build in `SHADOW-REPOSITORY-PLAN.md` calls `scripts/allowlist.py` rather
  than carrying its own copy of the selection. Changing the module's interface changes
  the build, which is the point of sharing it, and is why that document names the
  command rather than restating the pathspec resolution.

## Known Issues

- The consistency check tests named defect classes, so it proves the absence of the
  faults it knows about and nothing more. It cannot establish that the documents are
  correct, only that they are free of the specific errors that have recurred. Treat a
  clean run as a floor rather than a verdict.
- The assertions are structural rather than naming individual files, deliberately: a
  tracked file must not carry personal filenames, and a structural assertion also keeps
  working when the underlying files change. The trade is that it proves a property
  holds for some file rather than for one named file.
- The allowlist check reflects the working tree at the moment it runs. The design
  documents are themselves inside the selection, so editing them changes the reported
  count and size. No figure it prints should be copied into a document.
- The personal repository's own `info/exclude` is not read when the selection resolves
  in its git context, because `--git-dir` points the exclude lookup at that repository
  rather than the public one. Only the shared worktree `.gitignore` governs both, which
  is the intended design and is why the invariant check reads `.gitignore` alone. A
  future personal-repository-local exclude rule would not be seen by this check.

## Revision History

- 2026-08-25 - Initial creation. Built after a blind multi-reviewer round found that
  the classification decisions taken on 2026-08-24 had been written into the prose of
  both design documents while the patterns implementing them were left unchanged, so
  the allowlist as written would have excluded the exact files a decision had been
  taken to protect. Both checks existed first as throwaway scripts in a session
  scratchpad, which repeated the failure recorded in the review ledger, where a
  measuring script cited by three documents as the way to reproduce their figures had
  been left in a scratchpad and was gone by the next review. Giving them a tracked home
  is what makes the documents' citation of them true.
- 2026-08-26 - Added `scripts/allowlist.py` and rebuilt the allowlist check on it.
  A cross-AI review found that the check resolved the classification pathspecs in
  public-repository context while the Stage A build resolves them in the personal
  repository's, where "untracked" means something different. The consequence was that
  the no-overlap assertion could not fail: public context has already removed every
  tracked file before the assertion looks for one. Measured by deleting a single
  negation line from `.gitignore`, which the old form passed with an overlap of 0 and
  the new form failed naming the file; the line was restored byte-identically. The
  selection, the correcting subtraction and the invariant beneath them now live in one
  module that the check imports and the build calls, so the two cannot drift, and it is
  pure Python rather than a shell pipeline through `xargs`, which was unavailable on
  part of the AI roster.
- 2026-08-26 - Added `tests/`, so the close-out verifier actually runs these checks.
  The module shipped with a `--self-test`, but close-out only discovers suites inside a
  workflow's own tests directory, so a full close-out over this workflow reported zero
  test files and the self-test would never have run: a check that exists and is not
  reachable by the gate is not covered by it. The suite was mutation-tested on the way
  in and catches the removal of the subtraction, of `--no-index`, and of the staging
  error report. A fourth mutation caught a false claim rather than a defect, and the
  same-day entry above overstated it: a `core.bare=false` argument had been added to
  the staging call on a wrong diagnosis and recorded as one of two defects fixed.
  Removing it changed nothing, `--work-tree` overrides `core.bare`, and the real
  selection stages without it. The argument and the claim are both gone; the property
  it was guarding is now asserted by a test.
