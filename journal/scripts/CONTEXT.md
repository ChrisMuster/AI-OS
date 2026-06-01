# Scripts

**Last modified:** 2026-05-29

## Purpose
Contains the helper scripts for the journal. Currently one script: new-month.py, which creates the pre-filled entry file for the next (or a specified) month.

## Contents
- new-month.py — `journal/scripts/new-month.py` — Creates the monthly journal file, pre-filled with a heading for every day of the month.

## Inputs
- Optional `--month YYYY-MM` argument to target a specific month (default: next calendar month).
- Optional `--dry-run` flag to preview without creating anything.

## Outputs
- `journal/entries/YYYY-MM.md` — New monthly file with day headings pre-filled.
- Updated `journal/LOG.md` — Creation entry appended on each successful run.

## Steps
Run from anywhere:

```
python journal/scripts/new-month.py [--month YYYY-MM] [--dry-run]
```

## Dependencies
- `journal/entries/` — Target directory for new monthly files.
- `journal/LOG.md` — Appended on each successful run.

## Known Issues
- None.

## Revision History
- 2026-05-29 — Initial creation.
