# Rule Hooks - Git Hooks

**Last modified:** 2026-08-04

## Purpose
The universal, AI-agnostic safety net: a git pre-commit hook that fires at commit time no matter who staged the change (any AI, or a manual commit). It runs two gates in order: a hard block on a commit that would put personal data into tracked files (rule B3, layer 2), then a warn-not-block doc-sync CONTEXT/LOG drift advisory that prints a loud block but lets the commit through (close-out is the deterministic gate for that). The advisory reports drift only: it keeps the guard's WARN findings and stays silent on any other severity. It is the sole preventive layer for AIs without a native hook (e.g. Aider) and a backstop under every other AI.

## Contents
- pre-commit - `workflows/rule-hooks/git-hooks/pre-commit` [[workflows/rule-hooks/git-hooks/CONTEXT]] - A small POSIX-sh shim that runs `python workflows/rule-hooks/scripts/run.py --precommit`. The detection logic lives in the workflow (reusing personal-data-guard); the shim is only the entry point.

## Inputs
- The staged/tracked files at commit time (discovered by personal-data-guard via git).

## Outputs
- Exit 0 (commit proceeds) or exit 1 with the block-and-explain message (commit aborted for personal data). A doc-sync drift advisory prints to stderr but keeps exit 0 (commit proceeds); a doc-sync finding at any other severity, including DEGRADED, prints nothing at all. The human override is `git commit --no-verify`.

## Steps
1. Activate once per clone: `git config core.hooksPath workflows/rule-hooks/git-hooks`.
2. On every `git commit`, the shim runs the personal-data gate; a FAIL aborts the commit, a guard crash warns and allows (so a guard bug never locks committing). It then runs the doc-sync drift advisory, which prints its WARN findings but never aborts, and prints nothing when the guard's only findings are at another severity.
3. `verify.py` reports whether the hook is active.

## Dependencies
- `workflows/rule-hooks/scripts/run.py` [[workflows/rule-hooks/scripts/CONTEXT]] - The `--precommit` gate the shim calls.
- `workflows/personal-data-guard/` [[workflows/personal-data-guard/CONTEXT]] - The personal-data detection source (the blocking gate).
- `workflows/doc-sync-guard/` [[workflows/doc-sync-guard/CONTEXT]] - The CONTEXT/LOG drift detection source (the warn-not-block advisory).
- `git` with `core.hooksPath` support.

## Known Issues
- **What the commit-time surface does not show you.** The doc-sync advisory this shim triggers reports drift only. A DEGRADED finding (a component of the guard that could not run, today its output-inventory probe on an interpreter without PyYAML) is filtered out in `_precommit_doc_sync` and never reaches stderr, so a commit on such a machine looks identical to a clean one. That is deliberate while nothing consumes the inventory answers, and the audit and close-out do report the degrade; it is recorded here because this is the surface a person actually watches at commit time. The filter's own documentation is in `workflows/rule-hooks/scripts/CONTEXT.md` [[workflows/rule-hooks/scripts/CONTEXT]] and the producer half in `workflows/doc-sync-guard/scripts/CONTEXT.md` [[workflows/doc-sync-guard/scripts/CONTEXT]].
- `core.hooksPath` redirects ALL git hooks to this directory and disables the default `.git/hooks/`. This directory is the single source; `.git/hooks/` held no active (non-sample) hooks when this was set up. Only `pre-commit` lives here today; add other shims here if more git hooks are ever needed.

## Revision History
- 2026-06-30 - Initial creation: the universal pre-commit personal-data gate shim.
- 2026-07-07 - The `--precommit` gate the shim calls now also runs a warn-not-block doc-sync CONTEXT/LOG drift advisory after the personal-data block (doc-sync-guard build part 4). The shim itself is unchanged (still `run.py --precommit`); this updates the prose that described the gate as personal-data only.
- 2026-08-04 - Recorded what this commit-time surface does not show: the doc-sync advisory keeps WARN findings only, so a DEGRADED finding is invisible at commit time and a degraded run looks clean here. Purpose, Outputs, Steps and Known Issues updated to match the filter the shim's gate applies; the shim itself is unchanged.
