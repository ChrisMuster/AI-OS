#!/usr/bin/env python3
"""AI-style guard - catch AI writing tells in newly authored content.

Read-only check. There is deliberately no fix mode: replacing a typographic
marker (an em dash, say) needs human judgement - comma, hyphen, parentheses, or
full stop depending on the sentence, exactly as the AGENTS.md writing-style rule
describes - so auto-fixing would guess wrong. The guard only reports.

    python workflows/ai-style-guard/scripts/run.py --check [--json] [--since REF] [--strict]

Scope - added/changed lines only. The AGENTS.md writing-style rule applies
"going forward to new and edited content" and explicitly "does not require
rewriting existing text". AGENTS.md and SOUL.md are already full of grandfathered
em dashes, so a whole-file scan would flood findings on legacy content and be
useless. The guard therefore scans only:
  * lines added in ``git diff --unified=0 <ref>`` (default ref: HEAD, i.e. the
    working tree's uncommitted edits; the audit hook passes the branch's
    merge-base with main so it polices the whole branch's new content), and
  * every line of new untracked text files (entirely new content).
Because the scope is git diff content, the required exemptions come for free:
the journal, USER.md, conversations, and collections/raw are gitignored and so
never appear in a diff. The guard also skips its own workflow directory, whose
config and tests necessarily contain example tells.

Reports (both tiers advisory; there is no FAIL tier):
    WARN  a tier-1 high-confidence tell: a typographic marker (em/en dash, smart
          quote, ellipsis char, non-breaking space) or a stock AI phrase
    INFO  a tier-2 single-word denylist hit (advisory only - many of these words
          are legitimate in technical prose) and degraded/skip notes

The script source carries no literal markers or wordlist: typographic markers are
built from code points and the phrase/word lists are read at runtime from the
tracked config SSOT (config/ai-tells.yaml), so this file never flags itself.

Exit codes: 0 by default (purely advisory). With --strict, 1 when any WARN
exists, so a pre-commit hook or CI can gate on it. --json always prints payload.
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
_RUNTIME_SCRIPTS = PROJECT_ROOT / "workflows" / "biblio-tools" / "scripts"
sys.path.insert(0, str(_RUNTIME_SCRIPTS))
WORKFLOW_DIR = PROJECT_ROOT / "workflows" / "ai-style-guard"
CONFIG_FILE = WORKFLOW_DIR / "config" / "ai-tells.yaml"
# The guard does not police itself: its config and tests legitimately contain
# example tells. Paths under this prefix are skipped.
SELF_PREFIX = "workflows/ai-style-guard/"

Finding = tuple  # (severity, label, message)
LABEL = "ai-style"

# Only these extensions are read (git may track binaries; never decode them).
TEXT_EXTS = {
    ".md", ".py", ".json", ".toml", ".txt", ".js", ".ts", ".html", ".css",
    ".yml", ".yaml", ".cfg", ".ini", ".example", ".mdc", ".sh", ".bat",
    ".ps1", ".conf",
}


# ---------------------------------------------------------------------------
# Config (the single source of truth; no wordlist baked into this file)
# ---------------------------------------------------------------------------
def load_config(path):
    """Load the tells config. Returns (config, info).

    config = {
        "typographic": [(label, char), ...],   # tier 1 -> WARN
        "phrases":     [(label, regex), ...],   # tier 1 -> WARN
        "words":       [str, ...],              # tier 2 -> INFO
    }
    On a missing or unreadable config file the guard degrades to an empty ruleset
    plus a single INFO note rather than guessing, keeping one source of truth.
    PyYAML itself is never optional here: main() bootstraps into the project
    .venv via ensure_project_runtime() before this runs, so a guard whose whole
    job is the check can never silently skip for want of its parser.
    """
    empty = {"typographic": [], "phrases": [], "words": []}
    if not path.exists():
        return empty, [("INFO", LABEL,
                        f"config not found at {path.name}; nothing to check")]
    import yaml
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return empty, [("INFO", LABEL, f"config unreadable ({exc}); check skipped")]

    typographic = []
    for item in raw.get("typographic", []) or []:
        label = item.get("label", "marker")
        cp = item.get("codepoint")
        if cp is None:
            continue
        # codepoint may be an int (0x2014) or a hex string ("2014" / "U+2014").
        if isinstance(cp, str):
            cp = int(cp.lower().lstrip("u+"), 16)
        typographic.append((label, chr(cp)))

    phrases = []
    for item in raw.get("phrases", []) or []:
        text = item.get("text")
        if not text:
            continue
        label = item.get("label", text)
        # Collapse inner whitespace in the pattern so a phrase still matches
        # across a line wrap or doubled space; word-bounded, case-insensitive.
        pattern = r"\b" + r"\s+".join(re.escape(tok) for tok in text.split()) + r"\b"
        phrases.append((label, re.compile(pattern, re.IGNORECASE)))

    words = [w for w in (raw.get("words") or []) if w]
    return {"typographic": typographic, "phrases": phrases, "words": words}, []


def _word_re(term):
    return re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Detection (pure: one line of text in, findings out)
# ---------------------------------------------------------------------------
def scan_line(rel, lineno, text, config, word_res):
    """Return findings for a single added line. Pure - no filesystem access."""
    findings = []
    loc = f"{rel}:{lineno}"

    for label, char in config["typographic"]:
        if char in text:
            findings.append(("WARN", LABEL, f"{loc}: {label} present"))

    for label, rx in config["phrases"]:
        if rx.search(text):
            findings.append(("WARN", LABEL, f"{loc}: stock phrase `{label}`"))

    for term, rx in word_res:
        if rx.search(text):
            findings.append(("INFO", LABEL, f"{loc}: denylisted word `{term}`"))

    return findings


# ---------------------------------------------------------------------------
# Diff parsing (the one genuinely fiddly part; unit-tested with fixtures)
# ---------------------------------------------------------------------------
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def parse_diff(diff_text):
    """Parse ``git diff --unified=0`` output into added lines.

    Returns {relpath: [(new_lineno, line_text), ...]}. Only lines that begin
    with a single ``+`` (additions) are collected, with their line number in the
    new file tracked from each hunk header's ``+start`` field. Deletions and the
    ``+++``/``---`` file headers are ignored; a ``+++ /dev/null`` target (a file
    deletion) is skipped entirely.
    """
    added = {}
    current = None
    new_lineno = 0
    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            current = None
            continue
        if line.startswith("+++ "):
            target = line[4:].strip()
            if target == "/dev/null":
                current = None
            else:
                # Strip the "b/" prefix git uses for the new-side path.
                current = target[2:] if target.startswith("b/") else target
                added.setdefault(current, [])
            continue
        if line.startswith("--- "):
            continue
        m = HUNK_RE.match(line)
        if m:
            new_lineno = int(m.group(1))
            continue
        if current is None:
            continue
        if line.startswith("+"):
            added[current].append((new_lineno, line[1:]))
            new_lineno += 1
        elif line.startswith("-"):
            # Deletion: does not advance the new-file line counter.
            continue
        else:
            # With --unified=0 there are no context lines, but stay robust.
            new_lineno += 1
    return added


# ---------------------------------------------------------------------------
# Change collection (git diff + new untracked files)
# ---------------------------------------------------------------------------
def _git(root, args):
    """Run a git command, returning (ok, stdout, reason)."""
    try:
        result = subprocess.run(
            ["git", *args], capture_output=True, encoding="utf-8", cwd=str(root),
        )
    except Exception as exc:
        return False, "", str(exc)
    if result.returncode != 0:
        reason = (result.stderr or "").strip().splitlines()
        return False, "", (reason[-1] if reason else f"git exited {result.returncode}")
    return True, result.stdout, ""


def _eligible(rel):
    if rel.startswith(SELF_PREFIX):
        return False
    ext = os.path.splitext(rel)[1].lower()
    return ext in TEXT_EXTS


def collect_changes(root, since):
    """Return (changes, info) where changes = {relpath: [(lineno, text), ...]}.

    Combines added lines from ``git diff --unified=0 <since>`` with the full
    contents of new untracked text files (every line is new content). On any git
    failure, returns no changes and a single INFO note so the caller degrades.
    """
    ok, diff_out, reason = _git(
        root, ["diff", "--unified=0", "--no-color", since, "--"])
    if not ok:
        return {}, [("INFO", LABEL, f"scan skipped - {reason}")]

    changes = {rel: lines for rel, lines in parse_diff(diff_out).items()
               if _eligible(rel) and lines}

    # New untracked files: treat the whole file as added content.
    ok, untracked_out, _ = _git(
        root, ["ls-files", "--others", "--exclude-standard", "-z"])
    if ok:
        for rel in untracked_out.split("\0"):
            if not rel or not _eligible(rel):
                continue
            try:
                text = (root / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            lines = [(i, ln) for i, ln in enumerate(text.splitlines(), start=1)]
            if lines:
                changes.setdefault(rel, []).extend(lines)
    return changes, []


def merge_base(root, base):
    """Return the merge-base sha of ``base`` and HEAD, or None if unavailable.

    Used by the audit hook (``--base main``) so it polices the whole branch's
    new content. Falls back to None so the caller can default to HEAD on a repo
    with no ``main`` branch or no commits yet.
    """
    ok, out, _ = _git(root, ["merge-base", base, "HEAD"])
    if ok and out.strip():
        return out.strip()
    return None


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
def run_check(root, since):
    config, info = load_config(CONFIG_FILE)
    findings = list(info)
    word_res = [(w, _word_re(w)) for w in config["words"]]
    changes, walk_info = collect_changes(root, since)
    findings.extend(walk_info)
    for rel in sorted(changes):
        for lineno, text in changes[rel]:
            findings.extend(scan_line(rel, lineno, text, config, word_res))
    return findings


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def print_report(findings):
    counts = {"WARN": 0, "INFO": 0}
    for sev, _, _ in findings:
        counts[sev] = counts.get(sev, 0) + 1
    print("# AI-Style Guard Report\n")
    print(f"**Warnings:** {counts['WARN']}  **Info:** {counts['INFO']}\n")
    if not findings:
        print("No AI-style tells found in changed content.")
        return
    for sev in ("WARN", "INFO"):
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
        description="Scan added/changed lines for AI writing tells (read-only)")
    parser.add_argument("--check", action="store_true",
                        help="Read-only scan (default; the only mode)")
    parser.add_argument("--json", action="store_true",
                        help="Emit findings as JSON on stdout (for the audit hook)")
    parser.add_argument("--since", metavar="REF", default=None,
                        help="Diff base ref (default: HEAD - the uncommitted working tree)")
    parser.add_argument("--base", metavar="BRANCH", default=None,
                        help="Diff against the merge-base of BRANCH and HEAD "
                             "(the audit hook uses --base main to scan the whole branch)")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 if any WARN finding exists (for CI/pre-commit)")
    args = parser.parse_args()

    # This guard's job IS the check, so it must actually run - it must not
    # silently skip for want of PyYAML. Hand off to the canonical .venv where the
    # parser is guaranteed present; if the runtime is not set up,
    # ensure_project_runtime() fails loudly and names the setup.py fix.
    from runtime import ensure_project_runtime  # noqa: E402
    ensure_project_runtime()

    if args.since is not None:
        since = args.since
    elif args.base is not None:
        since = merge_base(PROJECT_ROOT, args.base) or "HEAD"
    else:
        since = "HEAD"
    findings = run_check(PROJECT_ROOT, since)
    if args.json:
        print(findings_json(findings))
    else:
        print_report(findings)
    if args.strict and any(s == "WARN" for s, _, _ in findings):
        sys.exit(1)


if __name__ == "__main__":
    main()
