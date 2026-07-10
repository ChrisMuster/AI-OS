# Session Search Skill

**Last modified:** 2026-07-09

## Purpose
Biblio-invocable skill for searching the Book Dragon session history archive. Wraps search.py with instructions for when to invoke it, how to interpret results, and what limitations apply.

## Contents
- SKILL.md - `workflows/session-search/skills/session-search/SKILL.md` [[workflows/session-search/skills/session-search/SKILL]] - Invocation spec: when to trigger, how to run, how to interpret results, and known limitations.

## Inputs
A user query phrase (natural language). Optionally: a date filter, source filter, or hostname filter.

## Outputs
A list of ranked result snippets from past sessions, including session title, date, source, role, and a short contextual excerpt. Biblio converts these into plain-prose recall responses.

## Steps
1. Trigger when the user asks to search or recall past conversations.
2. Run `python workflows/session-search/scripts/search.py "<query>" [options]`.
3. Read the returned snippets.
4. Respond in plain prose citing session title, date, source, and AI identity where available. Do not infer beyond what the snippets contain.

## Dependencies
- `workflows/session-search/scripts/search.py` [[workflows/session-search/scripts/CONTEXT]] - the underlying search command
- `data/sessions-<hostname>.db` - the FTS5 database this skill queries

## Known Issues
- FTS5 is keyword-based; fuzzy or conceptual queries may return no results.
- Cowork sessions may lag until the next scheduled index run or startup index pass.
- Results are limited to sessions that have been indexed; startup and scheduled indexing keep this fresh, but run index.py manually if results seem stale.

## Revision History
- 2026-06-08 - Initial creation.
- 2026-06-24 - Updated SKILL.md to describe all indexed AI sessions and the universal startup/scheduled indexing freshness model.
- 2026-07-09 - Added the required Verification and Hardening sections to SKILL.md (umbrella Bucket-1 child #6). Verification frames correctness as citing only returned snippets; Hardening documents the read-only-query envelope (local index write only via index.py).
