# Review Orchestration

**Last modified:** 2026-09-25

## Purpose
Replaces the manual relay between two AIs with a run the user starts from Book Dragon: both AIs plan a task independently from one brief, the plans are merged, then one AI builds while the other reviews, in rounds that repeat automatically until a stop reason is reached. The orchestrator is a script that hands out every task and moves results between the AIs; the user assigns every role and keeps every git decision.

The workflow is built in six sub-stages that each ship with tests and a close-out pass. **The first is built: the run brief and its checker.** A run starts from a brief, a markdown file with seven required sections (Goal, Constraints, Out of scope, Acceptance checks, Edit paths, Open questions, Source) written with the user before the run, and nothing starts without one that passes the check.

**The second, the engine, is being built in three chunks.** The first chunk is built: the run record folder and its state file, the usage-limit readings for both providers with the ceiling that stops a run before it spends allowance needed for interactive work, the settings file that holds that ceiling, and the optional requirements file for the two AI SDKs. The providers, the approver and the stop-reason rules come next, then the live build-and-review loop. The independent-plans front half, the intent check, the measurement harness and the plain-language triggers come in later sub-stages and do not exist yet.

## Contents
- config/ - `workflows/review-orchestration/config/` [[workflows/review-orchestration/config/CONTEXT]] - The settings file the user edits, `settings.json`.
- prompts/ - `workflows/review-orchestration/prompts/` [[workflows/review-orchestration/prompts/CONTEXT]] - The brief template, `brief.md`, which the checker refuses as shipped.
- scripts/ - `workflows/review-orchestration/scripts/` [[workflows/review-orchestration/scripts/CONTEXT]] - `run.py`, the entry point (one mode so far, `--check-brief`); `brief.py`, the brief checker; `limits.py`, the usage-limit readings; `runrecord.py`, the run record; `settings.py`, the settings reader.
- tests/ - `workflows/review-orchestration/tests/` [[workflows/review-orchestration/tests/CONTEXT]] - `test_brief.py`, `test_limits.py` and `test_runrecord.py`, the unittest suites.
- requirements.txt - `workflows/review-orchestration/requirements.txt` - The two AI SDKs the engine drives, pinned exactly. Optional and not in the root `requirements.txt`, since nothing else in the project needs them.

## Inputs
- A run brief: a markdown file at a project-relative path, normally `workflows/review-orchestration/runs/<run-id>/brief.md` once runs start, copied from `prompts/brief.md` and filled in with the user.
- For the Source section: the files and folders the brief names, which must exist in the project.
- `config/settings.json`, for the usage ceiling.
- The two AI SDKs, installed into the project `.venv` from `requirements.txt`, and each provider's existing subscription login. The engine's live parts need them; the brief checker does not.

## Outputs
- The brief check result on stdout: `PASS: <path>`, or one `FAIL: <heading>: <reason>` line per problem, with exit code 0 or 1.
- `started` and `completed` or `failed` entries in this directory's `LOG.md` for every command-line check, with a note that never includes the brief's path. `--dry-run` prints them to stderr instead.
- The run records: one folder per run, `runs/<run-id>/`, created by `runrecord.py` with its `state.json`, which is rewritten atomically after every completed step. `runs/` is gitignored and classified for the personal repository, so it is local only and a capture after a run picks it up. It is described here rather than under Contents because it does not exist until the first run, and the audit checks every Contents path against the disk.

## Steps
1. Copy `prompts/brief.md` to the run's brief path and fill in every section with the user.
2. Run `python workflows/review-orchestration/scripts/run.py --check-brief <brief>`.
3. Fix every FAIL line and rerun until it prints PASS.
4. Append LOG.md with a completion or failure entry (the script does this itself when run from the command line).

## Dependencies
- Python 3.13+ standard library for the brief checker, the run record and the settings reader.
- `claude-agent-sdk` and `openai-codex`, pinned in `requirements.txt`, for the usage readings and every later part of the engine. `limits.py` imports them only inside the functions that need them.
- `AGENTS.md` [[AGENTS]] - the Workflow scripts convention (`run.py` entry point), the Script safety rule (`--dry-run`), and the encoding rules the scripts follow.
- `workflows/close-out/` [[workflows/close-out/CONTEXT]] - discovers and runs the test suites as part of the close-out verifier.
- `workflows/sync-architecture/` [[workflows/sync-architecture/CONTEXT]] - `runs/` is classified in Category A of the executable `allowlist` block of the gitignored `SYNC-ARCHITECTURE-PLAN.md`, and checked with that workflow's `boundary.py` and `run.py --check`. Moving or renaming `runs/` means changing that line, the `.gitignore` rule, and its row in `workflows/doc-sync-guard/config/output-inventory.yaml` [[workflows/doc-sync-guard/config/CONTEXT]] together.

## Known Issues
- Only the brief sub-stage and the engine's first chunk are built. Nothing yet starts a run: `runrecord.py` and `limits.py` are called by the loop, which arrives in the engine's third chunk, and `run.py` has no run mode until then.
- The README entry is marked `[in progress]` and this file describes a partial workflow. Both are brought to their finished shape by the integration sub-stage, which also adds the trigger phrases.
- The checker proves a brief is complete and well-formed. It cannot prove the brief is right: whether the goal, the edit paths and the acceptance checks describe the work the user wants is the intent check's job, in a later sub-stage.
- The SDKs are pinned exactly and change their interfaces between releases, so upgrading either means rerunning the test suites and the live usage read before the new pin is trusted.

## Revision History
- 2026-09-24 - Initial creation: Stage A1 of review orchestration. The brief template, the brief checker with its `run.py --check-brief` entry point, and the test suite.
- 2026-09-25 - Stage A2, chunk (a): `requirements.txt` for the two SDKs, `config/` with `settings.json`, `limits.py`, `runrecord.py` and `settings.py` with their test suites, and the gitignored `runs/` folder classified for the personal repository (described under Outputs, since it does not exist until the first run).
