# Web Research — scripts/

**Last modified:** 2026-05-29

## Purpose
Python scripts that implement the web research skill. `research.py` is the main importable entry point; `compile.py` assembles raw results into a research package; the `sources/` subdirectory contains one adapter per data source.

## Contents
- research.py — `skills/web-research/scripts/research.py` — Main importable function: `from research import research`.
- compile.py — `skills/web-research/scripts/compile.py` — Deduplication, tier assignment, corroboration scoring, and package assembly.
- exceptions.py — `skills/web-research/scripts/exceptions.py` — Typed exception classes (QuotaExceededError, AuthError, SourceUnavailableError) and the shared check_response() helper used by all source adapters.
- requirements.txt — `skills/web-research/scripts/requirements.txt` — Python dependencies for the skill.
- sources/ — `skills/web-research/scripts/sources/` — Source adapter modules; one per data source.

## Inputs
None directly. Called via `research.py`.

## Outputs
None directly. `research.py` returns a research package dict.

## Steps
N/A. This is a scripts container, not a workflow itself.

## Dependencies
- `skills/web-research/config/rss_feeds.yaml` — Used by `sources/rss_source.py`.
- Python packages listed in `requirements.txt`.

## Known Issues
- None.

## Revision History
- 2026-05-29 — Initial creation.
- 2026-05-29 — Step 5: exceptions.py added. All source adapters updated to raise typed exceptions. research.py now tracks source_status and quality_flags per run.
