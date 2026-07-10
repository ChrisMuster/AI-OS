# Skill-Hardening Guard

**Last modified:** 2026-07-09

## Purpose
Makes the SKILL.md `## Hardening` section a checked obligation rather than discipline alone. It is a deterministic, read-only checker in the guard family (same shape as `encoding-guard`, `personal-data-guard`, `ai-style-guard` [[workflows/ai-style-guard/CONTEXT]], and `doc-sync-guard` [[workflows/doc-sync-guard/CONTEXT]]): a standalone CLI that the full audit and the close-out verifier consume. For every SKILL.md under `skills/` [[skills/CONTEXT]] and `workflows/` [[workflows/CONTEXT]], it verifies a `## Hardening` section exists and that all five required fields (`Allowed tool intent`, `Never`, `Approval-gated`, `Write boundaries`, `Verification / escape hatch`) are present and non-empty.

The Hardening section is a *declarative* safety envelope: the smallest correct blast radius a skill needs, written down so it is inspectable. It is documented intent, not runtime enforcement. Runtime per-skill tool restriction is not portable across Book Dragon's AI-agnostic markdown-skill model (Claude-native `.claude/skills` now expose partial runtime controls - `disallowed-tools`, skill-scoped `hooks`, `context: fork` - but adopting them as the mechanism would be Claude-only and break cross-AI parity). So the portable, checkable deliverable is the declarative section plus this presence/shape check. The guard validates structure, never the truth of the declared policy - a wrong-but-present Hardening section still passes, exactly as doc-sync validates that a Revision History entry exists without judging its prose. There is deliberately no fix mode: the author knows the skill's real blast radius; a context-free script could only produce filler.

## Contents
- scripts/ - `workflows/skill-hardening-guard/scripts/` [[workflows/skill-hardening-guard/scripts/CONTEXT]] - The `run.py` entry point (read-only `--check`, with `--json` and `--strict`) and its pure parser/checker functions.
- tests/ - `workflows/skill-hardening-guard/tests/` [[workflows/skill-hardening-guard/tests/CONTEXT]] - 23 hermetic tests for the parser/checker and skill discovery over a throwaway tree, plus a template-drift guard and a real-guard subprocess smoke test.

## Inputs
- The on-disk SKILL.md files under `skills/` [[skills/CONTEXT]] and `workflows/` [[workflows/CONTEXT]] (read as UTF-8). No git, no network, no config file - it is a whole-tree structural invariant, not a diff-scoped content check, so it reports the same result regardless of what changed.

## Outputs
- A findings report on stdout (human text, or `--json` for the audit hook). All findings are WARN under a `skill-hardening` label.
- Exit code 0 by default (advisory). With `--strict`, exit 1 when any WARN exists, so a caller can gate on it.
- No files are written and no LOG.md entry is made on a check run (read-only, like the other content guards).

## Steps
1. Scan every SKILL.md for a complete Hardening section (read-only):
   `python workflows/skill-hardening-guard/scripts/run.py --check [--json] [--strict]`
2. Review any WARN findings and fill in the flagged Hardening section or field (the guard reports the file and the gap; it never edits).
3. Re-run until the report is clean.
4. Append LOG.md only when run as a deliberate, logged workflow step (the read-only check and the audit-hook invocation do not log).

## Dependencies
- `AGENTS.md` [[AGENTS]] (root) - Defines the SKILL.md schema and the required Hardening section (five fields) this guard enforces.
- `templates/` [[templates/CONTEXT]] - The SKILL.md.template whose Hardening field labels the checker keys on; if the template's labels change, the guard's `REQUIRED_FIELDS` must change with them.
- The Python 3.9+ standard library only (no third-party packages, so no `.venv` bootstrap is needed).
- Consumers (both live): `workflows/audit/` [[workflows/audit/CONTEXT]] runs it as an advisory hook, merging its findings as WARN under a `skill-hardening` label without changing the audit's own exit code; `workflows/close-out/` [[workflows/close-out/CONTEXT]] turns any `skill-hardening` WARN into a hard fail (the deterministic gate).

## Known Issues
- The check enforces presence and shape (the five fields exist and are non-empty), not the correctness of the declared policy. A Hardening section describing the wrong blast radius still passes; correctness stays a human/AI review judgement.
- Field names are matched as fixed strings drawn from the template (both `**Field:**` and `**Field** -` forms accepted). A renamed or misspelled label reads as a missing field - intended strictness. `REQUIRED_FIELDS` is a hardcoded copy of the template's labels, kept honest by a drift test that fails if the two diverge rather than by runtime derivation.
- Retired skills under an `archived/` path are excluded, so an archived SKILL.md is not held to the live schema. If an archived skill is ever reactivated it must gain a Hardening section as part of that move.
- Deliberately no-git: `.gitignore` is not consulted, so a gitignored skill is only skipped when it sits under a hidden, `_`-prefixed, `archived/`, or standard non-project directory. A leading `_` is the portable out-of-scope signal for a local/scratch skill.

## Revision History
- 2026-07-09 - Initial creation (umbrella Bucket-1 child #6, Deliverable B): the read-only checker (`scripts/run.py`) that verifies every SKILL.md carries a `## Hardening` section with all five required fields non-empty; 13 hermetic tests. Wired into the full audit as an advisory `skill-hardening` WARN hook and into the close-out verifier as a hard-fail gate. Paired with Deliverable A (the Hardening section added to the SKILL.md schema and retrofitted across all 7 existing skills).
- 2026-07-09 - Codex review fix: `find_skill_files` now prunes skipped/hidden/archived/self directories with `os.walk` before descending (was `rglob` + post-match filtering), so the walk never enters ignored or source-data subtrees; added a 14th regression test.
- 2026-07-09 - Code-review fixes (child #6): parser now reads multi-line field values and accepts both label forms, strips fenced code blocks before matching, treats an unreadable SKILL.md as non-blocking DEGRADED (not a blocking WARN), prunes `_`-prefixed dirs, and flags a whole-value `<stub>` without false-flagging an embedded angle token; the close-out gate was generalised to a `BLOCKING_LABELS` map (doc-sync + skill-hardening) so a third blocking label is a one-line add; tests grew to 23 (guard) with a template-drift guard and a real-guard smoke test, and the audit hook gained a producer/consumer contract test.
