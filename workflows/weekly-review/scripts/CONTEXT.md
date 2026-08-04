# Weekly Review - Scripts

**Last modified:** 2026-08-04

## Purpose
The deterministic (~90%) half of the weekly-review flywheel: gather the week's
signal from existing stores, report staleness, and record completion. No AI
judgement lives here; the script assembles a briefing packet the AI then writes
the review from.

## Contents
- run.py - `workflows/weekly-review/scripts/run.py` [[workflows/weekly-review/scripts/CONTEXT]] - CLI entry point. Modes:
  default/`--gather` (print the briefing packet, read-only), `--status` (report
  whether a review is due and warn about empty journal days, read-only, `--json`),
  `--record` (advance the coverage watermark and stamp staleness after a review is
  written; honours `--dry-run`). `--today` overrides the date for testing.
- gather.py - `workflows/weekly-review/scripts/gather.py` [[workflows/weekly-review/scripts/CONTEXT]] - Deterministic readers
  (journal, LOG.md activity, git commits, memory changes, session activity, prior
  reviews) and the packet assembler. Each reader degrades to empty rather than
  raising.
- state.py - `workflows/weekly-review/scripts/state.py` [[workflows/weekly-review/scripts/CONTEXT]] - Coverage state: the
  window computation and the journal backfill logic (watermark + pending-days
  carry-forward) that guarantees a backfilled journal day is picked up by a later
  review rather than lost. The run day is never counted in its own review (the day
  is not finished) and is always deferred to a later one.
- config.py - `workflows/weekly-review/scripts/config.py` [[workflows/weekly-review/scripts/CONTEXT]] - Tunable constants
  (window, staleness, carry-forward horizon, prior-review count, max span).

## Inputs
- `journal/entries/YYYY-MM.md` - journal entries in the window.
- Every `LOG.md` across the project - filtered by entry date.
- git history (`git log`) for the window.
- `memory/LOG.md` - memory changes in the window (a lightweight memory-diff).
- `workflows/session-search/data/sessions-*.db` - session activity, read-only.
- `reviews/YYYY-Www.md` - prior reviews, for compounding context.
- `state.json` and `.last-run` (gitignored) - coverage watermark and staleness marker.

## Outputs
- The briefing packet (stdout).
- `state.json` and `.last-run` (written only by `--record`; gitignored).
- A LOG.md completion entry on `--record`.

## Steps
1. `--status`: read the staleness marker and window; report due/empty days.
2. default: gather all signals, assemble the packet, print it.
3. AI writes `reviews/<label>.md` from the packet (see the skill).
4. `--record`: verify the review file exists, advance the watermark, refresh the
   pending-days list, stamp `.last-run`.
5. Append LOG.md with a completion or failure entry.

## Dependencies
- `state.py`, `gather.py`, `config.py` (siblings) - imported by `run.py`.
- Standard library only (sqlite3, subprocess, datetime, json, re) - no third-party
  packages, so no project `.venv` handoff is needed.
- `journal/` [[journal/CONTEXT]], `memory/` [[memory/CONTEXT]],
  `workflows/session-search/` [[workflows/session-search/CONTEXT]],
  `reviews/` [[reviews/CONTEXT]] - signal sources and output store.

## Known Issues
- Session activity depends on the session-search index being present and on its
  `sessions` table schema (columns `timestamp`, `session_id`, `session_title`,
  `ai_identity`). A schema change there would drop the session section to empty
  (it degrades, never crashes).
- The LOG.md scan walks the whole project each run; it skips `data/`, `outputs/`,
  and `collections/` subtrees to avoid noise but is otherwise unfiltered.

## Revision History
- 2026-07-02 - Initial creation. run.py, gather.py, state.py, config.py for the
  weekly-review flywheel (best-practices umbrella Bucket-1 child #3).
- 2026-08-04 - Line endings pinned on all three text writes (the LOG.md append and `.last-run` stamp in `run.py`, and the `state.json` save in `state.py`), which now pass `newline="\n"` explicitly. Part of the project-wide pass closing this defect class at all 48 write sites.
