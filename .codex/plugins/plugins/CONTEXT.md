# Plugins

**Last modified:** 2026-06-24

## Purpose
Stores project-local Codex plugin source directories referenced by `.codex/plugins/marketplace.json`.

## Contents
- biblio-tools/ - `.codex/plugins/plugins/biblio-tools/` [[.codex/plugins/plugins/biblio-tools/CONTEXT]] - Codex plugin wrapper that exposes the Biblio Tools MCP server.

## Inputs
- Plugin source directories are read by Codex when installing from the project-local marketplace.

## Outputs
- Installed plugin cache entries under the user's Codex home.

## Steps
N/A. This is a configuration source directory, not a workflow.

## Dependencies
- `.codex/plugins/marketplace.json` [[.codex/plugins/CONTEXT]] - Lists plugin source directories for installation.

## Known Issues
- None.

## Revision History
- 2026-06-24 - Initial creation with the Biblio Tools plugin source.
