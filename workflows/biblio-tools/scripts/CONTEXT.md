# Biblio Tools Scripts

**Last modified:** 2026-06-10

## Purpose
Contains the MCP server script that wraps project scripts as typed tools, and the setup verification script.

## Contents
- server.py — `workflows/biblio-tools/scripts/server.py` [[workflows/biblio-tools/scripts/CONTEXT]] — The FastMCP server defining all tools and their typed parameters.
- verify.py — `workflows/biblio-tools/scripts/verify.py` [[workflows/biblio-tools/scripts/CONTEXT]] — Setup verification script (doctor pattern). Checks environment configuration for any supported AI. Standard library only — works on Python 3.9+.

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
- 2026-06-10 — Added verify.py (setup verification script).
- 2026-06-10 — verify.py: Python 3.9.x now returns WARN with upgrade advisory instead of PASS.
