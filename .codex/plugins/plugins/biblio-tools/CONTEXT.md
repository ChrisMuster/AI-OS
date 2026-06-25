# Biblio Tools Plugin

**Last modified:** 2026-06-24

## Purpose
Provides a Codex plugin wrapper that exposes the existing Biblio Tools MCP server through Codex's plugin-backed MCP path.

## Contents
- .codex-plugin/ - `.codex/plugins/plugins/biblio-tools/.codex-plugin/` [[.codex/plugins/plugins/biblio-tools/.codex-plugin/CONTEXT]] - Codex plugin manifest directory.
- .mcp.json - `.codex/plugins/plugins/biblio-tools/.mcp.json` [[.codex/plugins/plugins/biblio-tools/CONTEXT]] - Plugin MCP server registration for Biblio Tools.
- mcp/ - `.codex/plugins/plugins/biblio-tools/mcp/` [[.codex/plugins/plugins/biblio-tools/mcp/CONTEXT]] - Launcher used by the plugin MCP registration.

## Inputs
- The project-local marketplace installs this plugin into Codex.
- The launcher expects the project `workflows/biblio-tools/scripts/server.py` file to exist.

## Outputs
- A plugin-backed MCP server named `biblio_tools` in Codex sessions that install the plugin.

## Steps
N/A. This is a plugin source directory, not a workflow.

## Dependencies
- `workflows/biblio-tools/scripts/server.py` [[workflows/biblio-tools/scripts/CONTEXT]] - Existing Biblio Tools MCP server exposed by this plugin.
- Project `.venv` - Preferred Python runtime used by the launcher when available.

## Known Issues
- Codex sessions must be restarted after installing or updating the plugin so the tool palette is rebuilt.

## Revision History
- 2026-06-24 - Initial creation.
