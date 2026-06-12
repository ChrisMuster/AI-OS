# Session Search — Skills

**Last modified:** 2026-06-12

## Purpose
Container for workflow-scoped skills belonging to the session-search workflow. Each skill gets its own subdirectory following the standard convention.

## Contents
- session-search — `workflows/session-search/skills/session-search/` [[workflows/session-search/skills/session-search/CONTEXT]] — Biblio-invocable skill for searching Book Dragon session history.

## Inputs
None. Individual skill subdirectories define their own inputs.

## Outputs
None. Individual skill subdirectories define their own outputs.

## Steps
N/A. This is a container directory, not a workflow itself.

## Dependencies
- `AGENTS.md` [[AGENTS]] — Defines the skills convention: one skill per subdirectory, SKILL.md as the spec, CONTEXT.md as the directory record.

## Known Issues
None.

## Revision History
- 2026-06-08 — Initial creation.
- 2026-06-09 — Dependencies updated from CLAUDE.md to AGENTS.md (AI-agnostic transition).
- 2026-06-12 — Corrected context metadata so Last modified matches Revision History.
