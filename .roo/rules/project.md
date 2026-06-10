# Book Dragon — Roo Code Rules

**Last updated:** 2026-06-09

The universal rules for this project are defined in `AGENTS.md` at the project root. You must read and follow all rules in `AGENTS.md` before doing any work. This file contains only Roo Code-specific additions.

## AI self-identification

At the very start of each session — before the greeting — output a single line:

`AI_IDENTITY: Roo Code`

This identifies which AI is running for the duration of the session. Currently it is a visible marker only — session-search indexing and setup verification will be implemented in Phase 4 of the AI-agnostic plan. Output it once, before any other session startup work.

## Tool conventions

- Use the integrated terminal for all shell commands. Prefer Bash syntax for cross-platform compatibility.
- For timestamps: `date +"%Y-%m-%dT%H:%M:%S%:z"`
- For Python execution: `python script.py`
- Use Roo Code's native file editing tools for all file modifications.

## Roo Code-specific notes

- Roo Code reads `AGENTS.md` natively from the project root (enabled by default).
- Mode-specific rules can be placed in `.roo/rules-{mode}/` directories (e.g. `.roo/rules-code/`, `.roo/rules-architect/`). The project-wide rules in this file apply to all modes.
- See `AGENT-SETUP.md` for MCP server registration and full setup instructions.
