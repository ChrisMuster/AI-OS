# Biblio Tools Scripts

**Last modified:** 2026-06-25

## Purpose
Contains the canonical project-runtime setup and hand-off helpers, MCP server, setup verification, launcher, and protocol smoke-test scripts.

## Contents
- server.py - `workflows/biblio-tools/scripts/server.py` [[workflows/biblio-tools/scripts/CONTEXT]] - The FastMCP server defining all ten tools and their typed parameters, including the knowledge-graph build and query dispatcher assembled by the pure, unit-testable `build_kg_query_argv` helper.
- verify.py - `workflows/biblio-tools/scripts/verify.py` [[workflows/biblio-tools/scripts/CONTEXT]] - Setup verification script (doctor pattern). Checks environment configuration, shared PDF ingestion, and the configured MCP server for any supported AI. Standard library only; works on Python 3.9+.
- launch.py - `workflows/biblio-tools/scripts/launch.py` [[workflows/biblio-tools/scripts/CONTEXT]] - Cross-platform launcher that uses the project `.venv` interpreter when available.
- mcp_smoke.py - `workflows/biblio-tools/scripts/mcp_smoke.py` [[workflows/biblio-tools/scripts/CONTEXT]] - Connects to a configured stdio server, completes the MCP handshake, confirms the ten-tool inventory, calls read-only tools, checks input/path rejection, and verifies knowledge-graph query wiring using saved-index stats when available with a lightweight no-index fallback.
- lifecycle_check.py - `workflows/biblio-tools/scripts/lifecycle_check.py` [[workflows/biblio-tools/scripts/CONTEXT]] - Verifies helper-process lifecycle safeguards: MCP handshake, parent-death cleanup, and reader stale-PID safety.
- setup.py - `workflows/biblio-tools/scripts/setup.py` [[workflows/biblio-tools/scripts/CONTEXT]] - Creates or repairs the canonical `.venv`, installs the root dependency manifest, and verifies package availability.
- runtime.py - `workflows/biblio-tools/scripts/runtime.py` [[workflows/biblio-tools/scripts/CONTEXT]] - Shared project-runtime helpers: `.venv` discovery, process liveness checking (cross-platform), and runtime hand-off for dependency-bearing entry points.

## Inputs
None. The server is started by the AI's MCP client.

## Outputs
- Tool responses returned to the MCP client as structured data (JSON).
- Per-AI setup results that distinguish file/config presence from a working MCP handshake and tool inventory.
- A shared `.venv` containing all dependencies declared by the root `requirements.txt`.

## Steps
N/A. The server is started automatically by the MCP client, not run manually.

## Dependencies
- Python `mcp` package - Provides FastMCP for tool definitions and server runtime.
- Root `requirements.txt` - Includes every workflow-specific Python dependency manifest.
- Project `.venv` - Canonical runtime used by all dependency-bearing workflow entry points.
- All project scripts listed in `workflows/biblio-tools/CONTEXT.md` [[workflows/biblio-tools/CONTEXT]] Dependencies section.

## Known Issues
None.

## Revision History
Earlier history archived to LOG.md on 2026-06-24.
- 2026-06-23 - Knowledge-graph Phase 8: `build_knowledge_graph` and `query_knowledge_graph` gain an `include_conversation` parameter (appending `--layer conversation`); `build_kg_query_argv` threads it through. No new tools; the ten-tool inventory is unchanged.
- 2026-06-23 - Knowledge-graph audit-hook integration: `run_audit` gains a `with_graph: bool = True` parameter (appending `--no-graph` when false) and an updated docstring noting the full audit now also validates the structural graph and merges its WARN/FAIL findings. No new tools; the ten-tool inventory is unchanged.
- 2026-06-24 - Knowledge-graph session-search cross-reference: `query_knowledge_graph` gains a tenth command, `sessions` (added to the `Literal`, to `_KG_NEEDS_ID`, and to `build_kg_query_argv`), plus `terms`/`limit`/`since`/`ai`/`source` parameters threaded through to `run.py sessions` (the node-to-transcripts lookup). No new tools; the ten-tool inventory is unchanged.
- 2026-06-24 - Hardened MCP smoke verification: mcp_smoke.py now calls `query_knowledge_graph stats` with `from_index=True` when a saved graph index exists, falls back to lightweight argument validation when no saved index exists, and verify.py allows up to 90 seconds for the protocol smoke test.
- 2026-06-24 - Encoding hardening: pinned `encoding="utf-8"` on the text-mode `subprocess` calls in verify.py, setup.py, and lifecycle_check.py so they decode as UTF-8 rather than the Windows cp1252 default. No behavioural change.
- 2026-06-24 - Corrected verify.py's MCP handshake smoke-test timeout to the documented 90 seconds so Codex Desktop setup verification matches the direct protocol smoke behaviour.
- 2026-06-24 - Raised verify.py's project-venv MCP import probe timeout to avoid false warnings on slow Windows process startup.
- 2026-06-24 - verify.py now honours Codex MCP `cwd` resolution from `.codex/config.toml`, so setup verification matches Codex's actual project-config launch semantics.
- 2026-06-25 - verify.py now checks Codex's enabled plugin-backed `biblio_tools` MCP registry entry instead of treating the disabled raw `biblio-tools` entry as the live Codex route.
