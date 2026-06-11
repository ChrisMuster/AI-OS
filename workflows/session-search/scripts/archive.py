#!/usr/bin/env python3
"""
archive.py — Book Dragon session archive writer.

Reads conversation transcripts from all registered AI adapters and writes
extracted message content to the Book Dragon archive in a standard normalised format.

Called by:
  - Stop / PreCompact / Notification hooks  →  python archive.py --hook
  - Session hooks (all AIs)                 →  python archive.py --all
  - Hourly scheduled task / scheduler.py    →  python archive.py --all
  - index.py (as subprocess)                →  python archive.py --all
  - Manually                                →  python archive.py [--all] [--dry-run]

Archive format (one JSON object per line):
  {
    "role":          "user" | "assistant",
    "content":       "<plain text>",
    "timestamp":     "<ISO 8601>",
    "session_id":    "<string>",
    "session_title": "<string | empty>",
    "source":        "<adapter source label>",
    "hostname":      "<machine hostname>",
    "ai_identity":   "<string | empty>"
  }
"""

import argparse
import json
import os
import re
import socket
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (all relative to this script's location)
# ---------------------------------------------------------------------------
SCRIPT_DIR   = Path(__file__).resolve().parent
WORKFLOW_DIR = SCRIPT_DIR.parent                    # workflows/session-search/
PROJECT_ROOT = WORKFLOW_DIR.parent.parent            # project root
DATA_DIR     = WORKFLOW_DIR / 'data'
ARCHIVE_DIR  = DATA_DIR / 'archive'
STATE_FILE   = DATA_DIR / 'index_state.json'
HOSTNAME     = socket.gethostname()


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'archived': {}, 'indexed': {}}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding='utf-8')


# ---------------------------------------------------------------------------
# Session title lookup (Claude Code only)
# ---------------------------------------------------------------------------

def get_session_title(session_id: str, source: str) -> str:
    """Return the auto-generated session title, or ''.

    Currently only Claude Code stores session titles in a metadata file.
    Other sources return '' immediately.
    """
    if source != 'claude-code':
        return ''
    appdata = os.environ.get('APPDATA', '')
    if not appdata:
        return ''
    sessions_root = Path(appdata) / 'Claude' / 'claude-code-sessions'
    if not sessions_root.exists():
        return ''
    for org_dir in sessions_root.iterdir():
        if not org_dir.is_dir():
            continue
        for session_dir in org_dir.iterdir():
            if not session_dir.is_dir():
                continue
            for meta_file in session_dir.glob('local_*.json'):
                try:
                    meta = json.loads(meta_file.read_text(encoding='utf-8', errors='ignore'))
                    if meta.get('cliSessionId') == session_id:
                        return meta.get('title', '')
                except Exception:
                    continue
    return ''


# ---------------------------------------------------------------------------
# AI identity extraction
# ---------------------------------------------------------------------------

_AI_IDENTITY_RE = re.compile(r'^AI_IDENTITY:\s*(.+)$', re.MULTILINE)

_SOURCE_TO_AI: dict = {
    'claude-code': 'Claude Code',
    'cowork': 'Claude Cowork',
}


def extract_ai_identity(records: list) -> str:
    """Scan the first few assistant messages for an AI_IDENTITY line.

    Falls back to source-based mapping for sessions that predate the convention.
    """
    for record in records[:20]:
        if record.get('role') != 'assistant':
            continue
        content = record.get('content', '')
        match = _AI_IDENTITY_RE.search(content)
        if match:
            return match.group(1).strip()

    if records:
        source = records[0].get('source', '')
        return _SOURCE_TO_AI.get(source, '')

    return ''


# ---------------------------------------------------------------------------
# Archive writer
# ---------------------------------------------------------------------------

def write_archive(session_id: str, records: list, title: str = '',
                   ai_identity: str = '', dry_run: bool = False) -> int:
    """Write records to data/archive/<hostname>/<session_id>.jsonl. Returns count written."""
    if not records:
        return 0

    archive_file = ARCHIVE_DIR / HOSTNAME / f'{session_id}.jsonl'

    if title:
        for r in records:
            r['session_title'] = title

    if ai_identity:
        for r in records:
            r['ai_identity'] = ai_identity

    if dry_run:
        print(f'  [DRY RUN] Would write {len(records)} message(s) to {archive_file.relative_to(WORKFLOW_DIR)}')
        return len(records)

    archive_file.parent.mkdir(parents=True, exist_ok=True)
    with archive_file.open('w', encoding='utf-8') as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + '\n')

    return len(records)


# ---------------------------------------------------------------------------
# Generic file archiver (adapter-based)
# ---------------------------------------------------------------------------

def archive_file(adapter, file_path: Path, state: dict, dry_run: bool = False) -> int:
    """Archive one transcript file using the given adapter, if new or changed."""
    file_key = str(file_path)
    try:
        current_mtime = file_path.stat().st_mtime
    except OSError:
        return 0

    if current_mtime <= state['archived'].get(file_key, {}).get('mtime', 0):
        return 0

    records = list(adapter.parse(str(file_path)))
    if not records:
        return 0

    session_id = records[-1].get('session_id', file_path.stem)
    source = adapter.SOURCE_LABEL
    title = get_session_title(session_id, source)
    ai_identity = extract_ai_identity(records)
    count = write_archive(session_id, records, title=title, ai_identity=ai_identity, dry_run=dry_run)

    if not dry_run and count > 0:
        state['archived'][file_key] = {
            'mtime': current_mtime,
            'session_id': session_id,
            'count': count,
        }

    return count


# ---------------------------------------------------------------------------
# Main archive modes
# ---------------------------------------------------------------------------

def archive_all(dry_run: bool = False) -> int:
    """Discover and archive all new or updated sessions from all registered adapters."""
    from adapters._registry import all_adapters

    state = load_state()
    total = 0

    for adapter in all_adapters():
        label = adapter.SOURCE_LABEL
        try:
            files = adapter.discover(str(PROJECT_ROOT))
        except NotImplementedError:
            continue
        except Exception as e:
            print(f'{label}: discovery error — {e}', file=sys.stderr)
            continue

        print(f'{label}: found {len(files)} session file(s).')
        for f in files:
            f = Path(f)
            count = archive_file(adapter, f, state, dry_run=dry_run)
            if count > 0:
                tag = '[DRY RUN] ' if dry_run else ''
                print(f'  {tag}Archived {count} message(s) from {f.stem}')
            total += count

    if not dry_run:
        save_state(state)

    print(f'Done. {total} message(s) archived.')
    return total


def archive_from_hook(dry_run: bool = False) -> int:
    """
    Handle a Claude Code hook invocation.

    Reads session context JSON from stdin. Ignores `transcript_path` from
    hook data (known stale-path bug, GitHub #8564); discovers the latest
    JSONL by modification time instead.
    """
    from adapters._registry import get_adapter

    try:
        raw = sys.stdin.read()
        hook_data = json.loads(raw) if raw.strip() else {}
    except Exception:
        hook_data = {}

    state = load_state()
    total = 0

    is_cowork = os.environ.get('CLAUDE_CODE_IS_COWORK', '').strip() == '1'

    if is_cowork:
        adapter = get_adapter('cowork')
        if adapter:
            files = adapter.discover(str(PROJECT_ROOT))
            for f in files:
                total += archive_file(adapter, Path(f), state, dry_run=dry_run)
    else:
        adapter = get_adapter('claude-code')
        if adapter:
            files = adapter.discover(str(PROJECT_ROOT))
            if files:
                latest = max(files, key=lambda p: Path(p).stat().st_mtime)
                count = archive_file(adapter, Path(latest), state, dry_run=dry_run)
                if count > 0:
                    tag = '[DRY RUN] ' if dry_run else ''
                    print(f'  {tag}Hook: archived {count} message(s) from {Path(latest).stem}',
                          file=sys.stderr)
                total += count

    if not dry_run:
        save_state(state)

    print(json.dumps({'continue': True}))
    return total


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Book Dragon session archive writer',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        '--hook',
        action='store_true',
        help='Called from a Claude Code hook; reads session context from stdin',
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Scan all sources for new or updated sessions (default when --hook not set)',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Print what would be done without writing anything',
    )
    args = parser.parse_args()

    if args.hook:
        archive_from_hook(dry_run=args.dry_run)
    else:
        archive_all(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
