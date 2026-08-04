#!/usr/bin/env python3
"""Protect memory/backlog.md with snapshots and count checks.

Typical use:
  python workflows/backlog-guard/scripts/run.py --snapshot --reason "before edit"
  python workflows/backlog-guard/scripts/run.py --check
  python workflows/backlog-guard/scripts/run.py --restore <snapshot> --force

Test path overrides:
  BACKLOG_GUARD_BACKLOG, BACKLOG_GUARD_BACKUP_DIR, BACKLOG_GUARD_LOG
"""

import argparse
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
WORKFLOW_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = WORKFLOW_DIR.parent.parent

DEFAULT_BACKLOG = PROJECT_ROOT / "memory" / "backlog.md"
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "memory" / "backlog-backups"
DEFAULT_LOG = WORKFLOW_DIR / "LOG.md"

REQUIRED_SECTIONS = ("Active", "Build Only When Needed")
SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")
ITEM_RE = re.compile(r"^-\s+\*\*(.+?)\*\*")
DEFAULT_KEEP = 5
DEFAULT_MAX_ITEM_LINES = 8
DEFAULT_MAX_ITEM_CHARS = 1600


@dataclass
class ItemSpan:
    section: str
    title: str
    line_count: int
    char_count: int


@dataclass
class BacklogState:
    path: Path
    counts: dict
    missing_sections: list
    item_spans: list


def _timestamp():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _snapshot_stamp():
    return datetime.now(timezone.utc).astimezone().strftime("%Y%m%dT%H%M%S%z")


def _append_log(action, note, log_path):
    line = f"[{_timestamp()}] | Actor: Biblio | Action: {action} | Note: {note}\n"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)


def _append_backup_log(action, note, backup_dir):
    _append_log(action, note, backup_dir / "LOG.md")


def _resolve_paths():
    backlog = Path(os.environ.get("BACKLOG_GUARD_BACKLOG", str(DEFAULT_BACKLOG)))
    backup_dir = Path(os.environ.get("BACKLOG_GUARD_BACKUP_DIR", str(DEFAULT_BACKUP_DIR)))
    log_path = Path(os.environ.get("BACKLOG_GUARD_LOG", str(DEFAULT_LOG)))
    return backlog, backup_dir, log_path


def parse_backlog(path):
    data = path.read_bytes()
    text = data.decode("utf-8")
    lines = text.splitlines()
    sections = {}
    current = None
    section_start = {}

    for number, line in enumerate(lines, start=1):
        match = SECTION_RE.match(line)
        if match:
            current = match.group(1).strip()
            sections[current] = []
            section_start[current] = number
            continue
        if current is not None:
            sections[current].append((number, line))

    missing = [name for name in REQUIRED_SECTIONS if name not in sections]
    counts = {}
    item_spans = []
    for section in REQUIRED_SECTIONS:
        rows = sections.get(section, [])
        counts[section] = sum(1 for _number, line in rows if ITEM_RE.match(line))
        item_spans.extend(_item_spans(section, rows))

    return BacklogState(path=path, counts=counts, missing_sections=missing,
                        item_spans=item_spans)


def _item_spans(section, rows):
    spans = []
    current_title = None
    current_lines = []

    def flush():
        if current_title is None:
            return
        char_count = sum(len(line) + 1 for _number, line in current_lines)
        spans.append(ItemSpan(
            section=section,
            title=current_title,
            line_count=len(current_lines),
            char_count=char_count,
        ))

    for _number, line in rows:
        match = ITEM_RE.match(line)
        if match:
            flush()
            current_title = match.group(1).strip()
            current_lines = [(_number, line)]
        elif current_title is not None:
            if SECTION_RE.match(line):
                flush()
                current_title = None
                current_lines = []
            else:
                current_lines.append((_number, line))
    flush()
    return spans


def list_snapshots(backup_dir):
    if not backup_dir.exists():
        return []
    return sorted(p for p in backup_dir.glob("*-backlog.md") if p.is_file())


def latest_snapshot(backup_dir):
    snapshots = list_snapshots(backup_dir)
    return snapshots[-1] if snapshots else None


def _unique_snapshot_path(backup_dir):
    stamp = _snapshot_stamp()
    candidate = backup_dir / f"{stamp}-backlog.md"
    if not candidate.exists():
        return candidate
    counter = 2
    while True:
        candidate = backup_dir / f"{stamp}-{counter}-backlog.md"
        if not candidate.exists():
            return candidate
        counter += 1


def rotate_snapshots(backup_dir, keep):
    if keep < 1:
        raise ValueError("--keep must be at least 1")
    snapshots = list_snapshots(backup_dir)
    removed = []
    while len(snapshots) > keep:
        oldest = snapshots.pop(0)
        oldest.unlink()
        removed.append(oldest)
    return removed


def create_snapshot(backlog_path, backup_dir, keep, reason, log_path):
    state = parse_backlog(backlog_path)
    if state.missing_sections:
        missing = ", ".join(state.missing_sections)
        raise RuntimeError(f"cannot snapshot backlog with missing section(s): {missing}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = _unique_snapshot_path(backup_dir)
    temp = target.with_suffix(target.suffix + ".tmp")
    shutil.copyfile(backlog_path, temp)
    temp.replace(target)
    removed = rotate_snapshots(backup_dir, keep)
    note = (
        f"Snapshot {target.name} created for memory/backlog.md "
        f"(Active {state.counts['Active']}, Build Only When Needed "
        f"{state.counts['Build Only When Needed']}; reason: {reason or 'not supplied'})."
    )
    if removed:
        note += f" Rotated {len(removed)} old snapshot(s)."
    _append_log("completed", note, log_path)
    _append_backup_log("completed", note, backup_dir)
    return target, state, removed


def oversized_items(state, max_lines, max_chars):
    findings = []
    for item in state.item_spans:
        too_many_lines = item.line_count > max_lines
        too_many_chars = item.char_count > max_chars
        if too_many_lines or too_many_chars:
            findings.append(item)
    return findings


def check_backlog(backlog_path, backup_dir, allow_active_drop=0, allow_build_drop=0,
                  max_item_lines=DEFAULT_MAX_ITEM_LINES,
                  max_item_chars=DEFAULT_MAX_ITEM_CHARS):
    current = parse_backlog(backlog_path)
    baseline_path = latest_snapshot(backup_dir)
    messages = []

    if current.missing_sections:
        for section in current.missing_sections:
            messages.append(f"FAIL: current backlog is missing ## {section}")

    long_items = oversized_items(current, max_item_lines, max_item_chars)
    for item in long_items:
        messages.append(
            "FAIL: oversized backlog item in "
            f"## {item.section}: {item.title!r} "
            f"({item.line_count} lines, {item.char_count} chars)"
        )

    if baseline_path is None:
        messages.append("FAIL: no backlog snapshot exists for comparison")
        return False, messages, current, None, None

    baseline = parse_backlog(baseline_path)
    if baseline.missing_sections:
        for section in baseline.missing_sections:
            messages.append(f"FAIL: latest snapshot is missing ## {section}: {baseline_path.name}")

    active_drop = baseline.counts["Active"] - current.counts["Active"]
    build_drop = (baseline.counts["Build Only When Needed"] -
                  current.counts["Build Only When Needed"])
    if active_drop > allow_active_drop:
        messages.append(
            "FAIL: Active count dropped from "
            f"{baseline.counts['Active']} to {current.counts['Active']} "
            f"(allowed drop {allow_active_drop})"
        )
    if build_drop > allow_build_drop:
        messages.append(
            "FAIL: Build Only When Needed count dropped from "
            f"{baseline.counts['Build Only When Needed']} to "
            f"{current.counts['Build Only When Needed']} "
            f"(allowed drop {allow_build_drop})"
        )

    ok = not any(message.startswith("FAIL:") for message in messages)
    if ok:
        messages.append(
            "PASS: backlog guard check passed "
            f"(snapshot {baseline_path.name}; Active "
            f"{current.counts['Active']} vs {baseline.counts['Active']}, "
            "Build Only When Needed "
            f"{current.counts['Build Only When Needed']} vs "
            f"{baseline.counts['Build Only When Needed']})"
        )
    else:
        messages.append(f"Restore candidate: {baseline_path.name}")
    return ok, messages, current, baseline, baseline_path


def restore_snapshot(snapshot_name, backlog_path, backup_dir, force, log_path):
    if not force:
        raise RuntimeError("restore requires --force")
    backup_root = backup_dir.resolve()
    candidate = (backup_dir / snapshot_name).resolve()
    try:
        candidate.relative_to(backup_root)
    except ValueError:
        raise RuntimeError("snapshot name must stay inside the backup directory")
    if not candidate.exists() or not candidate.is_file():
        raise RuntimeError(f"snapshot does not exist: {snapshot_name}")
    backlog_path.parent.mkdir(parents=True, exist_ok=True)
    temp = backlog_path.with_suffix(backlog_path.suffix + ".tmp")
    shutil.copyfile(candidate, temp)
    temp.replace(backlog_path)
    state = parse_backlog(backlog_path)
    _append_log(
        "completed",
        f"Restored memory/backlog.md from backlog snapshot {candidate.name} "
        f"(Active {state.counts['Active']}, Build Only When Needed "
        f"{state.counts['Build Only When Needed']}).",
        log_path,
    )
    _append_backup_log(
        "completed",
        f"Restored memory/backlog.md from backlog snapshot {candidate.name} "
        f"(Active {state.counts['Active']}, Build Only When Needed "
        f"{state.counts['Build Only When Needed']}).",
        backup_dir,
    )
    return candidate, state


def print_status(backlog_path, backup_dir):
    state = parse_backlog(backlog_path)
    latest = latest_snapshot(backup_dir)
    print(f"Backlog: {backlog_path}")
    print(f"Active: {state.counts['Active']}")
    print(f"Build Only When Needed: {state.counts['Build Only When Needed']}")
    if state.missing_sections:
        print("Missing sections: " + ", ".join(state.missing_sections))
    print(f"Snapshots: {len(list_snapshots(backup_dir))}")
    print(f"Latest snapshot: {latest.name if latest else 'none'}")
    return 0 if not state.missing_sections else 1


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true",
                        help="Print current counts and latest snapshot.")
    parser.add_argument("--snapshot", action="store_true",
                        help="Create a snapshot of the current backlog.")
    parser.add_argument("--check", action="store_true",
                        help="Compare current backlog with latest snapshot.")
    parser.add_argument("--restore", metavar="SNAPSHOT",
                        help="Restore a named snapshot to memory/backlog.md.")
    parser.add_argument("--force", action="store_true",
                        help="Required with --restore.")
    parser.add_argument("--reason", default="",
                        help="Reason recorded for --snapshot.")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP,
                        help=f"Number of snapshots to retain (default {DEFAULT_KEEP}).")
    parser.add_argument("--allow-active-drop", type=int, default=0,
                        help="Allowed Active item count drop during --check.")
    parser.add_argument("--allow-build-drop", type=int, default=0,
                        help="Allowed Build Only When Needed count drop during --check.")
    parser.add_argument("--max-item-lines", type=int, default=DEFAULT_MAX_ITEM_LINES,
                        help=f"Maximum physical lines per backlog item (default {DEFAULT_MAX_ITEM_LINES}).")
    parser.add_argument("--max-item-chars", type=int, default=DEFAULT_MAX_ITEM_CHARS,
                        help=f"Maximum characters per backlog item (default {DEFAULT_MAX_ITEM_CHARS}).")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    backlog_path, backup_dir, log_path = _resolve_paths()
    actions = [args.status, args.snapshot, args.check, args.restore is not None]
    if sum(1 for action in actions if action) > 1:
        parser.error("choose only one action")

    try:
        if args.snapshot:
            target, state, removed = create_snapshot(
                backlog_path, backup_dir, args.keep, args.reason, log_path)
            print(
                f"SNAPSHOT: {target.name} "
                f"(Active {state.counts['Active']}, "
                f"Build Only When Needed {state.counts['Build Only When Needed']})"
            )
            if removed:
                print(f"Rotated {len(removed)} old snapshot(s).")
            return 0
        if args.check:
            ok, messages, _current, _baseline, _baseline_path = check_backlog(
                backlog_path,
                backup_dir,
                allow_active_drop=args.allow_active_drop,
                allow_build_drop=args.allow_build_drop,
                max_item_lines=args.max_item_lines,
                max_item_chars=args.max_item_chars,
            )
            stream = sys.stdout if ok else sys.stderr
            for message in messages:
                print(message, file=stream)
            return 0 if ok else 1
        if args.restore is not None:
            snapshot, state = restore_snapshot(
                args.restore, backlog_path, backup_dir, args.force, log_path)
            print(
                f"RESTORED: {snapshot.name} "
                f"(Active {state.counts['Active']}, "
                f"Build Only When Needed {state.counts['Build Only When Needed']})"
            )
            return 0
        return print_status(backlog_path, backup_dir)
    except (OSError, UnicodeError, RuntimeError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
