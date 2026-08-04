# Backlog Guard

**Last modified:** 2026-08-04

## Purpose
Protect `memory/backlog.md` from accidental data loss by taking local snapshots before edits and checking after edits that required sections, item counts, and backlog-item size limits still look sane.

## Contents
- scripts/ - `workflows/backlog-guard/scripts/` [[workflows/backlog-guard/scripts/CONTEXT]] - CLI implementation for snapshots, count checks, and restores.
- tests/ - `workflows/backlog-guard/tests/` [[workflows/backlog-guard/tests/CONTEXT]] - Hermetic tests for parsing, snapshots, rotation, checks, and restore behaviour.

## Inputs
- `memory/backlog.md` [[memory/CONTEXT]] - the active backlog document to protect.
- `memory/backlog-backups/` [[memory/backlog-backups/CONTEXT]] - local snapshot store used as the comparison baseline and restore source.
- Optional command-line allowances when a task deliberately moves items out of Active or Build Only When Needed.

## Outputs
- Timestamped exact-copy snapshots in `memory/backlog-backups/`.
- Pass/fail status on stdout/stderr for required sections, count drops, and oversized backlog items.
- Restored `memory/backlog.md` content when `--restore <snapshot> --force` is run.
- `LOG.md` entries for workflow runs that create snapshots or restore from one, plus backup-store log entries in `memory/backlog-backups/LOG.md`.

## Steps
1. Before editing `memory/backlog.md`, run `python workflows/backlog-guard/scripts/run.py --snapshot --reason "<why>"`.
2. Edit the backlog.
3. After editing, run `python workflows/backlog-guard/scripts/run.py --check`; if items were deliberately archived, pass the explicit drop allowance for the number moved.
4. If the check fails because content vanished unexpectedly, inspect the latest snapshot and restore it with `--restore <snapshot> --force` or manually recover the missing content.
5. Append LOG.md with a completion or failure entry.

## Dependencies
- `AGENTS.md` [[AGENTS]] - requires the snapshot/check procedure around backlog edits.
- `memory/backlog.md` [[memory/CONTEXT]] - the protected document.
- `memory/backlog-backups/` [[memory/backlog-backups/CONTEXT]] - the private local backup store.
- Python 3.9 or later.

## Known Issues
- The guard counts top-level Markdown bullets that begin with bold text under `## Active` and `## Build Only When Needed`. If the backlog format changes, the parser must change with it.
- The guard cannot prove that the wording of an item is correct; it catches missing sections, count drops, and oversized entries.
- Snapshots are local and gitignored. They help recover this machine's backlog state but are not a public/shared history.

## Revision History
- 2026-08-04 - Initial creation.
- 2026-08-04 - Snapshot and restore actions now append the backup store's own LOG.md as well as the workflow LOG.md.
