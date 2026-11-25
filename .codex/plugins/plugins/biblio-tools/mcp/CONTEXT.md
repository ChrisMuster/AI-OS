# MCP

**Last modified:** 2026-11-24

## Purpose
Contains the launcher used by the Biblio Tools Codex plugin's MCP registration.

## Contents
- launch_biblio.py - `.codex/plugins/plugins/biblio-tools/mcp/launch_biblio.py` [[.codex/plugins/plugins/biblio-tools/mcp/CONTEXT]] - Resolves the project root and runs the project's `launch.py` in its own process to start the server, so Codex gets the same lifecycle chain as every other AI: launch.py watches Codex, and the server exits if this process is killed.

## Inputs
- The project root, found as the first of these that holds `AGENTS.md` and `workflows/biblio-tools/scripts/server.py`: the environment variables `BIBLIO_PROJECT_ROOT`, `CODEX_PROJECT_ROOT`, `CODEX_WORKSPACE`, `CODEX_WORKSPACE_ROOT`, `WORKSPACE_FOLDER` and `PWD`; the working folder and its parents; then fixed guesses under the home folder (`Desktop/AI-Work/AI-OS`, `Desktop/AI-OS`, `AI-Work/AI-OS`, `AI-OS`). It does not look at the folders above this file.
- Project `.venv`, which `launch.py` requires for the server.
- `workflows/biblio-tools/scripts/launch.py` and `server.py` [[workflows/biblio-tools/scripts/CONTEXT]].

## Outputs
- A stdio MCP server process for Codex to connect to.

## Steps
N/A. This is a helper directory, not a workflow.

## Dependencies
- `workflows/biblio-tools/scripts/launch.py` [[workflows/biblio-tools/scripts/CONTEXT]] - Run in-process by `launch_biblio.py`; starts and watches the server.
- `workflows/biblio-tools/scripts/server.py` [[workflows/biblio-tools/scripts/CONTEXT]] - Server process started through `launch.py`.
- `workflows/biblio-tools/scripts/lifecycle_check.py` [[workflows/biblio-tools/scripts/CONTEXT]] - Its "Codex plugin launcher-death cleanup" check runs a copy of this launcher outside the project, as Codex's cache does, with no project-root hint, and kills it.

## Known Issues
- Finding the project is fragile. Codex runs the plugin from its cache with the plugin folder as the working folder, so unless Codex sets one of the variables above, only the home-folder guesses can find the project, and a checkout anywhere else leaves Biblio Tools missing from Codex. Backlog item "Codex plugin: a reliable way to find the project" (cross-review R3-1, 2026-11-24).
- Codex runs a cached copy of the plugin (under the user's Codex home), not this file. A change here reaches Codex only after the plugin is reinstalled or updated and Codex restarted; changes to `launch.py` or `server.py` reach it directly, since the cached launcher runs the project's copies.

## Revision History
- 2026-06-24 - Initial creation.
- 2026-11-24 - `launch_biblio.py` runs the project's `launch.py` in-process (`runpy`) instead of exec-ing `server.py`. Under `mcp` 2 the server's stdin watchdog is gone, and on Windows `os.execv` starts a new process whose parent, the venv launcher stub, outlives Codex, so a killed plugin launcher left the server running (cross-review R2-1, option A, the user's choice). The unused `_python_for` was removed. Then, from cross-review R3-1: Inputs now describe the project-root search as the code does it (it never looked five parents above the file), and Known Issues records that only the home-folder guesses find the project from Codex's cache.
