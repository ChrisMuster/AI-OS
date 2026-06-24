# Audit

**Last modified:** 2026-06-24

## Purpose
Walks every directory in the Book Dragon project and checks for structural compliance: missing CONTEXT.md or LOG.md files, missing required sections, inconsistent Last modified and Revision History metadata, broken Contents paths, unlisted subdirectories, and dead Obsidian [[links]]. It can also run a lightweight targeted check against named directories immediately after context maintenance. The full audit checks that AGENTS.md has not exceeded its line-count threshold (600 lines), runs code hygiene checks across all Python scripts, and validates the structural knowledge graph — merging its actionable (WARN/FAIL) findings under a `knowledge-graph` label so a graph regression surfaces in the same report. The graph step is additive and advisory (it never changes the audit's exit code) and degrades to a single INFO note if the graph cannot be validated. A full audit also runs the encoding-guard check and merges its WARN/FAIL findings under an `encoding` label, so an encoding regression (a file that stops being valid UTF-8, new mojibake, or a text-mode subprocess call with no explicit encoding) surfaces in the same report; this step is additive and advisory in the same way.

## Contents
- scripts/ — `workflows/audit/scripts/` [[workflows/audit/scripts/CONTEXT]] — Automation scripts for this workflow; run.py is the main audit entry point.
- tests/ — `workflows/audit/tests/` [[workflows/audit/tests/CONTEXT]] — Unit tests for the knowledge-graph audit-hook merge helper and its graceful-degradation paths, plus a one-command runner.

## Inputs
No inputs are required for a full audit. Targeted mode accepts one or more project-relative directories or CONTEXT.md paths after `--context`.

## Outputs
- Audit report printed to stdout.
- Optionally: `workflows/audit/last-report.md` [[workflows/audit/last-report]] — saved report from the most recent run (only created when --save is passed).

## Steps
1. Run the audit script from anywhere (a full run also validates the structural knowledge graph):
   `python workflows/audit/scripts/run.py [--save] [--no-graph]`
2. After changing CONTEXT.md files during normal work, run:
   `python workflows/audit/scripts/run.py --context <directory> [<directory> ...]`
   (Targeted mode is fast and graph-free — it never validates the graph.)
3. Review the report. Failures must be fixed; warnings should be investigated. Graph findings appear under the `knowledge-graph` label.
4. Fix any real issues found, then re-run the same mode to confirm clean.

## Dependencies
- `AGENTS.md` [[AGENTS]] (root) — Defines the structural rules (CONTEXT.md schema, LOG.md requirement) that this workflow audits against.
- `workflows/audit/scripts/run.py` [[workflows/audit/scripts/CONTEXT]] — The automation script that performs all checks.
- `workflows/knowledge-graph/scripts/run.py` [[workflows/knowledge-graph/scripts/CONTEXT]] — A full audit shells out to its `validate --json` command (structural graph only) and merges the WARN/FAIL findings. Decoupled via subprocess, not import; a missing or broken graph degrades to an INFO note.
- `workflows/encoding-guard/scripts/run.py` [[workflows/encoding-guard/scripts/CONTEXT]] — A full audit shells out to its `--check --json` command and merges the WARN/FAIL findings under an `encoding` label. Decoupled via subprocess, not import; a missing or broken checker degrades to an INFO note.

## Known Issues
- The audit is read-only — it reports issues but does not fix them. Fixing is a manual step.
- last-report.md is not listed in Contents because it only exists after the first --save run. If it exists, it is the saved output of the most recent audit.
- Requires git to be available for gitignore-aware directory pruning. Falls back to auditing all directories if git is not found.
- The knowledge-graph validation step is best-effort: if the knowledge-graph CLI is absent, crashes, or returns unparseable output, the audit adds a single INFO note ("graph validation skipped — …") and still completes with exit 0. It validates the structural graph only (never `--layer`), so merged findings carry no personal/gitignored names.

## Revision History
Earlier history archived to LOG.md on 2026-06-24.
- 2026-06-03 — Added dead [[link]] check. Audit now warns on any project [[links]] that point to non-existent .md files. Wiki-internal links are ignored.
- 2026-06-05 — Added CLAUDE.md line-count check. Warns when CLAUDE.md exceeds 600 lines, prompting a review and reorganisation into a rules/ directory.
- 2026-06-08 — Added code hygiene check: scans all project Python scripts for strftime calls with time components but no timezone offset. Fixed format_report to use isoformat (was itself a timezone-less timestamp).
- 2026-06-09 — Line-count check updated from CLAUDE.md to AGENTS.md. Dependencies updated. AGENTS added to dead-link root stems.
- 2026-06-09 — GEMINI added to dead-link root stems (Phase 2 AI-agnostic transition).
- 2026-06-12 — Added deterministic Last modified and Revision History checks plus targeted `--context` mode for immediate context maintenance.
- 2026-06-17 — Replaced `rglob` tree walk with `os.walk` and `git check-ignore` pruning. Gitignored directories (collections, state, wiki content) are now skipped entirely — eliminates false positives and avoids traversing large data directories.
- 2026-06-23 — Knowledge-graph audit-hook integration: a full audit now also validates the structural graph (via the knowledge-graph `validate --json` CLI) and merges its WARN/FAIL findings under a `knowledge-graph` label; added the `--no-graph` opt-out and a new `tests/` directory for the merge helper. Additive and advisory (exit code unchanged); degrades to an INFO note on failure. Targeted `--context` mode stays graph-free.
- 2026-06-24 - Encoding-guard hook: a full audit now also runs the encoding-guard `--check --json` and merges its WARN/FAIL findings under an `encoding` label (subprocess, not import; degrades to an INFO note on failure). Added a UTF-8 stdout/stderr reconfigure so the report no longer mojibakes when piped on Windows, and pinned `encoding="utf-8"` on the script's subprocess calls.
