# Settings Check

**Last modified:** 2026-06-08

## Purpose
Validates that every command configured to run automatically — hook commands in `.claude/settings.json` and Bash commands in scheduled task SKILL.md files — is covered by at least one allowlist entry. Catches the class of bug where a command is set up to run silently but would prompt for permission every time because no matching allowlist pattern exists.

## Contents
- scripts/ — `workflows/settings-check/scripts/` [[workflows/settings-check/scripts/CONTEXT]] — Python script that performs the coverage check.

## Inputs
- `.claude/settings.json` — read for allowlist patterns and hook command definitions.
- `~/.claude/scheduled-tasks/*/SKILL.md` — read for scheduled task commands. Gracefully skipped if the directory does not exist (expected on a fresh clone before first session startup).

## Outputs
- Coverage report printed to stdout.
- Updated `workflows/settings-check/LOG.md` — started and completed entries appended on every run.

## Steps
1. Run from anywhere:
   `python workflows/settings-check/scripts/run.py [--verbose]`
2. Review the report. Any FAIL finding means a command has no matching allowlist entry and will prompt for permission every time it fires. Fix by adding the appropriate pattern to `permissions.allow` in `.claude/settings.json`.
3. Re-run to confirm clean.

This check also runs automatically at session startup (CLAUDE.md step 6c). Only failures are reported at startup.

## Dependencies
- `.claude/settings.json` — source of allowlist patterns and hook commands; must exist for the check to run.
- `~/.claude/scheduled-tasks/` — machine-local directory holding scheduled task SKILL.md files. Not part of the project repository; gracefully skipped if absent.
- `CLAUDE.md` [[CLAUDE]] (root) — step 6c triggers this check at every session startup.

## Known Issues
- Scheduled task discovery reads from `~/.claude/scheduled-tasks/`, which exists only on machines where at least one session has run. On a fresh clone before first session startup, no tasks have been created and that section is skipped. A mis-configured task would not be caught until after the task is created.
- The coverage check uses Python `fnmatch` glob matching. Patterns with a trailing ` *` (space + wildcard) will not match argless commands. All current hook and task commands include arguments, so this is not presently an issue — but a new argless command would need a pattern without the trailing space.
- The check validates coverage only, not correctness. A command may be allowed but still fail at runtime for unrelated reasons (wrong path, missing file, etc.).

## Revision History
- 2026-06-08 — Initial creation.
