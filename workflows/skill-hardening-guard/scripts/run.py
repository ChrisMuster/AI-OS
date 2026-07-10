#!/usr/bin/env python3
"""Skill-hardening guard - every SKILL.md must carry a complete Hardening section.

Read-only structural check in the guard family (same shape as `encoding-guard`,
`personal-data-guard`, `ai-style-guard`, and `doc-sync-guard`): a standalone CLI
the full audit and the close-out verifier consume.

    python workflows/skill-hardening-guard/scripts/run.py --check [--json] [--strict]

What it enforces (AGENTS.md "Workflow-scoped skills"): the Hardening section is a
declarative safety envelope, not runtime enforcement. Runtime per-skill tool
restriction is not portable across Book Dragon's AI-agnostic markdown-skill model,
so the portable, checkable deliverable is that every SKILL.md documents its blast
radius in a `## Hardening` section with all five required fields non-empty:

    Allowed tool intent, Never, Approval-gated, Write boundaries,
    Verification / escape hatch

The guard validates presence and shape only. It never inspects whether the
declared policy is true - a wrong-but-present Hardening section still passes,
exactly as doc-sync validates that a Revision History entry exists without judging
its prose. There is deliberately no fix mode: the author knows the skill's real
blast radius; a context-free script could only produce filler.

It also enforces the other required load-bearing SKILL.md section: every SKILL.md
must carry a non-empty `## Verification` section (the caller-facing statement of how
to confirm the skill's output is correct, required by the schema and by the
Verification-discipline rule). Both checks report under the one `skill-hardening`
label, so the close-out gate and the audit hook pick up a missing Verification
section with no extra wiring.

Scope - every SKILL.md on disk under `skills/` and `workflows/`, excluding the
guard's own directory (whose test fixtures carry deliberate gaps) and any
`archived/` path (retired skills are not held to the live schema). This is a
whole-tree structural invariant, not a diff-scoped content check, so it needs no
git and reports the same result regardless of what changed.

Reports (one advisory tier):
    WARN  a SKILL.md with a missing Hardening section, a missing required field,
          an empty field, or a field left as an unfilled template placeholder;
          or a missing, empty, or unfilled-placeholder `## Verification` section

Exit codes: 0 by default (advisory, matching the other content guards). With
--strict, 1 when any WARN exists, so the close-out gate or a CI step can gate on
it. --json always prints the findings payload.
"""

import argparse
import json
import os
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

LABEL = "skill-hardening"
Finding = tuple  # (severity, label, message)

# Directories to walk for SKILL.md files. Skills live either at the top level
# (skills/<name>/SKILL.md) or inside a workflow (workflows/<wf>/skills/<name>/SKILL.md).
SKILL_ROOTS = ("skills", "workflows")

# The guard does not police itself: its tests hold fixture SKILL.md files with
# deliberate gaps. Paths under this prefix are skipped.
SELF_PREFIX = "workflows/skill-hardening-guard/"

# Retired skills under an archived/ path are not held to the live schema.
ARCHIVED_SEGMENT = "archived"

# Directories never walked into.
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".claude"}

# The five required Hardening fields, in canonical order. These strings must match
# the bold field labels in templates/SKILL.md.template exactly.
REQUIRED_FIELDS = [
    "Allowed tool intent",
    "Never",
    "Approval-gated",
    "Write boundaries",
    "Verification / escape hatch",
]

# The template's own {{TOKEN}} placeholder syntax. Unambiguous (it never appears
# in real content), so a match anywhere in a field means the field is unfilled.
_BRACE_PLACEHOLDER_RE = re.compile(r"\{\{[^}]+\}\}")
# A bare angle-bracket stub such as <fill in> or <describe> that is the WHOLE
# field value. Only a whole-value match counts: an angle token embedded in real
# prose (e.g. a `reviews/<label>.md` path pattern) is legitimate content, not a
# stub, so it must not be flagged.
_ANGLE_STUB_RE = re.compile(r"^<[^>\n]+>$")


def _is_unfilled(content):
    """True when a field's (already-stripped) content is an unfilled placeholder:
    the ``{{TOKEN}}`` template syntax anywhere, or a bare ``<stub>`` that is the
    entire value."""
    if _BRACE_PLACEHOLDER_RE.search(content):
        return True
    return bool(_ANGLE_STUB_RE.match(content))


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def find_skill_files(root):
    """Return the sorted list of SKILL.md paths under the skill roots.

    Prunes the guard's own directory, any archived/ path, hidden directories,
    and the standard non-project directories from the walk *before descending*,
    so it never enters large ignored or source-data subtrees (and so an
    incidental ``SKILL.md`` inside one is never found or flagged). Only files
    named exactly ``SKILL.md`` match, so the ``SKILL.md.template`` in templates/
    is never picked up.
    """
    found = []
    self_rel = SELF_PREFIX.rstrip("/")
    for base in SKILL_ROOTS:
        base_dir = root / base
        if not base_dir.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(base_dir):
            # Prune before descent: drop skipped, hidden, `_`-prefixed
            # (private/scratch), archived, and the guard's own directory so
            # os.walk never enters them. The guard is deliberately no-git, so a
            # leading `_` is the portable "this is local/out-of-scope" signal in
            # place of consulting .gitignore.
            kept = []
            for d in dirnames:
                if (d in SKIP_DIRS or d.startswith(".") or d.startswith("_")
                        or d == ARCHIVED_SEGMENT):
                    continue
                child_rel = (Path(dirpath) / d).relative_to(root).as_posix()
                if child_rel == self_rel:
                    continue
                kept.append(d)
            dirnames[:] = kept
            if "SKILL.md" in filenames:
                found.append(Path(dirpath) / "SKILL.md")
    return sorted(found)


# ---------------------------------------------------------------------------
# Parsing (pure: text in, findings out)
# ---------------------------------------------------------------------------
def strip_code_blocks(text):
    """Remove fenced code blocks (``` ... ```) so an example ``## Hardening``
    documented inside a fence is never mistaken for the skill's real section
    (and a fenced line beginning with ``## `` cannot truncate it). Mirrors the
    audit's strip_code_blocks.
    """
    return re.sub(r"```.*?```", "", text, flags=re.DOTALL)


def extract_section(text, heading):
    """Return the body of the ``## <heading>`` section, or None if absent.

    The body runs from the heading to the next ``## `` heading or end of file.
    Callers pass text with fenced code blocks already stripped. The heading match
    is exact (``## Verification`` does not match ``## Verification / escape hatch``),
    so a Hardening field label can never be mistaken for a section heading.
    """
    match = re.search(
        r"^## " + re.escape(heading) + r"[ \t]*\n(.*?)(?=^## |\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else None


def extract_hardening_section(text):
    """Back-compat wrapper: the body of the ``## Hardening`` section, or None.

    Kept as a named entry point for the template-drift guard and unit tests.
    """
    return extract_section(text, "Hardening")


def _field_content(section, field):
    """Return the content of a Hardening ``field`` in the section, or None if the
    field label is absent. An empty string means the label is present but carries
    no content.

    Tolerant of both label forms: ``**Field:**`` (colon inside the bold, as the
    template scaffolds) and ``**Field** -`` / ``**Field** :`` (colon or dash
    outside, as AGENTS.md prose describes them). Content spans the rest of the
    label line plus any wrapped continuation lines, up to the next field bullet,
    a blank line, or the end of the section - so a value written across lines is
    read in full rather than judged empty.
    """
    label = re.compile(
        r"\*\*[ \t]*" + re.escape(field) + r"[ \t]*:?\*\*[ \t]*[:\-]?[ \t]*",
        re.MULTILINE,
    )
    m = label.search(section)
    if m is None:
        return None
    rest = section[m.end():]
    # Stop at the next field bullet (a dash then bold) or a blank line. Both
    # patterns require a leading newline so the label line's own trailing newline
    # is never mistaken for a terminating blank line.
    stop = re.search(r"\n[ \t]*-[ \t]*\*\*|\n[ \t]*\n", rest)
    body = rest[:stop.start()] if stop else rest
    return body.strip()


def check_skill(rel, text):
    """Return findings for one SKILL.md. Pure - no filesystem access.

    Enforces the two required load-bearing sections independently, so a skill
    missing both is told about both: the ``## Hardening`` safety envelope (present
    with all five fields non-empty) and the ``## Verification`` section (present
    and non-empty). Both report under the one ``skill-hardening`` label.
    """
    findings = []
    stripped = strip_code_blocks(text)

    section = extract_section(stripped, "Hardening")
    if section is None:
        findings.append(
            ("WARN", LABEL, f"{rel}: missing `## Hardening` section")
        )
    else:
        for field in REQUIRED_FIELDS:
            content = _field_content(section, field)
            if content is None:
                findings.append(
                    ("WARN", LABEL,
                     f"{rel}: Hardening section is missing the `{field}` field")
                )
            elif not content:
                findings.append(
                    ("WARN", LABEL,
                     f"{rel}: Hardening field `{field}` is empty")
                )
            elif _is_unfilled(content):
                findings.append(
                    ("WARN", LABEL,
                     f"{rel}: Hardening field `{field}` still contains an unfilled "
                     f"template placeholder")
                )

    # The Verification section is the other required load-bearing section (the
    # caller-facing "how to confirm this worked"). It has no sub-fields, so the
    # check is whole-section: present, non-empty, and not an unfilled placeholder.
    verification = extract_section(stripped, "Verification")
    if verification is None:
        findings.append(
            ("WARN", LABEL, f"{rel}: missing `## Verification` section")
        )
    else:
        body = verification.strip()
        if not body:
            findings.append(
                ("WARN", LABEL, f"{rel}: `## Verification` section is empty")
            )
        elif _is_unfilled(body):
            findings.append(
                ("WARN", LABEL,
                 f"{rel}: `## Verification` section still contains an unfilled "
                 f"template placeholder")
            )
    return findings


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
def run_check(root):
    findings = []
    for path in find_skill_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            # A file we cannot read means the check could not run on it - a
            # DEGRADED (non-blocking) condition, not a Hardening gap. Emitting it
            # as WARN would let a transiently locked or non-UTF-8 file hard-fail
            # close-out; DEGRADED surfaces it distinctly without blocking, the
            # same contract the other guards use for a can't-run condition.
            findings.append(("DEGRADED", LABEL, f"{rel}: could not read ({exc})"))
            continue
        findings.extend(check_skill(rel, text))
    return findings


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def print_report(findings):
    warns = [f for f in findings if f[0] == "WARN"]
    degraded = [f for f in findings if f[0] == "DEGRADED"]
    print("# Skill-Hardening Guard Report\n")
    print(f"**Warnings:** {len(warns)}  **Degraded:** {len(degraded)}\n")
    if not findings:
        print("Every SKILL.md carries a complete Hardening section and a "
              "Verification section.")
        return
    if warns:
        print("## WARN")
        for _, _, msg in sorted(warns, key=lambda f: f[2]):
            print(f"- {msg}")
        print()
    if degraded:
        print("## DEGRADED")
        for _, _, msg in sorted(degraded, key=lambda f: f[2]):
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
        description="Check that every SKILL.md carries a complete Hardening "
                    "section and a non-empty Verification section (read-only)")
    parser.add_argument("--check", action="store_true",
                        help="Read-only scan (default; the only mode)")
    parser.add_argument("--json", action="store_true",
                        help="Emit findings as JSON on stdout (for the audit hook)")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 if any WARN finding exists (for CI/close-out)")
    args = parser.parse_args()

    findings = run_check(PROJECT_ROOT)
    if args.json:
        print(findings_json(findings))
    else:
        print_report(findings)
    if args.strict and any(s == "WARN" for s, _, _ in findings):
        sys.exit(1)


if __name__ == "__main__":
    main()
