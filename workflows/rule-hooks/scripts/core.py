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
from functools import lru_cache


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
# Shell-command reading (shared by A3, A4, A6, A7, A9)
# ---------------------------------------------------------------------------
# Every rule here must PARSE the command rather than substring-match it, so
# that `echo "never run rm -rf"` is not flagged while `rm -rf build` is.
#
# **Why this is hand-written rather than `shlex`.** It was `shlex` until
# 2026-09-04. `shlex` splits text into words and respects quotes, which is all
# it was built for; it is not a shell and does not know shell's rules. Four
# review rounds each found a fresh crop of the same defect: a rule reaching the
# wrong verdict because the reader did not model something the shell does. The
# ones that were live at the time it was replaced:
#
#   - A newline was never a separator (`whitespace_split` consumes it), so
#     `echo cleaning` followed by `rm -rf build` on the next line arrived as a
#     single command called `echo` and A6 allowed it. A3 alone had a private
#     workaround; A4, A6, A7 and A9 did not.
#   - `#` was treated as starting a comment anywhere, where the shell only
#     does so at the start of a word, so `echo done#now && rm -rf build`
#     discarded everything after the `#`.
#   - Quoting was resolved and then thrown away, so a *quoted* operator could
#     not be told from a real one afterwards. That cut both ways:
#     `rm "&" -rf build` slipped past A6, and `echo ">" .env` was wrongly
#     blocked by A7.
#   - `$(...)`, backticks and `<(...)` were not modelled, so
#     `echo "$(rm -rf build)"` hid a real command inside a word.
#   - An input redirection was discarded along with its target, so
#     `cat < README.md` named no file at all.
#   - A line continuation (`\` before a newline) was a parse error, and a
#     parse error means "cannot determine", which means allow.
#
# Each of those is a fact about shell that a lexer has no reason to know.
# Teaching them one at a time is what produced the crop-per-round pattern, so
# the reader below knows them by construction instead: it tracks quoting as it
# scans, keeps whether a token was quoted, treats a newline as the separator it
# is, honours a line continuation, recognises a comment only at word start,
# captures substitutions as commands in their own right, and keeps both sides
# of a redirection rather than dropping them.

_SEGMENT_SEPS = {";", ";;", "&&", "||", "|", "&", "\n"}
# Grouping tokens start a new command rather than being part of one.
_GROUPING = {"(", ")", "{", "}"}
# Redirections. Both directions are kept: an output redirection names a file
# being written (A4 and A7 care) and an input redirection names a file being
# read (A3 cares, and used to miss it entirely).
_WRITE_REDIR_OPS = (">", ">>", ">|", "&>", "&>>")
_READ_REDIR_OPS = ("<",)
# Redirections whose operand is consumed but is not a file either rule wants:
# a heredoc delimiter, a here-string, a duplicated file descriptor.
_OTHER_REDIR_OPS = ("<<", "<<-", "<<<", ">&", "<&")
_REDIR_OPS = (set(_WRITE_REDIR_OPS) | set(_READ_REDIR_OPS)
              | set(_OTHER_REDIR_OPS))

# Longest first, so `&>>` is never read as `&>` followed by `>`.
_OPERATORS = tuple(sorted(
    _SEGMENT_SEPS | _GROUPING | _REDIR_OPS, key=len, reverse=True))

# The same 22 operators, indexed by first character. An operator can only match
# at a position whose character equals the operator's first character, so
# grouping by that character cannot change any verdict - it only stops the
# scanner asking 22 questions at every `a` in a long argument, where all 22
# answers were already no. Measured at 4.2s to 3.0s on a 240,000-character
# command, and cost is a correctness property here: a hook that does not answer
# inside its budget has allowed the command.
_OPERATORS_BY_FIRST = {}
for _operator in _OPERATORS:
    _OPERATORS_BY_FIRST.setdefault(_operator[0], []).append(_operator)
for _candidates in _OPERATORS_BY_FIRST.values():
    _candidates.sort(key=len, reverse=True)  # longest first, within the bucket

_MAX_SUBSTITUTION_DEPTH = 8

# A `#` opens a comment only where a word could start: at the beginning of a
# line, or after whitespace or an operator. `a#b` is an ordinary word.
#
# Written once and shared because the alternative is what CR2 and CR6 were:
# this module contains four independent character-walkers (`_scan`,
# `_matching_paren`, `_quote_end`, `_heredoc_openers`), each modelling shell for
# itself. `_heredoc_openers` knew about comments and arithmetic; `_matching_paren`
# knew about neither, so a `)` inside a comment closed a command substitution
# early for the reader while Bash executed the line after it. A fact about the
# shell belongs to the file, not to the function where its absence was noticed.
_WORD_BREAK_BEFORE_COMMENT = " \t\r\n;|&()<>"


def _opens_comment(text, index, line_start=None):
    """True when the `#` at *index* starts a comment rather than a word.

    The character before it has to be a real word break, which an ESCAPED
    character is not: in `echo \\>#a` the `>` is an ordinary literal, so `#`
    continues the word and bash prints `>#a`. Callers skip a backslash escape
    with `i += 2` and then ask about `text[index - 1]`, which is the escaped
    character, so without this test `\\>#` read as a comment - and a comment
    there swallowed the closing paren of an enclosing substitution, making the
    whole command unparseable and therefore allowed by every rule at once.
    """
    if text[index] != "#":
        return False
    if index == 0 or (line_start is not None and index == line_start):
        return True
    if text[index - 1] not in _WORD_BREAK_BEFORE_COMMENT:
        return False
    # Is that word break itself escaped? Only if an ODD number of backslashes
    # precedes it, because each pair is one escaped backslash and escapes
    # nothing further.
    #
    # The first version of this test asked whether the character two back was a
    # backslash, which is a fixed two-character question about a run of
    # unbounded length. It was right for one backslash and wrong for two: `echo
    # a \\ # <<EOF` is a comment in bash, the hook read it as a word, and the
    # `<<EOF` it then failed to discard fabricated a heredoc that swallowed
    # every command after it - allowing all six shell rules to be bypassed by
    # `rm -rf build` on the next line.
    run, j = 0, index - 2
    while j >= 0 and text[j] == "\\":
        run += 1
        j -= 1
    return run % 2 == 0


def _arithmetic_end(text, start):
    """Index just past the `))` closing the `$((` at *start*, or len(text).

    Arithmetic expansion is not command substitution. `$((1<<2))` is a left
    shift, not a heredoc, and `$((rm -rf build))` is a syntax error in Bash
    rather than a command that runs - so reading either as a command produced a
    false block and, worse, taught the reader that a `<<` inside arithmetic
    opened a body that then swallowed every command after it.

    Shared by the scanner and the heredoc finder, which is the point: the
    heredoc finder already knew this and the scanner did not.

    *start* is the index of the FIRST `(` of the `$((`, and the scan begins
    there so that paren is counted. Beginning at `start + 1` counted only the
    inner pair and returned the index OF the outer `)` rather than past it,
    leaving a stray `)` in the stream. That one character closed the enclosing
    `$(...)` early, so everything after it became inert text inside a quoted
    word and no rule saw it: `echo "$(: $((0)) ; rm -rf build)"` was allowed,
    and so was the same command carrying a `cat`, a `.env` write or an
    interpreter write. Two printable characters switched off all six shell
    rules at once. It was introduced by extracting this helper out of
    `_heredoc_openers`, whose inline version started at the first paren and
    counted it; the extraction changed where the scan began.

    **Quote-aware, like its sibling walkers.** It counted bare parens, so one
    unbalanced `(` inside a quoted string ran the count off the end of the
    input: `echo $(( $(printf "(") ))` returned `len(text)`, the scanner set
    `i = end`, and every command after it on the line was consumed as part of
    the expansion and seen by no rule. That is the same one-fact-two-walkers
    shape this module records three times already - `_matching_paren` had been
    taught about quotes and this had not.
    """
    i, n, depth, quote = start, len(text), 0, None
    while i < n:
        char = text[i]
        if quote:
            if char == "\\" and quote == '"' and i + 1 < n:
                i += 2
                continue
            if char == quote:
                quote = None
            i += 1
            continue
        if char == "\\":
            i += 2
            continue
        if char in "'\"":
            quote = char
            i += 1
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _arithmetic_span(text, dollar):
    """Index just past a real arithmetic expansion at *dollar*, or None.

    A `$((` is not always arithmetic. When the parens close without a second
    `)` alongside the first, bash re-reads the construct as `$( (` - command
    substitution of a subshell - and RUNS it. Confirmed in bash: `$((printf hi)
    )` prints `hi`, and `$((touch f) )` creates the file, where `$((printf
    hi))` is a genuine syntax error that runs nothing. One space separates the
    two, and reading both as arithmetic made `echo $((rm -rf build) )` a
    command that no rule saw.

    *dollar* is the index of the `$`. The caller falls through to its ordinary
    command-substitution handling when this returns None, which is what bash
    does with the same text.

    **The question is structural, not textual, and getting that wrong produced
    three separate bypasses in one round.** The first version asked whether the
    span's last two characters were `))`. Bash asks whether the `)` matching the
    SECOND `(` is immediately followed by the `)` matching the first, and the
    two questions differ on ordinary text:

    - `$((a) ; (b))` ends in `))` by coincidence. Bash runs it as a command
      substitution containing two subshells; the textual test called it
      arithmetic, and since an arithmetic body is only searched for `$(`, both
      commands vanished from every rule at once.
    - `$(( (1) ))` is arithmetic and the positional test says so.
    - `$((a)(b))` ends in `))` too, and bash rejects it as a substitution.

    A precondition test is also part of the fix rather than tidiness: this was
    being called on every `$(` inside double quotes, where `_arithmetic_end`
    would start counting at a single `(` and any nested substitution closing
    adjacent to the outer one made the span look arithmetic. That silently
    dropped the OUTER command of ordinary idioms such as `"$(cat $(ls))"`.
    """
    if not text.startswith("$((", dollar):
        return None
    inner_close = _matching_paren(text, dollar + 2)
    if inner_close == -1 or inner_close + 1 >= len(text):
        return None
    return inner_close + 2 if text[inner_close + 1] == ")" else None


def _arithmetic_substitutions(text, dollar, end):
    """The commands a `$((...))` at *dollar* really runs, or [].

    Arithmetic is not a command, and the fix that established that went one
    step too far. Bash evaluates an arithmetic expression, but it EXPANDS the
    expression first, so a command substitution written inside one is executed
    like any other:

        $ echo $(( $(printf 5 > marker; printf 7) + 1 ))
        8                       # and `marker` is on disk afterwards

    Skipping the whole body therefore hid a real command from every rule that
    reads substitutions - A3, A4, A6, A7 and A9 at once, since they all read
    the same parse. The body is scanned for its substitutions alone and nothing
    else is taken from it: the arithmetic text itself is still not a command,
    so `$((rm -rf build))` stays allowed and `$((1<<2))` stays a left shift
    rather than a heredoc opener.

    *dollar* is the index of the `$`; *end* is the index just past the closing
    `))`, as returned by :func:`_arithmetic_end`.

    **This walks the body itself rather than calling `_scan` on it, and that is
    the whole design.** The first version did call `_scan`, which calls this
    function back, and a review found three faults in that one line. The
    recursion had no depth cap, where every other recursion in this module has
    one, so ~850 nested `$((` raised `RecursionError`; the evaluator catches a
    crashing rule and ALLOWS the command, so five rules were switched off at
    once by a string of punctuation. It was also quadratic, because each
    nesting level re-scanned a body that is nearly the whole remaining command:
    a 162,000-character payload went from 0.03s to 4.69s, and 322,000 to 15s
    against a five-second budget that a hook must answer inside or it has
    allowed the command. And `_scan` is a COMMAND lexer, so it honoured `#` as
    a comment - which arithmetic does not, since bash expands the expression
    before it fails to evaluate it, so `$(( 1 # $(rm -rf build)` on one line
    and `))` on the next really runs the `rm` while the lexer saw a comment.

    Walking the body once fixes all three: no recursion, one pass over each
    character, and no comment rule. Each substitution's own text is handed
    back and parsed by :func:`split_segments_detailed`, which has the depth cap.

    **A nested `$((` is decided with a stack, as its parens close, not by a
    forward scan when it opens.** Bash's test is positional - arithmetic only
    if the `)` closing the SECOND `(` is immediately followed by the `)`
    closing the first; otherwise the construct is `$( (` and runs - and the
    first two answers to it both failed. Asking `_arithmetic_span` at each
    nested `$((` was a forward scan per level, quadratic in depth; a work
    budget bounded that, and the reading past the budget was a false negative
    twice, first dropping a buried command at the substitution depth cap and
    then splitting quoted words so `r''m` read as `r m`. The budget was also
    per walk rather than per command, so many nested siblings together passed
    the five-second limit. The stack removes all of it: an entry is pushed for
    each `$((` (two, one per paren) and each plain `(`, and when the entry for
    a second paren pops, the character after its `)` settles which construct
    it was, so a subshell form is collected whole when its outer entry pops.

    **Quote state follows bash's nesting.** Inside an arithmetic body a `$(`
    or a backtick runs even within quotes - measured, for single quotes as
    well as double - so those are recognised whatever the quote state. A
    plain paren inside quotes is literal and not paired, while a construct
    opened inside quotes starts fresh quoting of its own, which is restored
    when it closes. Each of those four behaviours was measured in bash, and
    each has a case that fails if it is removed.
    """
    body_end = end - 2 if text[end - 2:end] == "))" else end
    subs = []
    # Each entry: [kind, index of its "(", quote state to restore, is_subshell]
    stack = []
    quote = None
    i = dollar + 3
    while i < body_end:
        char = text[i]
        if char == "\\" and i + 1 < body_end:
            i += 2
            continue
        if char == "$" and i + 1 < body_end and text[i + 1] == "(":
            if text.startswith("$((", i):
                stack.append(["outer", i + 1, quote, False])
                stack.append(["inner", i + 2, None, False])
                quote = None
                i += 3
                continue
            close = _matching_paren(text, i + 1)
            if close == -1 or close >= body_end:
                break
            subs.append(text[i + 2:close])
            i = close + 1
            continue
        if char == "`":
            close = text.find("`", i + 1)
            if close == -1 or close >= body_end:
                break
            subs.append(text[i + 1:close])
            i = close + 1
            continue
        if quote:
            if char == quote:
                quote = None
            i += 1
            continue
        if char in "'\"":
            quote = char
        elif char == "(":
            stack.append(["paren", i, None, False])
        elif char == ")" and stack:
            kind, opened, saved_quote, is_subshell = stack.pop()
            if kind == "inner":
                # The positional test, answered in place: a second `)` right
                # here means arithmetic; anything else means `$( (`.
                stack[-1][3] = text[i + 1:i + 2] != ")"
            elif kind == "outer":
                if is_subshell:
                    subs.append(text[opened + 1:i])
                quote = saved_quote
        i += 1
    return subs


class Token:
    """One lexed token.

    ``quoted`` records whether any part of the token came from inside quotes or
    from a backslash escape. It is the field that makes `rm "&" -rf build`
    decidable: the text of that token is `&`, but it is an argument rather than
    an operator, and nothing downstream can tell the difference once the
    quoting has been resolved and discarded.
    """

    __slots__ = ("text", "quoted", "kind")

    def __init__(self, text, quoted, kind):
        self.text = text
        self.quoted = quoted
        self.kind = kind

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"Token({self.text!r}, quoted={self.quoted}, kind={self.kind})"


class Segment:
    """One command in a pipeline or sequence.

    ``separator`` is the operator that joined it to the segment before it (None
    for the first), which is what lets a caller tell `a && b` from `a || b`.
    ``depth`` is the parenthesis nesting it runs at, so a caller can tell that a
    `cd` inside `( ... )` runs in a subshell. ``stdin`` and ``stdout`` are the
    files it reads from and writes to by redirection.

    ``here_text`` is the operand of a here-string (`<<<`), which is not a
    filename but literal input text handed to the command. It was consumed and
    dropped, which is right for a filename-hunting rule and wrong for a rule
    that cares what a program is fed: `sqlite3 db <<< 'DROP TABLE users;'` is
    the same act as the heredoc form, and neither reached A6.
    """

    __slots__ = ("separator", "depth", "argv", "stdin", "stdout", "here_text")

    def __init__(self, separator, depth, argv, stdin=None, stdout=None,
                 here_text=None):
        self.separator = separator
        self.depth = depth
        self.argv = argv
        self.stdin = stdin if stdin is not None else []
        self.stdout = stdout if stdout is not None else []
        self.here_text = here_text if here_text is not None else []

    def __repr__(self):  # pragma: no cover - debugging aid
        return (f"Segment({self.separator!r}, {self.depth}, {self.argv!r}, "
                f"stdin={self.stdin!r}, stdout={self.stdout!r}, "
                f"here_text={self.here_text!r})")


def _quote_end(text, start, escaped=False):
    """Index of the quote closing the one at *start*, or -1.

    ``escaped`` is for ANSI-C quoting (`$'...'`), where a backslash escapes the
    closing quote. Without it `$'don\\'t'` closes at the escaped quote, the
    remaining quote is unbalanced, and the whole command becomes unreadable -
    which means allowed, disabling every parse-based rule at once.
    """
    quote, i, n = text[start], start + 1, len(text)
    while i < n:
        if escaped and text[i] == "\\" and i + 1 < n:
            i += 2
            continue
        if text[i] == quote:
            return i
        i += 1
    return -1


def _matching_paren(text, start):
    """Index of the `)` closing the `(` at *start*, or -1.

    Quote-aware, comment-aware and arithmetic-aware, because the content of a
    `$(...)` is shell code and all three are facts about shell code.

    The comment case is not a nicety. `echo "$(# )` on one line, `rm -rf build`
    on the next and `)"` on the third: the `)` sits inside a comment, so Bash
    keeps reading and runs the `rm`. Reading that `)` as the closing paren ended
    the substitution early, left the `rm` inside an ordinary quoted word, and
    allowed a command every rule was watching for.
    """
    depth, i, n, quote = 0, start, len(text), None
    line_start = 0
    while i < n:
        char = text[i]
        if quote:
            if char == "\\" and quote == '"' and i + 1 < n:
                i += 2
                continue
            if char == quote:
                quote = None
            if char == "\n":
                line_start = i + 1
            i += 1
            continue
        if char == "\\":
            i += 2
            continue
        if char == "\n":
            line_start = i + 1
            i += 1
            continue
        if _opens_comment(text, i, line_start):
            while i < n and text[i] != "\n":
                i += 1
            continue
        if text.startswith("$((", i):
            # Arithmetic, not a nested command substitution: its parentheses
            # are part of the expression and must not move the depth count.
            i = _arithmetic_end(text, i + 1)
            continue
        if char == "$" and i + 1 < n and text[i + 1] == "'":
            # ANSI-C quoting, where a backslash escapes the closing quote.
            # `_scan` and `_heredoc_openers` both had this branch and this
            # function did not, so `$'\''` inside a substitution closed the
            # quote early, the paren was never found, and the whole command
            # became unparseable - which means allowed, for every rule at once.
            # The third instance of one shell fact reaching some walkers here
            # and not others.
            end = _quote_end(text, i + 1, escaped=True)
            if end == -1:
                return -1
            i = end + 1
            continue
        if char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


# The two scanners below are memoised on their input text, which is what makes
# one hook fire pay for one parse instead of five.
#
# Seven shell rules each ask core for their own view of the same command, and
# core held nothing between calls, so `_scan` ran five times and the heredoc
# splitter twelve. Four-fifths of that was repeat work, and it is the reason a
# long command could take longer than the hook's budget - which means the
# command was allowed, since a hook that does not answer has permitted it.
#
# **The cache is on these two and deliberately not on `split_segments_detailed`.**
# That function returns `Segment` objects and its own recursion mutates them
# (`segment.depth += 1`), so handing the same objects to two rules would let the
# first rule's read change what the second rule sees - and `depth` is exactly
# what A3 uses to tell a subshell from the parent shell. Caching the segment
# builder would have re-created CR3 by another route. Caching the scanners
# instead shares only tokens, which nothing mutates, and every caller still
# builds its own segments. `test_shared_parse_does_not_leak_mutation` pins it.
#
# Both functions are pure: same text in, same result out, no I/O and no globals
# touched. The bound keeps a pathological input from holding memory for the
# life of a test run; a hook process is one-shot and parses a handful of
# strings.
@lru_cache(maxsize=16)
def _scan(command):
    """Lex *command* into `(tokens, substitutions)`, or None if unparseable.

    ``substitutions`` holds the inner text of every `$(...)`, backtick and
    process substitution, each of which is a command in its own right and is
    parsed as one by :func:`split_segments_detailed`. Capturing them is what
    stops `echo "$(rm -rf build)"` from arriving as an inert word.

    None means the quoting is unbalanced, which every caller treats as "cannot
    determine" and therefore allows. That is the documented fail direction.
    """
    tokens, subs = [], []
    buf, quoted, started = [], False, False
    i, n = 0, len(command)

    def flush():
        if started:
            tokens.append(Token("".join(buf), quoted, "word"))

    while i < n:
        char = command[i]

        # A comment runs to end of line, but only where `#` begins a word.
        # In the shell `a#b` is an ordinary word, and reading it as a comment
        # discarded the rest of the line along with any command on it.
        if char == "#" and not started:
            while i < n and command[i] != "\n":
                i += 1
            continue

        if char == "\\":
            if i + 1 < n and command[i + 1] == "\n":
                i += 2  # a line continuation joins the two lines
                continue
            if i + 1 < n:
                buf.append(command[i + 1])
                quoted, started = True, True
                i += 2
                continue
            buf.append(char)
            started = True
            i += 1
            continue

        if char == "'":
            end = command.find("'", i + 1)
            if end == -1:
                return None
            # Taken verbatim: a backslash inside single quotes is literal, so
            # `$'\r'` keeps its backslash and stays recognisable as a
            # byte-level pattern further down.
            buf.append(command[i + 1:end])
            quoted, started = True, True
            i = end + 1
            continue

        if char == '"':
            i += 1
            quoted, started = True, True
            while i < n and command[i] != '"':
                if command[i] == "\\" and i + 1 < n:
                    # Inside double quotes a backslash escapes only these; in
                    # front of anything else it is an ordinary character.
                    # Dropping it regardless turned the Windows path
                    # `"workflows\rule-hooks\CONTEXT.md"` into a name that
                    # matched no file, so a real read looked like none.
                    if command[i + 1] in '$`"\\\n':
                        buf.append(command[i + 1])
                    else:
                        buf.append(command[i])
                        buf.append(command[i + 1])
                    i += 2
                elif command[i] == "$" and i + 1 < n and command[i + 1] == "(":
                    # Arithmetic expansion produces a value, it does not run a
                    # command. Recursing into it as though it were `$()` made
                    # `echo "$((rm -rf build))"` a block, which Bash would
                    # reject as a syntax error rather than execute. A
                    # substitution written INSIDE the expression is a different
                    # thing and does run, so it is collected. And a `$((` whose
                    # parens do not close as `))` is not arithmetic at all -
                    # bash re-reads it as `$( (`, which runs - so it falls
                    # through to the substitution branch.
                    end = _arithmetic_span(command, i)
                    if end is not None:
                        subs.extend(_arithmetic_substitutions(command, i, end))
                        buf.append(command[i:end])
                        i = end
                    else:
                        end = _matching_paren(command, i + 1)
                        if end == -1:
                            return None
                        subs.append(command[i + 2:end])
                        buf.append(command[i:end + 1])
                        i = end + 1
                elif command[i] == "`":
                    end = command.find("`", i + 1)
                    if end == -1:
                        return None
                    subs.append(command[i + 1:end])
                    buf.append(command[i:end + 1])
                    i = end + 1
                else:
                    buf.append(command[i])
                    i += 1
            if i >= n:
                return None
            i += 1
            continue

        if char == "$" and i + 1 < n and command[i + 1] == "'":
            # ANSI-C quoting. The content is taken VERBATIM rather than
            # decoded, so `$'\r'` stays the word `$\r` and the byte-level
            # pattern it represents is still recognisable further down.
            end = _quote_end(command, i + 1, escaped=True)
            if end == -1:
                return None
            buf.append("$")
            buf.append(command[i + 2:end])
            quoted, started = True, True
            i = end + 1
            continue

        if char == "$" and i + 1 < n and command[i + 1] == "(":
            # See the note in the double-quoted branch above: arithmetic is not
            # a command, and the `<<` inside one is a left shift rather than a
            # heredoc opener - but a substitution inside the expression is a
            # command bash runs, and is collected as one, and a `$((` that does
            # not close as `))` is bash's `$( (` rather than arithmetic.
            end = _arithmetic_span(command, i) if command.startswith("$((", i) \
                else None
            if end is not None:
                subs.extend(_arithmetic_substitutions(command, i, end))
                buf.append(command[i:end])
                quoted, started = True, True
                i = end
                continue
            end = _matching_paren(command, i + 1)
            if end == -1:
                return None
            subs.append(command[i + 2:end])
            buf.append(command[i:end + 1])
            quoted, started = True, True
            i = end + 1
            continue

        if char == "`":
            end = command.find("`", i + 1)
            if end == -1:
                return None
            subs.append(command[i + 1:end])
            buf.append(command[i:end + 1])
            quoted, started = True, True
            i = end + 1
            continue

        if char in "<>" and i + 1 < n and command[i + 1] == "(":
            end = _matching_paren(command, i + 1)
            if end == -1:
                return None
            subs.append(command[i + 2:end])
            flush()
            buf, quoted, started = [], False, False
            tokens.append(Token(command[i:end + 1], True, "word"))
            i = end + 1
            continue

        if char in " \t\r":
            flush()
            buf, quoted, started = [], False, False
            i += 1
            continue

        # Only the operators that begin with this character can match here, so
        # the other buckets are not consulted. Identical verdicts to trying all
        # 22 in length order (differential-tested over 4.1 million positions
        # and exhaustively over the operator alphabet); the difference is that
        # an ordinary character now costs one dict miss instead of 22 failed
        # prefix comparisons.
        operator = None
        for candidate in _OPERATORS_BY_FIRST.get(char, ()):
            if command.startswith(candidate, i):
                operator = candidate
                break
        if operator in ("{", "}"):
            # A brace is a grouping keyword only when it stands alone as a
            # word. `{}` is an ordinary argument, and `find -exec rm {} \;`
            # would otherwise be cut in half at it.
            after = i + len(operator)
            standalone = not started and (after >= n or command[after] in " \t\r\n")
            if not standalone:
                operator = None
        if operator is not None:
            # A file descriptor written against a redirection belongs to it:
            # `2>&1` contributes neither a `2` nor a `1`. Adjacency is what
            # decides, so `head -n 2 > out` keeps its `2` as an argument.
            fd_prefix = (started and not quoted and "".join(buf).isdigit()
                         and operator[0] in "<>")
            if not fd_prefix:
                flush()
            buf, quoted, started = [], False, False
            tokens.append(Token(operator, False, "op"))
            i += len(operator)
            continue

        buf.append(char)
        started = True
        i += 1

    flush()
    return tokens, subs


def tokenize(command):
    """The token list for *command*, or None if the quoting is unbalanced.

    A copy, because `_scan` is memoised and this is the one caller that hands
    its list straight to someone else: a caller that appended to it would be
    editing the cached parse of that command for everyone after it.
    """
    scanned = _scan(command)
    return None if scanned is None else list(scanned[0])


def _heredoc_openers(command):
    """`{line_index: [(delimiter, prefix)]}` for every heredoc in *command*.

    One pass over the whole command rather than one pass per line, because
    everything that decides whether a `<<` opens a heredoc can span a line:
    a double-quoted string beginning on one line and ending on the next made a
    `<<` inside it look like an opener, and the fabricated body then swallowed
    every command after it. A fabricated heredoc is the worst kind of defect
    here, because it hides commands rather than misreading one: the body runs
    to the end of the input looking for a terminator that never comes.

    Three things that look like an opener and are not:
    - a `<<` inside quotes, on one line or several;
    - a `<<` in a comment (`# we used to write << EOF here`);
    - a `<<` in arithmetic (`$((1<<2))`), where it is a left shift.

    The delimiter is any word or quoted string rather than an identifier, since
    `<<'PY-CODE'`, `<<9EOF` and `<<'EOF.md'` are all valid and none of them is
    one. ``prefix`` is the text before the `<<` on its line, which identifies
    the command the body belongs to.
    """
    found, i, n = {}, 0, len(command)
    line, line_start, quote = 0, 0, None
    while i < n:
        char = command[i]
        if quote:
            if char == "\n":
                line += 1
                line_start = i + 1
            elif char == "\\" and quote == '"' and i + 1 < n:
                i += 2
                continue
            elif char == quote:
                quote = None
            i += 1
            continue
        if char == "\n":
            line += 1
            line_start = i + 1
            i += 1
            continue
        if char == "\\":
            i += 2
            continue
        if _opens_comment(command, i, line_start):
            while i < n and command[i] != "\n":
                i += 1
            continue
        if command.startswith("$((", i):
            # One definition of where arithmetic ends, shared with the scanner
            # and the paren matcher. The line bookkeeping stays here because
            # only this walker needs it: a heredoc opener is recorded by line
            # index, so a newline inside the expansion still has to be counted.
            end = _arithmetic_end(command, i + 1)
            skipped = command.count("\n", i, end)
            if skipped:
                line += skipped
                line_start = command.rindex("\n", i, end) + 1
            i = end
            continue
        if char == "$" and i + 1 < n and command[i + 1] == "'":
            end = _quote_end(command, i + 1, escaped=True)
            if end == -1:
                return found
            i = end + 1
            continue
        if char in "'\"":
            quote = char
            i += 1
            continue
        if command.startswith("<<<", i):  # a here-string is not a heredoc
            i += 3
            continue
        if command.startswith("<<", i):
            prefix = command[line_start:i]
            j = i + 2
            dashed = j < n and command[j] == "-"
            if dashed:
                j += 1
            while j < n and command[j] in " \t":
                j += 1
            # Whether the delimiter was quoted decides whether the shell
            # expands the body. `<<'EOF'` is literal text; `<<EOF` runs any
            # `$(...)` inside it, so a body is not always inert data.
            delim_quote = None
            if j < n and command[j] in "'\"":
                delim_quote = command[j]
                j += 1
            start = j
            if delim_quote:
                while j < n and command[j] not in (delim_quote, "\n"):
                    j += 1
                delimiter = command[start:j]
                if j < n and command[j] == delim_quote:
                    j += 1
            else:
                while j < n and command[j] not in " \t\n<>|&;()":
                    j += 1
                delimiter = command[start:j]
            if delimiter:
                found.setdefault(line, []).append(
                    (delimiter, prefix, dashed, delim_quote is not None))
            i = j
            continue
        i += 1
    return found


def _is_terminator(line, delimiter, dashed):
    """True when *line* closes a heredoc opened with *delimiter*."""
    return (line.lstrip("\t") if dashed else line) == delimiter


@lru_cache(maxsize=16)
def _split_heredocs(command):
    """`(command_without_bodies, [(prefix, delimiter, body)])`.

    A heredoc body is data, not commands. Removing it before the command is
    read stops a body from being lexed as argv, and stops an odd number of
    quotes inside one (perfectly legal, since the shell does not quote-parse a
    body) from making the whole command unreadable and therefore allowed.
    """
    lines = command.splitlines()
    by_line = _heredoc_openers(command)
    kept, found, i, total = [], [], 0, len(lines)
    while i < total:
        line = lines[i]
        kept.append(line)
        openers = by_line.get(i, [])
        i += 1
        for delimiter, prefix, dashed, quoted_delim in openers:
            body = []
            # The terminator is the delimiter ALONE on its line. `<<-` strips
            # leading tabs and nothing else: not spaces, and never trailing
            # whitespace. Matching on `.strip()` ended a body early at any
            # indented copy of the delimiter, and the remaining body lines were
            # then read as commands - which is what happens when this project
            # documents its own heredoc convention inside a heredoc.
            while i < total and not _is_terminator(lines[i], delimiter, dashed):
                body.append(lines[i])
                i += 1
            if i < total:
                kept.append(lines[i])  # the terminator closes the body
                i += 1
            found.append((prefix, delimiter, "\n".join(body), quoted_delim))
    return "\n".join(kept), found


def strip_heredoc_bodies(command):
    """*command* with every heredoc body removed, openers and terminators kept.

    Content quoted into a command is data, not syntax: a heredoc body is a file
    being written, a commit message, a sample.

    Not every caller wants this. A9 reads the bodies deliberately, because
    inline interpreter code is exactly what it inspects; it gets them from
    :func:`heredoc_bodies` instead.
    """
    return _split_heredocs(command)[0]


def heredoc_bodies(command):
    """`(prefix, body)` for every heredoc in *command*.

    ``prefix`` is the text on the opening line up to the `<<`, which is what
    identifies the command the body belongs to: its last segment opened it.
    The whole opening line is not enough, because two commands can share one
    and `python -c "..." && cat <<EOF` would then look like a python heredoc.
    """
    return [(prefix, body) for prefix, _, body, _q in _split_heredocs(command)[1]]


def expanded_heredoc_bodies(command):
    """The bodies whose delimiter was UNQUOTED, so the shell expands them.

    `<<'EOF'` is literal text and its body is inert. `<<EOF` is not: the shell
    runs any `$(...)` or backtick inside it before the body is written
    anywhere, so a substitution in one is a command that really executes. The
    bodies are stripped before parsing, which is right for the literal case and
    silently discarded the command in this one.
    """
    return [body for _p, _d, body, quoted in _split_heredocs(command)[1]
            if not quoted]


def _collect(tokens, depth_start=0):
    """Build the segment list for one already-lexed token stream."""
    segments = []
    argv, stdin, stdout, here_text = [], [], [], []
    separator, segment_depth, depth = None, depth_start, depth_start
    i, total = 0, len(tokens)

    def flush(new_separator):
        nonlocal argv, stdin, stdout, here_text, separator
        if argv or stdin or stdout or here_text:
            segments.append(Segment(separator, segment_depth, argv,
                                    stdin, stdout, here_text))
            argv, stdin, stdout, here_text = [], [], [], []
        separator = new_separator

    while i < total:
        token = tokens[i]
        if token.kind != "op":
            if not argv:
                segment_depth = depth
            argv.append(token.text)
            i += 1
            continue

        text = token.text
        if text == "\n" and not argv and not stdin and not stdout and (
                separator is None or separator in _SEGMENT_SEPS
                or separator in _GROUPING):
            # A newline after a control operator continues the command rather
            # than ending it, so `cd nowhere &&` on one line and the command on
            # the next are still one `&&` chain.
            i += 1
            continue

        if text in _SEGMENT_SEPS or text in _GROUPING:
            flush(text)
            if text == "(":
                depth += 1
            elif text == ")":
                depth = max(depth_start, depth - 1)
            segment_depth = depth
            i += 1
            continue

        if text in _REDIR_OPS:
            target = None
            if i + 1 < total and tokens[i + 1].kind != "op":
                target = tokens[i + 1].text
                i += 1
            if target is not None:
                if text in _WRITE_REDIR_OPS:
                    stdout.append(target)
                elif text in _READ_REDIR_OPS:
                    stdin.append(target)
                elif text == "<<<":
                    # Literal text handed to the command, not a filename.
                    here_text.append(target)
            i += 1
            continue

        i += 1

    flush(None)
    return segments


def split_segments_detailed(command, _depth=0):
    """The :class:`Segment` list for *command*, or None if unparseable.

    Heredoc bodies are removed first (they are data), and every command
    substitution is parsed as a command in its own right and appended one
    grouping level deeper, because that is what it is: a command, running in a
    subshell, whose effects are real whether or not it sits inside quotes.
    """
    if _depth > _MAX_SUBSTITUTION_DEPTH:
        return []
    stripped, _ = _split_heredocs(command)
    scanned = _scan(stripped)
    if scanned is None:
        return None
    tokens, subs = scanned
    segments = _collect(tokens)
    # A substitution and a shell's `-c` string are the same thing to a rule: a
    # command whose text is sitting right there, wrapped in something that
    # makes it look like an argument. Both are parsed and appended one level
    # deeper, which is where they run.
    carried = list(subs)
    for body in expanded_heredoc_bodies(command):
        # The body itself is data, but an unquoted delimiter means the shell
        # expands it first, so any substitution inside it is a real command.
        inner = _scan(body)
        if inner is not None:
            carried.extend(inner[1])
    for segment in list(segments):
        carried.extend(nested_commands(segment.argv))
    for text in carried:
        inner = split_segments_detailed(text, _depth + 1)
        if not inner:
            continue
        for segment in inner:
            segment.depth += 1
            segments.append(segment)
    return segments


def split_segments(command):
    """Split a command into pipeline/sequence segments of argv tokens.

    The argv-only view of :func:`split_segments_detailed`, kept because most
    rules only need to know what programs ran and with which arguments.
    Returns a list of argv lists, or None if the command cannot be parsed
    (caller treats an unparseable command as "cannot determine" -> allow, per
    the fail mode).
    """
    detailed = split_segments_detailed(command)
    if detailed is None:
        return None
    return [segment.argv for segment in detailed if segment.argv]


def redirect_targets(command):
    """The targets of output redirections (`>`, `>>`, `>|`, `&>`, `&>>`), or []."""
    detailed = split_segments_detailed(command)
    if detailed is None:
        return []
    targets = []
    for segment in detailed:
        targets.extend(segment.stdout)
    return targets


def posix_basename(arg):
    """Program basename, handling both slash styles (e.g. /usr/bin/rm -> rm)."""
    return arg.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


# Words that can sit in front of a command without being the command. Shared
# rather than owned by one rule: A3 needs it so `sudo cat f` is a `cat`, and A9
# needs it so `env python3 -c ...` is a `python3`. It lived in A3 first and was
# moved here the moment the second rule wanted it, rather than being copied.
_RESERVED_WORDS = {"if", "then", "elif", "else", "fi", "while", "until", "for",
                   "do", "done", "case", "esac", "select", "function", "time",
                   "!"}
_WRAPPER_COMMANDS = {"sudo", "doas", "command", "builtin", "exec", "nohup",
                     "nice", "stdbuf", "setsid", "xargs"}
# Programs that take a whole command as a STRING argument. They are not
# wrappers: stripping the program name leaves one quoted word, and the command
# inside it is never read. `sh -c "rm -rf build"` was allowed by every rule at
# once for exactly that reason, which is the same defect as an unparsed
# substitution one step further out.
_SHELL_COMMANDS = {"sh", "bash", "zsh", "dash", "ksh", "ash", "busybox"}


def nested_commands(argv):
    """Commands carried inside *argv* as a string argument, or [].

    Covers `sh -c "..."` and its relatives, including the attached `-c"..."`
    spelling, and `eval`, whose operands are joined and run as one command.
    """
    effective = effective_argv(argv)
    if not effective:
        return []
    prog = posix_basename(effective[0])
    if prog == "eval":
        return [" ".join(effective[1:])] if len(effective) > 1 else []
    if prog not in _SHELL_COMMANDS:
        return []
    found, i, total = [], 1, len(effective)
    while i < total:
        arg = effective[i]
        # A short-flag bundle ENDING in `c` takes the next word as the code:
        # `bash -lc '...'` and `sh -ec '...'` are ordinary spellings, and an
        # exact `-c` test missed both. A bundle with `c` anywhere else does
        # not, since only the last flag in a bundle can take a value.
        if (arg.startswith("-") and not arg.startswith("--")
                and len(arg) > 1 and arg.endswith("c")):
            j = i + 1
            if j < total and effective[j] == "--":
                j += 1
            if j < total:
                found.append(effective[j])
                i = j + 1
                continue
        if arg.startswith("-c") and len(arg) > 2 and not arg.startswith("--"):
            found.append(arg[2:])  # the attached `-c'code'` spelling
        i += 1
    return found
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_]\w*=")
_DURATION_RE = re.compile(r"^\d+(?:\.\d+)?[smhd]?$")

# A wrapper's OWN options, and which of them take a separate value. Skipping
# the wrapper name alone is not enough: `sudo -u root rm -rf /srv` then puts
# `-u` in argv[0], and every rule reads the program name as `-u`. Eight of the
# ten wrappers take options, so this was a bypass of all five rules at once,
# invisible to the tests only because they all used the bare forms.
_WRAPPER_VALUE_FLAGS = {
    "sudo": {"-u", "-g", "-U", "-C", "-p", "-r", "-t", "--user", "--group",
             "--prompt", "--close-from", "--role", "--type"},
    "doas": {"-u", "-C"},
    "xargs": {"-I", "-i", "-n", "-L", "-P", "-s", "-d", "-E", "-a",
              "--replace", "--max-args", "--max-lines", "--max-procs",
              "--max-chars", "--delimiter", "--arg-file", "--eof"},
    "nice": {"-n", "--adjustment"},
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
    "exec": {"-a"},
    "env": {"-u", "-C", "-S", "--unset", "--chdir", "--split-string"},
    "command": set(),
    "builtin": set(),
    "nohup": set(),
    "setsid": set(),
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
}

# The short LETTERS of those value-taking options, derived from the table above
# rather than written out a second time. Round 17 of this workflow's review
# series was exactly two hand-written lists that had drifted apart in opposite
# directions at once, so deriving is not tidiness here, it is the fix for a
# defect this file has already shipped.
_WRAPPER_VALUE_LETTERS = {
    prog: {flag[1:] for flag in flags if len(flag) == 2 and flag[0] == "-"}
    for prog, flags in _WRAPPER_VALUE_FLAGS.items()
}

# A wrapper option whose value is not configuration but THE COMMAND. `env -S
# "find . -delete"` sets nothing called `-S`: GNU env splits that string into
# words and executes them. Skipping it as an option value therefore discards
# the only part of the command any rule cares about, and leaves `effective_argv`
# with nothing at all - not a mis-read program, an empty argv, which every rule
# reads as "no command here" and allows.
#
# Each row is (short letters, long names, long options that take no value).
# A row says "this option carries a command", which is the shape A10 refuses.
_WRAPPER_COMMAND_FLAGS = {
    "env": ({"S"}, {"split-string"},
            {"ignore-environment", "null", "debug", "version", "help",
             "unset-all"}),
}


def _skip_wrapper_options(argv, i, prog):
    """Advance *i* past a wrapper's own options (and their values)."""
    value_flags = _WRAPPER_VALUE_FLAGS.get(prog, set())
    total = len(argv)
    while i < total:
        arg = argv[i]
        if arg == "--":
            return i + 1
        if not arg.startswith("-") or arg == "-":
            return i
        if arg in value_flags:
            i += 2  # the flag and the value it consumes
            continue
        i += 1  # a bare flag, or an attached `-o0` / `--user=root`
    return i


def _collect_command_flags(prog, option_words, found):
    """Record any option in *option_words* whose value is a command.

    *option_words* is the wrapper's OWN option region, already bounded by
    `_skip_wrapper_options`, which is what keeps a `-S` belonging to the
    wrapped program out of it: in `env ls -S` the region ends at `ls`, so the
    `-S` that sorts a listing by size never reaches this test.
    """
    if found is None:
        return
    spec = _WRAPPER_COMMAND_FLAGS.get(prog)
    if not spec:
        return
    short_flags, long_flags, valueless_long = spec
    short, long = option_letters(
        option_words, _WRAPPER_VALUE_LETTERS.get(prog, set()), valueless_long)
    for letter in sorted(short & short_flags):
        found.append((prog, "-" + letter))
    for name in sorted(long & long_flags):
        found.append((prog, "--" + name))


def _walk_wrappers(argv, found=None):
    """Index where the real command begins, past the words standing in front.

    When *found* is a list, every wrapper option carrying a command rather than
    configuration is appended to it as `(wrapper, spelling)`. Both questions are
    answered from the same prefix walk rather than from two, because a second
    copy of this walk would be free to drift from the first - which is the
    defect shape this module's own history is largely made of.
    """
    i, total = 0, len(argv)
    while i < total:
        word = argv[i]
        base = posix_basename(word)
        if base in _RESERVED_WORDS or _ASSIGNMENT_RE.match(word):
            i += 1
            continue
        if base == "timeout":
            end = _skip_wrapper_options(argv, i + 1, base)
            _collect_command_flags(base, argv[i + 1:end], found)
            i = end
            if i < total and _DURATION_RE.match(argv[i]):
                i += 1
            continue
        if base == "env":
            end = _skip_wrapper_options(argv, i + 1, base)
            _collect_command_flags(base, argv[i + 1:end], found)
            i = end
            while i < total and _ASSIGNMENT_RE.match(argv[i]):
                i += 1
            continue
        if base in _WRAPPER_COMMANDS:
            end = _skip_wrapper_options(argv, i + 1, base)
            _collect_command_flags(base, argv[i + 1:end], found)
            i = end
            continue
        break
    return i


def effective_argv(argv):
    """*argv* with the words in front of the command removed.

    Shell reserved words, a `!` negation, environment assignments and the
    wrappers that run another program all sit where the program name is
    expected. Left in place, `if grep -q TODO README.md; then` reads as a
    command called `if`, and `env python3 -c "..."` as one called `env`, so
    both a dedicated-tool read and an inline interpreter go unnoticed.

    **What it still does not recover is a command hidden inside a wrapper
    option's value**, and that is stated here rather than left to be found,
    per this module's standing rule that a reader which stops looking somewhere
    must say what is true of the place it stopped. `env -S "find . -delete"`
    returns an EMPTY argv from here, because the option's value is skipped as
    configuration and nothing else is left. Recovering it would mean
    reimplementing GNU env's own string splitting, where any difference is a
    fresh bypass of the same shape, so the shape is refused by rule A10
    instead of being parsed. `hidden_command_wrappers` is what A10 asks.
    """
    return argv[_walk_wrappers(argv):]


def hidden_command_wrappers(argv):
    """`(wrapper, spelling)` for each option in *argv* that hides a command.

    Empty for an ordinary command, and for ordinary wrapper use: `env find .
    -delete`, `env VAR=1 python3 x.py` and `env -u PATH ls` all return []. It
    is only ever the wrapper's own options that are read, so a `-S` belonging
    to the wrapped program is not one of these.
    """
    found = []
    _walk_wrappers(argv, found)
    return found


def negation_parity(argv):
    """True when *argv* is negated an odd number of times by a leading `!`.

    `effective_argv` strips the words standing in front of the command, `!` among
    them. That is right for finding the program and wrong for judging what the
    command RETURNS, and the two questions had been answered by the one helper:
    bash inverts a negated pipeline's exit status, so `! cd workflows` fails when
    the `cd` succeeds. A rule modelling an `&&` / `||` chain from an exit status
    has to read this, or it decides what runs next from the opposite of the truth.

    Parity rather than presence, because `! ! cmd` negates twice and so is not
    negated at all. Only the prefix `effective_argv` removes is examined, so a `!`
    standing anywhere else - in an argument, a pattern, a filename - is not a
    negation. This answers about one segment; a `!` negates a whole PIPELINE,
    whose status is its last stage's, so a caller spanning a pipeline has to carry
    the answer from its first segment to its last rather than asking per stage.
    """
    prefix = argv[: len(argv) - len(effective_argv(argv))]
    return sum(1 for word in prefix if word == "!") % 2 == 1


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
    the command, so a quoted mention is never a target.
    """
    detailed = split_segments_detailed(command)
    if detailed is None:
        return []
    targets = []
    for segment in detailed:
        targets.extend(segment.stdout)
        # A wrapper in front hides the writing program: `echo x | sudo tee
        # .env` reached neither A7 nor A4, because argv[0] was `sudo`.
        argv = effective_argv(segment.argv)
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
            # Every file operand is edited in place, not just the last: `sed -i
            # 's/a/b/' LOG.md notes.md` writes both. The script itself is the
            # first operand unless a flag supplied it.
            scripted = any(a == "-e" or a == "-f" or a.startswith("--expression")
                           or a.startswith("--file") for a in args)
            targets.extend(operands if scripted else operands[1:])
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


def option_letters(args, value_letters, valueless_long=None):
    """`(short_letters, long_names)` that *args* really sets, honouring values.

    `split_flags` reads every character after a dash as a flag, which is
    right only for a program whose options take no values. Where one does,
    that reading turns a value into flags, and it was found twice in one round
    in two different rules: A6 read `rg -tsql` (`-t sql`) as setting `q` and
    `l`, and A3 read `grep -e"Refactor"` as setting `R`, so a search of a pipe
    looked like a recursive walk of the tree. The fact belongs here rather
    than in either rule, which is the lesson this module keeps relearning.

    A letter in *value_letters* ends its bundle, because the rest of the word
    is its value; standing last, it takes the NEXT word as its value, so `-e -c`
    sets `e` and not `c`. Everything after `--` is an operand. A long option
    written without `=` takes the next word when *valueless_long* is given and
    the name is not in it - so an unknown long option is assumed to take a
    value, which only ever hides a flag and never invents one.
    """
    short, long, i, total = set(), set(), 0, len(args)
    while i < total:
        arg = args[i]
        if arg == "--":
            break
        if arg.startswith("--") and len(arg) > 2:
            name = arg[2:].split("=", 1)[0]
            long.add(name)
            i += 1
            if ("=" not in arg and valueless_long is not None
                    and name not in valueless_long):
                i += 1
            continue
        if arg.startswith("-") and len(arg) > 1:
            letters = arg[1:]
            for position, letter in enumerate(letters):
                short.add(letter)
                if letter in value_letters:
                    if position == len(letters) - 1:
                        i += 1
                    break
        i += 1
    return short, long


# A path-shaped backslash: a word character on each side of a backslash, e.g.
# `workflows\rule-hooks`. Used by the A2 trial rule (warn only).
#
# **Written as lookarounds rather than as `[\w.-]+\\[\w.-]+` on purpose.** The
# repeated form is quadratic: on a long unbroken run with no backslash in it -
# a hash, a token, a base64 blob - the engine tries every starting position and
# scans to the end of the run from each. Measured at 4.9 s for 20,000
# characters and 66 s for 80,000. The hook's own timeout is 5 seconds, and a
# hook that times out is a hook that allowed the command, so a long enough
# argument disabled the check. The lookaround form asks the same question at
# each backslash instead, which is linear.
PATH_BACKSLASH_RE = re.compile(r"(?<=[\w.\-])\\(?=[\w.\-])")
