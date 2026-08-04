# Scripts

**Last modified:** 2026-08-04

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
- 2026-06-24 - Encoding hardening: pinned `encoding="utf-8"` on the `git ls-files` `subprocess.run` call so it decodes as UTF-8 rather than the Windows cp1252 default. No behavioural change.
- 2026-06-26 - Absolute path audit refined: `ABS_PATH_PATTERNS` now capture the account-name segment, and new helpers `_is_placeholder_user` / `_line_has_real_abs_path` skip placeholder accounts (`Name`, `<username>`, `user`, ...) so documentation examples and test fixtures are no longer flagged; a real account name is still caught (and now also when it follows a placeholder on the same line). Added a local `PLACEHOLDER_USERS` set mirroring the personal-data-guard convention.
- 2026-07-25 - `extract_script_path` now strips a leading `$CLAUDE_PROJECT_DIR/` or `${CLAUDE_PROJECT_DIR}/` before resolving, so the script-existence check passes for hook commands that anchor their script to that variable. The rule-hooks and session-search hook commands in `.claude/settings.json` were changed to `python "$CLAUDE_PROJECT_DIR/.../script.py" ...` so they launch regardless of the shell's working directory; without this change the checker resolved the literal `$CLAUDE_PROJECT_DIR` segment against the project root and reported a false "script not found". The allowlist glob and the absolute-path audit were unaffected (the variable is not a machine-specific path).
- 2026-08-04 - Line endings pinned on the LOG.md append in `run.py`, which now passes `newline="\n"` explicitly. Part of the project-wide pass closing this defect class at all 48 write sites.
