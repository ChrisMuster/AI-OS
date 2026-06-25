# Agents

**Last modified:** 2026-06-24

## Purpose
Contains Codex marketplace metadata for the project-local plugin marketplace.

## Contents
- plugins/ - `.codex/plugins/.agents/plugins/` [[.codex/plugins/.agents/plugins/CONTEXT]] - Marketplace manifest directory.

## Inputs
- Codex reads this directory when registering the project-local marketplace.

## Outputs
- Marketplace metadata used by Codex plugin installation.

## Steps
N/A. This is a configuration directory, not a workflow.

## Dependencies
- `.codex/plugins/plugins/biblio-tools/CONTEXT.md` [[.codex/plugins/plugins/biblio-tools/CONTEXT]] - Plugin source listed by the marketplace manifest.

## Known Issues
- None.

## Revision History
- 2026-06-24 - Initial creation.
