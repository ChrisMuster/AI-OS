# Plugin Manifest

**Last modified:** 2026-06-24

## Purpose
Contains the Codex plugin manifest for the project-local Biblio Tools plugin.

## Contents
- plugin.json - `.codex/plugins/plugins/biblio-tools/.codex-plugin/plugin.json` [[.codex/plugins/plugins/biblio-tools/.codex-plugin/CONTEXT]] - Codex plugin metadata and MCP registration pointer.

## Inputs
- Codex reads `plugin.json` when installing or loading the plugin.

## Outputs
- Plugin metadata and MCP server discovery for Codex.

## Steps
N/A. This is a manifest directory, not a workflow.

## Dependencies
- `.codex/plugins/plugins/biblio-tools/.mcp.json` [[.codex/plugins/plugins/biblio-tools/CONTEXT]] - MCP server configuration referenced by the manifest.

## Known Issues
- None.

## Revision History
- 2026-06-24 - Initial creation.
