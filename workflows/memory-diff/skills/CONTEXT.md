# Memory Diff - Skills

**Last modified:** 2026-07-06

## Purpose
Container for the memory-diff workflow's skill. Holds the one skill this workflow
uses to turn the deterministic delta into a one-line summary folded into the
greeting.

## Contents
- memory-diff/ - `workflows/memory-diff/skills/memory-diff/` [[workflows/memory-diff/skills/memory-diff/CONTEXT]] - the skill that summarises
  the memory delta for the greeting.

## Inputs
None directly. The skill it contains defines its own inputs.

## Outputs
None directly. The skill it contains defines its own outputs.

## Steps
N/A. This is a container directory, not a workflow itself.

## Dependencies
- `workflows/memory-diff/` [[workflows/memory-diff/CONTEXT]] - the parent workflow.

## Known Issues
- The Contents section must be updated if another skill is added here.

## Revision History
- 2026-07-06 - Initial creation with the memory-diff skill.
