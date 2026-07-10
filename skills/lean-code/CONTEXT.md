# Lean Code (Skill Set)

**Last modified:** 2026-07-09

## Purpose
Shared skill set that steers AI coding agents toward the smallest correct solution, adapted from the Ponytail project (github.com/DietrichGebert/ponytail) but built as AI-agnostic Book Dragon skills with no external dependencies. It is opt-in per project: most Book Dragon work is documentation rather than code, so the ruleset is applied only where a project asks for it, and it targets external or app code. Inside Book Dragon the 90-10 Protocol already covers this ground.

Only the always-on ruleset is built so far. The diff review, repo audit, and debt-tracking skills are deferred until they are actually needed (see Contents and the backlog).

## Contents
- SKILL.md - `skills/lean-code/SKILL.md` [[skills/lean-code/SKILL]] - The Lean Code ruleset: the decision ladder, the three intensity modes, the tag vocabulary, the `lean:` marker convention, the safety carve-out, and how a project opts in. This is the active deliverable.
- lean-review (planned, not built) - diff-focused over-engineering review. Deferred; build only when needed.
- lean-audit (planned, not built) - whole-repo bloat scan. Deferred; build only when needed.
- lean-debt (planned, not built) - `lean:` marker tracker. Deferred; build only when needed.

The three planned skills do not exist yet. If diff review, repo audit, or debt tracking is needed, build them first (see the backlog item "Lean Code skill set - review/audit/debt skills") before relying on them.

## Inputs
- The target project's code or diff, provided at use time by the user or the AI session.
- A chosen mode (lite / full / ultra); defaults to full when a project opts in without naming one.

## Outputs
None. The ruleset shapes how the AI writes and reviews code in a session (leaner suggestions, ladder-based decisions, and `lean:` markers on deliberate minimal choices); it produces no files of its own.

## Steps
N/A. This is a ruleset applied by the AI, not a standalone workflow with a script. The future review, audit, and debt skills will each carry their own steps when built.

## Dependencies
- `AGENTS.md` [[AGENTS]] (root) - Governs UK English, the CONTEXT.md schema, and the shared-skill conventions this directory follows.
- The 90-10 Protocol in `AGENTS.md` [[AGENTS]] - The in-project minimisation mindset that Lean Code defers to inside Book Dragon and extends to external code.

## Known Issues
- The review, audit, and debt skills are not built yet, so the set is ruleset-only for now. The diff review and repo audit overlap with Claude Code's own `/simplify` and `/code-review`; the deferred skills are planned to be AI-agnostic and stack-aware (Next.js/React/TypeScript) and to wrap existing tooling (knip, depcheck, ts-prune) rather than reinvent it.
- Opt-in is a documented convention (a `lean-code: <mode>` declaration or a verbal trigger), not a machine-read config file. A config file comes later, alongside the skills that consume it, to avoid building a parser nothing reads yet.

## Revision History
- 2026-06-26 - Initial creation. Built the always-on Lean Code ruleset (decision ladder, three modes, tag vocabulary, `lean:` marker convention, safety carve-out, opt-in convention). Review, audit, and debt skills deferred to a build-only-when-needed backlog item.
- 2026-07-09 - Added the required Verification and Hardening sections to SKILL.md (umbrella Bucket-1 child #6). Verification frames correctness as vocabulary conformance plus an intact safety carve-out; Hardening documents that the ruleset invokes no tools and writes nothing.
