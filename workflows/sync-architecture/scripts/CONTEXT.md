# Scripts

**Last modified:** 2026-08-25

## Purpose

Holds the sync architecture workflow's single entry point. `run.py` carries both
checks: the allowlist check, which extracts the fenced classification block from the
architecture plan and resolves its pathspecs against the real working tree, and the
consistency check, which scans both design documents for the named defect classes and
compares each review baseline against its live document. Read-only throughout: it runs
`git ls-files` and reads files, and writes nothing, so it needs neither `--dry-run` nor
idempotency handling. Standard library plus the git CLI only.

## Contents

- run.py - `workflows/sync-architecture/scripts/run.py` [[workflows/sync-architecture/scripts/CONTEXT]] - The entry point and the whole implementation. `extract_block` pulls the pathspecs out of the plan's fenced block so the document is the authority rather than a copy of it; `check_allowlist` resolves them with `git ls-files --others --ignored --exclude-standard`, then asserts positive controls (properties that must be true of the selection), category sweeps (kinds that must not appear), and the no-overlap rule against `git ls-files`; `check_consistency` applies the four documentary rules and the baseline hash comparison; `main` runs both by default and exits non-zero on any failure. `_git` returns `None` rather than raising when git is unavailable, so the caller degrades to SKIPPED.

## Inputs

- `SYNC-ARCHITECTURE-PLAN.md` and `SHADOW-REPOSITORY-PLAN.md` at the project root, plus their `CODEX-*` review baselines. All are gitignored design-time documents, so all may be absent.
- A working `git`, used read-only.

## Outputs

- A human report on stdout, grouped FAIL / SKIP / PASS / INFO, and an exit code. No files written.

## Steps

1. Parse arguments: `--check` (default), `--allowlist`, or `--consistency`.
2. For the allowlist check, read the plan and extract the fenced classification block; report FAIL if the block is missing or empty, since the check is meaningless without an authority to read.
3. Resolve the block's pathspecs against the tree and collect the selected set and the tracked set.
4. Assert each positive control, each category sweep, and the no-overlap rule.
5. For the consistency check, apply the four documentary rules to each design document and compare each baseline to its live document by SHA-256.
6. Print the grouped report and return 1 if any finding is FAIL, otherwise 0.
7. Append LOG.md with a completion or failure entry.

## Dependencies

- The parent workflow's `CONTEXT.md` [[workflows/sync-architecture/CONTEXT]] for what the checks are for and why they exist.
- `git`, used read-only.
- The fenced ` ```allowlist ` block in the architecture plan.

## Known Issues

- The superseded-spelling rule treats a mention of the old pattern as legitimate only when the corrected pattern appears on the same line, which is what makes a contrast sentence pass and a stale rule fail. A contrast split across two lines is therefore reported as a failure. That is deliberate: the false positive is cheap and visible, while the false negative is the defect the check exists to catch.
- Absence of a design document is reported as SKIPPED. A caller that treats SKIPPED as success gets no protection, which is why the summary line distinguishes "no failures" from "no failures, but N checks could not run".

## Revision History

- 2026-08-25 - Initial creation. Both checks existed first as throwaway scripts in a session scratchpad; giving them a tracked home is what makes the design documents' citation of them true. The allowlist assertions were rewritten from named files to structural properties on the way in, because a tracked file must not carry personal filenames and because a structural assertion keeps working when the underlying files change.
