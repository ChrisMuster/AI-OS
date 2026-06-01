# Web Research Workflow — config/

**Last modified:** 2026-05-29

## Purpose
Workflow-level configuration for web-research. `sources.yaml` defines the default source priority order and per-source settings for CLI runs. This is separate from the skill-level config (which holds RSS feeds) so workflow defaults can be tuned without touching the shared skill.

## Contents
- sources.yaml — `workflows/web-research/config/sources.yaml` — Default source priority order and enabled/disabled settings; overridden per-run with --include and --exclude flags.

## Inputs
None. Static config files read by run.py.

## Outputs
None.

## Steps
N/A. This is a config container, not a workflow itself.

## Dependencies
- `skills/web-research/scripts/research.py` — The SOURCE_REGISTRY in research.py must contain all source names listed in sources.yaml.

## Known Issues
- sources.yaml is not yet wired into run.py — currently the defaults are hardcoded in research.py's SOURCE_REGISTRY. Future improvement: run.py reads sources.yaml to set default priority and enabled state.

## Revision History
- 2026-05-29 — Initial creation. sources.yaml added with 9 sources configured.
