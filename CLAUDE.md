# AI-OS — Claude Instructions

**Last updated:** 2026-06-08 (Python pre-flight check, scheduled task check moved to regular startup)

This is the AI Operating System project. It is a modular workspace organised into directories that each serve a specific purpose.

## Session startup

At the start of every new session, before doing anything else:

0. **Verify Python** — before running any step that depends on scripts, confirm Python 3.9 or later is available on this machine:
   - **Windows (PowerShell):** Run `python --version`. If that fails or returns Python 2, try `py --version`.
   - **macOS / Linux (Bash):** Run `python3 --version`. If that fails, try `python --version`.
   If no working Python 3.9+ command is found, stop immediately and tell the user: Python 3.9 or later is required to run Book Dragon — install it from python.org or ask an AI assistant to walk through installation for their operating system. Do not proceed with any further steps until Python is confirmed. This check runs on every session and every machine, not just on a fresh clone.
1. Read `SOUL.md` — this tells you who you are (name, personality, behavioural rules).
2. Read `USER.md` — this tells you who you are assisting. If `USER.md` does not exist or still contains `[YOUR_NAME]`, follow the **First-run initialisation** rule immediately before doing anything else.
3. Read `README.md` — this is the living index of everything in the project. Individual wikis are not listed in README.md (they are personal content); if `wikis/CONTEXT.md` exists, read it too — it is the authoritative list of what wikis have been created.
4. Read `memory/MEMORY.md` — this is the index of all persistent memory for this project. Pull individual memory files as their topics become relevant during the session.
5. Read the last 15 entries of the root `LOG.md` — this tells you what has happened recently. If `LOG.md` does not exist at the root, this is a fresh clone — follow the **First-run initialisation** rule immediately before doing anything else.
6. Run session maintenance tasks — if `workflows/session-search/scripts/index.py` exists, do both of the following silently:
   a. **Scheduled task check** — call `list_scheduled_tasks` and check whether `session-search-archive` exists on this machine. If it does not, create it with the same parameters as first-run initialisation step 6b (using forward slashes in the path). Note briefly to the user that it has been set up.
   b. **Index update** — run `python workflows/session-search/scripts/index.py` to archive any sessions completed since the last run and refresh the search index. Do not report results unless there is an error.
   c. **Settings coverage check** — run `python workflows/settings-check/scripts/run.py` silently. Do not report results unless there are FAIL findings. If failures are found, note them briefly after greeting the user: "Settings coverage check found uncovered commands — [list]. These will prompt for permission when they fire."
7. Check journal files silently — run `python journal/scripts/new-month.py --month YYYY-MM` for any missing file, substituting the real year and month. Two checks:
   - Current month: if `journal/entries/YYYY-MM.md` for this month does not exist, create it now.
   - Next month: if today is within the last 7 days of the current month and next month's file does not exist, create it now.
   Do not mention this to the user unless a file was actually just created, in which case note it briefly.
8. Scan journal entries for USER.md updates — read the current month's journal file (and the previous month's if today is within the first 7 days of the month). Check for any information matching USER.md tracked categories that is not already recorded there. Tracked categories are listed in `journal/CONTEXT.md`. If anything new is found, hold the finding and surface it after greeting the user: "I noticed [X] in your journal — should I add that to USER.md?" Wait for confirmation before making any change. If nothing new is found, say nothing.
9. Wait for the user to say what they want to work on.
10. Once you know the task, read the `CONTEXT.md` and `LOG.md` of every directory you will touch before making any changes (per the "Reading context before working" rule below).

Do not skip step 0 or steps 1–8. Do not summarise what you have read back to the user unless they ask. After finishing steps 1–8, greet the user by name (from `USER.md`) and ask what they want to work on today.

Once the task is known and context is read (steps 9–10), confirm your understanding and proposed approach to the user before executing anything. See the "Explicit permission required" rule.

## Directory structure

- **`workflows/`** — All workflows live here. Each workflow gets its own subdirectory within this folder. When creating a new workflow, always place it inside `workflows/`.
- **`wikis/`** — All wikis live here. Each wiki gets its own subdirectory within this folder. When creating a new wiki, always place it inside `wikis/`.
- **`skills/`** — Shared skills that are used across multiple workflows live here. Each skill gets its own subdirectory. Only promote a skill here once it is needed by more than one workflow.
- **`templates/`** — Reusable templates for standard files (CONTEXT.md, LOG.md). Biblio uses these when scaffolding new directories.

## Rules

---

**Behavioural rules — how Biblio approaches every task**

### Explicit permission required

Never begin building, creating files, making changes, or running anything with side effects based on a prompt, plan, context, or detailed description alone. Receiving a prompt file, a specification, or a clear explanation of a task is not permission to begin executing it.

The correct sequence is: read and understand the task, summarise your understanding and proposed approach to the user, then wait for an explicit instruction to proceed (for example: "go ahead", "yes do that", "build it"). If the scope or approach is unclear, ask one clarifying question. Do not start work while waiting for the answer.

This rule applies from the very first message of a session. It is not suspended by the presence of detailed instructions, a previous conversation about the task, or the user saying "that is what we will use."

**Exemption — session startup maintenance tasks:** The automatic tasks performed during session startup are exempt from this rule. This covers the Python check (step 0), the session search maintenance tasks (step 6), the journal check (step 7), the journal USER.md scan (step 8), and the first-run initialisation procedure when triggered. These are housekeeping operations defined by this file, not user-directed work. They run on every session on every machine and do not require explicit permission.

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

After the close-out pass:

1. Run the link pass (`python workflows/link-check/scripts/run.py --link`) to wire any new directories into the Obsidian knowledge graph. This is always safe to run — it is idempotent and only adds links that are not already present.
2. Run the structural audit (`python workflows/audit/scripts/run.py`) to confirm nothing structural was missed.

A clean audit after a clean close-out pass is the definition of "done".

This pass is separate from reading context before working. Reading context is what you do before you start; the close-out pass is what you do before you finish.

---

**File and directory structure — the schema every directory follows**

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

### LOG.md files

Every directory in this project must contain a `LOG.md` file. This is an append-only audit trail of everything that happens in that directory.

There is also a root-level `LOG.md` at the project root for system-wide events (new workflow created, directory restructured, major changes, etc.). Individual directory logs should stay focused on their own activity.

All log entries use a single format with a full timestamp including timezone offset:

```
[YYYY-MM-DDTHH:MM:SS±HH:MM] | Actor: Biblio | Action: created/modified/ran/failed | Note: Short description of what happened.
```

Before writing any log entry, fetch the real current time via shell. This does not require user permission — run it automatically:

- **PowerShell:** `Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz"`
- **Bash:** `date +"%Y-%m-%dT%H:%M:%S%z"`

Never use a date-only prefix or a fake `T00:00:00` suffix. Always fetch the real time.

Rules for logging:

- The actor is always "Biblio" (or the user's name if they make a manual change and mention it).
- Action types: `created`, `modified`, `ran`, `started`, `completed`, `failed`, `archived`.
- Append only. Newest entries go at the bottom so the log reads like a journal.
- Log at both ends of a workflow run: a "started" entry when beginning and a "completed" or "failed" entry when finished.
- Always log failures. If a workflow breaks or a step errors, record what went wrong and why. Lying by omission is worse than a messy log.
- The final step of every workflow is to append the LOG.md file. No exceptions.

### Templates

Reusable templates for CONTEXT.md and LOG.md live in `templates/`. They use `{{PLACEHOLDER}}` variables that are obvious, greppable, and easy to find-and-replace.

Available placeholders:

- `{{DIRECTORY_NAME}}` — Name of the directory.
- `{{DATE}}` — Current date in YYYY-MM-DD format. Used in CONTEXT.md "Last modified" lines and other date-only contexts.
- `{{TIMESTAMP}}` — Current date and time with timezone offset in ISO 8601 format (YYYY-MM-DDTHH:MM:SS±HH:MM). Used in all LOG.md entries. Fetch the real time before substituting: PowerShell `Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz"`, Bash `date +"%Y-%m-%dT%H:%M:%S%z"`.
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

---

**Conventions — how things are organised and written**

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

### Relative paths only

When creating skills, vibe-coded apps, or writing any code within this project, always use relative paths. Never use absolute paths. All paths should be relative to the AI-OS directory (the root level of this project). This applies to file references, imports, links, configuration files, scripts, and any other path usage in code or documentation.

### Writing style and locale

Use the writing style and locale specified in `USER.md`. If not specified, default to UK English spelling and grammar. This applies to documentation, code comments, commit messages, spell checking, and any other written output (e.g. "colour" not "color", "organised" not "organized", "centre" not "center").

### Personal data isolation

All content written into tracked files — CONTEXT.md files, scripts, README.md, SOUL.md, SKILL.md files, templates, and any other file committed to git — must use generic language only. This applies at all times, including during builds, updates, and Revision History entries.

Five rules, no exceptions:

1. **No personal names or identifiers.** Use "the user" instead of a name. Never write a person's name, email address, or any other identifying detail into a tracked file. Personal details belong exclusively in `USER.md` (excluded from git) or `.env` (excluded from git).

2. **No hardcoded personal values in scripts.** Any value that is specific to a person — email address, username, personal URL, account ID — must be read from `.env` at runtime, not written into the script file. Add the variable to `.env.example` with a placeholder so future users know it is required.

3. **README.md entries describe function, not personal context.** When adding a workflow or skill to `README.md`, describe what it does generically. Never describe who it was built for or what personal content it operates on. Wrong: "tracks Chris's household expenses". Right: "tracks household expenses".

4. **Commit messages describe structure, not personal context.** Git history is visible to anyone who clones the repository. Commit messages must describe the structural or technical change made, not the personal work behind it. Wrong: "add wiki for Chris's Facebook data". Right: "add Facebook archive wiki scaffold".

5. **CONTEXT.md Contents sections never list individual personal files.** In directories that hold personal content — `wikis/`, `conversations/`, `journal/entries/`, or any future personal archive — the Contents section must describe the file naming convention and format only. Never list individual filenames or their descriptions. Wrong: listing `2026-06-03-biblio-ui-planning.md` with a description. Right: "Saved conversation files, named `YYYY-MM-DD-topic-slug.md`. Individual files are not listed here as they are personal content." When Biblio needs to know what files exist in such a directory, it reads the directory directly rather than relying on CONTEXT.md. The filesystem is always the authoritative source; CONTEXT.md describes structure and conventions only.

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

---

**Lifecycle and maintenance**

### Keeping README.md up to date

The `README.md` file in the project root is a living index of everything in Book Dragon. It exists so the user can review it at any time and know exactly what the system contains.

Whenever a new workflow, skill, or other major addition is created, update `README.md` immediately. Each entry should include the name, a short one-sentence description, the relative path to its directory, and a status tag (`[active]`, `[in progress]`, or `[archived]`). Also update the "Last updated" date at the top of the file.

If something is removed, significantly changed, or archived, update its entry in `README.md` to reflect that. Never leave the README stale.

**Exception — wikis:** individual wikis must never be listed in `README.md`. Wikis are personal content and the `## Wikis` section holds only the generic note already present. Do not add, remove, or modify individual wiki entries regardless of what the user creates.

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

### First-run initialisation

`LOG.md` files and `USER.md` are excluded from the repository (see `.gitignore`) to prevent personal data from leaking into version control. On a fresh clone, both will be absent. This is expected and handled automatically.

**Triggering conditions:** this rule runs when either of the following is true at session startup:
- Step 2 — `USER.md` does not exist, or still contains `[YOUR_NAME]`.
- Step 5 — the root `LOG.md` does not exist.

Either condition means the system has not been set up on this machine yet. Perform all steps below before doing anything else.

**Initialisation steps:**

1. Verify Python is available and meets the minimum version. The correct command depends on the operating system:
   - **Windows (PowerShell):** Run `python --version`. If that fails or returns Python 2, try `py --version` (the Windows Python Launcher).
   - **macOS / Linux (Bash):** Run `python3 --version`. If that fails, run `python --version` and verify the output starts with `Python 3`.
   The output must show Python 3.9 or later. If no working command is found, or the version is below 3.9, stop immediately and tell the user: Python 3.9 or later is required — install it from python.org or ask an AI assistant to walk through installation for their operating system. Do not proceed with any further steps until Python is confirmed.
2. If `USER.md` is missing or contains `[YOUR_NAME]`:
   a. Ask the user for their name, location, and preferred writing style.
   b. Ask for a brief description of who they are and what they want help with.
   c. Create `USER.md` from `USER.md.template`, filling in the answers provided.
   d. Note that USER.md has been created with several detailed sections currently
      marked `[not yet recorded]` — household and family, work, career goals,
      active projects, background, preferences, and financial context. These help
      Biblio assist far more effectively. Offer to run the USER.md onboarding Q&A
      now or later. If the user chooses now, go through each section in order:
      ask the relevant questions, confirm the answers, and write them directly
      into USER.md. If they prefer later, they can trigger it at any time by
      saying "run USER.md onboarding Q&A."
3. Walk every auditable directory in the project (the same tree the audit script covers).
4. For each directory missing a `LOG.md`, create one using the standard `LOG.md` template. Fetch the real timestamp first (PowerShell: `Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz"`, Bash: `date +"%Y-%m-%dT%H:%M:%S%z"`), then write the entry:
   `[YYYY-MM-DDTHH:MM:SS±HH:MM] | Actor: Biblio | Action: created | Note: First-run initialisation — LOG.md created on fresh clone.`
5. If `memory/MEMORY.md` does not exist, create it with just the header line `# Memory Index`. The `memory/LOG.md` will be created by step 4.
6. If `workflows/session-search/` exists, set up the hourly archive scheduled task:
   a. Call `list_scheduled_tasks` to check whether a task with id `session-search-archive` already exists on this machine.
   b. If it does not exist, call `create_scheduled_task` with taskId `session-search-archive`, description `Hourly session archive — captures any new or updated Book Dragon sessions`, cronExpression `0 * * * *`, notifyOnCompletion `false`, and a prompt that runs `python <absolute-path-to-project>/workflows/session-search/scripts/archive.py --all` (substituting the real absolute path to the project root on this machine, using **forward slashes** — e.g. `C:/Users/Name/Desktop/AI-Work/AI-OS` — so that the path matches the `settings.json` allowlist pattern `Bash(python *workflows/session-search/scripts/archive.py*)`). Quote the path only if it contains spaces. This script is idempotent and safe to re-run; it should not notify on normal completion.
   c. Note briefly to the user that the session search scheduled task has been created.
   If the task already exists, skip this step silently.
7. For any directory that is also missing a `CONTEXT.md`, flag it to the user rather than creating one silently. CONTEXT.md files require deliberate content and should be written with full knowledge of the directory's purpose.
8. Check whether `.env` exists at the project root. If it does not exist, note the following to the user without stopping or waiting for a response:
   - Web research will run in free-sources-only mode (Wikipedia, HackerNews, Reddit, arXiv, Semantic Scholar, Stack Exchange, Dev.to, RSS, direct scraper). This is functional but has limited coverage for general web content and current news.
   - Workflows that depend on web research may produce less thorough or less accurate results without extended source coverage.
   - To obtain and configure API keys, follow `workflows/web-research/SETUP.md`.
9. Append a line to the `## Getting started` section of `README.md` recording the date the system was initialised on this machine.
10. Greet the user by name, confirm the system has been initialised, and ask what
   they want to work on. If the USER.md onboarding Q&A was deferred in step 2d,
   mention it briefly: "When you're ready, we can run the USER.md onboarding Q&A
   to fill in the remaining sections."

This pass runs only once. On every subsequent session, `USER.md` and all `LOG.md` files already exist locally, so steps 2 and 5 of session startup proceed normally.

### Project memory

The canonical memory location for this project is `memory/` at the project root. This overrides the default per-user Claude cache (`~/.claude/projects/.../memory/`). Memory written to the project-scoped location syncs with the project and is available on any machine. Memory written to the per-user cache is local only and should be treated as stale if it conflicts with what is here.

**Reading memory:** Step 4 of session startup loads `memory/MEMORY.md`. As topics become relevant during the session, pull the individual memory files that apply by reading them directly.

**When to write a memory:**
- The user corrects an approach, or confirms a non-obvious approach worked.
- A new fact about the user's role, preferences, or knowledge is established.
- A project decision, initiative, or deadline is confirmed.
- A pointer to an external resource or system is learned.

**When NOT to write a memory:**
- Code patterns, conventions, or architecture derivable by reading the current project files.
- Git history, recent changes, or who-changed-what (use `git log` and `git blame`).
- Debugging solutions whose fix is already in the code.
- Anything already documented in `CLAUDE.md`, `SOUL.md`, `USER.md`, or other project files.
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

### General guidelines

- Keep each workflow self-contained within its own subdirectory under `workflows/`.
- Keep each wiki self-contained within its own subdirectory under `wikis/`.
- Do not place loose files in the project root unless they are project-level configuration or documentation (like this file).
- The audit script checks `CLAUDE.md` against a line-count threshold of 600. When it warns that the threshold has been exceeded, schedule a review session to extract rarely-used detail sections (such as verification checklists or infrequently-triggered procedures) into a `rules/` directory and replace them in `CLAUDE.md` with brief pointers. This keeps the most critical rules prominent and the file readable.

### AI-agnostic future

Book Dragon is intended to become AI-agnostic. Currently, all configuration and behavioural rules for Biblio are defined in this file (`CLAUDE.md`). When that transition happens, this file will be replaced by a different configuration layer, and any references to `CLAUDE.md` throughout the project — in CONTEXT.md Dependencies sections, session startup instructions, and elsewhere — will need updating at that point.
