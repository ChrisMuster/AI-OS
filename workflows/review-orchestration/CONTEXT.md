# Review Orchestration

**Last modified:** 2026-09-24

## Purpose
Replaces the manual relay between two AIs with a run the user starts from Book Dragon: both AIs plan a task independently from one brief, the plans are merged, then one AI builds while the other reviews, in rounds that repeat automatically until a stop reason is reached. The orchestrator is a script that hands out every task and moves results between the AIs; the user assigns every role and keeps every git decision.

The workflow is built in six sub-stages that each ship with tests and a close-out pass. **Only the first is built so far: the run brief and its checker.** A run starts from a brief, a markdown file with seven required sections (Goal, Constraints, Out of scope, Acceptance checks, Edit paths, Open questions, Source) written with the user before the run, and nothing starts without one that passes the check. The engine, the independent-plans front half, the intent check, the measurement harness and the plain-language triggers come in later sub-stages and do not exist yet.

## Contents
- prompts/ - `workflows/review-orchestration/prompts/` [[workflows/review-orchestration/prompts/CONTEXT]] - The brief template, `brief.md`, which the checker refuses as shipped.
- scripts/ - `workflows/review-orchestration/scripts/` [[workflows/review-orchestration/scripts/CONTEXT]] - `run.py`, the entry point (one mode so far, `--check-brief`), and `brief.py`, the brief checker.
- tests/ - `workflows/review-orchestration/tests/` [[workflows/review-orchestration/tests/CONTEXT]] - `test_brief.py`, the checker's unittest suite.

## Inputs
- A run brief: a markdown file at a project-relative path, normally `workflows/review-orchestration/runs/<run-id>/brief.md` once runs exist, copied from `prompts/brief.md` and filled in with the user.
- For the Source section: the files and folders the brief names, which must exist in the project.

## Outputs
- The check result on stdout: `PASS: <path>`, or one `FAIL: <heading>: <reason>` line per problem, with exit code 0 or 1.
- `started` and `completed` or `failed` entries in this directory's `LOG.md` for every command-line check, with a note that never includes the brief's path. `--dry-run` prints them to stderr instead.

## Steps
1. Copy `prompts/brief.md` to the run's brief path and fill in every section with the user.
2. Run `python workflows/review-orchestration/scripts/run.py --check-brief <brief>`.
3. Fix every FAIL line and rerun until it prints PASS.
4. Append LOG.md with a completion or failure entry (the script does this itself when run from the command line).

## Dependencies
- Python 3.13+ standard library only. No SDK is needed yet; the optional requirements file for the two AI SDKs arrives with the engine.
- `AGENTS.md` [[AGENTS]] - the Workflow scripts convention (`run.py` entry point), the Script safety rule (`--dry-run`), and the encoding rules the scripts follow.
- `workflows/close-out/` [[workflows/close-out/CONTEXT]] - discovers and runs `tests/test_brief.py` as part of the close-out verifier.

## Known Issues
- Only the brief sub-stage is built. `runs/` does not exist yet: it is classified for the personal repository and created by the engine sub-stage, before the first run record is written. Until then a brief can be checked from any project-relative path.
- The README entry is marked `[in progress]` and this file describes a partial workflow. Both are brought to their finished shape by the integration sub-stage, which also adds the trigger phrases.
- The checker proves a brief is complete and well-formed. It cannot prove the brief is right: whether the goal, the edit paths and the acceptance checks describe the work the user wants is the intent check's job, in a later sub-stage.

## Revision History
- 2026-09-24 - Initial creation: Stage A1 of review orchestration. The brief template, the brief checker with its `run.py --check-brief` entry point, and the test suite.
