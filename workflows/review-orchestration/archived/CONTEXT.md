# Review Orchestration - Archived

**Last modified:** 2026-10-13

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
