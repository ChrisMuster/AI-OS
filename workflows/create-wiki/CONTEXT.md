# Create Wiki

**Last modified:** 2026-06-11

## Purpose
Scaffolds a new LLM Wiki directory inside `wikis/` [[wikis/CONTEXT]]. Creates the full wiki structure (raw/, wiki/, CONTEXT.md, index.md, operations-log.md) in one pass so every wiki starts consistent and ready to use.

## Contents
- wiki-context.md.template — `workflows/create-wiki/wiki-context.md.template` [[workflows/create-wiki/CONTEXT]] — The CONTEXT.md template for new wikis, with a `{{WIKI_TOPIC}}` placeholder that gets replaced with the wiki's topic description.
- requirements.txt — `workflows/create-wiki/requirements.txt` [[workflows/create-wiki/CONTEXT]] — Shared PDF extraction dependency installed in the project `.venv`.
- scripts/ — `workflows/create-wiki/scripts/` [[workflows/create-wiki/scripts/CONTEXT]] — Automation scripts for this workflow; run.py is the main scaffold entry point.

## Inputs
- A wiki name from the user (e.g. "ai-fundamentals", "react-patterns"). Used as the directory name under `wikis/` [[wikis/CONTEXT]].
- A topic description from the user (e.g. "AI and machine learning fundamentals"). Replaces `{{WIKI_TOPIC}}` in the wiki's CONTEXT.md.
- For PDF ingestion: a project-root-relative path to a PDF stored in a wiki's `raw/` directory.

## Outputs
A fully scaffolded wiki directory at `wikis/<wiki-name>/` containing:
- `CONTEXT.md` — Wiki-specific instructions and rules, with the topic filled in.
- `raw/` — Empty directory for source documents, with its own CONTEXT.md and LOG.md.
- `wiki/` — Empty directory for wiki pages, with its own CONTEXT.md and LOG.md.
- `wiki/index.md` — Table of contents for the wiki (seeded with header only).
- `wiki/operations-log.md` — Append-only record of wiki operations (seeded with creation entry).
- Page-preserving PDF extraction JSON in the operating system's temporary directory. This working data is not committed and does not modify the source PDF.

## Steps
1. Ask the user for the wiki name (lowercase with hyphens, e.g. `react-patterns`) and a one-line topic description.
2. Optionally run with `--dry-run` first to preview what will be created:
   `python workflows/create-wiki/scripts/run.py <wiki-name> "<topic description>" --dry-run`
3. Run the scaffold script:
   `python workflows/create-wiki/scripts/run.py <wiki-name> "<topic description>"`
4. The script handles everything: directory and file creation, the update to wikis/CONTEXT.md, all LOG entries, and a final structural audit to verify nothing is broken. README.md remains generic because individual wikis are personal content.
5. For PDF ingestion, run `python workflows/create-wiki/scripts/extract_pdf.py <project-relative-pdf>`, read the temporary page-preserving JSON, and follow the generated wiki's ingest workflow.

## Dependencies
- `AGENTS.md` [[AGENTS]] (root) — Defines the CONTEXT.md schema, LOG.md format, and verification checklist that this workflow follows.
- `templates/` [[templates/CONTEXT]] — The general CONTEXT.md and LOG.md templates inform the structure for subdirectories (raw/, wiki/), though the wiki's own CONTEXT.md uses a wiki-specific template stored in this workflow directory.
- `wikis/CONTEXT.md` [[wikis/CONTEXT]] — Updated automatically by run.py on each run.
- `workflows/create-wiki/scripts/run.py` [[workflows/create-wiki/scripts/CONTEXT]] — The main automation script that handles all file creation and updates.
- Python `pypdf` package — Extracts text and metadata from PDFs into temporary page-preserving JSON.
- `workflows/biblio-tools/scripts/runtime.py` [[workflows/biblio-tools/scripts/CONTEXT]] — Moves dependency-bearing scripts into the canonical `.venv` consistently across operating systems and AI clients.

## Known Issues
- The wiki CONTEXT.md template uses its own structure (based on Andrej Karpathy's LLM Wiki pattern) rather than the standard CONTEXT.md schema. This is intentional — the wiki CONTEXT.md serves as an operational instruction set for how to use the wiki, not a generic directory description.
- If the wiki CONTEXT.md template here changes, existing wikis that were created with older versions will not automatically update. Any structural changes to the template should be manually propagated to existing wikis if needed.
- The `wiki/operations-log.md` file inside each wiki serves the same purpose as our system LOG.md but follows the wiki's own format. This is not a duplication — the wiki log tracks wiki operations (ingests, page edits), while the system LOG.md in the wiki/ subdirectory tracks Biblio's system-level actions.
- The update logic in run.py for wikis/CONTEXT.md relies on its current markdown structure (section heading names and list item format). If that file is restructured, the script's insertion logic may fail or place entries incorrectly.
- Image-only and scanned PDFs require OCR. The extractor flags this condition but does not currently perform OCR.

## Revision History
- 2026-05-13 — Initial creation.
- 2026-05-14 — Renamed wiki/log.md to wiki/operations-log.md across all references to avoid case-insensitive filename collision with system LOG.md on Windows.
- 2026-05-27 — Converted to 90-10 protocol. Added scripts/ subdirectory with run.py scaffold script. Steps reduced from 11 AI-driven steps to 4. Script now handles all file creation, wikis/CONTEXT.md and README.md updates, and log entries. Also adds LOG.md to wiki root (previously missing).
- 2026-05-27 — Added idempotency and --dry-run flag to run.py. Added post-scaffold audit step: script now runs the structural audit automatically on completion and reports any failures or warnings.
- 2026-06-06 — Fixed bug in run.py: generated CONTEXT.md files used `../` relative paths in Dependencies sections instead of project-root-relative paths. Both raw_context() and wiki_subdir_context() corrected.
- 2026-06-09 — Dependencies updated from CLAUDE.md to AGENTS.md (AI-agnostic transition).
- 2026-06-11 — Removed individual wiki insertion into README.md to comply with personal data isolation rules.
- 2026-06-11 — Updated the wiki template to identify Biblio rather than a specific AI product as the maintainer.
- 2026-06-11 — Added AI-agnostic PDF extraction using pypdf in the shared project `.venv`.
- 2026-06-11 — Updated PDF extraction to enter the canonical project runtime automatically.
