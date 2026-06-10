# Book Dragon — Continue.dev Rules

**Last updated:** 2026-06-09

The universal rules for this project are defined in `AGENTS.md` at the project root. You must read and follow all rules in `AGENTS.md` before doing any work. This file contains only Continue-specific additions.

## AI self-identification

At the very start of each session — before the greeting — output a single line:

`AI_IDENTITY: Continue`

This identifies which AI is running for the duration of the session. Currently it is a visible marker only — session-search indexing and setup verification will be implemented in Phase 4 of the AI-agnostic plan. Output it once, before any other session startup work.

## Tool conventions

- Use the integrated terminal for all shell commands. Prefer Bash syntax for cross-platform compatibility.
- For timestamps: `date +"%Y-%m-%dT%H:%M:%S%:z"`
- For Python execution: `python script.py`
- Use Continue's native file editing tools for all file modifications.

## Continue-specific notes

- Continue auto-loads `AGENTS.md` from the project root.
- Additional rules can be scoped to specific files using `globs` in YAML frontmatter.
- See `AGENT-SETUP.md` for MCP server registration and full setup instructions.
