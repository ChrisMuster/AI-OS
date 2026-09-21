#!/usr/bin/env python3
r"""Rules A2 (trial, warn-only) and A3 (blocking since 2026-09-02).

A2 - forward slashes in Bash: flag a path-shaped backslash in a command
     argument. Still a watching-only trial.
A3 - dedicated tools over shell: BLOCK a shell command that reads a file with
     cat/sed/find/grep/head/tail, or writes one with a redirected echo, where
     the project's Read/Grep/Glob/Edit/Write tools are the prescribed method.

**Why A3 stopped being a trial.** It shipped warn-only because it asked one
question - does this command mention one of six programs anywhere - and that
question cannot separate the violation from its opposite. `grep pattern file.md`
reads a file and has a dedicated tool. `run-tests | grep pattern` filters output
that never existed on disk, and no dedicated tool can do it, because Read and
Grep read files. Blocking on the old question would have refused the second,
which has no compliant alternative, and a hook that forbids necessary work is
one that gets worked around.

On 2026-09-02 the fire-log held 75 A3 fires from a single session, all from one
AI, mixing both kinds. That is a rule failing routinely rather than once, which
is the project's stated threshold for replacing prose with a mechanism. In the
same session the wrong-tool habit put a personal name into a publicly tracked
file, because the write went through a shell interpreter and so never reached
the B3 personal-data check that guards the Edit and Write tools.

So the rule now asks a sharper question: **does this command name a file that
actually exists?** If it does, it is reading a file and a dedicated tool applies.
If it does not, it is reading standard input and no tool could have done it.

**The byte-level allowance.** Some file reads genuinely have no tool equivalent:
counting carriage returns, finding a tab, checking for a NUL byte. This
project's own review ledger *requires* byte-level verification for line endings
and encoding ("verify in bytes, not in grep"), so blocking those would forbid a
check the rules elsewhere insist on. A read whose pattern is written as a byte
or control-character escape is therefore allowed - and warned, so the fire-log
records how often the allowance is leaned on and whether it is being used as a
loophole. An allowance nobody can measure is indistinguishable from a hole.

**What the question is asked *about* has been wrong more often than the question
itself.** Every defect closed in this rule since it started blocking has had the
same shape: "does this command name a file that exists" was right, and the
answer was computed against the wrong thing, so a real read looked like no read.
The list is kept because the shape recurs, not because each case is interesting.

- **A `cd` moved the goalposts** and was ignored, so `cd workflows/audit && grep
  p CONTEXT.md` found nothing at the root. The directory is now carried through
  the segments as a shell carries it.
- **A glob never exists** as a literal name, so every globbed read looked like a
  read of nothing. Globs are expanded and count when they match.
- **The allowance was granted to a whole line** rather than to the read that
  earned it, so one `$'\r'` check excused everything beside it. It is judged per
  segment, off that segment's own tokens.
- **A `cd` was assumed to succeed** and the operator joining the commands was
  ignored. `cd nowhere ; grep p README.md` really does read the file, and `cd
  nowhere && grep p ../README.md` really does not run. A `cd` now applies only
  when the directory is there, a failed one ends its `&&` chain, and one inside
  a subshell or a pipeline does not move the outer shell.
- **The pattern could arrive by flag.** `grep`'s leading operand is the pattern
  and was dropped on that basis even when `-e` or `-f` had already supplied it,
  which discarded a real file. `sed -e` had the same fault.
- **`find` with no path** walks the current directory; the rule read it as
  reading nothing.
- **An input redirection was invisible.** `cat < README.md` reads the file and
  names it, and the redirection was being discarded along with its target.
- **The command was not always the first word.** `sudo cat f`, `LC_ALL=C cat f`
  and `if grep -q p f; then` all put something else in front, and the rule read
  that something else as the program.
- **`grep -r` with no path** searches the working directory, exactly like `find`
  with no path, and only `find` had been given the rule.
- **The byte-level marker was matched against every word**, including the
  filename, so on Windows any path with a `\r`, `\t` or `\0` segment boundary -
  `workflows\rule-hooks\CONTEXT.md` is one - downgraded a real read to a
  warning. The marker must now be the whole word, which is what a byte pattern
  actually looks like.

Each fix widens what the rule catches, which is the direction that risks
refusing legitimate work, so each ships with the control that must not move: a
read after `cd` of a file that is not there, `cd` with no operand, a glob
matching nothing, a byte-level read standing alone, a pattern flag naming no
real file, a piped `grep` with no operand, `find --help`, and a `find` whose
job is to delete rather than to list.
"""
import re
from pathlib import Path

from core import (
    Decision, PATH_BACKSLASH_RE, effective_argv, format_block, negation_parity,
    option_letters,
    posix_basename, split_segments_detailed, strip_heredoc_bodies,
)

A2_ID = "A2"
A3_ID = "A3"

_DEDICATED = {"cat", "sed", "find", "grep", "head", "tail"}

# Leading non-flag operands that are NOT files. grep's first operand is the
# pattern and sed's is the script; find, cat, head and tail take a path first.
_NON_FILE_OPERANDS = {"grep": 1, "sed": 1}

# Flags that supply the pattern (grep) or the script (sed), so the leading
# operand is a file already and must NOT be dropped as though it were the
# pattern. Long forms are also recognised in their `--flag=value` spelling.
# Combined short flags (`-ie pattern`) are deliberately out of scope, the same
# boundary _VALUE_FLAGS keeps: there the leading operand is still consumed as
# the pattern, which is the existing conservative behaviour rather than a new
# gap.
_PATTERN_FLAGS = {
    "grep": {"-e", "--regexp", "-f", "--file"},
    "sed": {"-e", "--expression", "-f", "--file"},
}

# Flags that consume the following token as their value, so that token is not a
# file operand. Deliberately not exhaustive: an unlisted value-flag makes the
# rule see a file where there is none, and the fail direction for that is a
# block on a command that had no file, which the allowance below does not cover.
# Kept to the forms this project actually uses.
_VALUE_FLAGS = {
    "grep": {"-e", "-f", "-m", "-A", "-B", "-C", "--regexp", "--file",
             "--max-count", "--after-context", "--before-context", "--context",
             "--include", "--exclude", "--exclude-dir"},
    "sed": {"-e", "-f", "--expression", "--file"},
    "head": {"-n", "-c", "--lines", "--bytes"},
    "tail": {"-n", "-c", "--lines", "--bytes"},
    "find": {"-name", "-iname", "-path", "-ipath", "-type", "-maxdepth",
             "-mindepth", "-newer", "-size", "-perm", "-user",
             "-group", "-regex"},
}

# find flags that make the command act rather than list. `find . -name '*.pyc'
# -delete` is not a read and no dedicated tool performs it, so pointing at Glob
# would refuse work with no compliant alternative. The known cost is that
# `find ... -exec cat {} \;` is allowed with it; that is accepted as the same
# trade, since the alternative is refusing every `-delete` and `-exec`.
_FIND_ACTIONS = {"-delete", "-exec", "-execdir", "-ok", "-okdir"}

# A pattern written as a byte or control-character escape: `$'\r'`, `\t`,
# `\x0d`, `\0`. The escape survives tokenising because a backslash inside single
# quotes is literal, so `$'\r'` arrives as the word `$\r`.
#
# **The whole word must be the escape.** Matching it anywhere in any word meant
# a Windows path counted: `workflows\rule-hooks\CONTEXT.md` contains `\r`, that
# file exists, and a plain read of it was downgraded to a warning. A byte
# pattern is the entire argument, so requiring that is both narrower and a
# better description of the thing being allowed.
_BYTE_ESCAPE_RE = re.compile(r"\$?(?:\\r|\\n|\\t|\\0|\\x[0-9A-Fa-f]{2})+\Z")

# One character of a path-shaped word, used to widen A2's match back out for
# its message.
_PATH_CHAR_RE = re.compile(r"[\w.\-]")

# `tail -f` follows a growing file. No dedicated tool does that, so pointing at
# Read refuses work with no compliant alternative, which is the failure the
# rule's own promotion criteria say to avoid.
_FOLLOW_FLAGS = {"-f", "-F", "--follow", "--retry"}

_TOOL_FOR = {
    "cat": "Read", "head": "Read", "tail": "Read",
    "grep": "Grep", "sed": "Read (or Edit, to change it)", "find": "Glob",
}


def check_forward_slashes(ctx):
    """A2 (trial): a path-shaped backslash in the command -> warn.

    Scans the RAW command, not the tokenised form: a backslash outside quotes
    is an escape and would vanish before a token-level check could see it.
    Coarse by design - this is a watching-only trial whose fire-log is the
    input for tuning before it could ever block.
    """
    if not ctx.command:
        return None
    match = PATH_BACKSLASH_RE.search(ctx.command)
    if match:
        # The pattern matches the backslash alone (it is written as
        # lookarounds so it stays linear), so the surrounding word is recovered
        # here for the message rather than by the regex.
        start, end = match.start(), match.end()
        while start > 0 and _PATH_CHAR_RE.match(ctx.command[start - 1]):
            start -= 1
        while end < len(ctx.command) and _PATH_CHAR_RE.match(ctx.command[end]):
            end += 1
        return Decision.warn(A2_ID, (
            f"A2 (trial): path-shaped backslash `{ctx.command[start:end]}` - "
            f"Bash paths should use forward slashes. Logged, not blocked."
        ))
    return None


_GLOB_CHARS = ("*", "?", "[")
_MAX_GLOB_MATCHES = 8
_BRACE_RE = re.compile(r"\{([^{}]*,[^{}]*)\}")


def _first_matches(paths):
    """The first few entries of a glob, as a list.

    A bound rather than a full expansion, because the only question asked of
    the result is whether it is empty.
    """
    found = []
    for path in paths:
        found.append(path)
        if len(found) >= _MAX_GLOB_MATCHES:
            break
    return found
_MAX_BRACE_EXPANSION = 64


def _expand_braces(candidate):
    """`{README,AGENTS}.md` -> the names the shell would produce.

    The same gap the rule already records for globs, left open for braces: at
    hook time the shell has not expanded it, so the literal `{a,b}.md` is not
    any file's name and a plain existence test read a real two-file read as
    naming nothing. Capped, because a nested brace list multiplies out and this
    runs on every fire.
    """
    results = [candidate]
    while True:
        expanded = []
        for value in results:
            match = _BRACE_RE.search(value)
            if not match:
                expanded.append(value)
                continue
            head, tail = value[:match.start()], value[match.end():]
            expanded.extend(head + option + tail
                            for option in match.group(1).split(","))
        if expanded == results or len(expanded) > _MAX_BRACE_EXPANSION:
            return results
        results = expanded


def _resolved_paths(candidate, base):
    """The real paths an operand names, or [].

    Resolved against *base*, the directory the command is running in: the
    project root, or wherever an earlier `cd` moved to. A read-only stat or
    glob; nothing is opened. A base of None means the directory is not known,
    and a relative operand then resolves to nothing rather than being guessed
    at against the wrong place.

    **A glob operand is expanded rather than stat-ed.** At hook time the shell
    has not expanded it yet, so `workflows/audit/*.md` arrives as that literal
    string and no file is ever named exactly that. A glob matching nothing
    reads nothing and stays allowed.
    """
    try:
        path = Path(candidate).expanduser()
        root = Path(base) if base else None
        if any(char in candidate for char in _GLOB_CHARS):
            # Stopped after a handful of matches. The question is only whether
            # the operand names anything real, so the whole expansion is never
            # needed, and `**` walks the entire project: `grep -rn TODO
            # **/*.py` measured 5.9 seconds against a 5-second hook timeout,
            # and a hook that times out has allowed the command. Taking the
            # first few keeps it a generator and stops the walk early.
            if path.is_absolute():
                anchor = Path(path.anchor)
                return _first_matches(anchor.glob(str(path.relative_to(anchor))))
            return _first_matches(root.glob(candidate)) if root else []
        if path.is_absolute():
            return [path] if path.exists() else []
        if not root:
            return []
        resolved = root / path
        return [resolved] if resolved.exists() else []
    except (OSError, ValueError, IndexError, NotImplementedError):
        return []


def _reads_a_file(prog, candidate, base, recursive):
    """True when this operand is something the command actually reads.

    A directory is a read for `find`, and for `grep` only when it was given a
    recursive flag. For everything else `cat workflows` is an error that reads
    nothing, and blocking it points at a tool that would not have helped.
    """
    paths = []
    for expanded in _expand_braces(candidate):
        paths.extend(_resolved_paths(expanded, base))
    for path in paths:
        if path.is_dir():
            if prog == "find" or (prog == "grep" and recursive):
                return True
            continue
        return True
    return False


def _flag_args(argv):
    """The part of *argv* that is still being read as flags.

    Everything after `--` is an operand, however many dashes it starts with.
    Without this, a dash-leading search pattern is read as a bundle of short
    flags: `grep -- -pattern README.md` contains an `r`, which made the command
    look recursive and so made it look like a directory walk.
    """
    args = argv[1:]
    return args[:args.index("--")] if "--" in args else args


# grep's short options that take a value. A letter in this set ends its bundle,
# because what follows is the value: `grep -e"Refactor"` sets `-e` and nothing
# else, and reading its letters as a bundle found an `R`, made grep look
# recursive, and blocked `git log | grep -e"Refactor"` as a walk of the tree.
_GREP_VALUE_LETTERS = set("efABCmdD")


def _is_recursive(argv):
    short, long = option_letters(argv[1:], _GREP_VALUE_LETTERS)
    return bool({"r", "R"} & short) or bool(
        {"recursive", "dereference-recursive"} & long)


CD_OK, CD_FAIL, CD_UNKNOWN = "ok", "fail", "unknown"


def _apply_cd(cwd, target):
    """`(status, directory)` after a `cd`. Three outcomes, not two.

    - **ok** - the directory is there, so the shell moves and reads after it
      resolve from the new place.
    - **fail** - the directory is not there. The shell does not move, and the
      next command runs where it always was. Applying the move regardless is
      what made a real read of a root-level file look like a read of something
      elsewhere, and so allowed it.
    - **unknown** - the destination cannot be established: `cd` with no operand
      goes to a home directory, `cd -` goes somewhere only the shell's history
      knows, and a relative move from an already-unknown place is unknown too.
      The directory becomes None, which resolves no relative operand and so
      blocks nothing, and the `&&` chain is left alone because an unknown
      status is not a failure.

    The third outcome is the one worth having. Folding it into "fail" would
    stop an `&&` chain that really runs, and folding it into "ok" would leave
    the rule resolving reads against a directory the command has left, which
    is how `cd && grep pattern README.md` came to be blocked for a file the
    command would never have found.
    """
    if target is None:
        return CD_UNKNOWN, None
    if "$" in target or "`" in target:
        # A destination the shell expands and we do not: `cd $PWD`,
        # `cd "$(pwd)"`, `cd $REPO`. It is the unknown-destination case this
        # function has a third state for, and calling it a FAILURE was wrong -
        # a failure ends the `&&` chain, so every command after it went
        # unexamined, including reads that name an absolute path and need no
        # working directory at all.
        #
        # **What this does NOT do, stated plainly because an earlier version of
        # this comment claimed otherwise.** It does not make `cd $PWD ; cat
        # README.md` block. An unknown directory resolves no relative operand,
        # so that read is still allowed. What changed is that the chain is no
        # longer killed and an absolute read after it is now seen.
        #
        # Leaving that gap open is a decision, taken by the user on
        # 2026-09-07, not an oversight. The alternative is to assume the `cd`
        # did not move the shell and resolve against the previous directory,
        # which catches this case and wrongly blocks a read after a `cd` that
        # really did move - a false block, which is the direction this rule set
        # treats as the more expensive one, because a guard that refuses honest
        # work is a guard people route around. Recorded in this directory's
        # CONTEXT.md Known Issues.
        return CD_UNKNOWN, None
    try:
        path = Path(target).expanduser()
        if path.is_absolute():
            return (CD_OK, str(path)) if path.is_dir() else (CD_FAIL, None)
        if cwd is None:
            return CD_UNKNOWN, None
        moved = Path(cwd) / path
        return (CD_OK, str(moved)) if moved.is_dir() else (CD_FAIL, None)
    except (OSError, ValueError):
        return CD_UNKNOWN, None


# A directory-stack position rather than a directory: `pushd +1`, `popd -0`.
_STACK_ROTATION_RE = re.compile(r"^[+-]\d+$")

# The options each builtin accepts. Anything else starting with `-` is an
# invalid option, which bash rejects without moving.
_DIRECTORY_VERB_OPTIONS = {"cd": set("LPe@"), "pushd": {"n"}, "popd": {"n"}}


def _parse_directory_args(prog, args):
    """`(no_move, position, operands, invalid)`, read as bash's builtin reads.

    **One reader for the three verbs' arguments, replacing a filter.** The
    model used to drop every dash-word and then count what was left, which is
    not how bash reads them, and each difference was a finding: options are
    read only BEFORE the first operand, so `pushd workflows -n` and `cd
    workflows -P` are too many arguments rather than a flag; an option bash
    does not accept (`pushd -nn`, `cd -x`) is an error that moves nothing; a
    lone `-` is the previous directory, not a missing operand; when several
    stack positions are given the LAST one is used; and after `--` a word such
    as `+1` is a directory name, not a position. All five were measured in bash.

    `popd` takes no operand at all, and accepts `-n` and a position in any
    order, so every other word is invalid for it.
    """
    allowed = _DIRECTORY_VERB_OPTIONS[prog]
    no_move, position, operands, invalid = False, None, [], False
    if prog == "popd":
        for arg in args:
            if arg == "-n":
                no_move = True
            elif _STACK_ROTATION_RE.match(arg):
                position = arg
            elif arg != "--":
                invalid = True
        return no_move, position, operands, invalid
    options_open = True
    for arg in args:
        if options_open and arg == "--":
            options_open = False
            continue
        if options_open and prog == "pushd" and _STACK_ROTATION_RE.match(arg):
            position = arg
            continue
        if options_open and arg.startswith("-") and arg != "-":
            letters = arg[1:]
            if (prog == "pushd" and letters != "n") or not set(letters) <= allowed:
                invalid = True
            elif letters == "n":
                no_move = True
            continue
        options_open = False
        operands.append(arg)
    return no_move, position, operands, invalid


def _apply_directory_verb(argv, cwd, cwd_stack, dir_stack, in_pipeline):
    """Apply `cd`, `pushd` or `popd` to the modelled directory state.

    Returns True when the verb succeeded, False when it failed, and None when
    its status cannot be established - which is not the same as a failure, and
    treating it as one killed an `&&` chain that really runs.

    **The three verbs are handled in one place so their exit status is decided
    once.** They were three separate branches, each working out for itself what
    to tell the `&&` and `||` flags, and the result was the shape this rule has
    now produced four times: `cd` was taught that success ends an `||` chain
    and neither stack verb was, so `pushd x ; popd || grep p file` blocked a
    read bash never performs. With one exit point there is one place to teach.

    A verb inside a pipeline runs in a child process, so it settles the chain
    (when it is the last stage) but moves no directory in the parent shell.
    That distinction is why the caller passes *in_pipeline* rather than the
    function asking.
    """
    prog = posix_basename(argv[0])
    # `-n` suppresses the directory change and leaves only the stack
    # manipulation. Without it, `pushd -n workflows` was modelled as moving the
    # shell, so a following read resolved somewhere the command never went.
    no_move, position, operands, invalid = _parse_directory_args(prog, argv[1:])
    if invalid or len(operands) > 1:
        # An invalid option, or too many arguments: bash reports it, returns
        # non-zero and moves nothing. Two operands used to read as none, which
        # sent `pushd` down the bare-swap path below.
        return False

    if prog == "popd" and position not in (None, "+0"):
        # A stack POSITION. `popd +N` removes one entry and, measured, leaves
        # the shell in place; `popd -N` counts from the other end and moves.
        # Which entry goes is something this model holds only approximately,
        # so every entry becomes unresolvable - but the stack still SHRINKS by
        # one, because blanking without shrinking modelled a later `popd` as a
        # certain success when bash reports the stack empty.
        if not dir_stack:
            return False
        if not in_pipeline:
            dir_stack.pop()
            dir_stack[:] = [None] * len(dir_stack)
            if position.startswith("-") and not no_move:
                cwd_stack[-1] = None
        return None

    if prog == "popd":
        # Plain `popd`, or `popd +0`, which removes the top entry and is the
        # same thing.
        if not dir_stack:
            # bash's `popd` on an empty stack fails, prints an error and leaves
            # the shell exactly where it was. The directory is deliberately not
            # blanked: a blank directory resolves no relative operand, which
            # was a one-word bypass of the whole rule.
            return False
        if not in_pipeline:
            restored = dir_stack.pop()
            if not no_move:
                cwd_stack[-1] = restored
        return True

    is_pushd = prog == "pushd"
    target = operands[0] if operands else None

    if is_pushd and position is not None:
        if target is not None:
            return False  # a position and a directory: too many arguments
        # `pushd +0` rotates by nothing and exits 0 without moving (measured,
        # including on an empty stack). Any other position moves to an entry
        # this model holds only approximately, so the stack is marked
        # unresolvable and the status left unknown rather than guessed.
        if position == "+0":
            return True
        if not in_pipeline:
            dir_stack[:] = [None] * len(dir_stack)
            if not no_move:
                cwd_stack[-1] = None
        return None

    if target == "-":
        # The previous directory. It is a real place the shell goes, and this
        # model does not track it, so the destination is unknown. It used to
        # be dropped with the dash-words and read as a missing operand, which
        # made `pushd -` the bare swap.
        if not in_pipeline:
            if is_pushd:
                dir_stack.append(None if no_move else cwd)
            if not no_move:
                cwd_stack[-1] = None
        return None

    if is_pushd and target is None:
        # Bare `pushd` is not bare `cd`. `cd` with no operand goes to a home
        # directory, which is unknowable but real. `pushd` with no operand
        # SWAPS the top of the directory stack with the current directory, and
        # with nothing on the stack it FAILS.
        if no_move:
            # Bare `pushd -n` changes neither the directory nor the stack and
            # exits 0, measured with stacks of one, two and three entries. It
            # was modelled as a rotation that blanked the directory, and on an
            # empty stack as a failure that killed its `&&` chain.
            return True
        if not dir_stack:
            return False
        if not in_pipeline:
            # Both halves of the swap are known, so model it exactly.
            dir_stack[-1], cwd_stack[-1] = cwd_stack[-1], dir_stack[-1]
        return True

    status, moved = _apply_cd(cwd, target)
    if is_pushd and no_move:
        # `pushd -n dir` never changes directory, so bash never checks that
        # the directory exists: `pushd -n nosuchdir` exits 0 and pushes the
        # name. Reading its unresolvable target as a failed `cd` killed a
        # chain bash really runs.
        if not in_pipeline:
            dir_stack.append(moved if status == CD_OK else None)
        return True
    if status == CD_FAIL:
        return False
    if not in_pipeline:
        if is_pushd:
            # The directory being LEFT is what a later `popd` returns to, and
            # it is known even when the DESTINATION is not. Pushing only on
            # CD_OK left the modelled stack empty where bash's was not, so the
            # following `popd` was read as a failure and killed its `&&` chain:
            # `pushd "$(pwd)/x" && popd && cat README.md` disabled the rule
            # entirely, for any read at all.
            dir_stack.append(cwd)
        cwd_stack[-1] = moved if status == CD_OK else None
    return True if status == CD_OK else None


def _plain_operands(prog, argv):
    """The non-flag words of *argv*, with flag values removed.

    The list before any program-specific dropping, so a caller can ask which
    operand is the pattern as well as which are files.
    """
    value_flags = _VALUE_FLAGS.get(prog, set())
    operands, skip_next, end_of_flags = [], False, False
    for arg in argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if not end_of_flags:
            if arg == "--":
                # Everything after `--` is an operand. Treating it as a flag
                # dropped it, and the pattern-drop then removed the real file.
                end_of_flags = True
                continue
            if arg in value_flags:
                skip_next = True
                continue
            if arg.startswith("-") and arg != "-":
                # `--flag=value` carries its value; a bare flag does not.
                continue
        operands.append(arg)
    return operands


def _pattern_from_flag(prog, argv):
    """The pattern a flag supplied, or None.

    Three spellings, and only the first two were recognised: `-e TODO`,
    `--regexp=TODO`, and the attached short form `-eTODO`. Missing the third
    was not a near-miss but an inversion: the rule concluded no flag had
    supplied the pattern, dropped the leading operand as though it were the
    pattern, and the leading operand was the FILE - so the read vanished.
    """
    flags = _PATTERN_FLAGS.get(prog)
    if not flags:
        return None
    short_flags = tuple(f for f in flags if not f.startswith("--"))
    long_forms = tuple(f + "=" for f in flags if f.startswith("--"))
    args = _flag_args(argv)
    for index, arg in enumerate(args):
        if arg in flags:
            return args[index + 1] if index + 1 < len(args) else ""
        if arg.startswith(long_forms):
            return arg.split("=", 1)[1]
        if arg.startswith(short_flags) and len(arg) > 2:
            return arg[2:]
    return None


def _pattern_supplied_by_flag(prog, argv):
    """True when a flag has already supplied grep's pattern or sed's script.

    When it has, the leading plain operand is a file rather than the pattern,
    so dropping it (as `_NON_FILE_OPERANDS` otherwise does) hides a real read.
    """
    return _pattern_from_flag(prog, argv) is not None


def _byte_level_pattern(prog, argv):
    """True when THIS command's search pattern is a byte or control escape.

    The allowance exists for one thing: a search whose pattern is a raw byte,
    which the dedicated tools cannot express. So it is asked of the pattern
    argument, not of the words in general.

    Asking it of any word was a one-token bypass: `cat README.md $'\\r'` reads
    README.md and earned the allowance from a spare operand, and `grep -e TODO
    README.md $'\\t'` did the same with the pattern already supplied by a flag.
    It also means the allowance now applies only to the programs that have a
    pattern at all - `cat` has none, so no `cat` can claim it.
    """
    if prog not in _PATTERN_FLAGS:
        return False
    supplied = _pattern_from_flag(prog, argv)
    if supplied is not None:
        return bool(_BYTE_ESCAPE_RE.match(supplied))
    operands = _plain_operands(prog, argv)
    return bool(operands) and bool(_BYTE_ESCAPE_RE.match(operands[0]))


def file_operands(prog, argv):
    """The file paths a dedicated-tool command names, or [].

    Empty means the command reads standard input, which is not the violation.
    Conservative on purpose: anything it cannot confidently read as a file is
    left out, so the rule under-reports rather than blocking a command that
    names no file at all.

    Three exceptions to "empty means stdin". `find` has no stdin form: given no
    path operand it walks the current directory, and `grep -r` does the same,
    so the directory in force is substituted. Where a flag has already supplied
    grep's pattern or sed's script the leading operand is not dropped, because
    it is a file rather than the pattern. And a `find` given an action rather
    than a listing job is not a read at all.
    """
    if prog == "find":
        if any(a in ("--help", "--version") for a in argv[1:]):
            return []
        if any(a in _FIND_ACTIONS for a in argv[1:]):
            return []
    if prog in ("tail", "head") and any(
            a.split("=", 1)[0] in _FOLLOW_FLAGS or (
                a.startswith("-") and not a.startswith("--") and "f" in a[1:])
            for a in _flag_args(argv)):
        # `--follow=name` is the standard spelling for following a rotated
        # log, and an exact-membership test missed it while exempting `-f` and
        # `--follow`, so the block named a tool that cannot follow a file.
        return []
    operands = _plain_operands(prog, argv)
    drop = 0 if _pattern_supplied_by_flag(prog, argv) else \
        _NON_FILE_OPERANDS.get(prog, 0)
    operands = operands[drop:]
    if not operands and (prog == "find"
                         or (prog == "grep" and _is_recursive(argv))):
        # Checked AFTER the pattern is dropped, not before: `grep -r pattern`
        # has one operand and it is the pattern, so testing the undropped list
        # never saw that no path had been given.
        return ["."]
    return operands


def check_dedicated_tools(ctx):
    """A3: a shell file-read or file-write where a dedicated tool is prescribed."""
    if not ctx.command:
        return None
    # Heredoc bodies are stripped for the A2-style raw scan only; the reader
    # removes them itself before parsing, because a body is data.
    command = strip_heredoc_bodies(ctx.command)
    parsed = split_segments_detailed(command)
    if parsed is None:
        return None  # unparseable: cannot determine, so allow

    # The working directory is a stack rather than a single value because
    # `( ... )` runs in a subshell: a `cd` inside one applies to the rest of
    # that subshell and is undone when it closes.
    cwd_stack = [ctx.project_root]
    # `pushd` saves the directory it left and `popd` returns to it. Modelling
    # the push without the pop left the rule resolving reads in a directory the
    # command had already come back from, which blocked a read of a file that
    # was never reached.
    #
    # **One stack per shell depth, not one stack.** The directory stack belongs
    # to a shell: a subshell gets its own copy and what it pushes dies with it.
    # A single flat list let `( pushd rule-hooks )` push onto the parent's
    # stack, so the `popd` after the subshell closed returned to the wrong
    # place and a real read of a project file resolved somewhere it would never
    # have looked - a false negative, with the inverse false positive available
    # by the same route. `cwd_stack` beside it was already depth-scoped for
    # exactly this reason; this list was declared 25 lines away and was not.
    dir_stacks = [[]]
    pending_warn, first_argv, first_stdout = None, None, []

    # A `cd` that fails stops an `&&` chain, and the commands after it never
    # run, so there is nothing there to block. Only a cd whose failure can
    # actually be established sets this: a command whose exit status cannot be
    # known is assumed to have succeeded, which keeps an ordinary
    # `build && grep p README.md` blocking.
    chain_dead = False
    # The mirror, and it was missing: a `cd` that SUCCEEDS stops an `||` chain,
    # so `cd workflows || grep p ../README.md` never runs the grep. Only the
    # `&&` half was modelled, which made that command a false block. The two
    # are kept as separate flags rather than one tri-state because a segment
    # whose separator is neither clears both.
    chain_satisfied = False
    # Whether the pipeline currently being read is negated by a leading `!`.
    # Carried across the pipeline rather than read per segment, because `!`
    # negates a PIPELINE: it sits on the first stage while the status that
    # settles the following `&&` / `||` belongs to the last. Reading it per
    # segment would fix `! cd x || read` and leave
    # `! echo hi | cd x || read` broken, which is the half-applied shape this
    # file's history is mostly made of.
    pipeline_negated = False

    for index, segment in enumerate(parsed):
        argv = effective_argv(segment.argv)
        if segment.separator != "|":
            pipeline_negated = negation_parity(segment.argv)
        if not argv and not segment.stdin:
            continue
        if chain_dead and segment.separator == "&&":
            continue
        if chain_satisfied and segment.separator == "||":
            continue
        chain_dead = chain_satisfied = False
        if first_argv is None and argv:
            first_argv, first_stdout = argv, segment.stdout

        # Bring the stacks to this segment's grouping depth: entering a subshell
        # inherits the directory in force and a COPY of the directory stack,
        # and leaving one discards both.
        while len(cwd_stack) <= segment.depth:
            cwd_stack.append(cwd_stack[-1])
            # A COPY. Appending the same list object gives the subshell the
            # parent's stack rather than its own, which is the defect being
            # fixed wearing a different spelling.
            dir_stacks.append(list(dir_stacks[-1]))
        del cwd_stack[segment.depth + 1:]
        del dir_stacks[segment.depth + 1:]
        cwd = cwd_stack[-1]
        dir_stack = dir_stacks[-1]

        next_separator = (parsed[index + 1].separator
                          if index + 1 < len(parsed) else None)
        in_pipeline = segment.separator == "|" or next_separator == "|"
        # A pipeline's exit status is its LAST stage's, so a directory verb
        # standing at the end of one does settle the following `&&` or `||`
        # even though its directory change dies with the child. Treating every
        # pipeline stage as settling nothing made
        # `echo hi | cd workflows || grep TODO README.md` a false block: the
        # `cd` succeeds, the pipeline succeeds, and bash never runs the grep.
        settles_chain = (not in_pipeline) or (segment.separator == "|"
                                              and next_separator != "|")

        if argv and posix_basename(argv[0]) in ("cd", "pushd", "popd"):
            # `in_pipeline` is computed above because a pipeline stage runs in
            # a child process whichever of the three directory verbs it runs.
            outcome = _apply_directory_verb(
                argv, cwd, cwd_stack, dir_stack, in_pipeline)
            if settles_chain and outcome is not None:
                # The one place a directory verb's exit status is turned into
                # chain state. A success exits 0, so an `||` branch after it
                # does not run; a failure ends an `&&` chain. An UNKNOWN status
                # sets nothing in either direction: it is not a success any
                # more than it is a failure, and guessing either way blocks or
                # allows a branch on no evidence. A stage that is not the last
                # of a pipeline settles nothing, because the pipeline's status
                # is its last stage's.
                #
                # **The negation is applied here and nowhere else.** A leading
                # `!` inverts what the pipeline REPORTS, not what it did, so the
                # directory move above still follows the real result of the `cd`
                # while the chain sees the opposite. Missing this made
                # `! cd workflows || cat ../README.md` an allow: the `cd`
                # succeeds, so the rule concluded the `||` branch never runs,
                # while bash inverts the status and runs the read.
                status = (not outcome) if pipeline_negated else outcome
                chain_dead, chain_satisfied = (not status), status
            continue

        if not argv:
            continue
        prog = posix_basename(argv[0])
        if prog not in _DEDICATED:
            continue
        recursive = _is_recursive(argv)
        # An input redirection names a file the command reads just as an
        # operand does, and dropping it made `cat < README.md` name nothing.
        candidates = list(file_operands(prog, argv)) + list(segment.stdin)
        files = [f for f in candidates if _reads_a_file(prog, f, cwd, recursive)]
        if not files:
            # Either the command reads stdin, or nothing it names is a file we
            # can find. Requiring the path to EXIST is what keeps the rule
            # honest: a rule that treats any leftover word as a filename blocks
            # commands that name no file at all. Under-reporting is the right
            # failure direction here.
            continue
        # The marker is read off this segment's own words, and must be a whole
        # word: a byte pattern is the entire argument, where matching it inside
        # any word let a Windows path carrying `\r` buy the allowance.
        if _byte_level_pattern(prog, argv):
            # Remembered rather than returned: a later segment may be an
            # ordinary read that must block, and returning here is what
            # allowed the first segment's allowance to cover it.
            if pending_warn is None:
                pending_warn = Decision.warn(A3_ID, (
                    f"A3 allowance: `{prog}` reading {files[0]} with a byte "
                    f"or control-character pattern, which the dedicated "
                    f"tools cannot do. Allowed and logged so the allowance "
                    f"stays measurable."
                ))
            continue
        tool = _TOOL_FOR.get(prog, "Read/Grep/Glob")
        return Decision.block(A3_ID, format_block(
            f"`{prog}` reading {files[0]} from the shell",
            f"CLAUDE.md prescribes the dedicated file tools for this and "
            f"names the Bash equivalents as the thing not to use: where a "
            f"rule specifies a method, that method is the only acceptable "
            f"choice. Going around the tools also goes around the "
            f"PreToolUse checks that guard them, which is how a personal "
            f"name reached a tracked file on 2026-09-02.",
            f"use the {tool} tool on {files[0]}. If this is a byte-level "
            f"check (carriage returns, tabs, NUL bytes) write the pattern "
            f"as an escape such as $'\\r' and it is allowed."))

    if pending_warn is not None:
        return pending_warn

    if (first_argv and posix_basename(first_argv[0]) == "echo"
            and first_stdout and not first_stdout[0].startswith("/dev/")):
        # Warn, not block. The blocking half of A3 is the READ half, where the
        # dedicated tool is unambiguous. Shell writes are a different problem
        # with its own coverage: A4 protects LOG.md, A7 protects .env, and A9
        # covers the interpreter route that actually caused harm. Blocking
        # every redirect here would reach far past that - a redirect
        # legitimately creates scratch files, and the rule cannot tell those
        # from a document edit. The target is read from the echo's own segment,
        # because taking it from the whole command attributed a later command's
        # redirect to an earlier `echo`.
        return Decision.warn(A3_ID, (
            f"A3: `echo` redirected into {first_stdout[0]} - prefer the Write "
            f"or Edit tool for a project file. Logged, not blocked."
        ))
    return None
