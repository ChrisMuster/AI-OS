# MCP Support Across Book Dragon AIs — Research Note

**Date:** 2026-06-10

## Summary

All 14 supported AIs now have MCP (Model Context Protocol) support. The six AIs previously marked `mcp_support: False` in `verify.py` — Gemini CLI, GitHub Copilot, Aider, Codex CLI, Codex Desktop, and OpenCode — have all added MCP client capabilities. The flags have been corrected.

## Per-AI findings

### Gemini CLI — now supports MCP

**Config format:** `.gemini/settings.json` with an `mcpServers` block (same key name as `.mcp.json` but in a different file). Also supports CLI commands (`gemini mcp add/list/remove`).

**Config location:** `~/.gemini/settings.json` (global) or `.gemini/settings.json` (project-scoped).

**Transport:** stdio and streamable HTTP. Supports rich multi-part responses (text, images, audio).

**Source:** [Gemini CLI MCP docs](https://geminicli.com/docs/tools/mcp-server/), [GitHub repo docs](https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md)

### GitHub Copilot — now supports MCP

**Config format:** JSON with `mcpServers` wrapper. Supports both the standard format and Claude-style flat format.

**Config location:** `~/.copilot/mcp-config.json` (CLI, global). Also workspace-local discovery from CWD up to git root (monorepo support). In VS Code, uses the standard VS Code MCP config (`.vscode/mcp.json`).

**Transport:** local (stdio) and remote (streamable HTTP with OAuth/PAT). The GitHub MCP server is built-in and available without configuration.

**Notable:** MCP works across all Copilot surfaces — IDE, CLI, Copilot app, and the coding agent on GitHub.com.

**Source:** [GitHub Copilot MCP docs](https://docs.github.com/en/copilot/concepts/context/mcp), [Copilot CLI MCP setup](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-mcp-servers)

### Aider — now supports MCP

**Config format:** YAML in `.aider.conf.yml` under `mcp-server` key, or `--mcp-server` CLI flags. Per-project `.aider.conf.yml` can be version-controlled.

**Config location:** `.aider.conf.yml` (project root), `~/.aider.conf.yml` (home), or `--config-file` (custom path).

**Transport:** stdio. MCP tools are offered to whatever model the session uses (assuming the model supports tool calls).

**Limitations:** Resources and sampling support varies by release — check the changelog.

**Source:** [Aider MCP client docs](https://learn.engineering.vips.edu/mcp/mcp-aider-client), [Aider MCP PR #3672](https://github.com/Aider-AI/aider/pull/3672)

### Codex CLI — now supports MCP

**Config format:** TOML in `~/.codex/config.toml` under `[mcp]` tables. Also has `codex mcp add/list/remove` CLI commands.

**Config location:** `~/.codex/config.toml` (global) or `.codex/config.toml` (project-scoped, trusted projects only).

**Transport:** stdio and streamable HTTP. Two "first-class runtime servers" ship built-in (OpenAI Docs and Memories) and start automatically.

**Notable:** Codex can itself run as an MCP server (`codex --mcp-server`) for embedding in other agents.

**Source:** [OpenAI Codex MCP docs](https://developers.openai.com/codex/mcp), [Codex CLI features](https://developers.openai.com/codex/cli/features)

### Codex Desktop — now supports MCP

**Shares config with Codex CLI.** The Codex app, CLI, and IDE extension share MCP settings via `~/.codex/config.toml`. Configuring MCP in one surface makes it available in all others.

**Source:** [OpenAI Codex config reference](https://developers.openai.com/codex/config-reference)

### OpenCode — now supports MCP

**Config format:** JSON/JSONC in `opencode.json` under the `mcp` key.

**Config location:** `opencode.json` (project root, highest precedence) or `~/.config/opencode/opencode.json` (global). Multiple configs merge (later overrides earlier for conflicting keys).

**Transport:** local (stdio with `command`/`args`) and remote (HTTP with `url`/`headers`). Automatic OAuth handling for remote servers (RFC 7591 dynamic client registration).

**Caveat:** MCP servers add to context budget. Many tools can exceed context limits quickly (e.g. the GitHub MCP server).

**Source:** [OpenCode MCP docs](https://opencode.ai/docs/mcp-servers/), [OpenCode config docs](https://opencode.ai/docs/config/)

## Implications for Book Dragon

### 1. `.mcp.json` is not universal

The project's `.mcp.json` (Claude-style format) is read natively by Claude Code/Cowork and by VS Code-based tools (Cursor, Cline, Continue, Windsurf/Devin Desktop, and GitHub Copilot in VS Code). But five CLI tools have their own config formats:

| AI | MCP config file | Format |
|---|---|---|
| Gemini CLI | `.gemini/settings.json` | JSON (`mcpServers` block) |
| GitHub Copilot CLI | `~/.copilot/mcp-config.json` | JSON (`mcpServers`) |
| Aider | `.aider.conf.yml` | YAML (`mcp-server` list) |
| Codex CLI/Desktop | `.codex/config.toml` (or `~/.codex/config.toml`) | TOML (`[mcp]` tables) |
| OpenCode | `opencode.json` | JSON (`mcp` key) |

Each needs a separate entry pointing to the same `python workflows/biblio-tools/scripts/server.py` command.

### 2. Per-AI verification scope

Resolved on 2026-06-11. `verify.py` now reads the selected AI's native config format and launches the exact configured command through a protocol-level smoke test. The test completes an MCP handshake, confirms the eight expected tools, calls `get_timestamp`, and checks invalid-input and path-traversal rejection.

### 3. Shell-fallback drift is no longer the primary concern

The original concern was that AIs without MCP might interpret shell commands differently. Now that all AIs support MCP, the biblio-tools server can enforce exact parameter types and formats for all 14. The drift problem shifts from "shell interpretation differences" to "MCP config format differences" — which is a one-time setup cost, not an ongoing drift risk.

However, MCP adoption is still optional (scripts work without it), so shell-fallback drift remains relevant for users who choose not to configure MCP. Mitigations for that scenario:

### 4. Shell-fallback drift prevention (for non-MCP configurations)

For AIs running scripts via shell without MCP typed tools, the following measures reduce drift:

**a. Canonical command format in AGENTS.md**
Every script invocation in AGENTS.md should use a single, exact command form with forward slashes. AIs are told to follow AGENTS.md literally. Example:
```
python workflows/audit/scripts/run.py
```
Not `py`, not `python3`, not backslashes. The wrapper files can specify which Python command to use on that platform.

**b. Argument quoting convention**
AGENTS.md should specify that arguments with spaces are always double-quoted. Arguments without spaces are never quoted. This removes ambiguity about when to quote.

**c. No path normalisation by AIs**
AIs must pass paths exactly as written in AGENTS.md. Forward slashes work on all platforms (Python, Git, and most CLI tools handle them). The only exception is PowerShell-specific operations (already covered in CLAUDE.md).

**d. Wrapper-file enforcement**
Each AI's wrapper file should reiterate: "When running project scripts, use the exact command format from AGENTS.md. Do not normalise paths, change the Python command, or reorder arguments."

**e. Periodic verification**
The settings-check workflow (`workflows/settings-check/scripts/run.py`) can be extended to audit whether recent sessions used the correct command format. This catches drift after the fact.

## Recommendation

1. **Done:** `mcp_support` flags corrected in verify.py (all 14 AIs now True).
2. **Done:** `AGENT-SETUP.md` documents each AI's native MCP configuration.
3. **Done:** Project-scoped configs exist for Gemini CLI, Codex, OpenCode, and Aider alongside `.mcp.json`.
4. **Done:** `verify.py` validates native config formats and performs an end-to-end MCP smoke test.
