# Scripts

**Last modified:** 2026-06-03

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
