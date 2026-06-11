#!/usr/bin/env python3
r"""
continue_dev.py — Continue.dev adapter for Book Dragon session search.

Reads session transcripts from:
  %USERPROFILE%\.continue\sessions\<uuid>.json

Each session JSON file contains:
  - sessionId       → unique UUID
  - title           → display name
  - workspaceDirectory → project path (used for project filtering)
  - history         → array of ChatHistoryItem objects
  - chatModelTitle  → selected model name

A sessions.json index file also exists at ~/.continue/sessions/sessions.json
with lightweight metadata (sessionId, title, dateCreated, workspaceDirectory,
messageCount). Used for fast discovery without reading every session file.

Note: Continue.dev is not installed on the development machine. This
adapter is built from official documentation and DeepWiki analysis.
Test when Continue.dev is available.
"""

import json
import socket
import sys
from pathlib import Path
from typing import Iterator

from ._base import TranscriptAdapter

HOSTNAME = socket.gethostname()


class ContinueDevAdapter(TranscriptAdapter):
    """Adapter for Continue.dev session transcripts."""

    SOURCE_LABEL = 'continue-dev'

    def discover(self, project_root: str) -> list:
        """Find all Continue.dev session files for this project."""
        sessions_dir = Path.home() / '.continue' / 'sessions'
        if not sessions_dir.exists():
            return []

        project_path = Path(project_root).resolve()
        results = []

        index_file = sessions_dir / 'sessions.json'
        if index_file.exists():
            try:
                index = json.loads(index_file.read_text(
                    encoding='utf-8', errors='ignore'
                ))
                if isinstance(index, list):
                    for entry in index:
                        ws_dir = entry.get('workspaceDirectory', '')
                        if ws_dir:
                            try:
                                if Path(ws_dir).resolve() == project_path:
                                    sid = entry.get('sessionId', '')
                                    session_file = sessions_dir / f'{sid}.json'
                                    if session_file.exists():
                                        results.append(session_file)
                            except Exception:
                                continue
                    if results:
                        return sorted(results)
            except Exception:
                pass

        for session_file in sorted(sessions_dir.glob('*.json')):
            if session_file.name == 'sessions.json':
                continue
            try:
                data = json.loads(session_file.read_text(
                    encoding='utf-8', errors='ignore'
                )[:2000])
                ws_dir = data.get('workspaceDirectory', '')
                if ws_dir and Path(ws_dir).resolve() == project_path:
                    results.append(session_file)
            except Exception:
                continue

        return results

    def validate(self, file_path: str) -> list:
        """Check for expected fields in a Continue.dev session file."""
        warnings = []
        try:
            data = json.loads(Path(file_path).read_text(
                encoding='utf-8', errors='ignore'
            ))
            for field in ('sessionId', 'history'):
                if field not in data:
                    warnings.append(
                        f'continue-dev: missing expected field "{field}"'
                    )
            if 'history' in data and not isinstance(data['history'], list):
                warnings.append('continue-dev: "history" is not an array')
        except json.JSONDecodeError:
            warnings.append(f'continue-dev: {file_path} is not valid JSON')
        except Exception as e:
            warnings.append(f'continue-dev: could not read {file_path}: {e}')
        return warnings

    def parse(self, file_path: str) -> Iterator[dict]:
        """Yield normalised records from a Continue.dev session file."""
        try:
            data = json.loads(Path(file_path).read_text(
                encoding='utf-8', errors='ignore'
            ))
        except Exception as e:
            print(f'  [WARNING] continue-dev: error reading {file_path}: {e}',
                  file=sys.stderr)
            return

        session_id = data.get('sessionId', Path(file_path).stem)
        history = data.get('history', [])

        for item in history:
            if not isinstance(item, dict):
                continue
            message = item.get('message', item)
            role = message.get('role', '')
            if role not in ('user', 'assistant'):
                continue
            content = message.get('content', '')
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        text = block.get('text', block.get('value', ''))
                        if text:
                            parts.append(text)
                    elif isinstance(block, str):
                        parts.append(block)
                content = '\n'.join(parts)
            if not isinstance(content, str):
                continue
            content = content.strip()
            if not content:
                continue

            yield {
                'role': role,
                'content': content,
                'timestamp': item.get('timestamp', ''),
                'session_id': session_id,
                'source': self.SOURCE_LABEL,
                'hostname': HOSTNAME,
            }
