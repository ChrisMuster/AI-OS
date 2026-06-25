# Personal Data Guard

**Last modified:** 2026-06-25

## Purpose
Catches personal data before it can be committed. It is a deterministic, read-only checker that scans only committable files (everything git tracks plus new files git would add, so all gitignored personal content is excluded automatically) for personal markers: real email addresses, personal absolute home paths carrying a real username (for example `C:\Users\<name>`), the user's own name and OS username, and an optional denylist of personal nouns. It mirrors the encoding-guard pattern: a standalone CLI that the full audit also consumes as an additive, advisory hook, so a personal-data leak is caught mechanically at close-out rather than by eye. It enforces the personal-data isolation rules in `AGENTS.md` [[AGENTS]] that previously relied entirely on reviewer discipline. There is deliberately no fix mode, because a leak cannot be safely auto-redacted.

## Contents
- scripts/ - `workflows/personal-data-guard/scripts/` [[workflows/personal-data-guard/scripts/CONTEXT]] - The `run.py` entry point (read-only `--check`, with `--json` for the audit hook) plus the pure detection and marker-derivation helpers.
- tests/ - `workflows/personal-data-guard/tests/` [[workflows/personal-data-guard/tests/CONTEXT]] - Unit tests for the detectors, allowlists, marker derivation, and the guarantee that the guard's own source carries no real personal data.
- config/ - `workflows/personal-data-guard/config/` [[workflows/personal-data-guard/config/CONTEXT]] - Holds `denylist.example.txt` (tracked format example); the real `denylist.txt` is gitignored and machine-local.

## Inputs
- Committable files, discovered via `git ls-files --cached --others --exclude-standard`. Git must be available; without it the guard reports a single INFO note and scans nothing (rather than walking the whole tree and flooding false positives).
- `USER.md` [[USER]] (gitignored, local) - the `**Name:**` line is read at runtime to derive the user's name markers. Absent on a fresh clone, in which case the guard degrades to pattern-only detection.
- `.env` (gitignored, local) - any email-looking values are read at runtime as own-email markers.
- `config/denylist.txt` (gitignored, optional) - extra personal nouns to flag as WARN.

## Outputs
- A findings report on stdout (human text, or `--json` for the audit hook). FAIL for emails, personal home paths, the user's name/username; WARN for denylist nouns; INFO for degraded/skip notes.
- Exit code 0 when there is no FAIL, 1 when a FAIL exists, so a pre-commit hook or CI can gate on it.
- No files are written and no LOG.md entry is made on a check run (the guard is read-only, like the audit and the encoding-guard check).

## Steps
1. Scan committable files for personal data (read-only):
   `python workflows/personal-data-guard/scripts/run.py --check [--json]`
2. Review any FAIL findings and remove the personal data, or move the content into a gitignored personal file if that is where it belongs.
3. Re-run until the report is clean (exit 0).
4. Append LOG.md only when run as a deliberate, logged workflow step (the read-only check and the audit-hook invocation do not log).

## Dependencies
- `AGENTS.md` [[AGENTS]] (root) - Defines the personal-data isolation rules this guard enforces mechanically.
- `USER.md` [[USER]] and `.env` (root, gitignored) - Canonical local sources for the user's name and email markers, read at runtime.
- `workflows/audit/` [[workflows/audit/CONTEXT]] - The full audit shells out to this guard's `--check --json` and merges its WARN/FAIL findings under a `personal-data` label.
- Python 3.9+ standard library only; the `git` CLI for file discovery.

## Known Issues
- File discovery depends on git. Outside a git checkout, or if git is unavailable, the guard scans nothing and emits a single INFO note - by design, since the gitignore-based scope is the whole point.
- Detection is pattern- and marker-based, so it is not exhaustive: it catches emails, home paths, the USER.md name, the OS username, and the configured denylist. A personal noun not on the denylist (and not the user's own name) is not caught. The denylist is the tunable surface for project-specific terms.
- Name markers include each name token of length 3+, so a common first name that doubles as an ordinary word could produce a WARN-grade false positive in tracked prose; such cases should be reworded (tracked files are not meant to carry the user's name in any form).
- Findings messages quote the matched value, so a saved `--json` report or audit `last-report.md` will contain that value; both outputs are gitignored.

## Revision History
- 2026-06-25 - Initial creation. Read-only check script with git-scoped discovery, runtime marker derivation from USER.md/.env, email/home-path/name/username detectors with placeholder allowlists, an optional gitignored denylist (WARN), audit-hook integration, and unit tests. First live run flagged the user's name in two AGENTS.md isolation-rule examples; those examples were reworded to a bracketed placeholder.
