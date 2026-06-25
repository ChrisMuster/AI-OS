# MCP

**Last modified:** 2026-06-24

## Purpose
Contains the launcher used by the Biblio Tools Codex plugin's MCP registration.

## Contents
- launch_biblio.py - `.codex/plugins/plugins/biblio-tools/mcp/launch_biblio.py` [[.codex/plugins/plugins/biblio-tools/mcp/CONTEXT]] - Resolves the project root and replaces itself with the existing Biblio Tools MCP server process.

## Inputs
- Project directory layout rooted five parents above this file.
- Project `.venv` if available.
- `workflows/biblio-tools/scripts/server.py` [[workflows/biblio-tools/scripts/CONTEXT]].

## Outputs
- A stdio MCP server process for Codex to connect to.

## Steps
N/A. This is a helper directory, not a workflow.

## Dependencies
- `workflows/biblio-tools/scripts/server.py` [[workflows/biblio-tools/scripts/CONTEXT]] - Server process launched by `launch_biblio.py`.

## Known Issues
- If the plugin directory is moved without preserving its relative depth under the project root, `launch_biblio.py` must be updated.

## Revision History
- 2026-06-24 - Initial creation.
