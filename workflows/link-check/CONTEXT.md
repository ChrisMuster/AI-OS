# Link Check

**Last modified:** 2026-08-11

## Purpose
Manages Obsidian wiki links in all Book Dragon CONTEXT.md files. Adds `[[links]]` alongside existing prose path references so the Obsidian knowledge graph shows connections between directories, and audits those links for dead targets after renames or deletions.

## Contents
- scripts/ — `workflows/link-check/scripts/` [[workflows/link-check/scripts/CONTEXT]] — The run.py script that handles link insertion, auditing, and auto-fix.
- tests/ - `workflows/link-check/tests/` [[workflows/link-check/tests/CONTEXT]] - Tests for `--link` mode and the regions it must not rewrite, discovered and run by the close-out verifier.

## Inputs
No inputs required. The script reads the existing project structure and CONTEXT.md files.

## Outputs
- Changes written in-place to CONTEXT.md files (--link and --fix modes).
- Report printed to stdout.
- Optionally: `workflows/link-check/last-report.md` — saved report from the most recent run (only created when --save is passed).

## Steps
1. **Add links (first run or after adding new directories):**
   `python workflows/link-check/scripts/run.py --link [--dry-run]`
   Scans all CONTEXT.md files and appends `[[path/CONTEXT]]` after backtick path references that do not already have a link. Fenced code blocks and Revision History sections are skipped: history is a dated record rather than editable content, and the section is taken to run to the next level-two heading.

2. **Audit for dead links:**
   `python workflows/link-check/scripts/run.py` (or `--audit`)
   Scans all CONTEXT.md files for `[[links]]` and reports any that point to files that no longer exist.

3. **Auto-fix dead links:**
   `python workflows/link-check/scripts/run.py --fix [--dry-run]`
   For each dead link, searches the project for the target filename. If exactly one match is found, rewrites the link. Ambiguous or missing targets are flagged for manual review.

4. Append LOG.md with a completion or failure entry.

## Dependencies
- `AGENTS.md` (root) [[AGENTS]] — Defines the CONTEXT.md schema and project structure that this workflow operates on.
- `workflows/link-check/scripts/run.py` [[workflows/link-check/scripts/CONTEXT]] — The script that performs all link operations.
- `workflows/link-check/tests/` [[workflows/link-check/tests/CONTEXT]] - Covers `--link` mode and pins the Revision History skip boundary.
- `workflows/audit/` [[workflows/audit/CONTEXT]] - Not called by this workflow, but its section parser defines where Revision History ends. The two tools are meant to agree on that boundary; if the audit's definition changes, `--link` starts editing history the audit considers off limits, or stops editing content it considers fair game.

## Known Issues
- Wiki-internal links (e.g. `[[page-name]]` inside wiki CONTEXT.md files) are skipped — the audit only checks links that start with a known top-level directory name or match a root file name.
- The --fix mode can only auto-resolve a dead link when exactly one file in the project matches the target filename. Renames that result in two files with the same name require manual resolution.
- last-report.md is not listed in Contents because it only exists after the first --save run.
- The Revision History skip covers `--link` only. `--fix` rewrites a renamed target with a blanket `content.replace()` over the whole file, so it will still edit a `[[link]]` sitting inside a dated entry. The exposure is narrower than the `--link` defect was, because `--fix` only rewrites links that are already present rather than inserting new ones, and the links currently in history entries are largely ones `--link` put there before this skip landed. It is recorded rather than fixed because it is outside the scope of the stage that added the skip, and because the right behaviour is a judgement rather than obvious: leaving a dead link in a dated entry preserves the record accurately, and repairing it keeps the graph navigable.

## Revision History
- 2026-06-03 — Initial creation.
- 2026-06-09 — Dependencies updated from CLAUDE.md to AGENTS.md (AI-agnostic transition).
- 2026-06-09 — GEMINI.md added to ROOT_LINK_FILES (Phase 2 AI-agnostic transition).
- 2026-08-11 - `--link` no longer rewrites Revision History, and the workflow gained the tests it had never had. `add_links_to_file` tracked fenced code blocks and nothing else, so any project path mentioned inside a dated entry had a `[[link]]` inserted into it on every run; it hit `workflows/rule-hooks/tests/CONTEXT.md` twice and `workflows/encoding-guard/CONTEXT.md` once, the latter an entry written minutes earlier, so it was a hazard to new history and not only to old. An `in_revision_history` flag now sits beside the existing `in_code_block` flag and is evaluated after it, so a heading written inside a fence stays sample text. The section is taken to end at the next level-two heading, matching the audit's own section parser, which is what makes the two tools agree on whether history is editable. Added `tests/` with 9 tests: the positive, rejection, and negative controls for the skip, the boundary cases, and three properties that predate it. The suite was run against the unfixed script first and 4 of the 9 failed, so it was shown to fail for the stated reason rather than only to pass afterwards. A related weakness in `--fix` was found and recorded in Known Issues rather than fixed, being outside this change's scope.
