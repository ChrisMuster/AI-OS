# Close-out - Scripts

**Last modified:** 2026-07-05

## Purpose
Holds the close-out verifier script.

## Contents
- `run.py` - the close-out verifier entry point. Runs the structural audit and link audit in-process, runs the selected workflow test suites as subprocesses, and returns one aggregate pass/fail result with an exit code. A check that could not run at all (a degraded advisory hook) is reported as DEGRADED: shown in its own report section, non-blocking (exit stays 0), and never counted as a clean pass. `--repair` runs `setup.py` to fix the runtime and re-runs the gates once.

## Inputs
- Invoked as `python workflows/close-out/scripts/run.py [--scope all|NAME] [--json] [--repair]`. Reads the audit and link-check scripts (imported), the project test suites, and git working-tree state for affected-scope selection.

## Outputs
- A stdout report (or `--json`), `workflows/close-out/last-result.json` [[workflows/close-out/CONTEXT]], an exit code (0 pass / 1 fail), and LOG.md entries at both ends of a run.
- The report verdict distinguishes three outcomes: `RESULT: PASS` (all checks ran and passed), `RESULT: DEGRADED` (gates passed but one or more checks did not run - non-blocking, but not a clean pass), and `RESULT: FAIL`. The JSON carries a matching `status` (`pass` / `degraded` / `fail`) and a `clean` boolean, so a consumer can tell a clean pass from a degraded one without parsing the human report.

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
- 2026-07-05 - Documented the DEGRADED status and the opt-in `--repair` flag (added in the loud-degrade work but not previously reflected here). `run.py` now emits a distinct machine-readable verdict: JSON `status` (`pass`/`degraded`/`fail`) plus a `clean` boolean, and the human report reads `RESULT: DEGRADED (gates passed, but N check(s) did not run)` rather than annotating a PASS, so a degraded run can never be mistaken for a clean pass. Codex review-fix pass on the runtime-bootstrap work.
