#!/usr/bin/env python3
"""
Deterministic signal-gathering for the weekly-review workflow.

This is the ~90% (code) half of the 90-10 split: it collects everything that
happened in a window from the stores that already exist, and assembles a plain
briefing packet. It writes nothing and makes no judgement - the ~10% (writing
the review from the packet) is the AI's job, defined in the workflow SKILL.md.

Every reader takes its roots explicitly (rather than reaching for globals) so the
test suite can point them at temporary fixtures, and every reader degrades to an
empty result rather than raising: a missing journal, absent session index, or
unavailable git must never break the packet.

Signals gathered:
  - journal entries          (journal/entries/YYYY-MM.md)
  - LOG.md activity          (every LOG.md across the project, filtered by date)
  - git history              (commits in the window)
  - memory changes           (memory/LOG.md entries in the window) - a lightweight
                             memory-diff, delivered as a byproduct
  - session activity         (session-search shards, per-AI, by date)
  - prior reviews            (reviews/YYYY-Www.md) for compounding context
"""

import re
import sqlite3
import subprocess
from datetime import timedelta
from pathlib import Path

# Leading day number of a journal heading, e.g. "## 1 July" -> 1.
_JOURNAL_HEADING = re.compile(r"^##\s+(\d{1,2})\b")
# Leading date inside a LOG.md entry, e.g. "[2026-07-02T20:23:36+01:00] | ..." -> 2026-07-02.
_LOG_DATE = re.compile(r"^\[(\d{4}-\d{2}-\d{2})")
# A review filename stem, e.g. "2026-W27". Distinguishes review files from the
# CONTEXT.md / LOG.md that also live in reviews/.
_REVIEW_LABEL = re.compile(r"^\d{4}-W\d{2}$")


# ---------------------------------------------------------------------------
# Labels and dates
# ---------------------------------------------------------------------------
def iso_week_label(day) -> str:
    """ISO year-week label for a date, e.g. date(2026, 7, 2) -> '2026-W27'."""
    iso = day.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------
def _month_sections(text: str) -> dict:
    """Parse a monthly journal file into {day_number: body_text}."""
    sections = {}
    current_day = None
    buffer = []
    for line in text.splitlines():
        match = _JOURNAL_HEADING.match(line)
        if match:
            if current_day is not None:
                sections[current_day] = "\n".join(buffer).strip()
            current_day = int(match.group(1))
            buffer = []
        elif current_day is not None:
            buffer.append(line)
    if current_day is not None:
        sections[current_day] = "\n".join(buffer).strip()
    return sections


def _month_file(journal_root, year: int, month: int) -> Path:
    return Path(journal_root) / "entries" / f"{year:04d}-{month:02d}.md"


def _read_month_sections(journal_root, year: int, month: int) -> dict:
    path = _month_file(journal_root, year, month)
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    return _month_sections(text)


def make_journal_checker(journal_root):
    """Return has_content(date) -> bool, caching parsed month files."""
    cache = {}

    def has_content(day) -> bool:
        key = (day.year, day.month)
        if key not in cache:
            cache[key] = _read_month_sections(journal_root, day.year, day.month)
        return bool(cache[key].get(day.day, "").strip())

    return has_content


def journal_entries(journal_root, iso_days):
    """Return [(iso_date, body)] for the given ISO date strings that have content."""
    from state import parse_date

    cache = {}
    entries = []
    for iso in sorted(iso_days):
        try:
            day = parse_date(iso)
        except (ValueError, TypeError):
            continue
        key = (day.year, day.month)
        if key not in cache:
            cache[key] = _read_month_sections(journal_root, day.year, day.month)
        body = cache[key].get(day.day, "").strip()
        if body:
            entries.append((iso, body))
    return entries


# ---------------------------------------------------------------------------
# LOG.md activity
# ---------------------------------------------------------------------------
def log_entries(project_root, start, end, skip_dirs=("data", "outputs", "collections")):
    """Return [(rel_path, [lines])] for LOG.md entries dated within [start, end].

    Skips the workflow's own LOG.md would be nice but is not necessary; the root
    and directory logs are the point. Reads every LOG.md, filters lines by the
    leading date, and keeps only files that have at least one in-window entry.
    """
    root = Path(project_root)
    start_iso, end_iso = start.isoformat(), end.isoformat()
    results = []
    for log_path in sorted(root.rglob("LOG.md")):
        parts = set(log_path.relative_to(root).parts)
        if parts & set(skip_dirs):
            continue
        try:
            text = log_path.read_text(encoding="utf-8")
        except OSError:
            continue
        hits = []
        for line in text.splitlines():
            match = _LOG_DATE.match(line)
            if not match:
                continue
            if start_iso <= match.group(1) <= end_iso:
                hits.append(line.strip())
        if hits:
            rel = log_path.relative_to(root).as_posix()
            results.append((rel, hits))
    return results


# ---------------------------------------------------------------------------
# git history
# ---------------------------------------------------------------------------
def git_commits(project_root, start, end):
    """Return [(short_hash, iso_date, subject)] for commits in the window.

    Empty on any failure (git missing, not a repo, etc.).
    """
    # git --until is exclusive of the following midnight; add a day so the end
    # date is fully included regardless of commit time.
    until = (end + timedelta(days=1)).isoformat()
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "log",
             f"--since={start.isoformat()}", f"--until={until}",
             "--date=short", "--pretty=%h%x1f%ad%x1f%s"],
            capture_output=True, encoding="utf-8", timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    commits = []
    for line in result.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            commits.append((parts[0], parts[1], parts[2]))
    return commits


# ---------------------------------------------------------------------------
# memory changes (lightweight memory-diff)
# ---------------------------------------------------------------------------
def memory_changes(project_root, start, end):
    """Return in-window entry lines from memory/LOG.md, or [] if absent."""
    path = Path(project_root) / "memory" / "LOG.md"
    if not path.is_file():
        return []
    start_iso, end_iso = start.isoformat(), end.isoformat()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    hits = []
    for line in text.splitlines():
        match = _LOG_DATE.match(line)
        if match and start_iso <= match.group(1) <= end_iso:
            hits.append(line.strip())
    return hits


# ---------------------------------------------------------------------------
# session activity
# ---------------------------------------------------------------------------
def session_summary(data_dir, start, end):
    """Return {'total': int, 'by_ai': {ai: count}, 'titles': [..]} for the window.

    Queries every session-search shard read-only, filtering on the date prefix of
    the timestamp so format/timezone differences do not matter. Degrades to an
    empty summary on any failure or when no index exists.
    """
    empty = {"total": 0, "by_ai": {}, "titles": []}
    base = Path(data_dir)
    if not base.exists():
        return empty
    shards = sorted(base.glob("sessions-*.db"))
    if not shards:
        return empty

    sessions = {}  # session_id -> (ai_identity, title)
    start_iso, end_iso = start.isoformat(), end.isoformat()
    for shard in shards:
        try:
            conn = sqlite3.connect(f"file:{shard}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT DISTINCT session_id, session_title, ai_identity "
                "FROM sessions "
                "WHERE substr(timestamp, 1, 10) >= ? AND substr(timestamp, 1, 10) <= ?",
                (start_iso, end_iso),
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
# prior reviews
# ---------------------------------------------------------------------------
def prior_reviews(reviews_dir, current_label, limit):
    """Return [(label, text)] for up to `limit` most recent reviews before now.

    Excludes the current week's file so a re-run does not feed a review its own
    draft. Ordering is by filename, which sorts correctly for YYYY-Www labels.
    """
    base = Path(reviews_dir)
    if not base.exists():
        return []
    files = sorted(base.glob("*.md"))
    picked = []
    for path in files:
        label = path.stem
        if not _REVIEW_LABEL.match(label):
            continue  # skip CONTEXT.md / LOG.md and any non-review file
        if label == current_label:
            continue
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        picked.append((label, text))
    return picked[-limit:] if limit else picked


# ---------------------------------------------------------------------------
# Packet assembly
# ---------------------------------------------------------------------------
def build_packet(*, label, start, end, included_days, empty_days,
                 journal, logs, commits, memory, sessions, reviews):
    """Assemble the briefing packet as a markdown string for the AI to read."""
    lines = []
    lines.append(f"# Weekly review briefing packet - {label}")
    lines.append("")
    lines.append(f"**Window:** {start.isoformat()} to {end.isoformat()}")
    lines.append("")
    lines.append(
        "This packet is assembled deterministically by the gather script. Use it "
        "to write the review; do not treat it as the review itself."
    )
    lines.append("")
    lines.append(
        f"The run day ({end.isoformat()}) is deliberately not counted in the "
        "journal for this review - the day is not finished. It will be picked up "
        "by a later review once written."
    )
    lines.append("")

    if empty_days:
        lines.append("## Journal gaps (warning)")
        lines.append(
            "These days in the window have no journal entry yet. The review will "
            "note them as pending and pick them up automatically once written:"
        )
        lines.append("")
        for iso in empty_days:
            lines.append(f"- {iso} - (no entry)")
        lines.append("")

    lines.append("## Journal entries")
    if journal:
        for iso, body in journal:
            lines.append(f"### {iso}")
            lines.append(body)
            lines.append("")
    else:
        lines.append("None in window.")
        lines.append("")

    lines.append("## Session activity")
    if sessions["total"]:
        by_ai = ", ".join(f"{ai}: {n}" for ai, n in sorted(sessions["by_ai"].items()))
        lines.append(f"{sessions['total']} session(s) - {by_ai}")
        if sessions["titles"]:
            lines.append("")
            lines.append("Titles:")
            for title in sessions["titles"]:
                lines.append(f"- {title}")
        lines.append("")
    else:
        lines.append("No indexed sessions in window (or no session index present).")
        lines.append("")

    lines.append("## Git commits")
    if commits:
        for short, date_str, subject in commits:
            lines.append(f"- {date_str} {short} {subject}")
        lines.append("")
    else:
        lines.append("None in window.")
        lines.append("")

    lines.append("## Memory changes")
    if memory:
        for line in memory:
            lines.append(f"- {line}")
        lines.append("")
    else:
        lines.append("None in window.")
        lines.append("")

    lines.append("## LOG.md activity")
    if logs:
        for rel, hits in logs:
            lines.append(f"### {rel}")
            for hit in hits:
                lines.append(f"- {hit}")
            lines.append("")
    else:
        lines.append("None in window.")
        lines.append("")

    lines.append("## Prior reviews (for compounding context)")
    if reviews:
        lines.append(
            "Reconcile against these: note anything superseded or contradicted, "
            "and do not let older summaries override newer facts."
        )
        lines.append("")
        for rlabel, text in reviews:
            lines.append(f"### {rlabel}")
            lines.append(text)
            lines.append("")
    else:
        lines.append("None yet - this is an early review; it will be thin.")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
