# Codex

**Last modified:** 2026-06-25

## Purpose
Stores project-scoped Codex configuration for Book Dragon.

## Contents
- config.toml - `.codex/config.toml` [[.codex/CONTEXT]] - Stores Codex lifecycle hook configuration and keeps the older raw Biblio MCP registration disabled.
- plugins/ - `.codex/plugins/` [[.codex/plugins/CONTEXT]] - Project-local Codex marketplace and Biblio Tools plugin wrapper.

## Inputs
- Codex reads this directory when the project is trusted.
- Codex plugin commands install the project-local Biblio Tools plugin from `.codex/plugins/`.

## Outputs
- Codex sessions use the lifecycle hook settings.
- New Codex sessions can discover the plugin-backed `biblio_tools` MCP server once the project-local plugin is installed.

## Steps
N/A. This is a configuration directory, not a workflow.

## Dependencies
- `AGENTS.md` [[AGENTS]] (root) - Defines project startup, maintenance, and logging rules.
- `AGENT-SETUP.md` [[AGENT-SETUP]] (root) - Documents Codex setup expectations.
- `workflows/biblio-tools/scripts/launch.py` [[workflows/biblio-tools/scripts/CONTEXT]] - Launches the Biblio Tools MCP server using the project runtime.
- `workflows/biblio-tools/scripts/server.py` [[workflows/biblio-tools/scripts/CONTEXT]] - Defines the Biblio Tools MCP server.
- `workflows/session-search/scripts/index.py` [[workflows/session-search/scripts/CONTEXT]] - Runs from the configured Codex Stop hook to archive and index completed sessions.
- `.codex/plugins/plugins/biblio-tools/CONTEXT.md` [[.codex/plugins/plugins/biblio-tools/CONTEXT]] - Plugin-backed Biblio Tools MCP route used by Codex agent sessions.

## Known Issues
- The raw `[mcp_servers.biblio-tools]` entry in `config.toml` is intentionally disabled because Codex agent sessions expose local Biblio Tools reliably through the project-local plugin-backed `biblio_tools` server instead.
- Windows process startup for the project Python environment can exceed Codex's default MCP startup timeout, so Biblio MCP registrations set explicit startup timeouts.

## Revision History
- 2026-06-24 - Initial creation.
- 2026-06-24 - Updated config.toml so the Biblio Tools MCP server launches from the project root when Codex resolves paths from `.codex/`.
- 2026-06-24 - Added explicit Biblio Tools MCP startup and tool timeouts for slower Windows process startup and longer project workflow calls.
- 2026-06-24 - Updated the Codex Stop hook to run the session-search indexer so completed Codex sessions become searchable immediately.
- 2026-06-25 - Added the project-local Biblio Tools Codex plugin route and disabled the older raw Codex MCP server entry.
