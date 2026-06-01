# Scripts

**Last modified:** 2026-05-27

## Purpose
Contains the audit script for the audit workflow. run.py walks every directory in the project and checks for structural compliance issues.

## Contents
- run.py — `workflows/audit/scripts/run.py` — Main audit script. Checks all directories for missing files, missing CONTEXT.md sections, broken Contents paths, and unlisted subdirectories.

## Inputs
No required inputs. Optional flag:
- `--save` — Saves the report to `workflows/audit/last-report.md`.

## Outputs
- Audit report printed to stdout.
- Optionally: `workflows/audit/last-report.md` (when --save is passed).
- Updated `workflows/audit/LOG.md` — started and completed entries appended.
- Updated root `LOG.md` — completed entry appended.

## Steps
Run from anywhere:

```
python workflows/audit/scripts/run.py [--save]
```

## Dependencies
- All `CONTEXT.md` and `LOG.md` files in the project — the script reads these to perform its checks.
- `workflows/audit/LOG.md` — Appended on every run.
- `LOG.md` (root) — Appended on every run.

## Known Issues
- Wiki-root CONTEXT.md files (LLM Wiki format) are detected by the presence of `## Folder structure` and have their standard section checks skipped. Any wiki that uses a different non-standard format may generate false warnings.
- The unlisted-subdirectory check only catches subdirectory names not mentioned anywhere in the Contents section text. Subdirectories mentioned in prose (rather than as backtick paths) will not be flagged.
- Contents path checking only covers paths that start with a known top-level directory name (workflows/, wikis/, skills/, templates/). Relative paths using other conventions are skipped silently.
- Directories named `raw/` are treated as source-data boundaries: the raw/ directory itself is checked, but its contents are not walked. This prevents false failures from Facebook export data and other imported source files.
- Stale-phrase and parent-relative path checks strip fenced code blocks and the Revision History section before scanning. Phrases inside inline code (single backticks) are still matched — this may produce occasional false positives if prose examples contain the target patterns. See STALE_PHRASES in run.py for the exact patterns checked.

## Revision History
- 2026-05-27 — Initial creation.
- 2026-05-29 — Added two new warning checks: parent-relative path detection and stale build-phrase detection. Both skip fenced code blocks and the Revision History section to reduce false positives. See STALE_PHRASES in run.py for the exact phrase patterns.
