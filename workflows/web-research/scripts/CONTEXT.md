# Web Research Workflow — scripts/

**Last modified:** 2026-05-29

## Purpose
CLI entry point for the web-research workflow. `run.py` is the only script here — it parses command-line flags, calls the shared skill, saves the research package, and prints the report brief for Biblio.

## Contents
- run.py — `workflows/web-research/scripts/run.py` [[workflows/web-research/scripts/CONTEXT]] — Main CLI entry point for the workflow.

## Inputs
None directly. run.py receives its inputs as CLI flags.

## Outputs
None directly. run.py saves the research package to `workflows/web-research/outputs/` [[workflows/web-research/outputs/CONTEXT]] and appends to `workflows/web-research/LOG.md`.

## Steps
N/A. This is a scripts container, not a workflow itself.

## Dependencies
- `skills/web-research/scripts/research.py` [[skills/web-research/scripts/CONTEXT]] — Core research engine called by run.py.

## Known Issues
- None.

## Revision History
- 2026-05-29 — Initial creation. run.py added.
- 2026-05-29 — run.py updated: added --image-prompt and --platform flags; image prompt brief section appended to Biblio report brief when flag is present.
