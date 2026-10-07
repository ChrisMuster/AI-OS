# Close-out

**Last modified:** 2026-10-07

## Purpose
The executable, mechanical half of the close-out task. It bundles the structural audit, the link audit, and the workflow test suites into one pass/fail verifier, so a "the checks pass" claim is a script exit code rather than prose. It is the enforcement backing for the Verification discipline rule in `AGENTS.md` [[AGENTS]]. The `--read-only` mode exists so another workflow, such as review-orchestration, can run these checks without writing anywhere in the project: no close-out log or result file, no knowledge-graph log entry from the audit gate, and no bytecode cache from any process it starts. What a selected test file does on its own is outside that guarantee (Known Issues).

## Contents
- scripts/ - `workflows/close-out/scripts/` [[workflows/close-out/scripts/CONTEXT]] - holds `run.py`, the verifier that runs the audit and link checks in-process and the selected test suites as subprocesses, then returns one aggregate result. It surfaces any DEGRADED check scope distinctly and non-blocking, whether that means a guard could not run or one file was skipped as unreadable; `--repair` runs setup.py only for the runtime-failure class and re-runs the gates once. `--read-only` runs the same gates without saving the close-out log or result file, with the audit told not to log and no bytecode caches written.
- tests/ - `workflows/close-out/tests/` [[workflows/close-out/tests/CONTEXT]] - the test suite for the verifier, including a regression guard that flags entry-point scripts importing a project-only package without a runtime signal (a `.venv` bootstrap, a `# runtime-guard: degrades without <pkg>` marker, or a `# runtime-guard: launched via <mechanism>` marker).
- `last-result.json` - the structured result of the most recent normal run (gitignored; unchanged by `--read-only`). A failed test file's `output` field keeps its full standard output and standard error; a passing file has no `output` field.

## Inputs
- The project's existing check scripts: the audit (`workflows/audit/scripts/run.py` [[workflows/audit/scripts/CONTEXT]]), the link checker (`workflows/link-check/scripts/run.py` [[workflows/link-check/scripts/CONTEXT]]), and every workflow and skill test suite (the test files under each `tests/` directory).
- Optional git working-tree state, used to work out which workflows changed for the default affected scope.

## Outputs
- A human-readable PASS/FAIL report (or `--json`) to stdout.
- `last-result.json` - the structured result of the most recent normal run, including the whole output or run failure reason for each failed test file. `--read-only` prints the same report or JSON without writing this file.
- An exit code: 0 if every gate passed, 1 if a gate failed, or 2 if `--read-only` was combined with `--repair`.
- `workflows/close-out/LOG.md` entries at both ends of every normal run, and the audit gate's knowledge-graph validation entries in `workflows/knowledge-graph/LOG.md` and the root `LOG.md`; none of these for `--read-only`.

## Steps
1. Refuse `--read-only --repair` with exit code 2 before any gate or write. With `--read-only`, set `PYTHONDONTWRITEBYTECODE=1` so no process started from here writes a bytecode cache. Then re-exec under the project `.venv` interpreter if one exists and differs from the invoking interpreter, forwarding all flags so the gate does not depend on which `python` is first on PATH.
2. Select the test suites to run from `--scope` (affected/default, `all`, or a workflow name); affected falls back to all if git cannot determine the changed set, and escalates to all when the change touches project-wide files no single suite owns (root-level `.md` governance docs or `templates/` [[templates/CONTEXT]]).
3. Run the structural audit in-process, read-only when `--read-only` is set (0 FAIL required to pass; WARN reported but not gating, except a WARN under one of the three blocking labels - `doc-sync` for CONTEXT/LOG drift, `skill-hardening` for a SKILL.md gap, and `encoding` for a text-I/O or line-ending violation - which hard-fails the gate. Severity is part of the rule: a DEGRADED finding under any of them means a check did not happen - the guard could not run, or a single file could not be read - and is non-blocking).
4. Run the link audit in-process (0 dead links required to pass).
5. Run each selected test file as a subprocess (all must exit 0). On a normal run, keep each failure's complete output in `last-result.json` and print its last few lines and the result file location. In read-only mode, print that the full output is not saved.
6. Surface any DEGRADED checks distinctly and non-blocking; with `--repair`, run setup.py to fix runtime-related DEGRADED findings and re-run the gates once, otherwise print guidance to handle either the runtime fix or the file-specific skipped-scope message.
7. Aggregate into one verdict, write `last-result.json` unless `--read-only` was set, print the same report or JSON, and set exit code 0 if every gate passed or 1 otherwise.
8. Append LOG.md with a completion or failure entry.

## Dependencies
- `workflows/audit/` [[workflows/audit/CONTEXT]] - imported in-process for the structural audit gate.
- `workflows/link-check/` [[workflows/link-check/CONTEXT]] - imported in-process for the link audit gate.
- Every workflow and skill test suite - executed as subprocesses.
- `AGENTS.md` [[AGENTS]] - defines the Verification discipline rule this workflow enforces and the close-out procedure it slots into.

## Known Issues
- The verifier gates on the audit FAIL count (0 required). Audit WARNs, including advisory personal-data and ai-style findings, are reported but do not fail the gate, matching the audit's own advisory semantics. The exceptions are the three blocking labels held in `BLOCKING_LABELS` in `scripts/run.py` [[workflows/close-out/scripts/CONTEXT]]: `doc-sync` (CONTEXT/LOG drift), `skill-hardening` (a SKILL.md missing its Hardening section, one of that section's five required fields, or its Verification section), and `encoding` (a violation of the AGENTS.md text-I/O rule, such as a text-mode write with no explicit `newline=`, or a file carrying CR line endings). All three are advisory WARN inside the audit but a hard fail at close-out (the deterministic "done means done" gate; plan R2-3, Option B), so a directory whose content changed without its CONTEXT.md / LOG.md moving blocks close-out, as does a SKILL.md with a Hardening or Verification gap, as does an encoding regression. **Only a WARN blocks.** A finding under any of the three at DEGRADED severity means a check did not happen: usually that the guard itself could not run, and for `encoding` and `skill-hardening` also that one file could not be read (a `.py` that does not parse, a SKILL.md the guard could not open). Either way it is non-blocking and surfaces through the DEGRADED path, so a missing runtime package or an unreadable file is repaired rather than announced as drift. The `encoding` label also carries INFO findings (an `open()` with no explicit `encoding=`), which stay advisory for the same reason. Personal-data leaks are hard-blocked separately by the git pre-commit hook [[workflows/rule-hooks/CONTEXT]].
- It covers the mechanical checks only. It does not judge whether the planned work is complete, whether LOG.md files are current, or whether CONTEXT.md files are accurate; those remain the human judgement steps of close-out.
- Affected-scope test selection is only as good as git's changed-file view; when git is unavailable it runs all suites rather than risk under-testing. Changes confined to cross-cutting files (root-level `.md` governance docs or `templates/` [[templates/CONTEXT]]) escalate affected scope to all suites, since no single workflow suite owns those files.
- A normal run writes its own `last-result.json` and LOG.md, the knowledge-graph validation's entries in its own and the root LOG.md, and Python bytecode caches. `--read-only` writes none of these (tested by running a copied tree's real close-out, `tests/test_read_only_writes.py`); it cannot be combined with `--repair`. Selected test subprocesses may still write files of their own in either mode. The opt-in `--repair` runs `setup.py` to repair the project runtime only when a check DEGRADED.
- A DEGRADED finding is non-blocking: it does not flip the verdict to FAIL, but it is counted and shown distinctly so a run where a scope was not checked can never read as a clean pass. Runtime-related DEGRADED findings can be retried with `--repair` (auto-runs setup.py and re-runs); file-level DEGRADED findings need the file-specific message handled, then the verifier re-run.

## Revision History
- 2026-07-02 - Initial creation. Built as best-practices umbrella child #2 (verification discipline): the executable close-out verifier that backs the new AGENTS.md rule.
- 2026-07-02 - Review-fix pass: the verifier re-execs under the project `.venv` interpreter (so the documented `python ...` command does not depend on PATH), and affected scope now escalates to all suites for cross-cutting changes (root `.md` docs or `templates/` [[templates/CONTEXT]]).
- 2026-07-03 - Added a runtime-bootstrap regression guard to the close-out tests
  so scripts importing PyYAML must use the canonical `.venv` handoff or
  explicitly document a degrade path.
- 2026-07-03 - Generalised that guard from PyYAML to the whole `requirements.txt`
  package set, extended it to `skills/*/scripts/`, scoped it to entry-point
  scripts, and moved to `# runtime-guard:` marker comments. Added the launcher
  markers to `server.py`/`mcp_smoke.py` and a degrade marker to
  `ai-style-guard/run.py`.
- 2026-07-04 - The verifier now surfaces DEGRADED (a check that could not run)
  distinctly and non-blocking, reading the audit's DEGRADED findings and adding a
  `DEGRADED` report section plus a verdict annotation. Added the opt-in `--repair`
  flag (runs setup.py, then re-runs the gates once, falling back to the printed
  fix). ai-style-guard was converted from a degrade marker to a `.venv` bootstrap
  in the same body of work (a guard must run, not silently skip).
- 2026-07-06 - Doc-sync teeth (build part 3, plan R2-3 Option B): the structural-audit
  gate now also hard-fails on any `doc-sync`-labelled finding, so CONTEXT/LOG drift
  (advisory WARN inside the audit) becomes a close-out failure with the drift
  directories listed under the gate. Added `DocSyncTeethTests` to the close-out
  suite (27 -> 33 tests).
- 2026-08-04 - Documentation correction, no behaviour change. Steps and Known
  Issues described `doc-sync` as the single blocking exception and never named
  severity, so a reader landing on either would conclude the label alone
  hard-fails. Both now name the two `BLOCKING_LABELS` entries (`doc-sync` for
  drift, `skill-hardening` for a SKILL.md gap) and state that only a WARN
  blocks, with DEGRADED non-blocking. `skill-hardening` had been a blocking
  label since 2026-07-09 without appearing here at all. The same entries also
  described that guard as checking the Hardening section alone, omitting the
  Verification section it equally requires. Codex adversarial review of
  guard-coverage stage 3a, plus the wider sweep it asked for.
- 2026-08-04 - `encoding` added as the third blocking label, so a violation of
  the AGENTS.md text-I/O rule fails close-out instead of being reported and
  ignored. The prompt was 50 real violations (test files writing CRLF fixtures)
  sitting inside a `RESULT: PASS` for a day: the encoding guard reported every
  one of them and nothing gated on the report, so detection was never the gap.
  Same contract as the other two labels - only a WARN blocks, DEGRADED means the
  guard could not run and stays non-blocking, and the `encoding` label's INFO
  findings (an `open()` with no explicit `encoding=`) stay advisory. Added
  `EncodingTeethTests` (42 -> 49 tests).
- 2026-08-05 - Documentation only, no gate change. Both places describing DEGRADED
  said it means "the guard could not run", which was true of all three labels when
  it was written and is now too narrow for two of them: `encoding` reports it for a
  single `.py` that will not parse, and `skill-hardening` for a SKILL.md it cannot
  read, while the guard around them runs normally. The gate treats every DEGRADED
  the same way and always did, so the behaviour is unchanged; the wording was
  telling a reader that a DEGRADED line means something broke in the tooling, when
  it can equally mean one file went unchecked, which is the thing they need to
  notice.
- 2026-08-05 - Follow-up wording cleanup after review: the remaining close-out
  descriptions of DEGRADED now say a check scope was skipped, which may be a
  runtime failure or a single unreadable file. The `--repair` wording now
  specifically targets runtime-related DEGRADED findings instead of implying
  setup.py can fix file-level skips. No gate behaviour changed.
- 2026-10-03 - Failed test files now keep their full output in `last-result.json`; the report retains a short tail and points to that file.
- 2026-10-04 - Added `--read-only` to run all gates without changing the close-out log or saved result, with a clear unsaved-output report note and an early refusal for `--repair`.
- 2026-10-04 - Clarified that the audit gate's knowledge-graph validation still appends to its own and the root LOG.md during read-only runs.
- 2026-10-04 - Stated why another workflow uses read-only mode, its limited tree guarantee, and the refused flag combination's exit code.
- 2026-10-07 - `--read-only` now writes nothing in the project (orchestrator isolation S2): the audit gate runs the audit read-only, so the knowledge-graph validation writes no LOG.md entry, and `PYTHONDONTWRITEBYTECODE` is set before the re-exec. Purpose, Contents, Outputs, Steps and Known Issues updated; `tests/test_read_only_writes.py` added.
