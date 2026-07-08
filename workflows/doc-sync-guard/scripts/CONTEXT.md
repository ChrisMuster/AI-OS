# Scripts

**Last modified:** 2026-07-07

## Purpose
Holds the doc-sync guard's entry point and its pure, unit-tested helpers. `run.py` collects the change set from git, resolves each changed file to the directory that owns its `CONTEXT.md`, then checks that each in-scope directory's CONTEXT.md and LOG.md were updated for the change. The two helper modules are pure (no filesystem or git access) so the fiddly parsing is tested with fixtures. It is read-only by design and uses only the standard library plus the git CLI.

## Contents
- run.py - `workflows/doc-sync-guard/scripts/run.py` [[workflows/doc-sync-guard/scripts/CONTEXT]] - The entry point and impure layer: git I/O (`changed_name_status` with `--no-renames`, `untracked_files`, `committable_paths`, `file_at_ref`, `merge_base`), ownership resolution (`committable_context_dirs`, `deleted_context_dirs`, `path_under_dir`, `owner_dir`), the per-directory CONTEXT/LOG checks (`run_check`, `_check_directory`, `_check_log`, `_check_parent_propagation`), and the CLI (`--check`, `--json`, `--since`, `--base`, `--staged`, `--strict`).
- context_parse.py - `workflows/doc-sync-guard/scripts/context_parse.py` [[workflows/doc-sync-guard/scripts/CONTEXT]] - Pure CONTEXT.md parsing: `last_modified_date`, `revision_history_dates`, `revision_history_entry_lines`, `archive_reference_lines`, `newest_revision_history_date`, and `gained_rh_entry` (a section-aware before/after comparison of full entry lines, archive-aware), all skipping the "Earlier history archived" reference line.
- logtime.py - `workflows/doc-sync-guard/scripts/logtime.py` [[workflows/doc-sync-guard/scripts/CONTEXT]] - Pure LOG.md timestamp parsing: `newest_log_datetime` (handles the ISO-with-offset and legacy date-only forms) and `log_is_behind` (the batch-reference comparison with a small mtime tolerance).

## Inputs
- `git diff` output and the on-disk `CONTEXT.md` / `LOG.md` files under the project root.

## Outputs
- A human report or `--json` payload on stdout. No files written.

## Steps
1. Resolve the diff base (default HEAD; `--since REF`, `--base BRANCH`, or `--staged` to override).
2. Collect changed files (tracked diff plus new untracked, or the staged set), group them by owning directory, and select the directories with real content changes.
3. For each in-scope directory, check the CONTEXT.md (Revision History entry added, Last modified matches newest entry) and the LOG.md (newest entry not older than the changed files); add the coarse child add/remove parent-propagation check.
4. Print the report (or JSON). Exit 1 under `--strict` if any WARN exists, else 0. (Read-only: no LOG.md entry on a check run.)

## Dependencies
- `context_parse.py` and `logtime.py` - imported by `run.py` for the pure parsing.
- `AGENTS.md` [[AGENTS]] (root) - The CONTEXT.md schema and LOG.md rules the checks are derived from.
- The `git` CLI for change discovery; otherwise the Python 3.9+ standard library.

## Known Issues
- Ownership assumes every documented directory carries a committable `CONTEXT.md` (the project convention). A directory without one is treated as owned by its nearest ancestor that has one.
- The "added a Revision History entry" check (`gained_rh_entry`) is section-aware, so a dated bullet added elsewhere does not count, but a Revision History entry written in a non-standard shape would not be recognised. Only a flush-left `- YYYY-MM-DD ...` bullet is an entry; a dated bullet indented under an entry is treated as detail, not a separate entry. It compares full entry lines (via a multiset) before and after: a new line with no old line removed is a gain (normal add or same-day second entry); a new line alongside removed lines is a gain only when the change fits a genuine archive-plus-append shape - a non-empty suffix of the old entries is retained unchanged as the prefix of the new entries, at least one genuinely new entry (dated no earlier than the newest retained entry) follows them, and the "Earlier history archived" reference line was added or advanced. A bare archive reference line change is not sufficient on its own, so an in-place edit of an existing entry dressed up with an archive line - or an old entry edited and moved to the bottom - is correctly not counted as a gain. Anything that does not fit the archive-plus-append shape (including archiving every old entry, or manually deleting entries without archiving and adding an unrelated one) biases to "no entry gained" - a loud false positive on those rare, rule-breaking combinations, never a silent miss.
- File discovery depends on git. Outside a git checkout the guard scans nothing and emits a single WARN finding, so consumers surface the degraded scan instead of treating it as clean.

## Revision History
- 2026-07-06 - Initial creation. `run.py` (scope collector, ownership resolver, CONTEXT and LOG checks, parent propagation, CLI) plus the pure `context_parse` and `logtime` helper modules.
- 2026-07-07 - Codex review fixes. `changed_name_status` now passes `--no-renames` so a move is seen as delete+add and the source directory is checked, not only the destination (finding 1). Revision History "gained an entry" detection is now section-aware: `context_parse.added_lines_have_rh_entry` was replaced by `gained_rh_entry`, a before/after comparison that treats an entry as gained when the newest entry date advances or the entry count grows (so it handles a same-day second entry and an add-while-archiving change alike), and `run.py` loads the pre-change CONTEXT via a new `file_at_ref` helper instead of parsing added diff lines, so a dated bullet added outside the section no longer counts (finding 2). Removed the now-unused `added_lines` / `_parse_added_lines`.
- 2026-07-07 - Second-review fix in `gained_rh_entry`. It now compares full Revision History entry *lines* (via a multiset) rather than dates, so editing an existing entry's date forward is no longer mistaken for a new entry (the date-only comparison returned a false positive there - a silent miss). New pure helpers `revision_history_entry_lines` and `archive_reference_lines`; the archive-plus-add case is distinguished by the archive reference line changing, which also handles a flat-count archive. `revision_history_dates` / `newest_revision_history_date` are retained for the Last modified check.
- 2026-07-07 - Third-review fix in `gained_rh_entry`. Tightened the archive branch: a changed "Earlier history archived" reference line alone no longer proves a gain, which let an in-place edit of the only entry pass silently when a bogus archive line was added. New pure helper `_is_archive_plus_append` requires the genuine shape - a non-empty suffix of the old entries retained as the prefix of the new, plus at least one new entry appended - and biases to "not gained" (a loud warning) for anything else, including archiving every entry. Known Issues updated to match.
- 2026-07-07 - Adversarial-review fixes (two further silent misses found by probing). (1) `RH_ENTRY_RE` is now anchored flush-left, so an indented dated sub-bullet under an entry is no longer counted as a Revision History entry. (2) `_is_archive_plus_append` now also requires each appended entry to be dated no earlier than the newest retained entry (new `_entry_date` helper), so an old entry edited and moved to the bottom with a bogus archive line no longer reads as a gain. (3) In `run.py`, `--staged` mode now reads the new CONTEXT.md from the staged blob (`git show :path`) rather than the working tree, so a partially-staged CONTEXT.md is judged as it will be committed. Known Issues updated.
- 2026-07-07 - Codex review fixes in `run.py`: deleted tracked `CONTEXT.md` files are removed from the active owner set and internal deletes under those removed documented directories are not reassigned upward, so deleting a child directory only asks the parent to update Contents; git discovery failures now emit a doc-sync WARN (`scan skipped - ...`) rather than INFO so consumers surface the unrun scan.
