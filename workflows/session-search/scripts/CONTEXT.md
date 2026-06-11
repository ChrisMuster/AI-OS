# Session Search — Scripts

**Last modified:** 2026-06-11

## Purpose
Python scripts that implement the session-search workflow: archiving conversation transcripts, building the SQLite FTS5 search index, querying it, scaffolding adapters for new AI sources, and running the background archive scheduler.

## Contents
- archive.py — `workflows/session-search/scripts/archive.py` [[workflows/session-search/scripts/CONTEXT]] — Shared write layer. Iterates all registered adapters to discover and parse session files, then writes normalised records to the archive. Called by hooks (via `--hook`) and the scheduled task/scheduler (via `--all`).
- index.py — `workflows/session-search/scripts/index.py` [[workflows/session-search/scripts/CONTEXT]] — Calls archive.py then reads the archive and builds or updates the SQLite FTS5 database. Supports `--rebuild` for full database reconstruction.
- search.py — `workflows/session-search/scripts/search.py` [[workflows/session-search/scripts/CONTEXT]] — Queries all machine shards and returns merged, ranked results.
- discover.py — `workflows/session-search/scripts/discover.py` [[workflows/session-search/scripts/CONTEXT]] — Inspects a new AI tool's data footprint; either identifies a matching existing adapter or scaffolds a blank one.
- scheduler.py — `workflows/session-search/scripts/scheduler.py` [[workflows/session-search/scripts/CONTEXT]] — Background scheduler. Runs archive.py --all every hour in a loop. PID-file-guarded, auto-terminates after 4 hours of inactivity. Used by non-Claude AIs (Claude uses its MCP scheduled task). Started at AGENTS.md step 6d.
- adapters/ — `workflows/session-search/scripts/adapters/` [[workflows/session-search/scripts/adapters/CONTEXT]] — Session transcript adapters for 9 AI sources (claude-code, cowork, codex, copilot, gemini-cli, continue-dev, opencode, cursor, cline).

## Inputs
- Claude Code JSONL cache files (read-only)
- Cowork audit.jsonl files (read-only)
- Claude Code session metadata JSON files (read-only, for session titles)
- Codex session JSONL files (read-only, from `~/.codex/sessions/`)
- Copilot CLI events JSONL files (read-only, from `~/.copilot/session-state/`)
- Gemini CLI chat files (read-only, from `~/.gemini/tmp/`)
- Continue.dev session JSON files (read-only, from `~/.continue/sessions/`)
- OpenCode SQLite database (read-only, from `~/.local/share/opencode/`)
- Cursor agent transcript JSONL files (read-only, from `~/.cursor/projects/`)
- Cline task JSON files (read-only, from VS Code globalStorage)
- Hook context JSON (stdin when called with `--hook`)

## Outputs
- `data/archive/<hostname>/<session-id>.jsonl` — normalised session content
- `data/sessions-<hostname>.db` — SQLite FTS5 search database
- `data/index_state.json` — mtime state tracker for idempotency
- Scaffolded adapter files in `adapters/` when `discover.py` creates a new one

## Steps
N/A. Scripts are invoked individually; see each script's module docstring for usage.

## Dependencies
- Python 3.9+ standard library (json, sqlite3, pathlib, socket, argparse, subprocess)
- `scripts/adapters/` — archive.py delegates AI-specific parsing to adapter modules
- `.claude/settings.json` — hooks defined there invoke archive.py at session events

## Known Issues
- `archive.py` ignores `transcript_path` from Claude Code hook data due to a known stale-path bug (GitHub #8564); uses mtime-based discovery instead.
- Cowork sessions cannot use hooks (GitHub #40495); archive.py in `--all` mode handles them via discovery.
- All paths are derived from `Path(__file__)` at runtime — safe for all invocation contexts.

## Revision History
- 2026-06-08 — Initial creation.
- 2026-06-11 — Added scheduler.py for background hourly archiving (non-Claude AIs).
- 2026-06-11 — Refactored archive.py to use adapter registry (inline parsers removed). Added Codex adapter to registry.
