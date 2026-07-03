# Close-out

**Last modified:** 2026-07-02

## Purpose
The executable, mechanical half of the close-out task. It bundles the structural audit, the link audit, and the workflow test suites into one pass/fail verifier, so a "the checks pass" claim is a script exit code rather than prose. It is the enforcement backing for the Verification discipline rule in `AGENTS.md` [[AGENTS]].

## Contents
- scripts/ - `workflows/close-out/scripts/` [[workflows/close-out/scripts/CONTEXT]] - holds `run.py`, the verifier that runs the audit and link checks in-process and the selected test suites as subprocesses, then returns one aggregate result.
- tests/ - `workflows/close-out/tests/` [[workflows/close-out/tests/CONTEXT]] - the test suite for the verifier.
- `last-result.json` - the structured result of the most recent run (gitignored; rewritten on every run).

## Inputs
- The project's existing check scripts: the audit (`workflows/audit/scripts/run.py` [[workflows/audit/scripts/CONTEXT]]), the link checker (`workflows/link-check/scripts/run.py` [[workflows/link-check/scripts/CONTEXT]]), and every workflow and skill test suite (the test files under each `tests/` directory).
- Optional git working-tree state, used to work out which workflows changed for the default affected scope.

## Outputs
- A human-readable PASS/FAIL report (or `--json`) to stdout.
- `last-result.json` - the structured result of the most recent run.
- An exit code: 0 if every gate passed, 1 otherwise.
- LOG.md entries at both ends of every run.

## Steps
1. Re-exec under the project `.venv` interpreter if one exists and differs from the invoking interpreter, so the gate does not depend on which `python` is first on PATH.
2. Select the test suites to run from `--scope` (affected/default, `all`, or a workflow name); affected falls back to all if git cannot determine the changed set, and escalates to all when the change touches project-wide files no single suite owns (root-level `.md` governance docs or `templates/` [[templates/CONTEXT]]).
3. Run the structural audit in-process (0 FAIL required to pass; WARN reported but not gating).
4. Run the link audit in-process (0 dead links required to pass).
5. Run each selected test file as a subprocess (all must exit 0).
6. Aggregate into one verdict, write `last-result.json`, print the report, and set the exit code.
7. Append LOG.md with a completion or failure entry.

## Dependencies
- `workflows/audit/` [[workflows/audit/CONTEXT]] - imported in-process for the structural audit gate.
- `workflows/link-check/` [[workflows/link-check/CONTEXT]] - imported in-process for the link audit gate.
- Every workflow and skill test suite - executed as subprocesses.
- `AGENTS.md` [[AGENTS]] - defines the Verification discipline rule this workflow enforces and the close-out procedure it slots into.

## Known Issues
- The verifier gates on the audit FAIL count (0 required). Audit WARNs, including advisory personal-data and ai-style findings, are reported but do not fail the gate, matching the audit's own advisory semantics. Personal-data leaks are hard-blocked separately by the git pre-commit hook [[workflows/rule-hooks/CONTEXT]].
- It covers the mechanical checks only. It does not judge whether the planned work is complete, whether LOG.md files are current, or whether CONTEXT.md files are accurate; those remain the human judgement steps of close-out.
- Affected-scope test selection is only as good as git's changed-file view; when git is unavailable it runs all suites rather than risk under-testing. Changes confined to cross-cutting files (root-level `.md` governance docs or `templates/` [[templates/CONTEXT]]) escalate affected scope to all suites, since no single workflow suite owns those files.
- It is read-only with respect to project content (it writes only its own `last-result.json` and LOG.md), so like the audit it is exempt from the `--dry-run` convention.

## Revision History
- 2026-07-02 - Initial creation. Built as best-practices umbrella child #2 (verification discipline): the executable close-out verifier that backs the new AGENTS.md rule.
- 2026-07-02 - Review-fix pass: the verifier re-execs under the project `.venv` interpreter (so the documented `python ...` command does not depend on PATH), and affected scope now escalates to all suites for cross-cutting changes (root `.md` docs or `templates/` [[templates/CONTEXT]]).
