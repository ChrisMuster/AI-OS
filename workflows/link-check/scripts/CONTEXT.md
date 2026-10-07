# Scripts

**Last modified:** 2026-10-07

## Purpose
Contains the run.py script for the link-check workflow. Handles three operations: inserting Obsidian wiki links into CONTEXT.md files (--link), auditing those links for dead targets (--audit), and auto-fixing dead links where possible (--fix).

## Contents
- run.py - Main script. Three modes: --link inserts links, --audit reports dead links, --fix auto-resolves dead links. `--audit --json` prints the dead links as `{"dead_links": [{"file": ..., "target": ...}]}` (`dead_links_json`) in place of the Markdown report, for callers that must not parse prose; `--no-log` writes no LOG.md entry in any mode. `--link` skips a reference resolving to the containing file's own target (`own_link_target`), and removes such a link where it already exists (`strip_self_links`) but only in a file it is already rewriting for an insertion.

## Inputs
No required inputs. Optional flags:
- `--link` — Insert [[links]] mode.
- `--audit` — Dead-link audit mode (default).
- `--fix` — Audit plus auto-fix mode.
- `--dry-run` — Preview without writing.
- `--save` - Save report to `workflows/link-check/last-report.md` [[workflows/link-check/last-report]].
- `--json` - With `--audit` only: print the dead links as JSON instead of the report; refused with `--link`, `--fix` or `--save`.
- `--no-log` - Write no LOG.md entry.

Optional environment variables (orchestrator isolation S3):
- `BOOK_DRAGON_ROOT` - The project to check, for a copy of this script run from outside it (the review orchestrator's trusted host copy). The CONTEXT.md files walked, the link targets and both LOG.md files follow it. Read at import by `project_root()`; a set value that is not an existing folder holding AGENTS.md (an empty value included) exits 2. Unset, the root is three folders above the script.
- `BOOK_DRAGON_CLEAN_COPY` - Set to `1` only inside the orchestrator's offline container (any other value is not the marker). A link whose target is absent and which `git check-ignore -q` reports as ignored in the copy then has the status "checked on the host" (`link_status`, `HOST`): it is not dead, not counted by the close-out gate and not in `--json`'s `dead_links`, and the report counts and lists it under its own heading. An absent target git does not ignore is dead, as without the marker; so is one git cannot answer for.

## Outputs
- Modified CONTEXT.md files (--link and --fix modes, unless --dry-run).
- Report printed to stdout, or with `--audit --json` one JSON object, `{"dead_links": [{"file": <project-relative path>, "target": <link target>}, ...]}`. The exit code is the same either way.
- Optionally: `workflows/link-check/last-report.md` [[workflows/link-check/last-report]] (when --save is passed).
- Updated `workflows/link-check/LOG.md` - started and completed entries appended (not with `--no-log`).
- Updated root `LOG.md` - completed entry appended (not with `--no-log`).

## Steps
Run from anywhere:

```
python workflows/link-check/scripts/run.py [--link|--audit|--fix] [--dry-run] [--save] [--no-log]
python workflows/link-check/scripts/run.py --audit --json [--no-log]
```

## Dependencies
- All `CONTEXT.md` files in the project — read by all modes.
- `workflows/link-check/LOG.md` - appended on every run but a `--no-log` one.
- `LOG.md` (root) [[LOG]] - appended on every run but a `--no-log` one.

## Known Issues
- In clean-copy mode `target_ignored` runs one `git check-ignore` per absent link target, which is fine for the handful of links to gitignored files the project holds; a tree with thousands of absent targets would be slow there.
- `apply_link_fix` rewrites a renamed target with a blanket `content.replace()` across the whole file, so unlike the `--link` path it has no concept of a region it must not touch and will edit a `[[link]]` inside a dated Revision History entry. See the fuller note in `workflows/link-check/CONTEXT.md` [[workflows/link-check/CONTEXT]] Known Issues.

## Revision History
- 2026-06-03 — Initial creation.
- 2026-08-04 - Line endings pinned on all four text writes in `run.py` (the LOG.md append, the two CONTEXT.md rewrites in the `--link` and retarget paths, and the `--save` report), which now pass `newline="\n"` explicitly. This directory rewrites other directories' CONTEXT.md files wholesale, so a translated newline here converted a whole file to CRLF on every link pass. Part of the project-wide pass closing this defect class at all 48 write sites.
- 2026-08-11 - `add_links_to_file` gained an `in_revision_history` flag beside its existing `in_code_block` flag, so `--link` no longer inserts links into dated Revision History entries. Set on a `## Revision History` heading and cleared at the next level-two heading, which is the boundary the audit's section parser already uses; evaluated after the fenced-block guard, so a heading written inside a fence is sample text rather than a section boundary. Covered by the new suite at `workflows/link-check/tests/` [[workflows/link-check/tests/CONTEXT]]. Reading `apply_link_fix` during the same change surfaced that the `--fix` path has no such region concept at all; recorded in Known Issues rather than changed here.
- 2026-09-22 - `--link` stopped inserting a link from a file to itself, and removes one where it finds it. `resolve_link_target` sends a non-`.md` file reference to its parent directory's CONTEXT.md, and a Contents entry naming a file in its own directory is the commonest shape of a Contents line in this project, so a self-link was the default outcome rather than an edge case: 170 had accumulated across 63 of 143 CONTEXT.md files, a fifth of every link present. `process_line` now takes the containing file's own target and skips a reference resolving to it, and `strip_self_links` removes one already there. **The removal never earns a write on its own**, which is the whole of the cleanup policy and is pinned by a test: stripping project-wide would rewrite 63 tracked files and owe a Revision History and LOG.md entry for each, a large documentation cost for links the knowledge graph already discards, so removals ride along only in a file being rewritten for an insertion, where that cost is already paid. The stated limitation is that a file whose only candidate references point at itself never earns an insertion again, so its self-links stay until something else edits it. The skip is deliberately the same comparison the knowledge-graph builder already makes (`target == source_id`), so two components answer one question the same way rather than each inventing a rule.
- 2026-10-07 - `run.py` gained `--no-log` (no LOG.md entry in any mode) and `--audit --json` (`dead_links_json`: the dead links as one JSON object, file and target each), for the review orchestrator's trusted host link check (orchestrator isolation S2). Default behaviour unchanged. Inputs, Outputs, Steps and Dependencies updated; tested in `tests/test_json_and_no_log.py`.
- 2026-10-07 - Project-root override and clean-copy mode (orchestrator isolation S3): `run.py` gained `project_root()` (`BOOK_DRAGON_ROOT`, a bad value exits 2) and `link_status`, under which, with the clean-copy marker only, an absent target git ignores is "checked on the host" rather than dead and is listed in its own report section. `link_resolves` is unchanged. Inputs and Known Issues updated; tested in `tests/test_root_and_clean_copy.py`.
