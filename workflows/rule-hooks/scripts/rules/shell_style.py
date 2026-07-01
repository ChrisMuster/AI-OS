#!/usr/bin/env python3
"""Trial rules A2 and A3 - log-only in Phase 1 (warn, never block).

These two have a higher false-positive risk than the hard rules, so they ship
in watching-only mode: each fire is written to the fire-log for review, and the
action is never blocked. Once the fire-log shows the false-positive rate is
clean, they can be promoted to blocking (a Phase-2 decision).

A2 - forward slashes in Bash: flag a path-shaped backslash in a command argument.
A3 - dedicated tools over shell: flag a file-targeted cat/sed/find/grep/head/tail
     (or echo redirected into a file) where a dedicated tool should be used.
"""

from core import (
    Decision, PATH_BACKSLASH_RE, posix_basename, redirect_targets, split_segments,
)

A2_ID = "A2"
A3_ID = "A3"

_DEDICATED = {"cat", "sed", "find", "grep", "head", "tail"}


def check_forward_slashes(ctx):
    """A2 (trial): a path-shaped backslash in the command -> warn.

    Scans the RAW command, not the tokenised form: POSIX shlex consumes
    backslashes as escapes, so a path-shaped backslash would vanish before a
    token-level check could see it. Coarse by design - this is a watching-only
    trial whose fire-log is the input for tuning before it could ever block.
    """
    if not ctx.command:
        return None
    match = PATH_BACKSLASH_RE.search(ctx.command)
    if match:
        return Decision.warn(A2_ID, (
            f"A2 (trial): path-shaped backslash `{match.group(0)}` - Bash paths "
            f"should use forward slashes. Logged, not blocked."
        ))
    return None


def check_dedicated_tools(ctx):
    """A3 (trial): a shell tool used where a dedicated tool fits -> warn."""
    if not ctx.command:
        return None
    segments = split_segments(ctx.command)
    if segments is None:
        return None
    for argv in segments:
        if not argv:
            continue
        prog = posix_basename(argv[0])
        if prog in _DEDICATED:
            return Decision.warn(A3_ID, (
                f"A3 (trial): `{prog}` used in shell - prefer the dedicated "
                f"Read/Grep/Glob tool. Logged, not blocked."
            ))
    if segments:
        first = segments[0]
        if first and posix_basename(first[0]) == "echo" and redirect_targets(ctx.command):
            return Decision.warn(A3_ID, (
                "A3 (trial): `echo` redirected into a file - prefer the Write/"
                "Edit tool. Logged, not blocked."
            ))
    return None
