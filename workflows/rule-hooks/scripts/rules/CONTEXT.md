# Rule Hooks - Rules

**Last modified:** 2026-07-01

## Purpose
One module per enforcement rule plus a registry mapping an event category (shell / write / read) to its ordered list of checks. Each rule is a pure `check(ctx) -> Decision | None` function, AI-agnostic; the adapters handle the per-AI input and output.

## Contents
- __init__.py - `workflows/rule-hooks/scripts/rules/__init__.py` [[workflows/rule-hooks/scripts/rules/CONTEXT]] - The registry: `rules_for(category)` returns the ordered checks (hard rules before trial rules).
- env_protect.py - Rule A7: block AI writes to `.env` / `.env.*` (except `.env.example`); reads allowed.
- log_redirect.py - Rule A4: block shell writes into a `*LOG.md` file - redirections (`>`/`>>`/`>|`/`&>`/`&>>`) plus `cp`/`mv`/`tee`/`sed -i` targets, via the shared `shell_write_targets` helper (not just `>`/`>>`).
- dangerous_bash.py - Rule A6: block destructive shell (rm -rf, git push --force, git reset --hard, git clean -fd/-fdx, destructive SQL); parses the command, never substring-matches.
- personal_data.py - Rule B3 (layer 1): block personal data written into a committable (non-gitignored) file, reusing personal-data-guard.
- shell_style.py - Trial rules A2 (path-shaped backslash) and A3 (dedicated tools over shell); warn-only and fire-logged in Phase 1, never blocking.

## Inputs
- A `Context` (from `core.py`), built by an adapter from a firing tool event.

## Outputs
- A `Decision` (block / warn) or `None` (allow).

## Steps
N/A. Rule modules consumed by the dispatcher in `run.py`.

## Dependencies
- `workflows/rule-hooks/scripts/core.py` [[workflows/rule-hooks/scripts/CONTEXT]] - `Context`, `Decision`, `format_block`, and the shell-parsing helpers.
- `workflows/personal-data-guard/` [[workflows/personal-data-guard/CONTEXT]] - Loaded lazily by `personal_data.py` (B3) as the detection source.

## Known Issues
- A2/A3 are deliberately log-only trials (higher false-positive risk than the hard rules); they are reviewed via the fire-log before any promotion to blocking. A2 is intentionally coarse (scans the raw command).
- A6's destructive-SQL detection only fires when a known SQL client (sqlite3/psql/mysql/mariadb/mysqlsh) is the program, to keep false positives low.

## Revision History
- 2026-06-30 - Initial creation: A7, A4, A6, B3 (blocking) and A2, A3 (trial, log-only) plus the category registry.
- 2026-07-01 - Close-out correction: updated the A4 description to reflect the shell-write detection broadened on 2026-06-30 (cp/mv/tee/sed -i and the extra redirect operators via `shell_write_targets`), not just `>`/`>>`.
