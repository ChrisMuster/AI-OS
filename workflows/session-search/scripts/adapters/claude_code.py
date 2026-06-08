#!/usr/bin/env python3
"""
claude_code.py — Claude Code adapter for Book Dragon session search.

One-time historical import of Claude Code session transcripts from:
  %USERPROFILE%\.claude\projects\<sanitized-cwd>\<session-uuid>.jsonl

After the initial import, ongoing capture is handled automatically by the
Stop / PreCompact / Notification hooks in .claude/settings.json.
"""

import json
import socket
from pathlib import Path
from typing import Iterator

from ._base import TranscriptAdapter

HOSTNAME = socket.gethostname()


def _extract_text(content_raw) -> str:
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


class ClaudeCodeAdapter(TranscriptAdapter):
    """
    Adapter for Claude Code session transcripts.

    Reads: %USERPROFILE%\\.claude\\projects\\<sanitized-cwd>\\<session-uuid>.jsonl
    """

    SOURCE_LABEL = 'claude-code'

    def discover(self, project_root: str) -> list:
        """Find all Claude Code session JSONL files that belong to this project."""
        claude_dir = Path.home() / '.claude' / 'projects'
        if not claude_dir.exists():
            return []

        project_path = Path(project_root).resolve()
        results = []

        for project_dir in claude_dir.iterdir():
            if not project_dir.is_dir():
                continue

            # Check each JSONL for a matching cwd
            for jsonl_file in sorted(project_dir.glob('*.jsonl')):
                try:
                    for line in jsonl_file.read_text(
                        encoding='utf-8', errors='ignore'
                    ).splitlines()[:30]:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                            cwd = event.get('cwd', '')
                            if cwd and Path(cwd).resolve() == project_path:
                                results.append(jsonl_file)
                                break
                        except json.JSONDecodeError:
                            continue
                except Exception:
                    continue

        return results

    def validate(self, file_path: str) -> list:
        """Check for expected fields in a Claude Code JSONL session file."""
        warnings = []
        try:
            lines = Path(file_path).read_text(
                encoding='utf-8', errors='ignore'
            ).splitlines()
            for line in lines[:50]:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if event.get('type') in ('user', 'assistant'):
                        missing = [
                            f for f in ('message', 'timestamp', 'sessionId')
                            if f not in event
                        ]
                        if missing:
                            warnings.append(
                                f'claude-code: missing expected fields {missing} in '
                                f'{Path(file_path).name} — adapter may need updating'
                            )
                        break
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            warnings.append(f'claude-code: could not read {file_path}: {e}')
        return warnings

    def parse(self, file_path: str) -> Iterator[dict]:
        """Yield normalised records from a Claude Code JSONL session file."""
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
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if event.get('type') not in ('user', 'assistant'):
                    continue

                if event.get('sessionId'):
                    session_id = event['sessionId']

                message = event.get('message', {})
                role    = message.get('role', event['type'])
                content = _extract_text(message.get('content', ''))

                if not content:
                    continue

                yield {
                    'role':       role,
                    'content':    content,
                    'timestamp':  event.get('timestamp', ''),
                    'session_id': session_id,
                    'source':     self.SOURCE_LABEL,
                    'hostname':   HOSTNAME,
                }

        except Exception as e:
            print(f'  [WARNING] claude-code: error parsing {jsonl_path.name}: {e}')
