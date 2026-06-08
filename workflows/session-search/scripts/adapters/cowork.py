#!/usr/bin/env python3
r"""
cowork.py — Cowork (FleetView) adapter for Book Dragon session search.

One-time historical import of Cowork session transcripts from:
  %APPDATA%\Claude\local-agent-mode-sessions\<orgId>\<sessionId>\local_<conv-uuid>\audit.jsonl

Note: Cowork does not support hooks (GitHub Issue #40495).
After the initial import, ongoing Cowork capture is handled by the hourly
scheduled task running archive.py --all.

Recommendation: use Claude Code rather than Cowork for reliable real-time archiving.
"""

import json
import os
import socket
from pathlib import Path
from typing import Iterator

from ._base import TranscriptAdapter
from .claude_code import _extract_text

HOSTNAME = socket.gethostname()


class CoworkAdapter(TranscriptAdapter):
    """
    Adapter for Cowork (FleetView) session transcripts.

    Reads: %APPDATA%\\Claude\\local-agent-mode-sessions\\
           <orgId>\\<sessionId>\\local_<conv-uuid>\\audit.jsonl
    """

    SOURCE_LABEL = 'cowork'

    def _base_dir(self) -> Path:
        appdata = os.environ.get('APPDATA', '')
        return Path(appdata) / 'Claude' / 'local-agent-mode-sessions'

    def discover(self, project_root: str) -> list:
        """Find all Cowork audit.jsonl files."""
        base = self._base_dir()
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

    def validate(self, file_path: str) -> list:
        """Check for expected fields in a Cowork audit.jsonl file."""
        warnings = []
        try:
            lines = Path(file_path).read_text(
                encoding='utf-8', errors='ignore'
            ).splitlines()
            for line in lines[:30]:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if event.get('type') in ('user', 'assistant'):
                        missing = [
                            f for f in ('message', '_audit_timestamp')
                            if f not in event
                        ]
                        if missing:
                            warnings.append(
                                f'cowork: missing expected fields {missing} in '
                                f'{Path(file_path).parent.name} — adapter may need updating'
                            )
                        break
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            warnings.append(f'cowork: could not read {file_path}: {e}')
        return warnings

    def parse(self, file_path: str) -> Iterator[dict]:
        """Yield normalised records from a Cowork audit.jsonl file."""
        audit_path = Path(file_path)
        # Default session ID is the conversation folder name
        session_id = audit_path.parent.name

        try:
            for line in audit_path.read_text(
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

                # cwd is on the system/init line; also get session_id if present
                if event_type == 'system' and event.get('subtype') == 'init':
                    if event.get('session_id'):
                        session_id = event['session_id']
                    continue

                if event_type not in ('user', 'assistant'):
                    continue

                if event.get('session_id'):
                    session_id = event['session_id']

                message = event.get('message', {})
                role    = message.get('role', event_type)
                content = _extract_text(message.get('content', ''))

                if not content:
                    continue

                yield {
                    'role':       role,
                    'content':    content,
                    'timestamp':  event.get('_audit_timestamp', ''),
                    'session_id': session_id,
                    'source':     self.SOURCE_LABEL,
                    'hostname':   HOSTNAME,
                }

        except Exception as e:
            print(f'  [WARNING] cowork: error parsing {audit_path.parent.name}: {e}')
