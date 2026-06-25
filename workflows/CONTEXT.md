# Workflows

**Last modified:** 2026-06-25

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
- Reddit Collector — `workflows/reddit-collector/` [[workflows/reddit-collector/CONTEXT]] — Downloads posts from configured subreddits, saves as Markdown, detects multi-part series and groups them with navigable indexes.
- Knowledge Graph — `workflows/knowledge-graph/` [[workflows/knowledge-graph/CONTEXT]] — Deterministic indexer that parses CONTEXT.md files and approved relationships into a rebuildable node/edge graph for querying, traversal, and broken-reference validation.
- Encoding Guard - `workflows/encoding-guard/` [[workflows/encoding-guard/CONTEXT]] - Checks the project for encoding problems (invalid UTF-8, mojibake, BOMs, text-mode subprocess calls with no explicit encoding) and repairs corrupted files to clean UTF-8; the full audit runs the check automatically.
- Personal Data Guard - `workflows/personal-data-guard/` [[workflows/personal-data-guard/CONTEXT]] - Read-only checker that scans committable files for personal data (emails, personal home paths, the user's name/username, and a configurable denylist of personal nouns); the full audit runs the check automatically as an advisory hook.

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
Earlier history archived to LOG.md on 2026-06-25.
- 2026-06-08 — Added session-search workflow to Contents.
- 2026-06-08 — Added settings-check workflow to Contents.
- 2026-06-09 — Dependencies updated from CLAUDE.md to AGENTS.md (AI-agnostic transition).
- 2026-06-10 — Added biblio-tools MCP server to Contents (Phase 3, AI-agnostic transition).
- 2026-06-12 — Updated the Audit entry to include targeted CONTEXT.md metadata checks.
- 2026-06-15 — Added reddit-collector workflow to Contents.
- 2026-06-19 — Added knowledge-graph workflow to Contents.
- 2026-06-24 - Added encoding-guard workflow to Contents.
- 2026-06-25 - Added personal-data-guard workflow to Contents.
