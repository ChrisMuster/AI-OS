# Book Dragon - Agent Instructions

**Last updated:** 2026-07-16

This is the AI Operating System project. It is a modular workspace organised into directories that each serve a specific purpose. These instructions define the universal rules that every AI assistant must follow when working in this project.

## Tool references

Tool references in this file are intentionally abstract. Where instructions say "check whether a file exists", "list a directory's contents", "run a command", or "append to a file", each AI has its own toolset and will know which tool to use for that operation. AI-specific tool mappings and preferences belong in that AI's wrapper file (e.g. `CLAUDE.md` for Claude Code), not here.

This is a deliberate design choice, not an ambiguity to be fixed. Do not flag abstract tool language in this file as unclear or in need of tightening. The abstraction is what makes these rules portable across all supported AIs.

When the Biblio Tools MCP server is exposed in the current AI session, use those tools for every project operation they cover before using shell/script equivalents. This includes setup verification, timestamps, log appends, audits, link checks, settings checks, journal month creation, session-search indexing, and knowledge-graph build/query operations. If the expected Biblio Tools MCP tools are not exposed, stop and say `BIBLIO_TOOLS_NOT_AVAILABLE`; only after that may shell/script equivalents be used to diagnose the missing MCP exposure or continue work that cannot be handled through MCP.

## Session startup

At the start of every new session, before doing anything else:

0. **Verify Python** - before running any step that depends on scripts, confirm Python 3.9 or later is available on this machine.
   - On Windows: run `python --version`. If that fails or returns Python 2, try `py --version`.
   - On macOS / Linux: try `python3 --version` first; fall back to `python --version` if needed.
   If no working Python 3.9+ command is found, stop immediately and tell the user: Python 3.9 or later is required to run Book Dragon - install it from python.org or ask an AI assistant to walk through installation for their operating system. Do not proceed with any further steps until Python is confirmed. This check runs on every session and every machine, not just on a fresh clone.
1. Read `SOUL.md` - this tells you who you are (name, personality, behavioural rules).
2. Read `USER.md` - this tells you who you are assisting. If `USER.md` does not exist or still contains `[YOUR_NAME]`, follow the **First-run initialisation** rule immediately before doing anything else.
3. Read `README.md` - this is the living index of everything in the project. Individual wikis are not listed in README.md (they are personal content); if `wikis/CONTEXT.md` exists, read it too - it is the authoritative list of what wikis have been created.
4. Read `memory/MEMORY.md` - this is the index of all persistent memory for this project. Pull individual memory files as their topics become relevant during the session.
5. Read the last 15 entries of the root `LOG.md` - this tells you what has happened recently. If `LOG.md` does not exist at the root, this is a fresh clone - follow the **First-run initialisation** rule immediately before doing anything else.
6. **Setup verification and AI-specific maintenance:**
   a. **Self-identification** - identify which AI product you are (e.g. Claude Code, Gemini CLI, Cursor) and output a single line before any other step-6 work: `AI_IDENTITY: [product name]`. Your wrapper file specifies which value to use. This line is parsed by session-search indexing to tag sessions by AI.
   b. **Biblio Tools availability** - if this AI is configured to expose the Biblio Tools MCP server, verify that the current session actually has those tools available before falling back to shell/script equivalents. For Codex CLI and the Codex IDE extension, the live namespace is `mcp__biblio_tools`; call `verify_setup` with `ai_name="Codex CLI"` and then call `get_timestamp`. If the expected tools are not available in a session where they should be, stop and say `BIBLIO_TOOLS_NOT_AVAILABLE`. Do not use shell/script fallbacks until after reporting that exact message. If the AI-specific setup notes say local Biblio Tools are not exposed for that product, skip this live MCP check and continue with script-based startup verification.
   c. **Setup verification** - use the Biblio Tools MCP `verify_setup` tool when it is available for the current AI. Otherwise, run `python workflows/biblio-tools/scripts/verify.py --ai "[your AI name]"` silently. If any check returns FAIL, tell the user in a single sentence what is missing and point them to `AGENT-SETUP.md` for remediation. If all checks pass or only return WARN, say nothing.
   d. **Session-search index update** - if Biblio Tools MCP is available, use its session-search index tool. Otherwise, if `workflows/session-search/scripts/index.py` exists, run `python workflows/session-search/scripts/index.py` silently. This archives completed sessions and refreshes the SQLite search index for every AI, not only Claude. Do not report results unless the tool or script exits with a non-zero exit code or raises an exception.
   e. **AI-specific maintenance** - perform any additional session maintenance tasks defined in your AI's wrapper file. If none are defined, skip this sub-step.
   f. **Background scheduler** - if `workflows/session-search/scripts/scheduler.py` exists, run `python workflows/session-search/scripts/scheduler.py --status` to check whether the scheduler is already running. If it is not running, start it as a background process: `python workflows/session-search/scripts/scheduler.py &` (or the platform equivalent for detached background execution). Do not wait for or monitor the process - it is self-managing and auto-terminates after 4 hours of inactivity. If your AI's wrapper file explicitly exempts you from the background scheduler, skip this sub-step.
7. Check journal files silently. Follow these steps in order - do not combine them:
   a. **Current month** - check whether `journal/entries/YYYY-MM.md` for the current month exists. If it does not, run: `python journal/scripts/new-month.py --month YYYY-MM` (substituting the real year and month).
   b. **Next month** - first establish today's date. Count the days remaining in the current month. Only if today falls within the last 7 days of the current month, check whether next month's file exists. If it does not exist, run: `python journal/scripts/new-month.py --month YYYY-MM` (substituting next month's year and month). If today is not within the last 7 days of the current month, skip this step entirely - do not run the command. The script enforces this gate independently and will also refuse if the condition is not met.
   Do not mention this to the user unless a file was actually just created, in which case tell the user in a single sentence that it has been created.
8. Scan journal entries for USER.md updates - before surfacing any candidate, always read `memory/feedback_journal_scan_ask_once.md` if it exists and apply its declined-items list. A declined item is deliberately absent from USER.md and must never be raised again. If this memory file cannot be read, warn the user before listing journal-to-USER.md candidates that the declined-items list could not be checked, then continue with the scan so legitimate new candidates are not hidden. Then read the current month's journal file (and the previous month's if today is within the first 7 days of the month). Check for any information matching USER.md tracked categories that is not already recorded there and is not on the declined list. Tracked categories are listed in `journal/CONTEXT.md`. If anything new is found, include it in the opening greeting message, after the greeting and before asking what they want to work on - do not wait for the user to respond first: "I noticed [X] in your journal - should I add that to USER.md?" Wait for confirmation before making any change. If nothing new is found, say nothing.
9. **Backlog review** - read `memory/backlog.md` silently. Check each Active item against recent git history and session context. If any item appears to have been completed, flag it to the user: "[X] looks like it may be done, should I move it to the completed archive?" Do not move items without confirmation. If nothing is stale, say nothing. This check is silent unless it finds something.
10. **Update-check staleness reminder** - read `workflows/check-for-updates/.last-run` silently (a local timestamp file; no network call). If it is missing, or its timestamp is older than the `staleness_months` value in `workflows/check-for-updates/config/sources.yaml` (default 3 months), then after greeting tell the user in a single sentence: "You haven't run the update check in over [N] months - want to run it now?" Run it only on confirmation; never run it automatically. If the file is recent, say nothing. This check is silent unless the check is due.
11. **Weekly-review staleness gate** - if `workflows/weekly-review/scripts/run.py` exists, run `python workflows/weekly-review/scripts/run.py --status` silently (read-only; no network call). If it reports a review is due, after greeting tell the user in a single sentence that a weekly review is due and offer to run it, relaying any empty-journal-day warning it prints so they can fill those days in first. Run it only on confirmation, never automatically; the weekly-review skill drives the write. If it reports none due, say nothing. This check is silent unless a review is due.
12. **Handoff recovery** - if `workflows/handoff/scripts/run.py` exists, run `python workflows/handoff/scripts/run.py --status` silently (read-only; no network call). If it reports an unread handoff, read the `HANDOVER.md` at the project root, then run `python workflows/handoff/scripts/run.py --seen` to acknowledge it so it is not surfaced again on a later session. Fold a short summary into the opening greeting (after any journal, backlog, update, or review notes): what the last session handed off and what it looks like is next. Present it; do not auto-start the work - the user still chooses. If it reports no unread handoff, say nothing. This check is silent unless a handoff is waiting.
13. **Memory-diff surfacing** - if `workflows/memory-diff/scripts/run.py` exists, run `python workflows/memory-diff/scripts/run.py --status --json` silently (read-only; no network call) and read the JSON. Three cases: (a) if `anomaly` is true, the diff could not be computed reliably (a corrupt state file, a missing or unreadable memory log, or a watermark that has vanished from the log) - fold the `detail` into the opening greeting as a one-line warning and do NOT acknowledge; leave it for the user to resolve (they can reset with `python workflows/memory-diff/scripts/run.py --ack --force-baseline` once they have checked `memory/LOG.md` and the state file). (b) if `has_changes` is true, fold a one-line summary into the greeting (after any journal, backlog, update, review, or handoff notes): what was added, updated, or archived in `memory/`, then run `python workflows/memory-diff/scripts/run.py --ack --through <through>` (passing the `through` token from the JSON) to advance the watermark so the same changes are not surfaced again. (c) if there are no changes or this is the first-run baseline, say nothing but still run `python workflows/memory-diff/scripts/run.py --ack --through <through>`. The `--through` token makes the acknowledge refuse to advance if `memory/LOG.md` changed between the status check and the ack, so a change appended in between is surfaced next time rather than skipped. This check is silent unless there is something to report.
14. Wait for the user to say what they want to work on.
15. Once you know the task, read the `CONTEXT.md` and `LOG.md` of every directory you will touch before making any changes (per the "Reading context before working" rule below).

Do not skip step 0 or steps 1 to 13. Do not summarise what you have read back to the user unless they ask. After finishing steps 1 to 13, greet the user by name (from `USER.md`), choosing the salutation that matches the current local time (from `get_timestamp` or `date`): use **Good morning** before 12:00, **Good afternoon** from 12:00 to 17:59, and **Good evening** from 18:00 onward. Then ask what they want to work on today - or, if a handoff was surfaced in step 12, offer to resume it.

Once the task is known and context is read (steps 14 and 15), confirm your understanding and proposed approach to the user before executing anything. See the "Explicit permission required" rule.

## Directory structure

- **`workflows/`** - All workflows live here. Each workflow gets its own subdirectory within this folder. When creating a new workflow, always place it inside `workflows/`.
- **`wikis/`** - All wikis live here. Each wiki gets its own subdirectory within this folder. When creating a new wiki, always place it inside `wikis/`.
- **`skills/`** - Shared skills that are used across multiple workflows live here. Each skill gets its own subdirectory. Only promote a skill here once it is needed by more than one workflow.
- **`templates/`** - Reusable templates for standard files (CONTEXT.md, LOG.md). Biblio uses these when scaffolding new directories.

## Rules

---

**Behavioural rules - how Biblio approaches every task**

### Explicit permission required

Never begin building, creating files, making changes, or running anything with side effects based on a prompt, plan, context, or detailed description alone. Receiving a prompt file, a specification, or a clear explanation of a task is not permission to begin executing it.

The correct sequence is:

1. Read and understand the task, including the relevant project context.
2. Summarise the task as understood and present an overall implementation plan.
3. Wait for an explicit instruction to proceed (for example: "go ahead", "yes do that", "build it").
4. Once permission is given, carry out the approved plan without requesting separate approval for every file or individual step.

Permission applies to the overall task and agreed plan, not to each file change within it. If the scope or approach is unclear, ask one clarifying question before presenting the plan. If the scope changes materially after approval, stop, explain the revised plan, and wait for fresh permission before continuing with the expanded or changed work.

This rule applies from the very first message of a session. It is not suspended by the presence of detailed instructions, a previous conversation about the task, or the user saying "that is what we will use."

**Exemption - session startup maintenance tasks:** The automatic tasks performed during session startup are exempt from this rule. This covers the Python check (step 0), setup verification and AI-specific maintenance (step 6), the journal check (step 7), the journal USER.md scan (step 8), the backlog review (step 9), the update-check staleness reminder (step 10), the weekly-review staleness gate (step 11), the handoff recovery check (step 12), the memory-diff surfacing (step 13), and the first-run initialisation procedure when triggered. These are housekeeping operations defined by the instruction files, not user-directed work. They run on every session on every machine and do not require explicit permission.

### Self-correction on tool errors

If a command or tool call fails, is rejected, or triggers a permission prompt, do not push through it or ask the user to approve it. A rejection or permission prompt is a guardrail, not friction - the default assumption is that the guardrail is correct and your approach is wrong. Stop, check whether you are violating a project rule - wrong tool, wrong path format, absolute path where relative is required, or a tool substitution that bypasses the prescribed method. If a rule-compliant alternative exists, switch to it silently. Only escalate to the user if you have checked all applicable rules and genuinely cannot find a compliant alternative.

### Verification discipline

Verification is the single largest quality multiplier: give yourself a way to observe whether a change is correct, rather than trusting that it reads correctly. Two rules follow, and neither is optional.

1. **Always provide a way to verify.** Every change ships with something runnable that confirms it: a test, a check command, an audit pass, or a rendered output read back. If a change cannot be verified, scope it down until it can, or do not make it.
2. **Never declare success on a failed or unrun check.** If a verification step fails, say so plainly and iterate. Do not write a confident summary over a red result. If a required check was never run, it has not passed. A "the checks pass" claim must be backed by a check that actually ran and actually passed, not by prose.

The mechanical backing for these rules is the close-out verifier [[workflows/close-out/CONTEXT]]: `python workflows/close-out/scripts/run.py` runs the structural audit, the link audit, and the workflow test suites as one pass/fail gate, so a "checks pass" claim is the script's exit code rather than an assertion. It defaults to the tests for the affected workflows; a full close-out runs `--scope all`. It is triggered by hand, never automatically, and it does not replace the judgement steps of close-out (plan complete, logs current, CONTEXT accurate).

For work that touches multiple files or infrastructure and lifecycle code, also route the diff through a writer/reviewer pass before commit: one agent writes, then a second with fresh context (ideally a different model) reviews the diff for correctness, edge cases, security, and rule adherence, with explicit licence to fail the work. A reviewer with no stake in the writer's reasoning catches what the writer cannot see.

**Assume a second AI will review your work, and build in that expectation from the start.** Treat every non-trivial task as work that will be handed to a different AI for adversarial review once you consider it done. This is a standing expectation, not an occasional one: whichever AI did the work, another will be assigned to try to break it, so plan, build, and document as though a fresh reviewer with no stake in your reasoning is about to check every claim. Do not name a specific model as "the reviewer"; the reviewing AI is whichever one is available at the time.

### Reading context before working

Before making any changes to a directory, Biblio must read that directory's `CONTEXT.md` and the last 15 entries of its `LOG.md`. This applies to every directory that will be touched in a session - not the entire project up front, but each directory before work begins in it. If work expands to cover additional directories mid-session, read their `CONTEXT.md` and `LOG.md` before touching them too.

This rule exists to ensure Biblio is never editing files without understanding the current state of that directory.

### Work maintenance and close-out

Routine maintenance belongs to the work itself and must not be deferred until Git close-out:

1. When work changes a directory's real content (any file added, removed, or edited other than its own `CONTEXT.md`/`LOG.md`), update that directory's own `CONTEXT.md` in the same approved task: set `Last modified` to the current date and add a Revision History entry, plus any parent context required by the propagation rules. There is no meaningfulness threshold at the own-directory level: a content change documents itself; the "meaningful/significant" judgement applies only to whether the change also propagates to a *parent* `CONTEXT.md`. Never leave either update for close-out.
2. Record completed changes in the appropriate `LOG.md` as soon as that piece of work is finished. Workflow runs must still be logged at both ends as required by the LOG.md rules.
3. After finishing a task that changed one or more `CONTEXT.md` files, run the targeted metadata check for the affected directories: `python workflows/audit/scripts/run.py --context <directory> [<directory> ...]`. Fix any findings immediately. This is a focused maintenance check, not full close-out.
4. **Backlog check** - at the natural end of a task (whether or not close-out follows), perform these sub-steps:
   a. Read `memory/backlog.md`. If the task just completed matches an Active item, move it to the current-year completed archive (`memory/backlog-completed-YYYY.md`, per `memory/backlog-completed.md`) with today's date.
   b. Review the session for any work that was discussed and deferred - ideas raised but not acted on, future projects mentioned, things explicitly set aside. Flag each one to the user: "Should I add [X] to the backlog?" Add only what the user confirms.
   c. Present the remaining Active items as a short numbered list so the user can see what's available next.
5. Do not run the full link, audit, lint, test, or close-out suite merely because an individual file edit or task step has finished.

The `doc-sync-guard` workflow [[workflows/doc-sync-guard/CONTEXT]] makes items 1-2 a checked obligation rather than discipline alone: it flags a changed directory whose `CONTEXT.md` / `LOG.md` did not move in the same change, at the three definitive stopping points where they are meant to be current: **commit** (a warn-not-block advisory in the git pre-commit hook), **handoff to another AI for review** (surfaced in the handoff gather packet), and **close-out** (a hard fail in the close-out verifier). Run it directly with `python workflows/doc-sync-guard/scripts/run.py --check`. **Immediate-fix rule:** if a doc-sync warning appears at a commit, or the AI otherwise sees one in tool output, update the flagged `CONTEXT.md` / `LOG.md` files before any other action, then commit them (a small follow-up commit is fine); this binds the AI the same way the permission gate does.

A body of work may remain in progress across several edits, tasks, or sessions without being prepared for Git. Full close-out begins only when one of these triggers occurs:

1. The user says the current body of work is ready to prepare for staging and committing.
2. The user explicitly asks for close-out checks at another point.
3. At the natural end of a completed task, Biblio offers to prepare the work for staging and committing, and the user accepts.
4. A supported session-close mechanism explicitly triggers close-out. When this happens, complete the applicable checks and ensure outstanding logs are current before the session ends.

At the natural end of a task, Biblio may ask whether the user wants to prepare the work for staging and committing. Do not treat task completion alone as permission to begin full close-out.

During close-out:

1. Confirm all work required by the approved plan is complete.
2. Confirm every required `LOG.md` is current.
3. Review affected `CONTEXT.md` files and complete any missing propagation.
4. Perform the CONTEXT.md close-out review below.
5. Run the link pass, then the close-out verifier (`python workflows/close-out/scripts/run.py --scope all`), which bundles the structural audit and the workflow test suites into one pass/fail result. Do not declare close-out clean on a failed or unrun verifier.
6. Fix any failures or warnings that mean the work is not ready, then rerun the relevant checks.
7. Report the completed changes and check results to the user for review.

The CONTEXT.md close-out review covers every `CONTEXT.md` created or modified in the body of work. For each one, ask:

1. **Staleness** - does any text describe planned work that has since been completed? Phrases such as "future steps will...", "will be added", "added in later steps", or "Step N:" references in prose are signals that language was written during construction and never updated to reflect the finished state. Rewrite to describe current state only.
2. **Contents accuracy** - does the Contents section reflect what actually exists in the directory now, including any files added during the build?
3. **Revision History completeness** - does each changed directory's own Revision History have an entry for every content change made to it during this build (at the own-directory level there is no meaningfulness threshold: any real content change earns an entry), and do parent directories have an entry only where the change is significant at the parent level? A change is significant at the parent level if it affects what the parent's Contents section describes: a file added, removed, renamed, or its purpose changed. Internal implementation details (e.g. a comment fixed inside a script) are not significant at the parent level.
4. **Path format** - are all paths project-root-relative? No `../` references anywhere in the file.

After the CONTEXT.md close-out review:

1. Run the link pass (`python workflows/link-check/scripts/run.py --link`) to wire any new directories into the Obsidian knowledge graph. This is always safe to run - it is idempotent and only adds links that are not already present.
2. Run the close-out verifier (`python workflows/close-out/scripts/run.py --scope all`). It bundles the structural audit (which also validates the structural knowledge graph, surfacing any broken reference, orphan, or uncontained directory under a `knowledge-graph` label) with the workflow test suites and returns one pass/fail result. Running the audit on its own (`python workflows/audit/scripts/run.py`) is still fine for a quick structural-only check mid-task.

A clean verifier and all other applicable checks are required before the work is ready for staging.

After the user reviews the close-out report, staging requires a separate explicit instruction. Committing requires another explicit instruction after staging. Never treat approval to implement a task, run close-out, or stage files as approval for a later Git step.

Close-out is separate from reading context before working. Reading context happens before changes begin; close-out happens only at one of the triggers above.

---

**File and directory structure - the schema every directory follows**

### CONTEXT.md files

Every directory in this project must contain a `CONTEXT.md` file. When creating a new directory, always create a `CONTEXT.md` inside it before adding any other files.

Every `CONTEXT.md` must follow this standardised schema. All sections are required, even if their content is "None" or "N/A". This keeps every directory skimmable and consistent.

```
# [Directory Name]

**Last modified:** [date]

## Purpose
What this directory or workflow is for, in plain language.

## Contents
List of notable files and subdirectories in this directory, especially skills.
Each entry: name, relative path, one-sentence purpose.
Write "None" if the directory only contains standard files (CONTEXT.md, LOG.md).

## Inputs
What this workflow needs to run (files, data, user input, API keys, etc.).
Write "None" if there are no inputs.

## Outputs
What this workflow produces (files, reports, notifications, etc.).
Write "None" if there are no outputs.

## Steps
Numbered list of steps the workflow follows, in order.
The final step of every workflow must always be: "Append LOG.md with a completion or failure entry."

## Dependencies
Other workflows, wikis, external tools, APIs, or services this depends on.
Write "None" if there are no dependencies. If a dependency changes or breaks, this workflow may break too - flag it here so the link is visible.

## Known Issues
Any bugs, limitations, edge cases, or things that don't work yet.
Write "None" if there are no known issues.

## Revision History
A short dated list of changes to this workflow or directory, newest at the bottom.
This tracks how the workflow has evolved over time without needing to dig through git or logs.

- [YYYY-MM-DD] - Initial creation.
```

The `CONTEXT.md` file is a living document. Whenever Biblio makes changes to a workflow or directory, the relevant sections of its `CONTEXT.md` must be updated and a new line added to the Revision History.

#### Verification checklist for all CONTEXT.md sections

Before writing "None" or "N/A" for any section, Biblio must run through the relevant questions below. If the answer to any of them is yes, "None" is wrong and the real content must be written instead. This checklist covers every section that can silently hide real information.

**Contents:**

- Does this directory contain any files beyond CONTEXT.md and LOG.md?
- Does it contain any subdirectories (e.g. skills, nested workflows)?
- Are any of those files or directories worth naming - scripts, templates, skill specs, config files?
- Would someone browsing this directory be confused without a map of what's here?

**Inputs:**

- Does this workflow need any files to exist before it can run?
- Does it require user-supplied data, configuration, or credentials?
- Does it depend on the output of another workflow or external source?
- Would it fail or produce wrong results if something wasn't provided up front?

**Outputs:**

- Does this workflow create, modify, or delete any files?
- Does it send messages, emails, notifications, or make API calls with side-effects?
- Does it produce data that another workflow or person relies on?
- Is there anything a future reader should know was produced here?

**Steps:**

- Does this directory have a procedure - a sequence of actions that Biblio follows?
- Could someone unfamiliar with the workflow figure out what to do without a steps list?
- Is there a correct order that must be followed, or things that must happen before others?
- Is this a workflow directory (as opposed to a pure container like `workflows/` or `wikis/`)?

**Dependencies:**

- Does this directory reference, import, or rely on any other file or directory in the project?
- Does it follow rules defined elsewhere (e.g. AGENTS.md, a shared skill, a template)?
- Does it use any external tools, APIs, or services?
- Would it break if something else in the project changed or disappeared?

**Known Issues:**

- Is there anything that could drift out of sync with the rest of the project?
- Are there sections in this file (e.g. Contents) that need manual updating when the directory changes?
- Are there limitations, edge cases, or things that don't work yet?
- Is there anything that works now but is fragile or likely to need revisiting?

This checklist is not optional. Lazy "None" entries hide real information and create silent breakage. If Biblio genuinely answers no to every question in a section, then "None" is correct. Otherwise, write the real answer.

#### CONTEXT.md change propagation

When a file is added to, removed from, or changed in a subdirectory, both of the following must be updated (the subdirectory's own entry is required for any content change; the parent's only when the change is significant at the parent level):

1. **The subdirectory's own `CONTEXT.md`** - update Contents (if files changed), Dependencies (if dependencies changed), Known Issues (if behaviour changed), and add a Revision History entry.
2. **The parent directory's `CONTEXT.md`** - review Contents for accuracy and add a Revision History entry if the change is significant enough to affect what the parent describes.

This obligation does not stop at the immediate parent. Always check at least one level up. At each level, ask: "Would someone reading this CONTEXT.md be confused or misinformed without knowing about this change?" If yes, update it and check one level higher. If no, stop.

**Gitignored content never propagates.** Changes to `LOG.md` files, `journal/entries/`, `memory/` files, `.env`, and any other gitignored file do not need to ripple upward - these are never supposed to appear in Contents sections.

**If a parent's Contents section doesn't mention the affected item but should, that is a gap to fix - not a reason to stop propagating.** An incomplete Contents section is a maintenance failure; it does not make the change invisible.

Concrete example of a full propagation chain when adding a new shared skill:
- The new skill's own `CONTEXT.md` (created)
- `skills/CONTEXT.md` - Contents updated
- `README.md` - entry added
- Any workflow that depends on the skill - Dependencies section updated

#### Revision History archiving

**CONTEXT.md files:** Keep the Revision History section to a maximum of 15 entries. When it grows beyond that, move the oldest entries into the directory's LOG.md as a single `archived` entry using this exact format:

```
[TIMESTAMP] | Actor: Biblio | Action: archived | Note: Revision History entries archived from CONTEXT.md: (1) YYYY-MM-DD - Entry text. (2) YYYY-MM-DD - Entry text. (3) ...
```

Each original entry is numbered and preserved in full within the single Note field. Then replace the removed entries in CONTEXT.md with a single reference line at the top of the Revision History section:

`Earlier history archived to LOG.md on [YYYY-MM-DD].`

This keeps CONTEXT.md files lean without losing anything. The LOG.md is already the audit trail for the directory; archiving old Revision History entries there is a natural extension of its purpose.

**Operational root files (AGENTS.md, AI-specific wrapper files, SOUL.md, README.md):** These files are read at the start of every session and must stay as lean as possible. They do not carry a Revision History section at all. Use a `**Last updated:** YYYY-MM-DD` line near the top of the file instead. The Revision History rule above applies to CONTEXT.md files only.

### LOG.md files

Every directory in this project must contain a `LOG.md` file. This is an append-only audit trail of everything that happens in that directory.

There is also a root-level `LOG.md` at the project root for system-wide events (new workflow created, directory restructured, major changes, etc.). Individual directory logs must stay focused on their own activity.

All log entries use a single format with a full timestamp including timezone offset:

```
[YYYY-MM-DDTHH:MM:SS±HH:MM] | Actor: Biblio | Action: created/modified/started/completed/failed/archived | Note: Short description of what happened.
```

Before writing any log entry, follow these two steps in order:

1. Fetch the real current time by running `date +"%Y-%m-%dT%H:%M:%S%:z"` or your platform's equivalent, immediately before you append. The format must be ISO 8601 with timezone offset (e.g. `+01:00`). Never use a date-only prefix or a fake `T00:00:00` suffix.
2. Append the entry to the LOG.md file.

Always fetch the real time at the moment you append. Never reuse a timestamp fetched earlier in the task, and never hardcode one. A pre-fetched time is already stale by the time you write, and if it predates the CONTEXT.md edits you made in between, the entry ends up older than the change it records and doc-sync-guard flags it. When a task writes several log entries after a batch of CONTEXT.md edits, fetch a fresh time per entry, or fetch once only after every CONTEXT.md edit is saved, so each entry's stated time is at or after every changed-file mtime.

Rules for logging:

- The actor is always "Biblio" (or the user's name if they make a manual change and mention it).
- Action types: `created`, `modified`, `started`, `completed`, `failed`, `archived`. The `ran` action type is retired - do not use it. Every workflow run must produce two entries: `started` when beginning and `completed` or `failed` when finished.
- Append only. Newest entries go at the bottom so the log reads like a journal.
- Log at both ends of every workflow run: a `started` entry when beginning and a `completed` or `failed` entry when finished. No exceptions.
- Always log failures. If a workflow breaks or a step errors, record what went wrong and why. Lying by omission is worse than a messy log.
- The final step of every workflow is to append the LOG.md file. No exceptions.

### Templates

Reusable templates for CONTEXT.md and LOG.md live in `templates/`. They use `{{PLACEHOLDER}}` variables that are obvious, greppable, and easy to find-and-replace.

Available placeholders:

- `{{DIRECTORY_NAME}}` - Name of the directory.
- `{{DATE}}` - Current date in YYYY-MM-DD format. Used in CONTEXT.md "Last modified" lines and other date-only contexts.
- `{{TIMESTAMP}}` - Current date and time with timezone offset in ISO 8601 format (YYYY-MM-DDTHH:MM:SS±HH:MM). Used in all LOG.md entries. Fetch the real time before substituting by running `date +"%Y-%m-%dT%H:%M:%S%:z"` or equivalent.
- `{{ONE_LINE_PURPOSE}}` - Short plain-language description of the directory's purpose.
- `{{CONTENTS_LIST}}` - List of notable files and subdirectories, or "None".
- `{{INPUTS}}` - What the workflow needs to run, or "None".
- `{{OUTPUTS}}` - What the workflow produces, or "None".
- `{{STEPS}}` - Numbered list of steps, or "N/A" for non-workflow directories.
- `{{DEPENDENCIES}}` - Other workflows, wikis, tools, or services this depends on, or "None".
- `{{KNOWN_ISSUES}}` - Bugs, limitations, or edge cases, or "None".
- `{{CREATION_NOTE}}` - Short note for the initial log entry.

When creating any new directory, Biblio must use these templates as the starting point for its CONTEXT.md and LOG.md files, replacing all placeholders with the correct values. No freehanding the structure.

After filling in all placeholders, Biblio must run the full verification checklist (see above) across all sections before finalising the file. This is the last step before the CONTEXT.md is considered complete.

---

**Conventions - how things are organised and written**

### Triggers and session handoff

Book Dragon deliberately has no per-AI slash commands. Actions are invoked in plain
language, and the phrases that trigger them live in one tracked registry,
`workflows/triggers/config/triggers.yaml` [[workflows/triggers/CONTEXT]], so they
work identically on every AGENTS-reading AI. Recognise a registered phrase when the
user says it and run the mapped action. When the user asks for a list of triggers or
skills ("give me a list of triggers", "list the skills", "what can I ask you to
do"), run `python workflows/triggers/scripts/run.py --list` and show the grouped
result rather than reciting from memory. Keep the registry in sync when an action is
added or renamed.

**Session handoff** is the trigger-driven way to save state between sessions. When
the user asks for a handoff ("do the handoff", "hand off", "wrap up the session"),
run `python workflows/handoff/scripts/run.py --gather` and write `HANDOVER.md` at the
project root following the handoff skill [[workflows/handoff/skills/handoff/CONTEXT]]
(overwrite any existing one - it is a single rolling file, gitignored via
`**/HANDOVER.md`). If the user adds a steer with the trigger ("keep this in mind",
"this is where I'm going next session"), capture it in their own words at the top of
the document - it is the human's explicit priority for the next session, not
something to paraphrase away. The document must carry a `**Created:**` timestamp line: the
session-startup recovery step (step 12) reads it to tell an unread handoff from one
already picked up, so the next session opens knowing where the last one left off.

### Workflow-scoped skills

Skills that belong to a specific workflow live inside that workflow's directory, following this convention:

```
workflows/<workflow-name>/skills/<skill-name>/SKILL.md
```

One skill per subdirectory. Even if a workflow only has one skill, it still goes in `workflows/<workflow-name>/skills/<skill-name>/` - no loose SKILL.md files floating in the workflow root.

Each skill directory must have its own `CONTEXT.md` (per the universal rule). The SKILL.md is the functional spec - what the skill does and how to run it. The CONTEXT.md is the "why this exists" for the directory. For tiny skills these can be short, but the rule stays consistent.

Every new SKILL.md is scaffolded from `templates/SKILL.md.template` [[templates/CONTEXT]], which fixes a lightweight schema: Purpose, When to use, Inputs, How to run, Outputs, Verification, Hardening, Dependencies, and (optional) Known Issues. The **Verification** section is required: it must state how a caller confirms the skill produced a correct result, meaning the command, test, or observable check that proves it worked and what a failed check looks like. A skill with no stated way to verify its output is one you have to trust on prose, which the Verification discipline rule forbids. Its presence is enforced mechanically by the `skill-hardening-guard` workflow (below), alongside the Hardening section. All existing live SKILL.md files have been retrofitted onto this schema (given Verification and Hardening sections); any new or meaningfully edited skill must follow the template/schema too.

The **Hardening** section is also required. It is a declarative safety envelope: the smallest correct blast radius the skill needs, written down so the boundary is inspectable. It has five required fields, each with real content ("None" only where it genuinely applies, such as a read-only skill's write boundaries):

- **Allowed tool intent** - the tool classes the skill legitimately needs (e.g. read-only file access, git read, no network).
- **Never** - actions this skill must not perform.
- **Approval-gated** - side-effecting, destructive, or external operations that require explicit user approval before the skill runs them.
- **Write boundaries** - where the skill may write, if anywhere.
- **Verification / escape hatch** - how a reviewer can tell the skill stayed inside those boundaries, and what to do when a boundary turns out to be insufficient.

Hardening is documented intent, not runtime enforcement. Book Dragon's skills are AI-agnostic markdown specs the AI executes, so runtime per-skill tool restriction is not portable across the roster (Claude-native `.claude/skills` now expose partial runtime controls - `disallowed-tools`, skill-scoped `hooks`, `context: fork` - but adopting them as the mechanism would be Claude-only and break cross-AI parity). The portable, checkable deliverable is therefore the declarative section plus a deterministic presence/shape check, enforced by the `skill-hardening-guard` workflow [[workflows/skill-hardening-guard/CONTEXT]]: run `python workflows/skill-hardening-guard/scripts/run.py --check` to confirm every SKILL.md carries a Hardening section with all five fields non-empty and a non-empty Verification section (the two required load-bearing sections). It validates structure, never the truth of the declared policy.

When a workflow has skills, they must be documented in that workflow's `CONTEXT.md` in two places:

1. **Contents section** - each skill gets one line: name, relative path, one-sentence purpose.
2. **Dependencies section** - since the workflow relies on its skills to run, they are dependencies and must be listed there too.

### Workflow scripts

When a workflow includes scripts (per the 90-10 Protocol), they live in a `scripts/` subfolder inside the workflow directory:

```
workflows/<workflow-name>/scripts/run.py
```

Rules:
- The main entry point is always named `run` with the appropriate extension (`run.py`, `run.js`, etc.). Helper modules and utilities can be named freely alongside it.
- The `scripts/` folder must be listed in the workflow's `CONTEXT.md` Contents section.
- Scripts must use relative paths only (per the "Relative paths only" rule).

### Shared skills

When a skill becomes useful across multiple workflows, promote it to the top-level `skills/` directory rather than duplicating it. The convention is:

```
skills/<skill-name>/SKILL.md
```

The original workflow-scoped copy must be removed and replaced with a reference to the shared location. Update the CONTEXT.md and Dependencies of every workflow that uses the skill to point to the new path.

### Relative paths only

When creating skills, vibe-coded apps, or writing any code within this project, always use relative paths. Never use absolute paths. All paths should be relative to the AI-OS directory (the root level of this project). This applies to file references, imports, links, configuration files, scripts, and any other path usage in code or documentation.

### Encoding and text I/O

All text in this project is UTF-8 with LF line endings. Encoding glitches (mojibake, stray Windows-1252 bytes, accidental BOMs) are a recurring failure mode on Windows, so encoding is enforced in code, not left to chance:

- Every file read or write must pass `encoding="utf-8"` explicitly (`open(...)`, `read_text`, `write_text`). Never rely on the platform default.
- Every `subprocess` call that captures text (`text=True`) must also pass `encoding="utf-8"`. On Windows the default is cp1252, which silently corrupts or fails on non-ASCII output.
- Any script that prints a report to stdout must reconfigure it first: `sys.stdout.reconfigure(encoding="utf-8")`.
- The `encoding-guard` workflow [[workflows/encoding-guard/CONTEXT]] enforces all of the above. Run `python workflows/encoding-guard/scripts/run.py --check` to scan and `--fix` to repair; the full audit runs the check automatically.
- Scraped or imported third-party data (under `raw/` or `collections/`) is exempt and preserved verbatim.

### Writing style and locale

Use the writing style and locale specified in `USER.md`. If not specified, default to UK English spelling and grammar. This applies to documentation, code comments, commit messages, spell checking, and any other written output (e.g. "colour" not "color", "organised" not "organized", "centre" not "center").

Avoid AI writing tells in authored content. The clearest are typographic: em and en dashes (use a comma, parenthesis, hyphen, or full stop instead), smart quotes, the ellipsis character, and non-breaking spaces. These are AI artifacts and a frequent source of encoding corruption. Also avoid stock AI phrasing, and lean away from single-word AI tells where a plainer word reads as well. This applies going forward to new and edited content (documentation, code comments, commit messages, CONTEXT.md prose); it does not require rewriting existing text, and it never applies to third-party data preserved verbatim (e.g. Reddit post titles or quoted source material). The enforced markers, phrases, and words are defined in `workflows/ai-style-guard/config/ai-tells.yaml`, the single source of truth; tune the lists there. The `ai-style-guard` workflow [[workflows/ai-style-guard/CONTEXT]] enforces this mechanically: it scans only added or changed lines (so legacy text is left alone) and runs automatically as an advisory hook in the full audit. Run `python workflows/ai-style-guard/scripts/run.py --check` to scan the working tree.

### Personal data isolation

All content written into tracked files - CONTEXT.md files, scripts, README.md, SOUL.md, SKILL.md files, templates, and any other file committed to git - must use generic language only. This applies at all times, including during builds, updates, and Revision History entries.

The authoritative list of what is and isn't committed to git is `.gitignore` at the project root. Personal files excluded from git - `USER.md`, `LOG.md` files, `memory/` contents, `journal/entries/`, `.env` - may contain personal content. Everything else is tracked and must follow these rules.

Six rules, no exceptions:

1. **No personal names or identifiers.** Use "the user" instead of a name. Never write a person's name, email address, or any other identifying detail into a tracked file. Personal details belong exclusively in `USER.md` (excluded from git) or `.env` (excluded from git).

2. **No hardcoded personal values in scripts.** Any value that is specific to a person - email address, username, personal URL, account ID - must be read from `.env` at runtime, not written into the script file. Add the variable to `.env.example` with a placeholder so future users know it is required.

3. **README.md entries describe function, not personal context.** When adding a workflow or skill to `README.md`, describe what it does generically. Never describe who it was built for or what personal content it operates on. Wrong: "tracks [person]'s household expenses". Right: "tracks household expenses".

4. **Commit messages describe structure, not personal context.** Git history is visible to anyone who clones the repository. Commit messages must describe the structural or technical change made, not the personal work behind it. Wrong: "add wiki for [person]'s Facebook data". Right: "add Facebook archive wiki scaffold".

5. **CONTEXT.md Contents sections never list individual personal files.** In directories that hold personal content - `wikis/`, `conversations/`, `journal/entries/`, or any future personal archive - the Contents section must describe the file naming convention and format only. Never list individual filenames or their descriptions. Wrong: listing `2026-06-03-biblio-ui-planning.md` with a description. Right: "Saved conversation files, named `YYYY-MM-DD-topic-slug.md`. Individual files are not listed here as they are personal content." When Biblio needs to know what files exist in such a directory, it lists the directory's contents directly rather than relying on CONTEXT.md. The filesystem is always the authoritative source; CONTEXT.md describes structure and conventions only.

6. **Design-time documents are personal, never tracked.** Planning, handover, proposal, and roadmap documents (`*-PLAN.md`, `HANDOVER.md`, `PROPOSAL.md`, `ROADMAP.md`) are personal working artifacts: they routinely capture session-, machine-, and user-specific context. They are gitignored and must never be staged or committed, nor listed in a tracked `CONTEXT.md` Contents section. Durable, generic design decisions belong in the tracked `CONTEXT.md` and `README.md` instead. The choice is by category, not by case-by-case judgement: it is safer to keep every design-time doc local than to decide per file whether one is "clean enough" to commit. A genuinely generic forward-looking design doc may be tracked only as a deliberate opt-in - authored with no personal data and explicitly un-ignored.

The `personal-data-guard` workflow [[workflows/personal-data-guard/CONTEXT]] enforces these rules mechanically. Run `python workflows/personal-data-guard/scripts/run.py --check` to scan committable files for emails, personal home paths, the user's name/username, and a configurable denylist of personal nouns; the full audit runs the check automatically as an additive, advisory hook. It is a backstop, not a substitute for writing generically in the first place.

These rules are also enforced preventively by the `rule-hooks` workflow [[workflows/rule-hooks/CONTEXT]]: a universal git pre-commit hook blocks any commit that would add personal data to tracked files (for every AI and manual commits), and on Claude Code and Codex a PreToolUse hook blocks an in-progress write of personal data into a committable file before it lands. rule-hooks also makes several other always/never rules deterministic rather than prose-only: it blocks AI writes to `.env`, shell redirection into `LOG.md` (use the Edit tool or append_log), and dangerous shell commands (`rm -rf`, force-push, `reset --hard`, `clean -fd`, destructive SQL), and re-injects the permission gate at session start. Hooks guarantee execution where prose only asks; every hard block ships with the exact manual path, so a genuinely-wanted action stays a deliberate human act.

### 90-10 Protocol

When scoping any workflow - or anything else being built in this project - the first question is always: **"What part of this can a script do?"** Ask that before reaching for the AI layer. This is the default mindset, not a rigid mandate. Most things will fit it; some won't. If it doesn't fit, that should be a conscious call, not a habit of letting the AI do everything.

**The split:**
- **~90% - code.** Anything deterministic, repeatable, or data-processing in nature should be handled by a script. This saves tokens, produces consistent results, and keeps the logic auditable in a file.
- **~10% - AI.** Reserve the model for things that genuinely require judgement: summarisation, classification, writing prose, and any decision that requires context a script can't hold.

**Language choice:** Use the best tool for the job. Python and JavaScript are common defaults, but if another language is better suited or more recognised for the task, use that. No religious attachment to any one language.

**Storage escalation ladder - start dumb, escalate only if needed:**
1. Plain `.md` or `.log` files - start here
2. Flat files (JSON, CSV) - if a bit more structure is needed
3. Database - only when data volume or query complexity actually demands it

Don't jump rungs without a genuine reason to.

### Script safety

All workflow scripts that modify files must follow two conventions:

**Idempotency**
Scripts must be safe to re-run. If a script fails partway through, running it again should safely complete the remaining work - not abort, not duplicate, not corrupt. In practice:
- Before creating a file or directory, check if it already exists. Skip and note it rather than failing.
- Before inserting an entry into an existing file (e.g. `README.md`, `wikis/CONTEXT.md`), check whether that entry is already there.
- Log re-run behaviour clearly so it's obvious what was skipped and what was newly created.

**Dry-run flag**
Every workflow script that modifies files must accept a `--dry-run` flag. When passed, the script prints each action it would take - prefixed with `[DRY RUN]` - without making any actual changes. No files are created or modified, and nothing is written to any log. Useful for verifying a script before running it for real.

Read-only scripts (such as the audit script) are exempt from both requirements.

---

**Lifecycle and maintenance**

### Keeping README.md up to date

The `README.md` file in the project root is a living index of everything in Book Dragon. It exists so the user can review it at any time and know exactly what the system contains.

Whenever a new workflow, skill, or other major addition is created, update `README.md` immediately. Each entry must include the name, a short one-sentence description, the relative path to its directory, and a status tag (`[active]`, `[in progress]`, or `[archived]`). Also update the "Last updated" date at the top of the file.

If something is removed, changed in a way that affects its name, purpose, path, or status tag, or archived, update its entry in `README.md` to reflect that. Never leave the README stale.

**Exception - wikis:** individual wikis must never be listed in `README.md`. Wikis are personal content and the `## Wikis` section holds only the generic note already present. Do not add, remove, or modify individual wiki entries regardless of what the user creates.

### Archiving

When a workflow, wiki, or other directory is no longer active, it should be archived rather than deleted. Archived things are kept for reference but removed from the active workspace.

**The user's job:** tell Biblio what to archive and why. That's the only judgement call.

**Biblio's job - the steps:**

1. Create `archived/` inside the parent directory if it doesn't already exist (e.g. `workflows/archived/`, `wikis/archived/`). Give it a `CONTEXT.md` and `LOG.md` using the standard templates.
2. Move the target directory into `archived/` - for example, move `workflows/old-thing/` to `workflows/archived/old-thing/`. Always use forward slashes in paths.
3. Add a note to the archived directory's own `CONTEXT.md` explaining why it was archived and when.
4. Update the parent directory's `CONTEXT.md` Contents section - remove the entry from the active list and add it under the `archived/` entry instead.
5. Update `README.md` - change the status tag to `[archived]` and add a short reason in parentheses.
6. Append log entries to the archived directory's `LOG.md` and the root `LOG.md`.

No dedicated script is needed for archiving - it happens infrequently enough that Biblio handles it directly. Deletion is never the right choice; archived directories may have future value.

### First-run initialisation

`LOG.md` files and `USER.md` are excluded from the repository (see `.gitignore`) to prevent personal data from leaking into version control. On a fresh clone, both will be absent. This is expected and handled automatically.

**Triggering conditions:** this rule runs when either of the following is true at session startup:
- Step 2 - `USER.md` does not exist, or still contains `[YOUR_NAME]`.
- Step 5 - the root `LOG.md` does not exist.

Either condition means the system has not been set up on this machine yet. Perform all steps below before doing anything else.

**Initialisation steps:**

1. Verify Python is available and meets the minimum version.
   - On Windows: run `python --version`. If that fails or returns Python 2, try `py --version`.
   - On macOS / Linux: try `python3 --version` first; fall back to `python --version` if needed.
   The output must show Python 3.9 or later. If no working command is found, or the version is below 3.9, stop immediately and tell the user: Python 3.9 or later is required - install it from python.org or ask an AI assistant to walk through installation for their operating system. Do not proceed with any further steps until Python is confirmed.
2. If `USER.md` is missing or contains `[YOUR_NAME]`:
   a. Ask the user for their name, location, and preferred writing style.
   b. Ask for a brief description of who they are and what they want help with.
   c. Create `USER.md` from `USER.md.template`, filling in the answers provided.
   d. Note that USER.md has been created with several detailed sections currently
      marked `[not yet recorded]` - household and family, work, career goals,
      active projects, background, preferences, and financial context. These help
      Biblio assist far more effectively. Offer to run the USER.md onboarding Q&A
      now or later. If the user chooses now, go through each section in order:
      ask the relevant questions, confirm the answers, and write them directly
      into USER.md. If they prefer later, they can trigger it at any time by
      saying "run USER.md onboarding Q&A."
3. Find all directories under the project root, then check each one for a missing `LOG.md`.
4. For each directory missing a `LOG.md`, create one using the standard `LOG.md` template. Fetch the real timestamp first (run `date +"%Y-%m-%dT%H:%M:%S%:z"` or equivalent), then write the entry:
   `[YYYY-MM-DDTHH:MM:SS±HH:MM] | Actor: Biblio | Action: created | Note: First-run initialisation - LOG.md created on fresh clone.`
5. If `memory/MEMORY.md` does not exist, create it with just the header line `# Memory Index`. The `memory/LOG.md` will be created by step 4.
6. **AI-specific first-run setup** - perform any first-run setup tasks defined in your AI's wrapper file. If no AI-specific first-run tasks are defined, skip this step.
7. For any directory that is also missing a `CONTEXT.md`, flag it to the user rather than creating one silently. CONTEXT.md files require deliberate content and should be written with full knowledge of the directory's purpose.
8. Check whether `.env` exists at the project root. If it does not exist, note the following to the user without stopping or waiting for a response:
   - Web research will run in free-sources-only mode (Wikipedia, HackerNews, Reddit, arXiv, Semantic Scholar, Stack Exchange, Dev.to, RSS, direct scraper). This is functional but has limited coverage for general web content and current news.
   - Workflows that depend on web research may produce less thorough or less accurate results without extended source coverage.
   - To obtain and configure API keys, follow `workflows/web-research/SETUP.md`.
9. Append a line to the `## Getting started` section of `README.md` recording the date the system was initialised on this machine.
10. Greet the user by name, confirm the system has been initialised, and ask what
   they want to work on. If the USER.md onboarding Q&A was deferred in step 2d,
   add a single sentence: "When you're ready, we can run the USER.md onboarding Q&A
   to fill in the remaining sections."

This pass runs only once. On every subsequent session, `USER.md` and all `LOG.md` files already exist locally, so steps 2 and 5 of session startup proceed normally.

### Project memory

The canonical memory location for this project is `memory/` at the project root. All AIs working in this project must use this location for persistent memory. If an AI has its own default memory cache location, the project `memory/` directory takes precedence. Memory written to a per-AI cache is local only and should be treated as stale if it conflicts with what is in `memory/`.

**Reading memory:** Step 4 of session startup loads `memory/MEMORY.md`. Once the task is known (step 15), read the MEMORY.md index and pull any individual memory files whose topics relate to the current task before beginning work. Do not defer this until mid-session.

**When to write a memory:**
- The user corrects an approach, or confirms a non-obvious approach worked.
- A new fact about the user's role, preferences, or knowledge is established.
- A project decision, initiative, or deadline is confirmed.
- A pointer to an external resource or system is learned.

**When NOT to write a memory:**
- Code patterns, conventions, or architecture derivable by reading the current project files.
- Git history, recent changes, or who-changed-what (use `git log` and `git blame`).
- Debugging solutions whose fix is already in the code.
- Anything already documented in `AGENTS.md`, AI-specific wrapper files, `SOUL.md`, `USER.md`, or other project files.
- Ephemeral task details, in-progress work, or context that only matters for the current session.
- Vague observations or low-confidence hunches.

**Four-step write procedure:**
1. Check `MEMORY.md` and scan existing files for duplicates. Update an existing memory rather than creating a new one if a relevant file already exists.
2. Write the memory file using the naming convention `<type>_<short-name>.md`. Include YAML frontmatter with three fields: `name` (kebab-case slug), `description` (one line, specific enough to judge relevance in future sessions), `type` (one of the four values below). Body structure by type:
   - **feedback**: lead with the rule, then `**Why:**` (the reason the user gave, often a past incident), then `**How to apply:**` (when and where the rule kicks in).
   - **project**: lead with the fact or decision, then `**Why:**` (the motivation or constraint), then `**How to apply:**` (how this shapes suggestions).
   - **reference**: state the fact plainly. No required sub-sections.
   - **user**: state the trait plainly. No required sub-sections.
3. Add or update the entry in `memory/MEMORY.md`. One line, under 150 chars, enough signal to decide relevance in future sessions.
4. Append an entry to `memory/LOG.md`.

**Cross-linking memories:** Link related memories in the body with `[[filename-stem]]` - the target memory's filename without the `.md` extension (e.g. `[[feedback_wait_for_permission]]`, `[[backlog]]`). The filename stem is the canonical link identifier in this project: it is what `MEMORY.md` entries and the knowledge-graph memory layer resolve against. The frontmatter `name:` slug is descriptive metadata used by recall, **not** the link key. This project rule deliberately overrides the built-in memory default of linking by the `name:` slug, so that the documented convention matches actual practice and no drift accumulates. A link to a memory that does not exist yet is allowed - it marks something worth writing later.

### General guidelines

- Keep each workflow self-contained within its own subdirectory under `workflows/`.
- Keep each wiki self-contained within its own subdirectory under `wikis/`.
- Do not place loose files in the project root unless they are project-level configuration or documentation (like this file).
- The audit script checks instruction file length against a threshold of 600 lines. When it warns that the threshold has been exceeded, tell the user directly: "The instruction file has exceeded 600 lines - it may be worth extracting rarely-used sections to keep the most critical rules prominent." Do not proceed with other work until the user has acknowledged this.
