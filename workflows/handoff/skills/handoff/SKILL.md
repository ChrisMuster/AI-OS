# handoff - Skill Specification

**Last modified:** 2026-07-03

## Purpose
Write a structured `HANDOVER.md` that lets the next session (or another AI) resume
cleanly, turning the deterministic gather packet into the narrative a script
cannot produce.

## When to use
When the user triggers a handoff at the end of a working session with more work
still to do - e.g. "do the handoff", "hand off", "handover", "wrap up the
session", "save state for next time". Also whenever the user explicitly asks to
update or refresh the handoff document.

## Inputs
- The briefing packet from `python workflows/handoff/scripts/run.py --gather`
  (branch, working tree, line churn, recent commits, changed-directory LOG tails,
  active backlog, recent session activity).
- The current session's own context: what was being worked on, why, what was
  decided, and what is left. This is the part only the AI holds.
- Any steer the user gives when triggering the handoff, e.g. "keep this in mind",
  "this is where I'm going next session", "start by doing X". This is the human's
  explicit priority for the next session and must be captured, not paraphrased
  away. If the user gives no steer, do not invent one.

## How to run
1. Run `python workflows/handoff/scripts/run.py --gather` and read the packet.
2. Write `HANDOVER.md` at the project root (overwrite any existing one - it is a
   single rolling file). Follow this structure:
   - A title and a `**Created:** <timestamp>` line (ISO 8601 with offset, from
     `get_timestamp` or `date`). The `**Created:**` line is required: the startup
     recovery watermark reads it to tell a fresh handoff from a consumed one.
   - `**Branch:**` the current branch.
   - `## Steer for next session` - if the user gave any steer when triggering the
     handoff, capture it here, near the top, in their own words. This is what the
     next session should read first. Omit this section entirely if there was no
     steer (do not fabricate direction).
   - `## Situation` - what this body of work is and where the session stopped.
   - `## Done` - what was completed (with verification state, e.g. tests green).
   - `## In progress` - what was mid-way through, and specifically why / where it
     was left (the stuck point, the next edit).
   - `## Next steps` - an ordered list the next session can act on directly.
   - `## Decisions made` - choices taken and the reasoning, so they are not
     relitigated.
   - `## Gotchas / guardrails` - anything that will trip up the next session,
     plus an explicit note on git state: whether the work may be staged or
     committed, or whether the user still wants to review first.
3. Keep it honest and specific. Do not claim a check passed that did not run (per
   the Verification discipline rule). The existing quality bar is the structure
   and candour of a good handover: concrete file paths, real command output,
   clear "do not stage/commit" guardrails where they apply.
4. Do not stage or commit `HANDOVER.md` - it is gitignored by design.

## Outputs
- `HANDOVER.md` at the project root (rolling, overwritten, gitignored).

## Verification
- Confirm the file exists and contains a `**Created:**` line and the required
  sections. `python workflows/handoff/scripts/run.py --status --json` should then
  report `"unread": true` with `handoff_timestamp` equal to the `**Created:**`
  value, proving the next startup will surface it. A failed check looks like
  `unread: false` or a null `handoff_timestamp` (missing or unparsable Created
  line) - fix the document rather than declaring success.

## Dependencies
- `workflows/handoff/scripts/` - the gather packet and the status/watermark logic.
- `AGENTS.md` - the session-handoff trigger section and the startup recovery step
  that reads the finished document.

## Known Issues
- The trigger is natural language, so it depends on the user asking; there is no
  automatic end-of-session handoff. This is deliberate (handoff is user-initiated).
