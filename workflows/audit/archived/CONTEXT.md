# Audit - Archived

**Last modified:** 2026-06-26

## Purpose
Holds the plan, handover, and proposal documents that drove construction or
optimisation work on the audit workflow. These are personal, design-time working
artifacts: they capture session- and setup-specific context, so they are
gitignored and local-only (see the design-time-document rule in `AGENTS.md` [[AGENTS]]).
The durable, generic design decisions live in the tracked `CONTEXT.md` files, not here.

## Contents
Plan, handover, and proposal documents, named `*-PLAN.md` (and similar).
Individual files are not listed here; they are personal, gitignored, local-only
content. The filesystem is the authoritative source of what exists in this
directory.

## Inputs
None. This is a local reference archive, not a runnable workflow.

## Outputs
None.

## Steps
N/A. This is an archive container, not a workflow.

## Dependencies
- `workflows/audit/` [[workflows/audit/CONTEXT]] - The workflow these documents were written to build or optimise.

## Known Issues
- These documents describe the workflow as it was being built or changed; they
  contain construction-phase language by nature and are not maintained. Treat the
  tracked `workflows/audit/CONTEXT.md` [[workflows/audit/CONTEXT]] as the current
  truth; the documents here are historical and local-only.

## Revision History
- 2026-06-26 - Created during the collect_dirs speed-optimisation close-out. Archived AUDIT-SPEED-PLAN.md from the project root after the work completed.
