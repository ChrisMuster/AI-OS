# memory-diff skill

**Last modified:** 2026-07-06

## Purpose
The AI (~10%) half of the memory-diff workflow: take the deterministic delta from
`run.py --status` and fold a single honest sentence about what changed in memory
into the session greeting. The script decides *what* changed; this skill decides
*how to say it* in one line.

## Contents
- SKILL.md - `workflows/memory-diff/skills/memory-diff/SKILL.md` [[workflows/memory-diff/skills/memory-diff/SKILL]] - the functional
  spec (purpose, when to use, inputs, how to run, outputs, verification).

## Inputs
- The delta from `python workflows/memory-diff/scripts/run.py --status`
  [[workflows/memory-diff/scripts/CONTEXT]].

## Outputs
- One line in the opening greeting summarising the memory changes since the last
  session, or nothing when there are none.

## Steps
See SKILL.md for the full procedure.

## Dependencies
- `workflows/memory-diff/scripts/` [[workflows/memory-diff/scripts/CONTEXT]] - the
  status reader that produces the delta.
- `AGENTS.md` [[AGENTS]] - the startup surfacing step that invokes this skill.

## Known Issues
- None.

## Revision History
- 2026-07-06 - Initial creation. SKILL.md on the templates/SKILL.md.template schema
  with a Verification section.
