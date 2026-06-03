# Audit

**Last modified:** 2026-06-03

## Purpose
Walks every directory in the Book Dragon project and checks for structural compliance: missing CONTEXT.md or LOG.md files, missing required sections, broken Contents paths, unlisted subdirectories, and dead Obsidian [[links]]. Produces a report of failures and warnings for review.

## Contents
- scripts/ — `workflows/audit/scripts/` [[workflows/audit/scripts/CONTEXT]] — Automation scripts for this workflow; run.py is the main audit entry point.

## Inputs
No inputs required. The script reads the existing project structure and CONTEXT.md files.

## Outputs
- Audit report printed to stdout.
- Optionally: `workflows/audit/last-report.md` [[workflows/audit/last-report]] — saved report from the most recent run (only created when --save is passed).

## Steps
1. Run the audit script from anywhere:
   `python workflows/audit/scripts/run.py [--save]`
2. Review the report. Failures (missing files) must be fixed. Warnings (missing sections, broken paths, unlisted dirs) should be investigated.
3. Fix any real issues found, then re-run to confirm clean.

## Dependencies
- `CLAUDE.md` [[CLAUDE]] (root) — Defines the structural rules (CONTEXT.md schema, LOG.md requirement) that this workflow audits against.
- `workflows/audit/scripts/run.py` [[workflows/audit/scripts/CONTEXT]] — The automation script that performs all checks.

## Known Issues
- Wiki-root directories use a non-standard CONTEXT.md format and are excluded from section checks. They will appear in the report as INFO entries.
- The audit is read-only — it reports issues but does not fix them. Fixing is a manual step.
- last-report.md is not listed in Contents because it only exists after the first --save run. If it exists, it is the saved output of the most recent audit.

## Revision History
- 2026-05-27 — Initial creation. Resolves the long-standing TODO in workflows/CONTEXT.md Known Issues.
- 2026-05-29 — scripts/run.py updated with two new warning checks: parent-relative path detection and stale build-phrase detection. Both skip fenced code blocks and Revision History sections to reduce false positives.
- 2026-06-03 — Added dead [[link]] check. Audit now warns on any project [[links]] that point to non-existent .md files. Wiki-internal links are ignored.
