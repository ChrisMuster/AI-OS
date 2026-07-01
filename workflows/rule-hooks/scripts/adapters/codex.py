#!/usr/bin/env python3
"""Codex CLI adapter.

Codex shipped PreToolUse hooks ~Apr 2026 (config in `.codex/config.toml`
`[hooks]` or `.codex/hooks.json`). PreToolUse can deny (not modify) and covers
Bash / apply_patch / MCP. There is no project-root env var - the root equivalent
arrives as `cwd` in the stdin payload.

Block contract: Codex denies via a `hookSpecificOutput` JSON object on stdout.
Do not emit `continue:false`: current Codex parses it but does not support it
for PreToolUse, marks the hook failed, and lets the tool continue.
"""

import json
import sys

from core import Context

BLOCK_EXIT = 0

_SHELL_TOOLS = {"Bash", "shell", "local_shell", "exec"}
_PATCH_TOOLS = {"apply_patch", "edit", "write"}


def root_from_event(event):
    return event.get("cwd") or (event.get("tool_input") or {}).get("cwd")


def _patch_path_and_content(patch_text):
    """Best-effort parse of an apply_patch body -> (first file path, added text).

    apply_patch headers look like `*** Update File: path`, `*** Add File: path`,
    `*** Delete File: path`; added/changed lines start with `+`.
    """
    file_path, added = None, []
    for line in (patch_text or "").splitlines():
        stripped = line.strip()
        for marker in ("*** Update File:", "*** Add File:", "*** Delete File:"):
            if stripped.startswith(marker):
                if file_path is None:
                    file_path = stripped[len(marker):].strip()
                break
        else:
            if line.startswith("+") and not line.startswith("+++"):
                added.append(line[1:])
    return file_path, ("\n".join(added) if added else None)


def parse_event(event, project_root):
    tool = event.get("tool_name") or event.get("tool")
    tool_input = event.get("tool_input") or event.get("input") or {}
    event_name = event.get("hook_event_name") or event.get("event")

    if tool in _SHELL_TOOLS:
        command = tool_input.get("command")
        if isinstance(command, list):
            command = " ".join(command)
        return Context(
            ai_id="codex", category="shell", event_name=event_name,
            tool_name=tool, command=command,
            project_root=project_root, raw=event,
        )
    if tool in _PATCH_TOOLS:
        patch_text = (
            tool_input.get("command") or tool_input.get("patch") or tool_input.get("input")
            or tool_input.get("content") or ""
        )
        file_path = tool_input.get("file_path")
        content = tool_input.get("content")
        if patch_text and (file_path is None or content is None):
            parsed_path, parsed_content = _patch_path_and_content(patch_text)
            file_path = file_path or parsed_path
            content = content if content is not None else parsed_content
        return Context(
            ai_id="codex", category="write", event_name=event_name,
            tool_name=tool, file_path=file_path, content=content,
            project_root=project_root, raw=event,
        )
    return None


def emit_block(decision, ctx):
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": decision.reason,
        }
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    return BLOCK_EXIT
