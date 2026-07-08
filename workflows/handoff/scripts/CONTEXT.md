# Handoff - Scripts

**Last modified:** 2026-07-07

## Purpose
The deterministic half of the handoff workflow: gather the session-state packet,
report whether an unread handoff exists, and mark one as seen.

## Contents
- run.py - entry point with three modes: `--gather` (print the briefing packet,
  default), `--status` (report unread handoff; `--json`), `--seen` (advance the
  seen watermark; `--dry-run`).
- gather.py - deterministic signal readers (branch, `git status`, `git diff
  --stat`, recent commits, changed-directory LOG tails, active backlog, recent
  session activity, and `doc_sync_drift` - the doc-sync CONTEXT/LOG drift check so
  a handoff surfaces any behind directory before HANDOVER.md is written) and the
  packet builder. Writes nothing.
- state.py - the seen-watermark: load/save state, read a handoff's `**Created:**`
  timestamp (mtime fallback), and decide whether a handoff is unread.
- config.py - tunable constants (recent-commit count, LOG tail length, session
  look-back, the HANDOVER.md filename).

## Inputs
The working tree (git), every project `LOG.md`, `memory/backlog.md`, the
session-search shards, and the rolling `HANDOVER.md` at the project root.

## Outputs
A briefing packet (stdout) and the seen watermark (`state.json`, gitignored). The
gather mode writes nothing; `--seen` writes state and a LOG entry.

## Steps
1. `--gather` assembles and prints the packet for the AI to write from.
2. `--status` reports whether an unread handoff exists (startup recovery).
3. `--seen` advances the watermark so a shown handoff is not surfaced again.
4. Append LOG.md with a completion or failure entry (on `--seen`).

## Dependencies
- `workflows/handoff/` [[workflows/handoff/CONTEXT]] - the parent workflow.
- `workflows/session-search/data/` [[workflows/session-search/data/CONTEXT]] - session
  shards read for recent activity.
- `workflows/doc-sync-guard/scripts/run.py` [[workflows/doc-sync-guard/scripts/CONTEXT]] -
  run read-only (default working-tree scope) by `doc_sync_drift` for the packet's
  CONTEXT/LOG drift section; degrades to an empty result if missing or broken.
- Python standard library only (subprocess, sqlite3, json, re, sys). No third-party
  packages.

## Known Issues
- Untracked new directories are reported by git as a single path, so the changed
  directory shown is the parent. Acceptable: the LOG tail still gives context.

## Revision History
- 2026-07-03 - Initial creation. run.py, gather.py, state.py, config.py.
- 2026-07-07 - gather.py gained `doc_sync_drift` (runs the doc-sync guard read-only
  at working-tree scope) and `build_packet` a "CONTEXT/LOG drift (doc-sync)"
  section, so a handoff surfaces any behind directory before HANDOVER.md is
  written; run.py `--gather` wires it in. Degrades to empty on any failure
  (doc-sync-guard build part 5).
