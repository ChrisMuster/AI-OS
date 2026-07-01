#!/usr/bin/env python3
"""Rule A6 - block destructive shell commands (net-new, from the benchmark).

Covers `rm -rf`, `git push --force`, `git reset --hard`, `git clean -fd/-fdx`,
and destructive SQL (DROP TABLE/DATABASE, TRUNCATE). A hard block paired with
block-and-explain: the exact command is handed back so a genuinely-wanted
destructive op is a deliberate manual act, not an AI decision.

Detection PARSES the command (per the build guardrail): it splits the command
into pipeline/sequence segments and inspects each segment's program and flags,
so `echo "rm -rf"` is never flagged - only an actual `rm` invocation is.
"""

import re

from core import (
    Decision, format_block, posix_basename, split_flags, split_segments,
)

RULE_ID = "A6"

# Destructive SQL, matched only when a known SQL client is the program.
_DESTRUCTIVE_SQL = re.compile(
    r"\b(DROP\s+(TABLE|DATABASE|SCHEMA)|TRUNCATE\s+TABLE)\b", re.IGNORECASE,
)
_SQL_CLIENTS = {"sqlite3", "psql", "mysql", "mariadb", "mysqlsh"}


def _segment_danger(argv):
    """Return a short description if this argv is destructive, else None."""
    if not argv:
        return None
    prog = posix_basename(argv[0])
    args = argv[1:]
    short, long = split_flags(args)

    if prog == "rm":
        recursive = "r" in short or "R" in short or "recursive" in long
        force = "f" in short or "force" in long
        if recursive and force:
            return "rm -rf (recursive, forced delete)"

    if prog == "git":
        sub = next((a for a in args if not a.startswith("-")), "")
        if sub == "push":
            if ("f" in short or "force" in long) and "force-with-lease" not in long:
                return "git push --force (overwrites remote history)"
        elif sub == "reset" and "hard" in long:
            return "git reset --hard (discards uncommitted work)"
        elif sub == "clean":
            if ("f" in short or "force" in long) and ("d" in short or "x" in short):
                return "git clean -fd/-fdx (deletes untracked files)"

    if prog in _SQL_CLIENTS and _DESTRUCTIVE_SQL.search(" ".join(args)):
        return "destructive SQL (DROP/TRUNCATE)"

    return None


def check(ctx):
    if not ctx.command:
        return None
    segments = split_segments(ctx.command)
    if segments is None:
        return None  # unparseable -> cannot determine -> allow (fail mode)
    for argv in segments:
        danger = _segment_danger(argv)
        if danger:
            return Decision.block(RULE_ID, format_block(
                f"This command is blocked: {danger}.",
                "Destructive commands that delete files, rewrite history, or "
                "drop data are irreversible. The block makes sure such an "
                "action is a deliberate human decision, not an AI default.",
                f"If you genuinely intend this, run it yourself in the "
                f"terminal: `{ctx.command.strip()}`",
            ))
    return None
