# Scripts

**Last modified:** 2026-09-22

## Purpose
Contains the run.py script for the link-check workflow. Handles three operations: inserting Obsidian wiki links into CONTEXT.md files (--link), auditing those links for dead targets (--audit), and auto-fixing dead links where possible (--fix).

## Contents
- run.py - Main script. Three modes: --link inserts links, --audit reports dead links, --fix auto-resolves dead links. `--link` skips a reference resolving to the containing file's own target (`own_link_target`), and removes such a link where it already exists (`strip_self_links`) but only in a file it is already rewriting for an insertion.

## Inputs
No required inputs. Optional flags:
- `--link` — Insert [[links]] mode.
- `--audit` — Dead-link audit mode (default).
- `--fix` — Audit plus auto-fix mode.
- `--dry-run` — Preview without writing.
- `--save` — Save report to `workflows/link-check/last-report.md`.

## Outputs
- Modified CONTEXT.md files (--link and --fix modes, unless --dry-run).
- Report printed to stdout.
- Optionally: `workflows/link-check/last-report.md` (when --save is passed).
- Updated `workflows/link-check/LOG.md` — started and completed entries appended.
- Updated root `LOG.md` — completed entry appended.

## Steps
Run from anywhere:

```
python workflows/link-check/scripts/run.py [--link|--audit|--fix] [--dry-run] [--save]
```

## Dependencies
- All `CONTEXT.md` files in the project — read by all modes.
- `workflows/link-check/LOG.md` — appended on every run.
- `LOG.md` (root) [[LOG]] — appended on every run.

## Known Issues
- `apply_link_fix` rewrites a renamed target with a blanket `content.replace()` across the whole file, so unlike the `--link` path it has no concept of a region it must not touch and will edit a `[[link]]` inside a dated Revision History entry. See the fuller note in `workflows/link-check/CONTEXT.md` [[workflows/link-check/CONTEXT]] Known Issues.

## Revision History
- 2026-06-03 — Initial creation.
- 2026-08-04 - Line endings pinned on all four text writes in `run.py` (the LOG.md append, the two CONTEXT.md rewrites in the `--link` and retarget paths, and the `--save` report), which now pass `newline="\n"` explicitly. This directory rewrites other directories' CONTEXT.md files wholesale, so a translated newline here converted a whole file to CRLF on every link pass. Part of the project-wide pass closing this defect class at all 48 write sites.
- 2026-08-11 - `add_links_to_file` gained an `in_revision_history` flag beside its existing `in_code_block` flag, so `--link` no longer inserts links into dated Revision History entries. Set on a `## Revision History` heading and cleared at the next level-two heading, which is the boundary the audit's section parser already uses; evaluated after the fenced-block guard, so a heading written inside a fence is sample text rather than a section boundary. Covered by the new suite at `workflows/link-check/tests/` [[workflows/link-check/tests/CONTEXT]]. Reading `apply_link_fix` during the same change surfaced that the `--fix` path has no such region concept at all; recorded in Known Issues rather than changed here.
- 2026-09-22 - `--link` stopped inserting a link from a file to itself, and removes one where it finds it. `resolve_link_target` sends a non-`.md` file reference to its parent directory's CONTEXT.md, and a Contents entry naming a file in its own directory is the commonest shape of a Contents line in this project, so a self-link was the default outcome rather than an edge case: 170 had accumulated across 63 of 143 CONTEXT.md files, a fifth of every link present. `process_line` now takes the containing file's own target and skips a reference resolving to it, and `strip_self_links` removes one already there. **The removal never earns a write on its own**, which is the whole of the cleanup policy and is pinned by a test: stripping project-wide would rewrite 63 tracked files and owe a Revision History and LOG.md entry for each, a large documentation cost for links the knowledge graph already discards, so removals ride along only in a file being rewritten for an insertion, where that cost is already paid. The stated limitation is that a file whose only candidate references point at itself never earns an insertion again, so its self-links stay until something else edits it. The skip is deliberately the same comparison the knowledge-graph builder already makes (`target == source_id`), so two components answer one question the same way rather than each inventing a rule.
