# Handoff - Scripts

**Last modified:** 2026-08-27

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
  a handoff surfaces any behind directory before HANDOVER.md is written, keeping
  the guard's WARN findings only, and `review_packets` - the open findings of any
  review round part-way through, read from `memory/*_review_packet.md`, reporting
  `None` rather than an empty list when a packet's shape is not recognised) and the
  packet builder. Writes nothing.
- state.py - the seen-watermark: load/save state, read a handoff's `**Created:**`
  timestamp (mtime fallback), and decide whether a handoff is unread.
- config.py - tunable constants (recent-commit count, LOG tail length, session
  look-back, the HANDOVER.md filename).

## Inputs
The working tree (git), every project `LOG.md`, `memory/backlog.md`, any
`memory/*_review_packet.md`, the session-search shards, and the rolling
`HANDOVER.md` at the project root.

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
  CONTEXT/LOG drift section. Only the guard's WARN findings are kept; a missing or
  broken guard, and any finding at another severity, produce an empty result.
- Python standard library only (subprocess, sqlite3, json, re, sys). No third-party
  packages.

## Known Issues
- Untracked new directories are reported by git as a single path, so the changed
  directory shown is the parent. Acceptable: the LOG tail still gives context.
- `doc_sync_drift` keeps `severity == "WARN"` findings only, so the guard's
  DEGRADED severity (a component of the guard that could not run, today its
  output-inventory probe on an interpreter without PyYAML) never reaches the
  packet. That is the intended boundary while nothing consumes the inventory
  answers - the section names directories needing a documentation update, and a
  missing package is not one - but it means a handoff written on such a machine
  reports nothing about it. It stops being safe once the guard acts on the
  inventory, and the guard-coverage scope extension carries a locked decision
  requiring this function to be made degrade-aware at that point, presenting the
  degrade on its own line rather than folding it into the drift list. The
  producer half of the boundary is recorded in
  `workflows/doc-sync-guard/scripts/CONTEXT.md` [[workflows/doc-sync-guard/scripts/CONTEXT]].

## Revision History
- 2026-07-03 - Initial creation. run.py, gather.py, state.py, config.py.
- 2026-07-07 - gather.py gained `doc_sync_drift` (runs the doc-sync guard read-only
  at working-tree scope) and `build_packet` a "CONTEXT/LOG drift (doc-sync)"
  section, so a handoff surfaces any behind directory before HANDOVER.md is
  written; run.py `--gather` wires it in. Degrades to empty on any failure
  (doc-sync-guard build part 5).
- 2026-08-04 - Documented `doc_sync_drift`'s severity filter where the code that
  applies it lives: it keeps WARN only, so the guard's DEGRADED findings are
  invisible in the packet. Deliberate today, and required to change when the
  guard starts acting on the output inventory. Contents, Dependencies and Known
  Issues updated; no behaviour change.
- 2026-08-04 - Line endings pinned on both text writes (the LOG.md append in `run.py` and the `state.json` save in `state.py`), which now pass `newline="\n"` explicitly. Part of the project-wide pass closing this defect class at all 48 write sites.
- 2026-08-27 - gather.py gained `review_packets` and `build_packet` an "Open review findings" section, wired into `--gather` by run.py, so a handoff reports how many findings of a review round are still open and where that round's packet is. It reads `memory/*_review_packet.md`, collecting `R<n>` tags under the `## Open` and `## Addressed` headings, and matches both shapes the packets use: a `### R4 - ...` heading and a `- **R1 - ...**` bullet. Two design points are load-bearing rather than incidental. It returns `None` for a section that is absent, distinct from `[]` for a section that is present and empty, so a packet whose shape has drifted reports as unreadable instead of as zero open findings, which would read as nothing left to do; this is the same rule `allowlist.py` follows in never letting an unanswered question render as an empty answer. And it reports without ever gating: the user rejected the stronger form, a handoff refused while findings are open, because crossing a session boundary with work outstanding is exactly what a handoff is for, so refusing one would make a review round impossible to continue. Degrades to `[]` on a missing `memory/` directory and skips an unreadable file, matching the other readers.
