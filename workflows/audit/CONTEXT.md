# Audit

**Last modified:** 2026-07-06

## Purpose
Walks every directory in the Book Dragon project and checks for structural compliance: missing CONTEXT.md or LOG.md files, missing required sections, inconsistent Last modified and Revision History metadata, broken Contents paths, unlisted non-gitignored subdirectories, and dead Obsidian [[links]]. It can also run a lightweight targeted check against named directories immediately after context maintenance. The full audit checks that AGENTS.md has not exceeded its line-count threshold (600 lines), runs code hygiene checks across all Python scripts, and validates the structural knowledge graph, merging its actionable (WARN/FAIL) findings under a `knowledge-graph` label so a graph regression surfaces in the same report. The graph step is additive and advisory (it never changes the audit's exit code); if it cannot be validated it reports a DEGRADED finding rather than silently passing. When any of the advisory hooks (graph, encoding, personal-data, ai-style) cannot run at all, it is reported as DEGRADED - a check that did not run, counted and shown in its own report section, never mistaken for a pass, and carrying the remediation to apply (run setup.py). A full audit also runs the encoding-guard check and merges its WARN/FAIL findings under an `encoding` label, so an encoding regression (a file that stops being valid UTF-8, new mojibake, or a text-mode subprocess call with no explicit encoding) surfaces in the same report; this step is additive and advisory in the same way. It likewise runs the personal-data guard and merges its WARN/FAIL findings under a `personal-data` label, so a personal-data leak in a committable file (an email, a personal home path, the user's name/username, or a denylisted noun) surfaces in the same report - again additive and advisory. It also runs the ai-style guard scoped to the branch (`--base main`) and merges its WARN findings under an `ai-style` label, so an AI writing tell introduced on the branch (an em dash, a smart quote, a stock phrase) surfaces in the same report - again additive and advisory. Finally it runs the doc-sync guard at working-tree scope (its default HEAD, not `--base main`) and merges its WARN findings under a `doc-sync` label, so CONTEXT.md / LOG.md drift in the current uncommitted work (a directory changed without its CONTEXT.md or LOG.md moving) surfaces in the same report - advisory here, and turned into a hard fail at close-out via the label.

## Contents
- scripts/ - `workflows/audit/scripts/` [[workflows/audit/scripts/CONTEXT]] - Automation scripts for this workflow; run.py is the main audit entry point.
- tests/ - `workflows/audit/tests/` [[workflows/audit/tests/CONTEXT]] - Unit tests for the knowledge-graph, encoding-guard, personal-data-guard, and ai-style-guard audit-hook merge helpers and their graceful-degradation paths, the subdirectory filter, the breadth-first `collect_dirs` walk, and a one-command runner.
- archived/ - `workflows/audit/archived/` [[workflows/audit/archived/CONTEXT]] - Design-time plan and handover documents kept for history after close-out (personal, gitignored; individual files not listed).

## Inputs
No inputs are required for a full audit. Targeted mode accepts one or more project-relative directories or CONTEXT.md paths after `--context`.

## Outputs
- Audit report printed to stdout.
- Optionally: `workflows/audit/last-report.md` [[workflows/audit/last-report]] — saved report from the most recent run (only created when --save is passed).

## Steps
1. Run the audit script from anywhere (a full run also validates the structural knowledge graph):
   `python workflows/audit/scripts/run.py [--save] [--no-graph]`
2. After changing CONTEXT.md files during normal work, run:
   `python workflows/audit/scripts/run.py --context <directory> [<directory> ...]`
   (Targeted mode is fast and graph-free — it never validates the graph.)
3. Review the report. Failures must be fixed; warnings should be investigated. Graph findings appear under the `knowledge-graph` label.
4. Fix any real issues found, then re-run the same mode to confirm clean.

## Dependencies
- `AGENTS.md` [[AGENTS]] (root) — Defines the structural rules (CONTEXT.md schema, LOG.md requirement) that this workflow audits against.
- `workflows/audit/scripts/run.py` [[workflows/audit/scripts/CONTEXT]] — The automation script that performs all checks.
- `workflows/knowledge-graph/scripts/run.py` [[workflows/knowledge-graph/scripts/CONTEXT]] - A full audit shells out to its `validate --json` command (structural graph only) and merges the WARN/FAIL findings. Decoupled via subprocess, not import; a missing or broken graph reports a DEGRADED finding on failure.
- `workflows/encoding-guard/scripts/run.py` [[workflows/encoding-guard/scripts/CONTEXT]] - A full audit shells out to its `--check --json` command and merges the WARN/FAIL findings under an `encoding` label. Decoupled via subprocess, not import; a missing or broken checker reports a DEGRADED finding on failure.
- `workflows/personal-data-guard/scripts/run.py` [[workflows/personal-data-guard/scripts/CONTEXT]] - A full audit shells out to its `--check --json` command and merges the WARN/FAIL findings under a `personal-data` label. Decoupled via subprocess, not import; a missing or broken guard reports a DEGRADED finding on failure.
- `workflows/ai-style-guard/scripts/run.py` [[workflows/ai-style-guard/scripts/CONTEXT]] - A full audit shells out to its `--check --json --base main` command and merges the WARN findings under an `ai-style` label. Decoupled via subprocess, not import; a missing or broken guard reports a DEGRADED finding on failure.
- `workflows/doc-sync-guard/scripts/run.py` [[workflows/doc-sync-guard/scripts/CONTEXT]] - A full audit shells out to its `--check --json` command (default working-tree scope) and merges the WARN findings under a `doc-sync` label. Decoupled via subprocess, not import; a missing or broken guard reports a DEGRADED finding on failure.

## Known Issues
- The audit is read-only — it reports issues but does not fix them. Fixing is a manual step.
- last-report.md is not listed in Contents because it only exists after the first --save run. If it exists, it is the saved output of the most recent audit.
- Requires git to be available for gitignore-aware directory pruning and for suppressing unlisted-subdirectory warnings on ignored child directories. Falls back to auditing all directories if git is not found.
- The knowledge-graph validation step is best-effort: if the knowledge-graph CLI is absent, crashes, or returns unparseable output, the audit reports a DEGRADED finding ("knowledge-graph check did not run - ...") carrying the setup.py fix and still completes with exit 0 (DEGRADED is advisory and non-blocking, but it is counted and shown in its own report section so it is never mistaken for a pass). It validates the structural graph only (never `--layer`), so merged findings carry no personal/gitignored names.

## Revision History
Earlier history archived to LOG.md on 2026-07-06.
- 2026-06-24 - Encoding-guard hook: a full audit now also runs the encoding-guard `--check --json` and merges its WARN/FAIL findings under an `encoding` label (subprocess, not import; degrades to an INFO note on failure). Added a UTF-8 stdout/stderr reconfigure so the report no longer mojibakes when piped on Windows, and pinned `encoding="utf-8"` on the script's subprocess calls.
- 2026-06-25 - Personal-data-guard hook: a full audit now also runs the personal-data guard `--check --json` and merges its WARN/FAIL findings under a `personal-data` label (subprocess, not import; degrades to an INFO note on failure). Added test_audit_personal_data_hook.py for the merge helper and graceful-skip paths.
- 2026-06-25 - The unlisted-subdirectory check now ignores gitignored immediate child directories, matching the project rule that gitignored personal or generated content does not propagate into tracked Contents sections.
- 2026-06-25 - Ai-style-guard hook: a full audit now also runs the ai-style guard (`--check --json --base main`) and merges its WARN findings under an `ai-style` label (subprocess, not import; degrades to an INFO note on failure). Added test_audit_ai_style_hook.py for the merge helper and graceful-skip paths.
- 2026-06-26 - Speed: `collect_dirs` rewritten to a breadth-first walk that batches one `git check-ignore` call per depth level (was one subprocess per directory), with a filesystem fast path that skips git entirely outside a worktree. Directory set and audit output are byte-for-byte identical; the walk is roughly 5x faster locally. Added tests/test_collect_dirs.py (8 tests).
- 2026-06-26 - Added archived/ subdirectory for the workflow's design-time plan documents (gitignored); moved AUDIT-SPEED-PLAN.md from the project root into it during close-out.
- 2026-07-04 - Advisory-hook degrades are now first-class DEGRADED findings (was a silently-dropped INFO): a check that could not run is counted, shown in its own report section, carries a remediation (run setup.py), and can never be mistaken for a pass. Added the `degraded()` helper and a `## Degraded (did not run)` report section; added tests/test_audit_degraded.py.
- 2026-07-06 - Doc-sync-guard hook: a full audit now also runs the doc-sync guard (`--check --json`, default working-tree scope) and merges its WARN findings under a `doc-sync` label (subprocess, not import; degrades to a DEGRADED finding on failure), so CONTEXT.md / LOG.md drift surfaces in the same report. Advisory here; close-out turns doc-sync findings into a hard fail via the label. Added tests/test_audit_doc_sync_hook.py.
