# Scripts

**Last modified:** 2026-08-26

## Purpose

Holds the sync architecture workflow's entry point and the selection rule it checks.
`run.py` carries both checks: the allowlist check, which extracts the fenced
classification block from the architecture plan and proves what it selects against the
real working tree, and the consistency check, which scans both design documents for the
named defect classes and compares each review baseline against its live document.
`allowlist.py` holds the selection itself, in one copy, because `run.py` and the Stage A
build both need it and a check that resolves the pathspecs differently from the build is
not checking the build.

`run.py` is read-only and needs neither `--dry-run` nor idempotency handling.
`allowlist.py` is read-only except under `--stage`, which force-adds the selection into
the personal repository's index; that path takes `--dry-run` and is idempotent, since
re-adding an already-tracked file is a no-op. Standard library plus the git CLI only,
with no shell pipeline, so both run wherever Python does.

## Contents

- run.py - `workflows/sync-architecture/scripts/run.py` [[workflows/sync-architecture/scripts/CONTEXT]] - The entry point. `check_allowlist` resolves the classification through `allowlist.select`, then asserts positive controls (properties that must be true of the selection), category sweeps (kinds that must not appear), the invariant that no file is both publicly tracked and ignored, and the no-overlap rule measured on the uncorrected selection where it can actually fail; `check_consistency` applies the four documentary rules and the baseline hash comparison; `main` runs both by default and exits non-zero on any failure. It re-exports `extract_block` from `allowlist` rather than carrying its own parser, so there is one copy. A `None` from the selection means git could not answer, and the caller degrades to SKIPPED rather than treating an unanswered question as a pass.
- allowlist.py - `workflows/sync-architecture/scripts/allowlist.py` [[workflows/sync-architecture/scripts/CONTEXT]] - The single executable copy of the selection rule, imported by `run.py` and called by the Stage A build. `extract_block` and `read_specs` pull the pathspecs out of the plan's fenced block so the document stays the authority. `shadow_selection` resolves them in the personal repository's git context, defaulting to a throwaway empty repository, which is the state the real one is in immediately before its seed commit. `public_tracked` and `tracked_and_ignored` measure the public repository; the latter passes `--no-index`, without which git refuses to report a tracked path as ignored and the check could never produce the finding it exists for. `select` returns the raw selection, the overlap, and the corrected set. `stage` force-adds it in length-bounded batches, sized for the Windows command-line limit, and surfaces git's own stderr on failure rather than returning a bare `False`. `self_test` builds a tree containing a tracked-and-ignored file and asserts the two contexts disagree about it.

## Inputs

- `SYNC-ARCHITECTURE-PLAN.md` and `SHADOW-REPOSITORY-PLAN.md` at the project root, plus their `CODEX-*` review baselines. All are gitignored design-time documents, so all may be absent.
- A working `git`. Read-only for `run.py` and for `allowlist.py` in every mode except `--stage`.
- For `allowlist.py --stage`, the path to the personal repository's git directory.

## Outputs

- A human report on stdout, grouped FAIL / SKIP / PASS / INFO, and an exit code. `run.py` writes no files.
- `allowlist.py --stage` writes to the personal repository's index only: never the working tree, never the public repository. `--dry-run` prints the same selection and writes nothing.

## Steps

1. Parse arguments: `--check` (default), `--allowlist`, or `--consistency`.
2. For the allowlist check, read the plan and extract the fenced classification block; report FAIL if the block is missing or empty, since the check is meaningless without an authority to read.
3. Resolve the block's pathspecs through `allowlist.select`, in the personal repository's git context, and subtract the public repository's tracked set.
4. Assert each positive control and each category sweep against the corrected selection.
5. Assert the invariant that no file is both publicly tracked and matched by an ignore rule, then the no-overlap rule against the uncorrected selection, then that the subtraction removed them.
6. For the consistency check, apply the four documentary rules to each design document and compare each baseline to its live document by SHA-256.
7. Print the grouped report and return 1 if any finding is FAIL, otherwise 0.
8. Append LOG.md with a completion or failure entry.

## Dependencies

- The parent workflow's `CONTEXT.md` [[workflows/sync-architecture/CONTEXT]] for what the checks are for and why they exist.
- `workflows/sync-architecture/tests/` [[workflows/sync-architecture/tests/CONTEXT]], which imports `allowlist.py` by file path. Renaming or moving the module breaks that import rather than degrading, and it is also what the Stage A build calls by name.
- `git`, used read-only.
- The fenced ` ```allowlist ` block in the architecture plan.

## Known Issues

- The superseded-spelling rule treats a mention of the old pattern as legitimate only when the corrected pattern appears on the same line, which is what makes a contrast sentence pass and a stale rule fail. A contrast split across two lines is therefore reported as a failure. That is deliberate: the false positive is cheap and visible, while the false negative is the defect the check exists to catch.
- Absence of a design document is reported as SKIPPED. A caller that treats SKIPPED as success gets no protection, which is why the summary line distinguishes "no failures" from "no failures, but N checks could not run".
- `run.py` imports `allowlist` by inserting its own directory on `sys.path`, so the two files must stay in the same directory. Moving one without the other breaks the import rather than degrading.
- The "corrected selection excludes tracked files" assertion is a regression guard on the subtraction step, not a property of the tree: it can only fail if that step is weakened or removed. It is labelled as such in the source so a future reader does not mistake it for evidence about the working tree.

## Revision History

- 2026-08-25 - Initial creation. Both checks existed first as throwaway scripts in a session scratchpad; giving them a tracked home is what makes the design documents' citation of them true. The allowlist assertions were rewritten from named files to structural properties on the way in, because a tracked file must not carry personal filenames and because a structural assertion keeps working when the underlying files change.
- 2026-08-26 - Added `allowlist.py` and rebuilt `run.py`'s allowlist check on top of it. The check had been resolving the classification pathspecs in public-repository context while the build resolves them with `--git-dir` and `--work-tree`, where "untracked" means untracked by the personal repository; the no-overlap assertion therefore could not fail, because public context strips every tracked file before the assertion looks for one. The selection, the subtraction that actually prevents the overlap, and the invariant beneath it now live in one module the check imports and the build calls. One defect was found and fixed while testing it: the staging helper discarded git's stderr, so a refused force-add printed nothing and read as a successful no-op.
- 2026-08-26 - Removed a `core.bare=false` argument from `stage` and the claim that went with it. A staging run had failed, the failure was attributed to the probe repository being bare, and the argument was added as the fix; the same entry recorded it as a defect found and fixed. Mutation-testing the new suite exposed the claim, because removing the argument changed nothing. Re-tested directly: `--work-tree` overrides `core.bare`, a bare probe accepts the force-add, and the real selection stages all 555 files without it. The original failure is not reproducible and was almost certainly the discarded-stderr defect hiding its own cause, which is the one real fix of the two. The property the verifier depends on is now asserted by a test rather than worked around.
