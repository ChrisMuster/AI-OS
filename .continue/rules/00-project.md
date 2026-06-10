# Book Dragon — Continue.dev Rules

**Last updated:** 2026-06-10

The universal rules for this project are defined in `AGENTS.md` at the project root. You must read and follow all rules in `AGENTS.md` before doing any work. This file contains only Continue-specific additions.

## AI self-identification

For step 6a of session startup (`AGENTS.md`), output: `AI_IDENTITY: Continue`

## Tool conventions

- Use the integrated terminal for all shell commands. Prefer Bash syntax for cross-platform compatibility.
- For timestamps: `date +"%Y-%m-%dT%H:%M:%S%:z"`
- For Python execution: `python script.py`
- Use Continue's native file editing tools for all file modifications.

## Continue-specific notes

- Continue auto-loads `AGENTS.md` from the project root.
- Additional rules can be scoped to specific files using `globs` in YAML frontmatter.
- See `AGENT-SETUP.md` for MCP server registration and full setup instructions.
