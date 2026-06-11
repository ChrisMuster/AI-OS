# Book Dragon — AI Setup Guide

**Last updated:** 2026-06-11 (Roo Code removed — shut down May 2026; 13 AIs supported)

This file is the single reference for setting up Book Dragon with any supported AI. It covers what each AI needs, how to verify setup, and how to fix common issues.

## Quick start

1. **Clone the repository** and open it in your AI tool.
2. **Run the verification script** to check what's configured and what's missing:
   ```
   python workflows/biblio-tools/scripts/verify.py --ai "Your AI Name"
   ```
3. **Fix any failures** using the remediation steps in this file.
4. **Start a session.** The AI reads `AGENTS.md`, identifies itself, runs setup verification automatically, and walks you through first-run initialisation if needed.

To see all supported AIs: `python workflows/biblio-tools/scripts/verify.py --list`

## Setup verification

Every AI runs `verify.py` automatically during session startup (step 6 of the startup sequence in `AGENTS.md`). If any check fails, the AI reports it before proceeding.

The script checks:
- `AGENTS.md` exists at the project root
- Python 3.9+ is available
- The AI's wrapper file exists (if one is needed)
- `SOUL.md` and `USER.md` exist (USER.md is created during first-run init)
- `.env` exists (optional — extends web research functionality)
- AI-specific configuration files exist
- MCP configuration (`.mcp.json`) is present with biblio-tools registered (MCP-capable AIs only)
- The `mcp` Python package is installed (MCP-capable AIs with Python 3.10+ only)

## Universal requirements

These apply to every AI:

| Requirement | Status | Notes |
|---|---|---|
| Python 3.9+ | Required | Install from [python.org](https://python.org). All project scripts depend on it. |
| `AGENTS.md` | Required | Must exist at project root. Contains all universal rules. Ships with the repository. |
| `SOUL.md` | Required | Defines the assistant persona. Ships with the repository. |
| `USER.md` | Created on first run | The AI creates this during first-run initialisation. |
| `.env` | Optional | Extends web research source coverage. See `workflows/web-research/SETUP.md`. |

## MCP support (biblio-tools)

The biblio-tools MCP server exposes project scripts as typed tools. It is optional — all scripts work via direct shell commands without MCP. But for AIs that support MCP, it saves tool calls and provides typed parameters.

| Requirement | Notes |
|---|---|
| Python 3.10+ | The MCP SDK requires 3.10+. If running 3.9, MCP is unavailable but everything else works. |
| `mcp` package | Install: `pip install -r workflows/biblio-tools/requirements.txt` |
| `.mcp.json` | Must exist at project root with biblio-tools registered. Ships with the repository. |

**All 13 supported AIs have MCP support.** Each AI's MCP configuration format differs — see the per-AI sections below for details. For most AIs, the biblio-tools server is pre-configured in a project-scoped config file that ships with the repository. The only exception is GitHub Copilot CLI, which requires a one-time manual config step (see its section below).

## Per-AI setup

### Claude Code / Claude Cowork

| Item | Detail |
|---|---|
| Wrapper file | `CLAUDE.md` (shared by both products) |
| AGENTS.md loading | Native — Claude reads both `CLAUDE.md` and `AGENTS.md` automatically |
| MCP support | Yes — registered in `.mcp.json`, permissions in `.claude/settings.json` |
| Config files | `.claude/settings.json` (permissions allowlist, hooks, MCP tool permissions) |
| Session hooks | Pre-configured — Stop, PreCompact, Notification events in `.claude/settings.json` |
| AI identity | `Claude Code` or `Claude Cowork` (auto-detected by product) |

**Setup steps:**
1. Ensure Python 3.9+ is installed.
2. Install MCP package: `pip install -r workflows/biblio-tools/requirements.txt`
3. Open the project in Claude Code or Claude Cowork. Both read `CLAUDE.md` and `AGENTS.md` automatically.
4. The first session triggers first-run initialisation (creates `USER.md`, `LOG.md` files, etc.).

**Troubleshooting:**
- If MCP tools prompt for permission, check that the tool names in `.claude/settings.json` match the `mcp__biblio-tools__*` pattern.
- If hooks prompt for permission, run `python workflows/settings-check/scripts/run.py` to identify uncovered commands.

### Gemini CLI

| Item | Detail |
|---|---|
| Wrapper file | `GEMINI.md` |
| AGENTS.md loading | Import — `@AGENTS.md` on line 3 of `GEMINI.md` |
| MCP support | Yes — pre-configured in `.gemini/settings.json` |
| Config files | `.gemini/settings.json` (context files, MCP servers, and hooks) |
| Session hooks | Pre-configured — SessionEnd event in `.gemini/settings.json` |
| AI identity | `Gemini CLI` |

**Setup steps:**
1. Ensure Python 3.9+ is installed.
2. Install MCP package: `pip install -r workflows/biblio-tools/requirements.txt`
3. Install and configure Gemini CLI per Google's documentation.
4. Open the project. Gemini reads `GEMINI.md` which imports `AGENTS.md` via `@AGENTS.md`.
5. The biblio-tools MCP server is pre-configured in `.gemini/settings.json` — no additional MCP setup needed.
6. The first session triggers first-run initialisation.

**MCP config location:** `.gemini/settings.json` (project-scoped, ships with the repository). Gemini CLI does not read `.mcp.json` — it uses its own `mcpServers` block in `settings.json`.

### GitHub Copilot

| Item | Detail |
|---|---|
| Wrapper file | `.github/copilot-instructions.md` |
| AGENTS.md loading | Native — Copilot reads `AGENTS.md` natively (since August 2025) |
| MCP support | Yes — requires one-time manual config |
| Config files | None in the repository (MCP config is user-scoped) |
| Session hooks | None — no hook events available. Relies on background scheduler for session archiving. |
| AI identity | `GitHub Copilot` |

**Setup steps:**
1. Ensure Python 3.9+ is installed.
2. Install MCP package: `pip install -r workflows/biblio-tools/requirements.txt`
3. Install the GitHub Copilot extension in VS Code or JetBrains (requires a Copilot subscription).
4. Open the project. Copilot reads `.github/copilot-instructions.md` and `AGENTS.md` automatically.
5. **MCP setup (one-time):** Add the biblio-tools server to your Copilot CLI config. Run `/mcp add` in the Copilot CLI, or manually add the following to `~/.copilot/mcp-config.json`:
   ```json
   {
     "mcpServers": {
       "biblio-tools": {
         "command": "python",
         "args": ["workflows/biblio-tools/scripts/server.py"]
       }
     }
   }
   ```
   In VS Code, Copilot also reads `.mcp.json` (which ships with the repository), so MCP may work without additional config in the IDE.

**Note:** Copilot CLI's MCP config is stored at `~/.copilot/mcp-config.json` (user home), so it cannot be shipped in the repository. The VS Code extension reads `.mcp.json` at the project root, which is pre-configured.

**Model selection:** Copilot supports 23+ models from OpenAI, Anthropic, Google,
and Microsoft. The user selects the model via the model picker, or Copilot's
auto-selection chooses one based on task complexity. Model choice does not affect
which instruction files are loaded — `AGENTS.md` and `.github/copilot-instructions.md`
apply regardless of model. However, instruction-following quality may vary between
models. For best results with Book Dragon's complex multi-step procedures, use a
high-capability model (Claude Opus, GPT-5.4+, or Gemini 3.1 Pro).

**Known issue — CLAUDE.md loading:** GitHub's documentation is ambiguous about whether Copilot loads `CLAUDE.md` only when a Claude model is active, or always regardless of model. If loaded unconditionally, non-Claude models would receive Claude-specific instructions (settings.json paths, session-search hooks) that do not apply to them. This has not caused problems in practice but may warrant testing if unexpected behaviour is observed.

### Cursor

| Item | Detail |
|---|---|
| Wrapper file | `.cursor/rules/project.mdc` |
| AGENTS.md loading | Native — Cursor reads `AGENTS.md` alongside rule files |
| MCP support | Yes |
| Config files | `.cursor/hooks.json` (session hooks) |
| Session hooks | Pre-configured — sessionEnd event in `.cursor/hooks.json` |
| AI identity | `Cursor` |

**Setup steps:**
1. Ensure Python 3.9+ is installed.
2. Open the project in Cursor. It reads `.cursor/rules/project.mdc` and `AGENTS.md` automatically.
3. For MCP: configure the biblio-tools server in Cursor's MCP settings (see Cursor documentation for MCP server registration).

**Note:** Cursor rule files should stay under 200 words each (token budget recommendation).

### Windsurf / Devin Desktop

| Item | Detail |
|---|---|
| Wrapper files | `.windsurf/rules/project.md` and `.devin/rules/project.md` (both exist) |
| AGENTS.md loading | Native |
| MCP support | Yes |
| Config files | `.windsurf/hooks.json` (session hooks) |
| Session hooks | Pre-configured — post_cascade_response event in `.windsurf/hooks.json`. Fires per-response (no session-end event available); archive.py is idempotent so repeated calls are safe. |
| AI identity | `Windsurf` or `Devin Desktop` (depending on which product is running) |

**Setup steps:**
1. Ensure Python 3.9+ is installed.
2. Open the project. The AI reads its rule file and `AGENTS.md` automatically.
3. For MCP: configure in the product's MCP settings.

**Note:** Windsurf was acquired by Cognition and rebranded to Devin Desktop in June 2026. Both directories exist with cross-references. `.devin/rules/` takes precedence if both are present.

### Cline

| Item | Detail |
|---|---|
| Wrapper file | `.clinerules/00-project.md` |
| AGENTS.md loading | Manual — Cline does **not** auto-load `AGENTS.md` (support in development). The wrapper file contains an explicit instruction to read it. |
| MCP support | Yes |
| Config files | `.clinerules/hooks/TaskComplete` (session hook script) |
| Session hooks | Pre-configured — TaskComplete event via executable script in `.clinerules/hooks/TaskComplete` |
| AI identity | `Cline` |

**Setup steps:**
1. Ensure Python 3.9+ is installed.
2. Install the Cline extension in VS Code.
3. Open the project. Cline reads `.clinerules/00-project.md` which instructs it to read `AGENTS.md`.
4. For MCP: configure in Cline's MCP settings.

**Important:** Cline is the only supported AI that does not auto-load `AGENTS.md`. The wrapper file has a bold "CRITICAL — Read AGENTS.md first" section to ensure it is read manually. When native support is added, the wrapper will be simplified.

### Continue.dev

| Item | Detail |
|---|---|
| Wrapper file | `.continue/rules/00-project.md` |
| AGENTS.md loading | Native |
| MCP support | Yes |
| Config files | None beyond the rule file |
| Session hooks | Not yet available — Continue.dev hook support is pending official documentation. Relies on background scheduler for session archiving. |
| AI identity | `Continue` |

**Setup steps:**
1. Ensure Python 3.9+ is installed.
2. Install the Continue extension.
3. Open the project. Continue reads `.continue/rules/00-project.md` and `AGENTS.md` automatically.

**Note:** Additional rules can be scoped to specific files using `globs` in YAML frontmatter.

### Aider

| Item | Detail |
|---|---|
| Wrapper file | `.aider.conf.yml` (config file, not an instruction file) |
| AGENTS.md loading | Config — `.aider.conf.yml` lists `AGENTS.md` in the `read` section |
| MCP support | Yes — pre-configured in `.aider.conf.yml` |
| Config files | None beyond `.aider.conf.yml` |
| Session hooks | None — no hook events available. Relies on background scheduler for session archiving. |
| AI identity | `Aider` |

**Setup steps:**
1. Ensure Python 3.9+ is installed.
2. Install MCP package: `pip install -r workflows/biblio-tools/requirements.txt`
3. Install Aider per its documentation.
4. Open the project. Aider reads `.aider.conf.yml` which loads `AGENTS.md` into context.
5. The biblio-tools MCP server is pre-configured in `.aider.conf.yml` under `mcp-server` — no additional MCP setup needed.

**Note:** Aider has no wrapper/instruction file concept. The `.aider.conf.yml` is a configuration file that ensures `AGENTS.md` is loaded and the biblio-tools MCP server is registered. MCP tools are offered to whatever model the session uses (assuming tool-call support).

### Codex CLI / Codex Desktop

| Item | Detail |
|---|---|
| Wrapper file | None needed |
| AGENTS.md loading | Native — Codex reads `AGENTS.md` by default |
| MCP support | Yes — pre-configured in `.codex/config.toml` |
| Config files | `.codex/config.toml` (MCP server registration and hooks) |
| Session hooks | Pre-configured — Stop event in `.codex/config.toml` |
| AI identity | `Codex CLI` or `Codex Desktop` |

**Setup steps:**
1. Ensure Python 3.9+ is installed.
2. Install MCP package: `pip install -r workflows/biblio-tools/requirements.txt`
3. Install the tool per OpenAI's documentation.
4. Open the project. Codex reads `AGENTS.md` automatically — no wrapper file needed.
5. The biblio-tools MCP server is pre-configured in `.codex/config.toml` — no additional MCP setup needed.

**Note:** Codex CLI, Codex Desktop, and the Codex IDE extension share MCP settings. The project-scoped `.codex/config.toml` is picked up by all three surfaces. You can also manage servers via the `codex mcp` CLI commands.

### OpenCode

| Item | Detail |
|---|---|
| Wrapper file | None needed |
| AGENTS.md loading | Native — OpenCode reads `AGENTS.md` by default |
| MCP support | Yes — pre-configured in `opencode.json` |
| Config files | `opencode.json` (MCP server registration) |
| Session hooks | None — OpenCode supports only plugin hooks (not session lifecycle events). Relies on background scheduler for session archiving. |
| AI identity | `OpenCode` |

**Setup steps:**
1. Ensure Python 3.9+ is installed.
2. Install MCP package: `pip install -r workflows/biblio-tools/requirements.txt`
3. Install OpenCode per its documentation.
4. Open the project. OpenCode reads `AGENTS.md` automatically — no wrapper file needed.
5. The biblio-tools MCP server is pre-configured in `opencode.json` — no additional MCP setup needed.

**Note:** Project-scoped `opencode.json` has the highest precedence among OpenCode config files. It merges with (rather than replacing) global config at `~/.config/opencode/opencode.json`.

## Adding a new AI

When a new AI tool becomes available and you want to add it to this project, follow these steps in order:

1. **Research** — read the AI's official documentation. Determine: where it looks for instruction files (path and format), whether it reads `AGENTS.md` natively, whether it supports MCP, and where it stores session files locally.
2. **Create the wrapper file** — if the AI needs one, create it in the location the AI expects. Use the existing wrappers as reference (e.g. `CLAUDE.md`, `.cursor/rules/project.mdc`). The wrapper must import or point to `AGENTS.md` for the full rules and add only AI-specific details. Include the `AI_IDENTITY` line for session startup.
3. **Update `verify.py`** — add an entry to the `AI_REQUIREMENTS` mapping in `workflows/biblio-tools/scripts/verify.py` with the wrapper path, MCP support flag, config file paths, and AGENTS.md loading method.
4. **Add a per-AI section to this file** — follow the format of the existing sections: a requirements table, setup steps, and any troubleshooting notes.
5. **Verify** — run `python workflows/biblio-tools/scripts/verify.py --ai "New AI Name"` to confirm all checks pass.
6. **Update CONTEXT.md files** — update `workflows/biblio-tools/CONTEXT.md` (Known Issues mentions adding new AIs) and any other affected CONTEXT.md files per the change propagation rule in `AGENTS.md`.
