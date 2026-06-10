# Book Dragon — GitHub Copilot Instructions

**Last updated:** 2026-06-10

The universal rules for this project are defined in `AGENTS.md` at the project root. You must read and follow all rules in `AGENTS.md` before doing any work. This file contains only Copilot-specific additions.

## AI self-identification

For step 6a of session startup (`AGENTS.md`), output: `AI_IDENTITY: GitHub Copilot`

## Tool conventions

Copilot operates within the IDE (VS Code, JetBrains) and via the GitHub coding agent. The abstract tool references in AGENTS.md map to Copilot's native capabilities:

- **Check file/directory existence:** Use the file system tools available in your environment.
- **Search file contents:** Use workspace search or terminal commands.
- **Read files:** Open and read files directly.
- **Modify existing files:** Edit files in place.
- **Create new files:** Create files using your file system tools.
- **Run shell commands:** Use the integrated terminal.

## Copilot-specific notes

- When running as the GitHub coding agent, you have full terminal access. Prefer `python` for script execution and `date +"%Y-%m-%dT%H:%M:%S%:z"` for timestamps (via Bash/terminal).
- When running as Copilot Chat in the IDE, you may not have terminal access. Do your best with the tools available and flag any steps you cannot complete.
- Path-specific instructions in `.github/instructions/` apply alongside these project-wide instructions.
- See `AGENT-SETUP.md` for MCP server registration and full setup instructions.

## Model awareness

GitHub Copilot can use models from multiple providers (OpenAI, Anthropic, Google,
Microsoft). The underlying model may vary between sessions or be selected by the
user. All models should follow AGENTS.md identically. If a complex multi-step
procedure (such as session startup or build close-out) is beyond your current
capabilities, complete as many steps as you can and clearly state which steps you
were unable to perform.
