# Archived

**Last modified:** 2026-08-13

## Purpose
Holds finished project-level design-time documents that have no owning workflow. Book Dragon's convention is that a spent plan is archived into the `archived/` directory of the workflow it was written to build, but some plans govern the project as a whole rather than any one workflow, and before this directory existed they had nowhere to go. This is that destination, and only that: a plan belonging to a workflow still goes to that workflow's own `archived/` directory.

These are personal, design-time working artifacts. They capture session-, machine- and user-specific context, so they are gitignored and local-only (see the design-time-document rule in `AGENTS.md` [[AGENTS]]). The durable, generic design decisions live in the tracked `CONTEXT.md` files and `README.md`, not here.

**A document arrives here only when the work it specifies is finished or abandoned, never when it merely stops being read.** A live plan, a history archive of a plan still being built from, and a review baseline whose subject is still under review all stay at the project root until their item is done. A superseded plan is finished for this purpose: the work it specified will never be carried out.

## Contents
Design-time documents, named `*-PLAN.md`. Individual files are not listed here - they are personal, gitignored, local-only content. The filesystem is the authoritative source of what exists in this directory.

## Inputs
None. This is a local reference archive, not a runnable workflow.

## Outputs
None.

## Steps
N/A. This is an archive container, not a workflow.

## Dependencies
- `AGENTS.md` [[AGENTS]] - defines the Archiving procedure this directory implements, and the design-time-document rule that makes its contents gitignored and unlisted.

## Known Issues
- The archived documents describe the project as it was being designed; they contain planning-phase language by nature and are not maintained. Treat the tracked `CONTEXT.md` files and `README.md` as the current truth.
- Archived documents are deliberately never edited to read as current. An archived plan is a record of what was intended, so rewriting it to match today destroys the only thing it is good for. Stale references and superseded instructions inside them are expected, not findings. Where a document could be mistaken for live guidance, it carries a banner at the top saying so; the banner is added on arrival and not revisited afterwards.
- The boundary between this directory and a workflow's own `archived/` is a judgement call at the margin: a plan that mostly builds one workflow belongs to that workflow even if it touches others. When it is genuinely unclear, ask rather than guess.

## Revision History
- 2026-08-13 - Created to give project-level plans an archive destination. The gap it closes was found when a superseded whole-project plan had no owning workflow to be archived into and was renamed in place at the project root instead, which is not an archive. The first document archived here is that plan, superseded by a replacement architecture and never started.
