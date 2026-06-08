# Session Search — Design Proposal

**Status:** Approved and implemented  
**Date approved:** 2026-06-08  
**Implemented in:** `workflows/session-search/`

---

## Overview

Session Search gives Book Dragon a full conversational memory — a searchable, persistent index of every session ever held with Biblio. It fills the gap that memory files and LOG.md cannot cover: the ability to search the raw text of past conversations by keyword, date, or source.

---

## Core Architecture

### Archive-first design

The archive is the source of truth. The SQLite full-text index is always derived from the archive and can be rebuilt from it at any time. This means:

- Sessions are never lost due to database corruption.
- The archive is the permanent record; the database is just the search interface.
- Recovery from any database problem is a single `--rebuild` command.

### No intermediate transcript copies

Sessions are read directly from their existing locations in the AI tool's caches. Nothing is copied into the project folder first. The only data written to the project is:

1. The normalised `data/archive/<hostname>/<session-id>.jsonl` files — one per session, in the Book Dragon standard format.
2. The SQLite shard `data/sessions-<hostname>.db` — rebuilt from the archive on demand.

### Multi-machine design

Each machine produces its own archive subdirectory and its own database shard, named by hostname. All shards live inside `data/` which is gitignored but Google Drive-synced. The search script queries all shards and merges results, so a search covers sessions from every machine.

---

## Data Flow

```
Claude Code JSONL cache       ──► archive.py ──► data/archive/<host>/<session-id>.jsonl
Cowork audit.jsonl files      ──►                         │
                                                          ▼
                                               index.py ──► data/sessions-<host>.db
                                                          │
                                                          ▼
                                              search.py ──► results to Biblio skill
```

### Adapters (one-time import only)

Adapters in `scripts/adapters/` are used once: at initial historical import and when adding a new AI source. They read AI-specific cache formats and pass normalised records to `archive.py`. After import, hooks take over permanently.

- `claude_code.py` — reads `%USERPROFILE%\.claude\projects\<sanitized-cwd>\<session-uuid>.jsonl`
- `cowork.py` — reads `%APPDATA%\Claude\local-agent-mode-sessions\<orgId>\<sessionId>\local_<conv-uuid>\audit.jsonl`

### Hooks (ongoing input — all AIs)

All ongoing session capture flows through hooks calling `archive.py`. This is the universal input path — it does not depend on which AI is in use.

Three hooks are configured in `.claude/settings.json`:

| Hook | When it fires |
|------|--------------|
| `Stop` | When the session closes — Biblio waits for this to complete before the window closes |
| `PreCompact` | Before Claude compacts the context — captures a snapshot before compression |
| `Notification (idle_prompt)` | When Claude detects an idle session |

Additionally, an MCP-based scheduled task fires `archive.py --all` hourly while the Claude desktop app is running, providing a safety net for both idle Claude Code sessions and Cowork sessions.

---

## Archive Format

One `.jsonl` file per session, written to `data/archive/<hostname>/`. Each line is one message:

```json
{
  "role": "user",
  "content": "message text here",
  "timestamp": "2026-06-08T14:22:00Z",
  "session_id": "abc123def456",
  "session_title": "Session search workflow design",
  "source": "claude-code",
  "hostname": "DESKTOP-XXXXX"
}
```

Fields:
- `role` — `"user"` or `"assistant"` only. Tool calls, tool results, and system messages are excluded.
- `content` — plain text only. Text blocks extracted from structured content arrays.
- `timestamp` — ISO 8601. Sourced from `timestamp` (Claude Code) or `_audit_timestamp` (Cowork).
- `session_id` — unique per session, sourced from `sessionId` (Claude Code) or `session_id` (Cowork).
- `session_title` — auto-generated title from `claude-code-sessions` metadata where available.
- `source` — `"claude-code"` or `"cowork"` (extensible for future AI sources).
- `hostname` — machine name for multi-machine disambiguation.

---

## Database Schema

SQLite FTS5 virtual table, one `.db` per machine:

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS sessions USING fts5(
    hostname,
    source,
    session_id,
    session_title,
    timestamp,
    role,
    content
);

CREATE TABLE IF NOT EXISTS session_meta (
    session_id TEXT PRIMARY KEY,
    hostname TEXT,
    source TEXT,
    session_title TEXT,
    first_timestamp TEXT,
    last_timestamp TEXT,
    message_count INTEGER
);
```

---

## Directory Structure

```
workflows/session-search/
├── PROPOSAL.md               — this document
├── CONTEXT.md
├── LOG.md
├── data/                     — gitignored; Google Drive-synced
│   ├── archive/
│   │   └── <hostname>/
│   │       └── <session-id>.jsonl    — source of truth
│   ├── sessions-<hostname>.db        — FTS5 search index
│   └── index_state.json              — mtime tracking for idempotent re-runs
├── scripts/
│   ├── CONTEXT.md
│   ├── archive.py            — shared write layer; called by hooks and adapters
│   ├── index.py              — reads archive → builds/updates database; called at session startup
│   ├── search.py             — multi-shard query, merge, rank, output
│   ├── discover.py           — new AI source discovery and adapter scaffolding tool
│   └── adapters/
│       ├── CONTEXT.md
│       ├── __init__.py
│       ├── _base.py          — abstract base class (TranscriptAdapter interface)
│       ├── _registry.py      — adapter registry
│       ├── claude_code.py    — Claude Code historical import adapter
│       └── cowork.py         — Cowork historical import adapter
└── skills/
    └── session-search/
        ├── CONTEXT.md
        └── SKILL.md          — Biblio invocation spec
```

---

## Scripts Reference

### `archive.py`

Shared write layer. Reads from AI source caches and writes normalised records to `data/archive/`.

```
python workflows/session-search/scripts/archive.py [--all] [--hook] [--dry-run]
```

- `--all` — scan for all new or updated sessions and archive them (default behaviour)
- `--hook` — called from a Claude Code hook; reads session context from stdin
- `--dry-run` — print what would be done without writing anything

Detects source type via `CLAUDE_CODE_IS_COWORK=1` environment variable when in hook mode.  
Known issue workaround: ignores the `transcript_path` from hook data (stale due to GitHub #8564); finds the most recently modified `.jsonl` in the project cache directory instead.

### `index.py`

Builds and maintains the SQLite FTS5 database from the archive.

```
python workflows/session-search/scripts/index.py [--rebuild] [--dry-run]
```

- `--rebuild` — drop the existing database and rebuild from the full archive
- `--dry-run` — print what would be done without writing anything

Calls `archive.py --all` first to capture any sessions not yet in the archive, then indexes everything new or updated in the archive. Recommended to run at session startup via CLAUDE.md.

### `search.py`

Queries all shards and returns ranked results.

```
python workflows/session-search/scripts/search.py "query" [--limit N] [--since YYYY-MM-DD] [--source SOURCE] [--hostname HOSTNAME]
```

### `discover.py`

Inspects a new AI tool's data footprint and either identifies which existing adapter to use or scaffolds a blank adapter.

```
python workflows/session-search/scripts/discover.py <ai-name>
```

---

## Hook Configuration

Added to `.claude/settings.json`:

```json
"hooks": {
  "Stop": [
    {
      "matcher": "",
      "hooks": [
        {
          "type": "command",
          "command": "python \"%CLAUDE_PROJECT_DIR%\\workflows\\session-search\\scripts\\archive.py\" --hook"
        }
      ]
    }
  ],
  "PreCompact": [
    {
      "matcher": "",
      "hooks": [
        {
          "type": "command",
          "command": "python \"%CLAUDE_PROJECT_DIR%\\workflows\\session-search\\scripts\\archive.py\" --hook"
        }
      ]
    }
  ],
  "Notification": [
    {
      "matcher": "idle_prompt",
      "hooks": [
        {
          "type": "command",
          "command": "python \"%CLAUDE_PROJECT_DIR%\\workflows\\session-search\\scripts\\archive.py\" --hook"
        }
      ]
    }
  ]
}
```

---

## Scheduled Task

An MCP-based scheduled task runs `archive.py --all` hourly while the Claude desktop app is open. This handles:

- Cowork sessions (which cannot trigger hooks — GitHub Issue #40495)
- Idle Claude Code sessions where the idle_prompt hook has not fired
- Any sessions missed by hooks for any reason

Maximum data loss window: 1 hour. The archive is restored to exactly its last known state at every hourly run.

---

## Corruption Recovery

If `data/sessions-<hostname>.db` is corrupted or lost:

```
python workflows/session-search/scripts/index.py --rebuild
```

This drops the database and rebuilds it from the full archive. The archive itself is gitignored but Google Drive-synced, so it is protected by Google Drive version history independently of git.

---

## Source Detection

When `archive.py` is called from a hook, it checks `CLAUDE_CODE_IS_COWORK` in the environment:

- `CLAUDE_CODE_IS_COWORK=1` → Cowork mode: scan Cowork audit files
- Not set → Claude Code mode: find the most recently modified `.jsonl` in the project's Claude Code cache

When the scheduled task runs `archive.py --all`, it scans both sources regardless.

---

## Adding a New AI Source

1. Run `python workflows/session-search/scripts/discover.py <ai-name>` to inspect the new tool's data footprint.
2. If a blank adapter is scaffolded, fill in `discover()` and `parse()` in the new adapter module.
3. Register the adapter in `scripts/adapters/_registry.py`.
4. Run `python workflows/session-search/scripts/index.py --rebuild` to import the historical data.
5. Wire hooks for the new AI to call `archive.py --hook` at session end.

---

## Known Limitations

- **Cowork hooks not supported** — GitHub Issue #40495. Cowork sessions are captured by the scheduled task and the startup indexer, not in real time. Always prefer Claude Code for reliable archiving.
- **`transcript_path` stale bug** — GitHub Issue #8564. Workaround in place: ignore the hook-provided path and find the latest `.jsonl` by modification time instead.
- **FTS5 is keyword-based** — not semantic. Fuzzy or conceptual queries will not match unless the exact words appear in the transcript. This is a known limitation of the chosen storage approach.
- **CLAUDE.md startup wiring** — `index.py` runs automatically at step 6 of the session startup sequence. The hourly scheduled task is created automatically by the first-run initialisation procedure (first-run step 6) on any fresh clone.

---

## Design Decisions Record

| Decision | Rationale |
|----------|-----------|
| Archive-first, not database-first | Archive is portable and never lost; database is just an index |
| No intermediate transcript copies | Avoids bloat; reads directly from existing AI caches |
| Adapters for one-time import only | Hooks are the universal ongoing input path; adapters only needed on first run or when adding a new AI |
| `archive.py` as shared write layer | Single place that writes to the archive; consistent format regardless of source |
| SQLite FTS5 over a vector database | Sufficient for keyword search; no additional infrastructure required |
| One shard per machine | Clean multi-machine separation; Google Drive syncs all shards |
| No separate backup mirror | Archive on Google Drive with version history is sufficient; `--rebuild` handles any database failure |
| Session-end hook built in v1 | Automation is the goal; no manual steps |
| No summarise pass | Raw content preserved; summaries can be generated on demand by Biblio |
| Cowork recommended-but-second-class | Document it honestly; recommend Claude Code for reliability |
