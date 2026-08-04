# Triggers - Config

**Last modified:** 2026-08-04

## Purpose
Holds the trigger registry: the single source of truth mapping natural-language
phrases to Book Dragon actions.

## Contents
- triggers.yaml - the registry. A list of categories, each with a name, a one-line
  summary, the command it runs (or a short instruction), and its trigger phrases.

## Inputs
None. Edited by hand when an action is added or renamed.

## Outputs
None. Read by `workflows/triggers/scripts/run.py` [[workflows/triggers/scripts/CONTEXT]].

## Steps
N/A. This is a data directory.

## Dependencies
- `workflows/triggers/scripts/` [[workflows/triggers/scripts/CONTEXT]] - reads this
  file.

## Known Issues
- Must be kept in sync when triggerable actions change; there is no automatic check
  that a listed command still exists.

## Revision History
- 2026-07-03 - Initial creation. triggers.yaml seeded with handoff, close-out,
  audit, weekly-review, backlog, update-check, link-check, settings-check,
  onboarding, and triggers.
- 2026-07-06 - Added the memory-diff category (phrases "what changed in memory",
  "memory diff", "show memory changes", "what is new in memory"), taking
  triggers.yaml to eleven categories; best-practices umbrella Bucket-1 child #5.
- 2026-07-07 - Added the doc-sync category (phrases "check context is current",
  "are the docs in sync", "check for doc drift", "is the context up to date")
  running the doc-sync guard, taking triggers.yaml to twelve categories; also
  updated the audit summary to mention the doc-sync hook (doc-sync-guard build
  part 5).
- 2026-08-04 - Corrected the audit category summary: it enumerated the full audit's hooks but omitted skill-hardening, which has run as a sixth hook since 2026-07-09. This summary is what `run.py --list` prints when the user asks for the trigger list, so the omission was a live description, not a comment. Found by sweeping every enumeration of the audit's hooks rather than only the files a review had named.
