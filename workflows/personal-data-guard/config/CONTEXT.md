# Config

**Last modified:** 2026-06-25

## Purpose
Holds the personal-data guard's optional denylist of personal nouns. The denylist lets the user catch project-specific personal terms (family members, a village or street name, a client or company name) that the automatic detectors do not cover. A tracked example file documents the format; the real denylist is gitignored because it holds personal data.

## Contents
- denylist.example.txt - `workflows/personal-data-guard/config/denylist.example.txt` [[workflows/personal-data-guard/config/CONTEXT]] - Tracked, generic format example: one term per line, `#` comments. Copy to `denylist.txt` and add your own terms.
- The active denylist is named `denylist.txt` in this directory. It is gitignored and machine-local (it holds personal data), so it is not present until the user creates it and is never tracked - the path is intentionally not listed as a Contents entry.

## Inputs
None. This directory is read by the guard script; it is not a workflow itself.

## Outputs
None directly. `denylist.txt`, when present, feeds the guard's WARN-grade noun matching.

## Steps
N/A - this is a configuration directory, not a workflow.

## Dependencies
- `workflows/personal-data-guard/scripts/run.py` [[workflows/personal-data-guard/scripts/CONTEXT]] - Reads `denylist.txt` at runtime if it exists.
- `.gitignore` (root) - Excludes `denylist.txt` from version control while keeping `denylist.example.txt` tracked.

## Known Issues
- The denylist is opt-in: with no `denylist.txt` present the guard simply skips noun matching. This is intentional, not a fault.

## Revision History
- 2026-06-25 - Initial creation. Tracked `denylist.example.txt`; real `denylist.txt` gitignored.
