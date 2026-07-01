#!/usr/bin/env python3
"""Rule A4 - LOG.md must be written via Edit/append_log, not the shell.

Blocks a shell command that writes into a `*LOG.md` file - a `>` / `>>`
redirect or a cp/mv/tee whose target is LOG.md. The remedy is the Edit tool or
the append_log MCP tool, which keep the entry format correct and the timestamp
real. Detection parses the command, so a quoted mention of `>> LOG.md` inside
an argument is not matched.
"""

import os

from core import Decision, format_block, shell_write_targets

RULE_ID = "A4"


def _targets_logmd(command):
    for target in shell_write_targets(command):
        basename = os.path.basename(target.replace("\\", "/"))
        if basename.lower().endswith("log.md"):
            return basename
    return None


def check(ctx):
    if not ctx.command:
        return None
    basename = _targets_logmd(ctx.command)
    if not basename:
        return None
    return Decision.block(RULE_ID, format_block(
        f"Writing to `{basename}` from the shell is not allowed.",
        "LOG.md is an append-only audit trail with a fixed entry format and a "
        "real ISO-8601 timestamp. A shell redirect skips that format and is "
        "easy to corrupt.",
        "Use the Edit tool to append the entry, or the append_log MCP tool "
        "(which fills in the timestamp for you). Format: "
        "`[TIMESTAMP] | Actor: Biblio | Action: ... | Note: ...`",
    ))
