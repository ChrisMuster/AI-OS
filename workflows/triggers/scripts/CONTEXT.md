# Triggers - Scripts

**Last modified:** 2026-07-03

## Purpose
Read the trigger registry and print it grouped by category, so "give me a list of
triggers" gives a consistent answer on every AI.

## Contents
- run.py - re-execs under the project `.venv` if needed, then loads
  config/triggers.yaml and renders it. Modes: `--list` (grouped listing,
  default), `--category NAME` (one category), `--json` (machine output).

## Inputs
config/triggers.yaml. Optional `--category` to filter.

## Outputs
A grouped human-readable listing (stdout), or JSON with `--json`. Read-only.

## Steps
1. Load and validate the registry (drops entries without a name; degrades to an
   empty list on a missing or malformed file).
2. Filter to a category if requested.
3. Render as text or JSON.

## Dependencies
- `workflows/triggers/config/triggers.yaml` [[workflows/triggers/config/CONTEXT]] -
  the registry data.
- `workflows/biblio-tools/scripts/runtime.py` [[workflows/biblio-tools/scripts/CONTEXT]]
  - switches plain `python` invocations into the canonical project `.venv`.
- PyYAML - to parse the registry (a project dependency, loaded after the runtime
  handoff).

## Known Issues
None.

## Revision History
- 2026-07-03 - Initial creation. run.py with --list / --category / --json.
- 2026-07-03 - Added project-runtime handoff before PyYAML import so the
  documented plain-`python` trigger command works on every AI surface.
