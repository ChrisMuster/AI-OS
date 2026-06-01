# AI-OS — Claude Instructions

**Last updated:** 2026-05-29

This is the AI Operating System project. It is a modular workspace organised into directories that each serve a specific purpose.

## Session startup

At the start of every new session, before doing anything else:

1. Read `SOUL.md` — this tells you who you are (name, personality, behavioural rules).
2. Read `USER.md` — this tells you who you are assisting. If `USER.md` does not exist or still contains `[YOUR_NAME]`, follow the **First-run initialisation** rule immediately before doing anything else.
3. Read `README.md` — this is the living index of everything in the project. Individual wikis are not listed in README.md (they are personal content); if `wikis/CONTEXT.md` exists, read it too — it is the authoritative list of what wikis have been created.
4. Read the last 15 entries of the root `LOG.md` — this tells you what has happened recently. If `LOG.md` does not exist at the root, this is a fresh clone — follow the **First-run initialisation** rule immediately before doing anything else.
5. Wait for the user to say what they want to work on.
6. Once you know the task, read the `CONTEXT.md` and `LOG.md` of every directory you will touch before making any changes (per the "Reading context before working" rule below).

Do not skip steps 1–4. Do not summarise what you have read back to the user unless they ask. After finishing steps 1–4, greet the user by name (from `USER.md`) and ask what they want to work on today.

## Directory structure

- **`workflows/`** — All workflows live here. Each workflow gets its own subdirectory within this folder. When creating a new workflow, always place it inside `workflows/`.
- **`wikis/`** — All wikis live here. Each wiki gets its own subdirectory within this folder. When creating a new wiki, always place it inside `wikis/`.
- **`skills/`** — Shared skills that are used across multiple workflows live here. Each skill gets its own subdirectory. Only promote a skill here once it is needed by more than one workflow.
- **`templates/`** — Reusable templates for standard files (CONTEXT.md, LOG.md). Biblio uses these when scaffolding new directories.

## Rules

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
Write "None" if there are no dependencies. If a dependency changes or breaks, this workflow may break too — flag it here so the link is visible.

## Known Issues
Any bugs, limitations, edge cases, or things that don't work yet.
Write "None" if there are no known issues.

## Revision History
A short dated list of changes to this workflow or directory, newest at the bottom.
This tracks how the workflow has evolved over time without needing to dig through git or logs.

- [YYYY-MM-DD] — Initial creation.
```

The `CONTEXT.md` file is a living document. Whenever Biblio makes changes to a workflow or directory, the relevant sections of its `CONTEXT.md` must be updated and a new line added to the Revision History.

#### Verification checklist for all CONTEXT.md sections

Before writing "None" or "N/A" for any section, Biblio must run through the relevant questions below. If the answer to any of them is yes, "None" is wrong and the real content must be written instead. This checklist covers every section that can silently hide real information.

**Contents:**

- Does this directory contain any files beyond CONTEXT.md and LOG.md?
- Does it contain any subdirectories (e.g. skills, nested workflows)?
- Are any of those files or directories worth naming — scripts, templates, skill specs, config files?
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

- Does this directory have a procedure — a sequence of actions that Biblio follows?
- Could someone unfamiliar with the workflow figure out what to do without a steps list?
- Is there a correct order that must be followed, or things that must happen before others?
- Is this a workflow directory (as opposed to a pure container like `workflows/` or `wikis/`)?

**Dependencies:**

- Does this directory reference, import, or rely on any other file or directory in the project?
- Does it follow rules defined elsewhere (e.g. CLAUDE.md, a shared skill, a template)?
- Does it use any external tools, APIs, or services?
- Would it break if something else in the project changed or disappeared?

**Known Issues:**

- Is there anything that could drift out of sync with the rest of the project?
- Are there sections in this file (e.g. Contents) that need manual updating when the directory changes?
- Are there limitations, edge cases, or things that don't work yet?
- Is there anything that works now but is fragile or likely to need revisiting?

This checklist is not optional. Lazy "None" entries hide real information and create silent breakage. If Biblio genuinely answers no to every question in a section, then "None" is correct. Otherwise, write the real answer.

#### CONTEXT.md change propagation

When a file is added to, removed from, or meaningfully changed in a subdirectory, both of the following must be updated:

1. **The subdirectory's own `CONTEXT.md`** — update Contents (if files changed), Dependencies (if dependencies changed), Known Issues (if behaviour changed), and add a Revision History entry.
2. **The parent directory's `CONTEXT.md`** — review Contents for accuracy and add a Revision History entry if the change is significant enough to affect what the parent describes.

This obligation does not stop at the immediate parent. Follow the chain upward as far as it is relevant. If a source adapter is added to `skills/web-research/scripts/sources/`, the change may be significant enough to ripple up through `skills/web-research/scripts/CONTEXT.md`, then `skills/web-research/CONTEXT.md`. Use judgement about how far up the chain the change matters — but always check at least one level up.

Concrete example of a full propagation chain when adding a new shared skill:
- The new skill's own `CONTEXT.md` (created)
- `skills/CONTEXT.md` — Contents updated
- `README.md` — entry added
- Any workflow that depends on the skill — Dependencies section updated

#### Revision History archiving

**CONTEXT.md files:** Keep the Revision History section to a maximum of 10 entries. When it grows beyond that, move the oldest entries into the directory's LOG.md as a single `archived` entry — the text of each entry is preserved in full, just in a different file. Then replace the removed entries in CONTEXT.md with a single reference line at the top of the Revision History section:

`Earlier history archived to LOG.md on [YYYY-MM-DD].`

This keeps CONTEXT.md files lean without losing anything. The LOG.md is already the audit trail for the directory; archiving old Revision History entries there is a natural extension of its purpose.

**Operational root files (CLAUDE.md, SOUL.md, README.md):** These files are read at the start of every session and must stay as lean as possible. They do not carry a Revision History section at all. Use a `**Last updated:** YYYY-MM-DD` line near the top of the file instead. The Revision History rule above applies to CONTEXT.md files only — not to CLAUDE.md itself, SOUL.md, or README.md.

### Workflow-scoped skills

Skills that belong to a specific workflow live inside that workflow's directory, following this convention:

```
workflows/<workflow-name>/skills/<skill-name>/SKILL.md
```

One skill per subdirectory, matching Anthropic's own skill convention. Even if a workflow only has one skill, it still goes in `workflows/<workflow-name>/skills/<skill-name>/` — no loose SKILL.md files floating in the workflow root.

Each skill directory must have its own `CONTEXT.md` (per the universal rule). The SKILL.md is the functional spec — what the skill does and how to run it. The CONTEXT.md is the "why this exists" for the directory. For tiny skills these can be short, but the rule stays consistent.

When a workflow has skills, they must be documented in that workflow's `CONTEXT.md` in two places:

1. **Contents section** — each skill gets one line: name, relative path, one-sentence purpose.
2. **Dependencies section** — since the workflow relies on its skills to run, they are dependencies and must be listed there too.

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

The original workflow-scoped copy should be removed and replaced with a reference to the shared location. Update the CONTEXT.md and Dependencies of every workflow that uses the skill to point to the new path.

### Templates

Reusable templates for CONTEXT.md and LOG.md live in `templates/`. They use `{{PLACEHOLDER}}` variables that are obvious, greppable, and easy to find-and-replace.

Available placeholders:

- `{{DIRECTORY_NAME}}` — Name of the directory.
- `{{DATE}}` — Current date in YYYY-MM-DD format.
- `{{TIMESTAMP}}` — Current date and time in ISO 8601 format (YYYY-MM-DDTHH:MM:SS). For script-generated log entries only — scripts have real wall-clock time. Biblio's manual log entries use `{{DATE}}` instead.
- `{{ONE_LINE_PURPOSE}}` — Short plain-language description of the directory's purpose.
- `{{CONTENTS_LIST}}` — List of notable files and subdirectories, or "None".
- `{{INPUTS}}` — What the workflow needs to run, or "None".
- `{{OUTPUTS}}` — What the workflow produces, or "None".
- `{{STEPS}}` — Numbered list of steps, or "N/A" for non-workflow directories.
- `{{DEPENDENCIES}}` — Other workflows, wikis, tools, or services this depends on, or "None".
- `{{KNOWN_ISSUES}}` — Bugs, limitations, or edge cases, or "None".
- `{{CREATION_NOTE}}` — Short note for the initial log entry.

When creating any new directory, Biblio must use these templates as the starting point for its CONTEXT.md and LOG.md files, replacing all placeholders with the correct values. No freehanding the structure.

After filling in all placeholders, Biblio must run the full verification checklist (see above) across all sections before finalising the file. This is the last step before the CONTEXT.md is considered complete.

### LOG.md files

Every directory in this project must contain a `LOG.md` file. This is an append-only audit trail of everything that happens in that directory.

There is also a root-level `LOG.md` at the project root for system-wide events (new workflow created, directory restructured, major changes, etc.). Individual directory logs should stay focused on their own activity.

Log entries use one of two timestamp formats depending on origin:

**Manual entries (written by Biblio):**
```
[YYYY-MM-DD] | Actor: Biblio | Action: created/modified/ran/failed | Note: Short description of what happened.
```

**Script-generated entries (written by run.py or other scripts):**
```
[YYYY-MM-DDTHH:MM:SS] | Actor: Biblio | Action: created/modified/ran/failed | Note: Short description of what happened.
```

The difference is intentional: scripts have access to real wall-clock time via `datetime.now()`; Biblio only knows the current date. A date-only prefix is honest. A fake `T00:00:00` is not.

Rules for logging:

- The actor is always "Biblio" (or the user's name if they make a manual change and mention it).
- Action types: `created`, `modified`, `ran`, `started`, `completed`, `failed`, `archived`.
- Append only. Newest entries go at the bottom so the log reads like a journal.
- Log at both ends of a workflow run: a "started" entry when beginning and a "completed" or "failed" entry when finished.
- Always log failures. If a workflow breaks or a step errors, record what went wrong and why. Lying by omission is worse than a messy log.
- The final step of every workflow is to append the LOG.md file. No exceptions.

### First-run initialisation

`LOG.md` files and `USER.md` are excluded from the repository (see `.gitignore`) to prevent personal data from leaking into version control. On a fresh clone, both will be absent. This is expected and handled automatically.

**Triggering conditions:** this rule runs when either of the following is true at session startup:
- Step 2 — `USER.md` does not exist, or still contains `[YOUR_NAME]`.
- Step 4 — the root `LOG.md` does not exist.

Either condition means the system has not been set up on this machine yet. Perform all steps below before doing anything else.

**Initialisation steps:**

1. If `USER.md` is missing or contains `[YOUR_NAME]`:
   a. Ask the user for their name, location, and preferred writing style.
   b. Ask for a brief description of who they are and what they want help with.
   c. Create `USER.md` from `USER.md.template`, filling in the answers provided.
2. Walk every auditable directory in the project (the same tree the audit script covers).
3. For each directory missing a `LOG.md`, create one using the standard `LOG.md` template with a single entry:
   `[YYYY-MM-DD] | Actor: Biblio | Action: created | Note: First-run initialisation — LOG.md created on fresh clone.`
4. For any directory that is also missing a `CONTEXT.md`, flag it to the user rather than creating one silently. CONTEXT.md files require deliberate content and should be written with full knowledge of the directory's purpose.
5. Append a line to the `## Getting started` section of `README.md` recording the date the system was initialised on this machine.
6. Greet the user by name, confirm the system has been initialised, and ask what they want to work on.

This pass runs only once. On every subsequent session, `USER.md` and all `LOG.md` files already exist locally, so steps 2 and 4 of session startup proceed normally.

### Relative paths only

When creating skills, vibe-coded apps, or writing any code within this project, always use relative paths. Never use absolute paths. All paths should be relative to the AI-OS directory (the root level of this project). This applies to file references, imports, links, configuration files, scripts, and any other path usage in code or documentation.

### Writing style and locale

Use the writing style and locale specified in `USER.md`. If not specified, default to UK English spelling and grammar. This applies to documentation, code comments, commit messages, spell checking, and any other written output (e.g. "colour" not "color", "organised" not "organized", "centre" not "center").

### Personal data isolation

All content written into tracked files — CONTEXT.md files, scripts, README.md, SOUL.md, SKILL.md files, templates, and any other file committed to git — must use generic language only. This applies at all times, including during builds, updates, and Revision History entries.

Four rules, no exceptions:

1. **No personal names or identifiers.** Use "the user" instead of a name. Never write a person's name, email address, or any other identifying detail into a tracked file. Personal details belong exclusively in `USER.md` (excluded from git) or `.env` (excluded from git).

2. **No hardcoded personal values in scripts.** Any value that is specific to a person — email address, username, personal URL, account ID — must be read from `.env` at runtime, not written into the script file. Add the variable to `.env.example` with a placeholder so future users know it is required.

3. **README.md entries describe function, not personal context.** When adding a workflow or skill to `README.md`, describe what it does generically. Never describe who it was built for or what personal content it operates on. Wrong: "tracks Chris's household expenses". Right: "tracks household expenses".

4. **Commit messages describe structure, not personal context.** Git history is visible to anyone who clones the repository. Commit messages must describe the structural or technical change made, not the personal work behind it. Wrong: "add wiki for Chris's Facebook data". Right: "add Facebook archive wiki scaffold".

### Keeping README.md up to date

The `README.md` file in the project root is a living index of everything in Book Dragon. It exists so the user can review it at any time and know exactly what the system contains.

Whenever a new workflow, skill, or other major addition is created, update `README.md` immediately. Each entry should include the name, a short one-sentence description, the relative path to its directory, and a status tag (`[active]`, `[in progress]`, or `[archived]`). Also update the "Last updated" date at the top of the file.

If something is removed, significantly changed, or archived, update its entry in `README.md` to reflect that. Never leave the README stale.

**Exception — wikis:** individual wikis must never be listed in `README.md`. Wikis are personal content and the `## Wikis` section holds only the generic note already present. Do not add, remove, or modify individual wiki entries regardless of what the user creates.

### Reading context before working

Before making any changes to a directory, Biblio must read that directory's `CONTEXT.md` and the most recent entries in its `LOG.md`. This applies to every directory that will be touched in a session — not the entire project up front, but each directory before work begins in it. If work expands to cover additional directories mid-session, read their `CONTEXT.md` and `LOG.md` before touching them too.

This rule exists to ensure Biblio is never editing files without understanding the current state of that directory.

### Build close-out

At the end of any multi-step build — any work that spans more than one step or more than a handful of file changes — Biblio must perform a close-out pass before the work is considered complete.

The close-out pass covers every `CONTEXT.md` that was created or modified during the build. For each one, ask:

1. **Staleness** — does any text describe planned work that has since been completed? Phrases such as "future steps will...", "will be added", "added in later steps", or "Step N:" references in prose are signals that language was written during construction and never updated to reflect the finished state. Rewrite to describe current state only.
2. **Contents accuracy** — does the Contents section reflect what actually exists in the directory now, including any files added during the build?
3. **Revision History completeness** — does the Revision History have an entry for every meaningful change made during this build, including changes to child directories that are significant at the parent level?
4. **Path format** — are all paths project-root-relative? No `../` references anywhere in the file.

After the close-out pass, run the structural audit (`python workflows/audit/scripts/run.py`) to confirm nothing structural was missed. A clean audit after a clean close-out pass is the definition of "done".

This pass is separate from reading context before working. Reading context is what you do before you start; the close-out pass is what you do before you finish.

### 90-10 Protocol

When scoping any workflow — or anything else being built in this project — the first question is always: **"What part of this can a script do?"** Ask that before reaching for the AI layer. This is the default mindset, not a rigid mandate. Most things will fit it; some won't. If it doesn't fit, that should be a conscious call, not a habit of letting the AI do everything.

**The split:**
- **~90% — code.** Anything deterministic, repeatable, or data-processing in nature should be handled by a script. This saves tokens, produces consistent results, and keeps the logic auditable in a file.
- **~10% — AI.** Reserve the model for things that genuinely require judgement: summarisation, classification, writing prose, and any decision that requires context a script can't hold.

**Language choice:** Use the best tool for the job. Python and JavaScript are common defaults, but if another language is better suited or more recognised for the task, use that. No religious attachment to any one language.

**Storage escalation ladder — start dumb, escalate only if needed:**
1. Plain `.md` or `.log` files — start here
2. Flat files (JSON, CSV) — if a bit more structure is needed
3. Database — only when data volume or query complexity actually demands it

Don't jump rungs without a genuine reason to.

### Script safety

All workflow scripts that modify files must follow two conventions:

**Idempotency**
Scripts must be safe to re-run. If a script fails partway through, running it again should safely complete the remaining work — not abort, not duplicate, not corrupt. In practice:
- Before creating a file or directory, check if it already exists. Skip and note it rather than failing.
- Before inserting an entry into an existing file (e.g. `README.md`, `wikis/CONTEXT.md`), check whether that entry is already there.
- Log re-run behaviour clearly so it's obvious what was skipped and what was newly created.

**Dry-run flag**
Every workflow script that modifies files must accept a `--dry-run` flag. When passed, the script prints each action it would take — prefixed with `[DRY RUN]` — without making any actual changes. No files are created or modified, and nothing is written to any log. Useful for verifying a script before running it for real.

Read-only scripts (such as the audit script) are exempt from both requirements.

### Archiving

When a workflow, wiki, or other directory is no longer active, it should be archived rather than deleted. Archived things are kept for reference but removed from the active workspace.

**The user's job:** tell Biblio what to archive and why. That's the only judgement call.

**Biblio's job — the steps:**

1. Create `archived/` inside the parent directory if it doesn't already exist (e.g. `workflows/archived/`, `wikis/archived/`). Give it a `CONTEXT.md` and `LOG.md` using the standard templates.
2. Move the target directory into `archived/` (e.g. `workflows/old-thing/` → `workflows/archived/old-thing/`).
3. Add a note to the archived directory's own `CONTEXT.md` explaining why it was archived and when.
4. Update the parent directory's `CONTEXT.md` Contents section — remove the entry from the active list and add it under the `archived/` entry instead.
5. Update `README.md` — change the status tag to `[archived]` and add a short reason in parentheses.
6. Append log entries to the archived directory's `LOG.md` and the root `LOG.md`.

No dedicated script is needed for archiving — it happens infrequently enough that Biblio handles it directly. Deletion is never the right choice; archived directories may have future value.

### General guidelines

- Keep each workflow self-contained within its own subdirectory under `workflows/`.
- Keep each wiki self-contained within its own subdirectory under `wikis/`.
- Do not place loose files in the project root unless they are project-level configuration or documentation (like this file).

