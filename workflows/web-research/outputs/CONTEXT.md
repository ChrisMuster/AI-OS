# Web Research Workflow — outputs/

**Last modified:** 2026-05-29

## Purpose
Stores the output files produced by the web-research workflow. Research packages (JSON) are saved here by run.py; reports (Markdown) are saved here by Biblio after writing from the research package.

## Contents
None. Files are generated at runtime and not tracked in version control.

## Inputs
None directly. Files arrive here when run.py runs and when Biblio writes a report.

## Outputs
- `research-[slug]-[date].json` — Research package: sources, tiers, corroboration metadata.
- `report-[slug]-[date].md` — Final report written by Biblio.

## Steps
N/A. This is an output directory, not a workflow itself.

## Dependencies
- `workflows/web-research/scripts/run.py` [[workflows/web-research/scripts/CONTEXT]] — Saves research packages here.

## Known Issues
- Output files accumulate over time and are not automatically cleaned up. Remove old packages and reports manually as needed.
- This directory is not version-controlled by default. Reports are local-only unless manually committed.

## Revision History
- 2026-05-29 — Initial creation.
