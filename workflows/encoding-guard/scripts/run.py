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
    INFO  an open()/read_text()/write_text() call with no explicit encoding=

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
_CALL_RE = re.compile(
    r"(subprocess\.(?:run|Popen|check_output|check_call)|\.read_text|\.write_text|(?<![\w.])open)\s*\("
)


_BLANK_TOKENS = {tokenize.STRING, tokenize.COMMENT}
for _name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):
    if hasattr(tokenize, _name):
        _BLANK_TOKENS.add(getattr(tokenize, _name))


def _code_only(text):
    """Return ``text`` with string and comment tokens blanked to spaces, offsets
    preserved, so the call-pattern regex never matches a call name that only
    appears in prose, a docstring, or an f-string literal."""
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text
    rows = [list(line) for line in text.splitlines(keepends=True)]
    for tok in toks:
        if tok.type not in _BLANK_TOKENS:
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


_KWARG_RE = re.compile(r"^[A-Za-z_]\w*\s*=(?!=)")
_STR_LITERAL_RE = re.compile(r"^[rbufRBUF]{0,2}('''|\"\"\"|'|\")(.*?)\1$", re.S)


def _split_args(call):
    """Split a ``(...)`` call body into its top-level argument strings.

    Quote- and bracket-aware, so a comma inside a string or a nested call never
    splits an argument. Used to find an ``open()`` mode where a plain substring
    test cannot tell a mode from any other short literal in the call."""
    body = call[1:-1] if call.startswith("(") and call.endswith(")") else call
    args, depth, quote, start, i = [], 0, None, 0, 0
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
            args.append(body[start:i])
            start = i + 1
        i += 1
    args.append(body[start:])
    return [a.strip() for a in args if a.strip()]


def _literal_value(arg):
    """The text inside a simple string literal, or None if it is not one."""
    m = _STR_LITERAL_RE.match(arg.strip())
    return m.group(2) if m else None


def _open_mode(call):
    """The literal mode string of an ``open(...)`` call.

    Returns ``""`` when no mode is given (Python defaults to text read), and
    ``None`` when a mode is present but is not a literal we can read - a
    computed mode is left alone rather than guessed at, so an unreadable call
    never produces a false positive."""
    args = _split_args(call)
    for a in args:
        if a.startswith("mode="):
            return _literal_value(a[len("mode="):])
    positional = [a for a in args if not _KWARG_RE.match(a)]
    if len(positional) >= 2:
        return _literal_value(positional[1])
    return ""


def _is_text_mode_write(name, call):
    """True for a call that writes text, where Windows translates LF to CRLF on
    the way out unless ``newline=`` is passed. Reads and binary writes are
    excluded: the newline rule governs what gets written, not what is read."""
    if name == ".write_text":
        return True
    if name != "open":
        return False
    mode = _open_mode(call)
    if mode is None or "b" in mode:
        return False
    return any(c in mode for c in "wax+")


def check_python_code(rel, text):
    """Heuristic checks for missing explicit encodings in a .py source string.

    Matching is done against a code-only copy (strings/comments blanked) so prose
    never produces a false positive; call contents are read from the original so
    mode strings like ``"wb"`` and ``encoding=`` are still visible. Offsets are
    identical because blanking preserves length."""
    findings = []
    code = _code_only(text)
    for m in _CALL_RE.finditer(code):
        name = m.group(1)
        end = _call_end(code, m.end() - 1)
        call = text[m.end() - 1:end]
        if "encoding=" not in call:
            if name.startswith("subprocess"):
                if "text=True" in call or "universal_newlines=True" in call:
                    findings.append((
                        "WARN", "encoding",
                        f"{rel}: text-mode {name}(...) with no explicit encoding= "
                        f"(decodes as cp1252 on Windows)",
                    ))
            elif name == "open":
                modes = ("'b'", '"b"', "rb", "wb", "ab", "xb", "+b", "b'", 'b"')
                if not any(mode in call for mode in modes):
                    findings.append((
                        "INFO", "encoding",
                        f"{rel}: open(...) with no explicit encoding=",
                    ))
            else:  # .read_text / .write_text
                findings.append((
                    "INFO", "encoding",
                    f"{rel}:{name}(...) with no explicit encoding=",
                ))
        # The newline rule is a second, independent clause of the same project
        # rule, so it is checked separately. Folding it into the encoding branch
        # above is the defect this structure exists to prevent: a call that
        # pins encoding= but not newline= would return early and be read as
        # clean, which is exactly what happened before 2026-08-04.
        if _is_text_mode_write(name, call) and "newline=" not in call:
            findings.append((
                "WARN", "encoding",
                f"{rel}: text-mode {name}(...) with no explicit newline= "
                f"(Windows text mode writes CRLF)",
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
    counts = {"FAIL": 0, "WARN": 0, "INFO": 0}
    for sev, _, _ in findings:
        counts[sev] = counts.get(sev, 0) + 1
    print("# Encoding Guard Report\n")
    print(f"**Failures:** {counts['FAIL']}  **Warnings:** {counts['WARN']}  "
          f"**Info:** {counts['INFO']}\n")
    if not findings:
        print("No encoding problems found.")
        return
    for sev in ("FAIL", "WARN", "INFO"):
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
