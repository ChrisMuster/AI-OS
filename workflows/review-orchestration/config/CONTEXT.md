# Review Orchestration - Config

**Last modified:** 2026-09-26

## Purpose
Holds the review-orchestration settings, the files the user edits to change how runs behave. Settings live here rather than in the scripts so that nothing a run depends on is hard-coded.

## Contents
- settings.json - `workflows/review-orchestration/config/settings.json` - The run settings: `usage_ceiling_percent` (default 80, the usage level at which a run stops before its next step rather than spend allowance needed for interactive work); `roles` (which provider builds and which reviews, default Claude and Codex); `round_caps` (3 build-review rounds, 2 plan-review rounds); and `models`, the model and effort each provider uses in each role it can hold, as plan section 11 settled them.
- verify-commands.txt - `workflows/review-orchestration/config/verify-commands.txt` - The shell commands an orchestrated builder may run to verify its own work: one regular expression per line, the whole command must match one. The starting set is the B2 rerun's list, tightened so that no line accepts a flag it does not name.

## Inputs
None. The files are edited by hand.

## Outputs
None directly. `workflows/review-orchestration/scripts/settings.py` [[workflows/review-orchestration/scripts/CONTEXT]] reads and checks `settings.json`, and `approver.py` in the same folder reads `verify-commands.txt`.

## Steps
N/A - this is a configuration directory, not a workflow.

## Dependencies
- `workflows/review-orchestration/scripts/settings.py` [[workflows/review-orchestration/scripts/CONTEXT]] - reads `settings.json`. It refuses a value outside its allowed range rather than replacing it with the default.
- `workflows/review-orchestration/scripts/approver.py` [[workflows/review-orchestration/scripts/CONTEXT]] - reads `verify-commands.txt`, refusing a line that is not a valid regular expression and a file that lists nothing.

## Known Issues
- Swapping the roles (Codex builds, Claude reviews) needs two entries the defaults do not have, `models.codex.builder` and `models.claude.reviewer`, and the sessions for that pairing are not built yet; the settings reader and the providers both refuse it by name until then.
- Model names are the defaults on 2026-09-24 and are checked at run time; a model a provider no longer offers stops the run rather than being substituted.
- A pattern in `verify-commands.txt` that is too broad widens what the builder may run; an argument class that allows `-` lets in flags the line never meant, which is how the audit's `--save` got through (R2-2). The approver still refuses a `..` path component, a `.env` file, more than one line and leaving the sandbox whatever a pattern allows, but anything else a pattern matches runs.

## Revision History
- 2026-09-25 - Initial creation with `settings.json` holding `usage_ceiling_percent`, for the usage-limit reading of Stage A2's first chunk.
- 2026-09-26 - Stage A2, chunk (b): `settings.json` gained `roles`, `round_caps` and `models` (plan section 11), and `verify-commands.txt` was added for the builder's approver.
- 2026-09-26 - `verify-commands.txt` tightened after code review round R2 (R2-2): a free argument can no longer start with `-`, so the audit's `--save`, close-out's `--repair` and `py_compile`'s flags are refused; the audit line gained an optional `--no-graph`. The file's header states the rule.
- 2026-09-26 - The test-runner line brought under that rule after code review round R3 (R3-1): it takes test names and an optional `-v` only, where it had still accepted any flag.
