# Scripts

**Last modified:** 2026-06-09

## Purpose
Contains the helper scripts for the journal. Currently one script: new-month.py, which creates the pre-filled entry file for the next (or a specified) month.

## Contents
- new-month.py — `journal/scripts/new-month.py` [[journal/scripts/CONTEXT]] — Creates the monthly journal file, pre-filled with a heading for every day of the month.

## Inputs
- Optional `--month YYYY-MM` argument to target a specific month (default: next calendar month).
- Optional `--dry-run` flag to preview without creating anything.
- Optional `--force` flag to override the date gate and create next month's file regardless of the current date.

## Outputs
- `journal/entries/YYYY-MM.md` — New monthly file with day headings pre-filled.
- Updated `journal/LOG.md` — Creation entry appended on each successful run.

## Steps
Run from anywhere using the Bash tool:

```
python journal/scripts/new-month.py [--month YYYY-MM] [--dry-run] [--force]
```

**Date gate:** When the target is the month immediately after the current month, the script checks whether today falls within the last 7 days of the current month. If not, it exits without creating anything. Pass `--force` to override.

## Dependencies
- `journal/entries/` [[journal/entries/CONTEXT]] — Target directory for new monthly files.
- `journal/LOG.md` — Appended on each successful run.

## Known Issues
- None.

## Revision History
- 2026-05-29 — Initial creation.
- 2026-06-09 — Added `--force` flag and date gate logic. Next month's file is now blocked by the script itself if today is not within the last 7 days of the current month.
