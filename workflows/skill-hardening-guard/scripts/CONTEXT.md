# Skill-Hardening Guard - Scripts

**Last modified:** 2026-08-04

## Purpose
Holds the skill-hardening guard's single entry point. `run.py` is the read-only checker that verifies every SKILL.md carries the two required load-bearing sections: a complete `## Hardening` section (all five required fields non-empty) and a non-empty `## Verification` section. It reports WARN findings the full audit and close-out verifier consume.

## Contents
- run.py - `workflows/skill-hardening-guard/scripts/run.py` [[workflows/skill-hardening-guard/scripts/CONTEXT]] - The checker: skill discovery, code-fence stripping, required-section parsing, per-field validation, and the `--check` / `--json` / `--strict` CLI. Pure functions (`find_skill_files`, `strip_code_blocks`, `extract_section` (with an `extract_hardening_section` back-compat wrapper), `_field_content`, `_is_unfilled`, `check_skill`, `run_check`) keep it unit-testable without git or a subprocess.

## Inputs
- The on-disk SKILL.md files under `skills/` [[skills/CONTEXT]] and `workflows/` [[workflows/CONTEXT]] (read as UTF-8). No git, no network, no config file.

## Outputs
- A findings report on stdout (human text, or `--json` for the audit hook), under a `skill-hardening` label. A Hardening gap is a WARN; a SKILL.md the guard cannot read is a non-blocking DEGRADED (never a blocking WARN).
- Exit code 0 by default (advisory); with `--strict`, exit 1 when any WARN exists so a caller can gate on it. DEGRADED does not trip `--strict` (it means the check could not run, not that a skill failed).
- No files are written and no LOG.md entry is made on a check run (read-only, like the other content guards).

## Steps
1. Discover every SKILL.md under the skill roots via a pruned `os.walk` that drops the guard's own directory, any `archived/` path, hidden and `_`-prefixed (private/scratch) directories, and the standard non-project directories before descending into them.
2. For each, strip fenced code blocks (so a documented example section is not mistaken for the real one), then check both required sections. Extract the `## Hardening` section and validate that all five required fields (`Allowed tool intent`, `Never`, `Approval-gated`, `Write boundaries`, `Verification / escape hatch`) are present and non-empty (and not an unfilled placeholder); field content may span wrapped continuation lines, and both `**Field:**` and `**Field** -` label forms are accepted. Separately, confirm a `## Verification` section exists and is non-empty (it has no sub-fields, so this is a whole-section presence check). The heading match is exact, so the `Verification / escape hatch` Hardening field never satisfies the `## Verification` section requirement.
3. Emit a WARN for any gap, or a DEGRADED for a SKILL.md that could not be read; exit 1 under `--strict` only when a WARN exists.
4. Append LOG.md only when run as a deliberate, logged workflow step (the read-only check and the audit-hook invocation do not log).

## Dependencies
- `AGENTS.md` [[AGENTS]] (root) - Defines the SKILL.md schema and the required Hardening section (five fields) and Verification section this script enforces.
- `templates/SKILL.md.template` [[templates/CONTEXT]] - The canonical Hardening field labels the checker keys on; if the template's labels change, `REQUIRED_FIELDS` here must change with them.
- Python 3.9+ standard library only (no third-party packages, so no `.venv` bootstrap is needed).
- Consumers: `workflows/audit/` [[workflows/audit/CONTEXT]] runs it as an advisory hook (WARN gaps and DEGRADED unreadable-file findings under a `skill-hardening` label, no change to the audit's exit code); `workflows/close-out/` [[workflows/close-out/CONTEXT]] hard-fails on a `skill-hardening` WARN only, with DEGRADED surfaced but non-blocking (the deterministic gate).

## Known Issues
- The check validates presence and shape only, never the truth of the declared policy: a Hardening section with all five fields filled in but describing the wrong blast radius still passes. Correctness of the declaration stays a human/AI review judgement, exactly as doc-sync validates that a Revision History entry exists without judging its prose.
- The field names are matched as fixed strings (both `**Field:**` and `**Field** -` forms). A renamed or misspelled field label reads as missing - intended strictness, but it depends on authors following the template. `REQUIRED_FIELDS` is a hardcoded copy of the template's labels; the drift test in `tests/` [[workflows/skill-hardening-guard/tests/CONTEXT]] asserts the two agree, so a template field change that is not mirrored here fails a test rather than silently passing stale skills.
- The guard is deliberately no-git, so it does not consult `.gitignore`. A gitignored SKILL.md is only skipped if it sits under a hidden, `_`-prefixed, `archived/`, or standard non-project directory; a gitignored skill in an otherwise-normal path would still be checked. The `_`-prefix convention is the portable "local/out-of-scope" signal.

## Revision History
- 2026-07-09 - Initial creation. The read-only checker: skill discovery under skills/ and workflows/ (self/archived/template excluded), Hardening-section extraction, five-field presence/empty/placeholder validation, and the `--check` / `--json` / `--strict` CLI. Umbrella Bucket-1 child #6, Deliverable B.
- 2026-07-09 - Reworked `find_skill_files` to prune skipped/hidden/archived/self directories with `os.walk` before descending, rather than `rglob` then filtering matches after the fact, so the walk no longer enters ignored or source-data subtrees (Codex review fix).
- 2026-07-09 - Code-review fixes (child #6): `_field_content` now reads wrapped continuation lines (a multi-line field value is no longer judged empty) and accepts both `**Field:**` and `**Field** -` label forms; added `strip_code_blocks` so a fenced `## Hardening` example cannot be mistaken for the real section; a read/decode error is now DEGRADED (non-blocking) rather than WARN; `find_skill_files` also prunes `_`-prefixed dirs; and `_is_unfilled` flags a bare `<stub>` that is the whole field value while leaving an angle token embedded in real prose (e.g. `reviews/<label>.md`) alone.
- 2026-07-10 - Extended to also enforce the `## Verification` section (umbrella Bucket-1 child #7): generalised `extract_hardening_section` into `extract_section(text, heading)` (with a back-compat wrapper), and `check_skill` now checks both required load-bearing sections independently - Hardening (five fields) and a non-empty `## Verification` - reporting both under the one `skill-hardening` label so the audit hook and close-out gate pick up a missing Verification section with no extra wiring. The exact-heading match prevents the `Verification / escape hatch` Hardening field from satisfying the section check.
- 2026-07-10 - Codex review follow-up (child #7): brought the CLI wording in step with the behaviour - the module docstring's "Reports" block and the argparse `description` now name the `## Verification` section alongside Hardening (they previously described Hardening only). No logic change.
- 2026-08-04 - Docstring correction in `run.py`, no behaviour change: the module docstring's opening line said every SKILL.md must carry a complete Hardening section, omitting the Verification section the same docstring documents ten lines further down and the code enforces. The opening line now names both.
- 2026-08-04 - Consumers entry corrected to match the severity contract Outputs above already stated: it described the audit hook as merging WARN findings and close-out as hard-failing on any WARN, with no mention of the DEGRADED unreadable-file finding that is surfaced but never blocks. One file, two sections, two different answers about the same severities. No code or test change.
