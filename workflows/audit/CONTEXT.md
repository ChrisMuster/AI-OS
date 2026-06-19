# Audit

**Last modified:** 2026-06-17

## Purpose
Walks every directory in the Book Dragon project and checks for structural compliance: missing CONTEXT.md or LOG.md files, missing required sections, inconsistent Last modified and Revision History metadata, broken Contents paths, unlisted subdirectories, and dead Obsidian [[links]]. It can also run a lightweight targeted check against named directories immediately after context maintenance. The full audit checks that AGENTS.md has not exceeded its line-count threshold (600 lines) and runs code hygiene checks across all Python scripts.

## Contents
- scripts/ — `workflows/audit/scripts/` [[workflows/audit/scripts/CONTEXT]] — Automation scripts for this workflow; run.py is the main audit entry point.

## Inputs
No inputs are required for a full audit. Targeted mode accepts one or more project-relative directories or CONTEXT.md paths after `--context`.

## Outputs
- Audit report printed to stdout.
- Optionally: `workflows/audit/last-report.md` [[workflows/audit/last-report]] — saved report from the most recent run (only created when --save is passed).

## Steps
1. Run the audit script from anywhere:
   `python workflows/audit/scripts/run.py [--save]`
2. After changing CONTEXT.md files during normal work, run:
   `python workflows/audit/scripts/run.py --context <directory> [<directory> ...]`
3. Review the report. Failures must be fixed; warnings should be investigated.
4. Fix any real issues found, then re-run the same mode to confirm clean.

## Dependencies
- `AGENTS.md` [[AGENTS]] (root) — Defines the structural rules (CONTEXT.md schema, LOG.md requirement) that this workflow audits against.
- `workflows/audit/scripts/run.py` [[workflows/audit/scripts/CONTEXT]] — The automation script that performs all checks.

## Known Issues
- The audit is read-only — it reports issues but does not fix them. Fixing is a manual step.
- last-report.md is not listed in Contents because it only exists after the first --save run. If it exists, it is the saved output of the most recent audit.
- Requires git to be available for gitignore-aware directory pruning. Falls back to auditing all directories if git is not found.

## Revision History
- 2026-05-27 — Initial creation. Resolves the long-standing TODO in workflows/CONTEXT.md Known Issues.
- 2026-05-29 — scripts/run.py updated with two new warning checks: parent-relative path detection and stale build-phrase detection. Both skip fenced code blocks and Revision History sections to reduce false positives.
- 2026-06-03 — Added dead [[link]] check. Audit now warns on any project [[links]] that point to non-existent .md files. Wiki-internal links are ignored.
- 2026-06-05 — Added CLAUDE.md line-count check. Warns when CLAUDE.md exceeds 600 lines, prompting a review and reorganisation into a rules/ directory.
- 2026-06-08 — Added code hygiene check: scans all project Python scripts for strftime calls with time components but no timezone offset. Fixed format_report to use isoformat (was itself a timezone-less timestamp).
- 2026-06-09 — Line-count check updated from CLAUDE.md to AGENTS.md. Dependencies updated. AGENTS added to dead-link root stems.
- 2026-06-09 — GEMINI added to dead-link root stems (Phase 2 AI-agnostic transition).
- 2026-06-12 — Added deterministic Last modified and Revision History checks plus targeted `--context` mode for immediate context maintenance.
- 2026-06-17 — Replaced `rglob` tree walk with `os.walk` and `git check-ignore` pruning. Gitignored directories (collections, state, wiki content) are now skipped entirely — eliminates false positives and avoids traversing large data directories.
