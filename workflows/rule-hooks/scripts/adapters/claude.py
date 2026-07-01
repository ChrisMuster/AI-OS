#!/usr/bin/env python3
"""Claude Code adapter (reference implementation).

Claude fires PreToolUse with a stdin JSON payload:
    {"hook_event_name":"PreToolUse","tool_name":"Bash",
     "tool_input":{"command":"..."}, "cwd":"...", ...}
Write tools carry tool_input.file_path plus content (Write) / new_string (Edit)
/ edits[].new_string (MultiEdit). The project root is in $CLAUDE_PROJECT_DIR.

Block contract: exit code 2 with the reason on stderr - Claude feeds stderr
back to the model as the reason the tool was denied.
"""

import os
import sys

from core import Context

BLOCK_EXIT = 2

_SHELL_TOOLS = {"Bash"}
_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def root_from_event(event):
    return os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd")


def _write_content(tool_input):
    if tool_input.get("content") is not None:
        return tool_input.get("content")
    if tool_input.get("new_string") is not None:
        return tool_input.get("new_string")
    if isinstance(tool_input.get("edits"), list):
        return "\n".join(
            str(e.get("new_string", "")) for e in tool_input["edits"]
        )
    if tool_input.get("new_source") is not None:  # NotebookEdit
        return tool_input.get("new_source")
    return None


def parse_event(event, project_root):
    tool = event.get("tool_name")
    tool_input = event.get("tool_input") or {}
    event_name = event.get("hook_event_name")

    if tool in _SHELL_TOOLS:
        return Context(
            ai_id="claude", category="shell", event_name=event_name,
            tool_name=tool, command=tool_input.get("command"),
            project_root=project_root, raw=event,
        )
    if tool in _WRITE_TOOLS:
        file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
        return Context(
            ai_id="claude", category="write", event_name=event_name,
            tool_name=tool, file_path=file_path,
            content=_write_content(tool_input),
            project_root=project_root, raw=event,
        )
    return None


def emit_block(decision, ctx):
    sys.stderr.write(decision.reason + "\n")
    return BLOCK_EXIT
