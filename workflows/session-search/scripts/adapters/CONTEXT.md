# Session Search — Adapters

**Last modified:** 2026-06-08

## Purpose
One-time historical import adapters for each AI source supported by session-search. Each adapter reads transcripts from an AI tool's specific cache format and yields normalised records in the Book Dragon archive format. After the initial import, adapters are only needed when adding a new AI source or rebuilding the database from scratch.

## Contents
- __init__.py — `workflows/session-search/scripts/adapters/__init__.py` [[workflows/session-search/scripts/adapters/CONTEXT]] — Package initialiser.
- _base.py — `workflows/session-search/scripts/adapters/_base.py` [[workflows/session-search/scripts/adapters/CONTEXT]] — Abstract base class (TranscriptAdapter interface); all adapters must implement discover(), validate(), and parse().
- _registry.py — `workflows/session-search/scripts/adapters/_registry.py` [[workflows/session-search/scripts/adapters/CONTEXT]] — Maps source labels to adapter instances; updated when a new AI source is added.
- claude_code.py — `workflows/session-search/scripts/adapters/claude_code.py` [[workflows/session-search/scripts/adapters/CONTEXT]] — Historical import adapter for Claude Code session JSONL files.
- cowork.py — `workflows/session-search/scripts/adapters/cowork.py` [[workflows/session-search/scripts/adapters/CONTEXT]] — Historical import adapter for Cowork audit.jsonl files.

## Inputs
- Claude Code JSONL cache files (read-only)
- Cowork audit.jsonl files (read-only)

## Outputs
None directly. Adapters yield normalised record dicts; the caller (archive.py) writes them to data/archive/.

## Steps
N/A. Adapters are called programmatically by archive.py during --all discovery runs, not invoked directly.

## Dependencies
- `_base.py` — all adapters inherit from TranscriptAdapter defined here
- Python 3.9+ standard library only (json, socket, pathlib)

## Known Issues
- Cowork does not support real-time hooks (GitHub Issue #40495). The cowork adapter is used for one-time import and periodic scheduled-task runs only. Claude Code is recommended for reliable real-time archiving.
- `_extract_text` is defined in claude_code.py and re-imported by cowork.py to avoid duplication. If claude_code.py is removed, cowork.py must define its own copy.
- Adding a new adapter requires manually updating _registry.py and re-running index.py --rebuild to import historical data.

## Revision History
- 2026-06-08 — Initial creation.
