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
    Decision, effective_argv, format_block, heredoc_bodies, nested_commands,
    option_letters, posix_basename, split_flags, split_segments,
    split_segments_detailed,
)

RULE_ID = "A6"

# git's own options come before the subcommand, and two of them take a value.
# Taking the first non-dash word as the subcommand picked up that value
# instead, so `git -C . push --force` looked like a subcommand called `.`.
_GIT_VALUE_OPTIONS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                      "--exec-path", "--config-env"}


def _git_subcommand(args):
    """The git subcommand, skipping git's own options and their values."""
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg in _GIT_VALUE_OPTIONS:
            skip = True
            continue
        if arg.startswith("-"):
            continue
        return arg
    return ""

# Destructive SQL, matched only when a known SQL client is the program.
_DESTRUCTIVE_SQL = re.compile(
    r"\b(DROP\s+(TABLE|DATABASE|SCHEMA)|TRUNCATE\s+TABLE)\b", re.IGNORECASE,
)
_SQL_CLIENTS = {"sqlite3", "psql", "mysql", "mariadb", "mysqlsh"}

# A matcher's pattern describes what it EMITS, which is why the sweep reads an
# upstream stage's arguments at all: `git log | grep 'DROP TABLE' | sqlite3 db`
# feeds the client exactly the lines that contain a DROP, and the only place
# that statement appears in the command text is grep's own pattern.
#
# That stops being true when the matcher is asked for something other than the
# matched text. `-c` emits a count, `-l`/`-L` emit filenames and `-q` emits
# nothing at all, so `grep -c "DROP TABLE" dump.sql | sqlite3 db` hands the
# client a number while being read as though it had handed it the statement.
# `-v` emits the lines that do NOT match, which is the opposite of the
# pattern: `grep -v 'DROP TABLE' dump.sql | sqlite3 db` exists to remove the
# statement before the client sees it, and was blocked for naming it.
# The question is therefore not "is this a filter" but "does this stage emit
# what it matched".
# **The short flags differ per program and cannot be shared.** One table of
# grep's letters was applied to every matcher, and in ripgrep `-L` is
# `--follow`, which prints matching LINES; `--files-without-match` has no short
# form there. So `rg -L 'DROP TABLE x' dir | sqlite3 db` was skipped while
# emitting exactly the text the sweep exists to read, and the long form of the
# same command blocked - two spellings of one command disagreeing, which is the
# tell. Verified on this machine: `rg -L pattern .` printed the matching line
# where `grep -L pattern file` printed nothing.
#
# A program absent from this table is never skipped, which is the conservative
# direction for a destructive-command rule: its pattern is read, so the worst
# case is a false block on an audit rather than a DROP reaching a client. `ag`
# and `ack` are absent deliberately - neither is installed here, so their flag
# semantics could not be measured, and guessing them is how this defect arose.
_MATCHER_SUPPRESSED_OUTPUT = {
    "grep": set("clLqv"),
    "egrep": set("clLqv"),
    "fgrep": set("clLqv"),
    "rg": set("clqv"),
}
# Every short flag each program takes WITHOUT a value. A bundle buys the skip
# only when all of its letters are in this set, because a letter outside it may
# be a flag carrying an attached value, and then the rest of the word is that
# value rather than more flags: `rg -tsql` is `-t sql`, whose letters include
# both `q` and `l`, so reading the word as a bundle bought the skip for a
# command that prints the matched DROP. This list only ever ADDS skips - a
# letter it does not know disables the skip - which is the conservative
# direction for a destructive-command rule.
_VALUELESS_SHORT_FLAGS = {
    "grep": set("EFGPiyvwxclLoqsbHhnZzrRaIUT"),
    "egrep": set("EFGPiyvwxclLoqsbHhnZzrRaIUT"),
    "fgrep": set("EFGPiyvwxclLoqsbHhnZzrRaIUT"),
    "rg": set("cFiILlnNopqsSuUvwxzabhP"),
}
_NOT_MATCHED_TEXT_LONG = {"count", "count-matches", "files-with-matches",
                          "files-without-match", "quiet", "silent",
                          "invert-match"}
# The short options each matcher takes WITH a value. Read by `option_letters`,
# which ends a bundle at one of these (`-tsql` sets `t`) and makes one standing
# last take the next word (`-e -c` sets `e`, and `-c` is its value). Reading
# `grep -e -c -e 'DROP TABLE t'` letter by letter found a `-c` that is really
# the first pattern, and skipped a stage that prints the DROP.
_MATCHER_VALUE_LETTERS = {
    "grep": set("efABCmdD"),
    "egrep": set("efABCmdD"),
    "fgrep": set("efABCmdD"),
    "rg": set("ABCefgjmMrtTEd"),
}
# Long options known to take NO value. Any other long option written without
# `=` is assumed to take the next word, which can only hide a suppressor and so
# only ever removes a skip: `--regexp -c` sets no count.
_MATCHER_VALUELESS_LONG = _NOT_MATCHED_TEXT_LONG | {
    "ignore-case", "invert-match", "word-regexp", "line-regexp",
    "fixed-strings", "extended-regexp", "basic-regexp", "perl-regexp",
    "recursive", "dereference-recursive", "no-filename", "with-filename",
    "line-number", "only-matching", "null", "text", "follow", "hidden",
    "no-ignore", "smart-case", "case-sensitive", "multiline", "no-messages",
}


# Two filters whose whole job can be to REMOVE lines: a sed script made only of
# `/re/d` deletions, and an awk program that is only `!/re/` or `$0 !~ /re/`.
# Each emits its input minus the lines naming the pattern, so `sed '/DROP
# TABLE/d' dump.sql | sqlite3 db` takes the statement out rather than
# supplying it, and was blocked for naming it. Measured with GNU sed and awk in
# Git Bash. Only those exact shapes qualify: a script that does anything else -
# prints, substitutes, `!d`, or is read from a file with `-f` - is read as
# before, because it could as easily ADD the statement as remove it.
_SED_PROGRAMS = {"sed", "gsed"}
_AWK_PROGRAMS = {"awk", "gawk", "mawk", "nawk"}
_SED_DELETE_ONLY = re.compile(
    r"""^\s*(?:/(?:[^/\\\n]|\\.){1,300}/I?\s*d\s*(?:[;\n]\s*|$)){1,20}$""")
_AWK_INVERT_ONLY = re.compile(
    r"""^\s*(?:!\s*|\$0\s*!~\s*)/(?:[^/\\\n]|\\.){1,300}/\s*$""")
_SED_VALUELESS_LONG = {"quiet", "silent", "regexp-extended", "separate",
                       "unbuffered", "null-data", "posix", "debug", "sandbox",
                       "follow-symlinks", "in-place"}


def _sed_only_deletes(args):
    """True when every script this sed runs is made only of `/re/d`.

    The script is each `-e`/`--expression` value or, with none, the first
    operand. `-f` names a script file, which this rule cannot read, and any
    option not known here might carry a script, so both answer False.
    """
    scripts, operands, i, ended = [], [], 0, False
    while i < len(args):
        arg = args[i]
        following = args[i + 1] if i + 1 < len(args) else None
        if ended or arg == "-" or not arg.startswith("-"):
            operands.append(arg)
        elif arg == "--":
            ended = True
        elif arg.startswith("--"):
            name, equals, value = arg[2:].partition("=")
            if name in ("expression", "line-length"):
                if name == "expression":
                    scripts.append(value if equals else (following or ""))
                if not equals:
                    i += 1
            elif name not in _SED_VALUELESS_LONG:
                return False
        else:
            letters = arg[1:]
            for position, letter in enumerate(letters):
                rest = letters[position + 1:]
                if letter in "el":
                    if letter == "e":
                        scripts.append(rest if rest else (following or ""))
                    if not rest:
                        i += 1
                    break
                if letter == "i":  # an attached backup suffix, if any
                    break
                if letter not in "nrEsuz":
                    return False
        i += 1
    if not scripts:
        if not operands:
            return False
        scripts = [operands[0]]
    return all(_SED_DELETE_ONLY.match(script) for script in scripts)


def _awk_only_inverts(args):
    """True when the awk program only prints lines NOT matching a regex.

    Only `-F` and `-v` are read as options, each with its value; any other
    option, `-f` and gawk's `-e` included, may supply program text this rule
    cannot see, so it answers False.
    """
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            i += 1
            break
        if arg == "-" or not arg.startswith("-"):
            break
        if arg[1:2] in ("F", "v"):
            i += 1 if len(arg) > 2 else 2
            continue
        return False
    return i < len(args) and bool(_AWK_INVERT_ONLY.match(args[i]))


def _emits_what_it_matched(argv):
    """False when this stage is a matcher that emits something else.

    Asked of a matcher, and of sed and awk only in their delete-only shapes:
    every other program's arguments are read as before, because an allow-list
    of printing commands would have lost the idiomatic drop-every-table
    one-liner, where the DROP is written inside an `awk` program that really
    does print it.

    Everything after `--` is an operand rather than a flag, which is the same
    fact A3's `_flag_args` already knows: `grep -e 'DROP TABLE x' -- -c
    dump.sql` names a FILE called `-c` and still emits the matched line, so
    reading that `-c` as the count flag bought the skip on a command that
    really does feed the client.
    """
    if not argv:
        return True
    if posix_basename(argv[0]) in _SED_PROGRAMS:
        return not _sed_only_deletes(argv[1:])
    if posix_basename(argv[0]) in _AWK_PROGRAMS:
        return not _awk_only_inverts(argv[1:])
    suppressed = _MATCHER_SUPPRESSED_OUTPUT.get(posix_basename(argv[0]))
    if suppressed is None:
        return True
    prog = posix_basename(argv[0])
    args = argv[1:]
    before_operands = args[:args.index("--")] if "--" in args else args
    if any(arg == "--label" or arg.startswith("--label=")
           for arg in before_operands):
        # `--label` names the "file" grep reports for standard input, so with
        # `-l` or `-c` the output IS argument text: `grep -l --label='DROP
        # TABLE t;'` prints the statement. Such a stage is always read.
        return True
    short, long = option_letters(args, _MATCHER_VALUE_LETTERS[prog],
                                 _MATCHER_VALUELESS_LONG)
    if not short <= (_VALUELESS_SHORT_FLAGS[prog] | _MATCHER_VALUE_LETTERS[prog]):
        # A letter this table does not know might carry a value, and then
        # what looks like a suppressor after it is really that value. Unknown
        # disables the skip, which is the conservative direction here.
        return True
    return not (short & suppressed or long & _NOT_MATCHED_TEXT_LONG)


def _segment_danger(argv):
    """Return a short description if this argv is destructive, else None."""
    # A wrapper or a shell keyword in front hides the program: `sudo rm -rf`,
    # `if true; then rm -rf build; fi` and `echo b | xargs rm -rf` all ran the
    # destructive command while argv[0] said otherwise. A3 and A9 already
    # stripped these; this rule did not, which is the more serious omission of
    # the three.
    argv = effective_argv(argv)
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
        sub = _git_subcommand(args)
        if sub == "push":
            # The question is whether a plain force is present, not whether a
            # lease is absent. Asking the second let `git push
            # --force-with-lease --force` through: both spellings were on the
            # command, the lease suppressed the check, and the push still
            # forced. `--force-with-lease` on its own does not put `force` in
            # `long` (it is its own flag name), so the lease case needs no
            # exemption clause - it simply never matches.
            if "f" in short or "force" in long:
                return "git push --force (overwrites remote history)"
            # A leading `+` on a refspec forces that ref just as `--force`
            # forces the push, and the flag test alone never saw it.
            after_sub = args[args.index(sub) + 1:] if sub in args else []
            if any(a.startswith("+") and len(a) > 1 for a in after_sub):
                return "git push with a + refspec (forces the ref)"
        elif sub == "reset" and "hard" in long:
            return "git reset --hard (discards uncommitted work)"
        elif sub == "clean":
            if ("f" in short or "force" in long) and ("d" in short or "x" in short):
                return "git clean -fd/-fdx (deletes untracked files)"

    if prog in _SQL_CLIENTS and _DESTRUCTIVE_SQL.search(" ".join(args)):
        return "destructive SQL (DROP/TRUNCATE)"

    if prog == "find":
        # A3 waives `find` with an action, because `-delete` is not a read and
        # Glob cannot do it. That is A3's call to make and it is right, but it
        # left the action itself unexamined by anyone: `find . -type d -exec rm
        # -rf {} +` ran a recursive forced delete that no rule looked at.
        for exec_argv in _find_execs(args):
            danger = _segment_danger(exec_argv)
            if danger:
                return f"{danger}, run by find -exec"
            # `-exec sh -c '...'` carries its command as a string, exactly as
            # `sh -c` does anywhere else. A3 waives find-with-an-action on the
            # grounds that this rule examines it, so a gap here is a gap in
            # both rules at once.
            for carried in nested_commands(exec_argv):
                for argv in (split_segments(carried) or []):
                    danger = _segment_danger(argv)
                    if danger:
                        return f"{danger}, run by find -exec"
        # **`-delete` blocks on the verb, like every other destructive command
        # in this rule.** It did not until 2026-09-20: it was the one verb here
        # that got an adjudication engine, asking whether a narrowing predicate
        # ran before the action and allowing the command when one did. Rounds 14
        # to 18 each found a defect in that engine, and round 18 showed the
        # question itself cannot be answered from the argv. `-fstype ntfs` is a
        # genuine test that narrows nothing on a single-filesystem tree, and so
        # are `-name '*'`, `-size +0c`, `-type f` on a tree of files, and any
        # all-matching regex: `narrows` is a syntactic property of the predicate
        # while safety is a property of the operand supplied. No table row and no
        # evaluator closes that.
        #
        # `rm -rf build/` is blocked flatly on its flags with no path analysis,
        # as are `git reset --hard`, `git clean -fd`, `git push --force` and
        # destructive SQL. `find -delete` is now read the same way, which is what
        # the engine's 294 lines and 33 tests were buying an exception from.
        # Measured before removing it: across 82 days of fire-log and the whole
        # session-search index, the check had never fired on real work, and every
        # `find ... -delete` in this project's history postdates the check itself.
        #
        # The membership test is deliberate and slightly wider than the action.
        # `find . -name '-delete' -print` names a FILE and deletes nothing, and it
        # now blocks. That is the same trade `rm` already makes, `rm -- -rf`
        # being blocked on a filename too, and it costs one retry against a
        # message naming the manual path.
        if "-delete" in args:
            return "find -delete (deletes every file the traversal selects)"

    return None


# `-exec`/`-execdir` run everything up to a `;` or `+`.
_FIND_EXEC_FLAGS = {"-exec", "-execdir", "-ok", "-okdir"}
_FIND_EXEC_END = {";", "+", "\\;"}


def _find_execs(args):
    """The argv of each `find -exec ...` clause."""
    found, current, collecting = [], [], False
    for arg in args:
        if arg in _FIND_EXEC_FLAGS:
            if current:
                found.append(current)
            current, collecting = [], True
            continue
        if collecting:
            if arg in _FIND_EXEC_END:
                found.append(current)
                current, collecting = [], False
                continue
            if arg != "{}":
                current.append(arg)
    if current:
        found.append(current)
    return found


def _is_sql_client(argv):
    """True when this argv runs a SQL client, read through any wrapper."""
    argv = effective_argv(argv)
    return bool(argv) and posix_basename(argv[0]) in _SQL_CLIENTS


def _sql_input_danger(ctx):
    """Destructive SQL handed to a client as INPUT rather than as an argument.

    A6 searched `" ".join(args)`, which sees `sqlite3 db 'DROP TABLE users;'`
    and nothing else. The two ordinary ways of feeding a client a script both
    went past it:

        sqlite3 db <<SQL          sqlite3 db <<< 'DROP TABLE users;'
        DROP TABLE users;
        SQL

    The body is not shell code, and the reader is right to strip it before
    parsing - but for a SQL client it is executable input, and which command a
    body belongs to is decided by the text before its `<<`, exactly as A9
    decides which command opened the heredoc it reads.

    What is still out of scope, deliberately: `sqlite3 db < drop.sql`. The
    destructive statement is in a file, so nothing in the command text says
    what it contains, and this rule reads commands rather than files.
    """
    parsed = split_segments_detailed(ctx.command)
    for segment in parsed or []:
        if segment.here_text and _is_sql_client(segment.argv) and any(
                _DESTRUCTIVE_SQL.search(text) for text in segment.here_text):
            return "destructive SQL (DROP/TRUNCATE) fed to a SQL client"
    bodies_by_opener = {}
    for prefix, body in heredoc_bodies(ctx.command):
        opener = split_segments(prefix) or []
        if opener:
            bodies_by_opener.setdefault(tuple(opener[-1]), []).append(body)
    for pipeline in _pipelines(parsed or []):
        # EVERY client in the pipeline, not the first. Taking the first left
        # `upstream` empty whenever a pipeline began with a client, so the
        # idiomatic drop-every-table one-liner - `sqlite3 db .tables | awk
        # '{print "DROP TABLE " $1 ";"}' | sqlite3 db` - was unexamined, and so
        # was a heredoc opened by any client after the first.
        for client in [i for i, s in enumerate(pipeline)
                       if _is_sql_client(s.argv)]:
            # Only what flows INTO this client, so stages at or after it are
            # not read. A client is often a pipeline's source rather than its
            # sink: `sqlite3 db .dump | grep -c 'DROP TABLE'` audits a dump for
            # DROP statements, which is ordinary read-only work, and taking the
            # whole pipeline's text blocked it. The client's own arguments are
            # still covered, by the argument check in `_segment_danger`.
            # A stage's arguments stand in for what it emits, which holds for a
            # command that prints them and for a matcher that selects lines by
            # them, and stops holding when the matcher was asked for a count, a
            # filename or nothing. The wrapper is stripped first, so `sudo echo
            # ...` is still an echo rather than a program called `sudo`.
            parts = []
            for stage in pipeline[:client]:
                words = effective_argv(stage.argv)
                if words and _emits_what_it_matched(words):
                    for word in words[1:]:
                        parts.append(word)
                        if (word.startswith("-") and not word.startswith("--")
                                and len(word) > 2):
                            # An attached short-flag value is the same text as
                            # a separate one: `grep -e'DROP TABLE t'` arrives
                            # as `-eDROP TABLE t`, where `\bDROP` cannot match
                            # because `e` and `D` are both word characters, so
                            # the flag letter is set aside as well.
                            parts.append(word[2:])
            for segment in pipeline[:client]:
                parts.extend(bodies_by_opener.get(tuple(segment.argv), []))
            # A heredoc the CLIENT itself opened is input to it, not output
            # from it, so it belongs here even though the client is not
            # upstream of itself: `sqlite3 db <<SQL` is a one-stage pipeline.
            parts.extend(bodies_by_opener.get(tuple(pipeline[client].argv), []))
            if _DESTRUCTIVE_SQL.search(" ".join(parts)):
                return "destructive SQL (DROP/TRUNCATE) fed to a SQL client"
    return None


def _pipelines(segments):
    """Group *segments* into pipelines: runs joined by `|`.

    A pipe is the third ordinary way to hand a client a script, alongside an
    argument and a heredoc, and `echo 'DROP TABLE users;' | sqlite3 db` was
    allowed while both of the others blocked. The statement is sitting in the
    command text, which is the same reason a heredoc body is read.

    Grouped rather than checked pairwise so a filter in the middle
    (`echo ... | tr -d x | sqlite3 db`) does not separate the text from the
    client that receives it.
    """
    grouped, current = [], []
    for segment in segments:
        if segment.separator == "|" and current:
            current.append(segment)
            continue
        if current:
            grouped.append(current)
        current = [segment]
    if current:
        grouped.append(current)
    return grouped


def _danger_in(ctx):
    """The first destructive thing this command does, or None.

    Two questions, not one: what each segment RUNS, and what a SQL client is
    FED. The second was missing entirely, and it is asked once for the whole
    command rather than per segment because a heredoc body is matched back to
    its opener by its own prefix.
    """
    segments = split_segments(ctx.command)
    if segments is None:
        return None  # unparseable -> cannot determine -> allow (fail mode)
    for argv in segments:
        danger = _segment_danger(argv)
        if danger:
            return danger
    return _sql_input_danger(ctx)


def check(ctx):
    if not ctx.command:
        return None
    danger = _danger_in(ctx)
    if danger is None:
        return None
    return Decision.block(RULE_ID, format_block(
        f"This command is blocked: {danger}.",
        "Destructive commands that delete files, rewrite history, or "
        "drop data are irreversible. The block makes sure such an "
        "action is a deliberate human decision, not an AI default.",
        f"If you genuinely intend this, run it yourself in the "
        f"terminal: `{ctx.command.strip()}`",
    ))
