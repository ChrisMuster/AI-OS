# Book Dragon — Gemini CLI Instructions

@AGENTS.md

**Last updated:** 2026-06-09

The universal rules for this project are defined in `AGENTS.md`. Gemini CLI reads both files via the `@AGENTS.md` import above. This file contains only Gemini-specific additions.

## AI self-identification

At the very start of each session — before the greeting — output a single line:

`AI_IDENTITY: Gemini CLI`

This identifies which AI is running for the duration of the session. Currently it is a visible marker only — session-search indexing and setup verification will be implemented in Phase 4 of the AI-agnostic plan. Output it once, before any other session startup work.

## Gemini-specific tool conventions

### Shell commands

Use the terminal for all shell operations. Prefer Bash syntax for cross-platform compatibility:
- Python execution: `python script.py`
- Timestamps: `date +"%Y-%m-%dT%H:%M:%S%:z"`

### File operations

These map the abstract tool references in AGENTS.md to Gemini CLI's capabilities:

- **Check file/directory existence:** Use shell commands (`test -f`, `ls`).
- **Search file contents:** Use shell commands (`grep`, `rg`).
- **Read files:** Read files directly via your file reading capability.
- **Modify existing files:** Edit files directly.
- **Create new files:** Write files directly.
- **Run shell commands:** Use the integrated terminal.

## Configuration

Gemini CLI configuration for this project lives in `.gemini/settings.json`. This includes context file settings and MCP server registration. See `AGENT-SETUP.md` for full setup instructions.
