#!/usr/bin/env python3
r"""
cursor.py — Cursor adapter for Book Dragon session search.

Reads agent transcripts from:
  %USERPROFILE%\.cursor\projects\<project-id>\agent-transcripts\*.jsonl

Cursor stores transcripts in two layouts:
  - Flat:   agent-transcripts/<session-id>.jsonl
  - Nested: agent-transcripts/<session-id>/<session-id>.jsonl

JSONL format: each line is a JSON record with a 'type' field:
  - user      → message.role="user", message.content[].text
  - assistant → message.role="assistant", message.content[].text
  - tool_call → tool invocations (skipped)
  - result    → tool results (skipped)

Project identification: the <project-id> directory under ~/.cursor/projects/
is a hash or encoded name. We check all project directories and match by
reading session context when available.

Note: Cursor is not installed on the development machine. This adapter
is built from community reverse-engineering and documentation. Test
when Cursor is available.
"""

import json
import socket
import sys
from pathlib import Path
from typing import Iterator

from ._base import TranscriptAdapter

HOSTNAME = socket.gethostname()


def _extract_content(message: dict) -> str:
    """Extract plain text from a Cursor message object."""
    content = message.get('content', '')
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                text = block.get('text', '')
                if text:
                    parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
        return '\n'.join(parts).strip()
    return ''


class CursorAdapter(TranscriptAdapter):
    """Adapter for Cursor agent transcript files."""

    SOURCE_LABEL = 'cursor'

    def discover(self, project_root: str) -> list:
        """Find all Cursor agent-transcript JSONL files for this project."""
        cursor_projects = Path.home() / '.cursor' / 'projects'
        if not cursor_projects.exists():
            return []

        results = []
        project_path = Path(project_root).resolve()
        project_name = project_path.name.lower()

        for project_dir in cursor_projects.iterdir():
            if not project_dir.is_dir():
                continue
            transcripts_dir = project_dir / 'agent-transcripts'
            if not transcripts_dir.exists():
                continue
            dir_name = project_dir.name.lower()
            if project_name in dir_name or dir_name in project_name:
                for f in sorted(transcripts_dir.rglob('*.jsonl')):
                    results.append(f)
                continue
            for f in sorted(transcripts_dir.rglob('*.jsonl')):
                results.append(f)

        return results

    def validate(self, file_path: str) -> list:
        """Check for expected fields in a Cursor transcript JSONL file."""
        warnings = []
        try:
            lines = Path(file_path).read_text(
                encoding='utf-8', errors='ignore'
            ).splitlines()
            found_message = False
            for line in lines[:20]:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get('type') in ('user', 'assistant'):
                        found_message = True
                        break
                except json.JSONDecodeError:
                    continue
            if not found_message:
                warnings.append(
                    'cursor: no user/assistant records found in first 20 lines'
                )
        except Exception as e:
            warnings.append(f'cursor: could not read {file_path}: {e}')
        return warnings

    def parse(self, file_path: str) -> Iterator[dict]:
        """Yield normalised records from a Cursor agent transcript JSONL file."""
        jsonl_path = Path(file_path)
        session_id = jsonl_path.stem

        try:
            for line in jsonl_path.read_text(
                encoding='utf-8', errors='ignore'
            ).splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                rec_type = rec.get('type', '')
                if rec_type not in ('user', 'assistant'):
                    continue

                message = rec.get('message', rec)
                role = message.get('role', rec_type)
                if role not in ('user', 'assistant'):
                    continue

                content = _extract_content(message)
                if not content:
                    continue

                session_id_field = rec.get('session_id', session_id)

                yield {
                    'role': role,
                    'content': content,
                    'timestamp': rec.get('timestamp', ''),
                    'session_id': str(session_id_field),
                    'source': self.SOURCE_LABEL,
                    'hostname': HOSTNAME,
                }

        except Exception as e:
            print(f'  [WARNING] cursor: error parsing {jsonl_path.name}: {e}',
                  file=sys.stderr)
