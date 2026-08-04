# Memory

**Last modified:** 2026-08-04

## Purpose

Project-scoped persistent memory for Book Dragon. All memory lives here rather than in the per-user Claude cache so it syncs with the project and is available on any machine that opens this folder.

## Contents

- `CONTEXT.md` — this file. Structural documentation; tracked in git.
- `MEMORY.md` — the index. Loaded at every session start. One line per memory, under 150 chars each. Not tracked in git; created on first-run.
- `LOG.md` — append-only journal of all memory changes. Not tracked in git; created on first-run.
- `backlog-backups/` - `memory/backlog-backups/` [[memory/backlog-backups/CONTEXT]] - Local exact-copy snapshots of `memory/backlog.md`, maintained by backlog-guard. Not tracked in git.
- Individual memory files, named `<type>_<short-name>.md`. Types: `feedback`, `reference`, `user`, `project`. Not tracked in git.

## Inputs

None. Memory files are written by Biblio during sessions as relevant facts emerge from conversation.

## Outputs

None. This directory is a store, not a workflow.

## Steps

N/A. This is a store directory, not a workflow. See the "Project memory" rule in `AGENTS.md` [[AGENTS]] for the full read procedure, write rules, and four-step write procedure.

## Dependencies

- `AGENTS.md` [[AGENTS]] (root) — defines the session startup sequence and all rules governing when and how to read and write memory.

## Known Issues

- The old per-user Claude cache at `~/.claude/projects/<sanitized-cwd>/memory/` may still exist on machines where it was previously written to. New writes must land in this directory. If a conflict arises, this project-scoped memory takes precedence.

## Revision History

- 2026-06-05 — Initial creation. Project-scoped memory system established per user specification.
- 2026-06-05 — Stripped to standard CONTEXT.md schema. Procedural content consolidated into project memory rule. Privacy and git rules added.
- 2026-06-09 — Dependencies and references updated from CLAUDE.md to AGENTS.md. Removed stale Known Issue about AI-agnostic transition (now complete).
- 2026-08-04 - Recorded two journal-scan declines in feedback_journal_scan_ask_once.md so short-lived Hurst camp timing and Charlotte's confirmed blood clot remain journal-only unless they become durable context.
- 2026-08-04 - Corrected stale suite counts carried as current status in backlog.md and the MEMORY.md index line. The 2026-08-04 backlog update said the audit suite was "unchanged at 81", which was already wrong when written (the same session took it to 89), and the index line repeated 81. Both now carry the real counts, and the backlog gained a third 2026-08-04 update recording the review round that found it. These two files are read at session startup, so a stale number here is read as current truth.
- 2026-08-04 - Recorded two open defects in guard_coverage_review_ledger.md and pointed backlog.md at them: `link-check --link` rewrites dated Revision History entries (reproducible; stage 4 must fix it before widening link-check onto `.codex/`), and `encoding-guard --check` never reports CRLF, so the LF half of the AGENTS.md encoding rule is unenforced at check time. Both were kept out of the backlog as separate items because both fall inside the guard-coverage plan's own scope, and both are flagged in the plan against the stages that will hit them.
- 2026-08-04 - Reconstructed backlog.md after data loss removed most Active items and the Build Only When Needed section. Restored missing items from memory logs, completed archive evidence, MEMORY.md, and session-search transcripts; pruned the oversized guard-coverage entry back to backlog scale; restored Build Only When Needed; added a completed archive entry for the journal declined-list fix; and updated MEMORY.md with the restored counts.
- 2026-08-04 - Added the Shadow Repository Feasibility Spike to backlog.md ahead of the Personal AI OS plan review and refreshed the MEMORY.md backlog index line to 20 Active items.
- 2026-08-04 - Added backlog-backups/ as the local snapshot store used by the new backlog-guard workflow, and documented it in Contents.
- 2026-08-04 - Added `project_newline_test_sites.md`, the first memory written to act as a standing session-startup reminder rather than as a fact to recall on topic. It holds a set of outstanding text-write fixes and the agreed sequence around them, at the user's instruction that they are deliberately not a backlog item. Its MEMORY.md index line is marked as a startup reminder so it is surfaced in the greeting every session until the user confirms the work is done, at which point the file is deleted. Also extended `feedback_agents_line_threshold.md` with the reporting half of that rule: flag the line count in one line without restating the rationale.
