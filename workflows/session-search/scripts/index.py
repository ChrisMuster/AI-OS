#!/usr/bin/env python3
"""
index.py — Book Dragon session search indexer.

Reads the archive and builds or updates the SQLite FTS5 search database.
Also calls archive.py first to ensure the archive is up to date.

Usage:
  python index.py              — archive new sessions, then index anything new
  python index.py --rebuild    — drop and rebuild the database from scratch
  python index.py --dry-run    — print what would happen without writing

Recommended: run during universal AGENTS.md session startup and scheduled
maintenance so all supported AIs keep the search index fresh.
"""

import argparse
import json
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR   = Path(__file__).resolve().parent
WORKFLOW_DIR = SCRIPT_DIR.parent
DATA_DIR     = WORKFLOW_DIR / 'data'
ARCHIVE_DIR  = DATA_DIR / 'archive'
STATE_FILE   = DATA_DIR / 'index_state.json'
HOSTNAME     = socket.gethostname()
DB_FILE      = DATA_DIR / f'sessions-{HOSTNAME}.db'


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

CREATE_FTS5_TABLE = """
CREATE VIRTUAL TABLE IF NOT EXISTS sessions USING fts5(
    hostname,
    source,
    session_id,
    session_title,
    ai_identity,
    timestamp,
    role,
    content
);
"""

CREATE_META_TABLE = """
CREATE TABLE IF NOT EXISTS session_meta (
    session_id     TEXT PRIMARY KEY,
    hostname       TEXT,
    source         TEXT,
    session_title  TEXT,
    ai_identity    TEXT,
    first_timestamp TEXT,
    last_timestamp  TEXT,
    message_count  INTEGER
);
"""


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def needs_schema_migration(db_file: Path) -> bool:
    """Check if an existing database is missing the ai_identity column."""
    if not db_file.exists():
        return False
    try:
        conn = sqlite3.connect(f'file:{db_file}?mode=ro', uri=True)
        # Check FTS5 table columns
        row = conn.execute("SELECT * FROM sessions LIMIT 0").description
        col_names = [col[0] for col in row] if row else []
        conn.close()
        return 'ai_identity' not in col_names
    except Exception:
        return False


def open_db(db_file: Path) -> sqlite3.Connection:
    """Open the database, creating it with the correct schema if needed."""
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_file))
    conn.execute(CREATE_FTS5_TABLE)
    conn.execute(CREATE_META_TABLE)
    conn.commit()
    return conn


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
    with STATE_FILE.open('w', encoding='utf-8', newline='\n') as fh:
        fh.write(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Indexer
# ---------------------------------------------------------------------------

def index_archive_file(conn: sqlite3.Connection, archive_file: Path,
                        state: dict, dry_run: bool = False) -> int:
    """
    Index one archive JSONL file into the database.
    Skips if the file has not changed since the last index run.
    Returns the number of records indexed (0 = skipped or empty).
    """
    file_key = str(archive_file)
    try:
        current_mtime = archive_file.stat().st_mtime
    except OSError:
        return 0

    if current_mtime <= state['indexed'].get(file_key, {}).get('mtime', 0):
        return 0  # unchanged

    records = []
    try:
        for line in archive_file.read_text(encoding='utf-8', errors='ignore').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except Exception as e:
        print(f'  [WARNING] Could not read {archive_file.name}: {e}')
        return 0

    if not records:
        return 0

    session_id = records[0].get('session_id', archive_file.stem)

    if dry_run:
        print(f'  [DRY RUN] Would index {len(records)} message(s) from {archive_file.name}')
        return len(records)

    # Full re-index: remove any existing records for this session first
    conn.execute('DELETE FROM sessions WHERE session_id = ?', (session_id,))
    conn.execute('DELETE FROM session_meta WHERE session_id = ?', (session_id,))

    # Determine AI identity for the session
    ai_identity = records[0].get('ai_identity', '')

    # Insert all records
    for record in records:
        conn.execute(
            'INSERT INTO sessions '
            '(hostname, source, session_id, session_title, ai_identity, timestamp, role, content) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (
                record.get('hostname', ''),
                record.get('source', ''),
                record.get('session_id', session_id),
                record.get('session_title', ''),
                record.get('ai_identity', ai_identity),
                record.get('timestamp', ''),
                record.get('role', ''),
                record.get('content', ''),
            ),
        )

    # Update summary metadata
    timestamps = [r.get('timestamp', '') for r in records if r.get('timestamp')]
    conn.execute(
        'INSERT OR REPLACE INTO session_meta VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (
            session_id,
            records[0].get('hostname', ''),
            records[0].get('source', ''),
            records[0].get('session_title', ''),
            ai_identity,
            min(timestamps) if timestamps else '',
            max(timestamps) if timestamps else '',
            len(records),
        ),
    )
    conn.commit()

    state['indexed'][file_key] = {
        'mtime': current_mtime,
        'session_id': session_id,
        'count': len(records),
    }

    return len(records)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_index(dry_run: bool = False, rebuild: bool = False) -> None:
    # Step 1: run archive.py --all to capture any sessions not yet archived
    archive_script = SCRIPT_DIR / 'archive.py'
    cmd = [sys.executable, str(archive_script), '--all']
    if dry_run:
        cmd.append('--dry-run')
    subprocess.run(cmd, check=False)

    # Step 2: (re)build the database
    state = load_state()

    # Auto-detect schema migration: if the database exists but lacks the
    # ai_identity column, trigger a full rebuild so all sessions get the
    # new field (with retroactive AI identity from archive.py).
    if not rebuild and needs_schema_migration(DB_FILE):
        rebuild = True
        print('Schema migration detected (ai_identity column missing). Triggering rebuild.')

    if rebuild:
        if DB_FILE.exists() and not dry_run:
            DB_FILE.unlink()
            print(f'Dropped existing database: {DB_FILE.name}')
        elif dry_run:
            print(f'[DRY RUN] Would drop: {DB_FILE.name}')
        state['indexed'] = {}

    if dry_run:
        conn = None
    else:
        conn = open_db(DB_FILE)

    archive_files = (
        sorted(ARCHIVE_DIR.rglob('*.jsonl'), key=lambda p: p.stat().st_mtime)
        if ARCHIVE_DIR.exists() else []
    )

    print(f'\nIndexing {len(archive_files)} archive file(s) into {DB_FILE.name}.')
    total = 0

    for f in archive_files:
        if dry_run:
            # Simulate the mtime check without a real db connection
            file_key = str(f)
            try:
                current_mtime = f.stat().st_mtime
            except OSError:
                continue
            if current_mtime <= state['indexed'].get(file_key, {}).get('mtime', 0):
                continue
            records_count = sum(1 for line in f.read_text(encoding='utf-8', errors='ignore').splitlines()
                                 if line.strip())
            print(f'  [DRY RUN] Would index {records_count} record(s) from {f.name}')
            total += records_count
        else:
            count = index_archive_file(conn, f, state, dry_run=False)
            if count > 0:
                print(f'  Indexed {count} message(s) from {f.name}')
            total += count

    if conn:
        conn.close()

    if not dry_run:
        save_state(state)

    print(f'Done. {total} message(s) indexed.')


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Book Dragon session search indexer',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        '--rebuild',
        action='store_true',
        help='Drop the existing database and rebuild from the full archive',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Print what would happen without writing anything',
    )
    args = parser.parse_args()
    run_index(dry_run=args.dry_run, rebuild=args.rebuild)


if __name__ == '__main__':
    main()
