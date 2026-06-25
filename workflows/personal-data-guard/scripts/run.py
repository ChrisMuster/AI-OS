#!/usr/bin/env python3
"""Personal data guard - catch personal markers in committable files.

Read-only check. There is deliberately no fix mode: a personal-data leak cannot
be safely auto-redacted (you cannot know what generic text should replace a
name), so the guard only reports.

    python workflows/personal-data-guard/scripts/run.py --check [--json]

Scope - committable files only. The file list is driven by
``git ls-files --cached --others --exclude-standard`` (tracked files plus new
files git would add), so everything gitignored is excluded automatically. That
means the files that are *meant* to hold personal data - USER.md, LOG.md,
memory/, journal/entries/, wikis/, conversations/, .env, user-inputs/, and the
design-time docs (*-PLAN.md / HANDOVER.md / PROPOSAL.md / ROADMAP.md) - are
never scanned, and verbatim third-party data (collections/, raw/) is exempt for
free because it is gitignored too.

Reports:
    FAIL  a real email address (not an allowlisted placeholder)
    FAIL  a personal absolute home path carrying a real username
          (C:\\Users\\<name>, /home/<name>, /Users/<name>); documentation
          placeholders such as C:/Users/Name/... are ignored
    FAIL  a literal match of the user's own name / username / email, derived at
          runtime from USER.md and .env (the canonical local sources)
    WARN  a match against the optional personal-noun denylist
    INFO  degraded / skip notes (git unavailable, USER.md absent, etc.)

The script source is generic: every real personal marker is read at runtime
from local gitignored sources, never embedded here, so this file never carries
personal data and never flags itself.

Exit codes: 0 when there is no FAIL finding; 1 when a FAIL finding exists, so a
pre-commit hook or CI can gate on it. --json always prints the payload.
"""

import argparse
import json
import os
import re
import subprocess
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
WORKFLOW_DIR = PROJECT_ROOT / "workflows" / "personal-data-guard"
DENYLIST_FILE = WORKFLOW_DIR / "config" / "denylist.txt"
USER_MD = PROJECT_ROOT / "USER.md"
ENV_FILE = PROJECT_ROOT / ".env"

Finding = tuple  # (severity, label, message)
LABEL = "personal-data"

# ---------------------------------------------------------------------------
# What to scan
# ---------------------------------------------------------------------------
# Only these extensions are read (git may track binaries; we never decode them).
TEXT_EXTS = {
    ".md", ".py", ".json", ".toml", ".txt", ".js", ".ts", ".html", ".css",
    ".yml", ".yaml", ".cfg", ".ini", ".example", ".gitignore", ".gitattributes",
    ".mdc", ".sh", ".bat", ".ps1", ".conf",
}
TEXT_NAMES = {".gitignore", ".gitattributes"}

# ---------------------------------------------------------------------------
# Generic detectors (no personal data baked in)
# ---------------------------------------------------------------------------
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# A Windows or Unix home path with a captured username segment. Handles both
# back- and forward-slash separators so the Windows drive form and the Unix
# form are both matched.
HOME_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]Users[\\/]|/home/|/Users/)([^\\/\s\"'`)\];:,]+)"
)

# Username segments that are placeholders, not a real account name, so a path
# like C:/Users/Name/... in documentation is not a leak.
PLACEHOLDER_USERS = {
    "name", "names", "yourname", "your-name", "username", "user", "users",
    "you", "youruser", "example", "someone", "admin", "administrator",
    "public", "default", "<name>", "<user>", "<username>", "<you>",
    "me", "home",
}

# Emails that are obvious placeholders / non-personal. Matched case-insensitively
# against the whole address and against the domain.
ALLOWLIST_EMAIL_DOMAINS = {
    "example.com", "example.org", "example.net", "example.co.uk",
    "domain.com", "email.com", "host.com", "test.com", "sample.com",
    "yourdomain.com", "company.com", "mail.com",
}
ALLOWLIST_EMAILS = {
    "noreply@anthropic.com",
    "you@example.com", "user@example.com", "name@example.com",
    "your-email@example.com", "youremail@example.com",
}
# Local-part fragments that mark a placeholder address regardless of domain.
PLACEHOLDER_EMAIL_HINTS = ("example", "your", "placeholder", "user@", "name@")


def _word_re(term: str) -> re.Pattern:
    """Word-boundary, case-insensitive matcher for a literal personal term."""
    return re.compile(r"(?<![\w])" + re.escape(term) + r"(?![\w])", re.IGNORECASE)


# A real account name: starts alphanumeric, then word chars / dot / dash. This
# rejects the regex-fragment captures (``|``, ``[A-Za-z``) that a path pattern
# in source code produces, so the guard does not flag other scripts' regexes.
_USERNAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-]*\Z")


def _is_real_username(seg: str) -> bool:
    return (
        bool(_USERNAME_RE.match(seg))
        and not seg.startswith("<")
        and seg.lower() not in PLACEHOLDER_USERS
    )


# ---------------------------------------------------------------------------
# Marker derivation (read at runtime from local, gitignored sources)
# ---------------------------------------------------------------------------
def derive_name_markers(user_md_text):
    """Pull the user's name from a USER.md '**Name:**' line.

    Returns a list of literal terms: the full name plus each token of length 3+
    (so initials and one/two-letter particles do not generate noisy matches).
    Pure: takes the file text, returns terms.
    """
    if not user_md_text:
        return []
    m = re.search(r"^\*\*Name:\*\*\s*(.+)$", user_md_text, re.MULTILINE)
    if not m:
        return []
    full = m.group(1).strip()
    if not full:
        return []
    terms = {full}
    for tok in re.split(r"[\s\-]+", full):
        tok = tok.strip(".,'\"")
        if len(tok) >= 3:
            terms.add(tok)
    return sorted(terms, key=len, reverse=True)


def derive_env_emails(env_text):
    """Return every email-looking value found in a .env file's text."""
    if not env_text:
        return []
    return sorted({m.group(0) for m in EMAIL_RE.finditer(env_text)})


def derive_os_username():
    """The current account name, unless it is a generic placeholder."""
    name = (os.environ.get("USERNAME") or os.environ.get("USER")
            or Path.home().name or "").strip()
    if name and name.lower() not in PLACEHOLDER_USERS:
        return name
    return None


def load_denylist(text):
    """Parse a denylist file's text: one term per line, '#' starts a comment."""
    terms = []
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            terms.append(line)
    return terms


def _read(path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def build_markers():
    """Assemble the runtime marker set. Returns (markers, info).

    markers = {
        "name_terms":  [literal own-name terms -> FAIL],
        "env_emails":  [own emails from .env   -> FAIL (also caught generically)],
        "os_user":     str | None              (-> FAIL),
        "denylist":    [personal nouns          -> WARN],
    }
    info = list of INFO findings about degraded sources.
    """
    info = []
    user_text = _read(USER_MD) if USER_MD.exists() else ""
    if not user_text:
        info.append((
            "INFO", LABEL,
            "USER.md not found - name markers unavailable; running "
            "pattern-only (emails, home paths, OS username)",
        ))
    env_text = _read(ENV_FILE) if ENV_FILE.exists() else ""
    deny_text = _read(DENYLIST_FILE) if DENYLIST_FILE.exists() else ""

    markers = {
        "name_terms": derive_name_markers(user_text),
        "env_emails": derive_env_emails(env_text),
        "os_user": derive_os_username(),
        "denylist": load_denylist(deny_text),
    }
    return markers, info


# ---------------------------------------------------------------------------
# Classification (pure: text in, findings out)
# ---------------------------------------------------------------------------
def _email_allowed(addr):
    low = addr.lower()
    if low in ALLOWLIST_EMAILS:
        return True
    domain = low.rsplit("@", 1)[-1]
    if domain in ALLOWLIST_EMAIL_DOMAINS:
        return True
    return any(hint in low for hint in PLACEHOLDER_EMAIL_HINTS)


def scan_text(rel, text, markers):
    """Return findings for one file's text. Pure - no filesystem access."""
    findings = []

    # Emails (any non-placeholder address is a leak).
    for m in EMAIL_RE.finditer(text):
        addr = m.group(0)
        if not _email_allowed(addr):
            findings.append((
                "FAIL", LABEL, f"{rel}: email address `{addr}`",
            ))

    # Personal home paths carrying a real username.
    for m in HOME_PATH_RE.finditer(text):
        seg = m.group(1)
        if _is_real_username(seg):
            findings.append((
                "FAIL", LABEL,
                f"{rel}: personal home path with username `{seg}` "
                f"(`{m.group(0)}`)",
            ))

    # OS username appearing literally (and not already reported as a path above).
    os_user = markers.get("os_user")
    if os_user and _word_re(os_user).search(text):
        findings.append((
            "FAIL", LABEL, f"{rel}: OS username `{os_user}` present",
        ))

    # The user's own name and its tokens.
    for term in markers.get("name_terms", []):
        if _word_re(term).search(text):
            findings.append((
                "FAIL", LABEL, f"{rel}: personal name `{term}` present",
            ))

    # Optional personal-noun denylist (advisory WARN: could be a real word).
    for term in markers.get("denylist", []):
        if _word_re(term).search(text):
            findings.append((
                "WARN", LABEL, f"{rel}: denylisted personal term `{term}` present",
            ))

    return _dedupe(findings)


def _dedupe(findings):
    seen, out = set(), []
    for f in findings:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


# ---------------------------------------------------------------------------
# File discovery (committable files, via git)
# ---------------------------------------------------------------------------
def committable_files(root):
    """Yield committable text-file paths under ``root``.

    Uses git so gitignored personal files are excluded. Returns (paths, info):
    on any git failure, paths is empty and info carries a single INFO note, so
    the caller degrades gracefully rather than scanning the whole tree (which
    would defeat the gitignore-based scope and flood false positives).
    """
    info = []
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            capture_output=True, encoding="utf-8", cwd=str(root),
        )
    except Exception as exc:  # git missing
        return [], [("INFO", LABEL, f"scan skipped - could not run git ({exc})")]
    if result.returncode != 0:
        reason = (result.stderr or "").strip().splitlines()
        reason = reason[-1] if reason else f"git exited {result.returncode}"
        return [], [("INFO", LABEL, f"scan skipped - {reason}")]

    paths = []
    for rel in result.stdout.split("\0"):
        if not rel:
            continue
        ext = os.path.splitext(rel)[1].lower()
        if ext in TEXT_EXTS or os.path.basename(rel) in TEXT_NAMES:
            paths.append(root / rel)
    return paths, info


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
def run_check(root):
    markers, info = build_markers()
    findings = list(info)
    paths, walk_info = committable_files(root)
    findings.extend(walk_info)
    for path in paths:
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # unreadable / binary mislabelled - encoding-guard's remit
        findings.extend(scan_text(rel, text, markers))
    return findings


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def print_report(findings):
    counts = {"FAIL": 0, "WARN": 0, "INFO": 0}
    for sev, _, _ in findings:
        counts[sev] = counts.get(sev, 0) + 1
    print("# Personal Data Guard Report\n")
    print(f"**Failures:** {counts['FAIL']}  **Warnings:** {counts['WARN']}  "
          f"**Info:** {counts['INFO']}\n")
    if not findings:
        print("No personal data found in committable files.")
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Scan committable files for personal data (read-only)")
    parser.add_argument("--check", action="store_true",
                        help="Read-only scan (default; the only mode)")
    parser.add_argument("--json", action="store_true",
                        help="Emit findings as JSON on stdout (for the audit hook)")
    args = parser.parse_args()

    findings = run_check(PROJECT_ROOT)
    if args.json:
        print(findings_json(findings))
    else:
        print_report(findings)
    if any(s == "FAIL" for s, _, _ in findings):
        sys.exit(1)


if __name__ == "__main__":
    main()
