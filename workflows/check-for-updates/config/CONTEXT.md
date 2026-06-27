# Check For Updates - Config

**Last modified:** 2026-06-27

## Purpose
Holds the single source of truth for what the workflow inspects. Adding an AI CLI tool or a landscape-watch product is a config entry here, not new code.

## Contents
- sources.yaml - `workflows/check-for-updates/config/sources.yaml` [[workflows/check-for-updates/config/CONTEXT]] - The staleness threshold, the Python-deps toggle, the list of AI CLI tools (command, version flag, version regex, and "latest" source - npm or GitHub), and the `landscape` block (enabled toggle, signal keywords, and the watch list of products with query and optional aliases).

## Inputs
None. This is a static configuration file, edited by hand.

## Outputs
None. It is read by the workflow scripts; it produces nothing itself.

## Steps
N/A. Configuration directory, not a workflow.

## Dependencies
- `workflows/check-for-updates/scripts/` [[workflows/check-for-updates/scripts/CONTEXT]] - reads this file at runtime via PyYAML.

## Known Issues
- npm package ids and command names can drift as vendors rename or restructure their tools; this file must be updated by hand when that happens.

## Revision History
- 2026-06-26 - Initial creation. Staleness threshold, Python-deps toggle, and three AI CLI tools (Claude Code, Codex CLI, Gemini CLI / Antigravity).
- 2026-06-27 - Added the `landscape` block for Phase 2: enabled toggle, signal keywords, and a 9-product watch list (the supported AIs).
