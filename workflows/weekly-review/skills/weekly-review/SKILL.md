# Weekly Review - Skill Specification

**Last modified:** 2026-07-02

## Purpose
Turn the deterministic briefing packet produced by the weekly-review gather
script into a one-page written review, saved to the `reviews/` store, and distil
any genuinely durable facts from it into `memory/`. This is the ~10% (judgement)
half of the flywheel; the gather script does the ~90% (collection).

## When to use
- When the session-startup staleness gate reports a review is due and the user
  agrees to run it.
- Any time the user asks for a weekly review directly.

Do not run it unprompted: like every side-effecting task it waits for the user to
say go (the startup gate offers, it does not auto-run).

## Inputs
- The briefing packet from `python workflows/weekly-review/scripts/run.py`
  (session activity, journal entries, LOG.md activity, git commits, memory
  changes, and the previous 1-2 reviews).
- The `reviews/` store, for prior reviews to reconcile against.

## How to run
1. **Check for gaps first.** Run `python workflows/weekly-review/scripts/run.py --status`.
   If it warns about empty journal days in the window, tell the user which days
   are blank and ask whether they want to fill them in before continuing. If they
   fill them, or choose to proceed, carry on; days left blank are tracked and
   picked up automatically by a later review (never silently lost). The current
   day (the run day) is deliberately excluded from this warning and from the
   review - it is not finished - so never ask the user to "fill in today"; it is
   deferred to the next review automatically.
2. **Gather.** Run `python workflows/weekly-review/scripts/run.py` and read the
   whole briefing packet it prints.
3. **Write the review** to `reviews/<label>.md` (the packet states the label,
   e.g. `2026-W27.md`). Aim for about one page, covering:
   - **What got done** - grounded in the git commits, LOG entries, and journal,
     not invented.
   - **Recurring themes** - patterns across the week and against prior reviews.
   - **Blockers / open threads** - what stalled or is unfinished.
   - **Progress vs prior weeks** - this is the flywheel; compare against the
     previous reviews in the packet.
   - **Suggested next actions** - small, concrete, tied to the backlog where
     relevant.
4. **Reconcile, do not just append.** Where the packet's prior reviews contain a
   statement this week supersedes or contradicts, say so explicitly in the new
   review. Never let an older summary silently override a newer fact - that is the
   context-poisoning failure mode of compounding memory.
5. **Distil durable facts only.** If the review surfaces a genuinely durable fact
   (a confirmed decision, a lasting preference, a new project constraint), write
   it into `memory/` via the four-step memory procedure in AGENTS.md - and flag
   it to the user for confirmation first. Never bulk-copy the review into memory;
   the review lives in `reviews/`, memory holds only distilled, typed facts. This
   mirrors the copy-on-write "candidate memory" pattern: the review is the
   reviewable layer, memory is what survives review.
6. **Record completion.** Run `python workflows/weekly-review/scripts/run.py --record`
   to advance the coverage watermark and stamp the staleness marker.

## Outputs
- `reviews/<label>.md` - the written weekly review (gitignored personal content).
- Optionally, one or more new/updated `memory/` files (only durable facts, only
  with user confirmation).
- An advanced coverage watermark and refreshed staleness marker (via `--record`).

## Verification
- `python workflows/weekly-review/scripts/run.py --record` **refuses** (exit 1)
  if `reviews/<label>.md` is missing or empty, so completion cannot be recorded
  without a real review on disk.
- After recording, `python workflows/weekly-review/scripts/run.py --status`
  reports "no review due" - confirming the watermark and staleness marker moved.
- The gather logic is covered by `python workflows/weekly-review/tests/run_tests.py`
  (window computation, journal backfill catch-up and horizon, date filtering,
  packet assembly). A failed run means the packet cannot be trusted; fix before
  writing a review on top of it.
- A failed check looks like: `--record` exiting 1 with "No review found", or a
  red test run. Do not claim a review was recorded on either.

## Dependencies
- `workflows/weekly-review/scripts/` [[workflows/weekly-review/scripts/CONTEXT]]
  - the gather script and its state/config/gather modules.
- `reviews/` [[reviews/CONTEXT]] - the output store.
- `journal/` [[journal/CONTEXT]], `memory/` [[memory/CONTEXT]],
  `workflows/session-search/` [[workflows/session-search/CONTEXT]] - signal
  sources read by the gather script.
- `AGENTS.md` [[AGENTS]] - the four-step memory write procedure and the
  session-startup staleness gate that surfaces this skill.

## Known Issues
- The memory-changes section of the packet can be verbose in a busy week; it is
  the richest "what did we decide" signal, so it is kept in full rather than
  truncated.
- FTS5 session search is keyword-based, so the session summary is a count and
  title list, not a semantic topic breakdown.
