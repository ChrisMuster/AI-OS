# Book Dragon — Cline Rules

**Last updated:** 2026-06-09

## CRITICAL — Read AGENTS.md first

Before doing any work in this project, you **must** read the file `AGENTS.md` at the project root. It contains all universal rules for this project: session startup procedure, directory structure, file conventions, logging, build close-out, and behavioural rules. Nothing in this file overrides AGENTS.md — it only adds Cline-specific details.

If you have not read `AGENTS.md`, stop and read it now.

## AI self-identification

At the very start of each session — before the greeting — output a single line:

`AI_IDENTITY: Cline`

This identifies which AI is running for the duration of the session. Currently it is a visible marker only — session-search indexing and setup verification will be implemented in Phase 4 of the AI-agnostic plan. Output it once, before any other session startup work.

## Tool conventions

- Use the integrated terminal for all shell commands. Prefer Bash syntax for cross-platform compatibility.
- For timestamps: `date +"%Y-%m-%dT%H:%M:%S%:z"`
- For Python execution: `python script.py`
- Use Cline's native file editing tools for all file modifications.

## Cline-specific notes

- Cline does not yet auto-load `AGENTS.md`. The instruction above ensures it is read manually at session start. When native AGENTS.md support is added to Cline, this file will be simplified.
- See `AGENT-SETUP.md` for MCP server registration and full setup instructions.
