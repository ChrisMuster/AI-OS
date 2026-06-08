#!/usr/bin/env python3
"""
archive.py — Book Dragon session archive writer.

Reads conversation transcripts from Claude Code or Cowork caches and writes
extracted message content to the Book Dragon archive in a standard normalised format.

Called by:
  - Stop / PreCompact / Notification hooks  →  python archive.py --hook
  - Hourly scheduled task                   →  python archive.py --all
  - index.py (as subprocess)                →  python archive.py --all
  - Manually                                →  python archive.py [--all] [--dry-run]

Archive format (one JSON object per line):
  {
    "role":          "user" | "assistant",
    "content":       "<plain text>",
    "timestamp":     "<ISO 8601>",
    "session_id":    "<string>",
    "session_title": "<string | empty>",
    "source":        "claude-code" | "cowork",
    "hostname":      "<machine hostname>"
  }
"""

import argparse
import json
import os
import socket
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (all relative to this script's location)
# ---------------------------------------------------------------------------
SCRIPT_DIR   = Path(__file__).resolve().parent
WORKFLOW_DIR = SCRIPT_DIR.parent                    # workflows/session-search/
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

def get_session_title(session_id: str) -> str:
    """Return the auto-generated session title from claude-code-sessions metadata, or ''."""
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
# Text extraction
# ---------------------------------------------------------------------------

def extract_text(content_raw) -> str:
    """Extract plain text from a message content field (string or block array)."""
    if isinstance(content_raw, str):
        return content_raw.strip()
    if isinstance(content_raw, list):
        parts = []
        for block in content_raw:
            if isinstance(block, dict) and block.get('type') == 'text':
                parts.append(block.get('text', ''))
            elif isinstance(block, str):
                parts.append(block)
        return '\n'.join(parts).strip()
    return ''


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_claude_code_jsonl(file_path: Path) -> tuple:
    """
    Extract user and assistant messages from a Claude Code JSONL session file.

    Returns: (session_id: str, records: list[dict])
    """
    session_id = file_path.stem
    records = []
    try:
        for line in file_path.read_text(encoding='utf-8', errors='ignore').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = event.get('type')
            if event_type not in ('user', 'assistant'):
                continue

            # Update session ID from the event if present
            if event.get('sessionId'):
                session_id = event['sessionId']

            message = event.get('message', {})
            role = message.get('role', event_type)
            content = extract_text(message.get('content', ''))
            if not content:
                continue

            records.append({
                'role': role,
                'content': content,
                'timestamp': event.get('timestamp', ''),
                'session_id': session_id,
                'source': 'claude-code',
                'hostname': HOSTNAME,
            })
    except Exception as e:
        print(f'  [WARNING] Could not parse {file_path.name}: {e}', file=sys.stderr)

    return session_id, records


def parse_cowork_audit(file_path: Path) -> tuple:
    """
    Extract user and assistant messages from a Cowork audit.jsonl file.

    Returns: (session_id: str, records: list[dict])
    """
    # Fall back to conversation folder name if no session_id found in data
    session_id = file_path.parent.name
    records = []
    try:
        for line in file_path.read_text(encoding='utf-8', errors='ignore').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = event.get('type')

            # cwd lives on the system/init line only
            if event_type == 'system' and event.get('subtype') == 'init':
                if event.get('session_id'):
                    session_id = event['session_id']
                continue

            if event_type not in ('user', 'assistant'):
                continue

            if event.get('session_id'):
                session_id = event['session_id']

            message = event.get('message', {})
            role = message.get('role', event_type)
            content = extract_text(message.get('content', ''))
            if not content:
                continue

            records.append({
                'role': role,
                'content': content,
                'timestamp': event.get('_audit_timestamp', ''),
                'session_id': session_id,
                'source': 'cowork',
                'hostname': HOSTNAME,
            })
    except Exception as e:
        print(f'  [WARNING] Could not parse {file_path.parent.name}: {e}', file=sys.stderr)

    return session_id, records


# ---------------------------------------------------------------------------
# Archive writer
# ---------------------------------------------------------------------------

def write_archive(session_id: str, records: list, title: str = '', dry_run: bool = False) -> int:
    """Write records to data/archive/<hostname>/<session_id>.jsonl. Returns count written."""
    if not records:
        return 0

    archive_file = ARCHIVE_DIR / HOSTNAME / f'{session_id}.jsonl'

    if title:
        for r in records:
            r['session_title'] = title

    if dry_run:
        print(f'  [DRY RUN] Would write {len(records)} message(s) to {archive_file.relative_to(WORKFLOW_DIR)}')
        return len(records)

    archive_file.parent.mkdir(parents=True, exist_ok=True)
    with archive_file.open('w', encoding='utf-8') as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + '\n')

    return len(records)


# ---------------------------------------------------------------------------
# Session archivers
# ---------------------------------------------------------------------------

def archive_claude_code_file(jsonl_file: Path, state: dict, dry_run: bool = False) -> int:
    """Archive one Claude Code JSONL session file if new or changed."""
    file_key = str(jsonl_file)
    try:
        current_mtime = jsonl_file.stat().st_mtime
    except OSError:
        return 0

    if current_mtime <= state['archived'].get(file_key, {}).get('mtime', 0):
        return 0  # unchanged since last archive

    session_id, records = parse_claude_code_jsonl(jsonl_file)
    title = get_session_title(session_id)
    count = write_archive(session_id, records, title=title, dry_run=dry_run)

    if not dry_run and count > 0:
        state['archived'][file_key] = {
            'mtime': current_mtime,
            'session_id': session_id,
            'count': count,
        }

    return count


def archive_cowork_file(audit_file: Path, state: dict, dry_run: bool = False) -> int:
    """Archive one Cowork audit.jsonl file if new or changed."""
    file_key = str(audit_file)
    try:
        current_mtime = audit_file.stat().st_mtime
    except OSError:
        return 0

    if current_mtime <= state['archived'].get(file_key, {}).get('mtime', 0):
        return 0

    session_id, records = parse_cowork_audit(audit_file)
    count = write_archive(session_id, records, dry_run=dry_run)

    if not dry_run and count > 0:
        state['archived'][file_key] = {
            'mtime': current_mtime,
            'session_id': session_id,
            'count': count,
        }

    return count


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def get_project_cache_dir() -> Path | None:
    """Return the Claude Code cache directory for the current working project."""
    claude_dir = Path.home() / '.claude' / 'projects'
    if not claude_dir.exists():
        return None

    cwd = str(Path.cwd().resolve())
    # Claude sanitises the path by stripping the drive colon and replacing
    # separators with hyphens: C:\Users\X\project → C-Users-X-project
    sanitized = cwd.replace(':', '').replace('\\', '-').replace('/', '-')
    candidate = claude_dir / sanitized
    if candidate.exists():
        return candidate

    # Fallback: scan project directories and check cwd in JSONL files
    try:
        for project_dir in claude_dir.iterdir():
            if not project_dir.is_dir():
                continue
            for jsonl_file in list(project_dir.glob('*.jsonl'))[:3]:
                try:
                    first_line = jsonl_file.read_text(encoding='utf-8', errors='ignore').split('\n')[0]
                    event = json.loads(first_line)
                    if Path(event.get('cwd', '')).resolve() == Path(cwd).resolve():
                        return project_dir
                except Exception:
                    continue
    except Exception:
        pass

    return None


def discover_claude_code_files() -> list:
    """Return all Claude Code JSONL session files for the current project."""
    cache_dir = get_project_cache_dir()
    if not cache_dir:
        return []
    return sorted(cache_dir.glob('*.jsonl'), key=lambda p: p.stat().st_mtime)


def discover_cowork_files() -> list:
    """Return all Cowork audit.jsonl files."""
    appdata = os.environ.get('APPDATA', '')
    if not appdata:
        return []
    base = Path(appdata) / 'Claude' / 'local-agent-mode-sessions'
    if not base.exists():
        return []
    results = []
    try:
        for org_dir in base.iterdir():
            if not org_dir.is_dir():
                continue
            for session_dir in org_dir.iterdir():
                if not session_dir.is_dir():
                    continue
                for conv_dir in session_dir.iterdir():
                    if not conv_dir.is_dir():
                        continue
                    audit = conv_dir / 'audit.jsonl'
                    if audit.exists():
                        results.append(audit)
    except Exception:
        pass
    return results


# ---------------------------------------------------------------------------
# Main archive modes
# ---------------------------------------------------------------------------

def archive_all(dry_run: bool = False) -> int:
    """Discover and archive all new or updated sessions from all sources."""
    state = load_state()
    total = 0

    # Claude Code
    cc_files = discover_claude_code_files()
    print(f'Claude Code: found {len(cc_files)} session file(s).')
    for f in cc_files:
        count = archive_claude_code_file(f, state, dry_run=dry_run)
        if count > 0:
            tag = '[DRY RUN] ' if dry_run else ''
            print(f'  {tag}Archived {count} message(s) from {f.stem}')
        total += count

    # Cowork
    cw_files = discover_cowork_files()
    print(f'Cowork: found {len(cw_files)} session file(s).')
    for f in cw_files:
        count = archive_cowork_file(f, state, dry_run=dry_run)
        if count > 0:
            tag = '[DRY RUN] ' if dry_run else ''
            print(f'  {tag}Archived {count} message(s) from {f.parent.name}')
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
    try:
        raw = sys.stdin.read()
        hook_data = json.loads(raw) if raw.strip() else {}
    except Exception:
        hook_data = {}

    state = load_state()
    total = 0

    is_cowork = os.environ.get('CLAUDE_CODE_IS_COWORK', '').strip() == '1'

    if is_cowork:
        # Cowork hooks do not actually fire (GitHub #40495), but handle
        # gracefully in case that changes in future.
        cw_files = discover_cowork_files()
        for f in cw_files:
            total += archive_cowork_file(f, state, dry_run=dry_run)
    else:
        # Claude Code: find the most recently modified JSONL in this project's cache.
        cc_files = discover_claude_code_files()
        if cc_files:
            latest = max(cc_files, key=lambda p: p.stat().st_mtime)
            count = archive_claude_code_file(latest, state, dry_run=dry_run)
            if count > 0:
                tag = '[DRY RUN] ' if dry_run else ''
                print(f'  {tag}Hook: archived {count} message(s) from {latest.stem}',
                      file=sys.stderr)
            total += count

    if not dry_run:
        save_state(state)

    # Return a valid hook response so Claude Code does not treat this as an error.
    result = {
        'continue': True,
        'hookSpecificOutput': {
            'hookEventName': hook_data.get('hook_event_name', 'unknown'),
            'archived': total,
        },
    }
    print(json.dumps(result))
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
