# Rule Hooks - Scripts

**Last modified:** 2026-07-07

## Purpose
The evaluator code for the rule-hooks workflow: one entry point plus the shared contract and the rule and adapter packages.

## Contents
- run.py - `workflows/rule-hooks/scripts/run.py` [[workflows/rule-hooks/scripts/CONTEXT]] - Entry point. Parses `--ai`/`--reinject`/`--precommit`, resolves the project root, dispatches an event through the right adapter and rules, and applies the fail-safe (any failure degrades to allow + fire-log). In `--precommit` mode it runs two gates in order: a hard block on a personal-data leak (rule B3 layer 2, exit 1), then a warn-not-block doc-sync CONTEXT/LOG drift advisory (`format_doc_sync_advisory` + `_precommit_doc_sync`, running the doc-sync guard in `--staged` mode) that prints a loud block but always allows the commit (exit 0), since close-out is the deterministic gate.
- core.py - `workflows/rule-hooks/scripts/core.py` [[workflows/rule-hooks/scripts/CONTEXT]] - Shared contract: the `Context` and `Decision` types, the three-part `format_block` helper, and the shell-command parsing helpers - `split_segments` (used by A6 to parse destructive commands rather than substring-match), `redirect_targets`, and the broader `shell_write_targets` (redirections plus cp/mv/tee/sed -i, used by A4 and A7 to detect protected-file writes).
- rules/ - `workflows/rule-hooks/scripts/rules/` [[workflows/rule-hooks/scripts/rules/CONTEXT]] - One module per rule plus the category registry.
- adapters/ - `workflows/rule-hooks/scripts/adapters/` [[workflows/rule-hooks/scripts/adapters/CONTEXT]] - Per-AI input parsing and output block-contract (Claude, Codex).

## Inputs
- A PreToolUse event as JSON on stdin (in `--ai` mode); none for `--reinject`; the git index (in `--precommit` mode).

## Outputs
- A process exit code (0 allow, 2 block for the per-AI hooks, 1 block for the pre-commit personal-data gate) plus the block-and-explain text on the AI's channel; warns and blocks appended to the gitignored fire-log. In `--precommit` mode, doc-sync drift prints the advisory to stderr but keeps exit 0 (warn-not-block).

## Steps
N/A. This is the script directory for the rule-hooks workflow; the workflow steps live in the parent CONTEXT.md.

## Dependencies
- `workflows/rule-hooks/CONTEXT.md` [[workflows/rule-hooks/CONTEXT]] - Parent workflow.
- `workflows/personal-data-guard/scripts/run.py` [[workflows/personal-data-guard/scripts/CONTEXT]] - Loaded lazily by the B3 rule and the `--precommit` gate as the detection source.
- `workflows/doc-sync-guard/scripts/run.py` [[workflows/doc-sync-guard/scripts/CONTEXT]] - Loaded lazily by the `--precommit` gate (in `--staged` mode) as the CONTEXT/LOG drift detection source for the warn-not-block advisory.
- Python 3.9+ standard library; the `git` CLI.

## Known Issues
- `run.py` inserts its own directory on `sys.path` so `core`, `rules`, and `adapters` import cleanly when invoked as a script path. Kept stdlib-only and light at import time; the personal-data guard is imported lazily only when a write is actually checked.

## Revision History
- 2026-06-30 - Initial creation: run.py, core.py, and the rules/ and adapters/ packages.
- 2026-07-01 - Close-out correction: updated the core.py description to name the `shell_write_targets` helper (the broadened A4/A7 shell-write detection added 2026-06-30) and correct the helper-to-rule mapping.
- 2026-07-07 - The `--precommit` gate now also runs a warn-not-block doc-sync CONTEXT/LOG drift advisory after the personal-data block (doc-sync-guard build part 4). Refactored `run_precommit` into `_precommit_personal_data` + `_precommit_doc_sync` + the pure `format_doc_sync_advisory`, with a shared `_load_guard` loader; the advisory runs the doc-sync guard in `--staged` mode, prints a loud block naming each drift directory, and always exits 0. A guard crash is swallowed (never disrupts a commit).
