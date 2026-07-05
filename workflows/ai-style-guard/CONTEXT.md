# AI-Style Guard

**Last modified:** 2026-07-04

## Purpose
Catches AI writing tells in newly authored content before it can be committed. It is a deterministic, read-only checker that scans only added or changed lines (via `git diff`) in tracked text files plus new untracked files, so legacy text is never reported and the AGENTS.md "going forward" writing-style rule is enforced exactly as written. It mirrors the encoding-guard and personal-data-guard pattern: a standalone CLI that the full audit also consumes as an additive, advisory hook. It flags two tiers, both advisory: tier 1 (WARN) high-confidence typographic markers (em or en dash, smart quotes, ellipsis character, non-breaking space) and stock AI phrases; tier 2 (INFO) a tunable single-word denylist. It enforces the writing-style rule in `AGENTS.md` [[AGENTS]] that previously relied entirely on AI discipline. There is deliberately no fix mode, because replacing a typographic marker needs human judgement about the right replacement.

## Contents
- scripts/ - `workflows/ai-style-guard/scripts/` [[workflows/ai-style-guard/scripts/CONTEXT]] - The `run.py` entry point (read-only `--check`, with `--json`, `--since`, `--base`, and `--strict`) plus the pure diff-parser and detector helpers.
- tests/ - `workflows/ai-style-guard/tests/` [[workflows/ai-style-guard/tests/CONTEXT]] - Unit tests for the hunk parser, the detectors, the config loader, and the self-exemption guarantee.
- config/ - `workflows/ai-style-guard/config/` [[workflows/ai-style-guard/config/CONTEXT]] - Holds `ai-tells.yaml`, the tracked single source of truth for the typographic markers, stock phrases, and word denylist.
- archived/ - `workflows/ai-style-guard/archived/` [[workflows/ai-style-guard/archived/CONTEXT]] - Holds the personal, gitignored design-time plan and handover document that drove the build. Individual files are not listed (local-only content).

## Inputs
- Added or changed lines, discovered via `git diff --unified=0 <ref>` plus new untracked text files. Git must be available; without it the guard reports a single INFO note and scans nothing.
- `config/ai-tells.yaml` - the tracked tells definition, read at runtime. If absent or unreadable the guard degrades to an INFO note and scans nothing, keeping a single source of truth.

## Outputs
- A findings report on stdout (human text, or `--json` for the audit hook). WARN for tier-1 typographic markers and stock phrases; INFO for tier-2 denylist words and degraded notes.
- Exit code 0 by default (purely advisory). With `--strict`, exit 1 when any WARN exists, so a pre-commit hook or CI can gate on it.
- No files are written and no LOG.md entry is made on a check run (the guard is read-only, like the audit and the other content guards).

## Steps
1. Scan added or changed lines for AI writing tells (read-only):
   `python workflows/ai-style-guard/scripts/run.py --check [--json] [--since REF | --base BRANCH] [--strict]`
2. Review any WARN findings and reword the flagged content (the guard reports file:line; it never edits).
3. Re-run until the report is clean (or until only advisory INFO remains).
4. Append LOG.md only when run as a deliberate, logged workflow step (the read-only check and the audit-hook invocation do not log).

## Dependencies
- `AGENTS.md` [[AGENTS]] (root) - Defines the writing-style rule this guard enforces mechanically, and points at `config/ai-tells.yaml` as the enforced source.
- `SOUL.md` [[SOUL]] (root) - The production-writing tell list that seeded the config.
- `config/ai-tells.yaml` - The tells definition, read at runtime.
- `workflows/audit/` [[workflows/audit/CONTEXT]] - The full audit shells out to this guard's `--check --json --base main` and merges its WARN findings under an `ai-style` label.
- `workflows/encoding-guard/` [[workflows/encoding-guard/CONTEXT]] - Complementary, not overlapping: encoding-guard only folds smart punctuation in files that are already corrupted (invalid UTF-8 or mojibake) and deliberately leaves a legitimate marker in a clean file alone. This guard fills that gap by flagging unwanted-but-valid markers in clean authored content.
- The project `.venv` runtime via `workflows/biblio-tools/scripts/runtime.py` [[workflows/biblio-tools/scripts/CONTEXT]]: `main()` calls `ensure_project_runtime()` so PyYAML (used to read the config) is always present. This guard bootstraps rather than degrading, because a style check that silently skipped for want of its parser would report a false clean. The `git` CLI is needed for diff discovery; otherwise the Python 3.9+ standard library.

## Known Issues
- Detection is diff- and pattern-based, so it covers deterministic (kind-1) tells only. Tone and flow tells (kind-2) are out of scope and stay a written convention or an optional AI-review step.
- The guard scopes to changed lines, so a tell on an unchanged line is never reported. This is intentional: the rule applies to new and edited content only.
- The guard skips its own workflow directory, because its config and tests necessarily contain example tells. A tell genuinely introduced inside `workflows/ai-style-guard/` [[workflows/ai-style-guard/CONTEXT]] would therefore not be self-reported.
- Tier-2 denylist words are common in technical prose (for example "robust", "navigate"), so they are INFO only and the audit hook drops them. Tune the list in `config/ai-tells.yaml`.
- Findings messages quote the location (file:line). A saved `--json` report or audit `last-report.md` will contain those; both outputs are gitignored.
- This guard bootstraps into the project `.venv` (via `ensure_project_runtime()`) rather than degrading when PyYAML is absent: its job is the check, so it must actually run. If the `.venv` is not set up it fails loudly and names the `setup.py` fix, instead of silently skipping and reporting a false clean.

## Revision History
- 2026-06-25 - Initial creation. Diff-scoped read-only checker (hunk parser, tier-1 typographic and phrase detectors, tier-2 word denylist), tracked YAML config single source of truth, `--since`/`--base`/`--strict` flags, advisory audit-hook integration under an `ai-style` label, and unit tests. First live run flagged em dashes in branch content added during the personal-data-guard work.
- 2026-06-25 - Added archived/ subdirectory holding the gitignored design-time build plan, per the archiving convention.
- 2026-07-04 - `main()` now bootstraps into the project `.venv` via `ensure_project_runtime()` and imports PyYAML unconditionally, so the guard actually runs rather than silently skipping when PyYAML is missing (a guard that skips reports a false clean). Part of the loud-degrade work: the audit/close-out now report a check that could not run as DEGRADED.
