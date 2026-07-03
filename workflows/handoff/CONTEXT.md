# Handoff

**Last modified:** 2026-07-03

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

## Known Issues
- The trigger is natural language, so recognition is softer than a slash command
  or a hook. Mitigated by documented trigger phrases in `AGENTS.md` [[AGENTS]]; the procedure
  itself is script-backed and reliable once triggered.
- `HANDOVER.md` is a single rolling file: it holds only the most recent handoff.
  History is deliberately not kept (the weekly-review store and LOG.md already
  carry the durable record).

## Revision History
- 2026-07-03 - Initial creation. Gather script (run/gather/state/config), seen
  watermark, synthesis skill, hermetic test suite, and the rolling root
  `HANDOVER.md`. Best-practices umbrella Bucket-1 child #4 (session handoff +
  startup recovery).
- 2026-07-03 - Corrected stale `--record` wording to match the implemented
  `--seen` recovery flow and explicit unread verification after writing.
