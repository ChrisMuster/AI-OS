# Reviews

**Last modified:** 2026-08-23

## Purpose
Store for the weekly-review flywheel. Holds the dated review files that the
`workflows/weekly-review/` [[workflows/weekly-review/CONTEXT]] workflow produces: each is a one-page synthesis of
what happened in a given week, written by Biblio from the deterministic briefing
packet the gather script assembles. Each review reads the previous ones, so the
store compounds over time into a running record of progress, recurring themes,
and blockers.

## Contents
Dated weekly review files, named `YYYY-Www.md` (ISO year and week, e.g.
`2026-W27.md`). Individual review files are not listed here - they are personal
content and are gitignored (per the personal-data isolation rule). The
filesystem is the authoritative source; list this directory directly when the
set of reviews is needed.

Only `CONTEXT.md` (this file) is tracked in git.

## Inputs
- The briefing packet produced by `workflows/weekly-review/scripts/run.py` [[workflows/weekly-review/scripts/CONTEXT]]
  (session activity, journal
  entries, LOG.md entries, git history, memory changes, and prior reviews for
  the week).
- Biblio, to write the synthesis from that packet.

## Outputs
- One dated review file per week, `YYYY-Www.md`, written by Biblio.

## Steps
N/A. This is a store directory, not a workflow. The procedure that reads and
writes it lives in `workflows/weekly-review/` [[workflows/weekly-review/CONTEXT]].

## Dependencies
- `workflows/weekly-review/` [[workflows/weekly-review/CONTEXT]] - produces the
  briefing packet and defines how reviews are written and how genuine durable
  facts are distilled from them into `memory/` [[memory/CONTEXT]].

## Known Issues
- Review files are personal content and gitignored. A fresh clone starts with an
  empty store (only this `CONTEXT.md` and a `.gitkeep`); the first few reviews
  are thin until enough history accumulates.

## Revision History
- 2026-07-02 - Initial creation. Dedicated store for the weekly-review flywheel
  (best-practices umbrella Bucket-1 child #3).
- 2026-08-04 - Recorded the 2026-W32 review landing in the store. Contents is
  unchanged, because it describes the naming convention rather than listing the
  personal review files, but a file was added and the own-directory rule carries
  no meaningfulness threshold. The four earlier reviews did not record their
  arrival here; that gap is a symptom of the guard-coverage problem rather than a
  precedent, since this directory is gitignored and no git-scoped guard can see
  a missing entry.
- 2026-08-12 - Recorded the 2026-W33 review landing in the store. Contents is
  unchanged for the same reason as the entry above, since it describes the naming
  convention rather than listing personal files, but the own-directory rule
  carries no meaningfulness threshold and a file was added.
- 2026-08-23 - Recorded the 2026-W34 review landing in the store. Contents is
  unchanged, for the same reason as the two entries above. This review covers an
  eleven-day window rather than the usual seven because the user was away from 14
  to 21 August, which is a cadence break worth noting here only insofar as the
  store now holds one file whose window is longer than its name implies.
