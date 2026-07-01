# Rule Hooks - Archived

**Last modified:** 2026-07-01

## Purpose
Holds the plan document that drove construction of the rule-hooks workflow. This is a personal, design-time working artifact: it captures session- and setup-specific context, so it is gitignored and local-only (see the design-time-document rule in `AGENTS.md` [[AGENTS]]). The durable, generic design decisions live in the tracked `CONTEXT.md` files, not here.

## Contents
Plan documents, named `*-PLAN.md`. Individual files are not listed here - they are personal, gitignored, local-only content. The filesystem is the authoritative source of what exists in this directory.

## Inputs
None. This is a local reference archive, not a runnable workflow.

## Outputs
None.

## Steps
N/A. This is an archive container, not a workflow.

## Dependencies
- `workflows/rule-hooks/` [[workflows/rule-hooks/CONTEXT]] - The workflow this document was written to build.

## Known Issues
- The archived plan describes the workflow as it was being designed; it contains planning-phase language by nature and is not maintained. Treat the tracked `workflows/rule-hooks/CONTEXT.md` [[workflows/rule-hooks/CONTEXT]] as the current truth; the document here is historical and local-only.

## Revision History
- 2026-07-01 - Created during rule-hooks Phase 1 close-out. Archived HOOKS-PLAN.md from the project root after distilling the durable design into the tracked CONTEXT.md files and committing the build (c4b690f58).
