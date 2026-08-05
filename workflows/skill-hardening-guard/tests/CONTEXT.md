# Skill-Hardening Guard - Tests

**Last modified:** 2026-08-04

## Purpose
Hermetic tests for the skill-hardening guard: unit tests for the pure parser and checker (both required SKILL.md sections - Hardening and Verification), and integration tests for skill discovery over a throwaway temporary tree. No git, no network, and no dependence on the real project's SKILL.md files, so the suite is deterministic and safe to run anywhere.

## Contents
- test_run.py - `workflows/skill-hardening-guard/tests/test_run.py` [[workflows/skill-hardening-guard/tests/CONTEXT]] - Covers `check_skill` for the Hardening section (complete section passes; missing section, missing field, empty field, placeholder field, and all-fields-missing each flagged; "None" accepted; a multi-line field value read in full; the `**Field** -` colon-outside form accepted; a fenced `## Hardening` example ignored; an angle-bracket stub flagged while an embedded angle token in prose is not; the section boundary stops at the next heading); a `VerificationSectionTests` class for the `## Verification` section (complete passes; missing, empty, and placeholder each flagged; the `Verification / escape hatch` Hardening field does not satisfy the section requirement); the pure `extract_section` / `extract_hardening_section` / `_field_content` helpers; `find_skill_files` / `run_check` over a temporary tree (top-level and workflow skills found; self, archived, template, and `_`-prefixed/hidden/skipped subtrees pruned before descent; an unreadable file surfaces as DEGRADED); a `TemplateDriftTests` guard that asserts `REQUIRED_FIELDS` matches the template's Hardening labels and that the template still carries a `## Verification` section; and a `SmokeTests` that runs the real guard as a subprocess and asserts valid JSON + exit 0.

## Inputs
- None beyond the guard's `scripts/run.py`, imported directly by file path. The tests build their own SKILL.md fixtures in a `tempfile` directory.

## Outputs
- Test results on stdout (unittest). Exit code 0 when all pass, non-zero on failure. No files written outside the temporary directory.

## Steps
1. Run `python workflows/skill-hardening-guard/tests/test_run.py`.
2. Confirm every test passes (exit 0).
3. Append LOG.md only when run as a deliberate, logged workflow step.

## Dependencies
- `workflows/skill-hardening-guard/scripts/run.py` [[workflows/skill-hardening-guard/scripts/CONTEXT]] - the module under test.
- Python 3.9+ standard library only (`unittest`, `tempfile`, `importlib`, `subprocess`, `json`, `re`).
- `templates/SKILL.md.template` [[templates/CONTEXT]] - read by the drift test to confirm the guard's `REQUIRED_FIELDS` still matches the template's Hardening labels.
- `workflows/close-out/` [[workflows/close-out/CONTEXT]] discovers and runs this suite as part of the close-out verifier.

## Known Issues
- None.

## Revision History
- 2026-07-09 - Initial creation. 13 tests: 10 for the pure parser/checker and 3 for discovery/run over a throwaway tree. Umbrella Bucket-1 child #6, Deliverable B.
- 2026-07-09 - Added a 14th test (`test_prunes_skipped_and_hidden_dirs_before_descent`) proving a SKILL.md inside a pruned subtree (node_modules, a hidden dir, archived/) is neither found nor flagged, backing the `find_skill_files` pruning fix (Codex review).
- 2026-07-09 - Grew to 23 tests for the code-review fixes: multi-line field value, colon-outside label form, fenced-example ignored, angle-bracket stub vs embedded-token, `_`-dir prune, and unreadable-file DEGRADED; plus new `TemplateDriftTests` (REQUIRED_FIELDS matches the template) and `SmokeTests` (real guard subprocess emits valid JSON, exit 0). Added `subprocess`/`json`/`re` imports and a template-file dependency.
- 2026-07-10 - Grew to 28 tests for the `## Verification` section enforcement (child #7): added a `VerificationSectionTests` class (complete/missing/empty/placeholder, and the Hardening `Verification / escape hatch` field not satisfying the section check), and updated several Hardening-focused fixtures to include a `## Verification` section so each stays single-concern.
- 2026-07-10 - Codex review follow-up (child #7): tightened `test_run_check_over_tree` to assert the exact two missing-section findings for the bad fixture (was a loose "some finding points at the bad file"), and added `test_template_has_verification_section` to the drift guard so a template that lost its `## Verification` section fails a test. Suite 28 -> 29.
- 2026-08-04 - The one fixture write in `test_run.py` converted from `Path.write_text(..., encoding="utf-8")` to `write_bytes`, so it stops writing a CRLF fixture on Windows and stops breaking the newline half of the AGENTS.md text-I/O rule. These fixtures are SKILL.md bodies parsed section by section, so CRLF endings were feeding the parser a different input here than it sees in the real tree. `write_bytes` rather than `Path.write_text(newline=...)`, which is a 3.10 API against the stated 3.9 floor. Part of the pass clearing the last 50 sites project-wide; `encoding` became a close-out blocking label in the same change. Suite unchanged at 29; no assertion touched.
