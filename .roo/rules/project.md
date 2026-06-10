# Book Dragon — Roo Code Rules

**Last updated:** 2026-06-10

The universal rules for this project are defined in `AGENTS.md` at the project root. You must read and follow all rules in `AGENTS.md` before doing any work. This file contains only Roo Code-specific additions.

## AI self-identification

For step 6a of session startup (`AGENTS.md`), output: `AI_IDENTITY: Roo Code`

## Tool conventions

- Use the integrated terminal for all shell commands. Prefer Bash syntax for cross-platform compatibility.
- For timestamps: `date +"%Y-%m-%dT%H:%M:%S%:z"`
- For Python execution: `python script.py`
- Use Roo Code's native file editing tools for all file modifications.

## Roo Code-specific notes

- Roo Code reads `AGENTS.md` natively from the project root (enabled by default).
- Mode-specific rules can be placed in `.roo/rules-{mode}/` directories (e.g. `.roo/rules-code/`, `.roo/rules-architect/`). The project-wide rules in this file apply to all modes.
- See `AGENT-SETUP.md` for MCP server registration and full setup instructions.
