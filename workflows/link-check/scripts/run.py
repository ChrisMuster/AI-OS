#!/usr/bin/env python3
"""
run.py — Manage Obsidian wiki links in Book Dragon CONTEXT.md files.

Modes:
  --link    Scan all CONTEXT.md files and append [[links]] after backtick path
            references where a link is not already present. Idempotent — safe
            to re-run; existing links are never duplicated.
  --audit   Scan all CONTEXT.md files for [[links]] and report any that point
            to files that no longer exist. Default mode when no mode flag given.
  --fix     Run the audit then attempt to auto-resolve each dead link by
            searching the project for a file matching the target name. If
            exactly one match is found, the link is rewritten. Zero or multiple
            matches are flagged for manual review.

Other flags:
  --dry-run  Preview all changes without writing anything.
  --save     Save the report to workflows/link-check/last-report.md.

Usage:
  python workflows/link-check/scripts/run.py --link [--dry-run]
  python workflows/link-check/scripts/run.py [--audit] [--fix] [--save]
"""

import re
import sys
import argparse
from pathlib import Path
from datetime import datetime

# Ensure stdout handles Unicode on Windows terminals
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
WORKFLOW_DIR = PROJECT_ROOT / "workflows" / "link-check"
WORKFLOW_LOG = WORKFLOW_DIR / "LOG.md"
ROOT_LOG     = PROJECT_ROOT / "LOG.md"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".claude"}

# Top-level project directories whose paths we link
TOP_LEVEL_DIRS = {"workflows", "wikis", "skills", "templates", "journal"}

# Root-level .md files that get direct links (LOG.md excluded — too noisy)
ROOT_LINK_FILES = {"CLAUDE.md", "README.md", "USER.md", "SOUL.md"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def now_ts() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def append_log(path: Path, ts: str, action: str, note: str) -> None:
    entry = f"[{ts}] | Actor: Biblio | Action: {action} | Note: {note}\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(entry)


def collect_context_files() -> list[Path]:
    """Return all CONTEXT.md files in the project, skipping skip dirs."""
    files = []
    for p in sorted(PROJECT_ROOT.rglob("CONTEXT.md")):
        parts = p.relative_to(PROJECT_ROOT).parts
        if any(part in SKIP_DIRS or part.startswith(".") for part in parts):
            continue
        files.append(p)
    return files


# ---------------------------------------------------------------------------
# Link resolution
# ---------------------------------------------------------------------------
def should_process_ref(raw: str) -> bool:
    """Return True if this backtick string is a path reference we should link."""
    p = raw.strip().rstrip("/")
    if p in ROOT_LINK_FILES:
        return True
    first = p.split("/")[0]
    return first in TOP_LEVEL_DIRS


def resolve_link_target(raw: str) -> str | None:
    """
    Given a backtick path reference, return the Obsidian [[link]] target
    (project-root-relative path without .md extension, forward slashes).
    Returns None if the path cannot be resolved to a real file.
    """
    p = raw.strip().rstrip("/")

    # Root-level .md files
    if p in ROOT_LINK_FILES:
        if (PROJECT_ROOT / p).exists():
            return p[:-3]  # strip .md: "CLAUDE.md" → "CLAUDE"
        return None

    full = PROJECT_ROOT / p

    # Directory path
    if full.is_dir():
        if (full / "CONTEXT.md").exists():
            return p + "/CONTEXT"
        return None

    # Existing .md file
    if full.exists() and p.endswith(".md"):
        if Path(p).name == "LOG.md":
            return None  # don't link log files
        return p[:-3]

    # Existing non-.md file — link to its parent directory's CONTEXT.md
    if full.exists():
        parent = full.parent
        if (parent / "CONTEXT.md").exists():
            return rel(parent) + "/CONTEXT"
        return None

    return None


# ---------------------------------------------------------------------------
# --link mode
# ---------------------------------------------------------------------------
def process_line(line: str) -> tuple[str, list[str]]:
    """
    Add [[links]] after backtick path references that don't already have one.
    Returns (modified_line, list_of_descriptions).
    Processes right-to-left so inserted text doesn't shift remaining positions.
    """
    insertions: list[tuple[int, str]] = []
    descriptions: list[str] = []

    for match in re.finditer(r"`([^`]+)`", line):
        raw = match.group(1)

        if not should_process_ref(raw):
            continue

        target = resolve_link_target(raw)
        if target is None:
            continue

        full_link = f"[[{target}]]"
        if full_link in line:
            continue  # already linked anywhere on this line

        insertions.append((match.end(), f" {full_link}"))
        descriptions.append(f"`{raw}` -> {full_link}")

    if not insertions:
        return line, []

    result = line
    for pos, text in sorted(insertions, reverse=True, key=lambda x: x[0]):
        result = result[:pos] + text + result[pos:]

    return result, descriptions


def add_links_to_file(context_path: Path, dry_run: bool) -> list[str]:
    """
    Process one CONTEXT.md file, inserting [[links]] where missing.
    Returns a list of change descriptions (empty = no changes).
    """
    content = context_path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)

    new_lines: list[str] = []
    all_changes: list[str] = []
    in_code_block = False

    for line in lines:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            new_lines.append(line)
            continue

        if in_code_block:
            new_lines.append(line)
            continue

        new_line, changes = process_line(line)
        new_lines.append(new_line)
        all_changes.extend(changes)

    if all_changes and not dry_run:
        context_path.write_text("".join(new_lines), encoding="utf-8")

    return all_changes


def run_link_mode(dry_run: bool) -> tuple[str, int, int]:
    """
    Run --link mode. Returns (report_text, files_changed, links_added).
    """
    files = collect_context_files()
    files_changed = 0
    links_added = 0
    sections: list[str] = []

    for f in files:
        changes = add_links_to_file(f, dry_run)
        if changes:
            files_changed += 1
            links_added += len(changes)
            prefix = "[DRY RUN] " if dry_run else ""
            block = [f"### {rel(f)}"]
            for c in changes:
                block.append(f"- {prefix}{c}")
            block.append("")
            sections.append("\n".join(block))

    verb = "Would add" if dry_run else "Added"
    summary = (
        f"{verb} {links_added} link(s) across {files_changed} file(s)."
        if links_added
        else "No links to add — all path references are already linked."
    )

    report_lines = [
        "# Book Dragon — Link Check: Link Mode",
        "",
        f"**Run at:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Mode:** {'dry run' if dry_run else 'live'}",
        "",
        summary,
        "",
    ]
    if sections:
        report_lines += ["## Changes", ""] + sections

    return "\n".join(report_lines), files_changed, links_added


# ---------------------------------------------------------------------------
# --audit / --fix modes
# ---------------------------------------------------------------------------
def is_project_link(target: str) -> bool:
    """
    Return True if this [[link]] target is a project path we should audit.
    Filters out wiki-internal links (bare page names with no slash).
    """
    if "/" in target:
        return target.split("/")[0] in TOP_LEVEL_DIRS
    # Root file stems (e.g. "CLAUDE", "README")
    return target in {f[:-3] for f in ROOT_LINK_FILES}


def link_resolves(target: str) -> bool:
    """Return True if [[target]] points to an existing .md file."""
    return (PROJECT_ROOT / (target + ".md")).exists()


LinkFinding = tuple[str, str, str]  # (file_rel, target, "ok"|"dead")


def audit_links() -> list[LinkFinding]:
    """Find all project [[links]] in CONTEXT.md files and check each one."""
    findings: list[LinkFinding] = []
    for context_path in collect_context_files():
        content = context_path.read_text(encoding="utf-8")
        file_rel = rel(context_path)
        seen = set()
        for match in re.finditer(r"\[\[([^\]]+)\]\]", content):
            raw = match.group(1)
            target = raw.split("|")[0].strip()  # strip display alias
            if not is_project_link(target):
                continue
            key = (file_rel, target)
            if key in seen:
                continue
            seen.add(key)
            status = "ok" if link_resolves(target) else "dead"
            findings.append((file_rel, target, status))
    return findings


def try_fix_dead_link(target: str) -> str | None:
    """
    Search the project for a file that could replace a dead link target.
    Returns the new target path (no .md) if exactly one match is found.
    Returns None if zero or multiple matches exist.
    """
    filename = Path(target).name  # e.g. "CONTEXT" from "old/path/CONTEXT"
    matches: list[str] = []
    for md_file in sorted(PROJECT_ROOT.rglob(f"{filename}.md")):
        parts = md_file.relative_to(PROJECT_ROOT).parts
        if any(p in SKIP_DIRS or p.startswith(".") for p in parts):
            continue
        candidate = rel(md_file)[:-3]  # strip .md
        matches.append(candidate)
    return matches[0] if len(matches) == 1 else None


def apply_link_fix(context_path: Path, old_target: str, new_target: str, dry_run: bool) -> None:
    """Rewrite [[old_target]] as [[new_target]] throughout a file."""
    content = context_path.read_text(encoding="utf-8")
    updated = content.replace(f"[[{old_target}]]", f"[[{new_target}]]")
    if updated != content and not dry_run:
        context_path.write_text(updated, encoding="utf-8")


def run_audit_mode(fix: bool, dry_run: bool) -> tuple[str, int, int]:
    """
    Run --audit (and optionally --fix) mode.
    Returns (report_text, dead_count, fixed_count).
    """
    findings = audit_links()
    dead = [(f, t) for f, t, s in findings if s == "dead"]
    ok_count = sum(1 for _, _, s in findings if s == "ok")
    dead_count = len(dead)
    fixed_count = 0
    manual_count = 0

    fix_lines: list[str] = []
    manual_lines: list[str] = []

    if fix and dead:
        for file_rel, target in dead:
            replacement = try_fix_dead_link(target)
            if replacement:
                context_path = PROJECT_ROOT / file_rel
                prefix = "[DRY RUN] " if dry_run else ""
                apply_link_fix(context_path, target, replacement, dry_run)
                fix_lines.append(
                    f"- {prefix}`{file_rel}` — `[[{target}]]` → `[[{replacement}]]`"
                )
                fixed_count += 1
            else:
                manual_lines.append(
                    f"- `{file_rel}` — `[[{target}]]` — no unique match found"
                )
                manual_count += 1

    report_lines = [
        "# Book Dragon — Link Check: Audit",
        "",
        f"**Run at:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Mode:** {'fix (dry run)' if fix and dry_run else 'fix' if fix else 'audit'}",
        f"**Links checked:** {len(findings)}",
        f"**OK:** {ok_count}",
        f"**Dead:** {dead_count}",
        "",
    ]

    if not dead:
        report_lines.append("All project links resolve correctly.")
        report_lines.append("")
    else:
        if not fix:
            report_lines += ["## Dead Links", ""]
            for file_rel, target in dead:
                report_lines.append(f"- `{file_rel}` — `[[{target}]]`")
            report_lines.append("")
        else:
            if fix_lines:
                verb = "Would fix" if dry_run else "Fixed"
                report_lines += [f"## {verb} ({fixed_count})", ""] + fix_lines + [""]
            if manual_lines:
                report_lines += [
                    f"## Needs Manual Review ({manual_count})",
                    "",
                    "These dead links have no unique replacement — check whether the",
                    "target was deleted (remove the link) or renamed ambiguously (update manually).",
                    "",
                ] + manual_lines + [""]

    return "\n".join(report_lines), dead_count, fixed_count


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manage Obsidian [[links]] in Book Dragon CONTEXT.md files."
    )
    parser.add_argument("--link",    action="store_true", help="Insert [[links]] after backtick path references.")
    parser.add_argument("--audit",   action="store_true", help="Report dead [[links]] (default mode).")
    parser.add_argument("--fix",     action="store_true", help="Audit and auto-fix dead links where possible.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing.")
    parser.add_argument("--save",    action="store_true", help="Save report to workflows/link-check/last-report.md.")
    args = parser.parse_args()

    # Default to audit if no mode specified
    if not args.link and not args.fix:
        args.audit = True

    ts = now_ts()
    dry_label = " (dry run)" if args.dry_run else ""
    mode_label = "link" if args.link else ("fix" if args.fix else "audit")
    append_log(WORKFLOW_LOG, ts, "started", f"Running link-check in {mode_label} mode{dry_label}.")

    if args.link:
        report, files_changed, links_added = run_link_mode(args.dry_run)
        note = f"Link mode{dry_label}: {links_added} link(s) added across {files_changed} file(s)."
    else:
        report, dead_count, fixed_count = run_audit_mode(fix=args.fix, dry_run=args.dry_run)
        if args.fix:
            note = f"Fix mode{dry_label}: {dead_count} dead link(s) found, {fixed_count} fixed."
        else:
            note = f"Audit mode: {dead_count} dead link(s) found."

    print(report)

    if args.save:
        report_path = WORKFLOW_DIR / "last-report.md"
        report_path.write_text(report, encoding="utf-8")
        print(f"Report saved to workflows/link-check/last-report.md")

    append_log(WORKFLOW_LOG, ts, "completed", note)
    append_log(ROOT_LOG, ts, "completed", f"link-check workflow ran. {note}")


if __name__ == "__main__":
    main()
