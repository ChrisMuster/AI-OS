# Sync Architecture

**Last modified:** 2026-08-25

## Purpose

Read-only verification for the sync architecture design documents and for the file
classification they define. It exists because that classification is the thing the
build acts on: it decides which files a second, private git repository will track
permanently, and a mistake in it is discovered only after a permanent commit.

The classification used to live as prose in two documents in two notations, and a
decision taken in one of them failed to reach the patterns in the other. This workflow
replaces "the documents agree" as an assertion with a check that can fail.

## Contents

- `scripts/` - `workflows/sync-architecture/scripts/` [[workflows/sync-architecture/scripts/CONTEXT]] - Holds the workflow's single entry point and its documentation.
- `scripts/run.py` - the entry point. Runs two read-only checks, together by default.
  `--allowlist` extracts the executable classification block from the architecture
  plan and proves what it actually selects against the real working tree.
  `--consistency` checks both design documents for the defect classes that have
  recurred: a superseded pathspec spelling left standing, a second copy of the
  classification, an instruction to run a measuring script that is not in the tree,
  and a stale review baseline.

## Inputs

- The architecture plan and its Stage A build spec at the project root. Both are
  design-time documents and therefore gitignored, so on a fresh clone they are absent.
  Their absence is reported as SKIPPED rather than as a pass or a failure.
- The architecture plan must carry a fenced ` ```allowlist ` block. That block is the
  authority for the classification, and the check reads it from the document rather
  than from a copy, so the two cannot drift apart.
- A working `git`, used read-only.

## Outputs

None on disk. A report on stdout and an exit code: non-zero if any check failed.

## Steps

1. Read the architecture plan and extract the fenced classification block.
2. Resolve that block's pathspecs against the real tree with
   `git ls-files --others --ignored --exclude-standard`, which returns untracked files
   only, so a publicly tracked file cannot appear in the result.
3. Assert the positive controls: properties that must be TRUE of the selection, such
   as an authored personal folder contributing a non-markdown file and a file from a
   subfolder. A suite of exclusions alone would pass on an empty allowlist.
4. Assert the category sweeps: kinds of file that must not appear at all.
5. Assert that no publicly tracked file was selected.
6. Check both design documents for the recurring defect classes.
7. Compare each review baseline against its live document by SHA-256.
8. Print the report and exit non-zero on any failure.
9. Append LOG.md with a completion or failure entry.

## Dependencies

- The two design documents at the project root, which are gitignored. This workflow
  degrades to SKIPPED without them rather than failing.
- `git`, used read-only.
- The fenced ` ```allowlist ` block in the architecture plan. Removing or renaming that
  block breaks the allowlist check, which is intended: the check is meaningless without
  an authority to read.

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
