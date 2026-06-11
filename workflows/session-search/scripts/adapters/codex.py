#!/usr/bin/env python3
r"""
codex.py — Codex CLI/Desktop adapter for Book Dragon session search.

Reads session transcripts from:
  %USERPROFILE%\.codex\sessions\YYYY\MM\DD\rollout-<timestamp>-<uuid>.jsonl

Format: each line is a JSON event with a 'type' field. Relevant types:
  - session_meta  → session ID, cwd, originator, timestamp
  - event_msg     → type: user_message (user's typed message) or agent_message (assistant reply)
  - response_item → type: message, role: assistant (assistant content blocks)

After the initial import, ongoing capture is handled by the Stop hook
in .codex/config.toml which calls archive.py --all.
"""

import json
import socket
import sys
from pathlib import Path
from typing import Iterator

from ._base import TranscriptAdapter

HOSTNAME = socket.gethostname()


def _extract_content_blocks(content) -> str:
    """Extract plain text from Codex content block arrays."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get('type') in ('output_text', 'text'):
                    parts.append(block.get('text', ''))
            elif isinstance(block, str):
                parts.append(block)
        return '\n'.join(parts).strip()
    return ''


class CodexAdapter(TranscriptAdapter):
    """Adapter for Codex CLI/Desktop session transcripts."""

    SOURCE_LABEL = 'codex'

    def discover(self, project_root: str) -> list:
        """Find all Codex session JSONL files that belong to this project."""
        sessions_dir = Path.home() / '.codex' / 'sessions'
        if not sessions_dir.exists():
            return []

        project_path = Path(project_root).resolve()
        results = []

        for jsonl_file in sorted(sessions_dir.rglob('*.jsonl')):
            try:
                for line in jsonl_file.read_text(
                    encoding='utf-8', errors='ignore'
                ).splitlines()[:5]:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        if event.get('type') == 'session_meta':
                            cwd = event.get('payload', {}).get('cwd', '')
                            if cwd and Path(cwd).resolve() == project_path:
                                results.append(jsonl_file)
                            break
                    except json.JSONDecodeError:
                        continue
            except Exception:
                continue

        return results

    def validate(self, file_path: str) -> list:
        """Check for expected fields in a Codex session JSONL file."""
        warnings = []
        try:
            lines = Path(file_path).read_text(
                encoding='utf-8', errors='ignore'
            ).splitlines()
            found_meta = False
            for line in lines[:10]:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if event.get('type') == 'session_meta':
                        found_meta = True
                        payload = event.get('payload', {})
                        missing = [
                            f for f in ('id', 'timestamp', 'cwd')
                            if f not in payload
                        ]
                        if missing:
                            warnings.append(
                                f'codex: missing expected fields {missing} in '
                                f'session_meta — adapter may need updating'
                            )
                        break
                except json.JSONDecodeError:
                    continue
            if not found_meta:
                warnings.append('codex: no session_meta event found in first 10 lines')
        except Exception as e:
            warnings.append(f'codex: could not read {file_path}: {e}')
        return warnings

    def parse(self, file_path: str) -> Iterator[dict]:
        """Yield normalised records from a Codex session JSONL file."""
        jsonl_path = Path(file_path)
        session_id = ''
        session_timestamp = ''

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

                event_type = event.get('type')
                timestamp = event.get('timestamp', '')

                if event_type == 'session_meta':
                    payload = event.get('payload', {})
                    session_id = payload.get('id', jsonl_path.stem)
                    session_timestamp = payload.get('timestamp', timestamp)
                    continue

                if event_type == 'event_msg':
                    payload = event.get('payload', {})
                    msg_type = payload.get('type')

                    if msg_type == 'user_message':
                        content = payload.get('message', '').strip()
                        if content:
                            yield {
                                'role': 'user',
                                'content': content,
                                'timestamp': timestamp or session_timestamp,
                                'session_id': session_id,
                                'source': self.SOURCE_LABEL,
                                'hostname': HOSTNAME,
                            }

                    elif msg_type == 'agent_message':
                        content = payload.get('message', '').strip()
                        if content:
                            yield {
                                'role': 'assistant',
                                'content': content,
                                'timestamp': timestamp or session_timestamp,
                                'session_id': session_id,
                                'source': self.SOURCE_LABEL,
                                'hostname': HOSTNAME,
                            }

                elif event_type == 'response_item':
                    payload = event.get('payload', {})
                    if payload.get('type') == 'message' and payload.get('role') == 'assistant':
                        content = _extract_content_blocks(payload.get('content', []))
                        if content:
                            yield {
                                'role': 'assistant',
                                'content': content,
                                'timestamp': timestamp or session_timestamp,
                                'session_id': session_id,
                                'source': self.SOURCE_LABEL,
                                'hostname': HOSTNAME,
                            }

        except Exception as e:
            print(f'  [WARNING] codex: error parsing {jsonl_path.name}: {e}',
                  file=sys.stderr)
