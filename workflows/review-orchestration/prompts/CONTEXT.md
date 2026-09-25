# Review Orchestration - Prompts

**Last modified:** 2026-09-25

## Purpose
Holds the text the review-orchestration workflow gives to people and, in later sub-stages, to the AIs it runs. For now that is only the run brief template.

## Contents
- brief.md - `workflows/review-orchestration/prompts/brief.md` [[workflows/review-orchestration/prompts/brief]] - The run brief template: a usage comment, then the seven required headings in order, each followed only by an HTML comment saying what goes there. The checker refuses it as shipped, six sections empty, which is the proof that an unfilled brief cannot start a run; `tests/test_brief.py` asserts exactly that refusal.

## Inputs
None. The template is copied by hand and filled in with the user.

## Outputs
None. A filled copy lives with its run, never here.

## Steps
1. Copy `brief.md` to the run's brief path; do not edit it in place.
2. Fill in every section, then check it with `python workflows/review-orchestration/scripts/run.py --check-brief <path>`.
3. Append LOG.md with a completion or failure entry when the template itself is changed.

## Dependencies
- `workflows/review-orchestration/scripts/brief.py` [[workflows/review-orchestration/scripts/CONTEXT]] - the checker whose rules the template's comments describe.
- `workflows/review-orchestration/tests/test_brief.py` [[workflows/review-orchestration/tests/CONTEXT]] - asserts the shipped template is refused only for its six empty sections.

## Known Issues
- The comments restate the checker's rules in brief. A rule changed in `brief.py` and not here leaves the template giving wrong guidance; the test catches a heading change but not a wording drift in a comment.

## Revision History
- 2026-09-24 - Initial creation with the run brief template.
- 2026-09-25 - The Acceptance checks comment now also rules out a launcher such as `env` or `sudo` in front of the program, matching the checker after code review round R2.
