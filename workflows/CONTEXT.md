# Workflows

**Last modified:** 2026-05-27

## Purpose
Parent directory for all workflows in Book Dragon. Each workflow lives in its own subdirectory within this folder.

## Contents
- Create Wiki — `workflows/create-wiki/` — Scaffolds a new LLM Wiki directory inside `wikis/` with the full structure ready to use.
- Audit — `workflows/audit/` — Walks all project directories and reports structural compliance issues (missing files, sections, paths).
- Web Research — `workflows/web-research/` — CLI workflow for researching a topic; saves a research package for Biblio to turn into a report.

## Inputs
None. Individual workflow subdirectories define their own inputs.

## Outputs
None. Individual workflow subdirectories define their own outputs.

## Steps
N/A. This is a container directory, not a workflow itself.

## Dependencies
- `CLAUDE.md` (root) — Defines the rules for workflow structure, the skills convention, the CONTEXT.md schema, and the requirement to use templates when scaffolding new workflows.
- `templates/` — Biblio uses these templates when creating new workflow subdirectories.

## Known Issues
- The Contents section of this file must be updated every time a new workflow is added or removed. Container directories are easy to forget when the focus is on the new subdirectory itself.

## Revision History
- 2026-05-12 — Initial creation.
- 2026-05-12 — Updated to follow standardised CONTEXT.md schema.
- 2026-05-12 — Added Contents section per updated schema.
- 2026-05-12 — Added dependencies on CLAUDE.md and templates. Flagged Contents-staleness risk in Known Issues.
- 2026-05-12 — Added TODO reminder to build a CONTEXT.md audit workflow.
- 2026-05-13 — Added create-wiki workflow to Contents.
- 2026-05-27 — Added audit workflow to Contents. Removed long-standing TODO (audit workflow now built).
- 2026-05-29 — Added web-research workflow to Contents.
