# Rule Hooks - Rules

**Last modified:** 2026-08-12

## Purpose
One module per enforcement rule plus a registry mapping an event category (shell / write / read) to its ordered list of checks. Each rule is a pure `check(ctx) -> Decision | None` function, AI-agnostic; the adapters handle the per-AI input and output.

## Contents
- __init__.py - `workflows/rule-hooks/scripts/rules/__init__.py` [[workflows/rule-hooks/scripts/rules/CONTEXT]] - The registry: `rules_for(category)` returns the ordered checks (hard rules before trial rules).
- env_protect.py - Rule A7: block AI writes to `.env` / `.env.*` (except `.env.example`); reads allowed.
- log_redirect.py - Rule A4: block shell writes into a `*LOG.md` file - redirections (`>`/`>>`/`>|`/`&>`/`&>>`) plus `cp`/`mv`/`tee`/`sed -i` targets, via the shared `shell_write_targets` helper (not just `>`/`>>`).
- dangerous_bash.py - Rule A6: block destructive shell (rm -rf, git push --force, git reset --hard, git clean -fd/-fdx, destructive SQL); parses the command, never substring-matches.
- personal_data.py - Rule B3 (layer 1): block personal data written into a committable (non-gitignored) file, reusing personal-data-guard.
- powershell_syntax.py - Rule A8: block a PowerShell here-string (`@'...'@` / `@"..."@`) passed to the Bash tool. Detects the opening delimiter at end of line, which is what PowerShell's syntax requires and ordinary Bash never produces, after stripping heredoc bodies so text quoted into a command is not scanned.
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
- A8 covers the PowerShell here-string only. Other PowerShell-isms in a Bash command (backtick line continuation, `$env:VAR`, `Test-Path`) are deliberately out of scope: the here-string is the fault that actually recurs, and each of the others needs its own false-positive analysis rather than being bundled in on the assumption that it is the same problem.
- A8's heredoc stripping recognises the common forms (`<<DELIM`, `<<'DELIM'`, `<<"DELIM"`, `<<-DELIM`). A body introduced some other way - a quoted multi-line string built by the command itself, for instance - is still scanned, so a here-string appearing as data inside one would fire. The narrowing to an end-of-line opening delimiter keeps that unlikely, and a wrong fire costs one retry against a block message carrying the correct command.
- A6's destructive-SQL detection only fires when a known SQL client (sqlite3/psql/mysql/mariadb/mysqlsh) is the program, to keep false positives low.

## Revision History
- 2026-06-30 - Initial creation: A7, A4, A6, B3 (blocking) and A2, A3 (trial, log-only) plus the category registry.
- 2026-07-01 - Close-out correction: updated the A4 description to reflect the shell-write detection broadened on 2026-06-30 (cp/mv/tee/sed -i and the extra redirect operators via `shell_write_targets`), not just `>`/`>>`.
- 2026-08-12 - Added `powershell_syntax.py`, rule A8: a PowerShell here-string passed to the Bash tool is blocked. It was added because the prose form of the rule demonstrably failed - a memory records the convention, and a session handover named this exact fault in this exact context as "an easy repeat", after which it repeated hours later in the session that had read the handover. The pull is structural rather than careless: the PowerShell tool's own instructions demonstrate `git commit -m @'...'@` for multi-line commit messages, so the wrong template carries the strongest and most task-specific cue at the moment the mistake is made. Two narrowings keep the false-positive surface small: detection requires the opening delimiter to end the line, which PowerShell's syntax requires and Bash never produces (so `echo @'hi'` does not fire), and heredoc bodies are stripped first so text quoted into a command, including documentation about this syntax, is not scanned. It ships blocking rather than as an A2/A3-style trial, because a trial records the fault and lets the call through, which is the behaviour being corrected. Registered ahead of the trial rules in the shell chain. Numbered A8 because A5 is reserved for a different, unbuilt rule about reaching for the PowerShell tool at all, where A8 is about PowerShell syntax inside a Bash call. Confirmed blocking live in-session, with the correct heredoc form and a heredoc body containing the syntax both confirmed still allowed.
