# Book Dragon

**Last updated:** 30 July 2026

Book Dragon is a personal AI operating system, powered by **Biblio** — an assistant persona you configure for your own life and workflow. Biblio is not an AI in its own right: the intelligence behind it is provided by whichever AI you are running. Book Dragon is AI-agnostic — the underlying model can be swapped while Biblio's identity and rules remain the same.

All universal rules live in `AGENTS.md` (the open standard adopted by the Linux Foundation). Each supported AI has a thin wrapper file that imports `AGENTS.md` and adds only AI-specific details. See `CLAUDE.md`, `GEMINI.md`, `.github/copilot-instructions.md`, `.cursor/rules/project.mdc`, `.windsurf/rules/project.md`, `.clinerules/00-project.md`, and `.aider.conf.yml`.

The system is modular, organised into workflows, wikis, skills, and other tools that grow over time.

## Requirements

**Python 3.9+** is the only system-level requirement. Run `python workflows/biblio-tools/scripts/setup.py` once to create Book Dragon's shared project environment and install all workflow packages. See `REQUIREMENTS.md` for full details and API key setup.

**Supported AIs:** Book Dragon works with any AI that reads `AGENTS.md`. Currently supported via thin wrapper files: Claude Code, Claude Cowork, GitHub Copilot, Gemini CLI, Cursor, Windsurf, Cline, and Aider. Codex CLI, Codex Desktop, and OpenCode read `AGENTS.md` natively with no wrapper needed. See `AGENT-SETUP.md` for per-AI setup instructions.

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
- **Biblio Tools** — MCP server exposing project scripts as typed, callable tools for any AI that supports the Model Context Protocol. `workflows/biblio-tools/` `[active]`
- **Reddit Collector** — Downloads posts from configured subreddits, saves as Markdown, detects multi-part series and groups them. `workflows/reddit-collector/` `[active]` — For setup, see `workflows/reddit-collector/SETUP.md`.
- **Knowledge Graph** — Deterministic indexer that parses CONTEXT.md files and approved relationships into a rebuildable node/edge graph, with validation (broken references, orphans, uncontained directories, duplicate titles) and read-only query/traversal (impact analysis, shortest path, subtree, stats). Available via CLI and as the `build_knowledge_graph` / `query_knowledge_graph` MCP tools on the Biblio Tools server. `workflows/knowledge-graph/` `[active]`
- **Encoding Guard** - Checks the project for encoding problems (invalid UTF-8, mojibake, byte-order marks, and text-mode subprocess calls with no explicit encoding) and repairs corrupted files to clean UTF-8 with LF line endings. Runs standalone via CLI and automatically as part of the full audit. `workflows/encoding-guard/` `[active]`
- **Personal Data Guard** - Read-only checker that scans committable files for personal data (email addresses, personal home paths, the user's name and account name, and a configurable denylist of personal nouns) so a leak is caught mechanically before commit. Runs standalone via CLI and automatically as an advisory hook in the full audit. `workflows/personal-data-guard/` `[active]`
- **AI-Style Guard** - Read-only checker that scans added or changed lines (via git diff, so legacy text is never touched) for AI writing tells: high-confidence typographic markers and stock phrases (WARN) plus a tunable single-word denylist (INFO). Enforces the writing-style rule that previously relied on discipline alone. Runs standalone via CLI and automatically as an advisory hook in the full audit. `workflows/ai-style-guard/` `[active]`
- **Check For Updates** - Reports whether the project's Python packages and installed AI CLI tools have newer versions available, with recommendations. Read-only: it never updates anything; you review the report and decide what to act on. An opt-in advisory landscape mode (`--landscape`) also watches the supported AI tools for product-status changes (renames, deprecations, replacements) via web research. `workflows/check-for-updates/` `[active]`
- **Rule Hooks** - Deterministic rule enforcement that moves load-bearing always/never rules out of prose into hooks that block a violation the moment an AI acts. Phase 1 covers Claude Code and Codex (blocking: protect .env, no shell redirect into LOG.md, no dangerous bash, no personal data in committable files; plus two log-only trial rules) and ships a universal git pre-commit personal-data gate that protects every AI and manual commits, with a SessionStart reminder of the permission gate. `workflows/rule-hooks/` `[active]`
- **Close-out** - The executable half of the close-out task: one command that runs the structural audit, the link audit, and the workflow test suites as a single pass/fail gate, so a "checks pass" claim is a script exit code rather than prose. Defaults to the tests for the workflows changed in the current work; `--scope all` runs every suite for a full close-out. Backs the Verification discipline rule in AGENTS.md. `workflows/close-out/` `[active]`
- **Weekly Review** - The cron + memory flywheel: a deterministic gather script assembles the week's signal (journal, LOG.md activity, git history, memory changes, session activity, prior reviews) into a briefing packet, and a session-startup staleness gate surfaces "a review is due" so Biblio writes a one-page review into the `reviews/` store and distils durable facts into memory. Startup-gated rather than an autonomous cron (no CLI/IDE scheduler can wake an LLM and touch local files), so it works identically on every AGENTS-reading AI. Tracks journal coverage so a backfilled day is never lost. `workflows/weekly-review/` `[active]`
- **Handoff** - Clean session-to-session transitions. A trigger phrase ("do the handoff") runs a deterministic gather pass (branch, working tree, recent commits, changed-directory logs, active backlog, session activity) and Biblio writes a structured, rolling `HANDOVER.md`. On the next session a startup recovery step reads any unread handoff (tracked by a seen-watermark) and opens with an informed greeting, so the new session knows where the last one left off while the user keeps the choice. No per-AI slash commands; it works identically on every AGENTS-reading AI. `workflows/handoff/` `[active]`
- **Triggers** - The AI-agnostic replacement for a per-AI slash-command set: a single tracked registry (`config/triggers.yaml`) mapping natural-language phrases to Book Dragon actions, referenced by `AGENTS.md` so any AI recognises them. Ask "give me a list of triggers" and `run.py --list` prints the grouped set. `workflows/triggers/` `[active]`
- **Memory Diff** - Shows what changed in `memory/` since the last session. A content-watermark diff over `memory/LOG.md` surfaces new memory entries (added, updated, archived) silently at session startup and on the "what changed in memory" trigger, then advances the watermark so nothing is shown twice. Startup-gated and trigger-driven (no per-AI adapter), so it works identically on every AGENTS-reading AI. `workflows/memory-diff/` `[active]`
- **Doc-Sync Guard** - Read-only checker that catches CONTEXT.md / LOG.md drift: for every committable directory whose content changed, it verifies the directory's CONTEXT.md moved (a Revision History entry, Last modified matching the newest entry) and its gitignored LOG.md gained an entry (verified by mtime, since a gitignored file cannot be content-diffed), plus the coarse child add/remove parent-propagation case. It fires at three stopping points: a warn-not-block advisory in the git pre-commit hook, a section in the handoff packet, and a hard fail in the close-out verifier. Makes the AGENTS.md CONTEXT/LOG maintenance rule mechanical rather than discipline-dependent. `workflows/doc-sync-guard/` `[active]`
- **Doc-Verify** - Read-only structural self-consistency checker for a long markdown document, passed as an argument rather than hardcoded: it verifies that every table's rows match its separator's cell count, that enumerated table rows and numbered section headings are contiguous and in order, that every `path:line` code citation resolves against the repository, and it lists references-by-distance ("two bullets down") for adjudication. It also checks character hygiene in binary mode - CR bytes, tabs, trailing whitespace, a final newline, and any codepoint above U+007F - which covers the gap between Encoding Guard (UTF-8 validity, mojibake, BOMs) and AI-Style Guard (typographic tells, but scoped to `git diff` content and so blind to gitignored files). It exists because verifying a document by writing a fresh throwaway script each review round means each runner re-implements the definition, and a wrong definition produces a confident wrong number. `workflows/doc-verify/` `[active]`
- **Skill-Hardening Guard** - Read-only checker that verifies every SKILL.md carries the two required load-bearing sections: a complete `## Hardening` section (the declarative safety envelope: five fields - allowed tool intent, never-do actions, approval-gated operations, write boundaries, and a verification / escape hatch) and a non-empty `## Verification` section (how a caller confirms the skill's output is correct). It validates presence and shape only, never the truth of the declared policy, keeping runtime enforcement out of scope so the check stays portable across every AGENTS-reading AI. Advisory WARN in the full audit; a hard fail in the close-out verifier. `workflows/skill-hardening-guard/` `[active]`

## Skills

- **Web Research** — Shared research engine; importable by any workflow. `skills/web-research/` `[active]`
- **Image Prompt** — Analyses written content and recommends an image type (real photo, stock, or AI-generated), then delivers the appropriate prompt or search terms. `skills/image-prompt/` `[active]`
- **Lean Code** - Opt-in ruleset that steers AI coding agents toward the smallest correct solution, with a decision ladder, three intensity modes, a tag vocabulary, and a `lean:` marker convention. Ruleset is active; the diff-review, repo-audit, and debt-tracking skills are planned. `skills/lean-code/` `[active]`

## Wikis

Your personal wikis appear here once created — use the Create Wiki workflow to add them. Individual wikis are not listed in this file as they are personal content.

## Conversations

- **Conversations** — Saved summaries of notable sessions with Biblio — ideas explored, decisions made, threads left open. Saved by choice, not automatically. `conversations/` `[active]`

## Journal

- **Daily Journal** — Personal daily journal for noting small things and reviewing patterns on request. `journal/` `[active]`

## Memory

- **Memory** — Project-scoped persistent memory that syncs with the project. Overrides the per-user Claude cache. `memory/` `[active]`

## Reviews

- **Reviews** - Store for the weekly-review flywheel; holds the dated one-page reviews produced by the Weekly Review workflow. Personal content, local only. `reviews/` `[active]`

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
