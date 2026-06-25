# Scripts

**Last modified:** 2026-06-25

## Purpose
Holds the personal-data-guard implementation. `run.py` is both a CLI and an importable module: it discovers committable files via git, derives the user's personal markers at runtime from `USER.md` [[USER]] and `.env`, and classifies each text file for emails, personal home paths, the user's name/username, and an optional denylist of personal nouns. The script source is generic - every real marker is read at runtime from local gitignored sources, never baked in - so it never carries personal data and never flags itself.

## Contents
- run.py - `workflows/personal-data-guard/scripts/run.py` [[workflows/personal-data-guard/scripts/CONTEXT]] - Entry point and library. Provides `--check` (read-only scan; default and only mode) with `--json` for the audit hook. The pure helpers `derive_name_markers`, `derive_env_emails`, `load_denylist`, `_email_allowed`, `_is_real_username`, and `scan_text` are unit-tested without touching the filesystem.

## Inputs
No required flags. Optional:
- `--check` - Read-only scan (the default; there is no fix mode).
- `--json` - Emit findings as a JSON object on stdout (for the audit hook).

Reads `USER.md` [[USER]], `.env`, and `config/denylist.txt` at runtime if present; discovers files with the `git` CLI.

## Outputs
- Findings report on stdout (human or JSON).
- Exit code 1 when a FAIL finding exists, else 0. Writes no files; makes no LOG.md entry.

## Steps
Run from anywhere:

```
python workflows/personal-data-guard/scripts/run.py --check [--json]
```

## Dependencies
- `AGENTS.md` [[AGENTS]] (root) - The personal-data isolation rules this script enforces.
- `USER.md` [[USER]] and `.env` (root, gitignored) - Read at runtime for name and email markers.
- `workflows/personal-data-guard/config/denylist.txt` (gitignored, optional) - Extra personal nouns.
- Python 3.9+ standard library (argparse, os, re, subprocess, json, pathlib); the `git` CLI.

## Known Issues
- File discovery depends on `git ls-files`; outside a checkout the script scans nothing and emits one INFO note.
- Detectors are heuristic. The home-path detector only treats a captured segment as a username if it looks like a real account name (`_is_real_username`), so regex fragments in other scripts' source (for example `/home/[A-Za-z` in a path pattern) are not mistaken for a leak; an unusual layout could still be missed or, rarely, mis-flagged.
- Email and username allowlists (`ALLOWLIST_EMAIL_DOMAINS`, `PLACEHOLDER_USERS`) are denylist-style: a new placeholder convention not yet listed could produce a false positive until added.

## Revision History
- 2026-06-25 - Initial creation. Read-only check script with ASCII-safe, runtime-sourced markers; git-scoped discovery; placeholder allowlists for emails and path usernames.
