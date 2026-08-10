# Doc-Sync Guard - Archived

**Last modified:** 2026-08-10

## Purpose
Holds the completed plan documents behind the doc-sync-guard workflow: the design document that drove its original construction, and the tactical specs for guard-coverage stages that later extended it. These are personal, design-time working artifacts: they capture session- and setup-specific context, so they are gitignored and local-only (see the design-time-document rule in `AGENTS.md` [[AGENTS]]). The durable, generic design decisions live in the tracked `CONTEXT.md` files, not here. A document arrives here when the work it specifies is finished, not when it stops being read, so a plan whose stages are still outstanding stays where it is.

## Contents
Plan documents, named `*-PLAN.md`. Individual files are not listed here - they are personal, gitignored, local-only content. The filesystem is the authoritative source of what exists in this directory.

## Inputs
None. This is a local reference archive, not a runnable workflow.

## Outputs
None.

## Steps
N/A. This is an archive container, not a workflow.

## Dependencies
- `workflows/doc-sync-guard/` [[workflows/doc-sync-guard/CONTEXT]] - The workflow these documents were written to build or extend. One drove the original build; the others are tactical specs for guard-coverage stages that added to it.

## Known Issues
- The archived plans describe the workflow as it was being designed or extended; they contain planning-phase language by nature and are not maintained. Treat the tracked `workflows/doc-sync-guard/CONTEXT.md` [[workflows/doc-sync-guard/CONTEXT]] as the current truth; the documents here are historical and local-only.
- Archived documents are deliberately never edited to read as current. An archived build spec is a record of what was built, so rewriting it to match today destroys the only thing it is good for. Stale references and completed-tense planning language inside them are expected, not findings.

## Revision History
- 2026-07-10 - Created during the Task 3 hygiene sweep. Archived DOC-SYNC-GUARD-PLAN.md from the project root after the doc-sync-guard build was completed, committed (84c91b5e9), and pushed, per the "archive plans on completion" rule.
- 2026-08-10 - Archived STAGE-3A-PLAN.md and STAGE-3A-PLAN-ROUND41-HISTORY-PLAN.md from the project root, after guard-coverage stage 3a was built, committed (f4ec3b63f), and pushed, and after the four-step pre-archive check confirmed nothing load-bearing in either document existed only there. Purpose, Dependencies and Known Issues were rewritten: all three were written in the singular about one document and this directory now holds three. Known Issues also gained the rule that archived documents are not edited to read as current. Contents needed no change: it already describes the naming convention rather than listing files, which is correct for gitignored personal content. The stage 3a Codex baseline was deleted rather than archived, per the standing convention that a review baseline has no value once its subject is finished.
