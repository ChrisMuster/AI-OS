#!/usr/bin/env python3
r"""
copilot.py — GitHub Copilot CLI adapter for Book Dragon session search.

Reads session transcripts from:
  %USERPROFILE%\.copilot\session-state\<uuid>\events.jsonl

Each session directory also contains:
  - workspace.yaml — session metadata including cwd, git_root, branch
  - vscode.metadata.json — VS Code context (often empty)

Format: each line in events.jsonl is a JSON event with a 'type' field:
  - session.start   → sessionId, context.cwd
  - user.message    → data.content (user's typed message)
  - assistant.message → data.content (assistant reply), data.toolRequests
  - assistant.reasoning → data.content (chain-of-thought; skipped)
  - assistant.turn_start/end → turn boundaries (skipped)
  - tool.execution_start/complete → tool activity (skipped)

After the initial import, ongoing capture is handled by the background
scheduler (scheduler.py) which calls archive.py --all hourly.
"""

import json
import socket
import sys
from pathlib import Path
from typing import Iterator

from ._base import TranscriptAdapter

HOSTNAME = socket.gethostname()


def _read_workspace_yaml(session_dir: Path) -> dict:
    """Parse workspace.yaml from a Copilot session directory."""
    yaml_path = session_dir / 'workspace.yaml'
    if not yaml_path.exists():
        return {}
    result = {}
    try:
        for line in yaml_path.read_text(encoding='utf-8', errors='ignore').splitlines():
            if ':' in line:
                key, _, value = line.partition(':')
                result[key.strip()] = value.strip()
    except Exception:
        pass
    return result


class CopilotAdapter(TranscriptAdapter):
    """Adapter for GitHub Copilot CLI session transcripts."""

    SOURCE_LABEL = 'copilot'

    def discover(self, project_root: str) -> list:
        """Find all Copilot session files that belong to this project."""
        session_state_dir = Path.home() / '.copilot' / 'session-state'
        if not session_state_dir.exists():
            return []

        project_path = Path(project_root).resolve()
        results = []

        for session_dir in sorted(session_state_dir.iterdir()):
            if not session_dir.is_dir():
                continue
            events_file = session_dir / 'events.jsonl'
            if not events_file.exists():
                continue
            meta = _read_workspace_yaml(session_dir)
            cwd = meta.get('cwd', '')
            if cwd:
                try:
                    if Path(cwd).resolve() == project_path:
                        results.append(events_file)
                except Exception:
                    continue

        return results

    def validate(self, file_path: str) -> list:
        """Check for expected fields in a Copilot events.jsonl file."""
        warnings = []
        try:
            lines = Path(file_path).read_text(
                encoding='utf-8', errors='ignore'
            ).splitlines()
            found_start = False
            for line in lines[:10]:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if event.get('type') == 'session.start':
                        found_start = True
                        data = event.get('data', {})
                        if 'sessionId' not in data:
                            warnings.append(
                                'copilot: session.start missing sessionId field'
                            )
                        break
                except json.JSONDecodeError:
                    continue
            if not found_start:
                warnings.append('copilot: no session.start event found in first 10 lines')
        except Exception as e:
            warnings.append(f'copilot: could not read {file_path}: {e}')
        return warnings

    def parse(self, file_path: str) -> Iterator[dict]:
        """Yield normalised records from a Copilot events.jsonl file."""
        events_path = Path(file_path)
        session_dir = events_path.parent
        meta = _read_workspace_yaml(session_dir)
        session_id = meta.get('id', events_path.parent.name)

        try:
            for line in events_path.read_text(
                encoding='utf-8', errors='ignore'
            ).splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                event_type = event.get('type', '')
                timestamp = event.get('timestamp', '')

                if event_type == 'session.start':
                    data = event.get('data', {})
                    session_id = data.get('sessionId', session_id)
                    continue

                if event_type == 'user.message':
                    content = event.get('data', {}).get('content', '').strip()
                    if content:
                        yield {
                            'role': 'user',
                            'content': content,
                            'timestamp': timestamp,
                            'session_id': session_id,
                            'source': self.SOURCE_LABEL,
                            'hostname': HOSTNAME,
                        }

                elif event_type == 'assistant.message':
                    content = event.get('data', {}).get('content', '').strip()
                    if content:
                        yield {
                            'role': 'assistant',
                            'content': content,
                            'timestamp': timestamp,
                            'session_id': session_id,
                            'source': self.SOURCE_LABEL,
                            'hostname': HOSTNAME,
                        }

        except Exception as e:
            print(f'  [WARNING] copilot: error parsing {events_path.name}: {e}',
                  file=sys.stderr)
