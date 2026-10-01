# Review Orchestration - Prompts

**Last modified:** 2026-10-01

## Purpose
Holds the text the review-orchestration workflow gives to people and to the AIs it runs: the run brief template, and the prompts for the builder's first pass, its fix passes and the reviewer.

## Contents
- brief.md - `workflows/review-orchestration/prompts/brief.md` [[workflows/review-orchestration/prompts/brief]] - The run brief template: a usage comment, then the seven required headings in order, each followed only by an HTML comment saying what goes there. The checker refuses it as shipped, six sections empty, which is the proof that an unfilled brief cannot start a run; `tests/test_brief.py` asserts exactly that refusal.
- builder.md - `workflows/review-orchestration/prompts/builder.md` - The builder's first-pass prompt: the run's rules (edit paths, maintenance obligations, no git, the verification commands) and the brief. Fields `$edit_paths`, `$verify_commands`, `$brief`.
- fix.md - `workflows/review-orchestration/prompts/fix.md` - The fix-pass prompt: the open findings, the three actions and the fenced json action list the loop parses. Fields `$round`, `$findings`.
- reviewer.md - `workflows/review-orchestration/prompts/reviewer.md` - The reviewer's prompt: the triage rule, what to check, the brief, the mechanical results, the prior findings with the builder's actions, the changed files (each to be listed in `files_reviewed`), the exact lines the orchestrator's own checks wrote, and the diff, or where to read it when it is too large. Fields `$round`, `$brief`, `$mechanical`, `$prior`, `$changed_files`, `$check_written`, `$diff`.

## Inputs
None. The template is copied by hand and filled in with the user; the prompts are filled in by `workflows/review-orchestration/scripts/loop.py` [[workflows/review-orchestration/scripts/CONTEXT]].

## Outputs
None. A filled brief lives with its run, never here, and so does every filled prompt, in the run's transcripts.

## Steps
1. Copy `brief.md` to the run's brief path; do not edit it in place.
2. Fill in every section, then check it with `python workflows/review-orchestration/scripts/run.py --check-brief <path>`.
3. Append LOG.md with a completion or failure entry when the template or a prompt is changed.

## Dependencies
- `workflows/review-orchestration/scripts/brief.py` [[workflows/review-orchestration/scripts/CONTEXT]] - the checker whose rules the template's comments describe.
- `workflows/review-orchestration/scripts/loop.py` [[workflows/review-orchestration/scripts/CONTEXT]] - fills the prompts with `string.Template`, so a field is written `$name` and a literal dollar sign `$$`; a field missing from a prompt, or one the loop does not supply, fails loudly.
- `workflows/review-orchestration/scripts/stopreasons.py` [[workflows/review-orchestration/scripts/CONTEXT]] - the action names and the fenced json block `fix.md` asks for, and the reviewer schema `reviewer.md` describes.
- `workflows/review-orchestration/tests/test_brief.py` and `test_loop.py` [[workflows/review-orchestration/tests/CONTEXT]] - assert the shipped template is refused only for its six empty sections, and that every prompt renders with its fields.

## Known Issues
- The comments restate the checker's rules in brief. A rule changed in `brief.py` and not here leaves the template giving wrong guidance; the test catches a heading change but not a wording drift in a comment.
- The prompts restate rules held elsewhere: the action names and json block (`stopreasons.py`), the Revision History cap (`AGENTS.md` and the audit), and the reviewer's fields (the schema). A change there and not here leaves a prompt asking for the wrong thing, and the loop then ends the run `error` on the reply rather than accepting it.

## Revision History
- 2026-09-24 - Initial creation with the run brief template.
- 2026-09-25 - The Acceptance checks comment now also rules out a launcher such as `env` or `sudo` in front of the program, matching the checker after code review round R2.
- 2026-09-29 - Stage A2, chunk (c): added `builder.md`, `fix.md` and `reviewer.md`, the prompts the build-and-review loop fills in.
- 2026-09-30 - `reviewer.md` gained the changed-file list the reviewer must cover in `files_reviewed`, and the files the orchestrator's own checks changed (chunk (c) code review R4-1 and R4-2).
- 2026-10-01 - `reviewer.md` now gives the exact lines the orchestrator's checks wrote, not whole files, and says any other change in those files is the builder's (R5-2).
