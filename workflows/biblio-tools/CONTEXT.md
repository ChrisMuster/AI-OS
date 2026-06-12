# Biblio Tools

**Last modified:** 2026-06-11

## Purpose
Provides Book Dragon's canonical Python runtime, setup verification, cross-platform workflow launcher, and MCP (Model Context Protocol) server. The same project commands work for every AI; MCP-capable clients additionally receive typed tools, while other clients run the underlying scripts directly.

## Contents
- scripts/ — `workflows/biblio-tools/scripts/` [[workflows/biblio-tools/scripts/CONTEXT]] — Shared runtime setup and hand-off helpers, MCP server, per-AI verification, launcher, and protocol smoke tests.
- requirements.txt — `workflows/biblio-tools/requirements.txt` [[workflows/biblio-tools/CONTEXT]] — MCP dependency included by the root project manifest.

## Inputs
None. The server reads the project structure and calls existing scripts.

## Outputs
Each tool returns structured output: success status, stdout, stderr, and return code. The `get_timestamp` tool returns a plain timestamp string. The `append_log` tool returns the formatted log entry.

## Steps
1. Run `python workflows/biblio-tools/scripts/setup.py` to create or repair the canonical `.venv` and install the root dependency manifest.
2. Register the server in the AI's MCP configuration (see `AGENT-SETUP.md` [[AGENT-SETUP]] for per-AI instructions; for Claude, the registration is in `.mcp.json` at the project root).
3. The server starts automatically when the AI connects to it.
4. Session startup verification reads the selected AI's native config and runs its exact launch command through an MCP handshake and tool smoke test.
5. Tools are available as typed MCP tools with parameter validation.

## Dependencies
- `AGENTS.md` [[AGENTS]] (root) — Defines the project structure and conventions the tools operate on.
- `requirements.txt` (root) — Canonical dependency manifest covering every dependency-bearing workflow.
- `workflows/audit/scripts/run.py` [[workflows/audit/scripts/CONTEXT]] — Called by the `run_audit` tool.
- `workflows/link-check/scripts/run.py` [[workflows/link-check/scripts/CONTEXT]] — Called by the `run_link_check` tool.
- `journal/scripts/new-month.py` [[journal/scripts/CONTEXT]] — Called by the `run_new_month` tool.
- `workflows/session-search/scripts/index.py` [[workflows/session-search/scripts/CONTEXT]] — Called by the `run_session_search_index` tool.
- `workflows/settings-check/scripts/run.py` [[workflows/settings-check/scripts/CONTEXT]] — Called by the `run_settings_check` tool.
- `AGENT-SETUP.md` [[AGENT-SETUP]] (root) — Human-readable setup documentation; verify.py points users to it for remediation.
- Python `mcp` package (>= 1.0.0, requires Python 3.10+) — The MCP SDK providing FastMCP. Note: verify.py itself is standard-library only and works on Python 3.9+.
- `workflows/create-wiki/scripts/extract_pdf.py` [[workflows/create-wiki/scripts/CONTEXT]] — verify.py confirms that shared PDF ingestion is installed for every AI.

## Known Issues
- Requires Python 3.10 or later (the MCP SDK requirement), while the rest of the project supports 3.9+. If running Python 3.9, the MCP server cannot start but all underlying scripts still work via direct shell commands.
- Adding a new project script requires adding one tool to server.py and one entry to AGENT-SETUP.md.
- Adding a new AI requires updating the `AI_REQUIREMENTS` mapping in verify.py with its native MCP config format and updating the per-AI section in `AGENT-SETUP.md` [[AGENT-SETUP]].
- A successful standalone handshake proves that the checked-in config launches the server correctly. Each client still needs one live confirmation that it discovers its project-scoped config.

## Revision History
- 2026-06-10 — Initial creation. Seven tools: run_audit, run_link_check, run_new_month, run_session_search_index, run_settings_check, get_timestamp, append_log (Phase 3, AI-agnostic transition).
- 2026-06-10 — Added verify.py (setup verification script) and verify_setup MCP tool. Eight tools total (Phase 4, AI-agnostic transition).
- 2026-06-10 — verify.py: Python 3.9.x now returns WARN with upgrade advisory. AGENT-SETUP.md: added "Adding a new AI" section.
- 2026-06-11 — Added project-local `.venv` support through a cross-platform launcher and updated setup verification.
- 2026-06-11 — Updated the MCP server for compatibility with the current SDK.
- 2026-06-11 — Standardised checked-in MCP configs on the `.venv` launcher and added native-config handshake verification for all supported AI profiles.
- 2026-06-11 — Added universal setup verification for the shared Create Wiki PDF extractor.
- 2026-06-11 — Added one canonical setup command and root dependency manifest for all AI clients and dependency-bearing workflows.
