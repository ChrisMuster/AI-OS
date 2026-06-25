# Config

**Last modified:** 2026-06-25

## Purpose
Holds the AI-style guard's tells definition as a single tracked source of truth. One file defines the tier-1 typographic markers and stock phrases (WARN) and the tier-2 single-word denylist (INFO). The AGENTS.md and SOUL.md writing-style rules point at this file, so the written rule and the enforced rule cannot drift apart. The file is generic and carries no personal data, so it is safe in version control.

## Contents
- ai-tells.yaml - `workflows/ai-style-guard/config/ai-tells.yaml` [[workflows/ai-style-guard/config/CONTEXT]] - The tells definition: typographic markers defined by Unicode code point (so the file stays plain ASCII), multi-word stock phrases, and a tunable single-word denylist. Seeded from the SOUL.md production-writing list.

## Inputs
None. This directory is read by the guard script; it is not a workflow itself.

## Outputs
None directly. `ai-tells.yaml` feeds the guard's detectors at runtime.

## Steps
N/A - this is a configuration directory, not a workflow.

## Dependencies
- `workflows/ai-style-guard/scripts/run.py` [[workflows/ai-style-guard/scripts/CONTEXT]] - Reads `ai-tells.yaml` at runtime.
- `SOUL.md` [[SOUL]] and `AGENTS.md` [[AGENTS]] (root) - The writing-style rules that seeded and reference this config.

## Known Issues
- Typographic markers are defined by code point, not literal character, on purpose: it keeps this tracked file plain ASCII and stops it being a source of the very markers it defines.
- The tier-2 word denylist is advisory (INFO) because many entries are legitimate technical words; tune it here as needed.

## Revision History
- 2026-06-25 - Initial creation. Single tracked `ai-tells.yaml` source of truth, seeded from the SOUL.md production-writing list.
