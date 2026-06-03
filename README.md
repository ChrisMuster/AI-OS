# Book Dragon

**Last updated:** 3 June 2026 (conversations/ added)

Book Dragon is a personal AI operating system, powered by **Biblio** — an assistant persona you configure for your own life and workflow. Biblio is not an AI in its own right: the intelligence behind it is provided by whichever AI you are running. The current default is Claude by Anthropic. Book Dragon is intended to become AI-agnostic in future, so the underlying model can eventually be swapped while Biblio's identity and rules remain the same.

The system is modular, organised into workflows, wikis, skills, and other tools that grow over time.

## Getting started

This project is version-controlled. `LOG.md` files are excluded from the repository to keep personal run history private — they are created automatically on first use.

**On a fresh clone:** open a session and tell Biblio this is a new installation. Biblio will detect the missing root `LOG.md`, walk every directory, create fresh `LOG.md` files throughout, and confirm the system is ready before asking what you want to work on. No manual setup is required.

**Run history after that** accumulates locally and is never committed to git, so your usage history stays on your machine.

---

## Workflows

- **Create Wiki** — Scaffolds a new LLM Wiki directory with the full structure ready to use. `workflows/create-wiki/` `[active]`
- **Audit** — Checks all project directories for structural compliance and reports issues. `workflows/audit/` `[active]`
- **Web Research** — Researches any topic from multiple sources and produces a report. `workflows/web-research/` `[active]`
- **Link Check** — Inserts Obsidian `[[links]]` into CONTEXT.md files and audits for dead targets. `workflows/link-check/` `[active]`
- **Weather** — Fetches current conditions and forecasts for any location worldwide. No API keys required. `workflows/weather/` `[active]`

## Skills

- **Web Research** — Shared research engine; importable by any workflow. `skills/web-research/` `[active]`
- **Image Prompt** — Analyses written content and recommends an image type (real photo, stock, or AI-generated), then delivers the appropriate prompt or search terms. `skills/image-prompt/` `[active]`

## Wikis

Your personal wikis appear here once created — use the Create Wiki workflow to add them. Individual wikis are not listed in this file as they are personal content.

## Conversations

- **Conversations** — Saved summaries of notable sessions with Biblio — ideas explored, decisions made, threads left open. Saved by choice, not automatically. `conversations/` `[active]`

## Journal

- **Daily Journal** — Personal daily journal for noting small things and reviewing patterns on request. `journal/` `[active]`

## Other

- **Templates** — Reusable boilerplate for CONTEXT.md and LOG.md files with placeholder variables. `templates/` `[active]`

---

**Status key:** `[active]` = live and in use | `[in progress]` = being built | `[archived]` = no longer active
