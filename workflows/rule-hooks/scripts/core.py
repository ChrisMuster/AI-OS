#!/usr/bin/env python3
"""core.py - shared contract for the rule-hooks evaluator.

This module holds the small, AI-agnostic types and helpers that both the entry
point (run.py), the rules, and the per-AI adapters import. Keeping them here
(rather than in run.py) avoids an import cycle: run.py imports rules and
adapters; rules and adapters import this.

Nothing here touches the filesystem at import time, so it stays cheap to load on
every hook fire.
"""

import re
import shlex


# ---------------------------------------------------------------------------
# Context and Decision - the internal contract
# ---------------------------------------------------------------------------
class Context:
    """A firing tool event, normalised to common fields plus the raw payload.

    Rules read the normalised fields; ``raw`` is the escape hatch so a rule can
    reach an AI-specific field without the adapter having to anticipate it.

    ``category`` is one of "shell", "write", "read" - the adapter sets it from
    the firing tool so the dispatcher knows which rule set applies. A value of
    ``None`` means "not a guarded tool"; the adapter returns ``None`` instead in
    that case, so a Context always has a real category.
    """

    __slots__ = (
        "ai_id", "category", "event_name", "tool_name",
        "command", "file_path", "content", "project_root", "raw",
    )

    def __init__(self, ai_id, category, event_name=None, tool_name=None,
                 command=None, file_path=None, content=None,
                 project_root=None, raw=None):
        self.ai_id = ai_id
        self.category = category
        self.event_name = event_name
        self.tool_name = tool_name
        self.command = command
        self.file_path = file_path
        self.content = content
        self.project_root = project_root
        self.raw = raw if raw is not None else {}


class Decision:
    """The result of a rule check: allow (None is also "allow"), block, or warn.

    A blocking decision carries the three-part block-and-explain ``reason``
    (see :func:`format_block`). A warn is advisory - it is collected and
    fire-logged but never stops the action (used by the A2/A3 trial rules).
    """

    ALLOW = "allow"
    BLOCK = "block"
    WARN = "warn"

    __slots__ = ("action", "rule", "reason")

    def __init__(self, action, rule=None, reason=None):
        self.action = action
        self.rule = rule
        self.reason = reason

    @classmethod
    def block(cls, rule, reason):
        return cls(cls.BLOCK, rule=rule, reason=reason)

    @classmethod
    def warn(cls, rule, reason):
        return cls(cls.WARN, rule=rule, reason=reason)


def format_block(blocked, why, manual):
    """Render the consistent three-part block-and-explain message (rule 2.3).

    Every hard block ships with the exact manual path, which is the only reason
    a hard block is acceptable. Plain ASCII so it renders cleanly in any
    AI's channel (stderr, JSON reason field, or a thrown message).
    """
    return (
        f"BLOCKED: {blocked}\n\n"
        f"Why: {why}\n\n"
        f"To do it manually: {manual}"
    )


# ---------------------------------------------------------------------------
# Shell-command parsing (shared by A4, A6, A7)
# ---------------------------------------------------------------------------
# The dangerous-bash and LOG-redirect rules must PARSE the command, not
# substring-match it, so that `echo "never run rm -rf"` is not flagged. shlex
# with punctuation_chars treats ();<>|& as their own tokens, so shell operators
# and redirections separate cleanly from program names and arguments while
# quoted strings stay intact.

_SEGMENT_SEPS = {";", "&&", "||", "|", "&", "\n"}
_REDIR_OPS = {">", ">>", "<", ">|", "&>", "&>>", "<<"}
# Output redirections that create/overwrite/append their target file.
_WRITE_REDIR_OPS = (">", ">>", ">|", "&>", "&>>")


def _tokenize(command):
    """Tokenise a shell command, raising ValueError on unbalanced quotes."""
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    return list(lex)


def split_segments(command):
    """Split a command line into pipeline/sequence segments of argv tokens.

    Redirection operators and their targets are dropped from each segment's
    argv, so a segment is just the program and its arguments. Returns a list of
    argv lists, or None if the command cannot be parsed (caller treats an
    unparseable command as "cannot determine" -> allow, per the fail mode).
    """
    try:
        tokens = _tokenize(command)
    except ValueError:
        return None
    segments, current, i = [], [], 0
    while i < len(tokens):
        token = tokens[i]
        if token in _SEGMENT_SEPS:
            if current:
                segments.append(current)
                current = []
        elif token in _REDIR_OPS:
            i += 1  # skip the redirection target token as well
        else:
            current.append(token)
        i += 1
    if current:
        segments.append(current)
    return segments


def redirect_targets(command):
    """Return the targets of output redirections (`>`, `>>`, `>|`, `&>`, `&>>`)
    in a command, or []."""
    try:
        tokens = _tokenize(command)
    except ValueError:
        return []
    targets = []
    for i, token in enumerate(tokens):
        if token in _WRITE_REDIR_OPS and i + 1 < len(tokens):
            targets.append(tokens[i + 1])
    return targets


def posix_basename(arg):
    """Program basename, handling both slash styles (e.g. /usr/bin/rm -> rm)."""
    return arg.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


# Commands whose operand(s) name a file the command writes to, beyond a
# redirection. cp/mv write their last operand (the destination), or - with
# -t / --target-directory - their source operands into a directory; tee writes
# every file operand; sed -i edits its file operand in place.
_DEST_LAST = {"cp", "mv"}
_DEST_ALL = {"tee"}


def _has_target_dir_flag(args):
    """True if a cp/mv command uses -t / --target-directory, in which case the
    source operands (not the last operand) are the files being written."""
    for a in args:
        if a == "-t" or a == "--target-directory" or a.startswith("--target-directory="):
            return True
        if a.startswith("-") and not a.startswith("--") and "t" in a[1:]:
            return True
    return False


def _sed_in_place(args):
    """True if a sed command edits its file operand in place (-i / --in-place,
    including the `-i.bak` backup-suffix form)."""
    return any(
        a == "-i" or a.startswith("-i") or a.startswith("--in-place")
        for a in args
    )


def shell_write_targets(command):
    """File targets a shell command writes to.

    Covers output redirections (`>`, `>>`, `>|`, `&>`, `&>>`), the destination
    of cp/mv (including the `-t` / `--target-directory` forms), the file operands
    of tee, and the in-place file operand of `sed -i`, so a protected file
    (LOG.md for A4, .env for A7) cannot be overwritten through the shell.
    Returns a list of raw target strings (the caller takes the basename). Parses
    the command, so a quoted mention is never a target; falls back to the
    redirect targets alone if the argv cannot be parsed.
    """
    targets = list(redirect_targets(command))
    segments = split_segments(command)
    if segments is None:
        return targets
    for argv in segments:
        if not argv:
            continue
        prog = posix_basename(argv[0])
        args = argv[1:]
        operands = [a for a in args if not (a.startswith("-") and len(a) > 1)]
        if prog in _DEST_LAST:
            if _has_target_dir_flag(args):
                targets.extend(operands)        # sources written into a directory
            elif len(operands) >= 2:
                targets.append(operands[-1])    # last operand is the destination
        elif prog in _DEST_ALL and operands:
            targets.extend(operands)            # tee writes every file operand
        elif prog == "sed" and operands and _sed_in_place(args):
            targets.append(operands[-1])        # sed -i edits the file in place
    return targets


def split_flags(args):
    """Return (short_chars, long_names) from an argv tail.

    `-rf` -> short {'r','f'}; `--force` -> long {'force'};
    `--depth=2` -> long {'depth'}. Non-flag operands are ignored.
    """
    short_chars, long_names = set(), set()
    for arg in args:
        if arg.startswith("--") and len(arg) > 2:
            long_names.add(arg[2:].split("=", 1)[0])
        elif arg.startswith("-") and len(arg) > 1 and not arg[1:2].isdigit():
            for char in arg[1:]:
                short_chars.add(char)
    return short_chars, long_names


# A path-shaped backslash token: word chars on both sides of a backslash, e.g.
# `workflows\rule-hooks`. Used by the A2 trial rule (warn only).
PATH_BACKSLASH_RE = re.compile(r"[\w.\-]+\\[\w.\-]+")
