# Codex Plugins

**Last modified:** 2026-06-24

## Purpose
Contains project-local Codex plugin marketplace metadata used to expose project tools through Codex's plugin system.

## Contents
- .agents/ - `.codex/plugins/.agents/` [[.codex/plugins/.agents/CONTEXT]] - Codex marketplace metadata for project-local plugins.
- plugins/ - `.codex/plugins/plugins/` [[.codex/plugins/plugins/CONTEXT]] - Local plugin source directories referenced by the marketplace manifest.

## Inputs
- Codex plugin marketplace commands read `.agents/plugins/marketplace.json`.
- Plugin source directories must contain valid `.codex-plugin/plugin.json` manifests.

## Outputs
- Installed Codex plugins can expose skills, apps, and MCP tools to new Codex sessions.

## Steps
N/A. This is a configuration directory, not a workflow.

## Dependencies
- `AGENT-SETUP.md` [[AGENT-SETUP]] - Documents setup expectations for Codex surfaces.
- `.codex/plugins/plugins/biblio-tools/CONTEXT.md` [[.codex/plugins/plugins/biblio-tools/CONTEXT]] - The project-local Biblio Tools Codex plugin.

## Known Issues
- Codex must install this marketplace and plugin before the plugin-backed MCP tools are available in a new session.

## Revision History
- 2026-06-24 - Initial creation with the Biblio Tools project-local plugin marketplace.
