#!/usr/bin/env python3
"""
run.py — Audit all directories in Book Dragon for structural compliance.

Checks every directory (excluding the project root and system dirs) for:
  - Missing CONTEXT.md
  - Missing LOG.md
  - Missing required sections in CONTEXT.md (standard-format dirs only)
  - Contents entries pointing to non-existent paths
  - Subdirectories that exist but are not listed in Contents

Usage (run from anywhere):
    python workflows/audit/scripts/run.py [--save]

Options:
    --save    Save the full report to workflows/audit/last-report.md
"""

import re
import argparse
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
WORKFLOW_DIR = PROJECT_ROOT / "workflows" / "audit"
WORKFLOW_LOG = WORKFLOW_DIR / "LOG.md"
ROOT_LOG     = PROJECT_ROOT / "LOG.md"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# Directories to skip entirely when walking the tree
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".claude"}

# Top-level directory names that indicate a project-root-relative path
TOP_LEVEL_DIRS = {"workflows", "wikis", "skills", "templates"}

# Line-count threshold for CLAUDE.md — warn when exceeded
CLAUDE_MD_LINE_THRESHOLD = 600

# Required sections in every standard-format CONTEXT.md
REQUIRED_SECTIONS = [
    "Purpose",
    "Contents",
    "Inputs",
    "Outputs",
    "Steps",
    "Dependencies",
    "Known Issues",
    "Revision History",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def now_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def rel(path: Path) -> str:
    """Return path relative to project root with forward slashes."""
    try:
        return str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def append_log(path: Path, ts: str, action: str, note: str) -> None:
    entry = f"[{ts}] | Actor: Biblio | Action: {action} | Note: {note}\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(entry)


# ---------------------------------------------------------------------------
# CONTEXT.md analysis helpers
# ---------------------------------------------------------------------------
def is_wiki_format(content: str) -> bool:
    """
    Returns True if this CONTEXT.md uses the special LLM Wiki format
    (Andrej Karpathy pattern) rather than the standard Book Dragon schema.
    Detected by '## Folder structure' appearing as an actual section header
    (line-start match), not just mentioned in prose.
    """
    return bool(re.search(r"^## Folder structure", content, re.MULTILINE))


def get_sections(content: str) -> set[str]:
    """Return all ## section names found in a markdown file."""
    return {m.group(1).strip() for m in re.finditer(r"^## (.+)$", content, re.MULTILINE)}


def get_contents_paths(content: str) -> list[str]:
    """
    Extract backtick-quoted paths from the Contents section.
    Only returns paths that start with a known top-level directory,
    which means they are definitively project-root-relative and checkable.
    """
    match = re.search(
        r"^## Contents\n(.*?)(?=^## |\Z)", content, re.MULTILINE | re.DOTALL
    )
    if not match:
        return []

    section_text = match.group(1)
    candidates = re.findall(r"`([^`]+)`", section_text)

    checkable = []
    for c in candidates:
        parts = Path(c.rstrip("/")).parts
        if parts and parts[0] in TOP_LEVEL_DIRS:
            checkable.append(c)
    return checkable


# Stale build-phase phrases that should not appear in a finished CONTEXT.md.
# Each tuple: (regex_pattern, human-readable label for the warning message).
STALE_PHRASES = [
    (r'future steps?',   '"future step(s)" — forward reference that may be stale'),
    (r'will be added',   '"will be added" — planned work that may now be complete'),
    (r'added in later',  '"added in later" — build-phase language that may be stale'),
    (r'\bTODO\b',        'TODO marker — unresolved item'),
]

# Top-level directory names used to identify project [[links]] vs wiki-internal ones
_LINK_TOP_LEVEL = {"workflows", "wikis", "skills", "templates", "journal"}
# Root file stems we link (LOG excluded)
_LINK_ROOT_STEMS = {"CLAUDE", "README", "USER", "SOUL"}


def strip_code_blocks(content: str) -> str:
    """Remove fenced code blocks (``` ... ```) so checks ignore example code."""
    return re.sub(r'```.*?```', '', content, flags=re.DOTALL)


def strip_revision_history(content: str) -> str:
    """Remove the Revision History section from content before checking.

    Revision History entries legitimately reference historical TODOs, past build
    steps, and path notation in descriptive context. Checking them produces
    false positives for the stale-phrase and ../ checks.
    """
    return re.sub(r'^## Revision History.*', '', content, flags=re.MULTILINE | re.DOTALL)


def get_checkable_content(content: str) -> str:
    """Return content cleaned for stale-phrase and path checks.

    Strips fenced code blocks and the Revision History section — both are
    legitimate sources of pattern matches that are not actually problems.
    """
    return strip_revision_history(strip_code_blocks(content))


def check_dead_links(content: str) -> list[str]:
    """
    Return a warning message for each [[link]] in the file that points to a
    non-existent .md file. Only checks project-path links (those starting with
    a known top-level directory or matching a root file stem) — wiki-internal
    links such as [[page-name]] are ignored.
    """
    warnings = []
    for match in re.finditer(r"\[\[([^\]]+)\]\]", content):
        raw = match.group(1)
        target = raw.split("|")[0].strip()  # strip display alias if present

        # Determine if this is a project link worth checking
        if "/" in target:
            if target.split("/")[0] not in _LINK_TOP_LEVEL:
                continue
        elif target not in _LINK_ROOT_STEMS:
            continue

        full = PROJECT_ROOT / (target + ".md")
        if not full.exists():
            warnings.append(f"dead [[link]] — [[{target}]] points to a non-existent file")

    return warnings


def check_stale_phrases(content: str) -> list[str]:
    """Return a warning message for each stale build phrase found in checkable content."""
    clean = get_checkable_content(content)
    found = []
    for pattern, label in STALE_PHRASES:
        if re.search(pattern, clean, re.IGNORECASE):
            found.append(f'possible stale content — {label}')
    return found


def check_parent_relative_paths(content: str) -> bool:
    """Return True if ../ appears in checkable content (outside code blocks and Revision History)."""
    clean = get_checkable_content(content)
    return bool(re.search(r'\.\./', clean))


def get_listed_subdir_names(content: str) -> set[str]:
    """
    Return the set of subdirectory/file names mentioned anywhere in the
    Contents section text. Used to check for unlisted subdirectories.
    """
    match = re.search(
        r"^## Contents\n(.*?)(?=^## |\Z)", content, re.MULTILINE | re.DOTALL
    )
    if not match:
        return set()

    section_text = match.group(1)
    # Extract all backtick-quoted strings and pull out the last path component
    names = set()
    for raw in re.findall(r"`([^`]+)`", section_text):
        name = Path(raw.rstrip("/")).name
        if name:
            names.add(name)
    # Also grab plain text names that might not be in backticks
    # (e.g. bare filenames in descriptive text)
    for word in re.findall(r"\b([\w-]+\.[\w]+|[\w-]+/)\b", section_text):
        names.add(word.rstrip("/"))
    return names


def get_immediate_subdirs(directory: Path) -> list[Path]:
    """Return immediate subdirectories, excluding skip dirs and hidden dirs."""
    return [
        p for p in sorted(directory.iterdir())
        if p.is_dir()
        and p.name not in SKIP_DIRS
        and not p.name.startswith(".")
    ]


# ---------------------------------------------------------------------------
# Per-directory audit
# ---------------------------------------------------------------------------
Finding = tuple[str, str, str]  # (level, dir_rel, message)


def audit_directory(directory: Path) -> list[Finding]:
    findings: list[Finding] = []
    label = rel(directory) + "/"

    context_path = directory / "CONTEXT.md"
    log_path     = directory / "LOG.md"

    # --- Structural: required files ---
    if not context_path.exists():
        findings.append(("FAIL", label, "Missing CONTEXT.md"))
    else:
        content = context_path.read_text(encoding="utf-8")

        if is_wiki_format(content):
            findings.append((
                "INFO", label,
                "Non-standard wiki CONTEXT.md (LLM Wiki format) — standard section checks skipped"
            ))
        else:
            # Required sections
            sections = get_sections(content)
            for section in REQUIRED_SECTIONS:
                if section not in sections:
                    findings.append(("WARN", label, f"CONTEXT.md is missing section: ## {section}"))

            # Contents path existence
            for p in get_contents_paths(content):
                full = PROJECT_ROOT / p.rstrip("/")
                if not full.exists():
                    findings.append(("WARN", label, f"Contents lists `{p}` but path does not exist"))

            # Parent-relative paths (../) anywhere outside code blocks
            if check_parent_relative_paths(content):
                findings.append(("WARN", label,
                    "CONTEXT.md contains ../ — use project-root-relative paths instead"))

            # Stale build-phase language outside code blocks
            for msg in check_stale_phrases(content):
                findings.append(("WARN", label, f"CONTEXT.md — {msg}"))

            # Dead [[links]]
            for msg in check_dead_links(content):
                findings.append(("WARN", label, f"CONTEXT.md — {msg}"))

            # Unlisted subdirectories
            # Skip for source-data boundary directories (e.g. raw/) — they
            # hold imported files and are not required to enumerate their contents.
            if directory.name not in NO_RECURSE_DIRS:
                listed = get_listed_subdir_names(content)
                for subdir in get_immediate_subdirs(directory):
                    if subdir.name not in listed:
                        findings.append((
                            "WARN", label,
                            f"Subdirectory `{subdir.name}/` exists but is not listed in Contents"
                        ))

    if not log_path.exists():
        findings.append(("FAIL", label, "Missing LOG.md"))

    return findings


# Directories that are checked themselves but whose contents are not recursed into.
# 'raw' directories hold immutable source data, not Book Dragon directories.
NO_RECURSE_DIRS = {"raw"}


# ---------------------------------------------------------------------------
# Full tree walk
# ---------------------------------------------------------------------------
def collect_dirs() -> list[Path]:
    """
    Walk the project tree and return all auditable directories.

    Directories named in NO_RECURSE_DIRS are included themselves but their
    contents are not walked — they are source-data boundaries.
    """
    dirs = []
    for p in sorted(PROJECT_ROOT.rglob("*")):
        if not p.is_dir():
            continue
        if p == PROJECT_ROOT:
            continue

        parts = p.relative_to(PROJECT_ROOT).parts

        # Skip if any component is in SKIP_DIRS or is hidden
        if any(part in SKIP_DIRS or part.startswith(".") for part in parts):
            continue

        # Skip if any ANCESTOR component is a no-recurse dir
        # (keeps raw/ itself but drops everything inside it)
        if any(part in NO_RECURSE_DIRS for part in parts[:-1]):
            continue

        dirs.append(p)
    return dirs


def run_audit() -> tuple[list[Finding], int]:
    dirs = collect_dirs()
    findings: list[Finding] = []

    # Check CLAUDE.md line count
    claude_md = PROJECT_ROOT / "CLAUDE.md"
    if claude_md.exists():
        line_count = len(claude_md.read_text(encoding="utf-8").splitlines())
        if line_count > CLAUDE_MD_LINE_THRESHOLD:
            findings.append((
                "WARN", "CLAUDE.md",
                f"CLAUDE.md has {line_count} lines (threshold: {CLAUDE_MD_LINE_THRESHOLD})"
                " — review and reorganise: extract rarely-used detail into rules/ and replace with pointers"
            ))

    for d in dirs:
        findings.extend(audit_directory(d))
    return findings, len(dirs)


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------
def format_report(findings: list[Finding], dir_count: int) -> str:
    run_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    fails = [f for f in findings if f[0] == "FAIL"]
    warns = [f for f in findings if f[0] == "WARN"]
    infos = [f for f in findings if f[0] == "INFO"]

    lines = [
        "# Book Dragon — Audit Report",
        "",
        f"**Run at:** {run_at}",
        f"**Directories checked:** {dir_count}",
        f"**Failures:** {len(fails)}",
        f"**Warnings:** {len(warns)}",
        f"**Info:** {len(infos)}",
        "",
    ]

    if not fails and not warns:
        lines += ["All structural checks passed.", ""]
    else:
        if fails:
            lines += ["## Failures", ""]
            for _, path, msg in fails:
                lines.append(f"- `{path}` — {msg}")
            lines.append("")

        if warns:
            lines += ["## Warnings", ""]
            for _, path, msg in warns:
                lines.append(f"- `{path}` — {msg}")
            lines.append("")

    if infos:
        lines += ["## Info", ""]
        for _, path, msg in infos:
            lines.append(f"- `{path}` — {msg}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit Book Dragon directory structure for compliance."
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save the full report to workflows/audit/last-report.md",
    )
    args = parser.parse_args()

    ts = now_ts()

    append_log(WORKFLOW_LOG, ts, "started", "Running structural audit of all project directories.")

    findings, dir_count = run_audit()
    report = format_report(findings, dir_count)

    print(report)

    if args.save:
        report_path = WORKFLOW_DIR / "last-report.md"
        report_path.write_text(report, encoding="utf-8")
        print(f"Report saved to workflows/audit/last-report.md")

    fails = sum(1 for f in findings if f[0] == "FAIL")
    warns = sum(1 for f in findings if f[0] == "WARN")
    note = f"Audit complete. {dir_count} directories checked. {fails} failure(s), {warns} warning(s)."

    append_log(WORKFLOW_LOG, ts, "completed", note)
    append_log(ROOT_LOG, ts, "completed", f"audit workflow ran. {note}")


if __name__ == "__main__":
    main()
