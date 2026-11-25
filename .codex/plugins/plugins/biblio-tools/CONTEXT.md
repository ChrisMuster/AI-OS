# Biblio Tools Plugin

**Last modified:** 2026-11-24

## Purpose
Provides a Codex plugin wrapper that exposes the existing Biblio Tools MCP server through Codex's plugin-backed MCP path.

## Contents
- .codex-plugin/ - `.codex/plugins/plugins/biblio-tools/.codex-plugin/` [[.codex/plugins/plugins/biblio-tools/.codex-plugin/CONTEXT]] - Codex plugin manifest directory.
- .mcp.json - `.codex/plugins/plugins/biblio-tools/.mcp.json` [[.codex/plugins/plugins/biblio-tools/CONTEXT]] - Plugin MCP server registration for Biblio Tools.
- mcp/ - `.codex/plugins/plugins/biblio-tools/mcp/` [[.codex/plugins/plugins/biblio-tools/mcp/CONTEXT]] - Launcher used by the plugin MCP registration.

## Inputs
- The project-local marketplace installs this plugin into Codex.
- The launcher expects the project `workflows/biblio-tools/scripts/launch.py` and `server.py` files to exist.

## Outputs
- A plugin-backed MCP server named `biblio_tools` in Codex sessions that install the plugin.

## Steps
N/A. This is a plugin source directory, not a workflow.

## Dependencies
- `workflows/biblio-tools/scripts/launch.py` and `server.py` [[workflows/biblio-tools/scripts/CONTEXT]] - The project launcher the plugin runs, and the Biblio Tools MCP server it starts.
- Project `.venv` - The Python runtime `launch.py` starts the server with.

## Known Issues
- Codex sessions must be restarted after installing or updating the plugin so the tool palette is rebuilt.
- Codex runs a cached copy of the plugin, so a change to `mcp/launch_biblio.py` needs the plugin reinstalled or updated before Codex uses it.
- From that cache the launcher can find the project only through a few home-folder guesses unless Codex sets a workspace variable, so a checkout elsewhere leaves Biblio Tools missing from Codex (see `mcp/CONTEXT.md` and the backlog).

## Revision History
- 2026-06-24 - Initial creation.
- 2026-11-24 - The launcher now starts the server through the project's `launch.py` rather than exec-ing `server.py`, so the plugin route gets the same lifecycle cleanup as every other AI (cross-review R2-1). Inputs, Dependencies and Known Issues updated. After R3-1, Known Issues also records that the cached launcher finds the project only through home-folder guesses.
