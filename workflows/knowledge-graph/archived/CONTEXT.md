# Knowledge Graph — Archived

**Last modified:** 2026-06-24

## Purpose
Holds the plan, handover, and proposal documents that drove construction of the
knowledge-graph workflow (the per-phase build plans and the deferred-follow-up
handovers). These are personal, design-time working artifacts: they capture
session- and setup-specific context, so they are gitignored and local-only (see
the design-time-document rule in `AGENTS.md` [[AGENTS]]). The durable, generic
design decisions live in the tracked `CONTEXT.md` files, not here.

## Contents
Plan, handover, and proposal documents, named `*-PLAN.md` (and similar). Individual
files are not listed here — they are personal, gitignored, local-only content. The
filesystem is the authoritative source of what exists in this directory.

## Inputs
None. This is a local reference archive, not a runnable workflow.

## Outputs
None.

## Steps
N/A. This is an archive container, not a workflow.

## Dependencies
- `workflows/knowledge-graph/` [[workflows/knowledge-graph/CONTEXT]] — The workflow these documents were written to build.

## Known Issues
- These documents describe the workflow as it was being built; they contain
  construction-phase language by nature and are not maintained. Treat the tracked
  `workflows/knowledge-graph/CONTEXT.md` [[workflows/knowledge-graph/CONTEXT]] as
  the current truth; the documents here are historical and local-only.

## Revision History
- 2026-06-21 — Created during Phase 4 close-out. Archived the Phase 2–4 plans from the workflow root after distilling the durable roadmap.
- 2026-06-22 — Phase 5 close-out: archived the memory-layer plan.
- 2026-06-23 — Phase 6 close-out: archived the wiki sub-graph-layer plan.
- 2026-06-23 — Phase 7 close-out: archived the journal-layer plan.
- 2026-06-23 — Phase 8 close-out: archived the conversation-layer plan. The four-layer content roadmap is complete.
- 2026-06-23 — Audit-hook follow-up close-out: archived the audit-hook plan.
- 2026-06-24 — Session-search follow-up close-out: archived the session-search cross-reference plan.
- 2026-06-24 — Rebuild speed optimisation close-out: archived the rebuild-speed plan.
- 2026-06-24 - Reclassified plan/handover/roadmap documents as personal (gitignored, local-only); rewrote Contents to a convention note (individual plans no longer listed) and removed the ROADMAP references, per the new design-time-document rule in AGENTS.md.
