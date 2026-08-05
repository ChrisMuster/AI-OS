# Scripts

**Last modified:** 2026-08-04

## Purpose
Command-line implementation for the backlog-guard workflow.

## Contents
- run.py - `workflows/backlog-guard/scripts/run.py` [[workflows/backlog-guard/scripts/CONTEXT]] - Takes exact-copy snapshots of `memory/backlog.md`, checks required backlog sections and counts against the latest snapshot, enforces backlog-item size limits, and restores named snapshots on explicit command.

## Inputs
- `memory/backlog.md` [[memory/CONTEXT]] by default, or `BACKLOG_GUARD_BACKLOG` in tests.
- `memory/backlog-backups/` [[memory/backlog-backups/CONTEXT]] by default, or `BACKLOG_GUARD_BACKUP_DIR` in tests.
- Optional CLI flags for snapshot reason, retention count, deliberate count-drop allowances, size limits, and restore target.

## Outputs
- Snapshot files named `YYYYMMDDTHHMMSS+ZZZZ-backlog.md`.
- Pass/fail command output and exit status.
- Restored backlog content when `--restore <snapshot> --force` is used.
- Workflow LOG.md entries and `memory/backlog-backups/LOG.md` entries for snapshot and restore actions.

## Steps
1. Parse command-line action and resolve project paths.
2. Parse `memory/backlog.md` or a snapshot into required top-level sections and bold bullet counts.
3. For `--snapshot`, copy the backlog bytes to a timestamped snapshot and rotate old snapshots.
4. For `--check`, compare current counts and sections to the latest snapshot, applying only explicit drop allowances and size limits.
5. For `--restore`, copy the named snapshot back to `memory/backlog.md` only when `--force` is supplied.
6. Append LOG.md with a completion or failure entry.

## Dependencies
- Python standard library only.
- `workflows/backlog-guard/` [[workflows/backlog-guard/CONTEXT]] - owns the workflow contract.

## Known Issues
- The parser intentionally understands only the current backlog shape. It is not a general Markdown parser.

## Revision History
- 2026-08-04 - Initial creation.
- 2026-08-04 - Snapshot and restore actions now append the backup store's own LOG.md as well as the workflow LOG.md.
