#!/usr/bin/env python3
"""
Deterministic signal-gathering for the handoff workflow.

This is the ~90% (code) half of the 90-10 split: it collects the state of the
working session from the stores that already exist and assembles a plain briefing
packet. It writes nothing and makes no judgement - the ~10% (writing HANDOVER.md
from the packet) is the AI's job, defined in the workflow SKILL.md.

Every reader takes its roots explicitly (rather than reaching for globals) so the
test suite can point them at temporary fixtures, and every reader degrades to an
empty result rather than raising: unavailable git, a missing backlog, or an absent
session index must never break the packet.

Signals gathered:
  - branch + working tree   (git branch, git status --short)
  - line churn              (git diff --stat HEAD)
  - recent commits          (git log, last N)
  - changed-directory logs  (trailing LOG.md entries for each dir with changes)
  - active backlog          (the ## Active section of memory/backlog.md)
  - recent session activity  (session-search shards, by AI, for the last day)
"""

import json
import re
import sqlite3
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

# Leading date inside a LOG.md entry, e.g. "[2026-07-02T20:23:36+01:00] | ..."
_LOG_LINE = re.compile(r"^\[\d{4}-\d{2}-\d{2}T")
# A top-level Active backlog bullet, e.g. "- **Title** - description".
_ACTIVE_BULLET = re.compile(r"^- \*\*(.+?)\*\*")
# An H2 heading, e.g. "## Open - this round's scope". Deliberately not matching
# "###", so a finding heading inside a section never resets the section.
_H2 = re.compile(r"^##(?!#)\s+(.*)$")
# The two item shapes a review packet uses for a finding: an "### R4 - ..."
# sub-heading and a "- **R1 - ...**" top-level bullet. The bullet pattern is
# anchored with no leading whitespace on purpose, so an indented sub-bullet inside
# a finding's body is prose rather than another finding.
_SUB_HEADING = re.compile(r"^#{3,6}\s+(.*)$")
_TOP_BULLET = re.compile(r"^[-*]\s+(.*)$")

# A finding's label, if it carries one: any short uppercase prefix plus a number,
# so R1, F3 and BUG12 all read. This used to require R<n> specifically, which is
# why a round labelled F1 to F5 was read as zero findings on 2026-08-28. A label is
# now optional: the count is what matters, and an unlabelled bullet is a finding.
_FINDING_LABEL = re.compile(r"^(?:\*\*)?([A-Z]{1,4}\d+)\b")

# First words that mark a section as the open list or the addressed list. Matched
# on the first word so a reviewer can write "Open", "Outstanding", "Still to fix",
# "Addressed", "Fixed" or "Done" and be understood.
_OPEN_WORDS = frozenset(
    ("open", "outstanding", "remaining", "unresolved", "todo", "still"))
_ADDRESSED_WORDS = frozenset(
    ("addressed", "fixed", "done", "closed", "resolved", "complete", "completed"))


# ---------------------------------------------------------------------------
# git helpers
# ---------------------------------------------------------------------------
def _git(project_root, args, timeout=30):
    """Run a git command, returning stdout or "" on any failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), *args],
            capture_output=True, encoding="utf-8", timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout


def current_branch(project_root) -> str:
    """Return the current branch name, or "" if it cannot be determined."""
    return _git(project_root, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()


def git_status(project_root):
    """Return [(code, path)] from `git status --porcelain` (staged + untracked)."""
    out = _git(project_root, ["status", "--porcelain"])
    entries = []
    for line in out.splitlines():
        if not line.strip():
            continue
        code = line[:2]
        path = line[3:].strip()
        # Rename form "old -> new": keep the new path.
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        entries.append((code, path))
    return entries


def git_diff_stat(project_root) -> str:
    """Return the `git diff --stat HEAD` summary (tracked changes vs HEAD)."""
    return _git(project_root, ["diff", "--stat", "HEAD"]).rstrip()


def recent_commits(project_root, limit):
    """Return [(short_hash, iso_date, subject)] for the last `limit` commits."""
    out = _git(project_root, ["log", f"-{int(limit)}", "--date=short",
                              "--pretty=%h%x1f%ad%x1f%s"])
    commits = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            commits.append((parts[0], parts[1], parts[2]))
    return commits


# ---------------------------------------------------------------------------
# changed-directory LOG.md tails
# ---------------------------------------------------------------------------
def changed_dirs(status_entries):
    """Return the unique directories (repo-relative POSIX) of changed paths.

    The project root is represented as "." so its LOG.md can be located too.
    Ordering is stable (sorted) for deterministic output and tests.
    """
    dirs = set()
    for _code, path in status_entries:
        parent = Path(path).parent.as_posix()
        dirs.add("." if parent in ("", ".") else parent)
    return sorted(dirs)


def log_tail(project_root, rel_dir, tail):
    """Return the last `tail` LOG.md entry lines for a directory, or []."""
    root = Path(project_root)
    log_path = (root if rel_dir == "." else root / rel_dir) / "LOG.md"
    if not log_path.is_file():
        return []
    try:
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return []
    entries = [ln.strip() for ln in text.splitlines() if _LOG_LINE.match(ln)]
    return entries[-int(tail):] if tail else entries


def changed_dir_logs(project_root, status_entries, tail):
    """Return [(rel_dir, [lines])] of trailing LOG entries for each changed dir."""
    results = []
    for rel_dir in changed_dirs(status_entries):
        lines = log_tail(project_root, rel_dir, tail)
        if lines:
            results.append((rel_dir, lines))
    return results


# ---------------------------------------------------------------------------
# active backlog
# ---------------------------------------------------------------------------
def active_backlog(project_root):
    """Return the top-level Active backlog titles from memory/backlog.md, or [].

    Reads only the ## Active section and returns each top-level bold bullet
    title. Nested sub-bullets and the Completed section are ignored so the packet
    stays a scannable list of what is open.
    """
    path = Path(project_root) / "memory" / "backlog.md"
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    titles = []
    in_active = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_active = stripped[3:].strip().lower() == "active"
            continue
        if in_active:
            match = _ACTIVE_BULLET.match(line)  # top-level bullet (no indent)
            if match:
                titles.append(match.group(1).strip())
    return titles


# ---------------------------------------------------------------------------
# recent session activity
# ---------------------------------------------------------------------------
def recent_sessions(data_dir, today, lookback_days):
    """Return {'total', 'by_ai', 'titles'} for sessions in the last `lookback_days`.

    Queries every session-search shard read-only, filtering on the date prefix of
    the timestamp. Degrades to an empty summary on any failure or missing index.
    """
    empty = {"total": 0, "by_ai": {}, "titles": []}
    base = Path(data_dir)
    if not base.exists():
        return empty
    shards = sorted(base.glob("sessions-*.db"))
    if not shards:
        return empty

    since = (today - timedelta(days=max(0, lookback_days - 1))).isoformat()
    sessions = {}  # session_id -> (ai_identity, title)
    for shard in shards:
        try:
            conn = sqlite3.connect(f"file:{shard}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT DISTINCT session_id, session_title, ai_identity "
                "FROM sessions WHERE substr(timestamp, 1, 10) >= ?",
                (since,),
            ).fetchall()
            for row in rows:
                sid = row["session_id"]
                if sid and sid not in sessions:
                    sessions[sid] = (
                        row["ai_identity"] or "unknown",
                        row["session_title"] or "",
                    )
            conn.close()
        except (sqlite3.Error, OSError):
            continue

    by_ai = {}
    titles = []
    for ai, title in sessions.values():
        by_ai[ai] = by_ai.get(ai, 0) + 1
        if title:
            titles.append(title)
    return {"total": len(sessions), "by_ai": by_ai, "titles": sorted(set(titles))}


# ---------------------------------------------------------------------------
# doc-sync CONTEXT/LOG drift
# ---------------------------------------------------------------------------
def doc_sync_drift(project_root):
    """Return doc-sync CONTEXT/LOG drift messages for the working tree, or [].

    Runs the doc-sync guard read-only at its default (working-tree) scope and
    returns the WARN finding messages, so a handoff surfaces any directory whose
    CONTEXT.md / LOG.md is behind before HANDOVER.md is written - a reviewer
    reading stale context is worse than none. Degrades to [] on any failure
    (missing guard, bad JSON, git unavailable), matching the other readers, so a
    broken guard never breaks the packet.

    WARN only is deliberate. The guard's DEGRADED severity means a component of
    it could not run, which is not a directory needing a documentation update,
    so it is dropped here rather than listed as drift - and therefore never
    appears in the packet at all. That boundary is documented in this
    directory's CONTEXT.md, and it has to change when the guard starts acting on
    its output inventory.
    """
    guard = Path(project_root) / "workflows" / "doc-sync-guard" / "scripts" / "run.py"
    if not guard.is_file():
        return []
    try:
        result = subprocess.run(
            [sys.executable, str(guard), "--check", "--json"],
            capture_output=True, encoding="utf-8", timeout=60,
            cwd=str(project_root),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError):
        return []
    return [f.get("message", "") for f in payload.get("findings", [])
            if f.get("severity") == "WARN"]


# ---------------------------------------------------------------------------
# open review findings
# ---------------------------------------------------------------------------
def _section_kind(heading_text):
    """Classify an H2 heading as the open list, the addressed list, or neither.

    The format requirement on a review packet is deliberately this small, and it is
    the only one: somewhere in the file, a section saying what is still open and a
    section saying what has been dealt with. Every other section is free-form and is
    ignored here rather than rejected, so a packet can carry a good / okay / bad
    split, a checks table, or anything else asked of the reviewer, without the
    mechanism falling over. That breadth is the point: on 2026-08-28 a reviewing AI
    was asked for a good / okay / bad packet, produced exactly that, and the reader
    could not read the result at all.

    "Open questions" is excluded, because a packet may legitimately carry one and
    its entries are questions rather than findings.
    """
    name = heading_text.strip().strip("*_# ").lower()
    if not name or "question" in name:
        return None
    first = name.split()[0].rstrip(":,-")
    if first in _OPEN_WORDS:
        return "open"
    if first in _ADDRESSED_WORDS:
        return "addressed"
    return None


def _finding_label(item_text):
    """The finding's label if it has one, else None. A label is optional."""
    hit = _FINDING_LABEL.match(item_text.strip())
    return hit.group(1) if hit else None


def _resolve_items(section):
    """Collapse a section's two candidate item lists into one, or None if absent.

    Sub-headings win over bullets where both are present. A packet that uses
    "### R1 - ..." headings for its findings normally has bullets inside each
    finding's body, so counting both would multiply the count by the length of the
    prose rather than by the number of findings.

    Labelled items are de-duplicated; unlabelled ones are not, because two bullets
    with no label are two findings and nothing distinguishes them.
    """
    if section is None:
        return None
    items, seen = [], set()
    for label in (section["headings"] or section["bullets"]):
        if label is not None:
            if label in seen:
                continue
            seen.add(label)
        items.append(label)
    return items


def review_packets(project_root):
    """Return [{item, path, open, addressed}] for every memory/*_review_packet.md.

    A review packet holds the findings of the review round currently being worked
    through for one backlog item. This reader exists so a session boundary is never
    silent about outstanding review work: on 2026-08-26 a round's findings were
    written into HANDOVER.md and destroyed by the next handoff the same afternoon,
    because a handover has a session's lifetime and a review round has a
    multi-session one.

    Reporting only, deliberately. A handoff is NEVER withheld because findings are
    open - crossing a boundary with work outstanding is the entire purpose of a
    handoff, so gating on it would make a review round impossible to continue. The
    obligation is to carry the count and the pointer, never to block.

    `open` and `addressed` are None when no section of that kind is present, so a
    packet whose shape has drifted reports as unreadable rather than as zero
    findings. An unanswered question must never be rendered as an empty answer:
    "0 open" would read as "nothing left to do".

    Otherwise each is a list with one entry per finding, holding the finding's label
    where it has one and None where it does not. So the *count* is always the length
    of the list, and is right whether or not the reviewer used labels at all. This
    matters because the earlier version returned only recognised `R<n>` labels: a
    section full of findings labelled some other way produced an empty list, which
    rendered as a confident "0 open" rather than as a warning. That is the same
    failure the None case above exists to prevent, reached by a different route.

    Degrades to [] on a missing memory directory, and skips a file it cannot read,
    matching the other readers here - a broken packet never breaks the handoff.
    """
    memory = Path(project_root) / "memory"
    if not memory.is_dir():
        return []
    packets = []
    for path in sorted(memory.glob("*_review_packet.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        found = {}
        section = None
        for line in text.splitlines():
            heading = _H2.match(line)
            if heading:
                section = _section_kind(heading.group(1))
                if section is not None:
                    found.setdefault(section, {"headings": [], "bullets": []})
                continue
            if section is None:
                continue
            sub = _SUB_HEADING.match(line)
            if sub:
                found[section]["headings"].append(_finding_label(sub.group(1)))
                continue
            bullet = _TOP_BULLET.match(line)
            if bullet:
                found[section]["bullets"].append(_finding_label(bullet.group(1)))
        packets.append({
            "item": path.name[: -len("_review_packet.md")].replace("_", "-"),
            "path": path.relative_to(project_root).as_posix(),
            "open": _resolve_items(found.get("open")),
            "addressed": _resolve_items(found.get("addressed")),
        })
    return packets


# ---------------------------------------------------------------------------
# packet assembly
# ---------------------------------------------------------------------------
def build_packet(*, timestamp, branch, status, diffstat, commits, dir_logs,
                 backlog, sessions, doc_sync=(), review=()):
    """Assemble the handoff briefing packet as a markdown string for the AI."""
    lines = []
    lines.append("# Handoff briefing packet")
    lines.append("")
    lines.append(f"**Gathered:** {timestamp}")
    lines.append(f"**Branch:** {branch or '(unknown)'}")
    lines.append("")
    lines.append(
        "This packet is assembled deterministically by the gather script. Use it "
        "to write HANDOVER.md; it is the raw state, not the handoff itself. Add "
        "the narrative a script cannot: what you were mid-way through and why, the "
        "next steps, decisions made, and any gotchas."
    )
    lines.append("")

    lines.append("## Working tree (git status --short)")
    if status:
        for code, path in status:
            lines.append(f"- `{code}` {path}")
    else:
        lines.append("Clean - no uncommitted changes.")
    lines.append("")

    lines.append("## CONTEXT/LOG drift (doc-sync)")
    if doc_sync:
        lines.append(
            "Fix before handing off - a reviewer reading stale context is worse "
            "than none. These directories changed without their CONTEXT.md / "
            "LOG.md being updated:")
        for msg in doc_sync:
            lines.append(f"- {msg}")
    else:
        lines.append(
            "Clean - every changed directory's CONTEXT.md / LOG.md is current.")
    lines.append("")

    lines.append("## Open review findings")
    if review:
        lines.append(
            "Carry the open count and the packet path into HANDOVER.md. Do NOT "
            "copy the findings themselves in as their only copy: that is what "
            "destroyed a review round on 2026-08-26. This never blocks a handoff.")
        for entry in review:
            if entry["open"] is None:
                lines.append(
                    f"- **{entry['item']}:** shape not recognised (no section "
                    f"naming what is still open) - read it rather than trusting a "
                    f"count. Packet: `{entry['path']}`")
                continue
            labelled = [i for i in entry["open"] if i]
            total = len(entry["open"])
            if total == 0:
                shown = "none"
            elif len(labelled) == total:
                shown = ", ".join(labelled)
            elif labelled:
                shown = (f"{len(labelled)} labelled: {', '.join(labelled)}; "
                         f"{total - len(labelled)} unlabelled")
            else:
                shown = "unlabelled"
            done = len(entry["addressed"]) if entry["addressed"] is not None else "?"
            lines.append(
                f"- **{entry['item']}:** {total} open ({shown}), "
                f"{done} addressed. Packet: `{entry['path']}`")
    else:
        lines.append("No review packets in memory/ - no round is part-way through.")
    lines.append("")

    lines.append("## Line churn (git diff --stat HEAD)")
    if diffstat:
        lines.append("```")
        lines.append(diffstat)
        lines.append("```")
    else:
        lines.append("None vs HEAD.")
    lines.append("")

    lines.append("## Recent commits")
    if commits:
        for short, date_str, subject in commits:
            lines.append(f"- {date_str} {short} {subject}")
    else:
        lines.append("None found.")
    lines.append("")

    lines.append("## Recent LOG.md activity (changed directories)")
    if dir_logs:
        for rel, hits in dir_logs:
            lines.append(f"### {rel}")
            for hit in hits:
                lines.append(f"- {hit}")
            lines.append("")
    else:
        lines.append("No LOG.md entries in changed directories.")
        lines.append("")

    lines.append("## Active backlog")
    if backlog:
        for title in backlog:
            lines.append(f"- {title}")
    else:
        lines.append("None recorded.")
    lines.append("")

    lines.append("## Recent session activity")
    if sessions["total"]:
        by_ai = ", ".join(f"{ai}: {n}" for ai, n in sorted(sessions["by_ai"].items()))
        lines.append(f"{sessions['total']} session(s) - {by_ai}")
        if sessions["titles"]:
            lines.append("")
            lines.append("Titles:")
            for title in sessions["titles"]:
                lines.append(f"- {title}")
    else:
        lines.append("No indexed sessions in the last day (or no session index).")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"
