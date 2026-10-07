# Close-out - Tests

**Last modified:** 2026-10-07

## Purpose
Holds the test suite for the close-out verifier.

## Contents
- `test_close_out.py` - hermetic tests for scope selection (affected / all / name / git-unavailable fallback / cross-cutting escalation), the interpreter re-exec helpers (including `--read-only` flag forwarding), the subprocess test runner (including full failed output, no output field on a pass, run failure reasons, and output holding an undecodable byte), gate aggregation, report formatting, read-only execution and write suppression, the refused `--read-only --repair` combination, the import coupling to the audit and link-check scripts, DEGRADED surfacing (collected across gates, shown non-blocking, verdict annotated, repair note rendered), the `--repair` path (`run_repair` invoking setup.py and its missing-setup fallback), the doc-sync teeth (`DocSyncTeethTests`: a `doc-sync`-labelled WARN hard-fails `gate_audit` while other guards' WARNs stay advisory, a `doc-sync` finding at DEGRADED severity is non-blocking and surfaces via the DEGRADED path rather than as drift, a structural FAIL still fails, a clean audit passes, and the report surfaces the drift messages), the skill-hardening teeth (`SkillHardeningTeethTests`: the same set of assertions for the `skill-hardening` label - a WARN hard-fails the gate, a missing field also fails, non-blocking WARNs stay advisory, a DEGRADED finding is not a gap, a clean audit passes, and the report surfaces the gap messages), the encoding teeth (`EncodingTeethTests`: the same blocking-label contract for `encoding`, including a file-level DEGRADED that stays non-blocking), and the blocking-label documentation guard (`BlockingLabelsAreDocumentedTests`: every key of `BLOCKING_LABELS` is named in the workflow's own CONTEXT.md, and that CONTEXT.md still states that only a WARN blocks - so adding a blocking label cannot silently leave the prose describing the old one, which is how `skill-hardening` went a month undocumented).
- `test_runtime_bootstrap.py` - regression check that every entry-point script
  (one with a `__main__` block) in a workflow or skill `scripts/` directory
  importing a project-only package (any distribution in the
  `requirements.txt` set, resolved by following its `-r` includes) either
  bootstraps into the project `.venv` via `ensure_project_runtime()`, carries a
  `# runtime-guard: degrades without <pkg>` marker and degrades, or carries a
  `# runtime-guard: launched via <mechanism>` marker. Pure helper modules
  (no `__main__`) are exempt. Standard-library only, plus hermetic fixture tests
  for the parser and each acceptance signal.
- `test_read_only_writes.py` - what `--read-only` must not write. `ReadOnlySwitchTests` (in-process): `PYTHONDONTWRITEBYTECODE` and `sys.dont_write_bytecode` are set before the `.venv` re-exec, and read-only mode reaches the audit as `run_audit(read_only=True)`; the default leaves both alone. `BytecodeFixtureTests` (real runs): the live tree's tracked and unignored files are copied into a temporary git repository with no `__pycache__`, five LOG.md files are seeded, and the copy's own close-out runs on the link-check suite with `--json`, once in each mode (each in a fresh copy, shared by the class's tests); read-only, no file may be created or changed, caches included, while the default run must create a cache and write the knowledge-graph and close-out logs; and both modes must print a result with all three gates, the selected suite passing and the audit having walked the tree, with the same exit code, status and per-gate verdict and detail. The copy cannot pass (its LOG.md files and some link targets are gitignored), so the verdict is checked against the other mode's, not against a pass.

## Inputs
- Run as `python workflows/close-out/tests/test_close_out.py`. Imports the verifier from `workflows/close-out/scripts/run.py` [[workflows/close-out/scripts/CONTEXT]].

## Outputs
- Standard `unittest` pass/fail output. Exit code 0 on success, non-zero on failure.

## Steps
N/A - this is a test directory, not a workflow.

## Dependencies
- `workflows/close-out/scripts/run.py` [[workflows/close-out/scripts/CONTEXT]] - the module under test.
- Python standard library only (unittest, importlib, tempfile, pathlib).

## Known Issues
- `test_close_out.py` is hermetic (throwaway test files in a temp dir) and does not run the full structural audit. `BytecodeFixtureTests` in `test_read_only_writes.py` is the exception: it copies the live tree and runs the copy's real close-out twice, so it takes roughly half a minute and its result depends on the tracked files being in a state the copy can run.
- In that copy, `git add` was measured failing about one run in four with "unable to write file .git/objects/...: Permission denied", a file locked for a moment on Windows just after it was written. The test retries that one error up to three times, a second apart; any other git error, or a fourth lock, fails the test with git's message.
- The copy has no `.venv`, so the re-exec itself is not exercised there; `ReadOnlySwitchTests` checks the setting is in place when the re-exec is called.

## Revision History
Earlier history archived to LOG.md on 2026-10-07.
- 2026-07-04 - Added DegradedSurfacingTests and RepairTests to `test_close_out.py`
  for the new DEGRADED status and opt-in `--repair` flow (21 -> 26 tests).
- 2026-07-05 - `test_runtime_bootstrap.py` now detects the `ensure_project_runtime()`
  call via AST (a real call, not a bare text match), so a commented-out or
  docstringed mention no longer satisfies the guard; added attribute-form,
  commented-out, and docstring-mention fixtures (9 -> 12 tests). `test_close_out.py`
  gained `overall_status` coverage and now asserts the verdict reads `RESULT:
  DEGRADED` (distinct from a clean pass) rather than an annotated PASS (26 -> 27
  tests). Codex review-fix pass on the runtime-bootstrap work.
- 2026-07-06 - Added `DocSyncTeethTests` to `test_close_out.py` for the doc-sync
  close-out teeth (build part 3): a `doc-sync`-labelled finding hard-fails
  `gate_audit`, LOG drift also hard-fails, non-doc-sync WARNs (ai-style,
  personal-data) stay advisory, a structural FAIL still fails, a clean audit
  passes, and `build_report` surfaces the drift messages (27 -> 33 tests).
- 2026-07-07 - Codex review fix (finding 3): added `test_degraded_doc_sync_is_not_drift` to `DocSyncTeethTests`, asserting a `doc-sync` finding at DEGRADED severity leaves the structural-audit gate passing and surfaces through the DEGRADED path instead of counting as drift (33 -> 34 tests).
- 2026-07-09 - Added `SkillHardeningTeethTests` for the skill-hardening close-out teeth (umbrella Bucket-1 child #6): a `skill-hardening`-labelled WARN hard-fails `gate_audit`, a missing field also fails, non-blocking WARNs stay advisory, a DEGRADED finding is not a gap, a clean audit passes, and `build_report` surfaces the gap messages (34 -> 40 tests).
- 2026-07-09 - Review-fix (child #6): updated both teeth classes to read the generalised `gate["blocking"][label]` dict and to pass `"blocking": {...}` report fixtures, following the `BLOCKING_LABELS` refactor in the gate. Assertion-only change; still 40 tests.
- 2026-08-04 - Added `BlockingLabelsAreDocumentedTests` (40 -> 42 tests): every `BLOCKING_LABELS` key must be named in the workflow's CONTEXT.md, and that file must still state that only a WARN blocks. Adding a blocking label is deliberately a one-line change in `run.py`, which is exactly how `skill-hardening` stayed undocumented for a month while the prose still called `doc-sync` "the one exception". Both assertions were verified against positive controls (an undocumented third label, and a CONTEXT.md with the severity claim removed). Also corrected two stale comments in `DocSyncTeethTests` that described the gate as keying on the label alone.
- 2026-08-04 - The `_write` fixture helper in `TestRunnerTests` converted from `Path.write_text(..., encoding="utf-8")` to `write_bytes`, so it stops writing CRLF fixtures on Windows and stops breaking the newline half of the AGENTS.md text-I/O rule. One call site, every fixture in that class behind it. `write_bytes` rather than `Path.write_text(newline=...)`, which is a 3.10 API against the project's stated 3.9 floor. Test count unchanged at 42; no assertion touched.
- 2026-08-04 - The one fixture write in `test_runtime_bootstrap.py` converted to `write_bytes` on the same reasoning (the earlier pass covered `test_close_out.py` only), and `EncodingTeethTests` added to `test_close_out.py` for the new `encoding` blocking label (42 -> 49 tests). The teeth class mirrors the other two: a missing-`newline=` WARN and a CR-line-endings WARN each hard-fail the gate, other guards' WARNs stay advisory, an `encoding` INFO does not block, a DEGRADED finding is not a violation, a clean audit passes, and the report surfaces the message. Both positive controls are the guard's own message shapes for the two halves of the rule AGENTS.md states, rather than fixtures invented here. Verified beyond the mocks with a live control: a real violation planted in the tree fails the real gate, and removing it restores the pass.
- 2026-08-05 - Wording-only test cleanup: `EncodingTeethTests` now describes
  DEGRADED as skipped scope rather than only a guard runtime failure, and its
  non-blocking DEGRADED case uses encoding-guard's file-level skipped-code
  message as the fixture. Test count unchanged at 49.
- 2026-10-04 - Added controls for complete failed output, no output field on a pass, launch and timeout reasons (built 2026-10-03 by the review-orchestration proof run), an undecodable byte in a test's output, and the report omitting the result-file pointer when the file was not written (49 -> 54 tests). The undecodable-byte test was checked against a control with the fix removed, which lost the output.
- 2026-10-04 - Added main-entry controls for read-only output, unchanged or absent result and log files, failure exit code, refused repair, scoped JSON, all three gates, normal-run writes, and `.venv` flag forwarding.
- 2026-10-04 - Corrected the Revision History archive reference and recorded the removed entries verbatim in LOG.md.
- 2026-10-07 - Added `test_read_only_writes.py` (8 tests, 9 after code review R4-1, which found the copied-tree runs' results were ignored: both modes must now reach one verdict) for read-only mode writing nothing (orchestrator isolation S2). Single-change defects planted in memory (bytecode env not set, the flag not set, read-only not passed to the gate or to the audit) and in the copy's own files (no bytecode env, no `--no-log`) each fail a test. Known Issues rewritten: this suite now runs a real close-out in a copied tree, with the Windows lock retry.
