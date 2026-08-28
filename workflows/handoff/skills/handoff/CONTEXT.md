# Handoff Skill

**Last modified:** 2026-08-28

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
- 2026-08-28 - SKILL.md's `## Open review findings` instruction gained a clause for the two cases where the gather packet cannot fully read a review packet. If the packet section reports "shape not recognised", the writing AI must say so and name the file rather than invent a count or write zero; if it reports items as unlabelled, the count is carried as given. Added alongside the reader change that made both wordings possible, because the reader reporting honestly achieves nothing if the AI writing the handover rounds it back to a confident number. The rule is the SKILL.md-side half of the same obligation the packet mechanism exists for: a false zero at a session boundary is the failure, not an unreadable file.
- 2026-08-28 - SKILL.md's `## Open review findings` instruction gained the plan-readiness / review-process split: where the packet separates those two classes of finding, the handover keeps them separate too. Collapsed into one list, a repair to the review loop reads as a blocker on building the plan, which misstates what the next session is free to do; that is not hypothetical, since the round this was written during had to have exactly that classification corrected by hand. The instruction lives here as well as in `memory/review_process.md` because the skill is what an AI follows while writing the document, and a convention recorded only in the process file is read before the writing rather than during it.
