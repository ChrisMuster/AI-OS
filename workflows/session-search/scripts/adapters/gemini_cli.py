#!/usr/bin/env python3
r"""
gemini_cli.py — Gemini CLI / Antigravity CLI adapter for Book Dragon session search.

Reads session transcripts from two locations:

  Gemini CLI (legacy, sunsetting June 18 2026):
    ~/.gemini/tmp/<project_hash>/chats/*.jsonl  (JSONL format)
    ~/.gemini/tmp/<project_hash>/chats/*.json   (legacy JSON format)

  Antigravity CLI (replacement):
    ~/.gemini/antigravity/brain/<conversation-id>/.system_generated/logs/transcript.jsonl

Both use JSONL with the same record types:
  - session_metadata → sessionId, projectHash, model, startTime
  - user             → id, content[].text (user message)
  - gemini           → id, content[].text (assistant reply)
  - message_update   → id, tokens (metadata update; skipped)

Legacy JSON format: single JSON object with messages array.

Project identification for Gemini CLI: the <project_hash> directory
name is a hash of the project root path. For Antigravity CLI: the
projectHash field inside session_metadata records is matched.

Note: Neither tool is installed on the development machine. This
adapter is built from official documentation and community sources.
Test when either tool is available.
"""

import hashlib
import json
import socket
import sys
from pathlib import Path
from typing import Iterator

from ._base import TranscriptAdapter

HOSTNAME = socket.gethostname()


def _project_hash(project_root: str) -> str:
    """Compute the Gemini CLI project hash for a given path."""
    return hashlib.sha256(project_root.encode('utf-8')).hexdigest()[:16]


def _extract_text(content) -> str:
    """Extract plain text from Gemini content blocks."""
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


class GeminiCliAdapter(TranscriptAdapter):
    """Adapter for Gemini CLI session transcripts."""

    SOURCE_LABEL = 'gemini-cli'

    def _find_gemini_chats_dir(self, project_root: str) -> Path | None:
        """Locate the Gemini CLI chats directory for this project."""
        gemini_tmp = Path.home() / '.gemini' / 'tmp'
        if not gemini_tmp.exists():
            return None
        project_path = str(Path(project_root).resolve())
        p_hash = _project_hash(project_path)
        candidate = gemini_tmp / p_hash / 'chats'
        if candidate.exists():
            return candidate
        for hash_dir in gemini_tmp.iterdir():
            if not hash_dir.is_dir():
                continue
            chats_dir = hash_dir / 'chats'
            if not chats_dir.exists():
                continue
            for f in chats_dir.iterdir():
                if f.suffix == '.jsonl':
                    try:
                        first_line = f.read_text(
                            encoding='utf-8', errors='ignore'
                        ).split('\n', 1)[0].strip()
                        if first_line:
                            rec = json.loads(first_line)
                            if rec.get('projectHash') == p_hash:
                                return chats_dir
                    except Exception:
                        continue
        return None

    def _find_antigravity_transcripts(self, project_root: str) -> list:
        """Find Antigravity CLI transcript files for this project."""
        brain_dir = Path.home() / '.gemini' / 'antigravity' / 'brain'
        if not brain_dir.exists():
            return []
        project_path = str(Path(project_root).resolve())
        p_hash = _project_hash(project_path)
        results = []
        for conv_dir in brain_dir.iterdir():
            if not conv_dir.is_dir():
                continue
            logs_dir = conv_dir / '.system_generated' / 'logs'
            if not logs_dir.exists():
                continue
            for transcript in sorted(logs_dir.glob('transcript*.jsonl')):
                try:
                    first_line = transcript.read_text(
                        encoding='utf-8', errors='ignore'
                    ).split('\n', 1)[0].strip()
                    if first_line:
                        rec = json.loads(first_line)
                        if rec.get('projectHash') == p_hash:
                            results.append(transcript)
                            break
                except Exception:
                    continue
        return results

    def discover(self, project_root: str) -> list:
        """Find all Gemini CLI and Antigravity CLI session files."""
        results = []
        chats_dir = self._find_gemini_chats_dir(project_root)
        if chats_dir:
            for ext in ('*.jsonl', '*.json'):
                results.extend(sorted(chats_dir.glob(ext)))
        results.extend(self._find_antigravity_transcripts(project_root))
        return results

    def validate(self, file_path: str) -> list:
        """Check for expected fields in a Gemini CLI session file."""
        warnings = []
        path = Path(file_path)
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
            if path.suffix == '.jsonl':
                lines = text.splitlines()
                if not lines:
                    warnings.append('gemini-cli: empty JSONL file')
                    return warnings
                try:
                    first = json.loads(lines[0].strip())
                    if first.get('type') != 'session_metadata':
                        warnings.append(
                            'gemini-cli: first record is not session_metadata'
                        )
                except json.JSONDecodeError:
                    warnings.append('gemini-cli: first line is not valid JSON')
            else:
                try:
                    json.loads(text)
                except json.JSONDecodeError:
                    warnings.append('gemini-cli: file is not valid JSON')
        except Exception as e:
            warnings.append(f'gemini-cli: could not read {file_path}: {e}')
        return warnings

    def parse(self, file_path: str) -> Iterator[dict]:
        """Yield normalised records from a Gemini CLI session file."""
        path = Path(file_path)
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except Exception as e:
            print(f'  [WARNING] gemini-cli: error reading {path.name}: {e}',
                  file=sys.stderr)
            return

        if path.suffix == '.jsonl':
            yield from self._parse_jsonl(path, text)
        else:
            yield from self._parse_json(path, text)

    def _parse_jsonl(self, path: Path, text: str) -> Iterator[dict]:
        """Parse the JSONL format (current Gemini CLI)."""
        session_id = path.stem

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            rec_type = rec.get('type', '')

            if rec_type == 'session_metadata':
                session_id = rec.get('sessionId', session_id)
                continue

            if rec_type == 'user':
                content = _extract_text(rec.get('content', []))
                if content:
                    yield {
                        'role': 'user',
                        'content': content,
                        'timestamp': rec.get('timestamp', ''),
                        'session_id': session_id,
                        'source': self.SOURCE_LABEL,
                        'hostname': HOSTNAME,
                    }

            elif rec_type == 'gemini':
                content = _extract_text(rec.get('content', []))
                if content:
                    yield {
                        'role': 'assistant',
                        'content': content,
                        'timestamp': rec.get('timestamp', ''),
                        'session_id': session_id,
                        'source': self.SOURCE_LABEL,
                        'hostname': HOSTNAME,
                    }

    def _parse_json(self, path: Path, text: str) -> Iterator[dict]:
        """Parse the legacy JSON format (older Gemini CLI sessions)."""
        session_id = path.stem
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return

        if isinstance(data, dict):
            session_id = data.get('sessionId', session_id)
            messages = data.get('messages', data.get('history', []))
        elif isinstance(data, list):
            messages = data
        else:
            return

        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role_raw = msg.get('role', msg.get('type', ''))
            if role_raw in ('user',):
                role = 'user'
            elif role_raw in ('gemini', 'model', 'assistant'):
                role = 'assistant'
            else:
                continue
            content = _extract_text(msg.get('content', msg.get('parts', [])))
            if content:
                yield {
                    'role': role,
                    'content': content,
                    'timestamp': msg.get('timestamp', ''),
                    'session_id': session_id,
                    'source': self.SOURCE_LABEL,
                    'hostname': HOSTNAME,
                }
