# Link Check

**Last modified:** 2026-06-09

## Purpose
Manages Obsidian wiki links in all Book Dragon CONTEXT.md files. Adds `[[links]]` alongside existing prose path references so the Obsidian knowledge graph shows connections between directories, and audits those links for dead targets after renames or deletions.

## Contents
- scripts/ — `workflows/link-check/scripts/` [[workflows/link-check/scripts/CONTEXT]] — The run.py script that handles link insertion, auditing, and auto-fix.

## Inputs
No inputs required. The script reads the existing project structure and CONTEXT.md files.

## Outputs
- Changes written in-place to CONTEXT.md files (--link and --fix modes).
- Report printed to stdout.
- Optionally: `workflows/link-check/last-report.md` — saved report from the most recent run (only created when --save is passed).

## Steps
1. **Add links (first run or after adding new directories):**
   `python workflows/link-check/scripts/run.py --link [--dry-run]`
   Scans all CONTEXT.md files and appends `[[path/CONTEXT]]` after backtick path references that do not already have a link.

2. **Audit for dead links:**
   `python workflows/link-check/scripts/run.py` (or `--audit`)
   Scans all CONTEXT.md files for `[[links]]` and reports any that point to files that no longer exist.

3. **Auto-fix dead links:**
   `python workflows/link-check/scripts/run.py --fix [--dry-run]`
   For each dead link, searches the project for the target filename. If exactly one match is found, rewrites the link. Ambiguous or missing targets are flagged for manual review.

4. Append LOG.md with a completion or failure entry.

## Dependencies
- `AGENTS.md` (root) [[AGENTS]] — Defines the CONTEXT.md schema and project structure that this workflow operates on.
- `workflows/link-check/scripts/run.py` [[workflows/link-check/scripts/CONTEXT]] — The script that performs all link operations.

## Known Issues
- Wiki-internal links (e.g. `[[page-name]]` inside wiki CONTEXT.md files) are skipped — the audit only checks links that start with a known top-level directory name or match a root file name.
- The --fix mode can only auto-resolve a dead link when exactly one file in the project matches the target filename. Renames that result in two files with the same name require manual resolution.
- last-report.md is not listed in Contents because it only exists after the first --save run.

## Revision History
- 2026-06-03 — Initial creation.
- 2026-06-09 — Dependencies updated from CLAUDE.md to AGENTS.md (AI-agnostic transition).
