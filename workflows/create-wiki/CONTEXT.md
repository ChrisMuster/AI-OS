# Create Wiki

**Last modified:** 2026-05-27

## Purpose
Scaffolds a new LLM Wiki directory inside `wikis/`. Creates the full wiki structure (raw/, wiki/, CONTEXT.md, index.md, operations-log.md) in one pass so every wiki starts consistent and ready to use.

## Contents
- wiki-context.md.template — `workflows/create-wiki/wiki-context.md.template` — The CONTEXT.md template for new wikis, with a `{{WIKI_TOPIC}}` placeholder that gets replaced with the wiki's topic description.
- scripts/ — `workflows/create-wiki/scripts/` — Automation scripts for this workflow; run.py is the main scaffold entry point.

## Inputs
- A wiki name from the user (e.g. "ai-fundamentals", "react-patterns"). Used as the directory name under `wikis/`.
- A topic description from the user (e.g. "AI and machine learning fundamentals"). Replaces `{{WIKI_TOPIC}}` in the wiki's CONTEXT.md.

## Outputs
A fully scaffolded wiki directory at `wikis/<wiki-name>/` containing:
- `CONTEXT.md` — Wiki-specific instructions and rules, with the topic filled in.
- `raw/` — Empty directory for source documents, with its own CONTEXT.md and LOG.md.
- `wiki/` — Empty directory for wiki pages, with its own CONTEXT.md and LOG.md.
- `wiki/index.md` — Table of contents for the wiki (seeded with header only).
- `wiki/operations-log.md` — Append-only record of wiki operations (seeded with creation entry).

## Steps
1. Ask the user for the wiki name (lowercase with hyphens, e.g. `react-patterns`) and a one-line topic description.
2. Optionally run with `--dry-run` first to preview what will be created:
   `python workflows/create-wiki/scripts/run.py <wiki-name> "<topic description>" --dry-run`
3. Run the scaffold script:
   `python workflows/create-wiki/scripts/run.py <wiki-name> "<topic description>"`
4. The script handles everything: directory and file creation, updates to wikis/CONTEXT.md and README.md, all LOG entries, and a final structural audit to verify nothing is broken.

## Dependencies
- `CLAUDE.md` (root) — Defines the CONTEXT.md schema, LOG.md format, and verification checklist that this workflow follows.
- `templates/` — The general CONTEXT.md and LOG.md templates inform the structure for subdirectories (raw/, wiki/), though the wiki's own CONTEXT.md uses a wiki-specific template stored in this workflow directory.
- `wikis/CONTEXT.md` — Updated automatically by run.py on each run.
- `README.md` (root) — Updated automatically by run.py on each run.
- `workflows/create-wiki/scripts/run.py` — The main automation script that handles all file creation and updates.

## Known Issues
- The wiki CONTEXT.md template uses its own structure (based on Andrej Karpathy's LLM Wiki pattern) rather than the standard CONTEXT.md schema. This is intentional — the wiki CONTEXT.md serves as an operational instruction set for how to use the wiki, not a generic directory description.
- If the wiki CONTEXT.md template here changes, existing wikis that were created with older versions will not automatically update. Any structural changes to the template should be manually propagated to existing wikis if needed.
- The `wiki/operations-log.md` file inside each wiki serves the same purpose as our system LOG.md but follows the wiki's own format. This is not a duplication — the wiki log tracks wiki operations (ingests, page edits), while the system LOG.md in the wiki/ subdirectory tracks Biblio's system-level actions.
- The update logic in run.py for wikis/CONTEXT.md and README.md relies on their current markdown structure (section heading names, list item format). If those files are restructured, the script's insertion logic may fail or place entries incorrectly.

## Revision History
- 2026-05-13 — Initial creation.
- 2026-05-14 — Renamed wiki/log.md to wiki/operations-log.md across all references to avoid case-insensitive filename collision with system LOG.md on Windows.
- 2026-05-27 — Converted to 90-10 protocol. Added scripts/ subdirectory with run.py scaffold script. Steps reduced from 11 AI-driven steps to 4. Script now handles all file creation, wikis/CONTEXT.md and README.md updates, and log entries. Also adds LOG.md to wiki root (previously missing).
- 2026-05-27 — Added idempotency and --dry-run flag to run.py. Added post-scaffold audit step: script now runs the structural audit automatically on completion and reports any failures or warnings.
