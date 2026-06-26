# Skills

**Last modified:** 2026-06-26

## Purpose
Parent directory for all shared skills in Book Dragon. A skill lives here once it is needed by more than one workflow. Each skill gets its own subdirectory with a SKILL.md spec and, where applicable, its own scripts.

## Contents
- Web Research — `skills/web-research/` [[skills/web-research/CONTEXT]] — Fetches and packages web research from multiple sources; importable by any workflow.
- Image Prompt — `skills/image-prompt/` [[skills/image-prompt/CONTEXT]] — Analyses written content and produces an image recommendation (real photo, stock photo, or AI-generated) plus the appropriate output for the chosen path.
- Lean Code - `skills/lean-code/` [[skills/lean-code/CONTEXT]] - Opt-in ruleset that steers AI coding agents toward the smallest correct solution (decision ladder, three intensity modes, tag vocabulary, `lean:` marker). Ruleset built; the diff-review, repo-audit, and debt-tracking skills are deferred until needed.

## Inputs
None. Individual skill subdirectories define their own inputs.

## Outputs
None. Individual skill subdirectories define their own outputs.

## Steps
N/A. This is a container directory, not a workflow itself.

## Dependencies
- `AGENTS.md` [[AGENTS]] (root) — Defines the shared skills convention, the CONTEXT.md schema, and the promotion rules for workflow-scoped skills.

## Known Issues
- The Contents section of this file must be updated every time a new skill is added or removed.

## Revision History
- 2026-05-29 — Initial creation. Added web-research as the first shared skill.
- 2026-05-29 — Added image-prompt as the second shared skill.
- 2026-06-09 — Dependencies updated from CLAUDE.md to AGENTS.md (AI-agnostic transition).
- 2026-06-26 - Added lean-code as the third shared skill (ruleset only; review/audit/debt skills deferred).
