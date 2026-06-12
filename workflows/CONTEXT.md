# Workflows

**Last modified:** 2026-06-12

## Purpose
Parent directory for all workflows in Book Dragon. Each workflow lives in its own subdirectory within this folder.

## Contents
- Create Wiki — `workflows/create-wiki/` [[workflows/create-wiki/CONTEXT]] — Scaffolds a new LLM Wiki directory inside `wikis/` [[wikis/CONTEXT]] with the full structure ready to use.
- Audit — `workflows/audit/` [[workflows/audit/CONTEXT]] — Runs full-project structural audits or targeted CONTEXT.md metadata checks.
- Web Research — `workflows/web-research/` [[workflows/web-research/CONTEXT]] — CLI workflow for researching a topic; saves a research package for Biblio to turn into a report.
- Link Check — `workflows/link-check/` [[workflows/link-check/CONTEXT]] — Inserts Obsidian [[links]] into CONTEXT.md files and audits them for dead targets.
- Weather — `workflows/weather/` [[workflows/weather/CONTEXT]] — Fetches current conditions and forecasts for any location worldwide via Open-Meteo and Nominatim. No API keys required.
- Session Search — `workflows/session-search/` [[workflows/session-search/CONTEXT]] — Indexes all Book Dragon conversation transcripts into a local SQLite FTS5 full-text search database; provides a skill for searching session history.
- Settings Check — `workflows/settings-check/` [[workflows/settings-check/CONTEXT]] — Validates that all automated commands (hooks and scheduled tasks) are covered by allowlist entries in `.claude/settings.json`.
- Biblio Tools — `workflows/biblio-tools/` [[workflows/biblio-tools/CONTEXT]] — MCP server exposing project scripts as typed, callable tools for any AI that supports the Model Context Protocol.

## Inputs
None. Individual workflow subdirectories define their own inputs.

## Outputs
None. Individual workflow subdirectories define their own outputs.

## Steps
N/A. This is a container directory, not a workflow itself.

## Dependencies
- `AGENTS.md` [[AGENTS]] (root) — Defines the rules for workflow structure, the skills convention, the CONTEXT.md schema, and the requirement to use templates when scaffolding new workflows.
- `templates/` [[templates/CONTEXT]] — Biblio uses these templates when creating new workflow subdirectories.

## Known Issues
- The Contents section of this file must be updated every time a new workflow is added or removed. Container directories are easy to forget when the focus is on the new subdirectory itself.

## Revision History
Earlier history archived to LOG.md on 2026-06-12.
- 2026-05-27 — Added audit workflow to Contents. Removed long-standing TODO (audit workflow now built).
- 2026-05-29 — Added web-research workflow to Contents.
- 2026-06-03 — Added link-check workflow to Contents.
- 2026-06-03 — Added weather workflow to Contents.
- 2026-06-08 — Added session-search workflow to Contents.
- 2026-06-08 — Added settings-check workflow to Contents.
- 2026-06-09 — Dependencies updated from CLAUDE.md to AGENTS.md (AI-agnostic transition).
- 2026-06-10 — Added biblio-tools MCP server to Contents (Phase 3, AI-agnostic transition).
- 2026-06-12 — Updated the Audit entry to include targeted CONTEXT.md metadata checks.
