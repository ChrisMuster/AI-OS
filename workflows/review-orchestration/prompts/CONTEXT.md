# Review Orchestration - Prompts

**Last modified:** 2026-10-04

## Purpose
Holds the text the review-orchestration workflow gives to people and to the AIs it runs: the run brief template, the prompts for the builder's first pass, its fix passes and the reviewer, and the parts of those prompts that differ by which AI holds the role.

## Contents
- brief.md - `workflows/review-orchestration/prompts/brief.md` [[workflows/review-orchestration/prompts/brief]] - The run brief template: a usage comment, then the seven required headings in order, each followed only by an HTML comment saying what goes there. The checker refuses it as shipped, six sections empty, which is the proof that an unfilled brief cannot start a run; `tests/test_brief.py` asserts exactly that refusal.
- builder.md - `workflows/review-orchestration/prompts/builder.md` [[workflows/review-orchestration/prompts/builder]] - The builder's first-pass prompt: the run's rules (edit paths, maintenance obligations, the builder's tool rules) and the brief. Fields `$edit_paths`, `$tool_rules`, `$brief`.
- builder-tools-claude.md - `workflows/review-orchestration/prompts/builder-tools-claude.md` [[workflows/review-orchestration/prompts/builder-tools-claude]] - A Claude builder's tool rules: timestamps from the Biblio Tools tools, no git commands, and its verification commands. Field `$verify_commands`.
- builder-tools-codex.md - `workflows/review-orchestration/prompts/builder-tools-codex.md` [[workflows/review-orchestration/prompts/builder-tools-codex]] - A Codex builder's tool rules: read with the listed shell commands, and with `Get-Content` only in the one form that sets both encodings, shown literally with the reason, change files only with apply_patch, write a LOG.md entry with apply_patch and take its time from the listed command, run only its own verification list, and use no other tool. Fields `$read_commands`, `$verify_commands`.
- fix.md - `workflows/review-orchestration/prompts/fix.md` [[workflows/review-orchestration/prompts/fix]] - The fix-pass prompt: the open findings, the three actions and the fenced json action list the loop parses. Fields `$round`, `$findings`, `$tool_rules` (empty for a Claude builder; a Codex builder's tool rules again).
- reviewer-tools-codex.md and reviewer-tools-claude.md - `workflows/review-orchestration/prompts/reviewer-tools-codex.md` [[workflows/review-orchestration/prompts/reviewer-tools-codex]] - The one sentence of the reviewer prompt that differs by provider: a Codex reviewer may run read-only commands such as the tests; a Claude reviewer can read and search but run nothing, and may not use a file wildcard in a search.
- reviewer.md - `workflows/review-orchestration/prompts/reviewer.md` [[workflows/review-orchestration/prompts/reviewer]] - The reviewer's prompt: the triage rule, what to check, the brief, the mechanical results, the prior findings with the builder's actions, the changed files (each to be listed in `files_reviewed`), the exact lines the orchestrator's own checks wrote, and the diff, or where to read it when it is too large. Fields `$round`, `$brief`, `$mechanical`, `$prior`, `$changed_files`, `$check_written`, `$diff`, `$reviewer_tools`.

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
- The prompts restate rules held elsewhere: the action names and json block (`stopreasons.py`), the Revision History cap (`AGENTS.md` [[AGENTS]] and the audit), and the reviewer's fields (the schema). A change there and not here leaves a prompt asking for the wrong thing, and the loop then ends the run `error` on the reply rather than accepting it.
- The tool-rules files say what each provider's session can do, and the sessions enforce it elsewhere (`approver.py`, `codex_rules.py`, the reviewer's hook). A prompt that names a tool its provider lacks costs refused calls, not safety: a test renders each form and checks it names nothing the other provider's session alone has.
- `builder-tools-codex.md` is filled in with `string.Template`, so a literal dollar sign in it must be written twice. The command lists are passed in as values and are not parsed.
- `builder-tools-codex.md` shows the `Get-Content` form literally as well as in the read list; a change to that line of `config/codex-read-commands.txt` must change the example too. A test checks the example is accepted by the read list.

## Revision History
- 2026-09-24 - Initial creation with the run brief template.
- 2026-09-25 - The Acceptance checks comment now also rules out a launcher such as `env` or `sudo` in front of the program, matching the checker after code review round R2.
- 2026-09-29 - Stage A2, chunk (c): added `builder.md`, `fix.md` and `reviewer.md`, the prompts the build-and-review loop fills in.
- 2026-09-30 - `reviewer.md` gained the changed-file list the reviewer must cover in `files_reviewed`, and the files the orchestrator's own checks changed (chunk (c) code review R4-1 and R4-2).
- 2026-10-01 - `reviewer.md` now gives the exact lines the orchestrator's checks wrote, not whole files, and says any other change in those files is the builder's (R5-2).
- 2026-10-02 - Stage A2, chunk (d): the builder and fix prompts take `$tool_rules` and the reviewer prompt `$reviewer_tools`, each filled in by the provider holding the role; added `builder-tools-claude.md`, `builder-tools-codex.md`, `reviewer-tools-claude.md` and `reviewer-tools-codex.md`. A Claude builder's and a Codex reviewer's wording is as it was.
- 2026-10-04 - `builder-tools-codex.md` shows the one `Get-Content` form a Codex builder may read with, both encodings set, and why (a fix from the chunk (d) proof run, whose reads came back garbled).
