#!/usr/bin/env python3
"""Encoding guard - keep the project's text encoding clean and consistent.

Two modes:

    python workflows/encoding-guard/scripts/run.py --check [--json]
    python workflows/encoding-guard/scripts/run.py --fix [--dry-run]

--check (default) is read-only. It walks the project tree and reports:
    FAIL  a text file that is not valid UTF-8 (breaks strict readers)
    WARN  mojibake (double-encoded Windows-1252 punctuation) or an unexpected BOM
    WARN  CR line endings, where the project (and .gitattributes) require LF
    WARN  a text-mode subprocess call with no explicit encoding= (a Windows
          cp1252 decode trap, the exact bug class this workflow exists to stop)
    WARN  a text-mode write with no explicit newline= (the write-side half of
          the same trap: Windows text mode turns every LF into CRLF on the way
          out, so an unpinned write recreates the CR findings above)
    WARN  an encoding= or newline= that is present but is not a value the rule
          allows (encoding=None, encoding="cp1252", newline=None, or a newline
          spelled as a carriage return and a line feed)
    INFO  an open()/.open()/read_text()/write_text() call with no explicit
          encoding=
    DEGRADED  a .py file the code checks could not be read (it does not parse
          as Python, or does not tokenise). Non-blocking: it says the check did
          not run on that file, not that the file is wrong.

Calls are found on the parse tree, not by matching call-shaped text. That is
what keeps a *definition* out of the results: `def open(self, mode="w")` reads
as a call to any regex and is not a call at all, and since an `encoding` WARN
now hard-fails close-out, a harmless definition of that name would have blocked
the gate. A file that cannot be parsed is reported DEGRADED and its code checks
are skipped, rather than scanned as raw text - the old fallback left strings and
comments unblanked, so an unparseable file produced findings out of prose.

Both arguments are checked for their value, not merely their presence, because
AGENTS.md states both clauses as values. They differ in how wide the allowed set
is: encoding accepts only the spellings of UTF-8, while newline accepts an
escaped line feed and also the empty string, which is what csv needs to stop the
writer translating at all. A value is read for what it evaluates to rather than
for how it was typed, so an escaped spelling of the right value passes and a raw
literal is judged by the characters it really carries. What the scanner cannot
read - a variable, an expression, an f-string, or a non-string literal - is left
silent rather than guessed at.

Both the builtin open() and the Path.open() form are covered on both clauses.
They differ in where the mode sits (see _OPEN_CALLS): missing the dotted form is
how a CRLF-writing test fixture survived the 2026-08-04 clearance pass.

The encoding and newline clauses are checked independently. Pinning one must
never suppress the other: until 2026-08-04 the code check returned early on
encoding=, which left the newline rule unenforced everywhere it mattered most.

--fix repairs the flagged files in place: it restores valid UTF-8, folds
Windows-1252 punctuation and mojibake to plain ASCII (per the em-dash-avoidance
rule), strips an unexpected BOM, and normalises line endings to LF. A file whose
only fault is CR endings gets the newline normalisation alone, so a clean file is
never punctuation-folded just because it was written on Windows. It is
idempotent and honours --dry-run; --preserve-mtime keeps modification times, so
a bulk newline pass cannot fool a guard that reads mtime as evidence.

Scraped/imported third-party data is deliberately exempt: 'raw/' import folders
and 'collections/' data are pruned from the walk so verbatim source data is
never rewritten.

Detection signatures are built from Unicode escapes so this source file stays
pure ASCII and never flags or repairs itself.

Exit codes: 0 when there is no FAIL finding; 1 when a FAIL finding exists (so a
caller or CI can gate on it). --json always prints the payload regardless.
"""

import argparse
import ast
import io
import json
import os
import re
import sys
import tokenize
from datetime import datetime, timezone
from pathlib import Path

# Eat our own dog food: a UTF-8 stdout so this report never mojibakes when piped
# or redirected on Windows (the default there is cp1252).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
WORKFLOW_LOG = PROJECT_ROOT / "workflows" / "encoding-guard" / "LOG.md"
ROOT_LOG = PROJECT_ROOT / "LOG.md"

Finding = tuple  # (severity, label, message)

# ---------------------------------------------------------------------------
# What to scan
# ---------------------------------------------------------------------------
TEXT_EXTS = {
    ".md", ".py", ".json", ".toml", ".txt", ".js", ".ts", ".html", ".css",
    ".yml", ".yaml", ".cfg", ".ini", ".example", ".gitignore", ".gitattributes",
}
TEXT_NAMES = {".gitignore", ".gitattributes"}

# Never descended into: version-control internals, virtualenvs, dependency
# trees, caches, and IDE/tooling config that hold no project-authored prose.
SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules", ".obsidian",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".idea", ".vscode", ".cache", ".tox", ".svn", ".hg",
}
# Directory names that mark scraped/imported verbatim data (pruned from the walk).
EXEMPT_DIR_NAMES = {"raw"}
# Path segments that mark generated/scraped data (whole subtree pruned).
EXEMPT_PATH_PARTS = {"collections", "archive"}

# ---------------------------------------------------------------------------
# Encoding signatures and repair maps (built from escapes -> ASCII source)
# ---------------------------------------------------------------------------
# Legitimate smart punctuation -> ASCII. Built with chr() so this source stays
# pure ASCII and never flags or repairs itself. Only applied to files that are
# already flagged for repair (a clean file with legit punctuation is untouched).
SMART_PUNCT = {
    chr(0x2014): "-",    # em dash
    chr(0x2013): "-",    # en dash
    chr(0x2019): "'",    # right single quote
    chr(0x2018): "'",    # left single quote
    chr(0x201C): '"',    # left double quote
    chr(0x201D): '"',    # right double quote
    chr(0x2026): "...",  # horizontal ellipsis
    chr(0x00A0): " ",    # non-breaking space
}

BOM = chr(0xFEFF)


def _cp1252_char(b):
    """Decode a single byte the way the original corruption did: Windows-1252
    where defined, falling back to the raw codepoint (latin-1) for the handful of
    bytes cp1252 leaves undefined (0x81, 0x8D, 0x8F, 0x90, 0x9D)."""
    try:
        return bytes([b]).decode("cp1252")
    except UnicodeDecodeError:
        return chr(b)


def _mojibake(ch):
    """The literal string a smart character turns into when its UTF-8 bytes are
    mis-decoded as Windows-1252 and re-saved (the classic double-encoding)."""
    return "".join(_cp1252_char(b) for b in ch.encode("utf-8"))


# Corrupted-string -> ASCII replacement, derived from SMART_PUNCT so the two can
# never drift apart.
DOUBLE_ENCODED = {_mojibake(k): v for k, v in SMART_PUNCT.items()}

# Standalone Windows-1252 punctuation bytes (invalid as UTF-8) -> ASCII.
CP1252_BYTES = {
    0x91: "'", 0x92: "'", 0x93: '"', 0x94: '"',
    0x96: "-", 0x97: "-", 0x85: "...", 0xA0: " ", 0x95: "-",
}

# ---------------------------------------------------------------------------
# Pure helpers (unit-tested without the filesystem)
# ---------------------------------------------------------------------------
def scan_text(text):
    """Return a list of issue codes found in an already-decoded string.

    The caller must pass text decoded WITHOUT newline translation (read the
    bytes and decode them, never open() in text mode), or the 'crlf' code can
    never fire: text mode silently rewrites \\r\\n to \\n on the way in, which
    is the same translation that produces the defect on the way out.
    """
    codes = []
    if text.startswith(BOM):
        codes.append("bom")
    if any(k in text for k in DOUBLE_ENCODED):
        codes.append("mojibake")
    if "\r" in text:
        codes.append("crlf")
    return codes


def repair_newlines(data):
    """Normalise raw bytes to LF endings and change nothing else.

    Separate from repair_bytes because the two answer different questions. A
    file whose only fault is its line endings is not corrupted, so it must not
    be put through the full repair, which also folds legitimate smart
    punctuation to ASCII: that would rewrite the content of every CRLF file in
    the project to fix their newlines.
    """
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def repair_text(text):
    """Normalise a valid-UTF-8 string: strip BOM, fix mojibake, fold smart
    punctuation to ASCII, and normalise newlines to LF. Idempotent."""
    if text.startswith(BOM):
        text = text[len(BOM):]
    for bad, good in DOUBLE_ENCODED.items():
        text = text.replace(bad, good)
    for bad, good in SMART_PUNCT.items():
        text = text.replace(bad, good)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def repair_bytes(data):
    """Repair raw file bytes to clean UTF-8.

    Returns (repaired_bytes, note). repaired_bytes is None when the data cannot
    be made valid without guessing (an invalid byte outside the known
    Windows-1252 punctuation set), so the file is reported but left untouched.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        text = data.decode("utf-8")
        return repair_text(text).encode("utf-8"), "utf8"
    except UnicodeDecodeError:
        pass
    # Decode valid UTF-8 normally; invalid bytes become lone surrogates
    # (U+DC00+byte) so legitimate multibyte sequences are preserved untouched.
    text = data.decode("utf-8", errors="surrogateescape")
    out = []
    for ch in text:
        o = ord(ch)
        if 0xDC80 <= o <= 0xDCFF:
            b = o - 0xDC00
            if b in CP1252_BYTES:
                out.append(CP1252_BYTES[b])
            else:
                return None, "unmapped-byte"
        else:
            out.append(ch)
    return repair_text("".join(out)).encode("utf-8"), "recovered"


# ---------------------------------------------------------------------------
# Code-pattern checks (Windows cp1252 decode traps in Python source)
# ---------------------------------------------------------------------------
# The subprocess entry points that decode output, so a missing encoding= is a
# cp1252 decode trap rather than a style note.
_SUBPROCESS_ATTRS = {"run", "Popen", "check_output", "check_call"}

# Call names that take an open()-style mode, mapped to the mode's position in the
# argument list. Builtin open(path, mode) takes it second; Path.open(mode) takes
# it first, because the path is the receiver rather than an argument. Reading the
# wrong position reads the path as the mode, which reports nothing at all.
_OPEN_CALLS = {"open": 1, ".open": 0}

# `.open` is not owned by pathlib, and the sibling functions sharing the name do
# not take a mode first: io/gzip/codecs take (file, mode) like the builtin, and
# os/webbrowser/tarfile take something else entirely. Reading argument 0 as a
# mode there is a misread, not a finding. Matched on the *leftmost plain name* of
# the receiver expression, which covers both `tarfile.open(...)` and
# `zipfile.ZipFile(...).open(...)`; a receiver with no plain name at its root
# (`(base / name).open("w")`) is left checked, because that shape is a real
# Path.open and dropping it would delete the coverage this check exists for.
_NON_PATH_OPEN_ROOTS = {
    "os", "io", "bz2", "dbm", "gzip", "lzma", "wave", "codecs", "shelve",
    "shutil", "socket", "sqlite3", "tarfile", "zipfile", "webbrowser",
}


def _receiver_root(node):
    """The leftmost plain name of a receiver expression, or None.

    Walks down through attribute access and calls, so `zipfile.ZipFile("x")`
    resolves to `zipfile` and `Path(p)` resolves to `Path`. Anything else at the
    root (a subscript, a binary operator, a literal) has no name to judge and
    returns None."""
    while True:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func
        else:
            return None


def _call_name(func):
    """The guard's name for a call, from its callee node, or None to ignore it.

    Names match the ones used in findings: bare `open`, the dotted `.open` /
    `.read_text` / `.write_text` forms, and `subprocess.<entry point>`. Returning
    None is how a call leaves the check entirely, which is the right answer for
    a `.open` on a receiver that is not a path."""
    if isinstance(func, ast.Name):
        return "open" if func.id == "open" else None
    if not isinstance(func, ast.Attribute):
        return None
    attr = func.attr
    if (attr in _SUBPROCESS_ATTRS and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"):
        return "subprocess." + attr
    if attr in ("read_text", "write_text"):
        return "." + attr
    if attr == "open":
        if _receiver_root(func.value) in _NON_PATH_OPEN_ROOTS:
            return None
        return ".open"
    return None


def _node_end_index(node, line_starts, lines):
    """The character index just past ``node`` in the source, or None.

    ``col_offset`` is a UTF-8 *byte* offset, not a character one, so a line
    holding any non-ASCII text before the call would be off by the difference.
    The prefix is re-decoded rather than sliced directly for that reason."""
    lineno = getattr(node, "end_lineno", None)
    col = getattr(node, "end_col_offset", None)
    if lineno is None or col is None or not 1 <= lineno <= len(lines):
        return None
    prefix = lines[lineno - 1].encode("utf-8")[:col].decode("utf-8", "ignore")
    return line_starts[lineno - 1] + len(prefix)


def _call_sites(text, code, tree):
    """``(name, index of the call's opening parenthesis)`` for every checked call.

    Discovery is the half of this check that used to be a regex over call-shaped
    text, and every shape that matched but was not a call became a finding:
    `def open(path)`, `def open(self, mode="w")`, `class open(Base)`. A parse
    tree has no such ambiguity - a definition is not an `ast.Call` - and it also
    hands over the receiver, which is what lets a non-path `.open` be dropped by
    name rather than by guessing from its first argument.

    The opening parenthesis is found by scanning forward from the end of the
    callee through ``code`` (strings and comments blanked, offsets preserved), so
    a parenthesis inside a trailing comment cannot be mistaken for the call's.
    The candidate is then confirmed against the tree: the parenthesis it matches
    must close exactly where the tree says the call ends. Without that check a
    call the tree can see but the blanked view cannot - a call embedded in an
    f-string, which Python only tokenises separately from 3.12 - would silently
    take the offsets of some unrelated later call. A site that fails the check is
    dropped, so a shape the two views disagree about is left unchecked rather
    than checked against the wrong text."""
    lines = text.splitlines(keepends=True)
    line_starts, pos = [], 0
    for line in lines:
        line_starts.append(pos)
        pos += len(line)
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func)
        if name is None:
            continue
        end = _node_end_index(node.func, line_starts, lines)
        call_end = _node_end_index(node, line_starts, lines)
        if end is None or call_end is None:
            continue
        paren = code.find("(", end)
        if paren == -1 or _call_end(code, paren) != call_end:
            continue
        sites.append((name, paren))
    return sites


_COMMENT_TOKENS = {tokenize.COMMENT}
_BLANK_TOKENS = {tokenize.STRING} | _COMMENT_TOKENS
for _name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):
    if hasattr(tokenize, _name):
        _BLANK_TOKENS.add(getattr(tokenize, _name))


def _blank_tokens(text, token_types):
    """``text`` with the given token types blanked to spaces, or None.

    Offsets and total length are preserved, which is what lets two differently
    blanked views of the same source be sliced by one set of indices.

    None means the source could not be tokenised. It used to mean "return the
    text unblanked", which is the worst of the three options: the caller could
    not tell, and every string and comment in the file was then read as code, so
    an unparseable file produced findings out of its own prose. The caller now
    degrades instead."""
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None
    rows = [list(line) for line in text.splitlines(keepends=True)]
    for tok in toks:
        if tok.type not in token_types:
            continue
        (sr, sc), (er, ec) = tok.start, tok.end
        for r in range(sr, er + 1):
            row = rows[r - 1]
            c0 = sc if r == sr else 0
            c1 = ec if r == er else len(row)
            for c in range(c0, min(c1, len(row))):
                if row[c] != "\n":
                    row[c] = " "
    return "".join("".join(r) for r in rows)


def _code_only(text):
    """``text`` with string and comment tokens blanked to spaces, so no argument
    *name* can be read out of a string body or a comment, and so a parenthesis
    inside a comment is never mistaken for a call's. None if it cannot be
    tokenised."""
    return _blank_tokens(text, _BLANK_TOKENS)


def _no_comments(text):
    """``text`` with only comments blanked, so an argument's *value* stays
    readable (a mode literal has to survive) while a comment sitting inside a
    call can never be mistaken for one of its arguments. None if it cannot be
    tokenised."""
    return _blank_tokens(text, _COMMENT_TOKENS)


def _call_end(src, open_paren_idx):
    """Index just past the parenthesis matching the one at ``open_paren_idx``."""
    depth = 0
    for i in range(open_paren_idx, len(src)):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(src)


_KWARG_RE = re.compile(r"^([A-Za-z_]\w*)\s*=(?!=)")
_STR_LITERAL_RE = re.compile(r"^[rbufRBUF]{0,2}('''|\"\"\"|'|\")(.*?)\1$", re.S)


def _call_body(call):
    """The inside of a ``(...)`` call, parentheses removed."""
    return call[1:-1] if call.startswith("(") and call.endswith(")") else call


def _arg_spans(body):
    """The ``(start, end)`` span of every top-level argument in a call body.

    Quote- and bracket-aware, so a comma inside a string or a nested call never
    splits an argument. Spans rather than substrings, because the same offsets
    are used to read an argument's name from one view of the source and its
    value from another."""
    spans, depth, quote, start, i = [], 0, None, 0, 0
    while i < len(body):
        ch = body[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if body.startswith(quote, i):
                i += len(quote)
                quote = None
                continue
        elif ch in "\"'":
            quote = ch * 3 if body.startswith(ch * 3, i) else ch
            i += len(quote)
            continue
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            spans.append((start, i))
            start = i + 1
        i += 1
    spans.append((start, len(body)))
    return spans


class _Call:
    """One parsed call: its keyword arguments by name, its positional argument
    values, and whether a splat leaves either set unprovable."""

    __slots__ = ("kwargs", "positional", "star", "double_star")

    def __init__(self, kwargs, positional, star, double_star):
        self.kwargs = kwargs
        self.positional = positional
        self.star = star
        self.double_star = double_star


def _parse_call(code_call, value_call):
    """Parse one call's arguments from two aligned views of the same source.

    Argument *names* are read from ``code_call`` (strings and comments blanked),
    so nothing inside a string literal or a comment can be read as a keyword.
    Argument *values* are read from ``value_call`` (comments blanked only), so a
    mode literal is still readable. Both views preserve the original offsets,
    which is what lets one set of spans serve both.

    This replaces the substring tests that used to stand in for argument
    presence. ``"newline=" in call`` was satisfied by a string value, by a
    comment, and by a longer keyword such as ``file_encoding=``, and was
    defeated by the spaces in ``newline = "\\n"`` - wrong in both directions,
    inside the write shapes the guard claims to cover."""
    body_code = _call_body(code_call)
    body_value = _call_body(value_call)
    kwargs, positional = {}, []
    star = double_star = False
    for start, end in _arg_spans(body_code):
        frag_code = body_code[start:end]
        frag_value = body_value[start:end]
        lead = len(frag_code) - len(frag_code.lstrip())
        head = frag_code[lead:]
        if not head.strip() and not frag_value.strip():
            continue  # empty span, e.g. a trailing comma
        if head.startswith("**"):
            double_star = True
        elif head.startswith("*"):
            star = True
        else:
            m = _KWARG_RE.match(head)
            if m:
                kwargs[m.group(1)] = frag_value[lead + m.end():].strip()
            else:
                positional.append(frag_value.strip())
    return _Call(kwargs, positional, star, double_star)


def _literal_value(arg):
    """The text inside a simple string literal, or None if it is not one."""
    m = _STR_LITERAL_RE.match(arg.strip())
    return m.group(2) if m else None


def _literal_or_none(arg):
    """Read an argument written as a plain literal, or as ``None``.

    Returns ``("str", <value>)``, ``("none", None)``, or ``(None, None)`` when
    the argument is anything else: a variable, an expression, an f-string, or a
    literal that is not a string. Unreadable stays silent, the same trade
    ``_open_mode`` makes for a computed mode.

    The value is the *evaluated* string rather than the source spelling, which
    matters in both directions. ``"\\x0a"`` is a line feed written unusually and
    must pass; ``r"\\n"`` is a backslash and an 'n', which is not a line feed at
    all and must fail rather than be excused as unreadable. Comparing source text
    would get both backwards.

    Kept separate from ``_literal_value`` deliberately. That one serves the mode
    reader, which wants the characters as typed and for which ``None`` is not a
    mode; folding the two would change how a mode is read in order to answer a
    different question."""
    raw = arg.strip()
    if raw == "None":
        return "none", None
    try:
        value = ast.literal_eval(raw)
    except Exception:
        # literal_eval raises a documented ValueError/SyntaxError for a
        # non-literal, but also propagates whatever the parse hits on a
        # fragment this scanner sliced out of a larger call. Anything it
        # cannot turn into a value is simply unreadable.
        return None, None
    return ("str", value) if isinstance(value, str) else (None, None)


# Every spelling Python's codec lookup resolves to UTF-8. Lookup normalises case
# and treats "-" and "_" as the same character, so these are one codec typed
# several ways rather than several codecs.
_UTF8_ALIASES = {"utf_8", "utf8", "utf", "u8"}


def _encoding_verdict(arg):
    """Whether an ``encoding=`` argument satisfies the rule, as ``ok``, ``bad``,
    or None when the value cannot be read.

    AGENTS.md states this clause as a *value*: every read and write passes
    ``encoding="utf-8"``. Presence alone is not the rule, and unlike the newline
    clause there is no legitimate competing value to protect, so the value is
    pinned. ``encoding=None`` is a violation rather than an omission: it names
    the platform default explicitly, which is cp1252 on Windows."""
    kind, value = _literal_or_none(arg)
    if kind == "none":
        return "bad"
    if kind is None:
        return None
    return "ok" if value.lower().replace("-", "_") in _UTF8_ALIASES else "bad"


# The two newline values the project allows, as evaluated strings: a line feed,
# and the empty string that ``csv`` requires (it asks the writer not to
# translate, leaving the module's own line endings intact).
_ALLOWED_NEWLINES = {"\n", ""}


def _newline_verdict(arg):
    """Whether a ``newline=`` argument satisfies the rule, as ``ok``, ``bad``,
    or None when the value cannot be read.

    Pinned to the allowed set rather than to one value, because ``newline=""``
    is legitimate for ``csv``. ``newline=None`` is the default this clause exists
    to stop: it translates every LF to ``os.linesep`` on the way out, which is
    the CRLF the file check reports."""
    kind, value = _literal_or_none(arg)
    if kind == "none":
        return "bad"
    if kind is None:
        return None
    return "ok" if value in _ALLOWED_NEWLINES else "bad"


# A mode string is short and drawn from this set. Checking the shape (rather than
# accepting any literal) matters for the ``.open`` form, whose first positional
# argument is the mode for ``Path.open`` but is something else entirely for the
# unrelated standard-library calls sharing the name: ``webbrowser.open(url)``,
# ``os.open(path, flags)``, ``tarfile.open(name)``. Read as a mode, the URL
# "http://x" is a write, on the 'x' in it.
_MODE_CHARS = set("rwxab+t")


def _as_mode(arg):
    """``arg`` read as an open()-style mode string, or None if it is not one."""
    value = _literal_value(arg)
    if value is None or not value or len(value) > 3 or set(value) - _MODE_CHARS:
        return None
    return value


def _open_mode(parsed, mode_index=1):
    """The literal mode string of an ``open(...)``-style call.

    ``mode_index`` is the mode's position among the positional arguments; see
    ``_OPEN_CALLS``. Returns ``""`` when no mode is given (Python defaults to
    text read), and ``None`` when a mode is present but is not a literal we can
    read as a mode - a computed mode is left alone rather than guessed at, so an
    unreadable call never produces a false positive. A positional splat gets the
    same treatment: it makes the mode's *position* unknowable, which is no more
    readable than an unknowable value."""
    if "mode" in parsed.kwargs:
        return _as_mode(parsed.kwargs["mode"])
    if parsed.star:
        return None
    if len(parsed.positional) > mode_index:
        return _as_mode(parsed.positional[mode_index])
    return ""


def _is_text_mode_write(name, parsed):
    """True for a call that writes text, where Windows translates LF to CRLF on
    the way out unless ``newline=`` is passed. Reads and binary writes are
    excluded: the newline rule governs what gets written, not what is read."""
    if name == ".write_text":
        return True
    if name not in _OPEN_CALLS:
        return False
    mode = _open_mode(parsed, _OPEN_CALLS[name])
    if mode is None or "b" in mode:
        return False
    return any(c in mode for c in "wax+")


def check_python_code(rel, text):
    """Checks for missing or wrong explicit encodings in a .py source string.

    Calls are discovered on the parse tree (``_call_sites``), so only a real
    call is examined and a definition sharing one of the names is not. Each call
    is then parsed once, its argument names read from a code-only copy
    (strings/comments blanked) and its argument values from a copy with only the
    comments blanked, so a mode string like ``"wb"`` stays readable while neither
    a string body nor a comment can stand in for a real keyword argument. All
    views share the original offsets, because blanking preserves length.

    A file that will not parse or will not tokenise returns a single DEGRADED
    finding and no code findings. Saying "this was not checked" is the honest
    answer and is non-blocking downstream; the previous behaviour was to scan the
    raw text with nothing blanked, which turned an unparseable file's prose into
    WARNs."""
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [("DEGRADED", "encoding",
                 f"{rel}: code checks skipped - does not parse as Python "
                 f"({exc.msg} at line {exc.lineno})")]
    except ValueError as exc:  # e.g. a null byte in the source
        return [("DEGRADED", "encoding",
                 f"{rel}: code checks skipped - does not parse as Python ({exc})")]
    code = _code_only(text)
    values = _no_comments(text)
    if code is None or values is None:
        return [("DEGRADED", "encoding",
                 f"{rel}: code checks skipped - source could not be tokenised")]
    findings = []
    for name, start in _call_sites(text, code, tree):
        end = _call_end(code, start)
        parsed = _parse_call(code[start:end], values[start:end])
        # A splat makes the argument list unprovable, so the call is not checked.
        # ``**opts`` can carry either keyword; ``*parts`` can supply either one
        # positionally, since both ``open`` and ``write_text`` take ``encoding``
        # by position too. Left silent and recorded as an unsupported shape
        # rather than reported either way - the same choice the mode reader makes
        # for a mode it cannot read, and honest about the boundary instead of
        # claiming a call is clean that was never actually checked.
        if parsed.star or parsed.double_star:
            continue
        if "encoding" not in parsed.kwargs:
            if name.startswith("subprocess"):
                if (parsed.kwargs.get("text") == "True"
                        or parsed.kwargs.get("universal_newlines") == "True"):
                    findings.append((
                        "WARN", "encoding",
                        f"{rel}: text-mode {name}(...) with no explicit encoding= "
                        f"(decodes as cp1252 on Windows)",
                    ))
            elif name in _OPEN_CALLS:
                mode = _open_mode(parsed, _OPEN_CALLS[name])
                # A binary handle has no encoding, so it is not a finding. An
                # unreadable mode on a `.open(...)` whose receiver named no
                # known non-path module may still not be a file open at all
                # (see _MODE_CHARS and _NON_PATH_OPEN_ROOTS - the receiver rule
                # catches the modules, not a local of any other type), so that
                # stays silent too; builtin open() is unambiguous and is still
                # reported.
                binary = mode is not None and "b" in mode
                ambiguous = mode is None and name != "open"
                if not binary and not ambiguous:
                    findings.append((
                        "INFO", "encoding",
                        f"{rel}: {name}(...) with no explicit encoding=",
                    ))
            else:  # .read_text / .write_text
                findings.append((
                    "INFO", "encoding",
                    f"{rel}:{name}(...) with no explicit encoding=",
                ))
        elif _encoding_verdict(parsed.kwargs["encoding"]) == "bad":
            # Present but wrong. Until 2026-08-05 the clause above was the whole
            # check, so encoding=None and encoding="cp1252" were read as
            # compliant while AGENTS.md states the rule as a value. An
            # unreadable value stays silent rather than guessed at.
            findings.append((
                "WARN", "encoding",
                f"{rel}: {name}(...) with encoding="
                f"{parsed.kwargs['encoding']}, not utf-8",
            ))
        # The newline rule is a second, independent clause of the same project
        # rule, so it is checked separately. Folding it into the encoding branch
        # above is the defect this structure exists to prevent: a call that
        # pins encoding= but not newline= would return early and be read as
        # clean, which is exactly what happened before 2026-08-04.
        if _is_text_mode_write(name, parsed):
            if "newline" not in parsed.kwargs:
                findings.append((
                    "WARN", "encoding",
                    f"{rel}: text-mode {name}(...) with no explicit newline= "
                    f"(Windows text mode writes CRLF)",
                ))
            elif _newline_verdict(parsed.kwargs["newline"]) == "bad":
                findings.append((
                    "WARN", "encoding",
                    f"{rel}: text-mode {name}(...) with newline="
                    f"{parsed.kwargs['newline']}, not \"\\n\" (or \"\" for csv)",
                ))
    return findings


# ---------------------------------------------------------------------------
# Walk + classify
# ---------------------------------------------------------------------------
def iter_text_files(root):
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        if set(rel.parts) & EXEMPT_PATH_PARTS:
            dirnames[:] = []
            continue
        # Authored hidden config dirs (.codex, .github, .windsurf, .clinerules,
        # .continue, .cursor, .gemini, .claude, and any future AI dir) hold
        # tracked project prose, so they are scanned. Only system/tooling
        # dot-dirs (SKIP_DIRS) and the verbatim-data exemptions are pruned, so
        # coverage is never silently lost for a hidden directory.
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS
            and d not in EXEMPT_DIR_NAMES
        ]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext in TEXT_EXTS or fn in TEXT_NAMES:
                yield Path(dirpath) / fn


def classify_file(root, path):
    rel = path.relative_to(root).as_posix()
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [("INFO", "encoding", f"{rel}: could not read ({exc})")]
    findings = []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return [("FAIL", "encoding", f"{rel}: not valid UTF-8 ({exc})")]
    for code in scan_text(text):
        if code == "bom":
            findings.append(("WARN", "encoding", f"{rel}: unexpected UTF-8 BOM"))
        elif code == "mojibake":
            findings.append((
                "WARN", "encoding",
                f"{rel}: mojibake (double-encoded Windows-1252 punctuation)",
            ))
        elif code == "crlf":
            findings.append((
                "WARN", "encoding",
                f"{rel}: CR line endings (project policy is LF; fix with --fix)",
            ))
    if path.suffix.lower() == ".py":
        findings.extend(check_python_code(rel, text))
    return findings


def run_check(root):
    findings = []
    for path in iter_text_files(root):
        findings.extend(classify_file(root, path))
    return findings


# ---------------------------------------------------------------------------
# Fix
# ---------------------------------------------------------------------------
def problem_codes(data):
    """Issue codes for raw file bytes: ``["invalid-utf8"]`` when the bytes do
    not decode, otherwise whatever scan_text finds.

    Returns the codes rather than a bool because --fix has to know *which*
    problem a file has: a CRLF-only file gets its newlines normalised and
    nothing else, while a corrupted one goes through the full repair."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return ["invalid-utf8"]
    return scan_text(text)


def _has_encoding_problem(data):
    """True only if the bytes are invalid UTF-8 or carry mojibake, an
    unexpected BOM, or CR line endings. A clean file whose only 'fancy' content
    is legitimate Unicode (e.g. a real em dash) is NOT a problem, so --fix never
    mass-folds the whole project to ASCII; it only repairs genuinely damaged
    files."""
    return bool(problem_codes(data))


def run_fix(root, dry_run, preserve_mtime=False):
    """Repair only the files with a real problem (invalid UTF-8, mojibake, an
    unexpected BOM, or CR line endings). Code-pattern findings are advisory and
    are never auto-edited.

    A file whose ONLY fault is its line endings gets newline normalisation and
    nothing else. A genuinely corrupted file is fully normalised, which includes
    folding its legitimate smart punctuation to ASCII - correct for a damaged
    file, and quite wrong to apply to a clean one that merely has CRLF.

    ``preserve_mtime`` restores each repaired file's original modification time.
    Line endings are not content, and doc-sync-guard reads LOG.md mtime as its
    evidence that a directory recorded its change: a normalisation pass that
    touched every LOG.md would make every directory look freshly logged and
    mask real drift. Returns (changed, skipped)."""
    changed, skipped = [], []
    for path in iter_text_files(root):
        rel = path.relative_to(root).as_posix()
        original = path.read_bytes()
        codes = problem_codes(original)
        if not codes:
            continue
        if codes == ["crlf"]:
            repaired, note = repair_newlines(original), "newlines"
        else:
            repaired, note = repair_bytes(original)
        if repaired is None:
            skipped.append((rel, note))
            continue
        if repaired == original:
            continue
        if dry_run:
            print(f"[DRY RUN] would repair {rel} ({note})")
        else:
            stat = path.stat() if preserve_mtime else None
            with open(path, "wb") as fh:
                fh.write(repaired)
            if stat is not None:
                os.utime(path, (stat.st_atime, stat.st_mtime))
            print(f"repaired {rel} ({note})")
        changed.append(rel)
    return changed, skipped


# ---------------------------------------------------------------------------
# Output + logging
# ---------------------------------------------------------------------------
def print_report(findings):
    counts = {"FAIL": 0, "WARN": 0, "DEGRADED": 0, "INFO": 0}
    for sev, _, _ in findings:
        counts[sev] = counts.get(sev, 0) + 1
    print("# Encoding Guard Report\n")
    print(f"**Failures:** {counts['FAIL']}  **Warnings:** {counts['WARN']}  "
          f"**Degraded:** {counts['DEGRADED']}  **Info:** {counts['INFO']}\n")
    if not findings:
        print("No encoding problems found.")
        return
    # DEGRADED sits above INFO because it means a file was not checked at all,
    # which a reader needs to see before an advisory note about one that was.
    for sev in ("FAIL", "WARN", "DEGRADED", "INFO"):
        group = [f for f in findings if f[0] == sev]
        if not group:
            continue
        print(f"## {sev}")
        for _, _, msg in sorted(group, key=lambda f: f[2]):
            print(f"- {msg}")
        print()


def findings_json(findings):
    return json.dumps({
        "findings": [
            {"severity": s, "label": lab, "message": m} for s, lab, m in findings
        ]
    }, ensure_ascii=False)


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _log(path, action, note):
    entry = f"[{_now()}] | Actor: Biblio | Action: {action} | Note: {note}"
    sep = "" if (not path.exists() or path.read_text(encoding="utf-8").endswith("\n")) else "\n"
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(sep + entry + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Encoding hygiene check and fix")
    parser.add_argument("--check", action="store_true",
                        help="Read-only scan (default if no mode given)")
    parser.add_argument("--fix", action="store_true",
                        help="Repair flagged files in place")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --fix, print repairs without writing")
    parser.add_argument("--json", action="store_true",
                        help="Emit findings as JSON on stdout (check mode)")
    parser.add_argument("--preserve-mtime", action="store_true",
                        help="With --fix, keep each repaired file's modification "
                             "time (for bulk newline passes, so mtime-based "
                             "guards are not fooled)")
    args = parser.parse_args()

    if args.fix:
        # --dry-run writes nothing at all, not even a log entry.
        if not args.dry_run:
            _log(WORKFLOW_LOG, "started", "Encoding fix run started.")
        try:
            changed, skipped = run_fix(PROJECT_ROOT, args.dry_run,
                                       args.preserve_mtime)
        except Exception as exc:
            if not args.dry_run:
                _log(WORKFLOW_LOG, "failed", f"Encoding fix failed: {exc}")
            raise
        verb = "would repair" if args.dry_run else "repaired"
        print(f"\n{verb} {len(changed)} file(s); {len(skipped)} skipped "
              f"(unmappable).")
        for rel, note in skipped:
            print(f"  SKIPPED {rel} ({note})")
        if not args.dry_run:
            note = (f"Encoding fix completed - {len(changed)} repaired, "
                    f"{len(skipped)} skipped.")
            _log(WORKFLOW_LOG, "completed", note)
            _log(ROOT_LOG, "completed", note)
        return

    findings = run_check(PROJECT_ROOT)
    if args.json:
        print(findings_json(findings))
    else:
        print_report(findings)
    if any(s == "FAIL" for s, _, _ in findings):
        sys.exit(1)


if __name__ == "__main__":
    main()
