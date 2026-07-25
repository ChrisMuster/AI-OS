# Scripts

**Last modified:** 2026-07-25

## Purpose
Contains deterministic wiki scaffolding and PDF extraction scripts.

## Contents
- run.py - `workflows/create-wiki/scripts/run.py` [[workflows/create-wiki/scripts/CONTEXT]] - Main scaffold script. Creates all wiki directories and files, updates wikis/CONTEXT.md, and appends to LOG files.
- extract_pdf.py - `workflows/create-wiki/scripts/extract_pdf.py` [[workflows/create-wiki/scripts/CONTEXT]] - Extracts a project PDF into page-preserving temporary JSON, reports OCR requirements, and optionally writes a page-delimited plain-text rendering for direct reading.

## Inputs
- `wiki_name` (command-line argument) — Lowercase with hyphens, e.g. `react-patterns`.
- `wiki_topic` (command-line argument) — One-line plain-language description of what the wiki covers.
- `pdf` (extract_pdf.py argument) — Project-root-relative path to a PDF source.

## Outputs
- `wikis/<wiki-name>/` — Fully scaffolded wiki directory containing:
  - `CONTEXT.md` — Generated from wiki-context.md.template with topic filled in.
  - `LOG.md` — Wiki root log, seeded with creation entry.
  - `raw/` — Empty source document store with CONTEXT.md and LOG.md.
  - `wiki/` — Empty wiki pages store with CONTEXT.md, LOG.md, index.md, and operations-log.md.
- Updated `wikis/CONTEXT.md` [[wikis/CONTEXT]] — New wiki entry added to Contents section.
- Updated `workflows/create-wiki/LOG.md` — Started and completed entries appended.
- Updated root `LOG.md` — Completed entry appended.
- Temporary page-preserving JSON extraction for PDF sources.
- Optional page-delimited plain-text rendering (`--text`), written beside the JSON in the same temporary directory as UTF-8 with LF line endings. Pages are separated by `===== PAGE N =====` markers.

## Steps
Run from anywhere (paths are resolved relative to the script, not the CWD):

```
python workflows/create-wiki/scripts/run.py <wiki-name> "<topic description>"
```

For PDF extraction:

```
python workflows/create-wiki/scripts/extract_pdf.py wikis/<wiki-name>/raw/<document>.pdf
```

To read the source directly rather than working from the JSON, add `--text`:

```
python workflows/create-wiki/scripts/extract_pdf.py wikis/<wiki-name>/raw/<document>.pdf --text
```

Both outputs are keyed to the PDF's content digest and cached, so re-running is instant and `--force` is only needed when the source file itself changes.

## Dependencies
- `workflows/create-wiki/wiki-context.md.template` [[workflows/create-wiki/CONTEXT]] — Template used to generate the new wiki's root CONTEXT.md.
- `wikis/CONTEXT.md` [[wikis/CONTEXT]] — Updated by the script on every run.
- `workflows/create-wiki/LOG.md` — Appended by the script on every run.
- `LOG.md` (root) — Appended by the script on every run.
- `workflows/create-wiki/requirements.txt` [[workflows/create-wiki/CONTEXT]] — Provides pypdf for text-based PDF extraction.
- `workflows/biblio-tools/scripts/runtime.py` [[workflows/biblio-tools/scripts/CONTEXT]] — Automatically re-runs extraction with the canonical project `.venv`.

## Known Issues
- The update logic for wikis/CONTEXT.md relies on its current markdown structure (section heading names and list item format). If that file is restructured, the insertion logic may place entries incorrectly.
- extract_pdf.py detects image-only PDFs but does not provide OCR.

## Revision History
- 2026-05-27 — Initial creation. Implements the 90-10 protocol for the create-wiki workflow.
- 2026-06-06 — Fixed generated Dependencies paths in raw_context() and wiki_subdir_context(): replaced `../CONTEXT.md` and `../raw/` with project-root-relative paths (`wikis/{wiki_name}/CONTEXT.md`, etc.).
- 2026-06-11 — Stopped adding individual personal wikis to README.md; wikis/CONTEXT.md remains the authoritative wiki list.
- 2026-06-11 — Added extract_pdf.py for shared page-preserving PDF extraction with OCR detection.
- 2026-06-11 — Made extract_pdf.py enter the canonical project runtime automatically.
- 2026-07-08 - Replaced em dashes with hyphens in run.py's durable generated content and log-writing strings (wiki/raw CONTEXT and LOG headings, Revision History seed lines, the wikis/CONTEXT.md insertion entry, and the rerun and root-LOG notes), so scaffolded files and log entries no longer seed ai-style-guard em-dash warnings. Non-durable strings (docstrings, comments, stdout, argparse help) left unchanged.
- 2026-07-24 - Added a `--text` flag to extract_pdf.py that writes a page-delimited plain-text rendering beside the extraction JSON. Removes the need to hand-write a throwaway flatten script in the session scratchpad on every PDF ingest, which had been the practice because the extractor stopped at JSON. Additive and backward compatible: without the flag the behaviour and output are unchanged.
- 2026-07-25 - Converted the two em dashes in the Contents entries to hyphens; ai-style-guard flagged the extract_pdf.py line (edited on 2026-07-24), and the run.py line was converted too for consistency. Style-only, no behavioural change.
