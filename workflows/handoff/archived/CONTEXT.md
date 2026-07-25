# Handoff - Archived

**Last modified:** 2026-07-10

## Purpose
Holds the plan document that drove construction of the handoff workflow. This is a personal, design-time working artifact: it captures session- and setup-specific context, so it is gitignored and local-only (see the design-time-document rule in `AGENTS.md` [[AGENTS]]). The durable, generic design decisions live in the tracked `CONTEXT.md` files, not here. This is the build plan, distinct from the rolling `HANDOVER.md` session-handoff document (which lives at the project root and is never archived).

## Contents
Plan documents, named `*-PLAN.md`. Individual files are not listed here - they are personal, gitignored, local-only content. The filesystem is the authoritative source of what exists in this directory.

## Inputs
None. This is a local reference archive, not a runnable workflow.

## Outputs
None.

## Steps
N/A. This is an archive container, not a workflow.

## Dependencies
- `workflows/handoff/` [[workflows/handoff/CONTEXT]] - The workflow this document was written to build.

## Known Issues
- The archived plan describes the workflow as it was being designed; it contains planning-phase language by nature and is not maintained. Treat the tracked `workflows/handoff/CONTEXT.md` [[workflows/handoff/CONTEXT]] as the current truth; the document here is historical and local-only.

## Revision History
- 2026-07-10 - Created during the Task 3 hygiene sweep. Archived HANDOFF-PLAN.md from `workflows/handoff/` [[workflows/handoff/CONTEXT]] after the handoff build was completed, committed (efe2a4c1e), and pushed, per the "archive plans on completion" rule.
