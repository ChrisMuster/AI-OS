# Review Orchestration - Config

**Last modified:** 2026-09-25

## Purpose
Holds the review-orchestration settings file, the one file the user edits to change how runs behave. Settings live here rather than in the scripts so that nothing a run depends on is hard-coded.

## Contents
- settings.json - `workflows/review-orchestration/config/settings.json` - The run settings. So far one: `usage_ceiling_percent` (default 80), the usage level at which a run stops before its next step rather than spend allowance needed for interactive work.

## Inputs
None. The file is edited by hand.

## Outputs
None directly. `workflows/review-orchestration/scripts/settings.py` [[workflows/review-orchestration/scripts/CONTEXT]] reads and checks it.

## Steps
N/A - this is a configuration directory, not a workflow.

## Dependencies
- `workflows/review-orchestration/scripts/settings.py` [[workflows/review-orchestration/scripts/CONTEXT]] - the only reader. It refuses a value outside its allowed range rather than replacing it with the default.

## Known Issues
- Only the usage ceiling exists so far. The roles, default models and efforts of the plan's section 11, and `verify-commands.txt` (the verification commands a builder may run), are added by the next chunk of the engine.

## Revision History
- 2026-09-25 - Initial creation with `settings.json` holding `usage_ceiling_percent`, for the usage-limit reading of Stage A2's first chunk.
