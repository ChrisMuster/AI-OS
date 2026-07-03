# Templates

**Last modified:** 2026-07-03

## Purpose
Holds reusable boilerplate templates for standard files used across Book Dragon. Biblio uses these as the starting point when scaffolding any new directory, ensuring every directory in the project has a consistent structure without freehanding.

## Contents
- CONTEXT.md.template — `templates/CONTEXT.md.template` [[templates/CONTEXT]] — Boilerplate for new CONTEXT.md files with placeholder variables.
- LOG.md.template — `templates/LOG.md.template` [[templates/CONTEXT]] — Boilerplate for new LOG.md files with placeholder variables.
- SKILL.md.template - `templates/SKILL.md.template` [[templates/CONTEXT]] - Boilerplate for new SKILL.md files, fixing the skill schema including the required Verification section.

## Inputs
- The name of the new directory (e.g. daily-standup, product-catalogue, ai-glossary).
- A one-line purpose describing what the directory is for.
- Any additional details provided by the user (inputs, outputs, steps, dependencies, known issues).

## Outputs
A fully scaffolded new directory containing its own CONTEXT.md and LOG.md, with all placeholder variables replaced and ready to be built on.

## Steps
This directory is a container for reference files, not a runnable workflow. The procedure for using a template is:

1. Copy the relevant template file (CONTEXT.md.template or LOG.md.template) into the new directory.
2. Rename it by removing the .template extension.
3. Replace all `{{PLACEHOLDER}}` variables with the correct values for that directory.
4. Review the filled-in file to make sure no placeholders were missed and all sections make sense.
5. Run the full verification checklist across all sections (defined in root AGENTS.md) before finalising. No lazy "None" entries.
6. Append this directory's LOG.md if a new template is added or an existing one is modified.

Available placeholders:

- `{{DIRECTORY_NAME}}` — Name of the directory.
- `{{DATE}}` — Current date in YYYY-MM-DD format.
- `{{TIMESTAMP}}` - Current date and time with timezone offset in ISO 8601 format (YYYY-MM-DDTHH:MM:SS±HH:MM).
- `{{ONE_LINE_PURPOSE}}` — Short plain-language description of the directory's purpose.
- `{{CONTENTS_LIST}}` — List of notable files and subdirectories, or "None".
- `{{INPUTS}}` — What the workflow needs to run, or "None".
- `{{OUTPUTS}}` — What the workflow produces, or "None".
- `{{STEPS}}` — Numbered list of steps, or "N/A" for non-workflow directories.
- `{{DEPENDENCIES}}` — Other workflows, wikis, tools, or services this depends on, or "None".
- `{{KNOWN_ISSUES}}` — Bugs, limitations, or edge cases, or "None".
- `{{CREATION_NOTE}}` — Short note for the initial log entry.

SKILL.md.template adds its own placeholders: `{{SKILL_NAME}}`, `{{WHEN_TO_USE}}`, `{{HOW_TO_RUN}}`, and `{{VERIFICATION}}` (how a caller confirms the skill produced a correct result), reusing `{{DATE}}`, `{{ONE_LINE_PURPOSE}}`, `{{INPUTS}}`, `{{OUTPUTS}}`, `{{DEPENDENCIES}}`, and `{{KNOWN_ISSUES}}` from the list above. The SKILL.md schema, including the required Verification section, is defined in root AGENTS.md under "Workflow-scoped skills".

## Dependencies
- `AGENTS.md` [[AGENTS]] (root) — Defines the CONTEXT.md schema and LOG.md format that these templates implement. The templates must match the rules in AGENTS.md at all times.

## Known Issues
- If the CONTEXT.md schema or LOG.md format in root AGENTS.md changes, the templates here must be updated to match. Drift between the rules and the templates is the fastest way to corrupt the system.

## Revision History
- 2026-05-12 — Initial creation with CONTEXT.md and LOG.md templates.
- 2026-05-12 — Expanded CONTEXT.md with detailed inputs, outputs, and usage procedure.
- 2026-05-12 — Added dependency on root CLAUDE.md and drift risk to Known Issues.
- 2026-05-12 — Added verification checklist step to template usage procedure.
- 2026-05-12 — Updated checklist reference to cover all sections (Contents, Inputs, Outputs, Steps, Dependencies, Known Issues).
- 2026-06-09 — Dependencies and references updated from CLAUDE.md to AGENTS.md (AI-agnostic transition).
- 2026-07-02 - Added SKILL.md.template (with the required Verification section) and documented its placeholders. Part of the verification-discipline work.
- 2026-07-02 - Corrected the `{{TIMESTAMP}}` description to include the timezone offset (YYYY-MM-DDTHH:MM:SS±HH:MM), matching the AGENTS.md format requirement.
- 2026-07-03 - Replaced the em dash in the CONTEXT.md.template Revision History placeholder line with a hyphen, so new CONTEXT.md files no longer seed an ai-style-guard em-dash warning.
