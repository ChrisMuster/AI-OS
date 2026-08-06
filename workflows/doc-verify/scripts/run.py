#!/usr/bin/env python3
"""Doc-verify - structural self-consistency checks for one long markdown document.

Read-only. The target document is an argument, never embedded, so this script
carries no personal path and can be pointed at a tracked or a gitignored file:

    python workflows/doc-verify/scripts/run.py --check <file> [<file> ...]

Why it exists. A long design document accumulates internal claims about itself -
how many tables it has, that its enumerated rows are contiguous, that a reference
points where it says, that a code citation resolves. Verifying those by writing a
fresh throwaway script each review round means each runner re-implements the
definition, and a wrong definition produces a confident wrong number. Book Dragon's
rule (AGENTS.md, and the throwaway-script trap it comes from) is that a script whose
output is quoted belongs in the repo with tests. This is that script.

The checks here are the ones no existing guard covers for an arbitrary file.
`encoding-guard` covers UTF-8 validity, mojibake and BOMs across the whole tree
(including gitignored files) and is not duplicated here. It does **not** report CR
bytes, tabs, trailing whitespace, or a missing final newline, and `ai-style-guard`
is scoped to `git diff` content, so neither one can speak for a gitignored file's
line endings or typography. That gap is what the `hygiene` check closes.

    hygiene     Measured in BINARY mode: 0 CR bytes, 0 tabs, 0 trailing-whitespace
                lines, 0 codepoints above U+007F, and a final newline. Binary
                matters: a text-mode read on Windows translates CRLF to LF and
                hides every CR byte from the check meant to find them.
    tables      Every markdown table's header and body rows have the separator
                row's cell count.
    sequences   A table whose first column enumerates (1, 2, 3 / E10, E11 /
                13, 13a) is contiguous, free of duplicate tokens, and ascending,
                counted within the table that owns it; and numbered headings
                (## 4., ### 9.2) ascend with no gaps or repeats within their
                parent. Both halves check order because neither gaps nor
                duplicates can see it: 1, 3, 2 has neither, so a row or a
                subsection inserted in the wrong place is caught rather than
                read past.
    distance    Candidate references by distance ("two bullets down") and
                ordinals into a growing list ("the fifth condition"), which rot
                when anything is inserted.
    citations   Every `path:line` / `path:line-line` reference resolves: the file
                exists and the range is inside it. Path-less shorthand (`:265`)
                is reported as unresolvable by form.

Severities:
    FAIL   a definite structural defect: a ragged table row, a sequence gap,
           duplicate or out-of-order member, a citation whose file is missing or
           whose range is outside the file.
    WARN   a shorthand citation, which cannot be mechanically resolved at all.
    INFO   a distance-reference candidate. Adjudication needs judgement (a claim
           *about* distance is content and stays), so this tier is a worklist for
           a human or an AI, never a verdict.

Exit codes: 0 by default. With --strict, 1 when any FAIL or WARN exists, so a
caller can gate on it. INFO never gates. --json emits the findings payload.

Scope, stated so it can be disagreed with: fenced code blocks are excluded from
every check (a fence holds quoted or illustrative material, not the document's own
claims), and a table is recognised only by a separator row that carries a leading
pipe, which is the form this project's documents use.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# UTF-8 stdout/stderr so the report never mojibakes when piped or redirected on
# Windows (where the console default is cp1252).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent

LABEL = "doc-verify"

CHECK_NAMES = ("hygiene", "tables", "sequences", "distance", "citations")


def physical_line_count(text):
    """Number of physical lines: every newline-terminated line, plus a trailing
    unterminated remainder if there is one. An empty document has none.

    ``len(text.split("\\n"))`` is the obvious spelling and it is wrong by one on
    any file that ends with a newline, because the split leaves an empty final
    element. Since the hygiene check *requires* a final newline, that spelling is
    wrong for every valid document this script will ever be pointed at.

    It lives here as one function because the script previously held two
    disagreeing definitions of the same quantity: the citation resolver counted
    correctly (a citation to the last line resolved), while the stats block used
    the split spelling, so the line count printed for a document was one past its
    last line. A checker that exists because a wrong definition produces a
    confident wrong number is the last place to keep two of them.
    """
    if not text:
        return 0
    count = text.count("\n")
    if not text.endswith("\n"):
        count += 1
    return count


# ---------------------------------------------------------------------------
# Character hygiene (binary)
# ---------------------------------------------------------------------------
def check_hygiene(data):
    """FAIL for CR bytes, tabs, trailing whitespace, a codepoint above U+007F, or
    a missing final newline. Takes **raw bytes**, deliberately.

    A text-mode read on Windows translates CRLF to LF, so a checker that decodes
    first can never see a CR byte and reports a CRLF file as clean. The one
    property most likely to be broken by a tool writing the file is therefore the
    one a convenient implementation cannot detect.

    Codepoints above U+007F subsume the typographic tells (em dash, en dash, curly
    quotes, ellipsis, non-breaking space) while a document is pure ASCII, which is
    a stronger and simpler invariant than a list of characters to hunt for.
    """
    findings = []
    if not data:
        return findings

    cr = data.count(b"\r")
    if cr:
        findings.append((
            "FAIL", f"{cr} CR byte(s): the file has CRLF or CR line endings, and "
                    f"this project is LF"))
    tabs = data.count(b"\t")
    if tabs:
        findings.append(("FAIL", f"{tabs} tab character(s)"))
    if not data.endswith(b"\n"):
        findings.append(("FAIL", "no final newline"))

    # Trailing whitespace, judged on the line's content rather than its ending. A
    # single trailing CR is the line-ending artefact and is already reported above,
    # so it is removed first: otherwise every line of a CRLF file is reported twice,
    # once for the CR and once as trailing whitespace, and the real finding drowns.
    # A whitespace-only line IS trailing whitespace and is reported; a genuinely
    # empty line has nothing to strip and is not.
    for number, raw in enumerate(data.split(b"\n"), start=1):
        body = raw[:-1] if raw.endswith(b"\r") else raw
        if body.rstrip(b" \t") != body:
            findings.append((
                "FAIL", f"line {number}: trailing whitespace"))

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        findings.append(("FAIL", f"not valid UTF-8 ({exc})"))
        return findings
    for number, line in enumerate(text.split("\n"), start=1):
        high = sorted({ch for ch in line if ord(ch) > 0x7F})
        if high:
            shown = ", ".join(f"U+{ord(ch):04X}" for ch in high)
            findings.append((
                "FAIL", f"line {number}: codepoint(s) above U+007F ({shown})"))
    return findings

# ---------------------------------------------------------------------------
# Fences
# ---------------------------------------------------------------------------
# A fence opener/closer: 0 to 3 leading spaces, then three or more backticks or
# tildes. Content inside is blanked rather than removed so every line number this
# script reports is the line number in the real file.
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def blank_fenced_lines(lines):
    """Return a copy of ``lines`` with fenced-code content (and the fence markers)
    replaced by empty strings, preserving length and therefore line numbers.

    An unterminated fence blanks to end of file: an unclosed fence means the rest
    of the document is code as far as any markdown renderer is concerned, and
    guessing otherwise would resurrect exactly the illustrative content the fence
    was meant to quarantine.
    """
    out = []
    fence = None
    for line in lines:
        match = _FENCE_RE.match(line)
        if fence is None and match:
            fence = match.group(1)[0]
            out.append("")
            continue
        if fence is not None:
            closing = match and match.group(1)[0] == fence
            out.append("")
            if closing:
                fence = None
            continue
        out.append(line)
    return out


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
# A separator row, per CommonMark's 0-to-3-leading-space allowance: optional
# indent, a leading pipe, then one or more cells of hyphens with optional
# alignment colons, each closed by a pipe. Four or more leading spaces is an
# indented code block, not a table, and is deliberately not matched.
_SEP_RE = re.compile(r"^ {0,3}\|(?:[ \t]*:?-+:?[ \t]*\|)+[ \t]*$")
# Any table row: optional indent then a leading pipe.
_ROW_RE = re.compile(r"^ {0,3}\|")

_ESCAPED_PIPE = "\x00"


def cell_count(line):
    """Number of cells in a pipe table row.

    Escaped pipes (``\\|``) are protected first so a pipe inside a cell's text
    does not read as a cell boundary. The empty strings either side of the leading
    and trailing pipes are dropped.
    """
    body = line.strip().replace(r"\|", _ESCAPED_PIPE)
    parts = body.split("|")
    if parts and parts[0].strip() == "":
        parts = parts[1:]
    if parts and parts[-1].strip() == "":
        parts = parts[:-1]
    return len(parts)


def find_tables(lines):
    """Return a list of table dicts for a document whose fences are already blanked.

    Each dict: ``sep_line`` (1-based), ``width`` (the separator's cell count),
    ``header`` (line number, or None when a separator has no row above it), and
    ``body`` (list of ``(line_number, text)``).
    """
    tables = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not _SEP_RE.match(line):
            index += 1
            continue
        header = index - 1 if index > 0 and _ROW_RE.match(lines[index - 1]) else None
        body = []
        cursor = index + 1
        while cursor < len(lines) and _ROW_RE.match(lines[cursor]):
            body.append((cursor + 1, lines[cursor]))
            cursor += 1
        tables.append({
            "sep_line": index + 1,
            "width": cell_count(line),
            "header": None if header is None else header + 1,
            "header_text": None if header is None else lines[header],
            "body": body,
        })
        index = cursor
    return tables


def check_tables(lines, tables):
    """FAIL for any header or body row whose cell count differs from its
    separator row's, and for a separator row with no header above it."""
    findings = []
    for table in tables:
        width = table["width"]
        if table["header"] is None:
            findings.append((
                "FAIL", f"line {table['sep_line']}: table separator row has no "
                        f"header row above it"))
        else:
            got = cell_count(table["header_text"])
            if got != width:
                findings.append((
                    "FAIL", f"line {table['header']}: header row has {got} cells, "
                            f"separator row (line {table['sep_line']}) has {width}"))
        for line_no, text in table["body"]:
            got = cell_count(text)
            if got != width:
                findings.append((
                    "FAIL", f"line {line_no}: body row has {got} cells, separator "
                            f"row (line {table['sep_line']}) has {width}"))
    return findings


# ---------------------------------------------------------------------------
# Enumerated sequences
# ---------------------------------------------------------------------------
# An enumerator token in a first column: a bare integer (1), a prefixed integer
# (E10), or an integer with a lettered sub-item (13a). Emphasis and backticks are
# stripped before matching.
_ENUM_RE = re.compile(r"^([A-Za-z]{0,2})(\d+)([a-z]?)$")
_MIN_ENUM_ROWS = 3


def _first_cell(line):
    body = line.strip().replace(r"\|", _ESCAPED_PIPE)
    parts = body.split("|")
    if parts and parts[0].strip() == "":
        parts = parts[1:]
    if not parts:
        return ""
    return parts[0].strip().strip("*`_ ").strip()


def check_sequences(tables):
    """FAIL for a gap, a duplicate, or a row out of order in a table whose first
    column enumerates.

    A table qualifies only when it has at least three body rows and **every** body
    row's first cell is an enumerator sharing one prefix. That deliberately
    excludes an ordinary table whose first column happens to hold a number, and it
    scopes every count to the table that owns it: a document-wide sweep pools
    separate tables and reports duplicates that do not exist.

    Order is judged **ascending**, the same direction as the heading half, and the
    two halves are deliberately one definition rather than two. Ascending is the
    weaker of the two available rules: a table numbered 5, 4, 3, 2, 1 is a legal
    presentation this refuses, and that limitation is recorded in the workflow's
    CONTEXT.md rather than solved by accepting either direction, which would put a
    second meaning of "in order" inside a single check. Gaps and duplicates cannot
    catch this class on their own: 1, 3, 2 has neither, so a row inserted in the
    wrong position passed silently until this was added.
    """
    findings = []
    for table in tables:
        tokens = []
        for line_no, text in table["body"]:
            match = _ENUM_RE.match(_first_cell(text))
            if match is None:
                tokens = []
                break
            tokens.append((line_no, match.group(1), int(match.group(2)),
                           match.group(3)))
        if len(tokens) < _MIN_ENUM_ROWS:
            continue
        prefixes = {t[1] for t in tokens}
        if len(prefixes) != 1:
            continue
        prefix = tokens[0][1]

        seen = {}
        previous = None
        for line_no, _, number, suffix in tokens:
            # (number, suffix) rather than number alone, so 13 sorts before 13a:
            # the empty suffix precedes every letter, which is the order a lettered
            # sub-item is written in.
            key = (number, suffix)
            if key in seen:
                findings.append((
                    "FAIL", f"line {line_no}: duplicate enumerator "
                            f"`{prefix}{number}{suffix}` in the table starting at "
                            f"line {table['sep_line']} (first seen line "
                            f"{seen[key]})"))
            else:
                seen[key] = line_no
            if previous is not None and key < previous:
                findings.append((
                    "FAIL", f"line {line_no}: enumerator "
                            f"`{prefix}{number}{suffix}` appears after "
                            f"`{prefix}{previous[0]}{previous[1]}`, so the table "
                            f"starting at line {table['sep_line']} is out of order"))
            # Carry the value just read, not a high-water mark. Both spellings
            # report a descent, but they differ on which row is blamed when one is
            # badly misplaced: for 1, 27, 2, 3, ... 26 a high-water mark reports
            # every later row as out of order (26 findings for one defect), while
            # this reports the single descent and then resumes. The same rule is
            # used by check_heading_order, so "out of order" means one thing.
            previous = key
        numbers = sorted({t[2] for t in tokens})
        missing = [n for n in range(numbers[0], numbers[-1] + 1)
                   if n not in set(numbers)]
        if missing:
            findings.append((
                "FAIL", f"line {table['sep_line']}: enumerated table runs "
                        f"{prefix}{numbers[0]} to {prefix}{numbers[-1]} and is "
                        f"missing " + ", ".join(f"{prefix}{n}" for n in missing)))
    return findings


# ---------------------------------------------------------------------------
# Numbered headings
# ---------------------------------------------------------------------------
# A numbered heading: hashes, then a dotted number (4, 4.1, 9.2), then a title.
# The trailing dot on a top-level number is optional ("## 4. Survey" / "## 9").
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(\d+(?:\.\d+)*)\.?[ \t]+\S")


def find_numbered_headings(lines):
    """Return ``(line_number, level, parts)`` for each numbered heading, where
    ``parts`` is the dotted number as a tuple of ints."""
    found = []
    for number, line in enumerate(lines, start=1):
        match = _HEADING_RE.match(line)
        if match:
            parts = tuple(int(p) for p in match.group(2).split("."))
            found.append((number, len(match.group(1)), parts))
    return found


def check_heading_order(lines):
    """FAIL for a numbered heading out of order, duplicated, or skipping a number
    within its parent.

    Scoped **per parent**, the same way the table check is scoped per table: 9.1
    to 9.6 are one sequence, 4.1 to 4.9 are another, and pooling them would
    report nonsense. A document may legitimately start a sequence at any number
    (4.6a-style suffixes are handled by the table checker, not here), so only the
    order and the gaps within one parent are judged.
    """
    findings = []
    groups = {}
    for line_no, _level, parts in find_numbered_headings(lines):
        groups.setdefault(parts[:-1], []).append((line_no, parts[-1]))
    for parent, entries in sorted(groups.items()):
        label = ".".join(str(p) for p in parent)
        prefix = f"{label}." if label else ""
        seen = {}
        previous = None
        for line_no, number in entries:
            if number in seen:
                findings.append((
                    "FAIL", f"line {line_no}: duplicate heading number "
                            f"`{prefix}{number}` (first seen line {seen[number]})"))
            else:
                seen[number] = line_no
            if previous is not None and number < previous:
                findings.append((
                    "FAIL", f"line {line_no}: heading `{prefix}{number}` appears "
                            f"after `{prefix}{previous}`, so the section is out of "
                            f"order"))
            # The value just read, matching check_sequences. A high-water mark
            # would blame every subsequent heading for one badly misplaced
            # section; this reports the descent once and resumes.
            previous = number
        numbers = sorted(seen)
        missing = [n for n in range(numbers[0], numbers[-1] + 1)
                   if n not in seen]
        if missing:
            findings.append((
                "FAIL", f"line {entries[0][0]}: heading sequence `{prefix}*` runs "
                        f"{prefix}{numbers[0]} to {prefix}{numbers[-1]} and is "
                        f"missing " + ", ".join(f"{prefix}{n}" for n in missing)))
    return findings


# ---------------------------------------------------------------------------
# Distance references
# ---------------------------------------------------------------------------
_CARDINALS = (
    r"\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|dozen"
)
_ORDINALS = (
    r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
    r"eleventh|twelfth|last|final|penultimate"
)
_UNITS = (
    r"lines?|paragraphs?|bullets?|rows?|sections?|sentences?|tables?|columns?|"
    r"entr(?:y|ies)|items?|steps?|pages?|headings?|screens?|blocks?|rules?|"
    r"words?|conditions?|decisions?|stages?|checks?|traps?|clauses?"
)
_DIRECTIONS = (
    r"above|below|earlier|later|up|down|back|previous|preceding|following|next|"
    r"before|after|hence|onward"
)

# cardinal + structural unit, then a direction within the same sentence. The
# intervening span is capped and cannot cross a sentence end or a line break, so
# an unrelated direction word further along the paragraph is not swept in.
_DISTANCE_RE = re.compile(
    r"\b(?:" + _CARDINALS + r")\s+(?:" + _UNITS + r")\b[^.\n]{0,40}?\b(?:"
    + _DIRECTIONS + r")\b",
    re.IGNORECASE,
)
# An ordinal into a list that can grow. Reported on its own tier of evidence: the
# ordinal alone is the rot risk, with or without a direction word.
_ORDINAL_RE = re.compile(
    r"\b(?:" + _ORDINALS + r")\s+(?:" + _UNITS + r")\b",
    re.IGNORECASE,
)


def logical_lines(lines):
    """Join hard-wrapped prose into logical lines, keeping every character's origin.

    Returns ``(text, line_numbers)`` pairs, where ``line_numbers[i]`` is the source
    line of ``text[i]``. A blank line ends a run, and a table row is a run of its own
    so a match can never span two cells.

    This exists because the distance patterns cannot match across a newline, which
    made the check's output depend on **where the text happened to wrap**: "the rule
    two\\nbullets down" is the defect the check is named for and was invisible, while
    the identical phrase on one line was reported. Measured on the document this
    workflow was built for, 12 candidates were hidden by wrapping alone, so every
    "the distance sweep is complete" claim made against the wrapped reading was an
    undercount by an unknown amount. Excluding the newline was never the point: the
    sentence boundary and the 40-character cap are what stop an unrelated direction
    word being swept in, and both survive the join.
    """
    out = []
    buffer = []
    for number, line in enumerate(lines, start=1):
        # _ROW_RE, not a second copy of it: "what a table row looks like" is one
        # definition, and this file already owns it.
        is_row = bool(_ROW_RE.match(line))
        if not line.strip() or is_row:
            if buffer:
                out.append(_join(buffer))
                buffer = []
            if is_row:
                out.append((line, [number] * len(line)))
            continue
        buffer.append((number, line))
    if buffer:
        out.append(_join(buffer))
    return out


def _join(buffer):
    """Join ``(line_number, text)`` pairs with single spaces, tracking origins."""
    parts = []
    origins = []
    for index, (number, line) in enumerate(buffer):
        if index:
            parts.append(" ")
            origins.append(number)
        stripped = line.strip()
        parts.append(stripped)
        origins.extend([number] * len(stripped))
    return "".join(parts), origins


def check_distance(lines):
    """INFO candidates for references by distance and ordinals into growing lists.

    Never a verdict. The section-7 discriminator - delete the distance and see
    whether the sentence still says what it came to say - needs judgement, so a
    claim *about* distance is a legitimate match here and is adjudicated by the
    reader, not by this function.

    Matching runs over logical lines, so a reference broken by a hard wrap is found
    and is reported at the line where it **starts**.
    """
    findings = []
    for text, origins in logical_lines(lines):
        for label, pattern in (("distance reference", _DISTANCE_RE),
                               ("ordinal-into-a-list", _ORDINAL_RE)):
            for match in pattern.finditer(text):
                number = origins[match.start()] if origins else 1
                findings.append((
                    "INFO", f"line {number}: {label} candidate: "
                            f"\"{match.group(0).strip()}\""))
    findings.sort(key=lambda f: int(f[1].split()[1].rstrip(":")))
    return findings


# ---------------------------------------------------------------------------
# Sequence sweep (opt-in, config-driven)
# ---------------------------------------------------------------------------
# A sentence ends at . ; : or ! ? followed by whitespace. Splitting matters here in
# a way it does not for the distance check: subject and relation must co-occur in
# one *sentence*, or a joined paragraph mentioning a stage in its first clause and
# "after" in its last would match as a candidate it never was.
_SENTENCE_END_RE = re.compile(r"(?<=[.;:!?])\s+")


def load_sequence_config(path):
    """Read a sweep config. Returns the parsed dict.

    The vocabulary is supplied rather than hardcoded because this workflow takes
    the document as an argument and must stay document-agnostic: a stage token
    belongs to one project's plan, not to a general markdown checker. JSON rather
    than YAML so the check stays stdlib-only and cannot inherit a missing-PyYAML
    degrade path.
    """
    with open(path, encoding="utf-8") as handle:
        config = json.load(handle)
    for field in ("subject", "relation"):
        if not config.get(field):
            raise ValueError(f"sequence config {path} has no non-empty '{field}'")
    return config


def sentences_with_origins(lines):
    """Yield ``(sentence, line_number)`` over de-wrapped prose.

    Built on ``logical_lines`` rather than on a second de-wrapper: the join and its
    per-character origin tracking are already defined once in this file, and a
    sweep that re-implemented them would report a different line number for the
    same sentence.
    """
    for text, origins in logical_lines(lines):
        offset = 0
        for sentence in _SENTENCE_END_RE.split(text):
            if sentence.strip():
                index = min(offset, len(origins) - 1) if origins else 0
                yield sentence.strip(), (origins[index] if origins else 1)
            # +1 approximates the separator consumed by the split; the origin is
            # only ever used to name the line a sentence starts on.
            offset += len(sentence) + 1


def check_sequence(lines, config):
    """INFO candidates for sentences stating a relationship between named subjects.

    Never a verdict. The sweep bounds the population; each candidate still needs a
    human verdict (dependency, non-dependency, interaction note, and so on). Its
    job is to make a completeness claim checkable rather than remembered.

    **The known-members positive control is the point of this check.** A sweep is
    only as complete as its vocabulary, and a vocabulary assembled by hand is a
    sample. Config lists the members the caller already knows are in the class; if
    the sweep cannot find one, the vocabulary has a hole and every count it
    produces is an undercount by an unknown amount, so that is a FAIL rather than
    an INFO. This was not hypothetical: the first vocabulary written for the
    guard-coverage inventory missed a known member phrased "when this stage runs",
    because it listed ordering words and not relational ones.
    """
    subject = re.compile("|".join(config["subject"]), re.I)
    relation = re.compile("|".join(config["relation"]), re.I)
    label = config.get("label", "sequence")

    findings = []
    hits = []
    for sentence, number in sentences_with_origins(lines):
        if subject.search(sentence) and relation.search(sentence):
            hits.append((number, sentence))
            excerpt = sentence if len(sentence) <= 120 else sentence[:117] + "..."
            findings.append((
                "INFO", f"line {number}: {label} candidate: \"{excerpt}\""))

    for member in config.get("known_members", []):
        phrase = member.get("phrase", "")
        if not phrase:
            continue
        if not any(phrase.lower() in sentence.lower() for _n, sentence in hits):
            findings.append((
                "FAIL", f"sequence sweep did not find known member "
                        f"\"{phrase}\" - the vocabulary has a hole, so every "
                        f"count from this sweep is an undercount"))

    findings.sort(key=lambda f: (f[0] != "FAIL",))
    return findings


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------
_CITED_EXTS = (
    r"py|md|json|ya?ml|txt|js|ts|toml|cfg|ini|sh|template|gitignore|"
    r"gitattributes|jsonl|db|csv|html|css"
)
# path:line or path:line-line. The lookbehind stops a match starting mid-path, so
# a longer path is never truncated into a shorter one that then fails to resolve.
_CITATION_RE = re.compile(
    r"(?<![A-Za-z0-9_/.\-])((?:[A-Za-z0-9_.\-]+/)*[A-Za-z0-9_.\-]*\.(?:"
    + _CITED_EXTS + r")):(\d+)(?:-(\d+))?\b"
)
# Path-less shorthand in backticks: `:265`, `:291-292`.
_SHORTHAND_RE = re.compile(r"`:(\d+)(?:-(\d+))?`")


def find_citations(lines):
    """Return ``(citations, shorthand)`` for fence-blanked lines.

    ``citations``: list of ``(line_number, raw, path, start, end)``.
    ``shorthand``: list of ``(line_number, raw)``.
    Pure - no filesystem access, so the parse can be tested without a repo.
    """
    citations = []
    shorthand = []
    for number, line in enumerate(lines, start=1):
        for match in _CITATION_RE.finditer(line):
            start = int(match.group(2))
            end = int(match.group(3)) if match.group(3) else start
            citations.append((number, match.group(0), match.group(1), start, end))
        for match in _SHORTHAND_RE.finditer(line):
            shorthand.append((number, match.group(0)))
    return citations, shorthand


def check_citations(lines, root):
    """FAIL for a citation whose file is missing or whose range is outside it;
    WARN for path-less shorthand.

    Line counts are read once per cited file and cached, so a document citing one
    file forty times reads it once.
    """
    findings = []
    citations, shorthand = find_citations(lines)
    # rel -> ("ok", line_count) | ("missing", None) | ("unreadable", message).
    # The status is cached alongside the count rather than collapsing both failure
    # modes to None: a file that exists but cannot be read is a different fact from
    # one that is not there, and reporting the second for the first is a wrong
    # answer that sends a reader looking for a path that is sitting right where the
    # citation says it is.
    resolved = {}
    for number, raw, rel, start, end in citations:
        if rel not in resolved:
            target = Path(root) / rel
            if not target.is_file():
                resolved[rel] = ("missing", None)
            else:
                try:
                    with open(target, "rb") as handle:
                        data = handle.read()
                    text = data.decode("utf-8", errors="replace")
                    resolved[rel] = ("ok", physical_line_count(text))
                except OSError as exc:
                    resolved[rel] = ("unreadable", str(exc))
        status, total = resolved[rel]
        if status == "missing":
            findings.append((
                "FAIL", f"line {number}: citation `{raw}` names a path that does "
                        f"not exist ({rel})"))
            continue
        if status == "unreadable":
            findings.append((
                "FAIL", f"line {number}: citation `{raw}` names a file that exists "
                        f"but could not be read ({total})"))
            continue
        if start < 1 or end < start:
            findings.append((
                "FAIL", f"line {number}: citation `{raw}` has an impossible range"))
        elif end > total:
            findings.append((
                "FAIL", f"line {number}: citation `{raw}` runs past the end of "
                        f"{rel} ({total} lines)"))
    for number, raw in shorthand:
        findings.append((
            "WARN", f"line {number}: shorthand citation `{raw}` carries no path, "
                    f"so it cannot be resolved mechanically"))
    return findings


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
def check_document(text, root, only=None, data=None, sequence_config=None):
    """Run the selected checks over one document.

    ``text`` is the decoded document; ``data`` its raw bytes, required for the
    binary-mode hygiene check and omitted by callers that only want the text
    checks. Returns ``(findings, stats)``: ``findings`` are ``(severity,
    message)``; ``stats`` carries the measured counts a caller may want to quote,
    each produced by the definitions above rather than by a fresh regex.
    """
    selected = tuple(only) if only is not None else CHECK_NAMES
    raw_lines = text.split("\n")
    lines = blank_fenced_lines(raw_lines)
    tables = find_tables(lines)

    findings = []
    if "hygiene" in selected:
        if data is None:
            findings.append((
                "FAIL", "hygiene check requires the raw bytes and was given none; "
                        "call check_file, or pass data="))
        else:
            findings.extend(check_hygiene(data))
    if "tables" in selected:
        findings.extend(check_tables(lines, tables))
    if "sequences" in selected:
        findings.extend(check_sequences(tables))
        findings.extend(check_heading_order(lines))
    if "distance" in selected:
        findings.extend(check_distance(lines))
    if "citations" in selected:
        findings.extend(check_citations(lines, root))
    # Opt-in and gated on a config rather than named in CHECK_NAMES, so that every
    # existing caller's result is unchanged and a run without a config cannot
    # silently report a sweep it never performed.
    if sequence_config is not None:
        findings.extend(check_sequence(lines, sequence_config))

    citations, shorthand = find_citations(lines)
    stats = {
        "lines": physical_line_count(text),
        "tables": len(tables),
        "table_rows": sum(len(t["body"]) for t in tables),
        "citations": len(citations),
        "shorthand_citations": len(shorthand),
    }
    return findings, stats


def check_file(path, root, only=None, sequence_config=None):
    """Read the target in binary and run the selected checks.

    Binary, then decode: the hygiene check needs the bytes as they are on disk, and
    reading in text mode would silently translate the line endings it exists to
    inspect.
    """
    with open(Path(path), "rb") as handle:
        data = handle.read()
    selected = tuple(only) if only is not None else CHECK_NAMES
    hygiene_findings = []
    if "hygiene" in selected:
        hygiene_findings = check_hygiene(data)
        if any("not valid UTF-8" in message for _, message in hygiene_findings):
            return hygiene_findings, {
                "lines": 0,
                "tables": 0,
                "table_rows": 0,
                "citations": 0,
                "shorthand_citations": 0,
            }

    text = data.decode("utf-8")
    remaining = [check for check in selected if check != "hygiene"]
    findings, stats = check_document(text, root, only=remaining, data=data,
                                     sequence_config=sequence_config)
    return hygiene_findings + findings, stats


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def print_report(results):
    print("# Doc-Verify Report\n")
    total = {"FAIL": 0, "WARN": 0, "INFO": 0}
    for rel, findings, stats in results:
        fails = [f for f in findings if f[0] == "FAIL"]
        warns = [f for f in findings if f[0] == "WARN"]
        infos = [f for f in findings if f[0] == "INFO"]
        total["FAIL"] += len(fails)
        total["WARN"] += len(warns)
        total["INFO"] += len(infos)
        print(f"## {rel}\n")
        print(f"**Lines:** {stats['lines']}  **Tables:** {stats['tables']}  "
              f"**Table rows:** {stats['table_rows']}  "
              f"**Citations:** {stats['citations']} "
              f"(+{stats['shorthand_citations']} shorthand)\n")
        print(f"**FAIL:** {len(fails)}  **WARN:** {len(warns)}  "
              f"**INFO:** {len(infos)}\n")
        for tier, group in (("FAIL", fails), ("WARN", warns), ("INFO", infos)):
            if not group:
                continue
            print(f"### {tier}")
            for _, msg in group:
                print(f"- {msg}")
            print()
    if total["FAIL"] == 0 and total["WARN"] == 0:
        print("No structural defects found. INFO candidates, if any, need "
              "adjudication.")


def findings_json(results):
    return json.dumps({
        "documents": [
            {
                "file": rel,
                "stats": stats,
                "findings": [
                    {"severity": s, "label": LABEL, "message": m}
                    for s, m in findings
                ],
            }
            for rel, findings, stats in results
        ]
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Structural self-consistency checks for a markdown document "
                    "(read-only). The target file is an argument.")
    parser.add_argument("files", nargs="+",
                        help="Markdown file(s) to check, project-root-relative")
    parser.add_argument("--check", action="store_true",
                        help="Read-only scan (default; the only mode)")
    parser.add_argument("--only", action="append", choices=CHECK_NAMES,
                        help="Run only this check (repeatable)")
    parser.add_argument("--json", action="store_true",
                        help="Emit findings as JSON on stdout")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 if any FAIL or WARN exists (INFO never gates)")
    parser.add_argument("--sequence-config", metavar="PATH",
                        help="JSON config enabling the opt-in sequence sweep: "
                             "subject and relation vocabularies, plus the known "
                             "members the sweep must find (its positive control)")
    args = parser.parse_args(argv)

    sequence_config = None
    if args.sequence_config:
        try:
            sequence_config = load_sequence_config(args.sequence_config)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            # Loud, not silent: a sweep that could not load its vocabulary would
            # otherwise report zero candidates and read as a clean document.
            print(f"FAIL: could not load sequence config "
                  f"{args.sequence_config} ({exc})")
            sys.exit(1)

    results = []
    hard_error = False
    for name in args.files:
        try:
            findings, stats = check_file(name, PROJECT_ROOT, only=args.only,
                                         sequence_config=sequence_config)
        except (OSError, UnicodeDecodeError) as exc:
            results.append((name, [("FAIL", f"could not read ({exc})")],
                            {"lines": 0, "tables": 0, "table_rows": 0,
                             "citations": 0, "shorthand_citations": 0}))
            hard_error = True
            continue
        results.append((name, findings, stats))

    if args.json:
        print(findings_json(results))
    else:
        print_report(results)

    gating = any(s in ("FAIL", "WARN")
                 for _, findings, _ in results for s, _ in findings)
    if hard_error or (args.strict and gating):
        sys.exit(1)


if __name__ == "__main__":
    main()
