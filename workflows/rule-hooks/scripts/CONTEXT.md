# Rule Hooks - Scripts

**Last modified:** 2026-08-11

## Purpose
The evaluator code for the rule-hooks workflow: one entry point plus the shared contract and the rule and adapter packages.

## Contents
- run.py - `workflows/rule-hooks/scripts/run.py` [[workflows/rule-hooks/scripts/CONTEXT]] - Entry point. Parses `--ai`/`--reinject`/`--precommit`, resolves the project root, dispatches an event through the right adapter and rules, and applies the fail-safe (any failure degrades to allow + fire-log). In `--precommit` mode it runs two gates in order: a hard block on a personal-data leak (rule B3 layer 2, exit 1), then a warn-not-block doc-sync CONTEXT/LOG drift advisory (`format_doc_sync_advisory` + `_precommit_doc_sync`, running the doc-sync guard in `--staged` mode and filtering the findings it *prints* to WARN, so only drift is announced) that prints a loud block but always allows the commit (exit 0), since close-out is the deterministic gate. The same step also appends one `doc_sync_recording` fire-log record per fire, carrying that fire's `INFO` and `DEGRADED` counts, which is the collection path for the doc-sync guard's switched-off CONTEXT-only LOG clause.
- core.py - `workflows/rule-hooks/scripts/core.py` [[workflows/rule-hooks/scripts/CONTEXT]] - Shared contract: the `Context` and `Decision` types, the three-part `format_block` helper, and the shell-command parsing helpers - `split_segments` (used by A6 to parse destructive commands rather than substring-match), `redirect_targets`, and the broader `shell_write_targets` (redirections plus cp/mv/tee/sed -i, used by A4 and A7 to detect protected-file writes).
- rules/ - `workflows/rule-hooks/scripts/rules/` [[workflows/rule-hooks/scripts/rules/CONTEXT]] - One module per rule plus the category registry.
- adapters/ - `workflows/rule-hooks/scripts/adapters/` [[workflows/rule-hooks/scripts/adapters/CONTEXT]] - Per-AI input parsing and output block-contract (Claude, Codex).

## Inputs
- A PreToolUse event as JSON on stdin (in `--ai` mode); none for `--reinject`; the git index (in `--precommit` mode).

## Outputs
- A process exit code (0 allow, 2 block for the per-AI hooks, 1 block for the pre-commit personal-data gate) plus the block-and-explain text on the AI's channel; warns and blocks appended to the gitignored fire-log. In `--precommit` mode, doc-sync drift prints the advisory to stderr but keeps exit 0 (warn-not-block); findings at any other severity, including DEGRADED, print nothing. Every fire that reaches the doc-sync step also appends one `doc_sync_recording` record (`{"event", "info", "degraded"}`) to the gitignored fire-log, including a fire with no findings at all, which carries zeroes as the denominator.

## Steps
N/A. This is the script directory for the rule-hooks workflow; the workflow steps live in the parent CONTEXT.md.

## Dependencies
- `workflows/rule-hooks/CONTEXT.md` [[workflows/rule-hooks/CONTEXT]] - Parent workflow.
- `workflows/personal-data-guard/scripts/run.py` [[workflows/personal-data-guard/scripts/CONTEXT]] - Loaded lazily by the B3 rule and the `--precommit` gate as the detection source.
- `workflows/doc-sync-guard/scripts/run.py` [[workflows/doc-sync-guard/scripts/CONTEXT]] - Loaded lazily by the `--precommit` gate (in `--staged` mode) as the CONTEXT/LOG drift detection source for the warn-not-block advisory.
- Python 3.9+ standard library; the `git` CLI.

## Known Issues
- **What `_precommit_doc_sync` prints and what it reads are two different sets, and both are current.** It still filters what it *prints* to `f[0] == "WARN"`, so drift and only drift is announced at commit time; that filter has not been relaxed. Separately it *reads* the guard's `INFO` and `DEGRADED` counts and writes them to the fire-log, so the guard's switched-off CONTEXT-only LOG clause can be measured against real commits. A reader who meets only the second half will think the filter was loosened; a reader who meets only the first will think `INFO` is discarded. Neither is true.
- `_precommit_doc_sync` filters the guard's findings to `f[0] == "WARN"`, so the guard's DEGRADED severity (a component of the guard that could not run, today its output-inventory probe on an interpreter without PyYAML) prints nothing at commit time. That is the intended boundary while nothing consumes the inventory answers - the advisory exists to name directories needing a documentation update, and a missing package is not one - and it is separate from the `except Exception` above it, which catches a guard that could not run at all. It stops being safe once the guard acts on the inventory, and the guard-coverage scope extension carries a locked decision requiring this advisory to be made degrade-aware at that point, presenting the degrade on its own line rather than folding it into the drift list. The producer half of the boundary is recorded in `workflows/doc-sync-guard/scripts/CONTEXT.md` [[workflows/doc-sync-guard/scripts/CONTEXT]].
- `run.py` inserts its own directory on `sys.path` so `core`, `rules`, and `adapters` import cleanly when invoked as a script path. Kept stdlib-only and light at import time; the personal-data guard is imported lazily only when a write is actually checked.

## Revision History
- 2026-06-30 - Initial creation: run.py, core.py, and the rules/ and adapters/ packages.
- 2026-07-01 - Close-out correction: updated the core.py description to name the `shell_write_targets` helper (the broadened A4/A7 shell-write detection added 2026-06-30) and correct the helper-to-rule mapping.
- 2026-07-07 - The `--precommit` gate now also runs a warn-not-block doc-sync CONTEXT/LOG drift advisory after the personal-data block (doc-sync-guard build part 4). Refactored `run_precommit` into `_precommit_personal_data` + `_precommit_doc_sync` + the pure `format_doc_sync_advisory`, with a shared `_load_guard` loader; the advisory runs the doc-sync guard in `--staged` mode, prints a loud block naming each drift directory, and always exits 0. A guard crash is swallowed (never disrupts a commit).
- 2026-08-04 - Documented `_precommit_doc_sync`'s severity filter where the code that applies it lives: it keeps WARN only, so the guard's DEGRADED findings print nothing at commit time. Deliberate today, and required to change when the guard starts acting on the output inventory. Contents, Outputs and Known Issues updated; no behaviour change.
- 2026-08-04 - Line endings pinned on the `fire-log.jsonl` append in `run.py`, which now passes `newline="\n"` explicitly. Part of the project-wide pass closing this defect class at all 48 write sites.
- 2026-08-11 - Guard-coverage stage 14 collection path. `_precommit_doc_sync` now appends one `doc_sync_recording` fire-log record per fire through the existing `fire_log()` helper, carrying that fire's `INFO` and `DEGRADED` counts, so the doc-sync guard's switched-off CONTEXT-only LOG clause accumulates evidence at commit time rather than being measured by one late command that would see only whatever happened to be uncommitted at that moment. Three placement constraints, each with its own test: the write is inside the success path, so the existing exception path still returns without writing; a fire with no findings is recorded too, carrying zeroes, because a numerator with no denominator cannot be turned into a rate; and a degraded fire is recorded rather than dropped, so the read can exclude it and the exclusion leaves a trace. A fire the personal-data gate blocks writes nothing, which is untouched behaviour in `run_precommit` and now has a test pinning the absence. The printed output is unchanged: the advisory still filters to WARN, and Known Issues now states that the print filter and the read set are separate so neither half is mistaken for the other.
