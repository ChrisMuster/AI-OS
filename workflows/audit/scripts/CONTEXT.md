# Scripts

**Last modified:** 2026-06-12

## Purpose
Contains the audit script for the audit workflow. run.py can walk the full project or validate only named context directories.

## Contents
- run.py — `workflows/audit/scripts/run.py` [[workflows/audit/scripts/CONTEXT]] — Main audit script. Checks structural compliance, validates Last modified and Revision History consistency, supports targeted context maintenance, and runs full-project code hygiene checks.

## Inputs
No required inputs. Optional flag:
- `--save` — Saves the report to `workflows/audit/last-report.md` [[workflows/audit/last-report]].
- `--context <directory> [<directory> ...]` — Checks only the named project-relative directories or CONTEXT.md files.

## Outputs
- Audit report printed to stdout.
- Optionally: `workflows/audit/last-report.md` [[workflows/audit/last-report]] (when --save is passed).
- Updated `workflows/audit/LOG.md` — started and completed entries appended.
- Updated root `LOG.md` — completed entry appended.

## Steps
Run from anywhere:

```
python workflows/audit/scripts/run.py [--save]
python workflows/audit/scripts/run.py --context <directory> [<directory> ...]
```

## Dependencies
- All `CONTEXT.md` and `LOG.md` files in the project — the script reads these to perform its checks.
- `workflows/audit/LOG.md` — Appended on every run.
- `LOG.md` (root) — Appended on every run.

## Known Issues
- Wiki-root CONTEXT.md files (LLM Wiki format) are detected by the presence of `## Folder structure` and have their standard section checks skipped. Any wiki that uses a different non-standard format may generate false warnings.
- The unlisted-subdirectory check only catches subdirectory names not mentioned anywhere in the Contents section text. Subdirectories mentioned in prose (rather than as backtick paths) will not be flagged.
- Contents path checking only covers paths that start with a known top-level directory name (workflows/, wikis/, skills/, templates/). Relative paths using other conventions are skipped silently.
- Directories named `raw/` or `data/` are treated as source-data boundaries: the directory itself is checked, but its contents are not walked. This prevents false failures from runtime-generated or imported data files (e.g. `session-search/data/archive/`).
- Stale-phrase and parent-relative path checks strip fenced code blocks and the Revision History section before scanning. Phrases inside inline code (single backticks) are still matched — this may produce occasional false positives if prose examples contain the target patterns. See STALE_PHRASES in run.py for the exact patterns checked.
- Metadata consistency is date-based. It catches mismatches between Last modified and visible Revision History metadata, but cannot prove that unchanged metadata accompanied an uncommitted prose-only edit; the immediate-maintenance rule and targeted command cover that workflow boundary.

## Revision History
- 2026-05-27 — Initial creation.
- 2026-05-29 — Added two new warning checks: parent-relative path detection and stale build-phrase detection. Both skip fenced code blocks and the Revision History section to reduce false positives. See STALE_PHRASES in run.py for the exact phrase patterns.
- 2026-06-03 — Added dead [[link]] check. Warns on project [[links]] pointing to non-existent .md files; wiki-internal links ignored.
- 2026-06-08 — Added code hygiene check (check_python_scripts): scans all project .py files for strftime with time but no timezone. Fixed format_report to use isoformat.
- 2026-06-12 — Added metadata consistency checks and targeted `--context` mode for lightweight post-task validation.
