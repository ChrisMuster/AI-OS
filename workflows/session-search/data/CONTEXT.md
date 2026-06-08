# Session Search — Data

**Last modified:** 2026-06-08

## Purpose
Stores all session-search personal data: the raw JSONL archive of every archived conversation (source of truth) and the SQLite FTS5 search database shards. All contents are gitignored and Google Drive-synced automatically as part of the project folder.

## Contents
- archive/ — Created on first run by archive.py. Per-machine subdirectories following the naming pattern `archive/<hostname>/<session-id>.jsonl`. Each file holds all messages from one archived session in Book Dragon standard format. Individual files are not listed here as they are personal conversation data.
- sessions-\<hostname\>.db — SQLite FTS5 search index for one machine (e.g. `sessions-DESKTOP-XXXXX.db`). One shard per machine; all shards are queried by search.py. Created by index.py on first run.
- index_state.json — Tracks the last-processed mtime for each source file; used by archive.py and index.py for idempotent re-runs. Machine-specific; not personal conversation data.

## Inputs
None. Content is written here by archive.py and index.py.

## Outputs
None. This is a storage directory read by search.py and index.py.

## Steps
N/A. This is a data directory, not a workflow itself.

## Dependencies
- `workflows/session-search/scripts/archive.py` [[workflows/session-search/scripts/CONTEXT]] — writes to archive/
- `workflows/session-search/scripts/index.py` [[workflows/session-search/scripts/CONTEXT]] — creates and updates the .db shard
- `workflows/session-search/scripts/search.py` [[workflows/session-search/scripts/CONTEXT]] — reads from the .db shard

## Known Issues
- This directory and all its contents are gitignored (except this CONTEXT.md and .gitkeep). On a fresh clone, only the empty skeleton exists. The archive and database are rebuilt by running index.py.
- archive/ subdirectories and .jsonl files are not listed here — they are personal data and grow indefinitely. The filesystem is the authoritative source.
- If a shard is corrupted, run: `python workflows/session-search/scripts/index.py --rebuild`

## Revision History
- 2026-06-08 — Initial creation.
