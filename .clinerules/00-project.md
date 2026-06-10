# Book Dragon — Cline Rules

**Last updated:** 2026-06-10

## CRITICAL — Read AGENTS.md first

Before doing any work in this project, you **must** read the file `AGENTS.md` at the project root. It contains all universal rules for this project: session startup procedure, directory structure, file conventions, logging, build close-out, and behavioural rules. Nothing in this file overrides AGENTS.md — it only adds Cline-specific details.

If you have not read `AGENTS.md`, stop and read it now.

## AI self-identification

For step 6a of session startup (`AGENTS.md`), output: `AI_IDENTITY: Cline`

## Tool conventions

- Use the integrated terminal for all shell commands. Prefer Bash syntax for cross-platform compatibility.
- For timestamps: `date +"%Y-%m-%dT%H:%M:%S%:z"`
- For Python execution: `python script.py`
- Use Cline's native file editing tools for all file modifications.

## Cline-specific notes

- Cline does not yet auto-load `AGENTS.md`. The instruction above ensures it is read manually at session start. When native AGENTS.md support is added to Cline, this file will be simplified.
- See `AGENT-SETUP.md` for MCP server registration and full setup instructions.
