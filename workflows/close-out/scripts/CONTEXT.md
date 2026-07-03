# Close-out - Scripts

**Last modified:** 2026-07-02

## Purpose
Holds the close-out verifier script.

## Contents
- `run.py` - the close-out verifier entry point. Runs the structural audit and link audit in-process, runs the selected workflow test suites as subprocesses, and returns one aggregate pass/fail result with an exit code.

## Inputs
- Invoked as `python workflows/close-out/scripts/run.py [--scope all|NAME] [--json]`. Reads the audit and link-check scripts (imported), the project test suites, and git working-tree state for affected-scope selection.

## Outputs
- A stdout report (or `--json`), `workflows/close-out/last-result.json` [[workflows/close-out/CONTEXT]], an exit code (0 pass / 1 fail), and LOG.md entries at both ends of a run.

## Steps
N/A - see the parent workflow `CONTEXT.md` for the verifier's step sequence.

## Dependencies
- `workflows/audit/scripts/run.py` [[workflows/audit/scripts/CONTEXT]] and `workflows/link-check/scripts/run.py` [[workflows/link-check/scripts/CONTEXT]] - imported by file path for the audit and link gates.
- Python standard library only (argparse, importlib, subprocess, json, pathlib, datetime).

## Known Issues
- The verifier imports the audit and link-check scripts by file path; if either script's `run_audit` / `run_audit_mode` function signature changes, the corresponding gate reports a failure until the verifier is updated. The close-out tests cover this coupling.
- To stay independent of whichever `python` is first on PATH, `run.py` re-execs under the project `.venv` interpreter when one exists and differs from the current one (guarded by the `BOOK_DRAGON_CLOSE_OUT_REEXEC` env marker against recursion; any failure falls through to the current interpreter). If the `.venv` is absent, it runs under the invoking interpreter, which must have the declared packages (e.g. PyYAML) for the ai-style gate.

## Revision History
- 2026-07-02 - Initial creation with `run.py` (the close-out verifier).
- 2026-07-02 - `run.py` now re-execs under the project `.venv` interpreter and escalates affected scope to all suites for cross-cutting changes (root `.md` docs or `templates/` [[templates/CONTEXT]]). Review-fix pass on the verification-discipline work.
