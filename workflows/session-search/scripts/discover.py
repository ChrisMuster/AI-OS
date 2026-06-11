#!/usr/bin/env python3
"""
discover.py — New AI source discovery tool for Book Dragon session search.

Inspects the data footprint of an AI tool that is not yet integrated with
session-search. Either identifies a compatible existing adapter or scaffolds
a blank adapter module ready to be filled in.

Usage:
  python discover.py cursor
  python discover.py "github copilot"
  python discover.py chatgpt
"""

import json
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR   = Path(__file__).resolve().parent
WORKFLOW_DIR = SCRIPT_DIR.parent

# Sources already handled by existing adapters
KNOWN_SOURCES = {'claude-code', 'cowork', 'codex'}

# Common Windows locations to inspect, parametrised by AI name
CANDIDATE_TEMPLATES = [
    '{appdata}\\{name}',
    '{localappdata}\\{name}',
    '{home}\\.{name}',
    '{appdata}\\Roaming\\{name}',
    '{appdata}\\Local\\{name}',
]

# File extensions that commonly contain transcript / session data
TRANSCRIPT_EXTENSIONS = {'.jsonl', '.json', '.log', '.txt', '.db', '.sqlite', '.sqlite3'}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_candidates(ai_name: str) -> list:
    """Expand candidate path templates for the given AI name."""
    appdata    = os.environ.get('APPDATA', str(Path.home() / 'AppData' / 'Roaming'))
    localappdata = os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))
    home       = str(Path.home())
    name_lower = ai_name.lower().replace(' ', '-')
    name_nospace = ai_name.lower().replace(' ', '')

    paths = []
    for template in CANDIDATE_TEMPLATES:
        for variant in (name_lower, name_nospace, ai_name.lower()):
            p = Path(template.format(
                appdata=appdata,
                localappdata=localappdata,
                home=home,
                name=variant,
            ))
            if p not in paths:
                paths.append(p)
    return paths


def scan_location(directory: Path) -> list:
    """Return a summary of transcript-like files found in directory."""
    found = []
    for ext in TRANSCRIPT_EXTENSIONS:
        try:
            matches = [f for f in directory.rglob(f'*{ext}') if f.is_file()]
        except PermissionError:
            continue
        if matches:
            total_bytes = sum(f.stat().st_size for f in matches)
            latest = max(matches, key=lambda f: f.stat().st_mtime)
            found.append({
                'directory': directory,
                'extension': ext,
                'count': len(matches),
                'total_bytes': total_bytes,
                'latest': latest,
            })
    return found


def inspect_format(file_path: Path) -> dict:
    """Sample a file to detect its format and check for known patterns."""
    try:
        sample = file_path.read_text(encoding='utf-8', errors='ignore')[:3000]
    except Exception:
        return {'format': 'unreadable', 'resembles': 'unknown', 'fields': []}

    lines = [l.strip() for l in sample.splitlines() if l.strip()]
    json_objects = 0
    fields_found = set()

    for line in lines[:15]:
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                json_objects += 1
                fields_found.update(obj.keys())
        except Exception:
            pass

    if json_objects >= 2:
        # Resembles Claude Code format?
        if {'type', 'message', 'timestamp', 'sessionId'}.issubset(fields_found):
            return {'format': 'jsonl', 'resembles': 'claude-code', 'fields': sorted(fields_found)[:15]}
        # Resembles Cowork format?
        if {'type', 'message', '_audit_timestamp', 'session_id'}.issubset(fields_found):
            return {'format': 'jsonl', 'resembles': 'cowork', 'fields': sorted(fields_found)[:15]}
        return {'format': 'jsonl', 'resembles': 'unknown', 'fields': sorted(fields_found)[:15]}

    # Try single-object JSON
    try:
        obj = json.loads(sample)
        if isinstance(obj, (dict, list)):
            return {'format': 'json', 'resembles': 'unknown', 'fields': []}
    except Exception:
        pass

    return {'format': 'unknown', 'resembles': 'unknown', 'fields': []}


def scaffold_adapter(ai_name: str) -> Path:
    """Create a blank adapter file for a new AI source and return its path."""
    adapter_dir = SCRIPT_DIR / 'adapters'
    safe_name   = ai_name.lower().replace(' ', '_').replace('-', '_')
    class_name  = ''.join(part.title() for part in safe_name.split('_')) + 'Adapter'
    adapter_file = adapter_dir / f'{safe_name}.py'

    if adapter_file.exists():
        print(f'  Adapter already exists at: {adapter_file.relative_to(WORKFLOW_DIR)}')
        return adapter_file

    content = f'''#!/usr/bin/env python3
"""
{ai_name} adapter for Book Dragon session search.
One-time historical import adapter — implement discover(), validate(), and parse().

Generated by discover.py — fill in the TODOs below.
"""
import socket
from pathlib import Path
from typing import Iterator

from ._base import TranscriptAdapter

HOSTNAME = socket.gethostname()


class {class_name}(TranscriptAdapter):
    """Adapter for {ai_name} session transcripts."""

    SOURCE_LABEL = "{safe_name}"

    def discover(self, project_root: str) -> list:
        """Return paths to all {ai_name} transcript files for this project.

        TODO: Replace the example below with the correct path pattern.
        """
        # Example:
        # return sorted(Path("~/.{safe_name}/sessions").expanduser().glob("*.jsonl"))
        raise NotImplementedError(
            f"Fill in the discovery path for {ai_name} transcripts."
        )

    def validate(self, file_path: str) -> list:
        """Check expected fields. Return warning strings (empty list = clean).

        TODO: Add field checks specific to {ai_name} format.
        """
        warnings = []
        return warnings

    def parse(self, file_path: str) -> Iterator[dict]:
        """Yield normalised records from a {ai_name} transcript file.

        Each record must contain:
            role        (str)  "user" or "assistant"
            content     (str)  plain text only
            timestamp   (str)  ISO 8601 string
            session_id  (str)  unique session identifier
            source      (str)  self.SOURCE_LABEL
            hostname    (str)  HOSTNAME

        TODO: Adapt the skeleton below to the actual {ai_name} file format.
        """
        import json
        session_id = Path(file_path).stem

        for line in Path(file_path).read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            # TODO: adapt these field names to match {ai_name}'s actual schema
            role = event.get("role", "")
            if role not in ("user", "assistant"):
                continue
            content = event.get("content", "").strip()
            if not content:
                continue

            yield {{
                "role":       role,
                "content":    content,
                "timestamp":  event.get("timestamp", ""),
                "session_id": event.get("session_id", session_id),
                "source":     self.SOURCE_LABEL,
                "hostname":   HOSTNAME,
            }}
'''

    adapter_file.write_text(content, encoding='utf-8')
    return adapter_file


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description='Discover the data footprint of a new AI tool',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('ai_name', help='Name of the AI tool to inspect (e.g. "cursor", "copilot")')
    args = parser.parse_args()

    ai_name  = args.ai_name
    safe     = ai_name.lower().replace(' ', '-')
    print(f'Inspecting data footprint for: {ai_name}\n')

    # Already handled?
    if safe in KNOWN_SOURCES or ai_name.lower().replace(' ', '_') in {s.replace('-', '_') for s in KNOWN_SOURCES}:
        print(f'  [{ai_name}] is already a supported source. No new adapter needed.')
        return

    # Scan common OS locations
    candidates = resolve_candidates(ai_name)
    all_found  = []
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            all_found.extend(scan_location(candidate))

    if not all_found:
        print(f'  No local data directories found for "{ai_name}".')
        print()
        print('  This AI tool does not appear to store transcripts locally in common locations.')
        print('  Recommended approach: implement a session-end hook that calls archive.py')
        print('  directly with the session content:')
        print()
        print('    python workflows/session-search/scripts/archive.py --hook')
        print()
        print('  The hook should pipe session context JSON to stdin.')
        print('  Refer to PROPOSAL.md for the expected archive record format.')
        return

    # Report findings
    print(f'  Found {len(all_found)} location(s) with potential transcript data:\n')
    resembles_known = None

    for loc in all_found:
        size_mb = loc['total_bytes'] / (1024 * 1024)
        print(f'  Path:  {loc["directory"]}')
        print(f'         {loc["count"]} {loc["extension"]} file(s)  ({size_mb:.1f} MB)')

        fmt = inspect_format(loc['latest'])
        if fmt['resembles'] == 'claude-code':
            print('         Format resembles Claude Code JSONL — the claude_code adapter may work directly.')
            resembles_known = 'claude-code'
        elif fmt['resembles'] == 'cowork':
            print('         Format resembles Cowork audit.jsonl — the cowork adapter may work directly.')
            resembles_known = 'cowork'
        elif fmt['fields']:
            print(f'         Format: {fmt["format"]}  |  fields: {", ".join(fmt["fields"][:8])}')
        else:
            print(f'         Format: {fmt["format"]}')
        print()

    # Recommendations
    print('  Next steps:')

    if resembles_known:
        print(f'  1. The file format resembles the existing {resembles_known!r} adapter.')
        print(f'     Try running archive.py --all to see if sessions are captured.')
        print(f'     If not, create a new adapter using the instructions below.')
    else:
        print('  1. A new adapter is needed. Scaffolding a blank adapter now...')
        adapter_file = scaffold_adapter(ai_name)
        rel = adapter_file.relative_to(WORKFLOW_DIR)
        print(f'     Created: {rel}')
        print()
        print('  2. Open the scaffolded file and fill in discover() and parse()')
        print('     using the file format details shown above.')
        print()
        print('  3. Test the import with:')
        print('       python workflows/session-search/scripts/index.py --dry-run')
        print()
        print('  4. Register the adapter in:')
        print('       workflows/session-search/scripts/adapters/_registry.py')


if __name__ == '__main__':
    main()
