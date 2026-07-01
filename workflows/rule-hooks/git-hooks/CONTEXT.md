# Rule Hooks - Git Hooks

**Last modified:** 2026-06-30

## Purpose
The universal, AI-agnostic safety net: a git pre-commit hook that fires at commit time no matter who staged the change (any AI, or a manual commit), blocking a commit that would put personal data into tracked files (rule B3, layer 2). It is the sole preventive layer for AIs without a native hook (e.g. Aider) and a backstop under every other AI.

## Contents
- pre-commit - `workflows/rule-hooks/git-hooks/pre-commit` [[workflows/rule-hooks/git-hooks/CONTEXT]] - A small POSIX-sh shim that runs `python workflows/rule-hooks/scripts/run.py --precommit`. The detection logic lives in the workflow (reusing personal-data-guard); the shim is only the entry point.

## Inputs
- The staged/tracked files at commit time (discovered by personal-data-guard via git).

## Outputs
- Exit 0 (commit proceeds) or exit 1 with the block-and-explain message (commit aborted). The human override is `git commit --no-verify`.

## Steps
1. Activate once per clone: `git config core.hooksPath workflows/rule-hooks/git-hooks`.
2. On every `git commit`, the shim runs the personal-data gate; a FAIL aborts the commit, a guard crash warns and allows (so a guard bug never locks committing).
3. `verify.py` reports whether the hook is active.

## Dependencies
- `workflows/rule-hooks/scripts/run.py` [[workflows/rule-hooks/scripts/CONTEXT]] - The `--precommit` gate the shim calls.
- `workflows/personal-data-guard/` [[workflows/personal-data-guard/CONTEXT]] - The detection source.
- `git` with `core.hooksPath` support.

## Known Issues
- `core.hooksPath` redirects ALL git hooks to this directory and disables the default `.git/hooks/`. This directory is the single source; `.git/hooks/` held no active (non-sample) hooks when this was set up. Only `pre-commit` lives here today; add other shims here if more git hooks are ever needed.

## Revision History
- 2026-06-30 - Initial creation: the universal pre-commit personal-data gate shim.
