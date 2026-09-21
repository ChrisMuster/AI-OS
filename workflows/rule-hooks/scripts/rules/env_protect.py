#!/usr/bin/env python3
"""Rule A7 - protect .env / credential files from AI-initiated writes.

Blocks an AI writing to a `.env` or `.env.<suffix>` file (the place
credentials live, gitignored), except `.env.example` which is a safe tracked
template. Two write paths are covered: the Edit/Write tools (via the target
file path) and a shell write into a `.env` file - a `>` / `>>` redirect or a
cp/mv/tee whose target is a `.env` file, the same back door A4 closes for
LOG.md. The shell branch parses the command, so a quoted mention of `> .env`
inside an argument is not matched. Reads are allowed - the risk is the AI
silently rewriting secrets, not reading them.
"""

import os

from core import Decision, format_block, shell_write_targets

RULE_ID = "A7"


def _is_env_target(basename):
    # Case-folded, because this is the only rule of the five that was not:
    # A4 uses IGNORECASE and A9 lowercases, while this one compared exactly.
    # On Windows and macOS `.ENV` IS `.env`, so `echo K=v | tee .ENV` wrote
    # the real file and the guard written for that file did not fire. It
    # affected the Edit/Write path as well as the shell path, so it was a
    # bypass of the tool-level guard rather than a shell-parsing nicety.
    name = basename.lower()
    if name == ".env.example":
        return False
    return name == ".env" or name.startswith(".env.")


def _shell_written_env(command):
    """Basename of the first shell write target that is a .env file, or None."""
    for target in shell_write_targets(command):
        basename = os.path.basename(target.replace("\\", "/"))
        if _is_env_target(basename):
            return basename
    return None


def _block(basename):
    return Decision.block(RULE_ID, format_block(
        f"Writing `{basename}` is not allowed.",
        "`.env` files hold credentials and are gitignored. The AI must never "
        "rewrite them, because a silent change to a secret is hard to spot and "
        "easy to get wrong.",
        f"Tell me the variable name and value and I will show you the exact "
        f"line; you add it to `{basename}` yourself (or add a placeholder to "
        f"`.env.example`, which is safe to edit).",
    ))


def check(ctx):
    # Edit/Write path: the tool names the target file directly.
    if ctx.file_path:
        basename = os.path.basename(ctx.file_path.replace("\\", "/"))
        if _is_env_target(basename):
            return _block(basename)
    # Shell path: a redirect (`>` / `>>`) or cp/mv/tee into a .env file.
    if ctx.command:
        basename = _shell_written_env(ctx.command)
        if basename:
            return _block(basename)
    return None
