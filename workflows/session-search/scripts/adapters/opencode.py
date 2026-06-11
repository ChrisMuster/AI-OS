#!/usr/bin/env python3
r"""
opencode.py — OpenCode adapter for Book Dragon session search.

Reads session data from OpenCode's SQLite database:
  ~/.local/share/opencode/opencode.db  (Linux/macOS)
  %USERPROFILE%\.local\share\opencode\opencode.db  (Windows fallback)

The database uses WAL mode with tables for sessions, messages, and parts.
Sessions are linked to projects via projectID.

OpenCode also supports a custom path via OPENCODE_DATA_DIR environment
variable and legacy JSON storage under storage/ directories.

Note: OpenCode is not installed on the development machine. This
adapter is built from official documentation and DeepWiki analysis.
Test when OpenCode is available.
"""

import json
import os
import socket
import sqlite3
import sys
from pathlib import Path
from typing import Iterator

from ._base import TranscriptAdapter

HOSTNAME = socket.gethostname()


def _find_db_path() -> Path | None:
    """Locate the OpenCode SQLite database."""
    custom = os.environ.get('OPENCODE_DATA_DIR', '')
    if custom:
        for data_dir in custom.split(','):
            candidate = Path(data_dir.strip()) / 'opencode.db'
            if candidate.exists():
                return candidate

    candidates = [
        Path.home() / '.local' / 'share' / 'opencode' / 'opencode.db',
        Path(os.environ.get('LOCALAPPDATA', '')) / 'opencode' / 'opencode.db',
        Path(os.environ.get('APPDATA', '')) / 'opencode' / 'opencode.db',
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


class OpenCodeAdapter(TranscriptAdapter):
    """Adapter for OpenCode session transcripts (SQLite)."""

    SOURCE_LABEL = 'opencode'

    def discover(self, project_root: str) -> list:
        """Return the database path if it contains sessions for this project."""
        db_path = _find_db_path()
        if not db_path:
            return []

        project_path = str(Path(project_root).resolve())

        try:
            conn = sqlite3.connect(str(db_path), timeout=5)
            conn.execute('PRAGMA journal_mode=WAL')
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = {row[0] for row in cursor.fetchall()}

            if 'session' not in tables:
                conn.close()
                return []

            cols = conn.execute('PRAGMA table_info(session)').fetchall()
            col_names = {c[1] for c in cols}

            if 'project_id' in col_names or 'projectID' in col_names:
                pid_col = 'project_id' if 'project_id' in col_names else 'projectID'

                if 'project' in tables:
                    proj_cols = conn.execute('PRAGMA table_info(project)').fetchall()
                    proj_col_names = {c[1] for c in proj_cols}
                    path_col = None
                    for candidate in ('path', 'root', 'directory', 'cwd'):
                        if candidate in proj_col_names:
                            path_col = candidate
                            break
                    if path_col:
                        rows = conn.execute(
                            f'SELECT id FROM project WHERE {path_col} = ?',
                            (project_path,)
                        ).fetchall()
                        if not rows:
                            norm = project_path.replace('\\', '/')
                            rows = conn.execute(
                                f'SELECT id FROM project WHERE {path_col} = ?',
                                (norm,)
                            ).fetchall()
                        if rows:
                            conn.close()
                            return [db_path]

            conn.close()
        except Exception:
            pass

        return [db_path] if db_path.exists() else []

    def validate(self, file_path: str) -> list:
        """Check that the OpenCode database has expected tables."""
        warnings = []
        try:
            conn = sqlite3.connect(str(file_path), timeout=5)
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = {row[0] for row in cursor.fetchall()}
            conn.close()
            for expected in ('session', 'message'):
                if expected not in tables:
                    warnings.append(
                        f'opencode: missing expected table "{expected}"'
                    )
        except Exception as e:
            warnings.append(f'opencode: could not read database: {e}')
        return warnings

    def parse(self, file_path: str) -> Iterator[dict]:
        """Yield normalised records from the OpenCode database."""
        try:
            conn = sqlite3.connect(str(file_path), timeout=5)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA journal_mode=WAL')
        except Exception as e:
            print(f'  [WARNING] opencode: error opening database: {e}',
                  file=sys.stderr)
            return

        try:
            sessions = conn.execute(
                'SELECT id, title FROM session ORDER BY rowid'
            ).fetchall()

            for session in sessions:
                session_id = session['id']

                try:
                    messages = conn.execute(
                        'SELECT * FROM message WHERE session_id = ? '
                        'ORDER BY rowid',
                        (session_id,)
                    ).fetchall()
                except Exception:
                    try:
                        messages = conn.execute(
                            'SELECT * FROM message WHERE sessionID = ? '
                            'ORDER BY rowid',
                            (session_id,)
                        ).fetchall()
                    except Exception:
                        continue

                for msg in messages:
                    msg_dict = dict(msg)
                    role = msg_dict.get('role', '')
                    if role not in ('user', 'assistant'):
                        continue
                    content = msg_dict.get('content', '')
                    if isinstance(content, str):
                        content = content.strip()
                    else:
                        continue
                    if not content:
                        continue

                    timestamp = msg_dict.get(
                        'created_at',
                        msg_dict.get('createdAt', msg_dict.get('timestamp', ''))
                    )
                    if isinstance(timestamp, (int, float)):
                        from datetime import datetime, timezone
                        timestamp = datetime.fromtimestamp(
                            timestamp / 1000 if timestamp > 1e12 else timestamp,
                            tz=timezone.utc
                        ).isoformat()

                    yield {
                        'role': role,
                        'content': content,
                        'timestamp': str(timestamp),
                        'session_id': str(session_id),
                        'source': self.SOURCE_LABEL,
                        'hostname': HOSTNAME,
                    }
        except Exception as e:
            print(f'  [WARNING] opencode: error querying database: {e}',
                  file=sys.stderr)
        finally:
            conn.close()
