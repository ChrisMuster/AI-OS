# Book Dragon — Windsurf Rules

**Last updated:** 2026-06-09

The universal rules for this project are defined in `AGENTS.md` at the project root. You must read and follow all rules in `AGENTS.md` before doing any work. This file contains only Windsurf-specific additions.

## AI self-identification

At the very start of each session — before the greeting — output a single line:

`AI_IDENTITY: Windsurf`

This identifies which AI is running for the duration of the session. Currently it is a visible marker only — session-search indexing and setup verification will be implemented in Phase 4 of the AI-agnostic plan. Output it once, before any other session startup work.

## Tool conventions

- Use the integrated terminal for all shell commands. Prefer Bash syntax for cross-platform compatibility.
- For timestamps: `date +"%Y-%m-%dT%H:%M:%S%:z"`
- For Python execution: `python script.py`
- Use Cascade's native file editing for all file modifications.

## Windsurf-specific notes

- Windsurf reads `AGENTS.md` natively from the project root.
- Rule files in `.windsurf/rules/` are limited to 12,000 characters each.
- See `AGENT-SETUP.md` for MCP server registration and full setup instructions.

## Rebranding note (June 2026)

Windsurf was acquired by Cognition and rebranded to **Devin Desktop** in June 2026. The preferred directory is now `.devin/rules/` (which takes precedence if present), but `.windsurf/rules/` is still supported as a fallback. If Windsurf stops reading from `.windsurf/`, move this file to `.devin/rules/project.md` and update the AI_IDENTITY line above accordingly.
