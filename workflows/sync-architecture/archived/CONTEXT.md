# Sync Architecture - Archived

**Last modified:** 2026-09-04

## Purpose
Holds the completed design-time documents behind the sync architecture work: superseded plan states kept as a record of what the live documents used to say, rather than as instructions anyone builds from. These are personal, design-time working artifacts that capture session- and setup-specific context, so they are gitignored and local-only (see the design-time-document rule in `AGENTS.md` [[AGENTS]]). The durable, generic design decisions live in the tracked `CONTEXT.md` files; the rationale for the live plan lives in its decision log and review ledger, neither of which is here. A document arrives here when the work it specifies is finished or the state it records has been superseded, not when it stops being read.

## Contents
Plan documents named `*-PLAN.md`, and the briefs of closed review rounds named `*-BRIEF.md`. Individual files are not listed here - they are personal, gitignored, local-only content. The filesystem is the authoritative source of what exists in this directory.

## Inputs
None. This is a local reference archive, not a runnable workflow.

## Outputs
None.

## Steps
N/A. This is an archive container, not a workflow.

## Dependencies
- `workflows/sync-architecture/` [[workflows/sync-architecture/CONTEXT]] - The workflow whose design documents these are. The live architecture plan cites the pre-split history archive held here, so this directory is a reference target rather than a dead end.

## Known Issues
- The archived documents describe the architecture as it was being designed; they contain planning-phase language by nature and are not maintained. Treat the live `SYNC-ARCHITECTURE-PLAN.md` as the current truth.
- Archived documents are deliberately never edited to read as current. An archived plan state is a record of what a document used to say, so rewriting it to match today destroys the only thing it is good for. Stale references and superseded stage names inside them are expected, not findings.
- Nothing here is captured by the public repository, since these are gitignored. They are captured by the personal repository, which is what makes archiving them rather than deleting them worth doing: a document moved here gains history, where one left outside the project has none.

## Revision History
- 2026-09-01 - Created during the Stage A close-out, to receive `SYNC-ARCHITECTURE-PRESPLIT-HISTORY-PLAN.md` from a holding folder outside the project. Three sibling documents in that folder were deleted rather than archived, at the user's instruction: they were pre-cut rollback copies whose purpose was discharged once the blind build test proved the cut removed no instruction and Stage A was built and working. This one was kept and moved because it is a different kind of document that had been swept in with them. It is the pre-split history archive, the live plan cites it by name, and it records a state from before the personal repository existed, so it is the one document in that folder with no other copy anywhere. Moving it into the project rather than leaving it outside also brings it inside the personal repository's worktree, so it now has history for the first time.
- 2026-09-03 - Widened to receive the briefs of closed review rounds as well as superseded plan states, and took delivery of the first, the brief that drove the 2026-08-28 document review round. It is archived rather than deleted because its structure is reusable and because it records what that round was actually asked to do, which nothing else preserves. The Contents section now names both document kinds, since a reader meeting only `*-PLAN.md` there would read a brief in this directory as misfiled. The arrival is worth recording for how it happened as well as that it did: the brief was destroyed at its previous path by a write that assumed the path was free, and it was recovered from the personal repository's history, which is the case the third Known Issue above describes. That entry claimed archiving here gives a document a restore path; the claim was exercised rather than assumed on the day it was first needed.
- 2026-09-04 - Received the brief that drove the 2026-09-04 buildability round, archived before the live brief path was rewritten for the next round rather than afterwards. The ordering is the point and is why this entry exists: the previous arrival got here by recovery, after a write assumed a path was free and destroyed a 135-line document, and the lesson only counts if the copy happens before the overwrite rather than after it. The archived brief was given a superseded header naming where its round's findings now live and where the live brief is, so a reader meeting it cannot mistake a closed commission for a current one. It was copied rather than moved, since the live path is immediately reused. This directory now holds two closed briefs and one pre-split history archive, which is enough to make the pattern visible: a brief and the round it commissioned are read together, because a reviewer that missed something and a brief that never asked for it look identical from the findings alone.
