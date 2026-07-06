# Triggers

**Last modified:** 2026-07-06

## Purpose
A single, AI-agnostic registry of the natural-language phrases that trigger Book
Dragon actions, and the command each one runs. It is the universal replacement
for a per-AI slash-command set: instead of building `.claude/commands/*.md`,
`.gemini/commands/*.toml`, and so on for every supported AI, the phrases live in
one tracked file that `AGENTS.md` [[AGENTS]] points every AI at. Ask "give me a list of
triggers" (or "list the skills") and `run.py --list` prints the grouped set.

## Contents
- config/ - `workflows/triggers/config/` [[workflows/triggers/config/CONTEXT]]
  - triggers.yaml, the single source of truth: each category maps to its trigger
    phrases and the command it runs.
- scripts/ - `workflows/triggers/scripts/` [[workflows/triggers/scripts/CONTEXT]]
  - run.py (`--list` renders the registry; `--json` for machine output).
- tests/ - `workflows/triggers/tests/` [[workflows/triggers/tests/CONTEXT]]
  - hermetic tests for the loader and the rendered listing.

## Inputs
- config/triggers.yaml. No user input required to list; `--category` optionally
  filters to one category.

## Outputs
- A grouped, human-readable listing of categories, phrases, and commands (stdout),
  or the same as JSON with `--json`. Read-only; writes nothing.

## Steps
1. The user asks for the trigger/skill list (or names an action).
2. Biblio runs `run.py --list` (optionally `--category NAME`).
3. The registry is read from config/triggers.yaml and printed grouped by category.
4. Append LOG.md with a completion or failure entry.

## Dependencies
- `workflows/triggers/scripts/` [[workflows/triggers/scripts/CONTEXT]] - the loader
  and renderer.
- `config/triggers.yaml` - the registry data (single source of truth).
- `AGENTS.md` [[AGENTS]] - documents the registry and the list-on-demand behaviour;
  the actions themselves live in their own workflows.
- PyYAML - to parse the registry (already a project dependency).

## Known Issues
- The registry is documentation of intent, not an enforcement layer: it records
  which phrases map to which action, but recognising a phrase in conversation is
  still the AI's job. Keep it in sync when actions are added or renamed.

## Revision History
- 2026-07-03 - Initial creation. triggers.yaml registry, run.py --list renderer,
  and hermetic tests. The AI-agnostic replacement for a per-AI slash-command set;
  part of best-practices umbrella Bucket-1 child #4.
- 2026-07-06 - Registered the memory-diff category (the "what changed in memory"
  trigger) in triggers.yaml, taking the registry to eleven categories; added for
  best-practices umbrella Bucket-1 child #5.
