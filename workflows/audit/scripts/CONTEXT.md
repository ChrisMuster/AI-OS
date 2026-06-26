# Scripts

**Last modified:** 2026-06-26

## Purpose
Contains the audit script for the audit workflow. run.py can walk the full project or validate only named context directories.

## Contents
- run.py - `workflows/audit/scripts/run.py` [[workflows/audit/scripts/CONTEXT]] - Main audit script. Checks structural compliance, validates Last modified and Revision History consistency, supports targeted context maintenance, ignores gitignored immediate child directories when checking for unlisted subdirectories, runs full-project code hygiene checks, and (in full mode) validates the structural knowledge graph via its `validate --json` CLI, merging the WARN/FAIL findings under a `knowledge-graph` label. The pure `graph_findings` merge helper and the graceful-skip `run_graph_validation` wrapper are unit-tested in `workflows/audit/tests/` [[workflows/audit/tests/CONTEXT]]. A parallel pair, `encoding_findings` and `run_encoding_check`, merges the encoding-guard `--check --json` WARN/FAIL findings under an `encoding` label, and a third pair, `personal_findings` and `run_personal_data_check`, merges the personal-data guard `--check --json` WARN/FAIL findings under a `personal-data` label. A fourth pair, `ai_style_findings` and `run_ai_style_check`, merges the ai-style guard `--check --json --base main` findings (WARN only; the guard's tier-2 INFO is dropped) under an `ai-style` label. The script reconfigures stdout/stderr to UTF-8 at startup so its report never mojibakes when piped on Windows. The full-tree walk (`collect_dirs`) is breadth-first and batches one `git check-ignore` call per depth level via `_git_check_ignored_batch`, with `_is_git_worktree` skipping git entirely outside a repository. In a full audit `run_audit` indexes that non-ignored result by parent and hands each `audit_directory` its immediate children, so the unlisted-subdirectory check reuses them instead of re-spawning git per directory; the per-call `_git_check_ignored` and `get_immediate_subdirs` remain the fallback for targeted `--context` mode.

## Inputs
No required inputs. Optional flags:
- `--save` — Saves the report to `workflows/audit/last-report.md` [[workflows/audit/last-report]].
- `--no-graph` — Skips the structural knowledge-graph validation in a full audit (no effect in `--context` mode).
- `--context <directory> [<directory> ...]` — Checks only the named project-relative directories or CONTEXT.md files.

## Outputs
- Audit report printed to stdout.
- Optionally: `workflows/audit/last-report.md` [[workflows/audit/last-report]] (when --save is passed).
- Updated `workflows/audit/LOG.md` — started and completed entries appended.
- Updated root `LOG.md` — completed entry appended.

## Steps
Run from anywhere:

```
python workflows/audit/scripts/run.py [--save]
python workflows/audit/scripts/run.py --context <directory> [<directory> ...]
```

## Dependencies
- All `CONTEXT.md` and `LOG.md` files in the project — the script reads these to perform its checks.
- `workflows/audit/LOG.md` — Appended on every run.
- `LOG.md` (root) — Appended on every run.
- `workflows/knowledge-graph/scripts/run.py` [[workflows/knowledge-graph/scripts/CONTEXT]] — A full audit shells out to its `validate --json --no-backrefs` command (structural graph only) and merges the WARN/FAIL findings. Subprocess, not import (both workflows ship a `common.py`/`parser.py`, so importing would risk a module-name collision); a missing or broken graph degrades to an INFO note.
- `workflows/encoding-guard/scripts/run.py` [[workflows/encoding-guard/scripts/CONTEXT]] — A full audit shells out to its `--check --json` command and merges the WARN/FAIL findings under an `encoding` label. Subprocess, not import; a missing or broken checker degrades to an INFO note.
- `workflows/personal-data-guard/scripts/run.py` [[workflows/personal-data-guard/scripts/CONTEXT]] - A full audit shells out to its `--check --json` command and merges the WARN/FAIL findings under a `personal-data` label. Subprocess, not import; a missing or broken guard degrades to an INFO note.
- `workflows/ai-style-guard/scripts/run.py` [[workflows/ai-style-guard/scripts/CONTEXT]] - A full audit shells out to its `--check --json --base main` command and merges the WARN findings under an `ai-style` label. Subprocess, not import; a missing or broken guard degrades to an INFO note.

## Known Issues
- Wiki-root CONTEXT.md files (LLM Wiki format) are detected by the presence of `## Folder structure` and have their standard section checks skipped. Any wiki that uses a different non-standard format may generate false warnings.
- The unlisted-subdirectory check only catches subdirectory names not mentioned anywhere in the Contents section text. Subdirectories mentioned in prose (rather than as backtick paths) will not be flagged.
- Contents path checking only covers paths that start with a known top-level directory name (workflows/, wikis/, skills/, templates/). Relative paths using other conventions are skipped silently.
- Directories named `raw/` or `data/` are treated as source-data boundaries: the directory itself is checked, but its contents are not walked. This prevents false failures from runtime-generated or imported data files (e.g. `session-search/data/archive/`).
- Stale-phrase and parent-relative path checks strip fenced code blocks and the Revision History section before scanning. Phrases inside inline code (single backticks) are still matched — this may produce occasional false positives if prose examples contain the target patterns. See STALE_PHRASES in run.py for the exact patterns checked.
- Metadata consistency is date-based. It catches mismatches between Last modified and visible Revision History metadata, but cannot prove that unchanged metadata accompanied an uncommitted prose-only edit; the immediate-maintenance rule and targeted command cover that workflow boundary.
- The unlisted-subdirectory check depends on git for ignored child-directory suppression. If git is unavailable, ignored child directories may be reported until the audit is re-run in a normal repository checkout.

## Revision History
Earlier history archived to LOG.md on 2026-06-26.
- 2026-06-12 — Added metadata consistency checks and targeted `--context` mode for lightweight post-task validation.
- 2026-06-23 — Added the knowledge-graph validation hook: the pure `graph_findings` merge helper and the `run_graph_validation` subprocess wrapper (graceful skip on any failure), called from `run_audit()` (full mode only) and gated by the new `--no-graph` flag. Imports `sys`/`json`. Targeted `--context` mode is unaffected.
- 2026-06-24 - Added the encoding-guard hook: the pure `encoding_findings` merge helper and the `run_encoding_check` subprocess wrapper (graceful skip on any failure), called from `run_audit()` in full mode and merged under an `encoding` label. Reconfigured stdout/stderr to UTF-8 at startup (fixes report mojibake when piped on Windows) and pinned `encoding="utf-8"` on the two `git check-ignore`/graph subprocess calls. Targeted `--context` mode is unaffected.
- 2026-06-25 - Added the personal-data-guard hook: the pure `personal_findings` merge helper and the `run_personal_data_check` subprocess wrapper (graceful skip on any failure), called from `run_audit()` in full mode and merged under a `personal-data` label. Targeted `--context` mode is unaffected.
- 2026-06-25 - Updated `get_immediate_subdirs()` so unlisted-subdirectory warnings ignore immediate child directories that are gitignored, matching the gitignored-content propagation rule.
- 2026-06-25 - Added the ai-style-guard hook: the pure `ai_style_findings` merge helper (WARN only) and the `run_ai_style_check` subprocess wrapper (graceful skip on any failure), called from `run_audit()` in full mode with `--base main` and merged under an `ai-style` label. Targeted `--context` mode is unaffected.
- 2026-06-26 - Rewrote `collect_dirs()` to a breadth-first, per-level batched `git check-ignore` walk (one subprocess per depth level instead of one per directory), and added `_is_git_worktree` (filesystem fast path that skips git entirely outside a repository) and `_git_check_ignored_batch` (NUL-separated `--stdin -z` raw-bytes call). The per-call `_git_check_ignored` is kept for `get_immediate_subdirs`. Directory set is byte-for-byte identical (verified against the previous os.walk output and the full audit report); `collect_dirs` wall-clock dropped from ~1050ms to ~190ms locally. Added `tests/test_collect_dirs.py` (8 tests).
- 2026-06-26 - Fed the unlisted-subdirectory check from `collect_dirs`' precomputed non-ignored set, eliminating the last per-directory `git check-ignore` spawn: `audit_directory` gained an optional `subdirs` parameter, and `run_audit` indexes the walk result by parent and passes each directory its immediate children. `get_immediate_subdirs` stays as the per-dir `--context` fallback. Full-audit `git check-ignore` spawns dropped from 26 to 4; audit report byte-for-byte identical. Extended `tests/test_audit_subdir_filter.py` (+3 tests).
