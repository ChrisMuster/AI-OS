# Memory Diff - Scripts

**Last modified:** 2026-07-06

## Purpose
The deterministic (~90%) half of the memory-diff workflow: read `memory/LOG.md`,
compute the entries added, updated, or archived since the content watermark, and
report them. Advancing the watermark is a separate mode so the surfacing step
stays read-only.

## Contents
- run.py - `workflows/memory-diff/scripts/run.py` [[workflows/memory-diff/scripts/CONTEXT]] - entry point. `--status`
  (default; read-only) reports the delta as text or `--json`; `--ack` advances the
  watermark (honours `--dry-run`).
- diff.py - `workflows/memory-diff/scripts/diff.py` [[workflows/memory-diff/scripts/CONTEXT]] - reads `memory/LOG.md` into
  entry lines and categorises them (created->Added, modified->Updated,
  archived->Archived, else Other).
- state.py - `workflows/memory-diff/scripts/state.py` [[workflows/memory-diff/scripts/CONTEXT]] - the content watermark:
  load/save, and the delta logic that returns the entries after the last-seen line.
- config.py - `workflows/memory-diff/scripts/config.py` [[workflows/memory-diff/scripts/CONTEXT]] - tunable constants
  (memory log path, soft display cap).

## Inputs
- `memory/LOG.md` [[memory/CONTEXT]] - the append-only memory audit trail.
- `state.json` in the workflow directory - the stored watermark (created on first
  `--ack`).
- Optional environment overrides for the three paths, used only by the integration
  tests so they never touch real state: `MEMORY_DIFF_MEMORY_LOG`,
  `MEMORY_DIFF_STATE`, `MEMORY_DIFF_WORKFLOW_LOG`.

## Outputs
- The grouped delta (stdout, text or `--json`) for `--status`. `--json` also emits
  a `through` token (for the ack handshake) and an `anomaly` flag.
- On an anomaly (corrupt/unreadable state, a present-but-invalid state file with no
  usable watermark, missing/unreadable memory log, or a lost watermark): a WARNING
  and a non-zero exit (2); nothing is advanced.
- Updated `state.json` and a workflow LOG.md entry for `--ack` when it surfaces
  real changes.

## Steps
1. `run.py --status` calls diff.read_log_entries, then state.new_entries against
   the stored watermark, and prints the grouped result (or a no-change line).
   `read_log_entries` raises `LogError` and `load_state` raises `StateError` when
   their inputs are broken, and `new_entries` returns a WATERMARK_MISSING status
   when a stored watermark is absent from the log; run.py turns each into a loud
   anomaly rather than a silent baseline.
2. `run.py --ack` advances the watermark to the newest entry and logs a completion
   entry when it acknowledged surfaced changes. It refuses on an anomaly (unless
   `--force-baseline`) and, when given `--through <token>`, refuses if the log's
   newest entry no longer matches the token from `--status --json`.
3. Append LOG.md with a completion or failure entry (via `--ack`, or by the caller).

## Dependencies
- `memory/LOG.md` [[memory/CONTEXT]] - the sole data source.
- The parent workflow `workflows/memory-diff/` [[workflows/memory-diff/CONTEXT]]
  and its skill.
- Python standard library only (argparse, json, re, datetime, pathlib). No
  third-party packages.

## Known Issues
- Paths are resolved relative to this file's location (unless the `MEMORY_DIFF_*`
  overrides are set); the scripts must stay in
  `workflows/memory-diff/scripts/` [[workflows/memory-diff/scripts/CONTEXT]] for the project-root and memory-log paths to
  resolve.
- See the parent CONTEXT.md Known Issues for the v1 removal-scope and the loud
  anomaly behaviour (a lost watermark or broken log/state warns and exits non-zero
  rather than re-baselining).

## Revision History
- 2026-07-06 - Initial creation. run.py (status/ack), diff.py (reader/categoriser),
  state.py (content watermark), config.py (constants).
- 2026-07-06 - Codex review fixes: LogError/StateError and a WATERMARK_MISSING
  status make broken inputs loud (non-zero exit) instead of silently baselining;
  `--ack` gains `--through` (race guard) and `--force-baseline` (anomaly reset);
  `line_token` added; `MEMORY_DIFF_*` path overrides added for hermetic testing.
- 2026-07-06 - Codex second-review fix: `state.load_state` validates the shape of
  an existing state file (a JSON object with a missing, non-string, or
  empty/whitespace `seen_line`, e.g. `{}`, now raises `StateError("malformed")`),
  so a present-but-invalid state file is a loud anomaly rather than a silent
  first-run re-baseline. Only a missing file is a first run.
