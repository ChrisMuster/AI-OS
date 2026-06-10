# Book Dragon — Devin Desktop Rules

**Last updated:** 2026-06-10

The universal rules for this project are defined in `AGENTS.md` at the project root. You must read and follow all rules in `AGENTS.md` before doing any work. This file contains only Devin Desktop-specific additions.

## AI self-identification

For step 6a of session startup (`AGENTS.md`), output: `AI_IDENTITY: Devin Desktop`

## Tool conventions

- Use the integrated terminal for all shell commands. Prefer Bash syntax for cross-platform compatibility.
- For timestamps: `date +"%Y-%m-%dT%H:%M:%S%:z"`
- For Python execution: `python script.py`
- Use Devin's native file editing for all file modifications.

## Devin Desktop-specific notes

- Devin Desktop reads `AGENTS.md` natively from the project root.
- Rule files in `.devin/rules/` are limited to 12,000 characters each.
- See `AGENT-SETUP.md` for MCP server registration and full setup instructions.

## Rebranding note

Devin Desktop was previously known as **Windsurf** (by Codeium). An identical rules file exists at `.windsurf/rules/project.md` for backward compatibility. If both directories are present, `.devin/rules/` takes precedence.
