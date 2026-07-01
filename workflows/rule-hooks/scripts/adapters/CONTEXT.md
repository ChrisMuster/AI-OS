# Rule Hooks - Adapters

**Last modified:** 2026-07-01

## Purpose
The only AI-specific code in the evaluator. Each adapter turns one AI's raw hook payload into a normalised `Context`, exposes that AI's project-root channel, and renders a block in that AI's block contract. One source of truth per rule; the per-AI glue (input shapes and output contracts differ) lives here.

## Contents
- __init__.py - `workflows/rule-hooks/scripts/adapters/__init__.py` [[workflows/rule-hooks/scripts/adapters/CONTEXT]] - Adapter registry: `get(ai_id)` returns the module for an AI id.
- claude.py - Claude Code adapter (reference). PreToolUse stdin JSON; root from `$CLAUDE_PROJECT_DIR`; block via exit 2 + stderr.
- codex.py - Codex CLI adapter. PreToolUse stdin JSON with `cwd` as root; apply_patch patch parsing from `tool_input.command`; block via `hookSpecificOutput.permissionDecision="deny"`.

## Inputs
- A firing AI's raw hook event (a parsed JSON dict) plus the resolved project root.

## Outputs
- A `Context` (or `None` for an unguarded tool); on a block, the AI's block-contract output and the process exit code.

## Steps
N/A. Adapter modules consumed by `run.py`.

## Dependencies
- `workflows/rule-hooks/scripts/core.py` [[workflows/rule-hooks/scripts/CONTEXT]] - The `Context` type.

## Known Issues
- The Codex adapter has been corrected against the live Codex hook docs and local smoke test evidence: `continue:false` is not supported for PreToolUse and causes Codex to continue, so blocks must use `hookSpecificOutput.permissionDecision="deny"`. Codex live blocking is confirmed for A7 on shell redirect, apply_patch, and cp writes to `.env.hooktest`.
- Phase 1 ships Claude and Codex only; further adapters (Cursor, Gemini, Windsurf/Devin, Copilot, Cline, OpenCode) are added on demand in Phase 2+.

## Revision History
- 2026-06-30 - Initial creation: Claude and Codex adapters plus the registry.
- 2026-07-01 - Corrected the Codex adapter after live verification failed: parse apply_patch bodies from `tool_input.command` and emit the supported `hookSpecificOutput.permissionDecision="deny"` block contract instead of the unsupported `continue:false` field. Confirmed live A7 blocks for shell redirect, apply_patch, and cp writes to `.env.hooktest`.
