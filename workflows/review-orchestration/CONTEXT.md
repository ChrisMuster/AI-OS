# Review Orchestration

**Last modified:** 2026-10-04

## Purpose
Replaces the manual relay between two AIs with a run the user starts from Book Dragon: both AIs plan a task independently from one brief, the plans are merged, then one AI builds while the other reviews, in rounds that repeat automatically until a stop reason is reached. The orchestrator is a script that hands out every task and moves results between the AIs; the user assigns every role and keeps every git decision.

The workflow is built in six sub-stages that each ship with tests and a close-out pass. **The first is built: the run brief and its checker.** A run starts from a brief, a markdown file with seven required sections (Goal, Constraints, Out of scope, Acceptance checks, Edit paths, Open questions, Source) written with the user before the run, and nothing starts without one that passes the check.

**The second, the engine, is built in four chunks.** The first built the run record folder and its state file, the usage-limit readings for both providers with the ceiling that stops a run before it spends allowance needed for interactive work, the settings file that holds that ceiling, and the optional requirements file for the two AI SDKs. The second built the approver that confines the builder to the brief's edit paths and a list of verification commands, the stop-reason rules and the structured replies they read, and the builder and reviewer sessions. The third built the live build-and-review loop: `run.py --run` takes a checked brief, has one AI build, then repeats review rounds and fix passes until a stop reason, checking usage and the Codex credit balance before every step, stopping cleanly and resuming, and writing the review packet and a report. Until the intent check exists, a run with nothing left to fix ends `reviewed-clean` rather than `clean`. The fourth built the reverse pairing. Roles are named by what they do, and the user names the builder for each run (`--builder claude` or `--builder codex`); the other AI reviews, and there is no default. A Codex session in the project can start, resume and stop a run through a rules entry in `.codex/rules/`. A Codex builder is held by a program Codex runs before every tool call, with its sandbox beneath that for file writes and a check of the whole project after every turn; a Claude reviewer can only read and search. The independent-plans front half, the intent check, the measurement harness and the plain-language triggers come in later sub-stages and do not exist yet.

## Contents
- config/ - `workflows/review-orchestration/config/` [[workflows/review-orchestration/config/CONTEXT]] - The files the user edits: `settings.json` (usage ceiling, round caps, models), `verify-commands.txt` (what a Claude builder may run), and `codex-verify-commands.txt` and `codex-read-commands.txt` (what a Codex builder may run and read with).
- prompts/ - `workflows/review-orchestration/prompts/` [[workflows/review-orchestration/prompts/CONTEXT]] - The brief template, `brief.md`, which the checker refuses as shipped, the builder, fix and reviewer prompts the loop fills in, and the parts of them that differ by provider.
- scripts/ - `workflows/review-orchestration/scripts/` [[workflows/review-orchestration/scripts/CONTEXT]] - `run.py`, the entry point (`--check-brief`, `--run`, `--resume`, `--stop`); `brief.py`, the brief checker and reader; `loop.py`, the build-and-review loop; `packet.py`, the review packet writer; `worktree.py`, what a run changed and its round records; `checks.py`, the mechanical checks; `limits.py`, the usage-limit readings; `runrecord.py`, the run record; `settings.py`, the settings reader; `approver.py`, the builder's tool-call decisions and the read rules; `codex_rules.py` and `codex_hook.py`, a Codex builder's decisions and the program Codex runs before each of its tool calls; `stopreasons.py`, the stop reasons and loop-exit rules; `providers.py`, the builder and reviewer sessions for both providers.
- tests/ - `workflows/review-orchestration/tests/` [[workflows/review-orchestration/tests/CONTEXT]] - `test_brief.py`, `test_limits.py`, `test_runrecord.py`, `test_approver.py`, `test_stopreasons.py`, `test_providers.py`, `test_codex_rules.py` and `test_loop.py`, the unittest suites.
- requirements.txt - `workflows/review-orchestration/requirements.txt` - The two AI SDKs the engine drives, pinned exactly. Optional and not in the root `requirements.txt`, since nothing else in the project needs them.

## Inputs
- A run brief: a markdown file at a project-relative path, normally `workflows/review-orchestration/runs/<run-id>/brief.md` once runs start, copied from `prompts/brief.md` and filled in with the user.
- For the Source section: the files and folders the brief names, which must exist in the project.
- Which AI builds, named by the user for each run (`--builder`).
- `config/settings.json`, for the usage ceiling, the round caps and the models, and `config/verify-commands.txt`, for the commands a Claude builder may run, or the two Codex lists for a Codex builder.
- The two AI SDKs, installed into the project `.venv` from `requirements.txt`, and each provider's existing subscription login. The engine's live parts need them; the brief checker does not.
- For a run: an item name for its review packet (`memory/<item>_review_packet.md`, which must not exist yet), and the personal repository's folder, captured before the run starts.

## Outputs
- The brief check result on stdout: `PASS: <path>`, or one `FAIL: <heading>: <reason>` line per problem, with exit code 0 or 1.
- `started` and `completed` or `failed` entries in this directory's `LOG.md` for every command-line check, with a note that never includes the brief's path. `--dry-run` prints them to stderr instead.
- From a run: the builder's edits inside the brief's edit paths; the review packet `memory/<item>_review_packet.md`; `started` and `completed` or `failed` entries in this directory's `LOG.md`; and a run record. Nothing is staged or committed.
- The run records: one folder per run, `runs/<run-id>/`, created by `runrecord.py` with its `state.json`, which is rewritten atomically after every completed step, and filled by the loop with the brief, the resolved settings, the start and round records, transcripts and the report. `runs/` is gitignored and classified for the personal repository, so it is local only and a capture after a run picks it up. It is described here rather than under Contents because it does not exist until the first run, and the audit checks every Contents path against the disk.

## Steps
1. Copy `prompts/brief.md` to the run's brief path and fill in every section with the user.
2. Run `python workflows/review-orchestration/scripts/run.py --check-brief <brief>`.
3. Fix every FAIL line and rerun until it prints PASS.
4. Commit any work under the brief's edit paths, make sure the close-out verifier passes, and make sure no `.env`-named file is both untracked and unignored (a run refuses to start without all three), then capture the personal repository with the gated chain (`workflows/sync-architecture/` [[workflows/sync-architecture/CONTEXT]]), so everything the run starts from is committed somewhere.
5. Start the run: `python workflows/review-orchestration/scripts/run.py --run <brief> --builder claude|codex --item <item> --git-dir <personal repository>`, with the builder the user named; the other AI reviews. An AI asked to start a run with no builder named asks the user which AI builds and never picks one itself. Do not edit the brief's edit paths while it runs; with Codex building, change nothing tracked or hand-written anywhere in the project while a builder turn is running, since the check after the turn would end the run.
6. Read `runs/<run-id>/report.md` and do what it recommends for the stop reason; `--resume <run-id>` continues a run that stopped on `max-rounds`, `usage-limit` or `error`.
7. Append LOG.md with a completion or failure entry (the script does this itself when run from the command line).

## Dependencies
- Python 3.13+ standard library for the brief checker, the run record and the settings reader.
- `claude-agent-sdk` and `openai-codex`, pinned in `requirements.txt`, for the usage readings and every later part of the engine. `limits.py` imports them only inside the functions that need them.
- `AGENTS.md` [[AGENTS]] - the Workflow scripts convention (`run.py` entry point), the Script safety rule (`--dry-run`), and the encoding rules the scripts follow.
- `workflows/close-out/` [[workflows/close-out/CONTEXT]] - discovers and runs the test suites as part of the close-out verifier.
- `.codex/rules/` [[.codex/rules/CONTEXT]] - the Codex rules entry that lets an interactive Codex session run `run.py --run`, `--resume` and `--stop` outside its sandbox; changing the entry point's path or modes means changing it too.
- `workflows/sync-architecture/` [[workflows/sync-architecture/CONTEXT]] - `runs/` is classified in Category A of the executable `allowlist` block of the gitignored `SYNC-ARCHITECTURE-PLAN.md`, and checked with that workflow's `boundary.py` and `run.py --check`. Moving or renaming `runs/` means changing that line, the `.gitignore` rule, and its row in `workflows/doc-sync-guard/config/output-inventory.yaml` [[workflows/doc-sync-guard/config/CONTEXT]] together.

## Known Issues
- A run starts from a brief the user wrote, not from a plan the two AIs made: the independent-plans front half, and the intent check that would let a run end `clean`, are later sub-stages. Until then a run with nothing left to fix ends `reviewed-clean`, and the user reads the diff against the brief.
- The reverse pairing (Codex builds, Claude reviews) made its first live run on 2026-10-03, which found five defects, fixed on 2026-10-04 and tested with no model call. A second proof run, with nine live checks, is what shows the fixed pairing against both providers.
- A Codex builder is kept off the network by its hook, not by its sandbox: on the build machine a third-party firewall leaves Codex's own network block inactive. The detail and the remaining limits are in `scripts/CONTEXT.md`.
- A Codex builder cannot run the targeted audit or the close-out verifier itself, so a documentation slip it would have caught reaches it as a finding in the next round.
- The README entry is marked `[in progress]` and this file describes a partial workflow. Both are brought to their finished shape by the integration sub-stage, which also adds the trigger phrases.
- The checker proves a brief is complete and well-formed. It cannot prove the brief is right: whether the goal, the edit paths and the acceptance checks describe the work the user wants is the intent check's job, in a later sub-stage.
- The SDKs are pinned exactly and change their interfaces between releases, so upgrading either means rerunning the test suites and the live usage read before the new pin is trusted.

## Revision History
- 2026-09-24 - Initial creation: Stage A1 of review orchestration. The brief template, the brief checker with its `run.py --check-brief` entry point, and the test suite.
- 2026-09-25 - Stage A2, chunk (a): `requirements.txt` for the two SDKs, `config/` with `settings.json`, `limits.py`, `runrecord.py` and `settings.py` with their test suites, and the gitignored `runs/` folder classified for the personal repository (described under Outputs, since it does not exist until the first run).
- 2026-09-26 - Stage A2, chunk (b): `approver.py`, `stopreasons.py` and `providers.py` with their test suites, roles, round caps and models in `config/settings.json`, and `config/verify-commands.txt`. Roles are named by what they do, at the user's decision, so the reverse pairing stays possible.
- 2026-09-29 - Stage A2, chunk (c): the live build-and-review loop (`loop.py`, `packet.py`, `worktree.py`, `checks.py`), `run.py --run`, `--resume` and `--stop`, the builder, fix and reviewer prompts, and `test_loop.py`. A run with nothing left to fix ends `reviewed-clean` until the intent check exists, the user's option A. The project allowlist gained one rule for the entry point.
- 2026-09-30 - Code review repairs for chunk (c): a run now refuses to start while its edit paths hold uncommitted work, so Steps say to commit first; the other repairs are recorded in `scripts/CONTEXT.md`.
- 2026-10-01 - A run now also refuses to start unless the close-out verifier is clean (chunk (c) code review R5-3); Steps say so.
- 2026-10-02 - Stage A2, chunk (d): the reverse pairing. `codex_rules.py`, `codex_hook.py`, the Codex builder and Claude reviewer sessions, the check after every Codex builder turn, the read rules for every session, two Codex command lists in `config/`, the provider-specific prompt parts, and `test_codex_rules.py`. A run of either pairing now refuses to start while a `.env`-named file is neither tracked nor ignored; Steps say so.
- 2026-10-04 - Fixes from the chunk (d) proof run: the builder is named per run with `--builder` and `config/settings.json` no longer holds roles; a new `.codex/rules/` entry lets Codex start, resume and stop runs; a Codex builder's launch readings, per-step tokens and file reads, and the run record's escaping, are fixed in `scripts/`, `config/` and `prompts/`. Purpose, Contents, Inputs, Steps, Dependencies and Known Issues updated.
- 2026-10-04 - `run.py` re-runs itself under the project `.venv`, so the documented `python ...` command works from a Codex session, whose shell finds the system Python; found when proof run 2 was refused at its start.
