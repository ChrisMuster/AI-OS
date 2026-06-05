# Memory

**Last modified:** 2026-06-05

## Purpose

Project-scoped persistent memory for Book Dragon. All memory lives here rather than in the per-user Claude cache so it syncs with the project and is available on any machine that opens this folder.

## Contents

- `CONTEXT.md` — this file. Structural documentation; tracked in git.
- `MEMORY.md` — the index. Loaded at every session start. One line per memory, under 150 chars each. Not tracked in git; created on first-run.
- `LOG.md` — append-only journal of all memory changes. Not tracked in git; created on first-run.
- Individual memory files, named `<type>_<short-name>.md`. Types: `feedback`, `reference`, `user`, `project`. Not tracked in git.

## Inputs

None. Memory files are written by Biblio during sessions as relevant facts emerge from conversation.

## Outputs

None. This directory is a store, not a workflow.

## Steps

N/A. This is a store directory, not a workflow. See the "Project memory" rule in `CLAUDE.md` [[CLAUDE]] for the full read procedure, write rules, and four-step write procedure.

## Dependencies

- `CLAUDE.md` [[CLAUDE]] (root) — defines the session startup sequence and all rules governing when and how to read and write memory.

## Known Issues

- The old per-user cache at `~/.claude/projects/<sanitized-cwd>/memory/` may still exist on machines where it was previously written to. New writes must land in this directory. If a conflict arises, this project-scoped memory takes precedence.
- This file currently references `CLAUDE.md` [[CLAUDE]] as its governing rules file. When Book Dragon becomes AI-agnostic, `CLAUDE.md` [[CLAUDE]] will be replaced by a different configuration layer and this dependency will need updating at that point.

## Revision History

- 2026-06-05 — Initial creation. Project-scoped memory system established per user specification.
- 2026-06-05 — Stripped to standard CONTEXT.md schema. Procedural content consolidated into CLAUDE.md "Project memory" rule. Privacy and git rules added.
