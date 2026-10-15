# Review Orchestration - Archived

**Last modified:** 2026-10-15

## Purpose
Holds the design-time documents of finished review-orchestration work: plans, their review briefs and the run briefs written for proof runs, archived once the work they specify is done. These are personal, design-time working artifacts that capture session- and setup-specific context, so they are gitignored and local-only (see the design-time-document rule in `AGENTS.md` [[AGENTS]]). The durable, generic decisions live in the tracked `CONTEXT.md` files of the workflow, not here. The live plan for the workflow's remaining stages stays at the project root until its own work is done.

## Contents
Plan documents and briefs, named `*-PLAN.md` and `*-BRIEF.md`. Individual files are not listed here: they are personal, gitignored, local-only content. The filesystem is the authoritative source of what exists in this directory.

## Inputs
None. This is a local reference archive, not a runnable workflow.

## Outputs
None.

## Steps
N/A. This is an archive container, not a workflow.

## Dependencies
- `workflows/review-orchestration/` [[workflows/review-orchestration/CONTEXT]] - The workflow these documents were written to build or prove.

## Known Issues
- An archived document describes the work as it was being designed and run; it contains planning-phase language by nature and is not maintained. Treat the tracked `workflows/review-orchestration/CONTEXT.md` [[workflows/review-orchestration/CONTEXT]] and its subfolders' `CONTEXT.md` files as the current truth.

## Revision History
- 2026-10-13 - Initial creation, when the orchestrator isolation item closed after its proof run: its plan, the plan's review brief and the proof run's brief were archived here, and the plan's review baseline, identical to the plan, was deleted, per the "archive plans on completion" rule.
- 2026-10-14 - Stage A2's spent documents archived here, A2 being complete: the chunk (d) tactical plan, the Stage B3 run brief, five finished review briefs (the Stage A plan round and four chunk (d) rounds), both chunk (c) proof-run briefs, and the brief of the run-record change built before A3. The tactical plan's review baseline was deleted, differing from the plan only in the plan's later status line.
- 2026-10-15 - The pre-A3 plan review brief archived here, the A3 plan review having closed clean at round R25. The live plan and its review baseline stay at the project root until the workflow's remaining stages are built.
