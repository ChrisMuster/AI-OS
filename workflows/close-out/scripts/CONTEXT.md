# Close-out - Scripts

**Last modified:** 2026-08-04

## Purpose
Holds the close-out verifier script.

## Contents
- `run.py` - the close-out verifier entry point. Runs the structural audit and link audit in-process, runs the selected workflow test suites as subprocesses, and returns one aggregate pass/fail result with an exit code. The structural-audit gate fails on any audit FAIL and, additionally, on any `doc-sync`-labelled or `skill-hardening`-labelled WARN finding: both are advisory WARN inside the audit (so the audit's own exit code is unchanged) but a hard fail at close-out, the deterministic "done means done" gate (doc-sync from plan R2-3 Option B; skill-hardening added for umbrella child #6, same treatment). doc-sync catches CONTEXT/LOG drift; skill-hardening catches a SKILL.md missing its Hardening section, one of that section's five required fields, or its Verification section. Both sets of messages are listed under the audit gate in the report. Only WARN counts - a `doc-sync` or `skill-hardening` finding at DEGRADED severity means the guard could not run, which is non-blocking and reported through the DEGRADED path, not as a hard fail. A check that could not run at all (a degraded advisory hook) is reported as DEGRADED: shown in its own report section, non-blocking (exit stays 0), and never counted as a clean pass. `--repair` runs `setup.py` to fix the runtime and re-runs the gates once.

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
- 2026-07-06 - Doc-sync teeth (build part 3, plan R2-3 Option B): `gate_audit()` now also fails on any `doc-sync`-labelled finding in the findings it already receives from the single in-process `run_audit` call, counting them separately from other WARNs, and `build_report` lists the drift messages under the audit gate. doc-sync CONTEXT/LOG drift is advisory WARN in the audit but a hard fail at close-out; other guards' WARNs (ai-style, personal-data) stay advisory.
- 2026-07-07 - Codex review fix (finding 3): the doc-sync drift filter in `gate_audit()` is now scoped to WARN severity, so a `doc-sync` finding at DEGRADED severity (the guard could not run) is no longer miscounted as drift and no longer hard-fails the structural-audit gate; it surfaces through the existing DEGRADED path instead. Added a `DocSyncTeethTests` regression test.
- 2026-07-09 - Skill-hardening teeth (umbrella Bucket-1 child #6): `gate_audit()` now also hard-fails on any `skill-hardening`-labelled WARN finding (a SKILL.md missing its Hardening section or a required field), counted separately as "skill-hardening gap" and exposed via a new `skill_hardening` gate key that `build_report` renders under the audit gate. Same WARN-only, DEGRADED-is-non-blocking treatment as doc-sync. Added a `SkillHardeningTeethTests` class to the tests.
- 2026-07-09 - Review-fix (child #6): generalised the two hardcoded blocking-label branches into a single `BLOCKING_LABELS = {label: noun}` map. `gate_audit()` derives one `blocking` dict (per-label WARN messages) and `build_report` renders it in one loop, replacing the separate `doc_sync`/`skill_hardening` gate keys; a third blocking label is now a one-line addition. Note: this changes the gate dict shape in `last-result.json` (a single `blocking` object instead of two keys).
- 2026-08-04 - Documentation correction in CONTEXT.md, no behaviour change: the `run.py` Contents entry described the skill-hardening gate as catching a SKILL.md missing its Hardening section or a required field, omitting the Verification section the guard has also required since the two-section contract landed. Corrected. Found by sweeping every description of what that guard checks, after the same incompleteness was found in three other files.
- 2026-08-04 - Line endings pinned on both text writes in `run.py` (the LOG.md append and the `last-result.json` durable result), which now pass `newline="\n"` explicitly. Part of the project-wide pass closing this defect class at all 48 write sites.
