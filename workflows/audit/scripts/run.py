#!/usr/bin/env python3
"""
run.py — Audit all directories in Book Dragon for structural compliance.

Checks every directory (excluding the project root and system dirs) for:
  - Missing CONTEXT.md
  - Missing LOG.md
  - Missing required sections in CONTEXT.md (standard-format dirs only)
  - Stale or inconsistent CONTEXT.md Last modified and Revision History metadata
  - Contents entries pointing to non-existent paths
  - Subdirectories that exist but are not listed in Contents

Also runs code hygiene checks across all Python scripts in the project:
  - strftime calls with time components but no timezone offset

A full audit additionally rebuilds and validates the structural knowledge graph
and merges its actionable (WARN/FAIL) findings under a `knowledge-graph` label,
so a graph regression surfaces in the same report. This is additive and advisory
(the exit code is unchanged) and reports a DEGRADED finding if the graph
cannot be validated. Use --no-graph to skip it; targeted --context mode never
validates the graph.

Usage (run from anywhere):
    python workflows/audit/scripts/run.py [--save] [--no-graph]
    python workflows/audit/scripts/run.py --context <directory> [<directory> ...]

Options:
    --save     Save the full report to workflows/audit/last-report.md
    --no-graph Skip the structural knowledge-graph validation (full mode only)
    --context  Check only the named directories and their CONTEXT.md metadata
"""

import os
import re
import sys
import json
import argparse
import subprocess
from pathlib import Path
from datetime import datetime

# A UTF-8 stdout/stderr so the report (which uses non-ASCII punctuation) never
# mojibakes when piped or redirected on Windows (the console default is cp1252).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
WORKFLOW_DIR = PROJECT_ROOT / "workflows" / "audit"
WORKFLOW_LOG = WORKFLOW_DIR / "LOG.md"
ROOT_LOG     = PROJECT_ROOT / "LOG.md"
KG_RUN_PY    = PROJECT_ROOT / "workflows" / "knowledge-graph" / "scripts" / "run.py"
ENCODING_RUN_PY = PROJECT_ROOT / "workflows" / "encoding-guard" / "scripts" / "run.py"
PERSONAL_RUN_PY = PROJECT_ROOT / "workflows" / "personal-data-guard" / "scripts" / "run.py"
AI_STYLE_RUN_PY = PROJECT_ROOT / "workflows" / "ai-style-guard" / "scripts" / "run.py"
DOC_SYNC_RUN_PY = PROJECT_ROOT / "workflows" / "doc-sync-guard" / "scripts" / "run.py"
SKILL_HARDENING_RUN_PY = PROJECT_ROOT / "workflows" / "skill-hardening-guard" / "scripts" / "run.py"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# Directories to skip entirely when walking the tree
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".claude"}

# Top-level directory names that indicate a project-root-relative path
TOP_LEVEL_DIRS = {"workflows", "wikis", "skills", "templates"}

# Line-count threshold for AGENTS.md — warn when exceeded
AGENTS_MD_LINE_THRESHOLD = 600

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

LAST_MODIFIED_RE = re.compile(r"^\*\*Last modified:\*\* (\d{4}-\d{2}-\d{2})$", re.MULTILINE)
REVISION_ENTRY_RE = re.compile(r"^- (\d{4}-\d{2}-\d{2})\s+.+$", re.MULTILINE)
ARCHIVE_REFERENCE_RE = re.compile(
    r"^Earlier history archived to LOG\.md on (\d{4}-\d{2}-\d{2})\.$",
    re.MULTILINE,
)
MAX_REVISION_HISTORY_ENTRIES = 15


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def now_ts() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


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
    with path.open("a", encoding="utf-8", newline="\n") as f:
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


def get_revision_history(content: str) -> str | None:
    """Return the Revision History body, or None when the section is absent."""
    match = re.search(
        r"^## Revision History\n(.*?)(?=^## |\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else None


def check_context_metadata(content: str) -> list[str]:
    """Check Last modified and Revision History consistency."""
    warnings = []
    last_modified_match = LAST_MODIFIED_RE.search(content)
    if not last_modified_match:
        warnings.append(
            "missing or malformed `**Last modified:** YYYY-MM-DD` line"
        )

    revision_history = get_revision_history(content)
    if revision_history is None:
        return warnings

    revision_dates = REVISION_ENTRY_RE.findall(revision_history)
    archive_dates = ARCHIVE_REFERENCE_RE.findall(revision_history)
    archive_lines = [
        line.strip()
        for line in revision_history.splitlines()
        if line.strip().startswith("Earlier history archived")
    ]

    if len(archive_lines) != len(archive_dates):
        warnings.append(
            "Revision History archive reference is malformed; expected "
            "`Earlier history archived to LOG.md on YYYY-MM-DD.`"
        )
    if len(archive_dates) > 1:
        warnings.append("Revision History contains more than one archive reference")

    visible_entries = len(revision_dates) + len(archive_dates)
    if visible_entries > MAX_REVISION_HISTORY_ENTRIES:
        warnings.append(
            f"Revision History has {visible_entries} visible entries "
            f"(maximum: {MAX_REVISION_HISTORY_ENTRIES})"
        )

    # Newest at the bottom (the CONTEXT.md schema). Entries are compared in the
    # order they appear, so an equal pair (a second entry the same day) is fine
    # and only a date older than the one above it is a violation. This is the
    # one schema rule that used to be discipline alone: a review caught an
    # out-of-order entry that had passed every automated check, and a sweep then
    # found two more elsewhere in the project.
    out_of_order = [
        (revision_dates[i - 1], revision_dates[i])
        for i in range(1, len(revision_dates))
        if revision_dates[i] < revision_dates[i - 1]
    ]
    if out_of_order:
        earlier, later = out_of_order[0]
        extra = (f" (and {len(out_of_order) - 1} more)"
                 if len(out_of_order) > 1 else "")
        warnings.append(
            f"Revision History is out of order: {later} follows {earlier}"
            f"{extra}; newest entry goes at the bottom"
        )

    nonblank_lines = [
        line.strip() for line in revision_history.splitlines() if line.strip()
    ]
    if archive_dates and (
        not nonblank_lines
        or not ARCHIVE_REFERENCE_RE.fullmatch(nonblank_lines[0])
    ):
        warnings.append(
            "Revision History archive reference must be the first entry"
        )

    metadata_dates = [*revision_dates, *archive_dates]
    if not metadata_dates:
        warnings.append("Revision History contains no dated entries")
    elif last_modified_match:
        newest_metadata_date = max(metadata_dates)
        last_modified = last_modified_match.group(1)
        if last_modified != newest_metadata_date:
            warnings.append(
                f"Last modified is {last_modified}, but the newest Revision "
                f"History date is {newest_metadata_date}"
            )

    return warnings


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
_LINK_ROOT_STEMS = {"AGENT-SETUP", "AGENTS", "CLAUDE", "GEMINI", "README", "USER", "SOUL"}


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
    """Return immediate subdirectories, excluding skip, hidden, and ignored dirs.

    Used by the unlisted-subdirectory check in targeted ``--context`` mode, where
    no precomputed directory set exists. A full audit does not call this: it feeds
    audit_directory the non-ignored children that collect_dirs already computed,
    avoiding a per-directory ``git check-ignore`` spawn.
    """
    candidates = [
        p for p in sorted(directory.iterdir())
        if p.is_dir()
        and p.name not in SKIP_DIRS
        and not p.name.startswith(".")
    ]
    ignored = _git_check_ignored(rel(directory), [p.name for p in candidates])
    return [p for p in candidates if p.name not in ignored]


# ---------------------------------------------------------------------------
# Shared type
# ---------------------------------------------------------------------------
Finding = tuple[str, str, str]  # (level, path_or_label, message)

# A check that could not run (a degrade) is reported at this level so it can
# never be mistaken for a pass. It stays advisory - like INFO it does not change
# the audit's exit code - but it is counted and shown in its own section, and the
# close-out verifier surfaces it distinctly and can act on it with --repair.
DEGRADED = "DEGRADED"
_REPAIR_HINT = (
    "Fix: run `python workflows/biblio-tools/scripts/setup.py` to repair the "
    "project runtime, then re-run."
)


def degraded(label: str, reason: str, repairable: bool = True,
             what: str = "check") -> Finding:
    """Build a DEGRADED finding for a check that could not run.

    A degrade means the check did not actually run, so it must be visible and
    carry its own remediation. ``repairable=True`` (a runtime/dependency reason
    setup.py can fix) appends the setup.py hint; ``repairable=False`` is for a
    genuinely absent guard whose workflow is missing, which setup.py cannot fix.

    ``what`` names the thing that did not run, and defaults to the whole check
    because that is the usual case. A guard whose main check completed while one
    component could not (the doc-sync inventory probe) passes its own noun, so
    the message does not claim more went wrong than actually did.
    """
    tail = _REPAIR_HINT if repairable else (
        f"The {label} workflow appears to be missing; reinstall or restore it.")
    return (DEGRADED, label, f"{label} {what} did not run - {reason}. {tail}")


# ---------------------------------------------------------------------------
# Code hygiene checks
# ---------------------------------------------------------------------------

def check_python_scripts() -> list[Finding]:
    """
    Check all Python scripts in the project for known code hygiene issues.

    Current checks:
      - strftime with time components (%H/%M/%S) but no timezone (%z/%Z).
        These produce log timestamps that violate the project log format standard.
        Fix: use datetime.now().astimezone().isoformat(timespec='seconds').
    """
    findings: list[Finding] = []

    for py_file in sorted(PROJECT_ROOT.rglob('*.py')):
        parts = py_file.relative_to(PROJECT_ROOT).parts
        if any(part in SKIP_DIRS or part.startswith('.') for part in parts):
            continue

        try:
            lines = py_file.read_text(encoding='utf-8', errors='ignore').splitlines()
        except Exception:
            continue

        file_label = rel(py_file)

        for lineno, line in enumerate(lines, 1):
            if '.strftime(' not in line:
                continue
            has_time = any(spec in line for spec in ('%H', '%M', '%S'))
            has_tz   = any(spec in line for spec in ('%z', '%Z'))
            if has_time and not has_tz:
                findings.append((
                    'WARN',
                    f'{file_label}:{lineno}',
                    'code hygiene — strftime with time but no timezone; '
                    'use datetime.now().astimezone().isoformat(timespec="seconds") '
                    'for log timestamps',
                ))

    return findings


# ---------------------------------------------------------------------------
# Knowledge-graph validation hook
# ---------------------------------------------------------------------------
# The full audit additionally rebuilds and validates the *structural* knowledge
# graph and merges its actionable findings, so a graph regression (a new broken
# reference, orphan, or uncontained directory) shows up in the same report the
# AI already reads at close-out. The hook is additive and advisory — it never
# changes the audit's exit code, and a missing or broken graph layer reports a
# DEGRADED finding rather than failing the audit.

def graph_findings(payload: dict) -> list[Finding]:
    """Map a knowledge-graph ``validate --json`` payload to audit Findings.

    Keeps only the actionable severities (WARN and FAIL). INFO findings —
    forward references and gitignored-artifact references — are dropped so the
    audit report stays a true to-do list. Each graph finding becomes a Finding
    labelled ``knowledge-graph`` with the node id carried inside the message,
    mirroring how the audit already renders its own findings.

    Pure (dict in, tuples out) so it can be unit-tested without a subprocess.
    """
    findings: list[Finding] = []
    for f in payload.get("findings", []):
        severity = f.get("severity")
        if severity not in ("WARN", "FAIL"):
            continue
        subject = f.get("subject", "")
        message = f.get("message", "")
        findings.append((severity, "knowledge-graph", f"`{subject}` — {message}"))
    return findings


def run_graph_validation() -> list[Finding]:
    """Rebuild and validate the structural knowledge graph, returning its
    actionable findings as audit Findings.

    Shells out to the knowledge-graph CLI (the contract) rather than importing
    it — both workflows ship a ``common.py``/``parser.py``, so importing would
    risk a module-name collision. Validates the *structural* graph only (never
    passes ``--layer``), so the merged findings carry no personal/gitignored
    names; ``--no-backrefs`` skips the advisory back-reference check (its gaps
    are INFO and dropped anyway).

    Degrades gracefully: on any failure (KG run.py missing, a crash, or
    unparseable output) it returns a single DEGRADED finding and never raises, so
    the audit always completes with exit 0.
    """
    if not KG_RUN_PY.exists():
        return [degraded("knowledge-graph", "knowledge-graph CLI not found",
                         repairable=False)]
    try:
        result = subprocess.run(
            [sys.executable, str(KG_RUN_PY), "validate", "--json", "--no-backrefs"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(PROJECT_ROOT),
        )
    except Exception as exc:
        return [degraded("knowledge-graph",
                         f"could not run validator ({exc})")]

    # validate exits 0 (clean) or 1 (a FAIL finding present) with the JSON
    # payload on stdout; an internal error exits 1 with an empty stdout and the
    # reason on stderr. Disambiguate by parsing stdout — a clean parse means we
    # have findings regardless of the exit code; a parse failure is the skip path.
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        reason = (result.stderr.strip().splitlines()
                  or [f"validator exited {result.returncode} with no JSON output"])[-1]
        return [degraded("knowledge-graph", reason)]
    return graph_findings(payload)


# ---------------------------------------------------------------------------
# Encoding-guard hook
# ---------------------------------------------------------------------------
# The full audit also runs the encoding-guard check and merges its actionable
# findings, so an encoding regression (a file that stops being valid UTF-8, new
# mojibake, CR line endings where the project requires LF, a text-mode
# subprocess call with no explicit encoding, a text-mode write with no explicit
# newline, or either of those arguments carrying a value the rule does not
# allow) surfaces in the same close-out report. Like the graph hook it is
# additive and advisory here - it never changes the audit's exit code, and both
# a missing or broken encoding-guard and an individual `.py` the guard could not
# parse report a DEGRADED finding. Downstream is where it differs: `encoding` is
# one of close-out's BLOCKING_LABELS, so a WARN merged here hard-fails that gate
# while a DEGRADED stays non-blocking.

def encoding_findings(payload: dict) -> list[Finding]:
    """Map an encoding-guard ``--check --json`` payload to audit Findings.

    Keeps the actionable severities (WARN and FAIL) and DEGRADED. The encoding-
    guard message already carries the file path. Pure (dict in, tuples out) so it
    can be unit-tested without a subprocess.

    DEGRADED here is a *file* the guard could not check - a `.py` that does not
    parse - rather than the guard failing to run, which is what
    :func:`run_encoding_check` reports through :func:`degraded`. It is passed
    through as-is (mirroring the skill-hardening hook, whose DEGRADED is an
    unreadable SKILL.md) so close-out routes it down the non-blocking DEGRADED
    path. Dropping it would be the wrong trade: an unchecked file would then look
    exactly like a clean one, which is the confusion this whole label exists to
    prevent. Mapping it to WARN would be worse still, since it would hard-fail
    close-out over a file nobody claims is broken.
    """
    findings: list[Finding] = []
    for f in payload.get("findings", []):
        severity = f.get("severity")
        if severity not in ("WARN", "FAIL", DEGRADED):
            continue
        findings.append((severity, "encoding", f.get("message", "")))
    return findings


def run_encoding_check() -> list[Finding]:
    """Run the encoding-guard check and return its actionable findings.

    Shells out to the encoding-guard CLI (the contract) rather than importing it,
    matching the graph hook. Degrades gracefully: on any failure the audit gets a
    single DEGRADED finding and never raises, so the exit code stays advisory.
    """
    if not ENCODING_RUN_PY.exists():
        return [degraded("encoding", "encoding-guard CLI not found",
                         repairable=False)]
    try:
        result = subprocess.run(
            [sys.executable, str(ENCODING_RUN_PY), "--check", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(PROJECT_ROOT),
        )
    except Exception as exc:
        return [degraded("encoding", f"could not run ({exc})")]
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        reason = (result.stderr.strip().splitlines()
                  or [f"checker exited {result.returncode} with no JSON output"])[-1]
        return [degraded("encoding", reason)]
    return encoding_findings(payload)


# ---------------------------------------------------------------------------
# Personal-data-guard hook
# ---------------------------------------------------------------------------
# The full audit also runs the personal-data guard and merges its actionable
# findings, so a personal-data leak in a committable file (an email address, a
# personal home path, the user's name, or a denylisted noun) surfaces in the same
# close-out report. Like the graph and encoding hooks it is additive and advisory:
# it never changes the audit's exit code, and a missing or broken guard
# reports a DEGRADED finding. The standalone CLI still exits 1 on a FAIL so a
# pre-commit hook or CI can gate on it directly.

def personal_findings(payload: dict) -> list[Finding]:
    """Map a personal-data-guard ``--check --json`` payload to audit Findings.

    Keeps only the actionable severities (WARN and FAIL); the guard's message
    already carries the file path. Pure (dict in, tuples out) so it can be
    unit-tested without a subprocess.
    """
    findings: list[Finding] = []
    for f in payload.get("findings", []):
        severity = f.get("severity")
        if severity not in ("WARN", "FAIL"):
            continue
        findings.append((severity, "personal-data", f.get("message", "")))
    return findings


def run_personal_data_check() -> list[Finding]:
    """Run the personal-data guard and return its actionable findings.

    Shells out to the guard CLI (the contract) rather than importing it, matching
    the graph and encoding hooks. Degrades gracefully: on any failure the audit
    gets a single DEGRADED finding and never raises, so the exit code stays
    advisory.
    """
    if not PERSONAL_RUN_PY.exists():
        return [degraded("personal-data", "personal-data-guard CLI not found",
                         repairable=False)]
    try:
        result = subprocess.run(
            [sys.executable, str(PERSONAL_RUN_PY), "--check", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(PROJECT_ROOT),
        )
    except Exception as exc:
        return [degraded("personal-data", f"could not run ({exc})")]
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        reason = (result.stderr.strip().splitlines()
                  or [f"checker exited {result.returncode} with no JSON output"])[-1]
        return [degraded("personal-data", reason)]
    return personal_findings(payload)


# ---------------------------------------------------------------------------
# AI-style-guard hook
# ---------------------------------------------------------------------------
# The full audit also runs the AI-style guard, scoped to the whole branch's new
# content (--base main, i.e. added lines since the merge-base with main), so an
# AI writing tell introduced on the branch surfaces in the same close-out report.
# Like the other content hooks it is additive and advisory: only the tier-1 WARN
# findings are merged (the tier-2 single-word denylist stays INFO and is dropped),
# it never changes the audit's exit code, and a missing or broken guard reports a
# DEGRADED finding. The standalone CLI can still gate via --strict.

def ai_style_findings(payload: dict) -> list[Finding]:
    """Map an ai-style-guard ``--check --json`` payload to audit Findings.

    Keeps only the actionable severity (WARN); the guard's message already
    carries file:line. Pure (dict in, tuples out) for unit testing.
    """
    findings: list[Finding] = []
    for f in payload.get("findings", []):
        if f.get("severity") != "WARN":
            continue
        findings.append(("WARN", "ai-style", f.get("message", "")))
    return findings


def run_ai_style_check() -> list[Finding]:
    """Run the AI-style guard over the branch and return its WARN findings.

    Shells out to the guard CLI (the contract) rather than importing it, matching
    the other hooks. Degrades gracefully: on any failure the audit gets a single
    DEGRADED finding and never raises, so the exit code stays advisory.
    """
    if not AI_STYLE_RUN_PY.exists():
        return [degraded("ai-style", "ai-style-guard CLI not found",
                         repairable=False)]
    try:
        result = subprocess.run(
            [sys.executable, str(AI_STYLE_RUN_PY),
             "--check", "--json", "--base", "main"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(PROJECT_ROOT),
        )
    except Exception as exc:
        return [degraded("ai-style", f"could not run ({exc})")]
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        reason = (result.stderr.strip().splitlines()
                  or [f"checker exited {result.returncode} with no JSON output"])[-1]
        return [degraded("ai-style", reason)]
    return ai_style_findings(payload)


# ---------------------------------------------------------------------------
# Doc-sync-guard hook
# ---------------------------------------------------------------------------
# The full audit also runs the doc-sync guard, scoped to the working tree (its
# default HEAD scope - NOT --base main), so CONTEXT.md / LOG.md drift in the
# current uncommitted body of work surfaces in the same close-out report. Working
# -tree scope is deliberate (plan R3-1): it keeps the guard's mtime-based LOG
# check honest and scopes to the current task rather than re-policing earlier,
# already-closed-out work on the branch. Like the other content hooks it is
# additive and advisory here - it never changes the audit's exit code, and a
# missing or broken guard degrades to a DEGRADED finding. The teeth are at
# close-out, which hard-fails on any doc-sync WARN (Option B) while treating a
# DEGRADED finding for the same label as non-blocking.

def doc_sync_findings(payload: dict) -> list[Finding]:
    """Map a doc-sync-guard ``--check --json`` payload to audit Findings.

    Two severities are actionable and they mean different things:

      * WARN is drift. The guard's message already names the directory and the
        reason, and close-out hard-fails on it.
      * DEGRADED is a component of the guard that could not run - today only its
        output-inventory probe, which needs PyYAML while the drift scan itself
        does not. It is re-wrapped through :func:`degraded` so it arrives with
        the same repair hint as every other unavailable-runtime report, and so
        close-out routes it down the non-blocking DEGRADED path. Mapping it to
        WARN instead would fail the build for a missing package while claiming
        documentation had drifted.

    Anything else is dropped. Pure (dict in, tuples out) for unit testing.
    """
    findings: list[Finding] = []
    for f in payload.get("findings", []):
        severity = f.get("severity")
        message = f.get("message", "")
        if severity == "WARN":
            findings.append(("WARN", "doc-sync", message))
        elif severity == DEGRADED:
            findings.append(degraded("doc-sync", message, what="inventory probe"))
    return findings


def run_doc_sync_check() -> list[Finding]:
    """Run the doc-sync guard over the working tree and return its findings.

    Shells out to the guard CLI (the contract) rather than importing it, matching
    the other hooks, and uses the guard's default HEAD scope (no --base). Degrades
    gracefully: on any failure the audit gets a DEGRADED finding and never raises,
    so the exit code stays advisory.
    """
    if not DOC_SYNC_RUN_PY.exists():
        return [degraded("doc-sync", "doc-sync-guard CLI not found",
                         repairable=False)]
    try:
        result = subprocess.run(
            [sys.executable, str(DOC_SYNC_RUN_PY), "--check", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(PROJECT_ROOT),
        )
    except Exception as exc:
        return [degraded("doc-sync", f"could not run ({exc})")]
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        reason = (result.stderr.strip().splitlines()
                  or [f"checker exited {result.returncode} with no JSON output"])[-1]
        return [degraded("doc-sync", reason)]
    return doc_sync_findings(payload)


# ---------------------------------------------------------------------------
# Skill-hardening-guard hook
# ---------------------------------------------------------------------------
# The full audit also runs the skill-hardening guard, so a SKILL.md that is
# missing its Hardening section, one of that section's five required fields, or
# its Verification section surfaces in the same close-out report. Like the other
# content hooks it is additive and advisory here - WARN gaps and DEGRADED
# unreadable-file findings are both merged, it never changes the audit's exit
# code, and a missing or broken guard degrades to a DEGRADED finding. The teeth
# are at close-out, which hard-fails on a skill-hardening WARN while treating a
# DEGRADED finding for the same label as non-blocking (mirroring doc-sync).

def skill_hardening_findings(payload: dict) -> list[Finding]:
    """Map a skill-hardening-guard ``--check --json`` payload to audit Findings.

    Keeps WARN (an actionable Hardening gap) and DEGRADED (a SKILL.md the guard
    could not read - non-blocking, surfaced distinctly and never a close-out hard
    fail). Other severities are dropped. The guard's message already names the
    SKILL.md and the gap. Pure (dict in, tuples out) for unit testing.
    """
    findings: list[Finding] = []
    for f in payload.get("findings", []):
        sev = f.get("severity")
        if sev not in ("WARN", "DEGRADED"):
            continue
        findings.append((sev, "skill-hardening", f.get("message", "")))
    return findings


def run_skill_hardening_check() -> list[Finding]:
    """Run the skill-hardening guard and return its WARN and DEGRADED findings.

    Shells out to the guard CLI (the contract) rather than importing it, matching
    the other hooks. Degrades gracefully: on any failure the audit gets a DEGRADED
    finding and never raises, so the exit code stays advisory.
    """
    if not SKILL_HARDENING_RUN_PY.exists():
        return [degraded("skill-hardening", "skill-hardening-guard CLI not found",
                         repairable=False)]
    try:
        result = subprocess.run(
            [sys.executable, str(SKILL_HARDENING_RUN_PY), "--check", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(PROJECT_ROOT),
        )
    except Exception as exc:
        return [degraded("skill-hardening", f"could not run ({exc})")]
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        reason = (result.stderr.strip().splitlines()
                  or [f"checker exited {result.returncode} with no JSON output"])[-1]
        return [degraded("skill-hardening", reason)]
    return skill_hardening_findings(payload)


# ---------------------------------------------------------------------------
# Per-directory audit
# ---------------------------------------------------------------------------


def audit_directory(directory: Path, subdirs: list[Path] | None = None) -> list[Finding]:
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

            # Last modified and Revision History consistency
            for msg in check_context_metadata(content):
                findings.append(("WARN", label, f"CONTEXT.md metadata — {msg}"))

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
                # In a full audit, collect_dirs already computed the non-ignored
                # immediate children (passed in as `subdirs`), so reuse that
                # instead of re-spawning git here. In targeted --context mode no
                # such set exists, so fall back to a direct per-dir git check.
                immediate = (
                    subdirs if subdirs is not None
                    else get_immediate_subdirs(directory)
                )
                for subdir in immediate:
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
NO_RECURSE_DIRS = {"raw", "data"}


# ---------------------------------------------------------------------------
# Gitignore helpers
# ---------------------------------------------------------------------------
def _git_check_ignored(parent_rel: str, subdirs: list[str]) -> set[str]:
    """Return the subset of subdirs that git would ignore.

    Passes paths as command-line arguments to ``git check-ignore`` in a
    single subprocess call.  Falls back to an empty set if git is
    unavailable or the project is not a repository.
    """
    if not subdirs:
        return set()

    if parent_rel == ".":
        candidates = {d: d for d in subdirs}
    else:
        candidates = {d: f"{parent_rel}/{d}" for d in subdirs}

    try:
        result = subprocess.run(
            ["git", "check-ignore", *candidates.values()],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(PROJECT_ROOT),
        )
        ignored_paths = {line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()}
        return {d for d, path in candidates.items() if path in ignored_paths}
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# Full tree walk
# ---------------------------------------------------------------------------
# Speed note: spawning ``git check-ignore`` is the dominant cost of a full audit
# on Windows (~0.4s per spawn). Three optimisations preserve the ignore semantics
# exactly while cutting that cost:
#   A. _is_git_worktree skips git entirely when the tree is not a repository
#      (e.g. a temporary test fixture) - zero subprocess in that case.
#   B. collect_dirs walks breadth-first and batches one ``git check-ignore``
#      call per depth level instead of one per directory, so the call count
#      drops from "directories with children" to "tree depth".
#   C. run_audit indexes collect_dirs' non-ignored result by parent and feeds it
#      to each audit_directory call, so the unlisted-subdirectory check reuses
#      that set instead of re-spawning git once per directory.
def _is_git_worktree(root: Path) -> bool:
    """True if ``root`` is inside a Git worktree (a ``.git`` exists at root or an
    ancestor). A filesystem check mirroring git's own repo discovery, so a
    non-repo tree (e.g. a temporary test fixture) costs no subprocess at all."""
    try:
        resolved = root.resolve()
    except OSError:
        return False
    for d in (resolved, *resolved.parents):
        if (d / ".git").exists():
            return True
    return False


def _git_check_ignored_batch(root: Path, rel_paths: list[str]) -> set[str]:
    """Return the subset of ``rel_paths`` (project-root-relative, forward slash)
    that git would ignore, in a single ``git check-ignore`` call.

    Uses NUL-separated stdin/stdout (``-z``) and raw bytes so that Windows
    newline translation cannot corrupt the piped paths. Empty set on any git
    failure, matching the fall-back behaviour of the per-call helper above.
    """
    if not rel_paths:
        return set()
    try:
        result = subprocess.run(
            ["git", "check-ignore", "--stdin", "-z"],
            input="\0".join(rel_paths).encode("utf-8"),
            capture_output=True,
            cwd=str(root),
        )
        out = result.stdout.decode("utf-8", errors="replace")
        return {p.replace("\\", "/") for p in out.split("\0") if p.strip()}
    except Exception:
        return set()


def collect_dirs() -> list[Path]:
    """
    Walk the project tree and return all auditable directories (sorted),
    excluding the project root itself.

    Breadth-first so each depth level's gitignore check is a single batched
    ``git check-ignore`` call rather than one subprocess per directory. Skip,
    hidden, gitignored, and no-recurse directories are pruned before descending,
    so large data trees such as collections/ are never entered. When the tree is
    not a Git worktree (e.g. a temporary test fixture), git is skipped entirely.

    The directory set this returns must stay identical to the previous os.walk
    implementation: same skip/hidden/gitignore pruning, no-recurse directories
    recorded but not descended, root excluded, output sorted.
    """
    is_repo = _is_git_worktree(PROJECT_ROOT)
    out: list[Path] = []
    current: list[Path] = [PROJECT_ROOT]

    while current:
        # Gather every candidate child across this whole level, after the cheap
        # skip/hidden pruning, so the gitignore check is one batched call.
        level: list[tuple[Path, str]] = []
        for parent in current:
            try:
                names = sorted(
                    e.name for e in os.scandir(parent)
                    if e.is_dir()
                    and e.name not in SKIP_DIRS
                    and not e.name.startswith(".")
                )
            except OSError:
                continue
            for name in names:
                child = parent / name
                rel = child.relative_to(PROJECT_ROOT).as_posix()
                level.append((child, rel))

        ignored: set[str] = set()
        if is_repo and level:
            ignored = _git_check_ignored_batch(PROJECT_ROOT, [rel for _, rel in level])

        next_level: list[Path] = []
        for child, rel in level:
            if rel in ignored:
                continue
            out.append(child)
            # No-recurse directories are recorded but never descended into.
            if child.name not in NO_RECURSE_DIRS:
                next_level.append(child)
        current = next_level

    return sorted(out)


def run_audit(with_graph: bool = True) -> tuple[list[Finding], int]:
    dirs = collect_dirs()
    findings: list[Finding] = []

    # collect_dirs already determined, for the whole tree, which immediate
    # children survived the skip/hidden/gitignore filter. Index that result by
    # parent so the per-directory unlisted-subdirectory check can reuse it
    # instead of re-spawning `git check-ignore` once per directory. (NO_RECURSE
    # dirs map to an empty list, which is harmless: their unlisted check is
    # skipped anyway.)
    children_by_parent: dict[Path, list[Path]] = {}
    for d in dirs:
        children_by_parent.setdefault(d.parent, []).append(d)

    # Check AGENTS.md line count
    agents_md = PROJECT_ROOT / "AGENTS.md"
    if agents_md.exists():
        line_count = len(agents_md.read_text(encoding="utf-8").splitlines())
        if line_count > AGENTS_MD_LINE_THRESHOLD:
            findings.append((
                "WARN", "AGENTS.md",
                f"AGENTS.md has {line_count} lines (threshold: {AGENTS_MD_LINE_THRESHOLD})"
                " — review and reorganise: extract rarely-used detail into rules/ and replace with pointers"
            ))

    for d in dirs:
        findings.extend(audit_directory(d, children_by_parent.get(d, [])))

    # Code hygiene checks across all Python scripts
    findings.extend(check_python_scripts())

    # Structural knowledge-graph validation (full mode only; opt out with --no-graph)
    if with_graph:
        findings.extend(run_graph_validation())

    # Encoding hygiene check (full mode only; always runs, advisory)
    findings.extend(run_encoding_check())

    # Personal-data leak check (full mode only; always runs, advisory)
    findings.extend(run_personal_data_check())

    # AI-style tell check (full mode only; always runs, advisory)
    findings.extend(run_ai_style_check())

    # CONTEXT.md / LOG.md drift check (full mode only; advisory here, working-tree
    # scope; close-out hard-fails on a doc-sync WARN, while a doc-sync DEGRADED
    # is non-blocking and routes down its DEGRADED path)
    findings.extend(run_doc_sync_check())

    # SKILL.md Hardening-section check (full mode only; advisory here; close-out
    # hard-fails on a skill-hardening WARN, while a skill-hardening DEGRADED is
    # non-blocking and routes down its DEGRADED path)
    findings.extend(run_skill_hardening_check())

    return findings, len(dirs)


def resolve_context_targets(raw_targets: list[str]) -> list[Path]:
    """Resolve project-relative directory or CONTEXT.md targets safely."""
    targets = []
    for raw in raw_targets:
        candidate = (PROJECT_ROOT / raw).resolve()
        try:
            candidate.relative_to(PROJECT_ROOT.resolve())
        except ValueError as exc:
            raise ValueError(f"Context target is outside the project: {raw}") from exc

        directory = candidate.parent if candidate.name == "CONTEXT.md" else candidate
        if not directory.is_dir():
            raise ValueError(f"Context target is not a directory: {raw}")
        targets.append(directory)
    return sorted(set(targets))


def run_context_audit(raw_targets: list[str]) -> tuple[list[Finding], int]:
    """Audit only the explicitly named context directories."""
    targets = resolve_context_targets(raw_targets)
    findings = []
    for directory in targets:
        findings.extend(audit_directory(directory))
    return findings, len(targets)


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------
def format_report(findings: list[Finding], dir_count: int) -> str:
    run_at = datetime.now().astimezone().isoformat(timespec='seconds')

    fails = [f for f in findings if f[0] == "FAIL"]
    warns = [f for f in findings if f[0] == "WARN"]
    degradeds = [f for f in findings if f[0] == DEGRADED]
    infos = [f for f in findings if f[0] == "INFO"]

    lines = [
        "# Book Dragon — Audit Report",
        "",
        f"**Run at:** {run_at}",
        f"**Directories checked:** {dir_count}",
        f"**Failures:** {len(fails)}",
        f"**Warnings:** {len(warns)}",
        f"**Degraded (did not run):** {len(degradeds)}",
        f"**Info:** {len(infos)}",
        "",
    ]

    if not fails and not warns and not degradeds:
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

        if degradeds:
            lines += ["## Degraded (did not run)", ""]
            for _, path, msg in degradeds:
                lines.append(f"- `{path}` - {msg}")
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
    parser.add_argument(
        "--context",
        nargs="+",
        metavar="DIRECTORY",
        help="Check only the named project-relative directories or CONTEXT.md files",
    )
    parser.add_argument(
        "--no-graph",
        action="store_true",
        help="Skip the structural knowledge-graph validation in a full audit "
             "(no effect in --context mode, which never validates the graph)",
    )
    args = parser.parse_args()

    ts = now_ts()

    if args.context:
        start_note = (
            "Running targeted CONTEXT.md maintenance audit for: "
            + ", ".join(args.context)
            + "."
        )
    else:
        start_note = "Running structural audit of all project directories."
    append_log(WORKFLOW_LOG, ts, "started", start_note)

    try:
        if args.context:
            findings, dir_count = run_context_audit(args.context)
        else:
            findings, dir_count = run_audit(with_graph=not args.no_graph)
    except ValueError as exc:
        note = f"Audit failed: {exc}"
        append_log(WORKFLOW_LOG, ts, "failed", note)
        append_log(ROOT_LOG, ts, "failed", f"audit workflow failed. {exc}")
        parser.error(str(exc))

    report = format_report(findings, dir_count)

    print(report)

    if args.save:
        report_path = WORKFLOW_DIR / "last-report.md"
        with report_path.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(report)
        print(f"Report saved to workflows/audit/last-report.md")

    fails = sum(1 for f in findings if f[0] == "FAIL")
    warns = sum(1 for f in findings if f[0] == "WARN")
    degradeds = sum(1 for f in findings if f[0] == DEGRADED)
    mode = "targeted context audit" if args.context else "audit"
    note = (
        f"{mode.capitalize()} complete. {dir_count} directories checked. "
        f"{fails} failure(s), {warns} warning(s), {degradeds} degraded."
    )

    append_log(WORKFLOW_LOG, ts, "completed", note)
    append_log(ROOT_LOG, ts, "completed", f"audit workflow ran. {note}")


if __name__ == "__main__":
    main()
