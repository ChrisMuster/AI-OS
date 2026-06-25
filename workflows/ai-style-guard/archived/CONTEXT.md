# AI-Style Guard — Archived

**Last modified:** 2026-06-25

## Purpose
Holds the plan and handover document that drove construction of the ai-style-guard workflow. This is a personal, design-time working artifact: it captures session- and setup-specific context, so it is gitignored and local-only (see the design-time-document rule in `AGENTS.md` [[AGENTS]]). The durable, generic design decisions live in the tracked `CONTEXT.md` files, not here.

## Contents
Plan and handover documents, named `*-PLAN.md` (and similar). Individual files are not listed here - they are personal, gitignored, local-only content. The filesystem is the authoritative source of what exists in this directory.

## Inputs
None. This is a local reference archive, not a runnable workflow.

## Outputs
None.

## Steps
N/A. This is an archive container, not a workflow.

## Dependencies
- `workflows/ai-style-guard/` [[workflows/ai-style-guard/CONTEXT]] - The workflow this document was written to build.

## Known Issues
- This document describes the workflow as it was being built; it contains construction-phase language by nature and is not maintained. Treat the tracked `workflows/ai-style-guard/CONTEXT.md` [[workflows/ai-style-guard/CONTEXT]] as the current truth; the document here is historical and local-only.

## Revision History
- 2026-06-25 - Created during ai-style-guard close-out. Archived the build plan and session handover from the project root after distilling the durable design into the tracked CONTEXT.md files.
