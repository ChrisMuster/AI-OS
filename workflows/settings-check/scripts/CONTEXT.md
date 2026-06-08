# Scripts

**Last modified:** 2026-06-08

## Purpose
Contains the settings coverage validator script for the settings-check workflow.

## Contents
- run.py — `workflows/settings-check/scripts/run.py` [[workflows/settings-check/scripts/CONTEXT]] — Parses `.claude/settings.json` and any scheduled task SKILL.md files; reports every command that has no matching allowlist entry.

## Inputs
- `.claude/settings.json` — read for allowlist patterns and hook command definitions.
- `~/.claude/scheduled-tasks/*/SKILL.md` — read for scheduled task commands (skipped if the directory does not exist).

## Outputs
- Coverage report printed to stdout.
- `workflows/settings-check/LOG.md` — started and completed entries appended on every run.

## Steps
Run from anywhere:

```
python workflows/settings-check/scripts/run.py [--verbose]
```

## Dependencies
- `.claude/settings.json` — must exist; read at runtime.
- `~/.claude/scheduled-tasks/` — optional; gracefully absent on fresh clones.
- `workflows/settings-check/LOG.md` — appended on every run.

## Known Issues
- See parent `workflows/settings-check/CONTEXT.md` [[workflows/settings-check/CONTEXT]] for the full list of known limitations.

## Revision History
- 2026-06-08 — Initial creation.
