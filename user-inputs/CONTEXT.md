# user-inputs

**Last modified:** 2026-06-05

## Purpose
Holds raw files that Biblio can read to populate or update USER.md. Drop any
relevant personal document here — a CV, a skills summary, a health note, or
similar — and Biblio will extract information matching USER.md tracked categories
and flag it for addition.

## Contents
None. Files are added by the user as needed. They are not listed individually
here as they are personal content. When a file is added, update the
"Current inputs" list in USER.md.

## Inputs
Files placed here by the user: CVs, skills summaries, or any other document
containing information relevant to USER.md categories.

## Outputs
No files are produced here. Extracted information is written to USER.md after
the user confirms it.

## Steps
1. Drop a file into this directory.
2. Update the "Current inputs" list in USER.md to note the filename and what it
   contains.
3. At the next session, or on request, Biblio reads the file and flags any
   information matching USER.md tracked categories: "I found X in [filename] —
   should I add that to USER.md?"
4. Confirm or dismiss each suggestion. Biblio writes confirmed items to USER.md
   and appends an entry to the root LOG.md.

## Dependencies
- USER.md — the target file for all extracted information.

## Known Issues
- Biblio reads files on request or at session startup when the "Current inputs"
  list in USER.md has changed. It does not watch the directory automatically.
- Binary formats (PDF, DOCX) are readable by Biblio directly; plain text or
  markdown files are preferred for reliability.

## Revision History
- 2026-06-05 — Initial creation.
