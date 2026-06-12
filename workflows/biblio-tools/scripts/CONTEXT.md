# Biblio Tools Scripts

**Last modified:** 2026-06-11

## Purpose
Contains the canonical project-runtime setup and hand-off helpers, MCP server, setup verification, launcher, and protocol smoke-test scripts.

## Contents
- server.py — `workflows/biblio-tools/scripts/server.py` [[workflows/biblio-tools/scripts/CONTEXT]] — The FastMCP server defining all tools and their typed parameters.
- verify.py — `workflows/biblio-tools/scripts/verify.py` [[workflows/biblio-tools/scripts/CONTEXT]] — Setup verification script (doctor pattern). Checks environment configuration and shared PDF ingestion for any supported AI. Standard library only — works on Python 3.9+.
- launch.py — `workflows/biblio-tools/scripts/launch.py` [[workflows/biblio-tools/scripts/CONTEXT]] — Cross-platform launcher that uses the project `.venv` interpreter when available.
- mcp_smoke.py — `workflows/biblio-tools/scripts/mcp_smoke.py` [[workflows/biblio-tools/scripts/CONTEXT]] — Connects to a configured stdio server, completes the MCP handshake, confirms the eight-tool inventory, calls a read-only tool, and checks input/path rejection.
- setup.py — `workflows/biblio-tools/scripts/setup.py` [[workflows/biblio-tools/scripts/CONTEXT]] — Creates or repairs the canonical `.venv`, installs the root dependency manifest, and verifies package availability.
- runtime.py — `workflows/biblio-tools/scripts/runtime.py` [[workflows/biblio-tools/scripts/CONTEXT]] — Hands dependency-bearing entry points to the canonical `.venv` without changing their documented commands.

## Inputs
None. The server is started by the AI's MCP client.

## Outputs
- Tool responses returned to the MCP client as structured data (JSON).
- Per-AI setup results that distinguish file/config presence from a working MCP handshake and tool inventory.
- A shared `.venv` containing all dependencies declared by the root `requirements.txt`.

## Steps
N/A. The server is started automatically by the MCP client, not run manually.

## Dependencies
- Python `mcp` package — provides FastMCP for tool definitions and server runtime.
- Root `requirements.txt` — includes every workflow-specific Python dependency manifest.
- Project `.venv` — canonical runtime used by all dependency-bearing workflow entry points.
- All project scripts listed in `workflows/biblio-tools/CONTEXT.md` [[workflows/biblio-tools/CONTEXT]] Dependencies section.

## Known Issues
None.

## Revision History
- 2026-06-10 — Initial creation.
- 2026-06-10 — Added verify.py (setup verification script).
- 2026-06-10 — verify.py: Python 3.9.x now returns WARN with upgrade advisory instead of PASS.
- 2026-06-11 — Added cross-platform `.venv` launcher and taught verify.py to detect MCP installed in the local environment.
- 2026-06-11 — Updated server initialisation for the current MCP SDK and made local-environment verification resilient to launch errors.
- 2026-06-11 — Added per-AI native config parsing and protocol-level MCP smoke tests; all supported profiles now verify their exact configured launch command.
- 2026-06-11 — Added a universal check for pypdf and the shared Create Wiki PDF extractor.
- 2026-06-11 — Added setup.py and runtime.py to provide one shared runtime and command path for every AI client.
