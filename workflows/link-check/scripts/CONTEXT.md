# Scripts

**Last modified:** 2026-08-04

## Purpose
Contains the run.py script for the link-check workflow. Handles three operations: inserting Obsidian wiki links into CONTEXT.md files (--link), auditing those links for dead targets (--audit), and auto-fixing dead links where possible (--fix).

## Contents
- run.py — `workflows/link-check/scripts/run.py` [[workflows/link-check/scripts/CONTEXT]] — Main script. Three modes: --link inserts links, --audit reports dead links, --fix auto-resolves dead links.

## Inputs
No required inputs. Optional flags:
- `--link` — Insert [[links]] mode.
- `--audit` — Dead-link audit mode (default).
- `--fix` — Audit plus auto-fix mode.
- `--dry-run` — Preview without writing.
- `--save` — Save report to `workflows/link-check/last-report.md`.

## Outputs
- Modified CONTEXT.md files (--link and --fix modes, unless --dry-run).
- Report printed to stdout.
- Optionally: `workflows/link-check/last-report.md` (when --save is passed).
- Updated `workflows/link-check/LOG.md` — started and completed entries appended.
- Updated root `LOG.md` — completed entry appended.

## Steps
Run from anywhere:

```
python workflows/link-check/scripts/run.py [--link|--audit|--fix] [--dry-run] [--save]
```

## Dependencies
- All `CONTEXT.md` files in the project — read by all modes.
- `workflows/link-check/LOG.md` — appended on every run.
- `LOG.md` (root) [[LOG]] — appended on every run.

## Known Issues
- None.

## Revision History
- 2026-06-03 — Initial creation.
- 2026-08-04 - Line endings pinned on all four text writes in `run.py` (the LOG.md append, the two CONTEXT.md rewrites in the `--link` and retarget paths, and the `--save` report), which now pass `newline="\n"` explicitly. This directory rewrites other directories' CONTEXT.md files wholesale, so a translated newline here converted a whole file to CRLF on every link pass. Part of the project-wide pass closing this defect class at all 48 write sites.
