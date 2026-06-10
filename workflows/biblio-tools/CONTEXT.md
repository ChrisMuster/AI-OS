# Biblio Tools

**Last modified:** 2026-06-10

## Purpose
MCP (Model Context Protocol) server that exposes Book Dragon project scripts as typed, callable tools. Also includes the setup verification script (`verify.py`) used during session startup. Provides a universal tool layer for any AI that supports MCP — typed parameters, cross-platform execution, and structured output. The underlying scripts are unchanged and still work via direct shell commands for AIs without MCP support.

## Contents
- scripts/ — `workflows/biblio-tools/scripts/` [[workflows/biblio-tools/scripts/CONTEXT]] — The MCP server script (server.py).
- requirements.txt — `workflows/biblio-tools/requirements.txt` [[workflows/biblio-tools/CONTEXT]] — Python package dependency (mcp).

## Inputs
None. The server reads the project structure and calls existing scripts.

## Outputs
Each tool returns structured output: success status, stdout, stderr, and return code. The `get_timestamp` tool returns a plain timestamp string. The `append_log` tool returns the formatted log entry.

## Steps
1. Install dependencies: `pip install -r workflows/biblio-tools/requirements.txt`
2. Register the server in the AI's MCP configuration (see `AGENT-SETUP.md` [[AGENT-SETUP]] for per-AI instructions; for Claude, the registration is in `.mcp.json` at the project root).
3. The server starts automatically when the AI connects to it.
4. Tools are available as typed MCP tools with parameter validation.

## Dependencies
- `AGENTS.md` [[AGENTS]] (root) — Defines the project structure and conventions the tools operate on.
- `workflows/audit/scripts/run.py` [[workflows/audit/scripts/CONTEXT]] — Called by the `run_audit` tool.
- `workflows/link-check/scripts/run.py` [[workflows/link-check/scripts/CONTEXT]] — Called by the `run_link_check` tool.
- `journal/scripts/new-month.py` [[journal/scripts/CONTEXT]] — Called by the `run_new_month` tool.
- `workflows/session-search/scripts/index.py` [[workflows/session-search/scripts/CONTEXT]] — Called by the `run_session_search_index` tool.
- `workflows/settings-check/scripts/run.py` [[workflows/settings-check/scripts/CONTEXT]] — Called by the `run_settings_check` tool.
- `AGENT-SETUP.md` [[AGENT-SETUP]] (root) — Human-readable setup documentation; verify.py points users to it for remediation.
- Python `mcp` package (>= 1.0.0, requires Python 3.10+) — The MCP SDK providing FastMCP. Note: verify.py itself is standard-library only and works on Python 3.9+.

## Known Issues
- Requires Python 3.10 or later (the MCP SDK requirement), while the rest of the project supports 3.9+. If running Python 3.9, the MCP server cannot start but all underlying scripts still work via direct shell commands.
- Adding a new project script requires adding one tool to server.py and one entry to AGENT-SETUP.md.
- Adding a new AI requires updating the `AI_REQUIREMENTS` mapping in verify.py and the per-AI section in `AGENT-SETUP.md` [[AGENT-SETUP]].

## Revision History
- 2026-06-10 — Initial creation. Seven tools: run_audit, run_link_check, run_new_month, run_session_search_index, run_settings_check, get_timestamp, append_log (Phase 3, AI-agnostic transition).
- 2026-06-10 — Added verify.py (setup verification script) and verify_setup MCP tool. Eight tools total (Phase 4, AI-agnostic transition).
- 2026-06-10 — verify.py: Python 3.9.x now returns WARN with upgrade advisory. AGENT-SETUP.md: added "Adding a new AI" section.
