# Session Search — Adapters

**Last modified:** 2026-06-12

## Purpose
Session transcript adapters for each AI source supported by session-search. Each adapter reads transcripts from an AI tool's specific cache format and yields normalised records in the Book Dragon archive format. Adapters are used by archive.py for both ongoing archiving (--all mode iterates all registered adapters) and historical imports.

## Contents
- __init__.py — `workflows/session-search/scripts/adapters/__init__.py` [[workflows/session-search/scripts/adapters/CONTEXT]] — Package initialiser.
- _base.py — `workflows/session-search/scripts/adapters/_base.py` [[workflows/session-search/scripts/adapters/CONTEXT]] — Abstract base class (TranscriptAdapter interface); all adapters must implement discover(), validate(), and parse().
- _registry.py — `workflows/session-search/scripts/adapters/_registry.py` [[workflows/session-search/scripts/adapters/CONTEXT]] — Maps source labels to adapter instances; updated when a new AI source is added.
- claude_code.py — `workflows/session-search/scripts/adapters/claude_code.py` [[workflows/session-search/scripts/adapters/CONTEXT]] — Historical import adapter for Claude Code session JSONL files.
- cowork.py — `workflows/session-search/scripts/adapters/cowork.py` [[workflows/session-search/scripts/adapters/CONTEXT]] — Adapter for Cowork audit.jsonl files.
- codex.py — `workflows/session-search/scripts/adapters/codex.py` [[workflows/session-search/scripts/adapters/CONTEXT]] — Adapter for Codex CLI/Desktop session JSONL files stored in `~/.codex/sessions/`.
- copilot.py — `workflows/session-search/scripts/adapters/copilot.py` [[workflows/session-search/scripts/adapters/CONTEXT]] — Adapter for GitHub Copilot CLI sessions stored in `~/.copilot/session-state/<uuid>/events.jsonl`.
- gemini_cli.py — `workflows/session-search/scripts/adapters/gemini_cli.py` [[workflows/session-search/scripts/adapters/CONTEXT]] — Adapter for Gemini CLI and Antigravity CLI sessions. Checks both `~/.gemini/tmp/<hash>/chats/` (Gemini CLI) and `~/.gemini/antigravity/brain/` (Antigravity CLI).
- continue_dev.py — `workflows/session-search/scripts/adapters/continue_dev.py` [[workflows/session-search/scripts/adapters/CONTEXT]] — Adapter for Continue.dev VS Code extension sessions stored in `~/.continue/sessions/<uuid>.json`.
- opencode.py — `workflows/session-search/scripts/adapters/opencode.py` [[workflows/session-search/scripts/adapters/CONTEXT]] — Adapter for OpenCode sessions stored in SQLite at `~/.local/share/opencode/opencode.db`.
- cursor.py — `workflows/session-search/scripts/adapters/cursor.py` [[workflows/session-search/scripts/adapters/CONTEXT]] — Adapter for Cursor agent transcripts stored in `~/.cursor/projects/*/agent-transcripts/*.jsonl`.
- cline.py — `workflows/session-search/scripts/adapters/cline.py` [[workflows/session-search/scripts/adapters/CONTEXT]] — Adapter for Cline VS Code extension task transcripts stored in `globalStorage/saoudrizwan.claude-dev/tasks/<id>/`.

## Inputs
- Claude Code JSONL cache files (read-only)
- Cowork audit.jsonl files (read-only)
- Codex session JSONL files (read-only, from `~/.codex/sessions/`)
- Copilot CLI events JSONL files (read-only, from `~/.copilot/session-state/`)
- Gemini CLI chat files (read-only, from `~/.gemini/tmp/`)
- Antigravity CLI transcript files (read-only, from `~/.gemini/antigravity/brain/`)
- Continue.dev session JSON files (read-only, from `~/.continue/sessions/`)
- OpenCode SQLite database (read-only, from `~/.local/share/opencode/`)
- Cursor agent transcript JSONL files (read-only, from `~/.cursor/projects/`)
- Cline task JSON files (read-only, from VS Code globalStorage)

## Outputs
None directly. Adapters yield normalised record dicts; the caller (archive.py) writes them to data/archive/.

## Steps
N/A. Adapters are called programmatically by archive.py (both --all and --hook modes iterate the adapter registry), not invoked directly.

## Dependencies
- `_base.py` — all adapters inherit from TranscriptAdapter defined here
- Python 3.9+ standard library only (json, socket, pathlib, sqlite3, hashlib, os)

## Known Issues
- Cowork does not support real-time hooks (GitHub Issue #40495). The cowork adapter is used for one-time import and periodic scheduled-task runs only. Claude Code is recommended for reliable real-time archiving.
- `_extract_text` is defined in claude_code.py and re-imported by cowork.py to avoid duplication. If claude_code.py is removed, cowork.py must define its own copy.
- Adding a new adapter requires manually updating _registry.py and re-running index.py --rebuild to import historical data.

## Revision History
- 2026-06-08 — Initial creation.
- 2026-06-11 — Added Codex CLI/Desktop adapter (codex.py). archive.py refactored to use adapter registry for all discovery and parsing (inline parsers removed). Adapters are now the primary parse layer, not just historical import tools.
- 2026-06-11 — Added 6 new adapters: Copilot CLI (copilot.py), Gemini CLI (gemini_cli.py), Continue.dev (continue_dev.py), OpenCode (opencode.py), Cursor (cursor.py), Cline (cline.py). Total adapters: 9. Two AIs have no adapter: Aider (git-based, no transcript files) and Windsurf/Devin Desktop (no documented local transcript storage).
- 2026-06-11 — Updated gemini_cli.py to also discover Antigravity CLI sessions at `~/.gemini/antigravity/brain/`. Gemini CLI is sunsetting June 18 2026; Antigravity CLI is the replacement.
- 2026-06-12 — Normalised the Last modified field to the required date-only format.
