# Book Dragon

**Last updated:** 9 June 2026

Book Dragon is a personal AI operating system, powered by **Biblio** — an assistant persona you configure for your own life and workflow. Biblio is not an AI in its own right: the intelligence behind it is provided by whichever AI you are running. Book Dragon is AI-agnostic — the underlying model can be swapped while Biblio's identity and rules remain the same.

All universal rules live in `AGENTS.md` (the open standard adopted by the Linux Foundation). Each supported AI has a thin wrapper file that imports `AGENTS.md` and adds only AI-specific details — see `CLAUDE.md`, `GEMINI.md`, `.github/copilot-instructions.md`, `.cursor/rules/project.mdc`, `.windsurf/rules/project.md`, `.clinerules/00-project.md`, `.roo/rules/project.md`, `.continue/rules/00-project.md`, and `.aider.conf.yml`.

The system is modular, organised into workflows, wikis, skills, and other tools that grow over time.

## Requirements

**Python 3.9+** is the only current system-level requirement. See `REQUIREMENTS.md` for full details including per-workflow package requirements and API key setup.

**Supported AIs:** Book Dragon works with any AI that reads `AGENTS.md`. Currently supported via thin wrapper files: Claude Code, Claude Cowork, GitHub Copilot, Gemini CLI, Cursor, Windsurf, Cline, Roo Code, Continue.dev, and Aider. Codex CLI, Codex Desktop, and OpenCode read `AGENTS.md` natively with no wrapper needed. See `AGENT-SETUP.md` for per-AI setup instructions.

**Running with Claude:** Claude requires a **paid Claude plan**. You can access it through the Claude desktop app (which includes Chat, Code, and Cowork in one installation), the Claude Code CLI, or an IDE extension for VS Code or JetBrains. The free Claude plan does not include Claude Code or Cowork.

- Desktop app (all three products): [claude.com/download](https://claude.com/download)
- Claude Code (CLI, IDE extension, and plan details): [anthropic.com/claude-code](https://www.anthropic.com/claude-code)

## Getting started

This project is version-controlled. Personal files — `LOG.md` files, `USER.md`, memory files, journal entries, conversation saves, and wiki content — are excluded from the repository and exist only on the local machine.

**On a fresh clone:** open a session. Biblio automatically detects the missing files, walks you through any first-time setup, creates fresh `LOG.md` files and a clean memory index throughout, and confirms the system is ready before asking what you want to work on. No manual steps are required.

**Run history after that** accumulates locally and is never committed to git, so your usage history stays on your machine.

---

## Workflows

- **Create Wiki** — Scaffolds a new LLM Wiki directory with the full structure ready to use. `workflows/create-wiki/` `[active]`
- **Audit** — Checks all project directories for structural compliance and reports issues. `workflows/audit/` `[active]`
- **Web Research** — Researches any topic from multiple sources and produces a report. `workflows/web-research/` `[active]` — For setup and API key configuration, see `workflows/web-research/SETUP.md`.
- **Link Check** — Inserts Obsidian `[[links]]` into CONTEXT.md files and audits for dead targets. `workflows/link-check/` `[active]`
- **Weather** — Fetches current conditions and forecasts for any location worldwide. No API keys required. `workflows/weather/` `[active]`
- **Session Search** — Indexes all conversation transcripts into a local SQLite FTS5 database and provides a searchable session history skill. `workflows/session-search/` `[active]`
- **Settings Check** — Validates that all automated commands (hooks and scheduled tasks) are covered by allowlist entries; catches permission-prompt bugs before they occur. `workflows/settings-check/` `[active]`

## Skills

- **Web Research** — Shared research engine; importable by any workflow. `skills/web-research/` `[active]`
- **Image Prompt** — Analyses written content and recommends an image type (real photo, stock, or AI-generated), then delivers the appropriate prompt or search terms. `skills/image-prompt/` `[active]`

## Wikis

Your personal wikis appear here once created — use the Create Wiki workflow to add them. Individual wikis are not listed in this file as they are personal content.

## Conversations

- **Conversations** — Saved summaries of notable sessions with Biblio — ideas explored, decisions made, threads left open. Saved by choice, not automatically. `conversations/` `[active]`

## Journal

- **Daily Journal** — Personal daily journal for noting small things and reviewing patterns on request. `journal/` `[active]`

## Memory

- **Memory** — Project-scoped persistent memory that syncs with the project. Overrides the per-user Claude cache. `memory/` `[active]`

## User profile

- **User inputs** — Raw files for Biblio to ingest into USER.md (e.g. a CV, a skills summary). Drop a file here; Biblio reads it and flags relevant information for addition to USER.md. `user-inputs/` `[active]`

## Other

- **Templates** — Reusable boilerplate for CONTEXT.md and LOG.md files with placeholder variables. `templates/` `[active]`

---

**Status key:** `[active]` = live and in use | `[in progress]` = being built | `[archived]` = no longer active

---

## Obsidian (optional)

[Obsidian](https://obsidian.md) is a note-taking and knowledge management app that renders `[[wiki-style links]]` between notes as a navigable graph. Book Dragon uses these links throughout its `CONTEXT.md` files to connect related directories — they are inserted automatically by the Link Check workflow.

Obsidian is entirely optional. If you do not use it, the `[[links]]` will appear as plain text in any other markdown editor and can be safely ignored. Nothing in the project requires Obsidian to function.

---

## Credits

- **[Brave Search API](https://brave.com/search/api/)** — used by the web research workflow as an optional search source. Attribution required per Brave's API terms.
