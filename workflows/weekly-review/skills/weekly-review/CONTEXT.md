# Weekly Review (Skill)

**Last modified:** 2026-07-02

## Purpose
The judgement half of the weekly-review flywheel: how Biblio turns the gather
script's deterministic briefing packet into a written one-page review, reconciles
it against prior reviews, and distils only durable facts into `memory/`. The
functional spec is in `SKILL.md`; this file records why the skill exists.

## Contents
- SKILL.md - `workflows/weekly-review/skills/weekly-review/SKILL.md` [[workflows/weekly-review/skills/weekly-review/SKILL]] - The
  functional specification: when to use, inputs, the write procedure, outputs,
  and how to verify a review was recorded.

## Inputs
The briefing packet from the gather script, and the `reviews/` store. See
`SKILL.md` for detail.

## Outputs
A written `reviews/<label>.md`, optional distilled `memory/` facts (with user
confirmation), and an advanced coverage watermark. See `SKILL.md`.

## Steps
The skill's step-by-step procedure lives in `SKILL.md` (gap check, gather, write,
reconcile, distil, record). It is not duplicated here.

## Dependencies
- `workflows/weekly-review/scripts/` [[workflows/weekly-review/scripts/CONTEXT]]
  - produces the packet the skill consumes and records completion.
- `reviews/` [[reviews/CONTEXT]] - output store.
- `AGENTS.md` [[AGENTS]] - memory write procedure and the startup staleness gate.

## Known Issues
- None.

## Revision History
- 2026-07-02 - Initial creation. Synthesis skill for the weekly-review flywheel
  (best-practices umbrella Bucket-1 child #3), scaffolded from the SKILL template.
