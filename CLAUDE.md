# Book Dragon — Claude Code Instructions

@AGENTS.md

**Last updated:** 2026-06-09

The universal rules for this project are defined in `AGENTS.md`. Claude reads both files. This file contains only Claude-specific additions: tool conventions, session maintenance tasks, and configuration guidance. It applies to all Claude products (Claude Code, Claude Cowork, and any future Claude interfaces that read this file).

## AI self-identification

At the very start of each session — before the greeting — identify which Claude product you are running as (e.g. Claude Code, Claude Cowork) and output a single line:

`AI_IDENTITY: [product name]`

This line is used by session-search indexing to tag sessions by AI. Output it once, before any other session startup work.

## Claude-specific tool conventions

### Shell tools

Book Dragon uses two shell tools in Claude Code: Bash and PowerShell. The rule for which to use is absolute — no discretion, no exceptions.

**Bash — use for all of the following, on every operating system including Windows:**
- Python execution: `python --version`, `py --version`, `python script.py`
- Timestamps: `date +"%Y-%m-%dT%H:%M:%S%:z"`
- Any cross-platform or POSIX-compatible operation

**PowerShell — use only when the operation has no Bash equivalent on Windows:**
- Windows registry access (`HKLM:\...`, `HKCU:\...`)
- Windows-specific system management with no POSIX equivalent

If PowerShell is genuinely required for a task, add the specific `PowerShell(command)` pattern to the allowlist in `.claude/settings.json`. Never add PowerShell entries as a workaround for Python execution or timestamps — those belong in Bash without exception.

**Path format in Bash:** Always use forward slashes in Bash commands, script paths, allowlist entries, and configuration files. Never use backslashes in these contexts. Backslashes belong in PowerShell and Windows file explorer only.

**Allowlist enforcement:** The allowlist in `.claude/settings.json` uses `Bash(...)` patterns. A PowerShell call to the same command is a different namespace — it will not match and will trigger a permission prompt. This is intentional: the allowlist is the enforcement mechanism, and using the wrong tool defeats it.

### File operations

These are the Claude Code tools that map to the abstract tool references in AGENTS.md:

- **Check file/directory existence:** Glob tool
- **Search file contents:** Grep tool
- **Read files:** Read tool
- **Modify existing files:** Edit tool (preferred — sends only the diff)
- **Create new files:** Write tool
- **List directory contents:** Glob tool
- **Run shell commands:** Bash tool (default) or PowerShell tool (see shell rules above)

### LOG.md writes

When writing log entries (per the LOG.md rules in AGENTS.md), always use the Edit tool to append. Never use Bash (`cat >>` or similar) — the Edit tool is always permitted without a permission prompt and is the correct tool for file modification in Claude Code.

## Session maintenance (step 6)

For step 6 of session startup (AI-specific session maintenance), perform the following silently if `workflows/session-search/scripts/index.py` exists:

a. **Scheduled task check** — call `list_scheduled_tasks` and check whether `session-search-archive` exists on this machine. If it does not, create it with the same parameters as first-run step 6b (using forward slashes in the path). Tell the user in a single sentence that it has been set up.

b. **Index update** — run `python workflows/session-search/scripts/index.py` to archive any sessions completed since the last run and refresh the search index. Do not report results unless the script exits with a non-zero exit code or raises an exception. Suppress all other output.

c. **Settings coverage check** — run `python workflows/settings-check/scripts/run.py` silently. Do not report results unless there are FAIL findings. If failures are found, tell the user in a single sentence after greeting them: "Settings coverage check found uncovered commands — [list]. These will prompt for permission when they fire."

## First-run setup (step 6)

For step 6 of first-run initialisation (AI-specific first-run setup), set up the session-search scheduled task if `workflows/session-search/` exists:

a. Call `list_scheduled_tasks` to check whether a task with id `session-search-archive` already exists on this machine.

b. If it does not exist, call `create_scheduled_task` with taskId `session-search-archive`, description `Hourly session archive — captures any new or updated Book Dragon sessions`, cronExpression `0 * * * *`, notifyOnCompletion `false`, and a prompt that runs `python <absolute-path-to-project>/workflows/session-search/scripts/archive.py --all` (substituting the real absolute path to the project root on this machine, using **forward slashes** — e.g. `C:/Users/Name/Desktop/AI-Work/AI-OS` — so that the path matches the `settings.json` allowlist pattern `Bash(python *workflows/session-search/scripts/archive.py*)`). Quote the path only if it contains spaces. This script is idempotent and safe to re-run; it should not notify on normal completion.

c. Note briefly to the user that the session search scheduled task has been created. If the task already exists, skip this step silently.

## Project memory — Claude-specific

The canonical memory location is `memory/` at the project root (as defined in AGENTS.md). This overrides the default per-user Claude cache (`~/.claude/projects/.../memory/`). Memory written to the project-scoped location syncs with the project and is available on any machine. Memory written to the per-user cache is local only and should be treated as stale if it conflicts with what is in `memory/`.

## Line-count threshold

The audit script checks `AGENTS.md` against a line-count threshold of 600 lines (see AGENTS.md general guidelines). This is where the bulk of the rules live; CLAUDE.md is a thin wrapper and is not checked separately.
