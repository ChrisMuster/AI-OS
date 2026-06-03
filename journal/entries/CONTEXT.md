# Entries

**Last modified:** 2026-05-29

## Purpose
Holds the monthly journal files. One markdown file per month, named YYYY-MM.md. Each file is pre-filled with a heading for every day of the month by the new-month.py script.

## Contents
None. Monthly files are data, not notable directories — they do not need individual listing here. The current file is whichever month is active.

## Inputs
- Monthly files created by `journal/scripts/new-month.py` [[journal/scripts/CONTEXT]].
- Daily notes written directly by the user into the relevant file.

## Outputs
Monthly markdown files read by Biblio during pattern review sessions.

## Steps
N/A. This is a storage directory. See the parent journal/CONTEXT.md for the full usage flow.

## Dependencies
- `journal/scripts/new-month.py` [[journal/scripts/CONTEXT]] — Creates new monthly files.
- `journal/CONTEXT.md` [[journal/CONTEXT]] — Defines the overall journal usage pattern.

## Known Issues
- This CONTEXT.md Contents section intentionally says "None" — monthly files are data files, not notable entries requiring individual documentation. This is an accepted exception to the verification checklist rule.

## Revision History
- 2026-05-29 — Initial creation.
