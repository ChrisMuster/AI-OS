# Scripts

**Last modified:** 2026-06-08

## Purpose
Contains the health validator script for the settings-check workflow. Runs four checks: permission coverage across project and global settings, script existence, Python syntax, and absolute path audit on tracked files.

## Contents
- run.py — `workflows/settings-check/scripts/run.py` [[workflows/settings-check/scripts/CONTEXT]] — Performs all four checks and prints a report. Accepts `--verbose` to show passing checks.

## Inputs
- `.claude/settings.json` — project allowlist patterns and hook commands.
- `~/.claude/settings.json` — global allowlist patterns.
- `~/.claude/scheduled-tasks/*/SKILL.md` — scheduled task commands (skipped if absent).
- All `.py` files under `workflows/` [[workflows/CONTEXT]] and `journal/scripts/` [[journal/scripts/CONTEXT]] — syntax-checked.
- All `git`-tracked files — scanned for hardcoded absolute paths.

## Outputs
- Health report printed to stdout.
- `workflows/settings-check/LOG.md` — started and completed entries appended on every run.

## Steps
Run from anywhere:

```
python workflows/settings-check/scripts/run.py [--verbose]
```

## Dependencies
- `.claude/settings.json` — must exist; read at runtime.
- `~/.claude/settings.json` — optional; checked when present.
- `~/.claude/scheduled-tasks/` — optional; gracefully absent on fresh clones.
- `workflows/settings-check/LOG.md` — appended on every run.
- `git` — called via subprocess for the absolute path audit.

## Known Issues
- See parent `workflows/settings-check/CONTEXT.md` [[workflows/settings-check/CONTEXT]] for the full list of known limitations.

## Revision History
- 2026-06-08 — Initial creation.
- 2026-06-08 — Extended run.py with global settings coverage, script existence, Python syntax, and absolute path audit checks.
