#!/usr/bin/env python3
"""
brief.py - the review-orchestration brief checker (Stage A1).

A run starts from a brief: a markdown file with seven required H2 sections, written
with the user before the run. This script refuses a brief that is not ready: a
missing, repeated, misspelt, extra or out-of-order heading, an empty section, an
unmeasured edit path, an unanswered open question, an acceptance check that is not a
single command, or a Source path that does not exist. It is the run's first
preflight step. It never runs a command from the brief.

Every problem is reported, one line each, as ``FAIL: <heading>: <reason>``, with
``Brief`` (the file itself) and ``Headings`` lines first and the section lines after
them in document order. A brief with no problem prints ``PASS: <path>``. Exit code 0
on PASS, 1 on any FAIL, 2 on a usage error.

The rules, and every choice made where the specification is silent, are recorded in
``workflows/review-orchestration/scripts/CONTEXT.md``. Each one has a test in
``workflows/review-orchestration/tests/test_brief.py``.

Usage:
  python workflows/review-orchestration/scripts/brief.py --check <brief>
  python workflows/review-orchestration/scripts/brief.py --check <brief> --dry-run
"""

import argparse
import os
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_WORKFLOW_DIR = _SCRIPTS_DIR.parent
PROJECT_ROOT = _WORKFLOW_DIR.parent.parent
LOG_PATH = _WORKFLOW_DIR / "LOG.md"

HEADINGS = (
    "Goal",
    "Constraints",
    "Out of scope",
    "Acceptance checks",
    "Edit paths",
    "Open questions",
    "Source",
)
_ACCEPTANCE, _EDIT, _OPEN, _SOURCE = HEADINGS[3], HEADINGS[4], HEADINGS[5], HEADINGS[6]

# Line shapes, per CommonMark. Every pattern is applied to the visible text of a line,
# after HTML comments have been removed.
_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_FENCE_CLOSE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_BLOCK_START = re.compile(r"^[ \t]*(?:[-*+]|\d{1,9}[.)])(?:[ \t]|$)|^[ \t]*>")
_INDENTED_CODE = re.compile(r"^(?: {4}|\t)")

_PLACEHOLDER = re.compile(r"^(?:[-*+][ \t]+)?(?:\.\.\.|<[^<>]+>)$")
_PLACEHOLDER_WORDS = ("TODO", "TBD")
_BULLET = re.compile(r"^-(?:[ \t]+(.*))?$")
_MEASURE_SPLIT = re.compile(r"[ \t]*\|[ \t]*Measure:[ \t]*")
_ANSWER_SPLIT = re.compile(r"[ \t]*\|[ \t]*Answer:[ \t]*")
_DRIVE = re.compile(r"^[A-Za-z]:")

_OPERATOR_CHARS = ";&|<>"
_SH_WRAPPERS = {"sh", "bash", "dash", "zsh", "ksh", "fish"}
_SH_FLAG = re.compile(r"-[A-Za-z]*c[A-Za-z]*")
# Long options that run a string. fish also runs one through -C / --init-command,
# where for the other shells -C is noclobber.
_SH_COMMAND_OPTIONS = ("--command",)
_FISH_COMMAND_OPTIONS = ("--command", "-C", "--init-command")
# Options that take the next argument as their value, which is not the script operand.
_SH_VALUED_OPTIONS = {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}
_LAUNCHERS = {"env", "sudo", "doas", "xargs", "nohup", "nice", "timeout", "time",
              "command", "exec", "stdbuf", "runas", "start"}
_PS_WRAPPERS = {"powershell", "pwsh"}
_PS_PARAM = re.compile(r"(?:--?|/)([A-Za-z]+)(?::.*)?")
_PS_COMMAND_PARAMS = ("command", "encodedcommand", "commandwithargs")
_PS_COMMAND_ALIASES = {"ec", "cwa"}


# --------------------------------------------------------------------------- reading


def _strip_comments(line, in_comment):
    """Remove HTML comments from one line. Returns (visible text, still in comment)."""
    parts = []
    pos = 0
    while True:
        if in_comment:
            end = line.find("-->", pos)
            if end < 0:
                return "".join(parts), True
            pos = end + 3
            in_comment = False
        else:
            start = line.find("<!--", pos)
            if start < 0:
                parts.append(line[pos:])
                return "".join(parts), False
            parts.append(line[pos:start])
            pos = start + 4
            in_comment = True


class _Line:
    """One physical line, classified.

    kind is one of: blank, text, fence_open, fence, h1, h2, h3.
    For a heading, ``text`` is its title; otherwise the visible text of the line.
    ``setext`` marks a heading written with an underline rather than ``#``.
    """

    __slots__ = ("no", "kind", "text", "setext")

    def __init__(self, no, kind, text="", setext=False):
        self.no = no
        self.kind = kind
        self.text = text
        self.setext = setext


def scan(text):
    """Classify every line of a brief. Fenced blocks and comments hide headings."""
    lines = []
    in_comment = False
    fence = None
    paragraph = False  # the previous line could be turned into a setext heading
    physical = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for no, raw in enumerate(physical, 1):
        if fence is not None:
            match = _FENCE_CLOSE.match(raw)
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= fence[1]:
                fence = None
            lines.append(_Line(no, "fence", raw))
            paragraph = False
            continue
        visible, in_comment = _strip_comments(raw, in_comment)
        if not visible.strip():
            lines.append(_Line(no, "blank"))
            paragraph = False
            continue
        match = _FENCE_OPEN.match(visible)
        if match and not (match.group(1)[0] == "`" and "`" in match.group(2)):
            fence = (match.group(1)[0], len(match.group(1)))
            lines.append(_Line(no, "fence_open", visible))
            paragraph = False
            continue
        match = _ATX.match(visible)
        if match:
            level = len(match.group(1))
            kind = "h1" if level == 1 else "h2" if level == 2 else "h3"
            lines.append(_Line(no, kind, (match.group(2) or "").strip()))
            paragraph = False
            continue
        match = _SETEXT.match(visible)
        if match and paragraph:
            previous = lines[-1]
            previous.kind = "h1" if match.group(1)[0] == "=" else "h2"
            previous.text = previous.text.strip()
            previous.setext = True
            lines.append(_Line(no, "blank"))
            paragraph = False
            continue
        lines.append(_Line(no, "text", visible))
        # An indented line that does not continue a paragraph is code, which an
        # underline cannot turn into a heading; a list item or quote cannot be either.
        starts_paragraph = paragraph or not _INDENTED_CODE.match(visible)
        paragraph = starts_paragraph and not _BLOCK_START.match(visible)
    return lines


def _read(arg, root):
    """Read the brief named on the command line.

    Returns (text or None, problems). ``text`` is None when the file cannot be
    checked at all.
    """
    problems = []
    spelled = arg.replace("\\", "/")
    if not spelled.strip():
        return None, ["no brief path given"]
    if os.path.isabs(arg) or _DRIVE.match(spelled) or spelled.startswith("/"):
        return None, [f"`{arg}` is not a project-relative path"]
    root = Path(root).resolve()
    target = (root / spelled).resolve()
    if target != root and root not in target.parents:
        return None, [f"`{arg}` resolves outside the project"]
    if target.is_dir():
        return None, [f"`{arg}` is a folder, not a file"]
    if not target.is_file():
        return None, [f"`{arg}` does not exist"]
    try:
        data = target.read_bytes()
    except OSError as exc:
        return None, [f"`{arg}` cannot be read ({exc.strerror or exc})"]
    if data.startswith(b"\xef\xbb\xbf"):
        problems.append("starts with a byte-order mark")
        data = data[3:]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        problems.append(f"not valid UTF-8 (byte {exc.start})")
        return None, problems
    return text, problems


# ------------------------------------------------------------------ one command


def _unquoted_operators(command):
    """Scan with POSIX quoting. Returns (operators outside quotes, unbalanced)."""
    found = []
    pos = 0
    length = len(command)
    while pos < length:
        char = command[pos]
        if char == "\\":
            if pos + 1 >= length:
                return found, True
            pos += 2
            continue
        if char == "'":
            end = command.find("'", pos + 1)
            if end < 0:
                return found, True
            pos = end + 1
            continue
        if char == '"':
            pos += 1
            while pos < length and command[pos] != '"':
                pos += 2 if command[pos] == "\\" else 1
            if pos >= length:
                return found, True
            pos += 1
            continue
        if char in _OPERATOR_CHARS:
            end = pos
            while end < length and command[end] in _OPERATOR_CHARS:
                end += 1
            operator = command[pos:end]
            if operator not in found:
                found.append(operator)
            pos = end
            continue
        pos += 1
    return found, False


def _program(arg):
    name = re.split(r"[\\/]", arg)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def _ps_name(arg):
    match = _PS_PARAM.fullmatch(arg)
    return match.group(1).lower() if match else None


def _shell_wrapper(arg, program, rest):
    """A POSIX shell runs a command string only through an option before its script."""
    options = _FISH_COMMAND_OPTIONS if program == "fish" else _SH_COMMAND_OPTIONS
    skip = False
    for flag in rest:
        if skip:
            skip = False
            continue
        if flag == "--" or not flag.startswith(("-", "+")) or flag in ("-", "+"):
            return None  # the script operand: everything after it is the script's
        if (_SH_FLAG.fullmatch(flag) or flag in options
                or flag.startswith(tuple(option + "=" for option in options))):
            return f"`{arg}` with `{flag}` runs a command string"
        skip = flag in _SH_VALUED_OPTIONS
    return None


def _cmd_wrapper(arg, rest):
    for flag in rest:
        if flag.lower().startswith(("/c", "/k")):
            return f"`{arg}` with `{flag}` runs a command string"
    return None


def _ps_wrapper(arg, program, rest):
    names = []
    for flag in rest:
        name = _ps_name(flag)
        if name and "file".startswith(name):
            return None  # everything after -File belongs to the script
        if name and (name in _PS_COMMAND_ALIASES
                     or any(full.startswith(name) for full in _PS_COMMAND_PARAMS)):
            return f"`{arg}` with `{flag}` runs a command string"
        names.append(name)
    positional = [flag for flag, name in zip(rest, names) if name is None]
    if program == "powershell" and positional:
        return (f"`{arg}` treats the bare argument `{positional[0]}` as a command "
                "string; use `-File`")
    return None


def _wrapper(argv):
    """Name a shell wrapper or launcher in argv, or return None.

    The program is argv[0], and only argv[0] is examined. A launcher that runs
    another program (`env`, `sudo`, `xargs` and the like) is refused outright
    rather than read: finding the program it runs means modelling each launcher's
    options, and `env -S` hides a whole command inside one argument. A brief can
    always name the program directly.
    """
    if not argv:
        return None
    arg = argv[0]
    program = _program(arg)
    rest = argv[1:]
    if program in _LAUNCHERS:
        return f"`{arg}` launches another program; name that program directly"
    if program in _SH_WRAPPERS:
        return _shell_wrapper(arg, program, rest)
    if program == "cmd":
        return _cmd_wrapper(arg, rest)
    if program in _PS_WRAPPERS:
        return _ps_wrapper(arg, program, rest)
    return None


def _split_keeping_backslashes(command):
    lexer = shlex.shlex(command, posix=True)
    lexer.whitespace_split = True
    lexer.escape = ""
    try:
        return list(lexer)
    except ValueError:
        return []


def check_command(command):
    """Reasons the text is not a single command. An empty list means it is one."""
    if not command.strip():
        return ["empty command"]
    reasons = []
    if "\n" in command or "\r" in command:
        reasons.append("contains a line break")
    if "$(" in command or "`" in command:
        reasons.append("contains command substitution (`$(` or a backtick)")
    operators, unbalanced = _unquoted_operators(command)
    if unbalanced:
        reasons.append("has an unbalanced quote or a trailing backslash")
        return reasons
    for operator in operators:
        reasons.append(f"contains the unquoted shell operator `{operator}`")
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        reasons.append("has an unbalanced quote or a trailing backslash")
        return reasons
    # POSIX reading turns an unquoted `C:\Git\bin\bash.exe` into `C:Gitbinbash.exe`,
    # so the command is read a second time with quotes honoured but backslashes kept,
    # and either reading naming a wrapper is enough to refuse.
    wrapper = _wrapper(argv) or _wrapper(_split_keeping_backslashes(command))
    if wrapper:
        reasons.append(wrapper)
    return reasons


# ------------------------------------------------------------------ paths


def _components(path):
    return path[:-1].split("/") if path.endswith("/") else path.split("/")


def _folded(component):
    """How Windows compares a name: without case, ignoring trailing dots and spaces."""
    return component.rstrip(". ").lower()


def check_edit_path(path):
    """Reasons an Edit paths entry is refused. Existence is not required."""
    if not path:
        return ["empty path"]
    reasons = []
    if "\\" in path:
        reasons.append(f"`{path}` uses a backslash; use forward slashes")
    if _DRIVE.match(path) or path.startswith("/"):
        reasons.append(f"`{path}` is not project-relative")
    if any(char in path for char in "*?[]"):
        reasons.append(f"`{path}` contains a wildcard character")
    parts = _components(path)
    if ".." in parts:
        reasons.append(f"`{path}` contains `..`")
    if not path.startswith("/") and ("" in parts or "." in parts):
        reasons.append(f"`{path}` has an empty or `.` component")
    folded = [_folded(part) for part in parts]
    if ".git" in folded:
        reasons.append(f"`{path}` is under `.git/`")
    if any(part == ".env" or part.startswith(".env.") for part in folded):
        reasons.append(f"`{path}` is a `.env` file")
    return reasons


def _edit_path_key(path):
    return "/".join(_folded(part) for part in _components(path))


def _exists_exactly(root, parts, want_dir):
    """True when every component exists with exactly this spelling, case included."""
    current = Path(root)
    for part in parts:
        try:
            names = os.listdir(current)
        except OSError:
            return False
        if part not in names:
            return False
        current = current / part
    return current.is_dir() if want_dir else current.exists()


def _check_source_token(token, root):
    if "\\" in token:
        return f"`{token}` uses a backslash; use forward slashes"
    if _DRIVE.match(token) or token.startswith(("/", "~")):
        return f"`{token}` is not project-relative"
    parts = [part for part in _components(token) if part not in ("", ".")]
    if ".." in parts:
        return f"`{token}` contains `..`"
    if not parts:
        return f"`{token}` names no file or folder"
    if not _exists_exactly(root, parts, want_dir=token.endswith("/")):
        return f"`{token}` does not exist in the project (spelling and case must match)"
    return None


def _looks_like_path(word):
    return "/" in word or word.lower().endswith(".md")


def check_source_line(content, root):
    """Reasons a Source bullet is refused."""
    reasons = []
    segments = content.split("`")
    balanced = len(segments) % 2 == 1
    if not balanced:
        reasons.append("has an unbalanced backtick")
    for index, segment in enumerate(segments):
        quoted = index % 2 == 1 and (balanced or index < len(segments) - 1)
        if quoted:
            token = segment.strip()
            if _looks_like_path(token):
                problem = _check_source_token(token, root)
                if problem:
                    reasons.append(problem)
            continue
        for word in segment.split():
            if _looks_like_path(word.strip("()[]{}<>\"',.;:!?")):
                reasons.append(f"`{word}` looks like a path; put it in backticks")
    return reasons


# ------------------------------------------------------------------ sections


def _is_placeholder(text):
    if _PLACEHOLDER.match(text):
        return True
    unbulleted = re.sub(r"^[-*+][ \t]+", "", text)
    return unbulleted.startswith(_PLACEHOLDER_WORDS)


def _bullets(content, problems, template):
    """Yield (line, bullet text) for each line, refusing any line that is not one."""
    for no, text in content:
        match = _BULLET.match(text)
        if not match:
            problems.append((no, f"line {no}: not a bullet written `{template}`"))
            continue
        yield no, (match.group(1) or "").strip()


def _check_section(name, heading_no, body, root, problems, seen_paths):
    content = []
    for line in body:
        if line.kind == "fence_open":
            problems.append((line.no, f"line {line.no}: a fenced code block is not allowed"))
        elif line.kind == "text":
            text = line.text.strip()
            if not _is_placeholder(text):
                content.append((line.no, text))

    if name == _OPEN:
        if not content or (len(content) == 1 and content[0][1] == "None"):
            return
        for no, item in _bullets(content, problems, "- <question> | Answer: <answer>"):
            parts = _ANSWER_SPLIT.split(item, maxsplit=1)
            if not parts[0]:
                problems.append((no, f"line {no}: empty question"))
            if len(parts) < 2 or not parts[1].strip():
                problems.append((no, f"line {no}: question has no answer"))
        return

    if not content:
        problems.append((heading_no, "section is empty"))
        return

    if name == _ACCEPTANCE:
        for no, command in _bullets(content, problems, "- <command>"):
            for reason in check_command(command):
                problems.append((no, f"line {no}: {reason}"))
    elif name == _EDIT:
        for no, item in _bullets(content, problems, "- <path> | Measure: <command>"):
            parts = _MEASURE_SPLIT.split(item, maxsplit=1)
            path = parts[0].strip()
            for reason in check_edit_path(path):
                problems.append((no, f"line {no}: {reason}"))
            if path:
                key = _edit_path_key(path)
                if key in seen_paths:
                    problems.append((no, f"line {no}: `{path}` duplicates line {seen_paths[key]}"))
                else:
                    seen_paths[key] = no
            if len(parts) < 2:
                problems.append((no, f"line {no}: no measuring command (`| Measure: <command>`)"))
                continue
            for reason in check_command(parts[1].strip()):
                problems.append((no, f"line {no}: measuring command {reason}"))
    elif name == _SOURCE:
        for no, item in _bullets(content, problems, "- <source>"):
            if not item:
                problems.append((no, f"line {no}: empty bullet"))
            for reason in check_source_line(item, root):
                problems.append((no, f"line {no}: {reason}"))


def check_text(text, root):
    """Check a brief's text. Returns (heading problems, section problems) as lines."""
    lines = scan(text)
    heading_problems = []
    sections = {}
    order = []
    current = None
    started = False
    highest = -1
    for line in lines:
        if line.kind == "h2" and not line.setext:
            started = True
            title = line.text
            current = None
            if title not in HEADINGS:
                hint = next((h for h in HEADINGS if h.lower() == title.lower()), None)
                suffix = f" (expected `## {hint}`)" if hint else ""
                heading_problems.append(
                    (line.no, f"line {line.no}: unexpected heading `## {title}`{suffix}"))
            elif title in sections:
                heading_problems.append(
                    (line.no, f"line {line.no}: `## {title}` appears more than once"))
            else:
                index = HEADINGS.index(title)
                if index < highest:
                    heading_problems.append(
                        (line.no, f"line {line.no}: `## {title}` is out of order "
                                  f"(it must come before `## {HEADINGS[highest]}`)"))
                highest = max(highest, index)
                sections[title] = (line.no, [])
                order.append(title)
                current = title
            continue
        if line.setext and line.kind == "h2":
            heading_problems.append(
                (line.no, f"line {line.no}: underlined heading `{line.text}`; "
                          "write headings with `## `"))
            continue
        if line.kind == "h1" and started:
            heading_problems.append(
                (line.no, f"line {line.no}: level-1 heading `{line.text}` after the first "
                          "section"))
            continue
        if current is not None:
            sections[current][1].append(line)

    heading_problems.sort(key=lambda item: item[0])
    for title in HEADINGS:
        if title not in sections:
            heading_problems.append((None, f"missing `## {title}`"))

    section_problems = []
    seen_paths = {}
    for title in order:
        heading_no, body = sections[title]
        found = []
        _check_section(title, heading_no, body, root, found, seen_paths)
        found.sort(key=lambda item: item[0])
        section_problems.extend((title, reason) for _, reason in found)
    return [reason for _, reason in heading_problems], section_problems


def check_brief(arg, root=PROJECT_ROOT):
    """Check the brief at a project-relative path. Returns the output lines and a code."""
    text, brief_problems = _read(arg, root)
    lines = [f"FAIL: Brief: {reason}" for reason in brief_problems]
    if text is not None:
        heading_problems, section_problems = check_text(text, root)
        lines += [f"FAIL: Headings: {reason}" for reason in heading_problems]
        lines += [f"FAIL: {title}: {reason}" for title, reason in section_problems]
    if lines:
        return lines, 1
    return [f"PASS: {arg}"], 0


# ------------------------------------------------------------------ command line


def _timestamp():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _log(action, note, log_path, dry_run):
    """Append one LOG.md entry. Returns the reason it could not be written, or None."""
    entry = f"[{_timestamp()}] | Actor: Biblio | Action: {action} | Note: {note}"
    if dry_run:
        print(f"[DRY RUN] would append to LOG.md: {entry}", file=sys.stderr)
        return None
    try:
        with open(log_path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(entry + "\n")
    except OSError as exc:
        return exc.strerror or str(exc)
    return None


def run_check(arg, *, root=PROJECT_ROOT, log_path=LOG_PATH, dry_run=False):
    """Check one brief from the command line: print the result, log it, return the code.

    A check whose LOG.md entries cannot be written does not pass: the audit trail is
    part of the check, so the result is a FAIL line and exit code 1.
    """
    log_error = _log("started", "Brief check started.", log_path, dry_run)
    try:
        lines, code = check_brief(arg, root)
    except Exception as exc:
        _log("failed", f"Brief check stopped on an unexpected error ({type(exc).__name__}).",
             log_path, dry_run)
        raise
    if code == 0 and log_error is None:
        log_error = _log("completed", "Brief check passed.", log_path, dry_run)
    elif code != 0:
        log_error = _log("failed", f"Brief check refused the brief ({len(lines)} problem(s)).",
                         log_path, dry_run) or log_error
    if log_error is not None:
        lines = [line for line in lines if not line.startswith("PASS: ")]
        lines.insert(0, f"FAIL: Brief: could not append to the workflow LOG.md ({log_error})")
        code = 1
    for line in lines:
        print(line)
    return code


def _reconfigure_streams():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def main(argv=None, *, root=PROJECT_ROOT, log_path=LOG_PATH):
    _reconfigure_streams()
    parser = argparse.ArgumentParser(
        prog="brief.py", description="Check a review-orchestration run brief.")
    parser.add_argument("--check", required=True, metavar="BRIEF",
                        help="project-relative path of the brief to check")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the LOG.md entries to stderr instead of writing them")
    args = parser.parse_args(argv)
    return run_check(args.check, root=root, log_path=log_path, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
