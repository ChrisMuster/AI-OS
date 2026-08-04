# Weekly Review

**Last modified:** 2026-08-04

## Purpose
The cron + memory flywheel for Book Dragon: a weekly retrospective that reads the
week's activity, produces a one-page review, and compounds over time (each review
reads the previous ones). It is the best-practices umbrella's Bucket-1 child #3.

Because no scheduling mechanism on the Claude Code CLI/IDE surface can both wake
an LLM and touch local files (cloud routines cannot read local stores; Desktop
scheduled tasks are Desktop-only - see [[reference_claude_scheduling_surfaces]]),
this workflow is **not** an autonomous overnight cron. It is a deterministic
gather script plus a **session-startup staleness gate** (AGENTS.md): the script
prepares the review, and the gate surfaces "a review is due" so Biblio writes it
when the user next sits down. That makes the flywheel work identically on every
AGENTS-reading AI, with no per-AI scheduling adapter.

## Contents
- scripts/ - `workflows/weekly-review/scripts/` [[workflows/weekly-review/scripts/CONTEXT]]
  - run.py (gather / status / record) plus gather.py, state.py, config.py.
- tests/ - `workflows/weekly-review/tests/` [[workflows/weekly-review/tests/CONTEXT]]
  - hermetic unit tests (31) for the window and backfill logic and the readers.
- skills/ - `workflows/weekly-review/skills/` [[workflows/weekly-review/skills/CONTEXT]]
  - the weekly-review synthesis skill (the AI half).

## Inputs
- The journal (`journal/entries/` [[journal/entries/CONTEXT]]), every project `LOG.md`, git history,
  `memory/LOG.md`, the session-search index, and prior reviews. All are read by
  the gather script; only `--topic`-free, no user flags are required.
- Biblio, to write the review from the packet (see the skill).

## Outputs
- A briefing packet (stdout) for Biblio to write from.
- A dated review file in `reviews/` [[reviews/CONTEXT]] (written by Biblio).
- Optionally, distilled durable facts in `memory/` [[memory/CONTEXT]] (with user
  confirmation).
- `state.json` and `.last-run` coverage/staleness markers (gitignored).

## Steps
1. **Staleness gate (startup):** AGENTS.md session startup runs `run.py --status`;
   if a review is due it surfaces the offer and warns about empty journal days.
2. **Gap check:** on the user's go-ahead, warn about any blank journal days in the
   window and let the user fill them or proceed.
3. **Gather:** `run.py` assembles the briefing packet from the week's signal.
4. **Write:** Biblio writes `reviews/<label>.md` (about one page), reconciling
   against prior reviews rather than blindly appending.
5. **Distil:** genuine durable facts go into `memory/` via the four-step
   procedure, flagged for user confirmation.
6. **Record:** `run.py --record` advances the coverage watermark and stamps
   staleness.
7. Append LOG.md with a completion or failure entry.

## Dependencies
- `workflows/weekly-review/scripts/` [[workflows/weekly-review/scripts/CONTEXT]]
  - run.py and the gather/state/config modules.
- `workflows/weekly-review/skills/weekly-review/` [[workflows/weekly-review/skills/weekly-review/CONTEXT]]
  - the synthesis skill this workflow relies on to produce the review.
- `reviews/` [[reviews/CONTEXT]] - output store.
- `journal/` [[journal/CONTEXT]], `memory/` [[memory/CONTEXT]],
  `workflows/session-search/` [[workflows/session-search/CONTEXT]] - signal sources.
- `AGENTS.md` [[AGENTS]] - hosts the session-startup staleness gate and the memory
  write procedure.

## Known Issues
- No autonomous unattended run on the CLI/IDE surface (by design; see Purpose). A
  Desktop-only scheduled task could drive it unattended, but that would be
  Claude-Desktop-only and is deliberately not built.
- The first few reviews are thin until enough history accumulates (expected for a
  flywheel).
- The run day is never counted in its own review (the day is not finished, so its
  journal is incomplete); it is deferred to the next review. This applies to the
  journal only - LOG.md, git, and session activity for the run day are timestamped
  real events and are included.

## Revision History
- 2026-07-02 - Initial creation. Gather script (run/gather/state/config), 28-test
  suite, synthesis skill, and the `reviews/` store. Best-practices umbrella
  Bucket-1 child #3 (cron + memory flywheel / weekly review).
- 2026-07-03 - Run day is never counted in its own review's journal (the day is
  not finished): excluded from coverage and from the empty-day warning, and always
  deferred to a later review. Tests 28 -> 31.
- 2026-08-04 - Maintenance, no behaviour change: the two entries above were
  reordered into the newest-at-bottom order the schema requires, after the audit
  gained a mechanical Revision History ordering check that flagged them. Recorded
  as its own entry because at the own-directory level any real change to a file
  earns one, reordering included; leaving a corrected file with no record of the
  correction is the drift the check exists to catch.
