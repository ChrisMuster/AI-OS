# Biblio Tools

**Last modified:** 2026-09-23

## Purpose
Provides Book Dragon's canonical Python runtime, setup verification, cross-platform workflow launcher, and MCP (Model Context Protocol) server. The same project commands work for every AI; MCP-capable clients additionally receive typed tools, while other clients run the underlying scripts directly.

## Contents
- scripts/ - `workflows/biblio-tools/scripts/` [[workflows/biblio-tools/scripts/CONTEXT]] - Shared runtime setup and hand-off helpers, MCP server, per-AI verification, launcher, protocol smoke tests, and lifecycle checks.
- tests/ - `workflows/biblio-tools/tests/` [[workflows/biblio-tools/tests/CONTEXT]] - Unit tests for the server's pure helpers (the knowledge-graph query dispatcher's argv assembly and the `_run_json_script` failure paths) and for the Python floor checks and the Codex hook trust check in verify.py and setup.py.
- requirements.txt - `workflows/biblio-tools/requirements.txt` - MCP dependency included by the root project manifest.
- archived/ - `workflows/biblio-tools/archived/` [[workflows/biblio-tools/archived/CONTEXT]] - Holds the gitignored CODEX-MCP-AVAILABILITY-PLAN.md investigation notes; local-only.

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
- `.codex/plugins/plugins/biblio-tools/CONTEXT.md` [[.codex/plugins/plugins/biblio-tools/CONTEXT]] - Codex plugin wrapper used to expose Biblio Tools through Codex's plugin-backed MCP path.
- Python `mcp` package (>= 1.0.0) - The MCP SDK providing FastMCP; a required project dependency. verify.py itself is standard-library only, so it runs before the project environment exists.
- `workflows/create-wiki/scripts/extract_pdf.py` [[workflows/create-wiki/scripts/CONTEXT]] - verify.py confirms that shared PDF ingestion is installed for every AI.
- Codex (optional, external) - verify.py starts the installed Codex's app-server to ask whether it will run the project's Codex hooks defined for `workflows/rule-hooks/` [[workflows/rule-hooks/CONTEXT]]. Absent Codex, that one check is skipped with a quiet WARN.

## Known Issues
- The project's Python floor (3.13) is held in three places that must move together: `PYTHON_FLOOR` in `scripts/verify.py`, `PYTHON_FLOOR` in `scripts/setup.py`, and the gate in `workflows/web-research/scripts/run.py` [[workflows/web-research/scripts/CONTEXT]]. A test asserts the first two agree; the third is not checked. The floor is judged against two interpreters, the one running verify.py and the project `.venv` that workflows are handed to, because they differ on the script route (the MCP route runs verify.py inside `.venv`). Below the floor is a FAIL; above it is a WARN tagged `"surface": true`, which AGENTS.md startup step 6c reports even though it is otherwise silent on WARNs, because the floor is only tested while it is the version the project runs. When both interpreters are the same newer version, the one cause produces two surfaced warnings; accepted as the cost of checking both.
- A `.venv` built below the floor is refused rather than repaired: `setup.py --check` fails and `setup.py` stops before installing, telling the user to delete `.venv` and rerun. The script never deletes it. Only a mocked probe proves the below-floor path, since this machine has no older Python installed.
- Adding a new project script requires adding one tool to server.py and one entry to AGENT-SETUP.md.
- Adding a new AI requires updating the `AI_REQUIREMENTS` mapping in verify.py with its native MCP config format and updating the per-AI section in `AGENT-SETUP.md` [[AGENT-SETUP]].
- A successful standalone handshake proves that the checked-in config launches the server correctly. The smoke test checks knowledge-graph tool wiring without forcing a full graph rebuild during setup; run the knowledge-graph workflow's own tests and CLI checks when changing graph behaviour. Each client still needs one live confirmation that it discovers its project-scoped config.
- Codex raw `mcp_servers` registration can appear in `codex mcp list` while failing to expose tools to agents. Codex now uses the project-local `biblio_tools` plugin-backed MCP server; existing sessions must be restarted before that tool palette is available.

## Revision History
Earlier history archived to LOG.md on 2026-06-17.
- 2026-06-17 - Hardened orphan-process lifecycle handling: launch.py now owns parent-death cleanup with Windows-safe liveness checks, server.py focuses on stdin/idle shutdown, and lifecycle_check.py verifies cleanup behaviour.
- 2026-06-20 - Phase 3: exposed the knowledge graph as two MCP tools (`build_knowledge_graph`, `query_knowledge_graph`), which shell out to `workflows/knowledge-graph/scripts/run.py` [[workflows/knowledge-graph/scripts/CONTEXT]]. Added a `tests/` directory with a unit test for the dispatcher's argv helper; extended mcp_smoke.py to the ten-tool inventory.
- 2026-06-21 - Knowledge-graph Phase 4: added tests/test_run_json_script.py covering the `_run_json_script` helper's failure paths (no change to server.py).
- 2026-06-24 - Hardened setup verification: mcp_smoke.py now uses the saved knowledge-graph index for fast stats when available and falls back to a lightweight argument-validation check when no index exists; verify.py's smoke timeout increased to 90 seconds as a safety margin.
- 2026-06-24 - Investigated Codex Desktop MCP availability (config and protocol checks pass, but the session does not inject project-local tools into the callable palette). The working investigation notes are kept local-only (gitignored) and are not listed in Contents; the open issue is tracked in the backlog.
- 2026-06-24 - Corrected the Codex Desktop setup verifier so its MCP handshake timeout now matches the documented 90-second smoke-test allowance.
- 2026-06-24 - Raised the verifier's project-venv MCP import probe timeout to prevent slow Windows process startup from producing a false MCP package warning.
- 2026-06-24 - Fixed Codex project-config path handling: `.codex/config.toml` now launches Biblio Tools from the project root, and verify.py validates the same configured cwd.
- 2026-06-25 - Added the Codex plugin-backed Biblio Tools route and updated setup verification to check the enabled `biblio_tools` registry entry.
- 2026-07-10 - Task 3 hygiene sweep: moved the concluded CODEX-MCP-AVAILABILITY-PLAN.md investigation notes into a new `archived/` subdirectory (was loose in the workflow root), per the archive-plans-on-completion rule. No behaviour change.
- 2026-09-23 - Project Python floor raised from 3.9 to 3.13. The 3.9 floor was already unreachable: `truststore` (web-research) needs 3.10+ with no version marker, so setup on 3.9 could not install. `requirements.txt` drops the `python_version` marker on `mcp`, which becomes an ordinary required dependency; the Dependencies line and the Known Issues entry describing a 3.9 degraded mode were replaced by one recording where the floor is held and why verify.py now warns above it.
- 2026-09-23 - Repaired the two findings from Codex's review of the floor change. The above-floor WARN now carries `"surface": true` and AGENTS.md step 6c reports such results, where before startup's silence on WARN-only results hid it. The project `.venv` interpreter is now checked against the floor: verify.py gained a "Project runtime Python version" check, and setup.py refuses a below-floor `.venv` in both `--check` and repair. Added `tests/test_verify_floor.py`. Known Issues rewritten for both.
- 2026-09-23 - Setup verification now asks the installed Codex, for every AI's session, whether it will run the project's Codex hooks, and fails visibly if it will not. Added after Codex was found to have run without them for eight weeks, because editing a hook voids its stored approval and Codex then skips it silently. Added `tests/test_verify_codex_hooks.py`; Codex is listed as an optional external dependency.
