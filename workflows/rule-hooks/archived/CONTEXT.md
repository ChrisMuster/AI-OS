# Rule Hooks - Archived

**Last modified:** 2026-09-23

## Purpose
Holds the finished design-time documents of the rule-hooks workflow: the plan that drove its construction, and the review briefs that commissioned work on it once it existed. These are personal working artifacts that capture session- and setup-specific context, so they are gitignored and local-only (see the design-time-document rule in `AGENTS.md` [[AGENTS]]). The durable, generic design decisions live in the tracked `CONTEXT.md` files, not here.

## Contents
Design-time documents, named `*-PLAN.md` and `*-BRIEF.md`. Individual files are not listed here - they are personal, gitignored, local-only content. The filesystem is the authoritative source of what exists in this directory.

## Inputs
None. This is a local reference archive, not a runnable workflow.

## Outputs
None.

## Steps
N/A. This is an archive container, not a workflow.

## Dependencies
- `workflows/rule-hooks/` [[workflows/rule-hooks/CONTEXT]] - The workflow this document was written to build.

## Known Issues
- The archived documents describe the workflow as it was being designed or reviewed; they contain planning-phase language by nature and are not maintained. Treat the tracked `workflows/rule-hooks/CONTEXT.md` [[workflows/rule-hooks/CONTEXT]] as the current truth; the documents here are historical and local-only.
- **A document only stops misleading people once it says so on its own face.** An archived brief still reads as live instructions to anyone who opens it directly, and archiving moves a file without changing a word inside it. Each document here carries a dated SUPERSEDED note at the top naming what replaced it. Add one when archiving, not later.

## Revision History
- 2026-07-01 - Created during rule-hooks Phase 1 close-out. Archived HOOKS-PLAN.md from the project root after distilling the durable design into the tracked CONTEXT.md files and committing the build (c4b690f58).
- 2026-09-07 - Archived CODEX-REVIEW-BRIEF.md here from the project root, at the user's decision, after the review it commissioned completed and its findings were repaired. Recorded as a review finding rather than as tidying: the brief had sat at the root for two days looking current, and the review that read it named that as a way a later reader could be misled. Purpose and Contents widened from plan documents to design-time documents generally, since a brief is not a plan and the directory now holds both. The document gained a dated SUPERSEDED note at the top pointing at the live review packet, and Known Issues records that note as the rule rather than as a one-off, because moving a file changes nothing about what the file says.
- 2026-09-13 - Archived the root `CODEX-REVIEW-BRIEF.md` here as `CODEX-REVIEW-ROUNDS-5-TO-8-BRIEF.md`, before a new brief was written at the same root path for the next external review. It was renamed rather than moved under its own name because the brief archived on 2026-09-07 already holds that name, and an archive must never overwrite. The commission it described became round 9 on 2026-09-11, with rounds 10 to 12 run since, so it carries a dated SUPERSEDED note naming the live brief and the review packet, per the rule in Known Issues.
- 2026-09-23 - Archived the three briefs of the pre-push review series together, at the user's instruction and as part of the same close-out that moved their backlog items to the completed archive, which is the moment the archiving rule keys to rather than the moment they stopped being read. The root `CODEX-REVIEW-BRIEF.md` came in as `CODEX-REVIEW-PRE-PUSH-BRIEF.md` for the same reason the 2026-09-13 one was renamed: two earlier briefs already hold names it would collide with, and an archive must never overwrite. `CLAUDE-REPAIR-BRIEF.md` and `CLAUDE-CLOSEOUT-BRIEF.md` had no collision and came in under their own names. All three were given dated SUPERSEDED notes before the move rather than after, per the rule in Known Issues, and the repair brief's note also repoints its "the current document is" line, which named a successor that was being archived in the same pass and would otherwise have sent a reader to a path with nothing at it. Two briefs were deliberately left at the project root, `CODEX-CODE-REVIEW-BRIEF.md` and `CODEX-RETEST-BRIEF.md`: they belong to the sync architecture item, which is still Active with open findings, and the entry condition for this directory is that the work a document specifies is finished or abandoned.
