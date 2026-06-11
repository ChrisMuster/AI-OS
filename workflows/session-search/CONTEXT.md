# Session Search

**Last modified:** 2026-06-11

## Purpose
Indexes all Book Dragon conversation transcripts into a local SQLite FTS5 full-text search database and provides a skill for Biblio to search session history on demand. Fills the recall gap that memory files and LOG.md cannot cover: the raw conversational archive of every session, searchable by keyword, date, or source.

## Contents
- PROPOSAL.md — `workflows/session-search/PROPOSAL.md` [[workflows/session-search/PROPOSAL]] — Completed design document; approved and implemented. Kept for historical reference.
- scripts/ — `workflows/session-search/scripts/` [[workflows/session-search/scripts/CONTEXT]] — Python scripts implementing the archive, indexing, search, discovery, and background scheduling functionality.
- data/ — `workflows/session-search/data/` [[workflows/session-search/data/CONTEXT]] — Archive JSONL files (source of truth) and SQLite database shards. Gitignored; Google Drive-synced automatically.
- skills/session-search/ — `workflows/session-search/skills/session-search/` [[workflows/session-search/skills/session-search/CONTEXT]] — Biblio-invocable skill for searching session history.

## Inputs
- Claude Code session transcripts: `%USERPROFILE%\.claude\projects\<sanitized-cwd>\<session-uuid>.jsonl`
- Cowork session transcripts: `%APPDATA%\Claude\local-agent-mode-sessions\<orgId>\<sessionId>\local_<conv-uuid>\audit.jsonl`
- Codex CLI/Desktop session transcripts: `%USERPROFILE%\.codex\sessions\YYYY\MM\DD\rollout-<timestamp>-<uuid>.jsonl`
- Copilot CLI session transcripts: `%USERPROFILE%\.copilot\session-state\<uuid>\events.jsonl`
- Gemini CLI chat files: `~/.gemini/tmp/<project_hash>/chats/*.jsonl`
- Antigravity CLI transcript files: `~/.gemini/antigravity/brain/<id>/.system_generated/logs/transcript.jsonl`
- Continue.dev session files: `%USERPROFILE%\.continue\sessions\<uuid>.json`
- OpenCode SQLite database: `~/.local/share/opencode/opencode.db`
- Cursor agent transcripts: `%USERPROFILE%\.cursor\projects\*\agent-transcripts\*.jsonl`
- Cline task transcripts: `%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\tasks\<id>\api_conversation_history.json`
- Session metadata (titles): `%APPDATA%\Claude\claude-code-sessions\<orgId>\<sessionId>\local_<uuid>.json`

## Outputs
- `data/archive/<hostname>/<session-id>.jsonl` — Extracted session content in Book Dragon standard format. Source of truth; never deleted.
- `data/sessions-<hostname>.db` — SQLite FTS5 search index, one shard per machine. Rebuilt from archive on demand via `index.py --rebuild`.
- `data/index_state.json` — Per-file mtime tracking for idempotent re-indexing; not personal data.

## Steps
1. **Initial import (one time):** Run `python workflows/session-search/scripts/index.py` to discover existing Claude Code and Cowork sessions, archive them, and build the initial database.
2. **Ongoing (automated):** Session hooks call `archive.py` automatically at session end or idle events. Claude Code uses `--hook` (via `.claude/settings.json`); other AIs with hooks (Gemini CLI, Cursor, Windsurf/Devin Desktop, Cline, Codex) use `--all` via their respective config files.
3. **Hourly safety net:** Two mechanisms: Claude uses its MCP scheduled task; all other AIs use the Python background scheduler (`scripts/scheduler.py`), which is PID-guarded and auto-terminates after 4 hours of inactivity. Both run `archive.py --all` hourly.
4. **Session startup (automated):** `index.py` runs automatically at step 6 of the Claude session startup sequence (defined in `CLAUDE.md` [[CLAUDE]]), updating the archive and search index at the start of every session.
5. **Search:** Trigger Biblio's session-search skill with phrases such as "when did we talk about X" or "search our history for...".
6. **Adding a new AI source:** Run `discover.py <ai-name>` to inspect the new tool's data footprint and scaffold an adapter.
7. Append LOG.md with a completion or failure entry.

## Dependencies
- `.claude/settings.json` — Stop, PreCompact, and Notification (idle_prompt) hooks call `archive.py --hook` for Claude Code sessions.
- `.gemini/settings.json` — SessionEnd hook calls `archive.py --all` for Gemini CLI sessions.
- `.cursor/hooks.json` — sessionEnd hook calls `archive.py --all` for Cursor sessions.
- `.windsurf/hooks.json` — post_cascade_response hook calls `archive.py --all` for Windsurf/Devin Desktop sessions.
- `.clinerules/hooks/TaskComplete` — Executable hook script calls `archive.py --all` for Cline sessions.
- `.codex/config.toml` — Stop hook calls `archive.py --all` for Codex CLI/Desktop sessions.
- `scheduled-tasks` MCP — Claude-only hourly scheduled task runs `archive.py --all` while the Claude desktop app is open.
- `scripts/scheduler.py` — Python background scheduler for non-Claude AIs. Started at session startup (AGENTS.md step 6d), PID-guarded, auto-terminates after 4 hours of inactivity. Primary archiving mechanism for AIs without hooks (GitHub Copilot, Continue.dev, OpenCode, Aider).
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
- 2026-06-10 — Added AI identity (ai_identity) field to archive format, FTS5 schema, and search filters. Retroactive identity inferred from source field for pre-existing sessions. Auto-migration detects old schema and triggers rebuild (Phase 4, AI-agnostic transition).
- 2026-06-11 — Added session hooks for 6 non-Claude AIs (Gemini CLI, Cursor, Windsurf/Devin Desktop, Cline, Codex). Updated Steps and Dependencies to reflect multi-AI hook coverage. AIs without hooks (Copilot, Continue.dev, OpenCode, Aider) rely on background scheduler (Phase 5, Item 2).
- 2026-06-11 — Added scheduler.py — PID-guarded background scheduler running archive.py --all hourly. Started at AGENTS.md step 6d for non-Claude AIs. Auto-terminates after 4 hours of inactivity. Updated Steps, Contents, and Dependencies (Phase 5, Item 3).
- 2026-06-11 — Refactored archive.py to use adapter registry for all discovery and parsing (inline parsers removed). Added Codex CLI/Desktop adapter. archive.py now iterates all registered adapters in --all mode. Updated Inputs (Phase 5, Item 4).
- 2026-06-11 — Added 6 new adapters: Copilot CLI, Gemini CLI, Continue.dev, OpenCode, Cursor, Cline. Total: 9 adapters covering all AIs with local transcript storage. Updated Inputs. Two AIs have no adapter: Aider (git-based only) and Windsurf/Devin Desktop (no documented local storage).
