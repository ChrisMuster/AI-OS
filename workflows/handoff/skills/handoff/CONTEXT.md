# Handoff Skill

**Last modified:** 2026-08-27

## Purpose
The AI half of the handoff workflow: turn the deterministic gather packet into a
structured `HANDOVER.md` that lets the next session resume cleanly.

## Contents
- SKILL.md - the functional spec: when to use, the required HANDOVER.md structure
  (including the `**Created:**` line the watermark depends on), and how to verify.

## Inputs
The gather packet (`run.py --gather`) and the current session's own context.

## Outputs
`HANDOVER.md` at the project root (rolling, overwritten, gitignored).

## Steps
See SKILL.md. In brief: run `--gather`, read the packet, write HANDOVER.md with
the required sections, then verify with `--status --json`.

## Dependencies
- `workflows/handoff/scripts/` [[workflows/handoff/scripts/CONTEXT]] - the gather
  packet and the status/watermark logic.
- `AGENTS.md` [[AGENTS]] - the session-handoff trigger section and the startup
  recovery step that reads the finished document.

## Known Issues
- The trigger is natural language, so the skill only runs when the user asks; there
  is no automatic end-of-session handoff (deliberate).

## Revision History
- 2026-07-03 - Initial creation. SKILL.md on the templates/SKILL.md.template schema.
- 2026-07-03 - Made the user's trigger-time steer ("keep this in mind", "this is where I'm going next session") a first-class input: captured verbatim in a leading "Steer for next session" section, omitted when there is no steer.
- 2026-07-09 - Added the required Hardening section to SKILL.md (umbrella Bucket-1 child #6): documents the single write boundary (HANDOVER.md at the project root) and the never-stage/commit rule.
- 2026-08-27 - SKILL.md gained a required `## Open review findings` section in the HANDOVER.md structure, a matching Hardening "Never" clause, and the review packets in its Inputs list. The section carries the open count and the packet path and explicitly forbids copying a review round's findings in as their only copy, which is the failure it exists to prevent: on 2026-08-26 a round's findings lived only in `HANDOVER.md` and the next handoff overwrote them one working day later. It also states that open findings never delay or prevent a handoff, because the user rejected the gating form of the fix on the grounds that crossing a session boundary with work outstanding is precisely what a handoff is for.
