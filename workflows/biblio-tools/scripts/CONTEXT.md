# Biblio Tools Scripts

**Last modified:** 2026-06-10

## Purpose
Contains the MCP server script that wraps project scripts as typed tools.

## Contents
- server.py — `workflows/biblio-tools/scripts/server.py` [[workflows/biblio-tools/scripts/CONTEXT]] — The FastMCP server defining all tools and their typed parameters.

## Inputs
None. The server is started by the AI's MCP client.

## Outputs
Tool responses returned to the MCP client as structured data (JSON).

## Steps
N/A. The server is started automatically by the MCP client, not run manually.

## Dependencies
- Python `mcp` package — provides FastMCP for tool definitions and server runtime.
- All project scripts listed in `workflows/biblio-tools/CONTEXT.md` [[workflows/biblio-tools/CONTEXT]] Dependencies section.

## Known Issues
None.

## Revision History
- 2026-06-10 — Initial creation.
