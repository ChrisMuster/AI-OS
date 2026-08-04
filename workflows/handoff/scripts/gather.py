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
# packet assembly
# ---------------------------------------------------------------------------
def build_packet(*, timestamp, branch, status, diffstat, commits, dir_logs,
                 backlog, sessions, doc_sync=()):
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
