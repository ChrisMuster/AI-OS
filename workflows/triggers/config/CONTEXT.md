# Triggers - Config

**Last modified:** 2026-07-03

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
