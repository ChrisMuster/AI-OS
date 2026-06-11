#!/usr/bin/env python3
r"""
cline.py — Cline (VS Code extension) adapter for Book Dragon session search.

Reads task transcripts from:
  %APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\tasks\<task-id>\

Each task directory contains:
  - api_conversation_history.json — full API conversation (role/content pairs)
  - ui_messages.json — UI-formatted messages (richer, but less stable)
  - task_metadata.json — task context including workspace path

We use api_conversation_history.json as the primary source (most stable
format) and task_metadata.json for project filtering.

The conversation history follows the Anthropic API message format:
  [{"role": "user", "content": [...]}, {"role": "assistant", "content": [...]}]

Note: Cline is not installed on the development machine. This adapter
is built from official documentation and GitHub sources. Test when
Cline is available.
"""

import json
import os
import socket
import sys
from pathlib import Path
from typing import Iterator

from ._base import TranscriptAdapter

HOSTNAME = socket.gethostname()


def _extract_text(content) -> str:
    """Extract plain text from Cline content (string or content blocks)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get('type') == 'text':
                    text = block.get('text', '')
                    if text:
                        parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
        return '\n'.join(parts).strip()
    return ''


def _find_tasks_dir() -> Path | None:
    """Locate the Cline tasks directory."""
    candidates = []
    appdata = os.environ.get('APPDATA', '')
    if appdata:
        candidates.append(
            Path(appdata) / 'Code' / 'User' / 'globalStorage'
            / 'saoudrizwan.claude-dev' / 'tasks'
        )
        candidates.append(
            Path(appdata) / 'Cursor' / 'User' / 'globalStorage'
            / 'saoudrizwan.claude-dev' / 'tasks'
        )

    candidates.extend([
        Path.home() / '.config' / 'Code' / 'User' / 'globalStorage'
        / 'saoudrizwan.claude-dev' / 'tasks',
        Path.home() / '.vscode' / 'extensions' / 'globalStorage'
        / 'saoudrizwan.claude-dev' / 'tasks',
    ])

    for c in candidates:
        if c.exists():
            return c
    return None


class ClineAdapter(TranscriptAdapter):
    """Adapter for Cline VS Code extension task transcripts."""

    SOURCE_LABEL = 'cline'

    def discover(self, project_root: str) -> list:
        """Find all Cline task directories for this project."""
        tasks_dir = _find_tasks_dir()
        if not tasks_dir:
            return []

        project_path = Path(project_root).resolve()
        results = []

        for task_dir in sorted(tasks_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            history_file = task_dir / 'api_conversation_history.json'
            if not history_file.exists():
                continue

            metadata_file = task_dir / 'task_metadata.json'
            if metadata_file.exists():
                try:
                    meta = json.loads(metadata_file.read_text(
                        encoding='utf-8', errors='ignore'
                    ))
                    workspace = meta.get(
                        'dirAbsolutePath',
                        meta.get('workspacePath',
                                 meta.get('cwd', ''))
                    )
                    if workspace:
                        try:
                            if Path(workspace).resolve() == project_path:
                                results.append(history_file)
                                continue
                        except Exception:
                            pass
                except Exception:
                    pass

            results.append(history_file)

        return results

    def validate(self, file_path: str) -> list:
        """Check for expected structure in a Cline conversation history."""
        warnings = []
        try:
            data = json.loads(Path(file_path).read_text(
                encoding='utf-8', errors='ignore'
            ))
            if not isinstance(data, list):
                warnings.append(
                    'cline: api_conversation_history.json is not an array'
                )
            elif data:
                first = data[0]
                if not isinstance(first, dict) or 'role' not in first:
                    warnings.append(
                        'cline: first message missing "role" field'
                    )
        except json.JSONDecodeError:
            warnings.append(f'cline: {file_path} is not valid JSON')
        except Exception as e:
            warnings.append(f'cline: could not read {file_path}: {e}')
        return warnings

    def parse(self, file_path: str) -> Iterator[dict]:
        """Yield normalised records from a Cline conversation history."""
        try:
            data = json.loads(Path(file_path).read_text(
                encoding='utf-8', errors='ignore'
            ))
        except Exception as e:
            print(f'  [WARNING] cline: error reading {file_path}: {e}',
                  file=sys.stderr)
            return

        if not isinstance(data, list):
            return

        task_id = Path(file_path).parent.name
        metadata_file = Path(file_path).parent / 'task_metadata.json'
        timestamp = ''
        if metadata_file.exists():
            try:
                meta = json.loads(metadata_file.read_text(
                    encoding='utf-8', errors='ignore'
                ))
                timestamp = meta.get('createdAt', meta.get('timestamp', ''))
            except Exception:
                pass

        for msg in data:
            if not isinstance(msg, dict):
                continue
            role = msg.get('role', '')
            if role not in ('user', 'assistant'):
                continue
            content = _extract_text(msg.get('content', ''))
            if not content:
                continue

            msg_ts = msg.get('timestamp', msg.get('ts', timestamp))

            yield {
                'role': role,
                'content': content,
                'timestamp': str(msg_ts) if msg_ts else '',
                'session_id': task_id,
                'source': self.SOURCE_LABEL,
                'hostname': HOSTNAME,
            }
