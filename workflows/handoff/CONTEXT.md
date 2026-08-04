# Handoff

**Last modified:** 2026-08-04

## Purpose
Clean session-to-session transitions for Book Dragon. When the user ends a working
session with more work still to do, a trigger phrase ("do the handoff") runs a
deterministic gather pass and Biblio writes a structured `HANDOVER.md` at the
project root. On the next session, a startup recovery step reads any unread
handoff and opens with an informed greeting so the new session knows where the
last one left off. The user always keeps the choice of what to work on.

There are no per-AI slash commands. Like the weekly-review flywheel, the work is a
deterministic gather script (the ~90%) plus an AI write step (the ~10%), triggered
by natural language documented in `AGENTS.md` [[AGENTS]], so it works identically on every
AGENTS-reading AI with no per-AI adapter. It is best-practices umbrella Bucket-1
child #4.

## Contents
- scripts/ - `workflows/handoff/scripts/` [[workflows/handoff/scripts/CONTEXT]]
  - run.py (gather / status / seen), gather.py (deterministic signal readers),
    state.py (the seen-watermark), config.py (tunable constants).
- skills/ - `workflows/handoff/skills/` [[workflows/handoff/skills/CONTEXT]]
  - the handoff synthesis skill (the AI half: writes HANDOVER.md from the packet).
- tests/ - `workflows/handoff/tests/` [[workflows/handoff/tests/CONTEXT]]
  - hermetic unit tests for the gather readers and the watermark logic.
- archived/ - `workflows/handoff/archived/` [[workflows/handoff/archived/CONTEXT]]
  - holds the gitignored HANDOFF-PLAN.md build plan; local-only (distinct from the
    rolling root HANDOVER.md, which is never archived).

## Inputs
- The working tree (git branch, status, diff), every project `LOG.md`, recent git
  commits, `memory/backlog.md`, and the session-search index. All are read by the
  gather script; no user flags are required.
- Biblio, to write the handoff document from the packet (see the skill).

## Outputs
- A briefing packet (stdout) for Biblio to write from.
- A single rolling `HANDOVER.md` at the project root (overwritten each handoff;
  gitignored via `**/HANDOVER.md`).
- `state.json` - the seen-watermark that records which handoff has been surfaced
  at startup (gitignored, machine-local).

## Steps
1. **Trigger (in-session):** the user asks for a handoff. Biblio runs
   `run.py --gather` to assemble the packet.
2. **Write:** Biblio writes `HANDOVER.md` from the packet, following the skill.
3. **Verify unread:** after writing, `run.py --status --json` should report the
   new handoff as unread so the next startup will surface it.
4. **Recovery (next startup):** the AGENTS.md startup sequence runs
   `run.py --status`; if an unread handoff exists it is surfaced in the greeting,
   then `run.py --seen` advances the watermark.
5. Append LOG.md with a completion or failure entry.

## Dependencies
- `workflows/handoff/scripts/` [[workflows/handoff/scripts/CONTEXT]] - the gather /
  status / seen entry point and its modules.
- `workflows/handoff/skills/handoff/` [[workflows/handoff/skills/handoff/CONTEXT]] -
  the synthesis skill this workflow relies on to write the handoff.
- `AGENTS.md` [[AGENTS]] - hosts the session-handoff trigger section and the
  startup recovery step.
- `memory/backlog.md` [[memory/CONTEXT]], `workflows/session-search/` [[workflows/session-search/CONTEXT]] - signal sources for the packet.
- `workflows/doc-sync-guard/` [[workflows/doc-sync-guard/CONTEXT]] - run read-only for the packet's CONTEXT/LOG drift section, so a handoff surfaces any behind directory before HANDOVER.md is written; degrades to empty if unavailable.

## Known Issues
- The trigger is natural language, so recognition is softer than a slash command
  or a hook. Mitigated by documented trigger phrases in `AGENTS.md` [[AGENTS]]; the procedure
  itself is script-backed and reliable once triggered.
- `HANDOVER.md` is a single rolling file: it holds only the most recent handoff.
  History is deliberately not kept (the weekly-review store and LOG.md already
  carry the durable record).
- **The packet's doc-sync section surfaces drift only.** `doc_sync_drift` keeps
  the guard's WARN findings, so a DEGRADED finding (a component of the guard that
  could not run, today its output-inventory probe on an interpreter without
  PyYAML) never reaches the packet. That is the intended boundary while nothing
  consumes the inventory answers: the section exists to name directories whose
  documentation is behind, and a missing package is not one. It stops being safe
  once the guard acts on the inventory, because the packet would then report a
  clean drift section on a machine that had silently skipped every gitignored
  directory, so the guard-coverage scope extension carries a locked decision
  requiring this reader to be made degrade-aware at that point, presenting the
  degrade on its own line rather than folding it into the drift list. The
  producer half of the boundary is recorded in
  `workflows/doc-sync-guard/scripts/CONTEXT.md` [[workflows/doc-sync-guard/scripts/CONTEXT]].

## Revision History
- 2026-07-03 - Initial creation. Gather script (run/gather/state/config), seen
  watermark, synthesis skill, hermetic test suite, and the rolling root
  `HANDOVER.md`. Best-practices umbrella Bucket-1 child #4 (session handoff +
  startup recovery).
- 2026-07-03 - Corrected stale `--record` wording to match the implemented
  `--seen` recovery flow and explicit unread verification after writing.
- 2026-07-07 - The gather packet now includes a "CONTEXT/LOG drift (doc-sync)"
  section (via the doc-sync guard, working-tree scope), so a handoff surfaces any
  directory whose CONTEXT.md / LOG.md is behind before HANDOVER.md is written
  (doc-sync-guard build part 5).
- 2026-07-10 - Task 3 hygiene sweep: archived the completed HANDOFF-PLAN.md build
  plan into a new `archived/` subdirectory, per the archive-plans-on-completion
  rule. No behaviour change.
- 2026-08-03 - Known Issues records the packet doc-sync section's severity
  boundary: `doc_sync_drift` keeps WARN only, so the guard's DEGRADED findings
  (its output-inventory probe on an interpreter without PyYAML) never reach the
  packet. Deliberate today, and required to change when the guard starts acting
  on the inventory. The constraint was recorded only on the producer side; this
  documents the consumer half where a reader of this workflow will find it. No
  behaviour change.
- 2026-08-04 - The same boundary recorded in `scripts/CONTEXT.md`, where the code
  that applies the filter lives (its Contents and Dependencies had described
  `doc_sync_drift` only as a drift surfacer that degrades to empty), and pinned by
  `TestDocSyncDriftSeverity` in the tests directory (24 -> 27 tests), so the
  documented boundary rests on a test rather than on a code read. No behaviour
  change.
