#!/usr/bin/env python3
"""
run.py — Scaffolds a new wiki directory in wikis/.

Safe to re-run: skips files and directories that already exist and skips
README/CONTEXT updates if the entry is already present.

Usage (run from anywhere — paths are resolved relative to this script):
    python workflows/create-wiki/scripts/run.py <wiki-name> "<topic description>"
    python workflows/create-wiki/scripts/run.py <wiki-name> "<topic description>" --dry-run

Options:
    --dry-run   Print every action that would be taken without making any changes.
                Nothing is created, modified, or logged.

Examples:
    python workflows/create-wiki/scripts/run.py react-patterns "React design patterns and component architecture"
    python workflows/create-wiki/scripts/run.py ai-glossary "AI and machine learning terminology" --dry-run
"""

import sys
import argparse
import importlib.util
from pathlib import Path
from datetime import datetime


# ---------------------------------------------------------------------------
# Paths — resolved relative to this script's location, not the CWD.
# Script lives at:  workflows/create-wiki/scripts/run.py
# Project root is:  ../../../  relative to this file
# ---------------------------------------------------------------------------
SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent

WIKIS_DIR     = PROJECT_ROOT / "wikis"
WORKFLOW_DIR  = PROJECT_ROOT / "workflows" / "create-wiki"
TEMPLATE_PATH = WORKFLOW_DIR / "wiki-context.md.template"
WIKIS_CONTEXT = WIKIS_DIR / "CONTEXT.md"
ROOT_README   = PROJECT_ROOT / "README.md"
WORKFLOW_LOG  = WORKFLOW_DIR / "LOG.md"
ROOT_LOG      = PROJECT_ROOT / "LOG.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def now_ts() -> str:
    """ISO 8601 timestamp, e.g. 2026-05-27T14:32:01"""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def now_date() -> str:
    """YYYY-MM-DD date string."""
    return datetime.now().strftime("%Y-%m-%d")


def now_display() -> str:
    """Human-readable date with no leading zero, e.g. '27 May 2026'."""
    n = datetime.now()
    return f"{n.day} {n.strftime('%B %Y')}"


def title(wiki_name: str) -> str:
    """Convert a kebab-case wiki name to Title Case display name."""
    return wiki_name.replace("-", " ").title()


def append_log(path: Path, ts: str, action: str, note: str) -> None:
    entry = f"[{ts}] | Actor: Biblio | Action: {action} | Note: {note}\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(entry)


def run_post_link() -> None:
    """
    Run the link-check --link pass after scaffolding so the new wiki's
    CONTEXT.md files are wired into the Obsidian knowledge graph immediately.
    Calls add_links_to_file() directly for each CONTEXT.md in the project.
    Prints a one-line summary.
    """
    link_path = PROJECT_ROOT / "workflows" / "link-check" / "scripts" / "run.py"
    spec   = importlib.util.spec_from_file_location("link_check", link_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    files = module.collect_context_files()
    total_links = 0
    files_changed = 0
    for f in files:
        changes = module.add_links_to_file(f, dry_run=False)
        if changes:
            files_changed += 1
            total_links += len(changes)

    print(f"  [links] {total_links} link(s) added across {files_changed} file(s)")


def run_post_audit() -> None:
    """
    Run the structural audit as a final verification step after scaffolding.
    Calls run_audit() directly (no log entries written, no subprocess overhead).
    Prints a one-line summary; prints the full report if there are failures or warnings.
    """
    audit_path = PROJECT_ROOT / "workflows" / "audit" / "scripts" / "run.py"
    spec   = importlib.util.spec_from_file_location("audit", audit_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    findings, dir_count = module.run_audit()
    fails = sum(1 for f in findings if f[0] == "FAIL")
    warns = sum(1 for f in findings if f[0] == "WARN")

    status = "OK" if (fails == 0 and warns == 0) else "ISSUES FOUND"
    print(f"  [audit] {dir_count} dirs checked — {fails} failure(s), {warns} warning(s) [{status}]")

    if fails > 0 or warns > 0:
        print()
        print(module.format_report(findings, dir_count))


def action_label(dry_run: bool, created: bool = True) -> str:
    """Return the right prefix for a printed action line."""
    if dry_run:
        return "[DRY RUN]"
    return "[+]" if created else "[=]"


# ---------------------------------------------------------------------------
# File content — each function returns the full text of a file
# ---------------------------------------------------------------------------
def wiki_root_log(wiki_name: str, ts: str) -> str:
    return (
        f"# {title(wiki_name)} Wiki — Log\n\n"
        f"[{ts}] | Actor: Biblio | Action: created"
        f" | Note: Wiki directory scaffolded by create-wiki script.\n"
    )


def raw_context(wiki_name: str, d: str) -> str:
    return f"""# Raw

**Last modified:** {d}

## Purpose
Holds immutable source documents for the {wiki_name} wiki. Files placed here are never modified — they are the ground truth that wiki pages are built from.

## Contents
None.

## Inputs
Source documents provided by the user (PDFs, HTML exports, text files, etc.).

## Outputs
None. This directory is read-only.

## Steps
N/A. This is a storage directory, not a runnable workflow.

## Dependencies
- `../CONTEXT.md` — The parent wiki's operational instructions, which define how these source files are used.

## Known Issues
None.

## Revision History
- {d} — Initial creation.
"""


def raw_log(wiki_name: str, ts: str) -> str:
    return (
        f"# Raw — Log\n\n"
        f"[{ts}] | Actor: Biblio | Action: created"
        f" | Note: Created raw/ directory for {wiki_name} wiki. Awaiting source documents.\n"
    )


def wiki_subdir_context(wiki_name: str, d: str) -> str:
    return f"""# Wiki

**Last modified:** {d}

## Purpose
Holds all wiki pages for the {wiki_name} wiki. Pages are created and maintained by Biblio based on sources in the raw/ directory.

## Contents
- index.md — `wikis/{wiki_name}/wiki/index.md` — Table of contents listing all wiki pages with one-line descriptions.
- operations-log.md — `wikis/{wiki_name}/wiki/operations-log.md` — Append-only record of all wiki operations (ingests, edits, lint passes).

## Inputs
Source documents from the sibling raw/ directory.

## Outputs
Markdown wiki pages covering concepts from the source material.

## Steps
N/A. Individual pages are created and updated by Biblio during ingest and editing sessions.

## Dependencies
- `../CONTEXT.md` — The wiki's operational instructions and rules.
- `../raw/` — Source documents that wiki pages are built from.

## Known Issues
None.

## Revision History
- {d} — Initial creation.
"""


def wiki_subdir_log(wiki_name: str, ts: str) -> str:
    return (
        f"# Wiki — Log\n\n"
        f"[{ts}] | Actor: Biblio | Action: created"
        f" | Note: Created wiki/ directory for {wiki_name} wiki.\n"
    )


def wiki_index(wiki_name: str) -> str:
    return (
        f"# {title(wiki_name)} — Index\n\n"
        "*No pages yet. Add source documents to raw/ and run an ingest session to populate this index.*\n"
    )


def wiki_ops_log(ts: str) -> str:
    return f"# Operations Log\n\n[{ts}] Wiki created. Awaiting source documents in raw/.\n"


# ---------------------------------------------------------------------------
# Idempotent file/directory creation helpers
# ---------------------------------------------------------------------------
def make_dir(path: Path, label: str, dry_run: bool) -> None:
    """Create a directory if it doesn't exist. Always prints what happened."""
    if path.exists():
        print(f"  [=] {label}/ — already exists, skipping")
    elif dry_run:
        print(f"  [DRY RUN] would create {label}/")
    else:
        path.mkdir(parents=True)
        print(f"  [+] {label}/")


def make_file(path: Path, content: str, label: str, dry_run: bool) -> None:
    """Write a file if it doesn't exist. Always prints what happened."""
    if path.exists():
        print(f"  [=] {label} — already exists, skipping")
    elif dry_run:
        print(f"  [DRY RUN] would create {label}")
    else:
        path.write_text(content, encoding="utf-8")
        print(f"  [+] {label}")


# ---------------------------------------------------------------------------
# Markdown updaters — idempotent inserts into existing files
# ---------------------------------------------------------------------------
def update_wikis_context(wiki_name: str, wiki_topic: str, d: str, dry_run: bool) -> None:
    """
    Insert a new entry into the Contents section of wikis/CONTEXT.md.
    Skips silently if the wiki is already listed.
    """
    content = WIKIS_CONTEXT.read_text(encoding="utf-8")

    # Idempotency guard — skip if already listed
    if f"`wikis/{wiki_name}/`" in content:
        print(f"  [=] wikis/CONTEXT.md — {wiki_name} already listed, skipping")
        return

    if dry_run:
        print(f"  [DRY RUN] would add {title(wiki_name)} entry to wikis/CONTEXT.md")
        return

    lines = content.splitlines()
    dname      = title(wiki_name)
    topic_str  = wiki_topic.rstrip(".") + "."
    new_entry  = f"- {dname} — `wikis/{wiki_name}/` — {topic_str}"
    rev_entry  = f"- {d} — Added {dname} wiki to Contents."

    result        = []
    in_contents   = False
    last_item_idx = -1

    for line in lines:
        if line.startswith("**Last modified:**"):
            result.append(f"**Last modified:** {d}")
            continue
        if line.strip() == "## Contents":
            in_contents = True
        elif in_contents and line.startswith("## "):
            in_contents = False
        if in_contents and line.startswith("- "):
            last_item_idx = len(result)
        result.append(line)

    if last_item_idx >= 0:
        result.insert(last_item_idx + 1, new_entry)

    while result and not result[-1].strip():
        result.pop()
    result.append(rev_entry)
    result.append("")

    WIKIS_CONTEXT.write_text("\n".join(result), encoding="utf-8")
    print(f"  [~] wikis/CONTEXT.md updated")


def update_readme(wiki_name: str, wiki_topic: str, dry_run: bool) -> None:
    """
    Insert a new entry into the Wikis section of README.md.
    Skips silently if the wiki is already listed.
    """
    content = ROOT_README.read_text(encoding="utf-8")

    # Idempotency guard — skip if already listed
    if f"`wikis/{wiki_name}/`" in content:
        print(f"  [=] README.md — {wiki_name} already listed, skipping")
        return

    if dry_run:
        print(f"  [DRY RUN] would add {title(wiki_name)} entry to README.md")
        return

    lines = content.splitlines()
    dname      = title(wiki_name)
    topic_str  = wiki_topic.rstrip(".")
    new_entry  = f"- **{dname}** — {topic_str}. `wikis/{wiki_name}/` `[active]`"

    result        = []
    in_wikis      = False
    last_item_idx = -1

    for line in lines:
        if line.startswith("**Last updated:**"):
            result.append(f"**Last updated:** {now_display()}")
            continue
        if line.strip() == "## Wikis":
            in_wikis = True
        elif in_wikis and line.startswith("## "):
            in_wikis = False
        if in_wikis and line.startswith("- "):
            last_item_idx = len(result)
        result.append(line)

    if last_item_idx >= 0:
        result.insert(last_item_idx + 1, new_entry)

    ROOT_README.write_text("\n".join(result) + "\n", encoding="utf-8")
    print(f"  [~] README.md updated")


# ---------------------------------------------------------------------------
# Main scaffold
# ---------------------------------------------------------------------------
def scaffold_wiki(wiki_name: str, wiki_topic: str, dry_run: bool) -> None:
    ts = now_ts()
    d  = now_date()

    wiki_dir    = WIKIS_DIR / wiki_name
    raw_dir     = wiki_dir / "raw"
    wiki_subdir = wiki_dir / "wiki"

    if dry_run:
        print("=== DRY RUN — no files will be created or modified ===")
        print()

    print(f"Scaffolding wiki '{wiki_name}'")
    print(f"Topic: {wiki_topic}")
    print()

    # Log started (skipped on dry run)
    if not dry_run:
        rerun = wiki_dir.exists()
        note  = f"Scaffolding {wiki_name} wiki. Topic: {wiki_topic}."
        if rerun:
            note += " (re-run — skipping existing files)"
        append_log(WORKFLOW_LOG, ts, "started", note)

    # --- Directories ---
    make_dir(wiki_dir,    f"wikis/{wiki_name}",       dry_run)
    make_dir(raw_dir,     f"wikis/{wiki_name}/raw",    dry_run)
    make_dir(wiki_subdir, f"wikis/{wiki_name}/wiki",   dry_run)

    # --- Wiki root files ---
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    make_file(
        wiki_dir / "CONTEXT.md",
        template.replace("{{WIKI_TOPIC}}", wiki_topic),
        f"wikis/{wiki_name}/CONTEXT.md",
        dry_run,
    )
    make_file(
        wiki_dir / "LOG.md",
        wiki_root_log(wiki_name, ts),
        f"wikis/{wiki_name}/LOG.md",
        dry_run,
    )

    # --- raw/ files ---
    make_file(raw_dir / "CONTEXT.md", raw_context(wiki_name, d),
              f"wikis/{wiki_name}/raw/CONTEXT.md", dry_run)
    make_file(raw_dir / "LOG.md",     raw_log(wiki_name, ts),
              f"wikis/{wiki_name}/raw/LOG.md",     dry_run)

    # --- wiki/ files ---
    make_file(wiki_subdir / "CONTEXT.md",      wiki_subdir_context(wiki_name, d),
              f"wikis/{wiki_name}/wiki/CONTEXT.md",      dry_run)
    make_file(wiki_subdir / "LOG.md",           wiki_subdir_log(wiki_name, ts),
              f"wikis/{wiki_name}/wiki/LOG.md",           dry_run)
    make_file(wiki_subdir / "index.md",         wiki_index(wiki_name),
              f"wikis/{wiki_name}/wiki/index.md",         dry_run)
    make_file(wiki_subdir / "operations-log.md", wiki_ops_log(ts),
              f"wikis/{wiki_name}/wiki/operations-log.md", dry_run)

    # --- Update existing files ---
    update_wikis_context(wiki_name, wiki_topic, d, dry_run)
    update_readme(wiki_name, wiki_topic, dry_run)

    # --- Log completed (skipped on dry run) ---
    if not dry_run:
        note = (
            f"Scaffolded {wiki_name} wiki at wikis/{wiki_name}/. "
            "wikis/CONTEXT.md and README.md updated."
        )
        append_log(WORKFLOW_LOG, ts, "completed", note)
        append_log(ROOT_LOG, ts, "completed",
                   f"create-wiki workflow ran. Created wikis/{wiki_name}/ — topic: {wiki_topic}.")
        print(f"  [~] workflows/create-wiki/LOG.md updated")
        print(f"  [~] LOG.md (root) updated")

        # --- Post-scaffold link pass then audit ---
        print()
        run_post_link()
        run_post_audit()

    print()
    if dry_run:
        print("=== DRY RUN complete — no changes made ===")
    else:
        print(f"Done. wikis/{wiki_name}/ is ready.")
        print(f"Next: add source documents to wikis/{wiki_name}/raw/ and run an ingest session.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scaffold a new wiki directory in wikis/.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python workflows/create-wiki/scripts/run.py react-patterns"
            ' "React design patterns and component architecture"\n'
            "  python workflows/create-wiki/scripts/run.py ai-glossary"
            ' "AI and machine learning terminology" --dry-run'
        ),
    )
    parser.add_argument(
        "wiki_name",
        help="Directory name — lowercase letters, numbers, and hyphens only (e.g. react-patterns)",
    )
    parser.add_argument(
        "wiki_topic",
        help='One-line topic description (e.g. "React design patterns and component architecture")',
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without making any changes. Nothing is created or logged.",
    )

    args = parser.parse_args()

    wiki_name = args.wiki_name.lower().strip()
    if not all(c.isalnum() or c == "-" for c in wiki_name):
        print("ERROR: Wiki name must contain only lowercase letters, numbers, and hyphens.")
        sys.exit(1)

    scaffold_wiki(wiki_name, args.wiki_topic.strip(), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
