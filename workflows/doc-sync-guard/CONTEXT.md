# Doc-Sync Guard

**Last modified:** 2026-07-07

## Purpose
Catches CONTEXT.md / LOG.md drift the moment a directory's real content changes but its own documentation is not updated in the same change. It is a deterministic, read-only checker in the guard family (same shape as `encoding-guard`, `personal-data-guard`, and `ai-style-guard` [[workflows/ai-style-guard/CONTEXT]]): a standalone CLI that the full audit and the close-out verifier consume. For every committable directory whose content changed (a file added, removed, or edited that is not the directory's own CONTEXT.md/LOG.md), it verifies that the directory's `CONTEXT.md` moved (a new Revision History entry, with `**Last modified:**` matching the newest entry date) and that its gitignored `LOG.md` gained an entry (verified by mtime, since a gitignored file cannot be content-diffed). It also covers the one mechanical parent-propagation case: a child directory added or removed without the parent CONTEXT.md moving. This makes the AGENTS.md "Work maintenance and close-out" rule reliable rather than discipline-dependent. There is deliberately no fix mode: the AI that made the change knows why each directory changed and writes proper entries from that knowledge, where a context-free script could only produce filler.

## Contents
- scripts/ - `workflows/doc-sync-guard/scripts/` [[workflows/doc-sync-guard/scripts/CONTEXT]] - The `run.py` entry point (read-only `--check`, with `--json`, `--since`, `--base`, `--staged`, `--strict`) plus the pure `context_parse` and `logtime` helper modules.
- tests/ - `workflows/doc-sync-guard/tests/` [[workflows/doc-sync-guard/tests/CONTEXT]] - Unit tests for the pure parsers and ownership logic, plus end-to-end integration tests against throwaway git repositories.

## Inputs
- The current change set, discovered via git: `git diff <base>` (base HEAD by default) plus untracked committable files, or `git diff --cached` in `--staged` mode. Git must be available; without it the guard reports a single WARN finding and scans nothing.
- On-disk `CONTEXT.md` and `LOG.md` files of the changed directories, read to check final-state dates and the newest LOG entry timestamp.

## Outputs
- A findings report on stdout (human text, or `--json` for the audit hook). All findings are WARN under a `doc-sync` label.
- Exit code 0 by default (advisory). With `--strict`, exit 1 when any WARN exists, so a caller can gate on it.
- No files are written and no LOG.md entry is made on a check run (read-only, like the other content guards).

## Steps
1. Scan the changed directories for CONTEXT.md / LOG.md drift (read-only):
   `python workflows/doc-sync-guard/scripts/run.py --check [--json] [--since REF | --base BRANCH | --staged] [--strict]`
2. Review any WARN findings and update the flagged `CONTEXT.md` / `LOG.md` files (the guard reports directory and reason; it never edits).
3. Re-run until the report is clean.
4. Append LOG.md only when run as a deliberate, logged workflow step (the read-only check and the audit-hook invocation do not log).

## Dependencies
- `AGENTS.md` [[AGENTS]] (root) - Defines the "Work maintenance and close-out" CONTEXT/LOG obligations this guard enforces mechanically, the CONTEXT.md schema (Revision History, Last modified), and the LOG.md rules.
- `templates/` [[templates/CONTEXT]] - The CONTEXT.md and LOG.md schema this guard checks against.
- The `git` CLI for change discovery; otherwise the Python 3.9+ standard library only (no third-party packages, so no `.venv` bootstrap is needed).
- Consumers (all live): `workflows/audit/` [[workflows/audit/CONTEXT]] runs it as an advisory hook, merging its findings as WARN under a `doc-sync` label without changing the audit's own exit code; `workflows/close-out/` [[workflows/close-out/CONTEXT]] turns any `doc-sync` WARN into a hard fail (the deterministic gate); `workflows/rule-hooks/` [[workflows/rule-hooks/CONTEXT]] runs it as a warn-not-block step in the universal git pre-commit hook; `workflows/handoff/` [[workflows/handoff/CONTEXT]] surfaces a doc-sync section in the gather packet; and `workflows/triggers/` [[workflows/triggers/CONTEXT]] exposes the on-demand "check context is current" phrase.

## Known Issues
- LOG.md freshness is verified by mtime because LOG.md is gitignored and cannot be content-diffed. This is reliable in the build-once-at-the-end flow (the whole body of work stays uncommitted with real mtimes). A deliberate mid-task git operation that rewrites an in-scope file's mtime while leaving LOG.md untouched (a mid-task commit then checkout, `git stash`/`pop`, or `git checkout -- <file>`) can raise a loud false positive - never a silent miss, with the commit-time gate as backstop.
- The check enforces the bookkeeping obligation (the required entries exist), not the correctness of the prose. A Revision History entry with wrong content still passes.
- Subjective parent-directory propagation (whether a change is significant enough for a parent Revision History entry or prose rewrite) stays a human/AI close-out judgement; only the mechanical child add/remove case is enforced.
- Discovery depends on git. Outside a git checkout the guard scans nothing and emits a single WARN finding so audit, close-out, handoff, and pre-commit do not mistake an unrun scan for a clean scan.
- The guard only checks the current working tree against HEAD, so drift is caught per-commit at the pre-commit hook and again at close-out; a commit made with `--no-verify` (or on a machine with no hook installed) bypasses the gate and is not re-checked afterward - commit through the hook. A whole-branch audit is available on demand with `--base main`, accepting that its mtime-based LOG half is noisy against already-committed files.

## Revision History
- 2026-07-06 - Initial creation (build part 1): the standalone read-only checker. `scripts/run.py` with the git-diff scope collector, committable-CONTEXT ownership resolution, the CONTEXT check (Revision History entry added + Last modified matches newest entry), the mtime-based LOG check (`T = max(CONTEXT mtime, newest changed-real-file mtime)`), and the coarse child add/remove parent-propagation case; pure `context_parse` and `logtime` helpers; 38 unit and integration tests. Audit / close-out / pre-commit / handoff wiring lands in later parts.
- 2026-07-07 - Codex review fixes and doc sync. Dependencies rewritten to describe the now-live consumers (audit advisory hook, close-out hard-fail, rule-hooks pre-commit warn, handoff packet section, triggers phrase) instead of "planned". Guard corrected for rename/move handling (source directory no longer skipped) and section-aware Revision History detection (a dated bullet outside the section no longer counts); see the scripts and tests CONTEXT for detail.
- 2026-07-07 - Second-review behaviour fix: `gained_rh_entry` now compares full Revision History entry lines rather than dates, so editing an existing entry's date forward is no longer counted as a new entry (previously a silent miss). See the scripts CONTEXT for detail.
- 2026-07-07 - Third-review behaviour fix: tightened the `gained_rh_entry` archive branch so a changed "Earlier history archived" reference line alone no longer counts as a gain - an in-place edit dressed up with a bogus archive line was a silent miss. It now requires a genuine archive-plus-append shape and biases to a loud warning otherwise. See the scripts CONTEXT for detail.
- 2026-07-07 - Adversarial-review fixes: closed two more silent misses and one staged-mode bug found by probing. Revision History entry detection is now anchored flush-left (an indented dated sub-bullet is no longer counted); the archive-plus-append check now also requires appended entries to be dated no earlier than the newest retained entry (an old entry edited and moved down no longer counts); and `--staged` mode now reads the staged CONTEXT.md blob rather than the working tree, so a partially-staged file is judged as it will be committed. See the scripts CONTEXT for detail.
- 2026-07-07 - Documented the working-tree-scope trade-off in Known Issues: a `--no-verify` commit bypasses the doc-sync gate and is not re-checked afterward (backstop deliberately not built), with `--base main` available for a manual whole-branch audit.
- 2026-07-07 - Codex review fixes for the maintenance-reliability review: deleting a documented child directory no longer creates an impossible own-directory warning for the deleted child, and git scan failures now report as doc-sync WARN findings so consumers cannot treat an unrun scan as clean. Test coverage increased from 55 to 58.
