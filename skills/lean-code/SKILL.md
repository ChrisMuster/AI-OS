# Lean Code - Skill Specification (Ruleset)

**Version:** 1.0
**Last modified:** 2026-06-26

---

## Purpose

Lean Code steers an AI coding agent toward the smallest correct solution. The guiding idea, borrowed from Ponytail, is that the best code is the code you never wrote: prefer deletion over addition, reuse over rewriting, and the platform's own features over new dependencies. It applies to general coding work (apps, websites, freelance, interview exercises), not to Book Dragon's own documentation, where the 90-10 Protocol already does this job.

This file is the always-on ruleset. The action skills that use it (diff review, repo audit, debt tracking) are not built yet; see the directory CONTEXT.md.

---

## How this is used in practice

Two ways, and they work together:

1. **You kick it off once per project.** Opting in (see below) is the only manual switch. You do it once.
2. **After that, the agent applies it automatically within that project or session.** It keeps the ladder in mind while writing and suggesting code, and marks deliberate minimal choices with a `lean:` comment. It is not a background process and nothing runs on its own; it is guidance the agent follows once a project has opted in.

It is deliberately not on for everything. Most Book Dragon work is documentation, and the 90-10 Protocol already governs the project's own scripts, so applying Lean Code everywhere would be noise. Opt in the repos where it earns its keep.

The planned review, audit, and debt skills will be the explicit, you-kick-it-off actions (for example, run a review over this diff). This ruleset is the passive layer that shapes code as it is written.

---

## How to switch it on

Declare a mode where the agent will see it: a line in the project's agent-instructions file (AGENTS.md, CLAUDE.md, etc.), a note at the top of a task, or simply telling the agent "lean-code full":

    lean-code: full

If a project opts in without naming a mode, default to `full`. To switch it off, do not opt in, or say "lean-code off".

There is deliberately no config-file parser yet. Nothing reads one until the review, audit, and debt skills exist, and building a parser before then would break the ladder below at step 1.

---

## The decision ladder

Apply this after understanding the problem, never instead of it. Work down the rungs and stop at the first that meets the need:

1. Does this need to exist at all? If not, skip it (YAGNI).
2. Is it already in this codebase? Reuse it; do not rewrite it.
3. Does the standard library do it? Use it.
4. Is there a native platform or language feature for it? Use it.
5. Does an already-installed dependency do it? Use it.
6. Is it one line? Make it one line.
7. Only then write new code, and only the minimum that works.

### Safety carve-out (never minimised away)

The ladder trims scope and cleverness, not correctness. These are never cut to save lines, and never count as over-engineering:

- Input validation and error handling
- Security controls (authentication, authorisation, escaping, secrets handling)
- Accessibility (semantic markup, labels, keyboard support, contrast)
- Tests that cover real behaviour

If applying the ladder would weaken any of these, stop and keep the safer version.

---

## Intensity modes

Three modes, in increasing strictness. They change how hard the agent pushes, not what the ladder says. The safety carve-out applies identically in all three.

| Mode | Behaviour |
|------|-----------|
| `lite` | Advisory. Point out a leaner alternative when one is obvious, but follow the user's chosen approach without insisting. |
| `full` (default) | Active. Apply the ladder while writing. Flag any added code that skips a rung and recommend the minimal version. Question new dependencies. |
| `ultra` | Strict. Any new dependency, abstraction, or speculative just-in-case code must be justified or refused. Demand the one-line version. Treat non-minimal choices as defects to fix, not preferences. |

---

## Finding tags

When reviewing or flagging code (now, by hand; later, via the review and audit skills), classify each finding with one tag. This vocabulary is the single source of truth that the future skills inherit:

| Tag | Meaning |
|-----|---------|
| `delete` | Dead or unreachable code, or code that serves no current need. Remove it. |
| `stdlib` | A reinvented wheel the standard library already provides. Replace it. |
| `native` | An unnecessary dependency for something the platform or language does natively. Drop the dependency. |
| `yagni` | A premature abstraction, generalisation, or feature added before it is needed. Collapse it to the concrete case. |
| `shrink` | Correct but verbose logic that a simpler equivalent replaces. Tighten it. |

One-line finding format the future review skill will use: `file:line - <tag> - <what to cut> -> <what replaces it>`.

---

## The `lean:` marker

When you make a deliberately minimal choice that a future reader might mistake for a gap, mark it in a comment so the decision is visible and, later, trackable:

    # lean: browser has a native date picker, no library needed

Extended form, when the minimal choice has a known ceiling and an upgrade path:

    # lean: in-memory dict is enough at this scale; swap for a store past ~10k keys

Format: `lean: <why this is the minimal choice>[, <upgrade trigger and path>]`. Use the comment syntax of the host language. No tracker reads these yet; the convention is fixed now so markers written today are ready when the `lean-debt` skill is built.

---

## Relationship to other tools

- Inside Book Dragon, defer to the 90-10 Protocol in AGENTS.md; do not apply Lean Code to documentation or to workflow scripts the Protocol already governs.
- Claude Code's own `/simplify` and `/code-review` overlap with the planned diff-review skill. Lean Code's distinct value is being AI-agnostic, opt-in per project, and (later) tracking deliberate shortcuts. Use whichever is to hand; they are complementary.

---

## Not built yet

The diff-review, repo-audit, and debt-tracker skills do not exist. If a task needs them, build them first (see the backlog item "Lean Code skill set - review/audit/debt skills"), then continue. Do not assume they are available.

---

## Verification

Because this is a passive ruleset rather than a script, a correct result means the guidance was applied faithfully: any findings raised use the fixed tag vocabulary (`delete`, `stdlib`, `native`, `yagni`, `shrink`) in the one-line finding format, `lean:` markers follow the fixed marker format, and the safety carve-out is intact.

A failed check looks like a "leaner" suggestion that removes input validation, security controls, accessibility, or real test coverage; a finding tagged outside the vocabulary; or the ruleset being applied to Book Dragon's own docs and scripts, which the 90-10 Protocol already governs. If applying a rung would weaken correctness or security, the rule itself is to stop and keep the safer version.

---

## Hardening
Safety envelope for this skill. All five fields are required.

- **Allowed tool intent:** None of its own. It is passive guidance that shapes how the agent writes code in an opted-in project; it invokes no tools and runs no scripts.
- **Never:** Apply itself to Book Dragon's own documentation or workflow scripts (defer to the 90-10 Protocol); weaken the safety carve-out (validation, security, accessibility, tests) to save lines.
- **Approval-gated:** None. It is advice only; any code changes it influences go through the host task's own permission and review flow.
- **Write boundaries:** None. The ruleset writes nothing; code edits happen under the host task's normal rules, not this skill's.
- **Verification / escape hatch:** A reviewer confirms findings use the fixed vocabulary and the safety carve-out was preserved (see Verification). If applying a rung would weaken correctness or security, the rule is to stop and keep the safer version rather than minimise.
