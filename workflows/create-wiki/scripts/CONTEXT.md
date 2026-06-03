# Scripts

**Last modified:** 2026-05-27

## Purpose
Contains the automation script for the create-wiki workflow. Handles all deterministic file creation when scaffolding a new wiki — the 90% that does not need an AI.

## Contents
- run.py — `workflows/create-wiki/scripts/run.py` [[workflows/create-wiki/scripts/CONTEXT]] — Main scaffold script. Creates all wiki directories and files, updates wikis/CONTEXT.md and README.md, and appends to LOG files.

## Inputs
- `wiki_name` (command-line argument) — Lowercase with hyphens, e.g. `react-patterns`.
- `wiki_topic` (command-line argument) — One-line plain-language description of what the wiki covers.

## Outputs
- `wikis/<wiki-name>/` — Fully scaffolded wiki directory containing:
  - `CONTEXT.md` — Generated from wiki-context.md.template with topic filled in.
  - `LOG.md` — Wiki root log, seeded with creation entry.
  - `raw/` — Empty source document store with CONTEXT.md and LOG.md.
  - `wiki/` — Empty wiki pages store with CONTEXT.md, LOG.md, index.md, and operations-log.md.
- Updated `wikis/CONTEXT.md` [[wikis/CONTEXT]] — New wiki entry added to Contents section.
- Updated `README.md` [[README]] — New wiki entry added to Wikis section, Last updated date refreshed.
- Updated `workflows/create-wiki/LOG.md` — Started and completed entries appended.
- Updated root `LOG.md` — Completed entry appended.

## Steps
Run from anywhere (paths are resolved relative to the script, not the CWD):

```
python workflows/create-wiki/scripts/run.py <wiki-name> "<topic description>"
```

## Dependencies
- `workflows/create-wiki/wiki-context.md.template` [[workflows/create-wiki/CONTEXT]] — Template used to generate the new wiki's root CONTEXT.md.
- `wikis/CONTEXT.md` [[wikis/CONTEXT]] — Updated by the script on every run.
- `README.md` [[README]] (root) — Updated by the script on every run.
- `workflows/create-wiki/LOG.md` — Appended by the script on every run.
- `LOG.md` (root) — Appended by the script on every run.

## Known Issues
- The update logic for wikis/CONTEXT.md and README.md relies on their current markdown structure (section heading names, list item format). If those files are restructured, the insertion logic may place entries incorrectly.

## Revision History
- 2026-05-27 — Initial creation. Implements the 90-10 protocol for the create-wiki workflow.
