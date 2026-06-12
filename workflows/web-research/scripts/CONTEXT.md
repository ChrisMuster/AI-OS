# Web Research Workflow — scripts/

**Last modified:** 2026-06-12

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
- `workflows/biblio-tools/scripts/runtime.py` [[workflows/biblio-tools/scripts/CONTEXT]] — Re-runs the entry point inside the canonical project `.venv`.

## Known Issues
- None.

## Revision History
- 2026-05-29 — Initial creation. run.py added.
- 2026-05-29 — run.py updated: added --image-prompt and --platform flags; image prompt brief section appended to Biblio report brief when flag is present.
- 2026-06-05 — run.py updated: added --check flag for static pre-flight check (Python version, required packages, .env presence, API key status). --topic changed from required to optional when --check is used.
- 2026-06-06 — run.py updated: paywalled content sections added to results summary and Biblio brief; --check extended to show SUBSCRIBED_DOMAINS configuration.
- 2026-06-11 — run.py now enters the canonical project runtime automatically before loading research dependencies.
- 2026-06-11 — Replaced the retired `ran` log action with required started/completed/failed workflow logging.
- 2026-06-12 — Made `--check` inspect the canonical project runtime when available while retaining host-Python diagnostics before initial setup.
