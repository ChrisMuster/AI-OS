# Handoff Skill

**Last modified:** 2026-07-03

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
