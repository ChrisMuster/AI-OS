# Settings Check

**Last modified:** 2026-06-08

## Purpose
Validates the health of the automated command infrastructure on every session startup. Runs four distinct checks: permission coverage (all automatic commands are covered by an allowlist entry), script existence (all referenced scripts are present on disk), Python syntax (all workflow scripts parse without errors or warnings), and absolute path audit (no tracked file contains a hardcoded machine-specific path).

## Contents
- scripts/ — `workflows/settings-check/scripts/` [[workflows/settings-check/scripts/CONTEXT]] — Python script that performs all four checks.

## Inputs
- `.claude/settings.json` — project-level allowlist patterns and hook command definitions.
- `~/.claude/settings.json` — global allowlist patterns (also checked, because scheduled tasks may not load project-level settings).
- `~/.claude/scheduled-tasks/*/SKILL.md` — scheduled task commands. Gracefully skipped if the directory does not exist (expected on a fresh clone before first session startup).
- All `.py` files under `workflows/` [[workflows/CONTEXT]] and `journal/scripts/` [[journal/scripts/CONTEXT]] — syntax-checked via `py_compile`.
- All files tracked by `git ls-files` — scanned for hardcoded absolute paths.

## Outputs
- Health report printed to stdout.
- Updated `workflows/settings-check/LOG.md` — started and completed entries appended on every run.

## Steps
1. Run from anywhere:
   `python workflows/settings-check/scripts/run.py [--verbose]`
2. Review the report. FAIL findings require immediate action; WARN findings should be investigated.
3. Re-run to confirm clean.

This check runs automatically at session startup (CLAUDE.md step 6c). Only FAIL findings are reported at startup; a clean run is silent.

## Dependencies
- `.claude/settings.json` — must exist; read at runtime.
- `~/.claude/settings.json` — optional; checked when present.
- `~/.claude/scheduled-tasks/` — machine-local; gracefully absent on fresh clones.
- `CLAUDE.md` [[CLAUDE]] (root) — step 6c triggers this check at every session startup.
- `git` — used by the absolute path audit to identify tracked files.

## Known Issues
- Scheduled task discovery reads from `~/.claude/scheduled-tasks/`, which exists only on machines where at least one session has run. A mis-configured task would not be caught until after the task is created.
- The coverage check uses Python `fnmatch` glob matching. Patterns with a trailing ` *` (space + wildcard) will not match argless commands. All current hook and task commands include arguments, so this is not presently an issue.
- The absolute path check skips comment lines and lines containing `e.g.` or `→` to avoid false positives from documentation examples. A real hardcoded path on one of those lines would be missed.
- The check validates coverage and syntax only, not runtime correctness. A script may pass all checks but still fail at runtime for unrelated reasons.

## Revision History
- 2026-06-08 — Initial creation.
- 2026-06-08 — Extended with global settings coverage, script existence, Python syntax, and absolute path audit checks.
