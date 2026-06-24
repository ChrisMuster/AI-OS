# Biblio Tools

**Last modified:** 2026-06-24

## Purpose
Provides Book Dragon's canonical Python runtime, setup verification, cross-platform workflow launcher, and MCP (Model Context Protocol) server. The same project commands work for every AI; MCP-capable clients additionally receive typed tools, while other clients run the underlying scripts directly.

## Contents
- scripts/ - `workflows/biblio-tools/scripts/` [[workflows/biblio-tools/scripts/CONTEXT]] - Shared runtime setup and hand-off helpers, MCP server, per-AI verification, launcher, protocol smoke tests, and lifecycle checks.
- tests/ - `workflows/biblio-tools/tests/` [[workflows/biblio-tools/tests/CONTEXT]] - Unit tests for the server's pure helpers (the knowledge-graph query dispatcher's argv assembly and the `_run_json_script` failure paths).
- requirements.txt - `workflows/biblio-tools/requirements.txt` [[workflows/biblio-tools/CONTEXT]] - MCP dependency included by the root project manifest.

## Inputs
None. The server reads the project structure and calls existing scripts.

## Outputs
Each tool returns structured output: success status, stdout, stderr, and return code. The `get_timestamp` tool returns a plain timestamp string. The `append_log` tool returns the formatted log entry.

## Steps
1. Run `python workflows/biblio-tools/scripts/setup.py` to create or repair the canonical `.venv` and install the root dependency manifest.
2. Register the server in the AI's MCP configuration (see `AGENT-SETUP.md` [[AGENT-SETUP]] for per-AI instructions; for Claude, the registration is in `.mcp.json` at the project root).
3. The server starts automatically when the AI connects to it.
4. Session startup verification reads the selected AI's native config and runs its exact launch command through an MCP handshake and fast tool smoke test.
5. Tools are available as typed MCP tools with parameter validation.

## Dependencies
- `AGENTS.md` [[AGENTS]] (root) - Defines the project structure and conventions the tools operate on.
- `requirements.txt` (root) - Canonical dependency manifest covering every dependency-bearing workflow.
- `workflows/audit/scripts/run.py` [[workflows/audit/scripts/CONTEXT]] - Called by the `run_audit` tool.
- `workflows/link-check/scripts/run.py` [[workflows/link-check/scripts/CONTEXT]] - Called by the `run_link_check` tool.
- `journal/scripts/new-month.py` [[journal/scripts/CONTEXT]] - Called by the `run_new_month` tool.
- `workflows/session-search/scripts/index.py` [[workflows/session-search/scripts/CONTEXT]] - Called by the `run_session_search_index` tool.
- `workflows/settings-check/scripts/run.py` [[workflows/settings-check/scripts/CONTEXT]] - Called by the `run_settings_check` tool.
- `workflows/knowledge-graph/scripts/run.py` [[workflows/knowledge-graph/scripts/CONTEXT]] - Called by the `build_knowledge_graph` and `query_knowledge_graph` tools.
- `AGENT-SETUP.md` [[AGENT-SETUP]] (root) - Human-readable setup documentation; verify.py points users to it for remediation.
- Python `mcp` package (>= 1.0.0, requires Python 3.10+) - The MCP SDK providing FastMCP. Note: verify.py itself is standard-library only and works on Python 3.9+.
- `workflows/create-wiki/scripts/extract_pdf.py` [[workflows/create-wiki/scripts/CONTEXT]] - verify.py confirms that shared PDF ingestion is installed for every AI.

## Known Issues
- Requires Python 3.10 or later (the MCP SDK requirement), while the rest of the project supports 3.9+. If running Python 3.9, the MCP server cannot start but all underlying scripts still work via direct shell commands.
- Adding a new project script requires adding one tool to server.py and one entry to AGENT-SETUP.md.
- Adding a new AI requires updating the `AI_REQUIREMENTS` mapping in verify.py with its native MCP config format and updating the per-AI section in `AGENT-SETUP.md` [[AGENT-SETUP]].
- A successful standalone handshake proves that the checked-in config launches the server correctly. The smoke test checks knowledge-graph tool wiring without forcing a full graph rebuild during setup; run the knowledge-graph workflow's own tests and CLI checks when changing graph behaviour. Each client still needs one live confirmation that it discovers its project-scoped config.
- Codex Desktop sessions on Windows have shown the server passing standalone MCP verification while the Biblio Tools themselves were not exposed in the session's callable tool list. This is under investigation (tracked in the backlog); until resolved, use direct shell commands for Book Dragon workflows in affected Codex Desktop sessions.

## Revision History
Earlier history archived to LOG.md on 2026-06-17.
- 2026-06-11 - Added universal setup verification for the shared Create Wiki PDF extractor.
- 2026-06-11 - Added one canonical setup command and root dependency manifest for all AI clients and dependency-bearing workflows.
- 2026-06-17 - Orphan prevention: server.py and launch.py now detect parent session death and self-terminate. Fixes accumulation of stale MCP server processes across sessions.
- 2026-06-17 - Hardened orphan-process lifecycle handling: launch.py now owns parent-death cleanup with Windows-safe liveness checks, server.py focuses on stdin/idle shutdown, and lifecycle_check.py verifies cleanup behaviour.
- 2026-06-20 - Phase 3: exposed the knowledge graph as two MCP tools (`build_knowledge_graph`, `query_knowledge_graph`), which shell out to `workflows/knowledge-graph/scripts/run.py` [[workflows/knowledge-graph/scripts/CONTEXT]]. Added a `tests/` directory with a unit test for the dispatcher's argv helper; extended mcp_smoke.py to the ten-tool inventory.
- 2026-06-21 - Knowledge-graph Phase 4: added tests/test_run_json_script.py covering the `_run_json_script` helper's failure paths (no change to server.py).
- 2026-06-24 - Hardened setup verification: mcp_smoke.py now uses the saved knowledge-graph index for fast stats when available and falls back to a lightweight argument-validation check when no index exists; verify.py's smoke timeout increased to 90 seconds as a safety margin.
- 2026-06-24 - Investigated Codex Desktop MCP availability (config and protocol checks pass, but the session does not inject project-local tools into the callable palette). The working investigation notes are kept local-only (gitignored) and are not listed in Contents; the open issue is tracked in the backlog.
