# Session Search

**Last modified:** 2026-06-08

## Purpose
Indexes all Book Dragon conversation transcripts into a local SQLite FTS5 full-text search database and provides a skill for Biblio to search session history on demand. Fills the recall gap that memory files and LOG.md cannot cover: the raw conversational archive of every session, searchable by keyword, date, or source.

## Contents
- PROPOSAL.md — `workflows/session-search/PROPOSAL.md` [[workflows/session-search/PROPOSAL]] — Completed design document; approved and implemented. Kept for historical reference.
- scripts/ — `workflows/session-search/scripts/` [[workflows/session-search/scripts/CONTEXT]] — Python scripts implementing the archive, indexing, search, and discovery functionality.
- data/ — `workflows/session-search/data/` [[workflows/session-search/data/CONTEXT]] — Archive JSONL files (source of truth) and SQLite database shards. Gitignored; Google Drive-synced automatically.
- skills/session-search/ — `workflows/session-search/skills/session-search/` [[workflows/session-search/skills/session-search/CONTEXT]] — Biblio-invocable skill for searching session history.

## Inputs
- Claude Code session transcripts: `%USERPROFILE%\.claude\projects\<sanitized-cwd>\<session-uuid>.jsonl`
- Cowork session transcripts: `%APPDATA%\Claude\local-agent-mode-sessions\<orgId>\<sessionId>\local_<conv-uuid>\audit.jsonl`
- Session metadata (titles): `%APPDATA%\Claude\claude-code-sessions\<orgId>\<sessionId>\local_<uuid>.json`

## Outputs
- `data/archive/<hostname>/<session-id>.jsonl` — Extracted session content in Book Dragon standard format. Source of truth; never deleted.
- `data/sessions-<hostname>.db` — SQLite FTS5 search index, one shard per machine. Rebuilt from archive on demand via `index.py --rebuild`.
- `data/index_state.json` — Per-file mtime tracking for idempotent re-indexing; not personal data.

## Steps
1. **Initial import (one time):** Run `python workflows/session-search/scripts/index.py` to discover existing Claude Code and Cowork sessions, archive them, and build the initial database.
2. **Ongoing (automated):** Stop, PreCompact, and Notification (idle_prompt) hooks in `.claude/settings.json` call `archive.py --hook` automatically at session end and idle events.
3. **Hourly safety net:** MCP scheduled task runs `archive.py --all` to catch Cowork sessions and idle Claude Code sessions.
4. **Session startup (automated):** `index.py` runs automatically at step 6 of the Claude session startup sequence (defined in `CLAUDE.md` [[CLAUDE]]), updating the archive and search index at the start of every session.
5. **Search:** Trigger Biblio's session-search skill with phrases such as "when did we talk about X" or "search our history for...".
6. **Adding a new AI source:** Run `discover.py <ai-name>` to inspect the new tool's data footprint and scaffold an adapter.
7. Append LOG.md with a completion or failure entry.

## Dependencies
- `.claude/settings.json` — Stop, PreCompact, and Notification (idle_prompt) hooks configured here call `archive.py --hook`.
- `scheduled-tasks` MCP — Hourly scheduled task runs `archive.py --all` while the Claude desktop app is open.
- `CLAUDE.md` [[CLAUDE]] — Claude-specific session maintenance: step 6b calls `index.py` automatically; step 6a checks for and creates the hourly scheduled task on any machine where it is missing. First-run step 6 also creates it on a fresh clone.
- Python 3.9+ with standard library only (sqlite3 with FTS5 is included in CPython builds on Windows).
- `templates/` [[templates/CONTEXT]] — Standard CONTEXT.md and LOG.md templates used during scaffolding.

## Known Issues
- **Cowork hooks not supported** — Platform limitation (GitHub Issue #40495). Cowork sessions are captured by the scheduled task (hourly) and `index.py` at startup. Claude Code is recommended over Cowork for reliable real-time archiving.
- **`transcript_path` stale bug in Claude Code Stop hook** — GitHub Issue #8564. Workaround: `archive.py` ignores the hook-provided path and finds the latest `.jsonl` by modification time instead.
- **FTS5 is keyword-based** — Not semantic. Conceptual or fuzzy queries will not match unless the exact words appear in the transcript.
- **Scheduled task creation** — The `session-search-archive` scheduled task is created automatically in two places: by first-run initialisation (CLAUDE.md first-run step 6) on a fresh clone, and by regular session startup step 6a on any machine where it is not yet present (e.g. a Google Drive transfer where first-run init does not trigger). The task command must use **forward slashes** in the absolute path so it matches the `settings.json` allowlist pattern `Bash(python *workflows/session-search/scripts/archive.py*)` and runs without a permission prompt.
- **`data/` contents are personal data** — Archive files and database shards are not listed in this CONTEXT.md. The filesystem is the authoritative source; read `data/archive/<hostname>/` directly when needed.

## Revision History
- 2026-06-08 — Initial creation.
- 2026-06-08 — Wired index.py into CLAUDE.md session startup (step 6) and scheduled task creation into first-run init (step 6). Updated Steps, Dependencies, and Known Issues accordingly.
- 2026-06-08 — Updated Dependencies and Known Issues to reflect scheduled task check in regular startup (step 6a) as well as first-run init. Fixed personal-data language in Known Issues.
- 2026-06-08 — Fixed scheduled task permission prompt: SKILL.md updated to use forward-slash path, settings.json allowlist broadened to wildcard pattern covering both relative (hooks) and absolute (scheduled task) invocations.
- 2026-06-09 — Clarified CLAUDE.md dependency as Claude-specific session maintenance (AI-agnostic transition).
