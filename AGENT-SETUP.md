# Book Dragon — AI Setup Guide

**Last updated:** 2026-06-24 (Codex Desktop MCP availability note)

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
- The canonical project `.venv` contains every package declared by root `requirements.txt`
- Shared PDF extraction is installed in the project `.venv`
- AI-specific configuration files exist
- The selected AI's native MCP configuration is present and contains biblio-tools
- The `mcp` Python package is installed (MCP-capable AIs with Python 3.10+ only)
- The exact configured command starts successfully, completes an MCP handshake, exposes all ten expected tools, calls `get_timestamp`, rejects an invalid month, blocks path traversal, and verifies the knowledge-graph query tool without forcing a full graph rebuild during setup

This proves the checked-in configuration and assembled MCP server work together without requiring the AI application to be installed. It does not prove that an unavailable client application discovers its project-scoped config; confirm that once when the client is first installed using its native MCP status command or interface.

## Universal requirements

These apply to every AI:

| Requirement | Status | Notes |
|---|---|---|
| Python 3.9+ | Required | Install from [python.org](https://python.org). All project scripts depend on it. |
| `AGENTS.md` | Required | Must exist at project root. Contains all universal rules. Ships with the repository. |
| `SOUL.md` | Required | Defines the assistant persona. Ships with the repository. |
| `USER.md` | Created on first run | The AI creates this during first-run initialisation. |
| `.env` | Optional | Extends web research source coverage. See `workflows/web-research/SETUP.md`. |
| `pypdf` package | Required for PDF wiki ingestion | Run `python workflows/biblio-tools/scripts/setup.py`. |
| Project `.venv` | Required for packaged workflows | Created and maintained by one AI-agnostic setup command. |

## MCP support (biblio-tools)

The biblio-tools MCP server exposes project scripts as typed tools. It is optional — all scripts work via direct shell commands without MCP. But for AIs that support MCP, it saves tool calls and provides typed parameters.

| Requirement | Notes |
|---|---|
| Python 3.10+ | The MCP SDK requires 3.10+. If running 3.9, MCP is unavailable but everything else works. |
| `mcp` package | Install in the project `.venv`; commands are shown below. |
| `.mcp.json` | Must exist at project root with biblio-tools registered. Ships with the repository. |

**All 13 supported AIs have MCP support.** Each AI's MCP configuration format differs — see the per-AI sections below for details. For most AIs, the biblio-tools server is pre-configured in a project-scoped config file that ships with the repository. The only exception is GitHub Copilot CLI, which requires a one-time manual config step (see its section below).

**Tools exposed (ten):**

| Tool | What it does |
|---|---|
| `run_audit` | Run the structural audit across all project directories. |
| `run_link_check` | Manage Obsidian wiki links in CONTEXT.md files (link / audit / fix). |
| `run_new_month` | Create the journal entry file for a specified month. |
| `run_session_search_index` | Archive completed sessions and refresh the session search index. |
| `run_settings_check` | Validate that automated commands are covered by allowlist entries. |
| `verify_setup` | Run deterministic setup verification for a given AI. |
| `build_knowledge_graph` | Build (or rebuild) the structural knowledge-graph index; logs both ends. |
| `query_knowledge_graph` | Query/traverse the knowledge graph — a dispatcher over the ten read-only commands (validate, node, neighbors, impact, path, subtree, stats, orphans, broken, sessions), returning parsed JSON. |
| `get_timestamp` | Get the current ISO 8601 timestamp with timezone offset. |
| `append_log` | Append a formatted entry to a directory's LOG.md. |

The two knowledge-graph tools require this MCP server (Python 3.10+). On Python 3.9 they are unavailable, but the underlying CLI works directly: `python workflows/knowledge-graph/scripts/run.py <command>`.

Canonical project setup on every operating system:

```
python workflows/biblio-tools/scripts/setup.py
```

Check it without making changes:

```
python workflows/biblio-tools/scripts/setup.py --check
```

The root `requirements.txt` includes every workflow-specific package manifest. Dependency-bearing workflow entry points automatically switch into `.venv`, so their normal commands work unchanged for Claude, Codex, Gemini and every other supported AI. Global Python packages may still exist, but Book Dragon does not rely on them.

## PDF wiki ingestion

Book Dragon includes an AI-agnostic PDF extractor at `workflows/create-wiki/scripts/extract_pdf.py`. It uses `pypdf` from the shared project `.venv`, preserves page boundaries, and writes extracted JSON only to the operating system's temporary directory. Source files in wiki `raw/` directories are never modified.

Run it directly:

```powershell
python workflows/create-wiki/scripts/extract_pdf.py wikis/<wiki-name>/raw/<document>.pdf
```

The command prints a compact JSON result containing the temporary output path, page count, character count, and OCR status. Re-running the same PDF uses the cached extraction. Pass `--force` to rebuild it or `--dry-run` to preview the action.

Text-based PDFs work across all supported AIs, regardless of whether the AI product has native PDF support. Image-only or scanned PDFs are detected and reported as requiring OCR; OCR is not yet included.

## Background scheduler

A Python background scheduler (`workflows/session-search/scripts/scheduler.py`) runs `archive.py --all` every hour to catch sessions that hooks may have missed. It is started automatically at session startup (AGENTS.md step 6d) for all AIs except Claude (which uses its own MCP scheduled task instead).

The scheduler is PID-file-guarded — only one instance runs at a time. It auto-terminates after 4 hours of inactivity (no new sessions archived). It is especially important for AIs without session hooks (GitHub Copilot, Continue.dev, OpenCode, Aider), where it is the primary archiving mechanism.

| Command | Description |
|---|---|
| `python workflows/session-search/scripts/scheduler.py` | Start the scheduler (foreground) |
| `python workflows/session-search/scripts/scheduler.py --status` | Check if running |
| `python workflows/session-search/scripts/scheduler.py --stop` | Stop a running instance |

## Per-AI setup

### Claude Code / Claude Cowork

| Item | Detail |
|---|---|
| Wrapper file | `CLAUDE.md` (shared by both products) |
| AGENTS.md loading | Native — Claude reads both `CLAUDE.md` and `AGENTS.md` automatically |
| MCP support | Yes — registered in `.mcp.json`, permissions in `.claude/settings.json` |
| Config files | `.claude/settings.json` (permissions allowlist, hooks, MCP tool permissions) |
| Session hooks | Pre-configured — Stop, PreCompact, Notification events in `.claude/settings.json` |
| Session transcripts | Claude Code: `~/.claude/projects/<project>/<session>.jsonl` — adapter: `claude-code`. Cowork: `%APPDATA%/Claude/local-agent-mode-sessions/` — adapter: `cowork`. |
| AI identity | `Claude Code` or `Claude Cowork` (auto-detected by product) |

**Setup steps:**
1. Ensure Python 3.9+ is installed.
2. Run the canonical project setup command.
3. Open the project in Claude Code or Claude Cowork. Both read `CLAUDE.md` and `AGENTS.md` automatically.
4. The first session triggers first-run initialisation (creates `USER.md`, `LOG.md` files, etc.).

**Troubleshooting:**
- If MCP tools prompt for permission, check that the tool names in `.claude/settings.json` match the `mcp__biblio-tools__*` pattern.
- If hooks prompt for permission, run `python workflows/settings-check/scripts/run.py` to identify uncovered commands.

### Gemini CLI / Antigravity CLI

> **Transition notice (June 2026):** Gemini CLI is being replaced by [Antigravity CLI](https://antigravity.google). Free and Pro users lose Gemini CLI access on **June 18, 2026**. Enterprise users retain Gemini CLI access. Antigravity CLI reads `GEMINI.md` and `AGENTS.md` unchanged — no wrapper file changes needed. Hooks use the same JSON format. MCP config has moved from inline in `settings.json` to dedicated `mcp_config.json` files (workspace: `.agents/mcp_config.json`, global: `~/.gemini/antigravity-cli/mcp_config.json`) — this migration is pending.
>
> Google also launched **Antigravity 2.0** (desktop app), **Antigravity IDE**, and **Antigravity SDK** alongside the CLI. These may also work with Book Dragon — research pending.

| Item | Detail |
|---|---|
| Wrapper file | `GEMINI.md` (shared by Gemini CLI and Antigravity CLI) |
| AGENTS.md loading | Import — `@AGENTS.md` on line 3 of `GEMINI.md` |
| MCP support | Yes — Gemini CLI: pre-configured in `.gemini/settings.json`. Antigravity CLI: pending migration to `.agents/mcp_config.json`. |
| Config files | Gemini CLI: `.gemini/settings.json` (context files, MCP servers, and hooks). Antigravity CLI: `~/.gemini/antigravity-cli/` (global config). |
| Session hooks | Pre-configured — SessionEnd event in `.gemini/settings.json`. Hook format is unchanged in Antigravity CLI. |
| Session transcripts | Gemini CLI: `~/.gemini/tmp/<project_hash>/chats/*.jsonl`. Antigravity CLI: `~/.gemini/antigravity/brain/<id>/.system_generated/logs/transcript.jsonl`. Adapter: `gemini-cli` (checks both locations). |
| AI identity | `Gemini CLI` |

**Setup steps (Gemini CLI):**
1. Ensure Python 3.9+ is installed.
2. Run the canonical project setup command.
3. Install and configure Gemini CLI per Google's documentation.
4. Open the project. Gemini reads `GEMINI.md` which imports `AGENTS.md` via `@AGENTS.md`.
5. The biblio-tools MCP server is pre-configured in `.gemini/settings.json` — no additional MCP setup needed.
6. The first session triggers first-run initialisation.

**Setup steps (Antigravity CLI):**
1. Ensure Python 3.9+ is installed.
2. Install Antigravity CLI from [antigravity.google](https://antigravity.google).
3. Open the project. Antigravity CLI reads `GEMINI.md` and `AGENTS.md` automatically.
4. MCP setup: pending — `.agents/mcp_config.json` config file needs to be created. See transition notice above.

**MCP config location:** Gemini CLI: `.gemini/settings.json` (project-scoped, ships with the repository). Antigravity CLI: `.agents/mcp_config.json` (workspace-scoped) and `~/.gemini/antigravity-cli/mcp_config.json` (global). Migration from inline `settings.json` to dedicated `mcp_config.json` is pending.

Running `verify.py --ai "Antigravity CLI"` currently returns a deliberate failure explaining this pending migration. This prevents Antigravity from being reported as ready before its native configuration and live transcript format have been verified.

### GitHub Copilot

| Item | Detail |
|---|---|
| Wrapper file | `.github/copilot-instructions.md` |
| AGENTS.md loading | Native — Copilot reads `AGENTS.md` natively (since August 2025) |
| MCP support | Yes — requires one-time manual config |
| Config files | None in the repository (MCP config is user-scoped) |
| Session hooks | None — no hook events available. Relies on background scheduler for session archiving. |
| Session transcripts | `~/.copilot/session-state/<uuid>/events.jsonl` — adapter: `copilot` |
| AI identity | `GitHub Copilot` |

**Setup steps:**
1. Ensure Python 3.9+ is installed.
2. Run the canonical project setup command.
3. Install the GitHub Copilot extension in VS Code or JetBrains (requires a Copilot subscription).
4. Open the project. Copilot reads `.github/copilot-instructions.md` and `AGENTS.md` automatically.
5. **MCP setup (one-time):** Add the biblio-tools server to your Copilot CLI config. Run `/mcp add` in the Copilot CLI, or manually add the following to `~/.copilot/mcp-config.json`:
   ```json
   {
     "mcpServers": {
       "biblio-tools": {
         "command": "python",
           "args": [
             "workflows/biblio-tools/scripts/launch.py",
             "workflows/biblio-tools/scripts/server.py"
           ]
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
| Session transcripts | `~/.cursor/projects/*/agent-transcripts/*.jsonl` — adapter: `cursor` |
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
| Session transcripts | No documented local transcript storage. Sessions may be cloud-only. No adapter — relies on hooks and background scheduler. |
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
| Session transcripts | `%APPDATA%/Code/User/globalStorage/saoudrizwan.claude-dev/tasks/<id>/api_conversation_history.json` — adapter: `cline` |
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
| Session transcripts | `~/.continue/sessions/<uuid>.json` — adapter: `continue-dev` |
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
| Session transcripts | None — Aider uses Git commits as the session record. No local transcript files are stored. No adapter. |
| AI identity | `Aider` |

**Setup steps:**
1. Ensure Python 3.9+ is installed.
2. Run the canonical project setup command.
3. Install Aider per its documentation.
4. Open the project. Aider reads `.aider.conf.yml` which loads `AGENTS.md` into context.
5. The biblio-tools MCP server is pre-configured in `.aider.conf.yml` under `mcp-server` — no additional MCP setup needed.

**Note:** Aider has no wrapper/instruction file concept. The `.aider.conf.yml` is a configuration file that ensures `AGENTS.md` is loaded and the biblio-tools MCP server is registered. MCP tools are offered to whatever model the session uses (assuming tool-call support).

### Codex CLI / Codex Desktop

| Item | Detail |
|---|---|
| Wrapper file | None needed |
| AGENTS.md loading | Native — Codex reads `AGENTS.md` by default |
| MCP support | Yes — pre-configured in `.codex/config.toml`; verify live tool exposure per Codex surface |
| Config files | `.codex/config.toml` (MCP server registration and hooks) |
| Session hooks | Pre-configured — Stop event in `.codex/config.toml` |
| Session transcripts | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` — adapter: `codex` |
| AI identity | `Codex CLI` or `Codex Desktop` |

**Setup steps:**
1. Ensure Python 3.9+ is installed.
2. Run the canonical project setup command.
3. Install the tool per OpenAI's documentation.
4. Open the project. Codex reads `AGENTS.md` automatically — no wrapper file needed.
5. The biblio-tools MCP server is pre-configured in `.codex/config.toml` — no additional MCP setup needed.

**Note:** Codex CLI, Codex Desktop, and the Codex IDE extension share MCP settings. The project-scoped `.codex/config.toml` uses `workflows/biblio-tools/scripts/launch.py` to select the platform-appropriate `.venv` interpreter. Setup verification proves the configured server starts and exposes all ten Biblio Tools over the MCP protocol. A live Codex Desktop session on Windows has shown the protocol check passing while the Biblio Tools were not injected into that session's callable tool list; in that case use direct shell commands for Book Dragon workflows and treat it as a Codex host/tool-palette issue unless `verify.py --ai "Codex Desktop"` fails. You can also manage servers via the `codex mcp` CLI commands where the local Codex executable is available.

### OpenCode

| Item | Detail |
|---|---|
| Wrapper file | None needed |
| AGENTS.md loading | Native — OpenCode reads `AGENTS.md` by default |
| MCP support | Yes — pre-configured in `opencode.json` |
| Config files | `opencode.json` (MCP server registration) |
| Session hooks | None — OpenCode supports only plugin hooks (not session lifecycle events). Relies on background scheduler for session archiving. |
| Session transcripts | `~/.local/share/opencode/opencode.db` (SQLite) — adapter: `opencode`. Custom path via `OPENCODE_DATA_DIR`. |
| AI identity | `OpenCode` |

**Setup steps:**
1. Ensure Python 3.9+ is installed.
2. Run the canonical project setup command.
3. Install OpenCode per its documentation.
4. Open the project. OpenCode reads `AGENTS.md` automatically — no wrapper file needed.
5. The biblio-tools MCP server is pre-configured in `opencode.json` using OpenCode's local command-array format — no additional MCP setup needed.

**Note:** Project-scoped `opencode.json` has the highest precedence among OpenCode config files. It merges with (rather than replacing) global config at `~/.config/opencode/opencode.json`.

## Adding a new AI

When a new AI tool becomes available and you want to add it to this project, follow these steps in order:

1. **Research** — read the AI's official documentation. Determine: where it looks for instruction files (path and format), whether it reads `AGENTS.md` natively, whether it supports MCP, and where it stores session files locally.
2. **Create the wrapper file** — if the AI needs one, create it in the location the AI expects. Use the existing wrappers as reference (e.g. `CLAUDE.md`, `.cursor/rules/project.mdc`). The wrapper must import or point to `AGENTS.md` for the full rules and add only AI-specific details. Include the `AI_IDENTITY` line for session startup.
3. **Update `verify.py`** — add an entry to the `AI_REQUIREMENTS` mapping in `workflows/biblio-tools/scripts/verify.py` with the wrapper path, MCP support flag, config file paths, and AGENTS.md loading method.
4. **Add a per-AI section to this file** — follow the format of the existing sections: a requirements table, setup steps, and any troubleshooting notes.
5. **Verify** — run `python workflows/biblio-tools/scripts/verify.py --ai "New AI Name"` to confirm all checks pass.
6. **Update CONTEXT.md files** — update `workflows/biblio-tools/CONTEXT.md` (Known Issues mentions adding new AIs) and any other affected CONTEXT.md files per the change propagation rule in `AGENTS.md`.
