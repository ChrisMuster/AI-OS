#!/usr/bin/env python3
"""Rule A9 - block an inline interpreter script that writes a project document.

An AI reaching for `python - <<'EOF' ... EOF` to edit a markdown file goes
around the Edit tool, and therefore around the B3 personal-data check that
guards it. On 2026-09-02 that put a personal name into a publicly tracked
`CONTEXT.md`. The write itself was the ordinary kind, three near-identical
appends batched into one call for convenience, which is exactly the shape this
rule expects: the threat model here is a cooperative AI reaching for the wrong
tool, not an adversary.

**This is a deliberate, narrow exception to the coverage boundary recorded in
the workflow's Known Issues**, which places arbitrary-code interpreters out of
scope because their writes "cannot be statically parsed". That reasoning holds
for a saved script - `python build.py` could write anything and nothing in the
command says so - and this rule does not attempt it. It applies only where the
code is *inline*, in a heredoc body or after `-c`, and therefore sits in the
command text where a hook can read it. The boundary moves from "interpreters are
out of scope" to "interpreters are out of scope unless the code is in front of
you", which is a real narrowing rather than an attempt at completeness.

Scope is deliberately documents rather than every file. A script writing a
`.json` index or a `.db` is doing something the Edit tool is not for; a script
writing a `.md` is almost always doing something Edit does better and safer.

**The rule asks about the write's target, not about the command, since
2026-09-04.** It originally required only that a `.md` appear somewhere and a
write verb appear somewhere, which treats the two as related when they need not
be: writing a `.json` index whose *contents* mention `README.md` satisfied both
halves and was blocked, though it writes no document at all. A cross-AI review
found it. Where the shape lets the target be read - a quoted first argument, a
literal receiver - the `.md` test is now applied to the target itself.

**Two further narrowings, after a second review found the first pass incomplete.**

- **What gets read is the interpreter's code, not the command line.** The rule
  scanned the whole command as soon as an interpreter appeared anywhere on it,
  so anything sitting beside one was treated as though the interpreter had
  written it: `python -c "print('ok')" && echo "open('README.md','w')..."`
  blocked on the quoted sample in the `echo`. Only the `-c`/`-e` argument and a
  heredoc body the interpreter itself opened are inspected now. Which command
  opened a heredoc is decided by the text before the `<<`, not by its whole
  line, because two commands share a line often enough for it to matter.
- **A write and its target are found in one pass.** They were matched by two
  separate pattern sets and then counted against each other, so a shape one set
  recognised and the other did not - `open(p, mode='w')` - made the counts
  disagree and dropped the command into the blunt whole-text fallback. Matching
  the call and capturing its target together removes the mismatch rather than
  adding a third pattern that has to be kept in step with the other two.

**Where the target is computed it cannot be read, and there the rule stays
blunt.** A variable, an f-string or a join hides the destination, and narrowing
to "only block a target I can see" would wave through `p = "notes.md"` followed
by `open(p, "w")`, which is precisely the shape this rule exists to stop. So a
command carrying any write whose target cannot be read falls back to the older
test. The fail directions are not symmetrical: a wrong block costs one retry
against a message naming the right tool, and a wrong allow costs what happened
on 2026-09-02.
"""
import ast
import re

from core import (
    Decision, effective_argv, format_block, heredoc_bodies, nested_commands,
    posix_basename, split_segments,
)

A9_ID = "A9"

# Matched on the stem so a version suffix counts: `python3.11` and `node20` run
# the same code `python3` would. A wrapper in front (`env python3 -c ...`) is
# stripped by `effective_argv` before this is consulted.
_INTERPRETER_STEMS = ("python", "node", "perl", "ruby", "php", "py")


def _interpreter_stem(word):
    """The interpreter a program word names, or None."""
    stem = re.match(r"^([A-Za-z_]+)", posix_basename(word))
    name = stem.group(1).lower() if stem else None
    return name if name in _INTERPRETER_STEMS else None


def _is_interpreter(word):
    return _interpreter_stem(word) is not None

# Inline code arrives one of two ways: a heredoc (the `<<` operator, whose body
# follows the command) or an option whose value is program text. `python -`
# also reads a script from stdin, which in practice is always a heredoc.
#
# **Which options carry code is a fact about each interpreter, not one list.**
# `-c`, `-e` and `-r` were read the same way for all six, in every position,
# and each part of that was wrong somewhere: node's `--eval`, `-p` and `-pe`
# run code and were missed; node's `-r` loads a module and was read as code;
# `python render.py -c X` passes `-c X` to render.py and was blocked; and
# `python -W ignore <<EOF` read the warning setting as a script name, which
# made the heredoc that python really runs look like the script's input, so a
# document write in it was allowed. All four were measured in Git Bash with
# python, node and perl; ruby and php are not installed here, so their rows
# are the documented options.
#
# Per interpreter: `code` letters and `code_long` names take program text;
# `value` letters take a value attached or as the next word; `attached` letters
# take only an attached value, so the next word is never theirs; `valueless`
# letters take none; `script` letters name the program file or module, which
# ends option reading just as a script path does; `value_long` and
# `valueless_long` are the long names; `code_ends` says the code option ends
# option reading, as python's `-c` does. An option these rows do not know is
# assumed possibly to take the next word, which can only make the rule read
# MORE words as options, never fewer.
_PYTHON_OPTIONS = {
    "code": set("c"), "code_long": set(), "value": set("WX"),
    "attached": set(), "valueless": set("bBdEhiIOPqRsSuvVx"),
    "script": set("m"), "value_long": {"check-hash-based-pycs"},
    "valueless_long": {"help", "version", "help-env", "help-xoptions",
                       "help-all"},
    "code_ends": True,
}
_OPTION_SPECS = {
    "python": _PYTHON_OPTIONS,
    # The Windows launcher takes a version first (`py -3.12 -c ...`).
    "py": dict(_PYTHON_OPTIONS,
               valueless=_PYTHON_OPTIONS["valueless"] | set("0123456789.-")),
    "node": {
        "code": set("ep"), "code_long": {"eval", "print"},
        "value": set("rC"), "attached": set(), "valueless": set("chiv"),
        "script": set(),
        "value_long": {"require", "import", "loader", "experimental-loader",
                       "conditions", "env-file", "input-type", "title",
                       "inspect-port", "disable-warning", "watch-path"},
        "valueless_long": {"inspect", "inspect-brk", "no-warnings",
                           "trace-warnings", "enable-source-maps", "expose-gc",
                           "check", "interactive", "test", "watch",
                           "no-deprecation", "trace-deprecation",
                           "throw-deprecation", "pending-deprecation",
                           "abort-on-uncaught-exception", "preserve-symlinks",
                           "experimental-vm-modules", "experimental-strip-types",
                           "frozen-intrinsics", "trace-uncaught", "help",
                           "version"},
        "code_ends": False,
    },
    # perl's valued options all take their value attached (`-Ilib`,
    # `-i.bak`); `-l` and `-0` take optional digits, so digits are valueless.
    "perl": {
        "code": set("eE"), "code_long": set(), "value": set(),
        "attached": set("iIMmxFdDCV"),
        "valueless": set("acnpsStTuUvwWXl0123456789"), "script": set(),
        "value_long": set(), "valueless_long": {"help", "version"},
        "code_ends": False,
    },
    "ruby": {
        "code": set("e"), "code_long": set(), "value": set("rICEX"),
        "attached": set("FixK0TW"), "valueless": set("acdhlnpsSvwyU"),
        "script": set(),
        "value_long": {"encoding", "external-encoding", "internal-encoding",
                       "enable", "disable", "dump"},
        "valueless_long": {"version", "verbose", "yydebug", "jit", "help",
                           "copyright"},
        "code_ends": False,
    },
    "php": {
        "code": set("rBRE"), "code_long": set(), "value": set("dcztS"),
        "attached": set(), "valueless": set("aehHilmnqsvw"),
        "script": set("fF"), "value_long": set(), "valueless_long": set(),
        "code_ends": False,
    },
}


def _read_interpreter_options(argv):
    """`(code, stdin_is_code)` for one interpreter invocation.

    *code* is every piece of program text the options carry. *stdin_is_code*
    is True only when the interpreter will run what arrives on stdin: no code
    option and no script or module named. With `-c`/`-e` the program is the
    argument and a heredoc is the data it reads, which is what python and perl
    were measured doing.

    Reading stops where the interpreter's own does: at a script path, at a
    module, at `-` or `--`, and for python after `-c`. Words after that are
    the program's arguments, so a `-c` among them is not code.
    """
    spec = _OPTION_SPECS[_interpreter_stem(argv[0])]
    code, program_named, uncertain = [], False, False
    args, i = argv[1:], 0
    while i < len(args):
        arg = args[i]
        following = args[i + 1] if i + 1 < len(args) else None
        if arg == "--":
            program_named = following is not None
            break
        if arg == "-":
            break
        if arg.startswith("--"):
            name, equals, value = arg[2:].partition("=")
            if name in spec["code_long"]:
                if equals:
                    code.append(value)
                elif following is not None:
                    code.append(following)
                    i += 1
                if spec["code_ends"]:
                    break
            elif name in spec["value_long"]:
                if not equals:
                    i += 1
            elif not equals and name not in spec["valueless_long"]:
                uncertain = True
            i += 1
            continue
        if arg.startswith("-") and len(arg) > 1:
            letters, takes_next, stop = arg[1:], False, False
            for position, letter in enumerate(letters):
                rest = letters[position + 1:]
                if letter in spec["code"]:
                    # `-pe` is two code letters sharing the next word; any
                    # other remainder is code written against the flag.
                    if rest and not set(rest) <= spec["code"]:
                        code.append(rest)
                    elif following is not None:
                        code.append(following)
                        takes_next = True
                    stop = spec["code_ends"]
                    break
                if letter in spec["script"]:
                    program_named = stop = True
                    break
                if letter in spec["value"]:
                    takes_next = not rest
                    break
                if letter in spec["attached"]:
                    break
                if letter not in spec["valueless"]:
                    # Unknown: keep reading the bundle, and do not trust the
                    # next bare word as a script, since it may be this
                    # option's value. Both choices read more, not less.
                    uncertain = True
            i += 2 if takes_next else 1
            if stop:
                break
            continue
        if uncertain:
            uncertain = False
            i += 1
            continue
        program_named = True
        break
    return code, not code and not program_named

# A markdown path, used only in the fallback where a write's target could not
# be read directly. The leading path characters are optional, because a
# computed target often has none: `f"{name}.md"` puts a `}` in front, and
# requiring a path character there let every f-string, `.format()` and `%`
# construction escape the fallback the docstring says covers them.
#
# **Bounded rather than unbounded on purpose.** `[\w./\\-]*` before a literal
# is quadratic: on a long unbroken run of those characters the engine retries
# from every start position. Measured at 4 minutes on a 40,000-character inline
# script, against a 5-second hook budget, and a hook that times out is a hook
# that allowed the command. A path component longer than the bound is not a
# real path, so capping the repetition costs nothing and makes the scan linear.
_MD_RE = re.compile(r"[\w./\\-]{0,200}\.md\b", re.IGNORECASE)
# The computed-target fallback for the other protected name. Bounded for the
# same reason.
_ENV_RE = re.compile(r"(?<![\w.])\.env(?:\.[\w-]{0,50})?\b", re.IGNORECASE)

# Receivers that are not files. `sys.stdout.writelines(open("README.md"))`
# prints a document and writes nothing, and reading it as a write with an
# unreadable target sent it to the fallback, which found the `.md` and blocked
# a command that was only reading.
_NON_FILE_RECEIVERS = {"sys.stdout", "sys.stderr", "stdout", "stderr",
                       "process.stdout", "process.stderr", "STDOUT", "STDERR"}

# A target that is ONE quoted string and nothing else. Testing only that the
# text starts and ends with a quote is not the same thing: `"%s.md" % "notes"`
# does both, and reading it as the literal `%s.md" % "notes` made a computed
# target look like a readable one, which skipped the fallback that exists for
# exactly that shape.
#
# A python prefix of `r`, `b` or `u`, or `rb`/`br`, still makes a plain
# literal: `Path(r"index.json")` names `index.json` as surely as the unprefixed
# form, and reading it as computed sent a non-document write to the fallback.
# `f` is deliberately absent, alone or combined, because an f-string is built
# at runtime and is exactly the computed target the fallback exists for.
_LITERAL_RE = re.compile(
    r"""^(?:[rRbBuU]|[rR][bB]|[bB][rR])?(['"])([^'"]*)\1$""")

# A captured target that is really a keyword argument, `name=value`. The `(?!=)`
# keeps a comparison such as `a == b` from reading as one.
_KEYWORD_TARGET_RE = re.compile(r"""^(\w+)\s*=(?!=)\s*(.*)$""", re.DOTALL)
# Keywords whose value IS the path: python's `open(file=...)` and the
# destination of `shutil.copy(src, dst=...)`.
_PATH_KEYWORDS = {"file", "dst", "path", "filename"}

# Python joins adjacent string literals at compile time, so
# `open('notes.' 'md', 'w')` writes `notes.md` while no `.md` text exists
# anywhere in the code for the computed-target fallback below to find. The same
# holds for three or more parts, for a split across a newline, and for `.env`.
#
# **The shape is refused rather than joined.** Joining was the alternative and
# is what the backlog item proposed, but it means lexing the parts exactly as
# Python does - both quote styles, escapes, the r/b/u prefixes, triple quotes -
# and every difference between that and the real lexer is a fresh way through
# the hole being closed. That is the argument that ended the A6 `find -delete`
# series and settled A10, and it applies here for the same reason: a refusal
# reads nothing, so it cannot be wrong about what the parts spell.
#
# The accepted cost is that a split literal naming a NON-document is refused
# too. It was measured before being accepted: across 27,278 indexed transcript
# messages spanning 2026-05-18 to 2026-09-21, every occurrence of this shape in
# the project's history is the review that reported it, and there is no instance
# of real use. A measured false block on ordinary work is the signal to revisit
# it.
#
# **Since 2026-09-22 this is the backstop, not the main check.** For code the
# Python parser accepts, `_hidden_documents` below answers the question
# definitively and runs first; this regex still catches the whitespace join in
# code the parser rejects, and in ruby, which also joins adjacent literals.
_ADJACENT_LITERALS_RE = re.compile(
    r"""(?<!\w)(?:[rRbBuU]|[rR][bB]|[bB][rR])?(['"])[^'"]*\1"""
    r"""\s*(?:[rRbBuU]|[rR][bB]|[bB][rR])?(['"])[^'"]*\2""")


def _splits_a_literal(text):
    """True when *text* holds two quoted literals joined by whitespace only.

    Whitespace is the whole test, and that is deliberate. `"%s.md" % "notes"`
    carries an operator between its literals, is genuinely assembled at
    runtime, and must keep going to the computed-target fallback that exists
    for exactly that shape rather than being refused here. An `f` prefix is
    absent from the prefix set for the same reason it is absent from
    `_LITERAL_RE`: an f-string is computed, so `f"{x}" "y"` stays a computed
    target.
    """
    return bool(text) and _ADJACENT_LITERALS_RE.search(text) is not None


# The part of a string a document test is asked of: the run of filename
# characters at its end, after the last path separator. Bounded for the same
# reason `_MD_RE` is.
_TRAILING_NAME_RE = re.compile(r"[\w.-]{1,200}$")

# One source line with its ending. Python's tokenizer ends a line at `\r\n`,
# `\r` or `\n` and nowhere else, which is why `str.splitlines` is not used: it
# also splits at form feeds and several Unicode separators, and would number
# the lines differently from the parser.
_SOURCE_LINE_RE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$")


class _SourceText:
    """A node's own source text, with the line table built once per program.

    `ast.get_source_segment` re-splits the whole program on every call, so a
    script holding many document-named strings cost quadratic time: measured
    at 5.08 seconds for 2,000 of them in 65,000 characters, which is past the
    hook's budget, and a hook that runs out of time has allowed the command.
    Node columns are UTF-8 byte offsets, so each line is encoded, once, before
    it is sliced.
    """

    def __init__(self, code):
        self.lines = _SOURCE_LINE_RE.findall(code)
        self.encoded = {}

    def _line(self, index):
        if index not in self.encoded:
            self.encoded[index] = self.lines[index].encode("utf-8")
        return self.encoded[index]

    def of(self, node):
        """The text *node* was parsed from, or None if it cannot be located."""
        try:
            first, last = node.lineno - 1, node.end_lineno - 1
            if first == last:
                return self._line(first)[
                    node.col_offset:node.end_col_offset].decode("utf-8")
            return (self._line(first)[node.col_offset:].decode("utf-8")
                    + "".join(self.lines[first + 1:last])
                    + self._line(last)[:node.end_col_offset].decode("utf-8"))
        except (AttributeError, IndexError, TypeError, UnicodeDecodeError):
            return None


def _hidden_documents(code):
    """Document names Python builds in *code* that its source never spells out.

    **Asked of Python's own parser, which is what makes it definitive.** The
    regex refusal above recognises one join, whitespace, and review found the
    rest of the family it could not see (PP1, 2026-09-22): a comment between
    the parts, a backslash continuation, a part holding the other quote
    character, and single literals with no join at all - `'notes\\x2emd'` and
    `'notes\\N{FULL STOP}md'` hide the dot behind an escape. Each is a place
    where a hand-written reading of Python differs from the real one, and
    listing them would have been the same defect again. The parser has no such
    difference to exploit: it is the reader that runs the code, so it hands back
    every string exactly as Python will use it, with the parts joined, comments
    and continuations dropped and escapes decoded, whatever the spelling.

    It is still a refusal rather than an assembly. Nothing is inferred about
    what the code writes, which is why this runs before the write shapes are
    consulted: one of the spellings (a comma inside the comment) stops those
    shapes recognising the call as a write at all. The cost is that a read
    spelled the same way is refused too, measured before it was accepted: read
    as though it were inline code, no tracked Python file spells a document
    name this way, and the transcript measurement recorded above for the
    adjacent-literal shape found no real use of it.

    Only the filename at the END of the string is compared, and only against
    that string's own source text. Comparing the whole string refused
    `print("\\nwrote notes.md")` and an escaped Windows path, whose filenames
    are written out in full; the question is whether the document's name is
    visible to a reader of the text, not whether every character is.

    Returns None when *code* is not something the parser accepts, so the caller
    keeps the rule's other checks as they were, and a list otherwise.
    """
    try:
        tree = ast.parse(code)
    except Exception:  # noqa: BLE001 - any failure means "not readable as Python"
        return None
    source = _SourceText(code)
    hidden = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        value = node.value
        if isinstance(value, bytes):
            value = value.decode("latin-1")
        if not isinstance(value, str):
            continue
        last = value.replace("\\", "/").rsplit("/", 1)[-1]
        name = _TRAILING_NAME_RE.search(last)
        if name is None or not _is_document(name.group(0)):
            continue
        name = name.group(0)
        # Only this string's own text is searched. Searching the whole program
        # first looked like a cheap shortcut and was the opposite: one pass over
        # everything per string is quadratic in a script full of paths.
        if name not in (source.of(node) or code):
            hidden.append(name)
    return hidden


def _follows_a_dot(code, index):
    """True when the text before *index* ends in a dot, ignoring spaces.

    Looks back a bounded distance on purpose: slicing everything before each
    match makes the scan quadratic in the size of the code, and a hook that
    runs past its five-second budget has allowed the command.
    """
    i = index - 1
    stop = max(-1, index - 40)
    while i > stop and code[i] in " \t\r\n":
        i -= 1
    return i > stop and code[i] == "."

# One entry per way of writing a file. Each pattern matches the whole write
# call and captures its target, so a write and its target are found in a single
# pass and cannot be counted separately. Counting them separately is what made
# `open(path, mode='w')` behave differently from `open(path, 'w')`: the verb
# pattern matched both, the target pattern matched only the second, and the
# mismatch dropped the command into the blunt whole-text fallback.
#
# The target group prefers a quoted string, so a path containing a comma is
# still read as one target, and otherwise takes a bare expression, allowing one
# level of nested parentheses so `open(os.path.join(a, b), "w")` is recognised
# as a write with a computed target rather than missed altogether.
# Every repetition here is bounded for the same reason `_MD_RE` is: an
# unbounded class followed by a literal is quadratic, and these patterns run
# over every inline interpreter body whether or not it contains a write, so a
# long argument stalled the hook past its timeout and thereby allowed the
# command. No real filename or call target approaches these lengths.
_TARGET = r"""['"][^'"]{0,300}['"]|(?:[^,()]|\([^()]{0,200}\)){0,200}"""

# A short quoted token that COULD be a file mode. Whether it actually is one,
# and whether it permits writing, is decided by `_permits_writing` below rather
# than by this pattern.
#
# Splitting it that way is the point. The first attempt at "does this mode
# permit writing" was a regex, `['"](?:[wax][btU+]{0,3}|r[btU]{0,2}\+...)['"]`,
# which encodes an assumption Python does not make: that the r/w/x/a letter
# comes first. Python parses mode characters order-independently, so `"bw"`,
# `"+w"`, `"+r"`, `"br+"`, `"b+r"`, `"ba+"` and `"t+r"` are all writable and all
# went straight past it. That is the same list-instead-of-a-question defect the
# rewrite was meant to fix, committed again in regex notation - which is why the
# question now lives in a function that can be read and tested as one.
#
# The closing quote and the four-character bound stay, because they are what
# keep a filename from being read as a mode: without them
# `io.open('workflows/x.md', ...)` matched as a write, since the path begins
# with a `w`, and the rule blocked one of this project's own commands.
#
# An encoding suffix is allowed after the mode letters, because ruby's Kernel
# `open("notes.md", "w:UTF-8")` reaches the python-shaped `open(target, mode)`
# pattern and was missed for the suffix alone. Python never writes one, so the
# widening costs nothing there, and only the letters are handed to
# `_permits_writing`.
_MODE_CAPTURE = r"""['"](?P<m>[A-Za-z+]{1,4})(?::[\w|-]{1,20}){0,2}['"]"""


def _permits_writing(mode):
    """True when *mode* is a real Python file mode that allows writing.

    Python's grammar: exactly one base letter from r/w/x/a, optionally one of
    b/t, optionally `+`, and no character twice. Writing is permitted when the
    base is w, a or x, or when a `+` is present in any position.

    The "exactly one base letter" test is what stops an ordinary word made of
    mode letters being read as a mode: `.open('war')` carries three base
    letters and is not a mode at all.
    """
    if not mode or len(mode) > 4 or len(set(mode)) != len(mode):
        return False
    if not set(mode) <= set("rwxabtU+"):
        return False
    base = [character for character in mode if character in "rwxa"]
    if len(base) != 1:
        return False
    return base[0] in "wax" or "+" in mode
# Keyword arguments written BEFORE `mode=`. Python takes keywords in any order,
# and this project's encoding rules make `open(path, encoding="utf-8",
# mode="w")` a likely spelling, so a pattern that wanted `mode=` straight after
# the path missed an ordinary write. Bounded like every repetition here.
_KEYWORD_ARGUMENTS = (r"""(?:\w{1,30}\s*=\s*(?:['"][^'"]{0,60}['"]"""
                      r"""|[^,()'"]{0,60})\s*,\s*){0,6}""")
# perl's write modes: `>`, `>>`, `+<`, `+>`, `+>>`, each optionally followed by
# a layer such as `:encoding(UTF-8)`. A plain `<` reads and is not among them.
_PERL_WRITE_MODE = r"""(?:\+?>>?|\+<)"""
# A perl handle: `FH`, `my $fh`, `local *FH`. Parentheses around the arguments
# are optional in perl, so both `open(...)` and `open ...` are read.
_PERL_OPEN = (r"""open\s*(?:\(\s*|\s+)(?:(?:my|our|local)\s+)?"""
              r"""[^,()'"]{0,60}\s*,\s*""")
_WRITE_CALL_RES = (
    # python: open(path, mode), positional or mode= keyword, with any other
    # keywords before `mode=`. The mode is captured and judged by
    # `_permits_writing`, not matched as a shape.
    (re.compile(r"""open\s*\(\s*(?P<t>""" + _TARGET
                + r""")\s*,\s*(?:""" + _KEYWORD_ARGUMENTS + r"""mode\s*=\s*)?"""
                + _MODE_CAPTURE), False),
    # node
    (re.compile(r"""(?:writeFileSync|appendFileSync)\s*\(\s*(?P<t>"""
                + _TARGET + r""")\s*[,)]"""), False),
    # node, callback and promise forms
    (re.compile(r"""(?:writeFile|appendFile)\s*\(\s*(?P<t>"""
                + _TARGET + r""")\s*[,)]"""), False),
    # perl: two-argument open(FH, ">file") and open my $fh, ">>file". The
    # target may not begin with `&`, which duplicates a handle rather than
    # naming a file.
    (re.compile(_PERL_OPEN + r"""['"]""" + _PERL_WRITE_MODE
                + r"""\s*(?P<t>[^'"<>&+\s][^'"]{0,300})['"]"""), True),
    # perl: three-argument open(my $fh, ">", "file"), with or without
    # parentheses and with an optional layer on the mode.
    (re.compile(_PERL_OPEN + r"""['"]""" + _PERL_WRITE_MODE
                + r"""(?::[^'"]{0,40})?['"]\s*,\s*(?P<t>""" + _TARGET
                + r""")\s*(?:[;)}\n]|\bor\b|\|\||$)"""), False),
    # ruby: Kernel `open` without parentheses, `open "notes.md", "w:UTF-8" do
    # |f| ... end`. The parenthesised form is read by the python-shaped
    # pattern above. A dot, colon or sigil in front means a method or a
    # variable rather than Kernel `open`, and those are read elsewhere.
    (re.compile(r"""(?<![\w.:$@])open\s+(?P<t>""" + _TARGET
                + r""")\s*,\s*(?:mode\s*:\s*)?""" + _MODE_CAPTURE), False),
    # ruby: `File.write` always writes, and so do `IO.write` and both classes'
    # `binwrite`. Ruby calls need no parentheses, so `File.write "notes.md",
    # "x"` is the same call and is read the same way.
    (re.compile(r"""(?:File|IO)\s*\.\s*(?:bin)?write\s*(?:\(\s*|\s+)(?P<t>"""
                + _TARGET + r""")\s*[,)]"""), False),
    # ruby: `File.open` takes the mode second and DEFAULTS TO READ, exactly as
    # python's does. Treating every `File.open` as a write blocked
    # `File.open("README.md") { |f| puts f.read }`, which is the ordinary way
    # to read a file in a one-liner - and an over-block on a rule that already
    # has an over-block surface is what gets a hook routed around. The mode is
    # judged by `_permits_writing` rather than matched as a shape, so the write
    # modes still block.
    #
    # **Three ruby spellings the python shapes do not reach**, each of which
    # this rule blocked before the read/write split and would otherwise have
    # lost: the `mode:` keyword, which is ruby's spelling of the keyword form
    # python writes as `mode=`; the `"w:UTF-8"` encoding suffix, which is the
    # form this project's own encoding rules push an author toward and so the
    # likeliest to appear here; and an integer mode built from `File::`
    # constants, which carries no quoted mode at all and is treated as a write
    # when any of the writing constants is named. `File::RDONLY` is not among
    # them and stays a read. Ruby is not installed on this machine, so these
    # are the language's documented behaviour rather than a local measurement;
    # the block-versus-allow results themselves were measured against the hook.
    #
    # `File.new` takes the same arguments as `File.open` and is read the same
    # way. The encoding suffix may name an external and an internal encoding
    # (`"w:UTF-8:UTF-8"`), and matching only one let the fuller documented
    # spelling of the same write through.
    (re.compile(r"""File\s*\.\s*(?:open|new)\s*(?:\(\s*|\s+)(?P<t>""" + _TARGET
                + r""")\s*,\s*(?:mode\s*:\s*)?"""
                r"""(?:['"](?P<m>[A-Za-z+]{1,4})(?::[\w-]{1,20}){0,2}['"]"""
                r"""|File\s*::\s*(?:WRONLY|RDWR|APPEND|CREAT|TRUNC))"""),
     False),
    # php
    (re.compile(r"""file_put_contents\s*\(\s*(?P<t>"""
                + _TARGET + r""")\s*[,)]"""), False),
    # python: writes by another name - a copy and a rename land a file just as
    # an open-and-write does, and the destination is the second argument.
    (re.compile(r"""(?:shutil\s*\.\s*(?:copy|copy2|copyfile|move)|"""
                r"""os\s*\.\s*(?:rename|replace)|os\s*\.\s*makedirs)"""
                r"""\s*\([^,)]*,\s*(?P<t>""" + _TARGET + r""")\s*\)"""), False),
)


def _interpreter_argvs(segments):
    """The segments that run an interpreter, as their effective argv.

    A wrapper or an environment assignment in front (`env python3 -c ...`,
    `PYTHONPATH=. python3 -c ...`) puts something else in argv[0], and reading
    argv[0] alone found no interpreter and allowed the write.
    """
    found = []
    for argv in segments:
        effective = effective_argv(argv)
        if effective and _is_interpreter(effective[0]):
            found.append(effective)
    return found


def _takes_stdin_as_code(argv):
    """True when this interpreter runs what arrives on stdin.

    `python - <<EOF` and `python <<EOF` execute the body. `python render.py
    <<EOF` runs a saved script and the body is that script's INPUT, which this
    rule has no business reading: a document named in a script's data is not a
    document the command writes. The same holds for `python -c CODE <<EOF`,
    where CODE is the program and the body is what it reads.
    """
    return _read_interpreter_options(argv)[1]


def _inline_code(command, segments):
    """The interpreter code carried in the command text, or ""."""
    return "\n".join(_inline_chunks(command, segments))


def _inline_chunks(command, segments, _depth=0):
    """The interpreter code carried in the command text, one entry per program.

    Kept apart rather than only joined because `_hidden_documents` asks the
    parser about each program separately: two programs joined with a newline
    are rarely one valid Python module, and a parse failure there would hide a
    python chunk behind a node one.

    Only the code itself, never the whole command line. Scanning the whole line
    treats anything sitting beside an interpreter as though the interpreter had
    written it, so `python -c "print('ok')" && echo "open('README.md','w')..."`
    blocked on the quoted sample text in the `echo`. A heredoc body is taken
    only when an interpreter opened it, because a body belongs to one command
    and a `cat` heredoc is not this rule's business.

    Which command opened it is decided by the text immediately before the
    `<<`, not by the whole opening line. Two commands share a line often
    enough for the difference to matter: in `python -c "print(1)" && cat
    <<EOF`, the body belongs to `cat`, and a line-level test reads it as
    python's script. Mutation testing found this, after the first control
    written for it turned out to pass through an earlier return.
    """
    chunks = []
    for argv in _interpreter_argvs(segments):
        # The shell concatenates, so `python -c'code'` arrives as one word;
        # the option reader takes the rest of such a word as the code.
        chunks.extend(_read_interpreter_options(argv)[0])
    for prefix, body in heredoc_bodies(command):
        opener = split_segments(prefix)
        if not opener:
            continue
        owner = effective_argv(opener[-1])
        if owner and _is_interpreter(owner[0]) and _takes_stdin_as_code(owner):
            chunks.append(body)
    # A heredoc opened INSIDE a shell's `-c` string is invisible at the outer
    # level, correctly, because the `<<` sits inside quotes there. It has to be
    # looked for at the inner level instead, or `bash -c "python - <<'PY' ...
    # PY"` is a wrapper that hides the rule entirely.
    if _depth < 4:
        for argv in segments:
            for carried in nested_commands(argv):
                inner = split_segments(carried)
                if inner is None:
                    continue
                chunks.extend(chunk for chunk in
                              _inline_chunks(carried, inner, _depth + 1)
                              if chunk.strip())
    return chunks


def _clean_target(target):
    """A literal target with any query string or fragment removed.

    `notes.md?raw=1` is still a write to `notes.md`.

    **`maxsplit` is passed by keyword, and that is load-bearing rather than
    tidy.** Python 3.13 deprecates the positional form, so under
    `PYTHONWARNINGS=error` - or a future release that promotes the warning -
    this call raised. A raising rule is caught by the evaluator's fail-safe,
    which logs it and ALLOWS the command, so a deprecation in hook code turns a
    document-write block into an allow. For a hook, a compatibility problem is
    a correctness problem.
    """
    return re.split(r"[?#]", target.strip(), maxsplit=1)[0]


def _is_document(target):
    """True when a literal target names a file this rule protects.

    Markdown, because that is where the review trail and the personal-data risk
    live, and `.env`, which was covered by nobody: A7 guards the Edit/Write
    path and the shell write verbs, and never reads interpreter code, while
    this rule narrowed to documents. So `python -c "open('.env','w')..."` went
    past both, through the same interpreter route that A9 exists to close and
    against the one file the project protects most carefully. `LOG.md` was only
    ever covered here by the accident of its extension.

    Case-folded because on this project's platform `NOTES.MD` and `notes.md`
    are one file.
    """
    name = _clean_target(target).lower().replace("\\", "/").rsplit("/", 1)[-1]
    if name.endswith(".md"):
        return True
    return name == ".env" or (name.startswith(".env.")
                              and name != ".env.example")


# Writes whose target sits BEFORE the call: `Path("x").write_text(...)`,
# `f.writelines(...)`, `Path("x").open("w")`. These are found by searching for
# the literal method first and then reading the receiver backwards, rather than
# by a regex beginning with a character class.
#
# The regex form was `(?P<t>['"][^'"]*['"]|[\w.\[\]]+)\s*\)?\s*\.write...`,
# which starts with a class and so is retried at every position in the text:
# quadratic, and measured at four minutes on a 40,000-character inline script
# against a 5-second hook budget. Bounding the repetition helped and was not
# enough, because 40,000 start positions times the bound is still millions of
# attempts. Anchoring on the literal removes the start positions instead.
#
# The mode string must CLOSE, not merely start with a write letter. Without the
# closing quote, `io.open('workflows/x.md', encoding='utf-8')` matched as a
# one-argument `.open("w")` because the path happens to begin with a `w`, so
# any read of a file whose name starts with w, a or x was read as a write. The
# rule blocked one of this project's own commands that way.
#
# **The mode may be written as a keyword and may be followed by more
# arguments.** The pattern required the mode to be the whole argument list, so
# only the bare `Path("x").open("w")` was recognised, and the two spellings this
# project's own encoding rules produce - `open(mode="w")` and
# `open("w", encoding="utf-8")` - were both missed. That is a false negative on
# the exact route the rule exists to close, which is worse than the over-block
# the strict form was guarding against.
#
# Other keywords may come before `mode=` here too, for the reason given at
# `_KEYWORD_ARGUMENTS`: fixing the builtin `open()` and leaving
# `Path.open(encoding=..., mode=...)` would be the one-sibling-taught shape this
# rule set keeps recording.
_RECEIVER_CALL_RE = re.compile(
    r"""\.\s*(?:write(?:_text|_bytes|lines)\s*\("""
    r"""|open\s*\(\s*(?:""" + _KEYWORD_ARGUMENTS + r"""mode\s*=\s*)?"""
    + _MODE_CAPTURE + r"""\s*[,)])""")
_RECEIVER_CHARS = set("_.[]")

# Receivers whose `.open(...)` takes the FILE first and the mode second, where
# `Path.open(...)` takes the mode first. Reading their first argument as a mode
# is how `io.open('workflows/x.md', encoding='utf-8')` came to be read as a
# write once before: the path begins with a `w`. The closing-quote and
# four-character bounds in `_MODE_CAPTURE` stop that for any real path, and
# these are named as well because a short filename made only of mode letters
# (`io.open('ab', 'r')`) would still qualify. Nothing is lost by skipping them
# here: the `open(target, mode)` pattern above reads exactly these calls, and
# reads them correctly, including ruby's `File.open`.
_FILE_FIRST_OPEN = {"io", "codecs", "gzip", "bz2", "lzma", "os", "tarfile",
                    "zipfile", "shutil", "webbrowser", "File"}


def _split_arguments(arguments):
    """The top-level comma-separated arguments of *arguments*, stripped."""
    parts, depth, quote, start = [], 0, None, 0
    for index, char in enumerate(arguments):
        if quote:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(arguments[start:index].strip())
            start = index + 1
    last = arguments[start:].strip()
    if last or parts:
        parts.append(last)
    return parts


def _receiver_before(code, end):
    """The expression a `.write...(` call was made on, or None.

    Scans backwards from the dot: a call, a quoted string, or a bare
    identifier chain. A call with exactly one argument is read by that
    argument; a call with none or several is returned as its own text, which
    no literal matches, so the write is recorded with a computed target.

    **It used to read the last quoted string before the `)`**, which was the
    path for `Path("x").write_text(` and the MODE for any two-argument call:
    `io.FileIO("notes.md", "w").writelines(x)` recorded `"w"` as a literal
    target, found it was not a document, and - every recorded target being
    literal - skipped the computed-target fallback that would have caught the
    real path. Adding a write to a command made it more likely to be allowed,
    which inverts the rule's stated fail direction.
    """
    i = end - 1
    while i >= 0 and code[i] in " \t":
        i -= 1
    if i >= 0 and code[i] == ")":
        close, depth, quote, j = i, 0, None, i
        while j >= 0:
            char = code[j]
            if quote:
                if char == quote:
                    quote = None
            elif char in "'\"":
                quote = char
            elif char == ")":
                depth += 1
            elif char == "(":
                depth -= 1
                if depth == 0:
                    break
            j -= 1
        if j < 0:
            return None
        arguments = _split_arguments(code[j + 1:close])
        if len(arguments) == 1:
            return arguments[0] or None
        # No argument, or several: the path is not written out as one
        # literal, so the target is COMPUTED. The call text is returned rather
        # than None, which records the write with an unreadable target and so
        # sends the command to the computed-target fallback. Taking the first
        # of several arguments was right for `io.FileIO(path, mode)` and wrong
        # for `Path("docs", "notes.md")`, where the first is only a directory
        # and read as a literal non-document, and returning None for
        # `Path("notes.md").resolve()` dropped the write altogether.
        return code[j:close + 1]
    if i >= 0 and code[i] in "'\"":
        quote, j = code[i], i - 1
        while j >= 0 and code[j] != quote:
            j -= 1
        return code[j:i + 1] if j >= 0 else None
    start = i
    while start >= 0 and (code[start].isalnum() or code[start] in _RECEIVER_CHARS):
        start -= 1
    return code[start + 1:i + 1] or None


def _writes(code):
    """Return (targets, splits).

    `targets` holds one entry per write call: its literal target, or None if
    computed. `splits` holds the target text of any write whose target is
    spelled as adjacent string literals, which A9 refuses rather than
    assembling - see `_splits_a_literal`. It is collected here rather than by a
    second scan of the code, because two walks over the same patterns are free
    to disagree, which is the defect shape this rule's own history is made of.

    A write onto a non-file receiver is not a write at all and is left out
    entirely rather than recorded with an unreadable target: recording it sent
    every `sys.stdout.writelines(...)` into the computed-target fallback, where
    a `.md` mentioned anywhere blocked a command that only read one.
    """
    def writes_here(match):
        """False when this call carries a mode that does not permit writing.

        A call with no mode at all (`.write_text(`, `writeFileSync(`) always
        writes, so an absent capture is a write.
        """
        mode = match.groupdict().get("m")
        return mode is None or _permits_writing(mode)

    found = []
    splits = []
    for match in _RECEIVER_CALL_RE.finditer(code):
        if not writes_here(match):
            continue
        receiver = _receiver_before(code, match.start())
        if receiver is None or receiver in _NON_FILE_RECEIVERS:
            continue
        if (match.groupdict().get("m") is not None
                and receiver.rsplit(".", 1)[-1] in _FILE_FIRST_OPEN):
            # A file-first `open`, so what matched as a mode is the PATH. The
            # `open(target, mode)` pattern reads this same call properly.
            continue
        receiver_text = receiver.strip()
        literal = _LITERAL_RE.match(receiver_text)
        if literal is None and _splits_a_literal(receiver_text):
            splits.append(receiver_text)
        found.append(literal.group(2) if literal else None)
    for pattern, already_unquoted in _WRITE_CALL_RES:
        for match in pattern.finditer(code):
            if not writes_here(match):
                continue
            raw = (match.group("t") or "").strip()
            if raw in _NON_FILE_RECEIVERS:
                continue
            keyword = _KEYWORD_TARGET_RE.match(raw)
            if keyword:
                if keyword.group(1) in _PATH_KEYWORDS:
                    # `open(file="notes.md", mode="w")` names its path by
                    # keyword, and the value is the target.
                    raw = keyword.group(2).strip()
                elif _follows_a_dot(code, match.start()):
                    # `Path("x").open(encoding=..., mode="w")`: the `open(`
                    # pattern also matches a method call, where the first
                    # argument is a keyword and never the path. The receiver
                    # scan reads that call; reading `encoding=...` here as a
                    # computed target sent a `.json` write to the fallback.
                    continue
            literal = _LITERAL_RE.match(raw)
            if already_unquoted:
                found.append(raw)
            elif literal:
                found.append(literal.group(2))
            else:
                if _splits_a_literal(raw):
                    splits.append(raw)
                found.append(None)
    return found, splits


def check(ctx):
    """A9: inline interpreter code that writes a markdown file -> block."""
    if not ctx.command:
        return None
    segments = split_segments(ctx.command)
    if segments is None:
        return None
    interpreters = _interpreter_argvs(segments)
    if not interpreters:
        return None

    chunks = _inline_chunks(ctx.command, segments)
    code = "\n".join(chunks)
    if not code.strip():
        return None

    for chunk in chunks:
        # Asked before the write shapes, because it does not depend on them:
        # see `_hidden_documents` for why a spelling that hides the name can
        # also hide the write.
        hidden = _hidden_documents(chunk)
        if hidden:
            return Decision.block(A9_ID, format_block(
                f"inline {posix_basename(interpreters[0][0])} code that builds "
                f"the document name {hidden[0]} without writing it out",
                "Python assembles this string from pieces, escapes or "
                "comments, so the command's text never spells the name it "
                "produces. A name no reader of the command can see is refused "
                "whether it is written or read, because a spelling that hides "
                "the name can also hide the write.",
                "write the filename as a single literal, spelled out in full. "
                "If it names a project document, use the Edit tool on it "
                "instead."))

    writes, splits = _writes(code)
    if not writes:
        return None

    if splits:
        # Refused before the literal/computed branch below, because a split
        # target reaches that branch as "computed" and the fallback then looks
        # for contiguous `.md` text this spelling never produces, which is the
        # bypass itself.
        return Decision.block(A9_ID, format_block(
            f"inline {posix_basename(interpreters[0][0])} code whose write "
            f"target is split across adjacent string literals: {splits[0]}",
            "Python joins adjacent string literals at compile time, so a "
            "filename spelled this way names a file that no text in the "
            "command contains. The shape is refused rather than assembled, "
            "because assembling it means matching Python's own lexer exactly "
            "and any difference between the two is a way through.",
            "write the filename as a single literal. If it names a project "
            "document, use the Edit tool on it instead."))

    if all(target is not None for target in writes):
        # Every write names its target literally, so the question can be asked
        # of the targets rather than of the surrounding text.
        documents = [t for t in writes if _is_document(t)]
        if not documents:
            return None
        target = documents[0]
    else:
        # At least one write computes its target - a variable, an f-string, a
        # join - so it cannot be read here. Fall back to the older, blunter
        # test rather than allowing it: `p = "notes.md"` then `open(p, "w")`
        # is exactly the shape a narrower rule would wave through, and A9
        # exists because this route put a personal name into a tracked file.
        # A wrong block costs one retry against a message naming the right
        # tool; a wrong allow costs what the rule was written to prevent.
        # The fallback reads the interpreter's code, not the whole command.
        match = _MD_RE.search(code) or _ENV_RE.search(code)
        if not match:
            return None
        target = match.group(0)
    return Decision.block(A9_ID, format_block(
        f"inline {posix_basename(interpreters[0][0])} code writing {target}",
        "Editing a project document through an interpreter goes around the "
        "Edit tool, and so around the personal-data check that guards it. That "
        "is how a personal name reached a publicly tracked CONTEXT.md on "
        "2026-09-02. CLAUDE.md prescribes the Edit tool for modifying files.",
        f"use the Edit tool on {target}, one call per file. If the change "
        f"genuinely cannot be expressed as an edit, say so and ask rather than "
        f"scripting around it."))
