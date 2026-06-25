#!/usr/bin/env python3
"""Encoding guard - keep the project's text encoding clean and consistent.

Two modes:

    python workflows/encoding-guard/scripts/run.py --check [--json]
    python workflows/encoding-guard/scripts/run.py --fix [--dry-run]

--check (default) is read-only. It walks the project tree and reports:
    FAIL  a text file that is not valid UTF-8 (breaks strict readers)
    WARN  mojibake (double-encoded Windows-1252 punctuation) or an unexpected BOM
    WARN  a text-mode subprocess call with no explicit encoding= (a Windows
          cp1252 decode trap, the exact bug class this workflow exists to stop)
    INFO  an open()/read_text()/write_text() call with no explicit encoding=

--fix repairs the flagged files in place: it restores valid UTF-8, folds
Windows-1252 punctuation and mojibake to plain ASCII (per the em-dash-avoidance
rule), strips an unexpected BOM, and normalises line endings to LF. It is
idempotent and honours --dry-run.

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
    """Return a list of issue codes found in an already-decoded string."""
    codes = []
    if text.startswith(BOM):
        codes.append("bom")
    if any(k in text for k in DOUBLE_ENCODED):
        codes.append("mojibake")
    return codes


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
        if "encoding=" in call:
            continue
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
def _has_encoding_problem(data):
    """True only if the bytes are invalid UTF-8 or carry mojibake/an unexpected
    BOM. A clean file whose only 'fancy' content is legitimate Unicode (e.g. a
    real em dash) is NOT a problem, so --fix never mass-folds the whole project
    to ASCII; it only repairs genuinely corrupted files."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return bool(scan_text(text))


def run_fix(root, dry_run):
    """Repair only the files with a real encoding problem (invalid UTF-8,
    mojibake, or an unexpected BOM). Code-pattern findings are advisory and are
    never auto-edited. A flagged file is fully normalised, which includes folding
    its legitimate smart punctuation to ASCII. Returns (changed, skipped)."""
    changed, skipped = [], []
    for path in iter_text_files(root):
        rel = path.relative_to(root).as_posix()
        original = path.read_bytes()
        if not _has_encoding_problem(original):
            continue
        repaired, note = repair_bytes(original)
        if repaired is None:
            skipped.append((rel, note))
            continue
        if repaired == original:
            continue
        if dry_run:
            print(f"[DRY RUN] would repair {rel} ({note})")
        else:
            with open(path, "wb") as fh:
                fh.write(repaired)
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
    with open(path, "a", encoding="utf-8") as fh:
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
    args = parser.parse_args()

    if args.fix:
        # --dry-run writes nothing at all, not even a log entry.
        if not args.dry_run:
            _log(WORKFLOW_LOG, "started", "Encoding fix run started.")
        try:
            changed, skipped = run_fix(PROJECT_ROOT, args.dry_run)
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
