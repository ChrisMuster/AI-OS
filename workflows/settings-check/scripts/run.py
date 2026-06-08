#!/usr/bin/env python3
"""
run.py — Settings coverage validator for Book Dragon.

Verifies that every command configured to run automatically — hook commands
in .claude/settings.json and Bash commands in scheduled task SKILL.md files —
is covered by at least one allowlist entry in permissions.allow.

A command is "covered" if at least one pattern matches it using glob matching
(the same logic Claude Code uses). Patterns have the form Bash(<glob>).

An uncovered command will prompt for permission every time it fires, defeating
the purpose of silent automation.

Usage:
    python workflows/settings-check/scripts/run.py [--verbose]

Options:
    --verbose    Show passing checks in addition to failures.
"""

import argparse
import fnmatch
import json
import re
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR    = Path(__file__).resolve().parent
WORKFLOW_DIR  = SCRIPT_DIR.parent
PROJECT_ROOT  = WORKFLOW_DIR.parent.parent
SETTINGS_FILE = PROJECT_ROOT / '.claude' / 'settings.json'
TASKS_DIR     = Path.home() / '.claude' / 'scheduled-tasks'
WORKFLOW_LOG  = WORKFLOW_DIR / 'LOG.md'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def now_ts() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def append_log(path: Path, ts: str, action: str, note: str) -> None:
    entry = f'[{ts}] | Actor: Biblio | Action: {action} | Note: {note}\n'
    with path.open('a', encoding='utf-8') as f:
        f.write(entry)


def rel(path: Path) -> str:
    """Return path relative to project root with forward slashes, or str(path) if outside."""
    try:
        return str(path.relative_to(PROJECT_ROOT)).replace('\\', '/')
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# Settings parsing
# ---------------------------------------------------------------------------
def load_settings() -> dict:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def get_allowlist(settings: dict) -> list[str]:
    """Return all allowlist pattern strings from permissions.allow."""
    return settings.get('permissions', {}).get('allow', [])


def get_hook_commands(settings: dict) -> list[tuple[str, str]]:
    """
    Return (source_label, command) for every hook command in settings.hooks.
    source_label identifies the hook event and matcher, e.g. 'Hook:Stop'.
    """
    results = []
    for event, hook_list in settings.get('hooks', {}).items():
        for entry in hook_list:
            matcher = entry.get('matcher', '')
            label = f'Hook:{event}' + (f'({matcher})' if matcher else '')
            for hook in entry.get('hooks', []):
                if hook.get('type') == 'command' and hook.get('command'):
                    results.append((label, hook['command']))
    return results


# ---------------------------------------------------------------------------
# Scheduled task SKILL.md parsing
# ---------------------------------------------------------------------------
def extract_commands_from_skill(skill_path: Path) -> list[str]:
    """
    Extract Bash commands from fenced code blocks in a SKILL.md file.
    Returns all non-empty, non-comment lines found inside ``` blocks.
    """
    try:
        content = skill_path.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return []
    commands = []
    for block in re.finditer(r'```[^\n]*\n(.*?)```', content, re.DOTALL):
        for line in block.group(1).splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                commands.append(line)
    return commands


def get_task_commands() -> list[tuple[str, str]]:
    """
    Return (task_name, command) for each command found in scheduled task SKILL.md
    files under ~/.claude/scheduled-tasks/. Returns an empty list if that directory
    does not exist (expected on a fresh clone before first session).
    """
    if not TASKS_DIR.exists():
        return []
    results = []
    for task_dir in sorted(TASKS_DIR.iterdir()):
        if not task_dir.is_dir():
            continue
        skill = task_dir / 'SKILL.md'
        if not skill.exists():
            continue
        for cmd in extract_commands_from_skill(skill):
            results.append((task_dir.name, cmd))
    return results


# ---------------------------------------------------------------------------
# Coverage matching
# ---------------------------------------------------------------------------
def is_covered(command: str, allowlist: list[str]) -> tuple[bool, str]:
    """
    Return (True, matching_pattern) if any allowlist entry covers the command,
    else (False, ''). Uses Python fnmatch glob matching on the pattern inside
    Bash(...).
    """
    for pattern in allowlist:
        if pattern.startswith('Bash(') and pattern.endswith(')'):
            glob = pattern[5:-1]
            if fnmatch.fnmatch(command, glob):
                return True, pattern
    return False, ''


# ---------------------------------------------------------------------------
# Check runner
# ---------------------------------------------------------------------------
Finding = tuple[str, str, str]  # (level, source_label, message)


def run_check(verbose: bool = False) -> tuple[list[Finding], dict]:
    findings: list[Finding] = []
    stats = {
        'allowlist': 0,
        'hooks': 0,
        'tasks': 0,
        'tasks_dir_missing': False,
    }

    if not SETTINGS_FILE.exists():
        findings.append(('FAIL', rel(SETTINGS_FILE),
            '.claude/settings.json not found'))
        return findings, stats

    settings = load_settings()
    if not settings:
        findings.append(('FAIL', rel(SETTINGS_FILE),
            'Could not parse .claude/settings.json'))
        return findings, stats

    allowlist = get_allowlist(settings)
    stats['allowlist'] = len(allowlist)

    if not allowlist:
        findings.append(('WARN', rel(SETTINGS_FILE),
            'permissions.allow is empty — all commands will prompt for permission'))

    # --- Hook commands ---
    hook_commands = get_hook_commands(settings)
    stats['hooks'] = len(hook_commands)
    for label, command in hook_commands:
        covered, pattern = is_covered(command, allowlist)
        if covered:
            if verbose:
                findings.append(('INFO', label,
                    f'OK — "{command}" matched by {pattern}'))
        else:
            findings.append(('FAIL', label,
                f'No allowlist entry covers hook command: "{command}"'))

    # --- Scheduled task commands ---
    if not TASKS_DIR.exists():
        stats['tasks_dir_missing'] = True
        findings.append(('INFO', '~/.claude/scheduled-tasks/',
            'Directory not found — skipping scheduled task check '
            '(expected on a fresh clone before first session startup)'))
    else:
        task_commands = get_task_commands()
        stats['tasks'] = len(task_commands)
        if not task_commands:
            findings.append(('INFO', '~/.claude/scheduled-tasks/',
                'No SKILL.md files found in scheduled tasks directory'))
        for task_name, command in task_commands:
            covered, pattern = is_covered(command, allowlist)
            if covered:
                if verbose:
                    findings.append(('INFO', f'Task:{task_name}',
                        f'OK — "{command}" matched by {pattern}'))
            else:
                findings.append(('FAIL', f'Task:{task_name}',
                    f'No allowlist entry covers scheduled task command: "{command}"'))

    return findings, stats


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------
def format_report(findings: list[Finding], stats: dict, verbose: bool) -> str:
    run_at = datetime.now().astimezone().isoformat(timespec='seconds')
    fails = [f for f in findings if f[0] == 'FAIL']
    warns = [f for f in findings if f[0] == 'WARN']
    infos = [f for f in findings if f[0] == 'INFO']

    lines = [
        '# Book Dragon — Settings Coverage Check',
        '',
        f'**Run at:** {run_at}',
        f'**Allowlist entries:** {stats["allowlist"]}',
        f'**Hook commands checked:** {stats["hooks"]}',
    ]
    if stats['tasks_dir_missing']:
        lines.append('**Scheduled tasks:** directory not found '
                     '(fresh clone — skipped)')
    else:
        lines.append(f'**Scheduled task commands checked:** {stats["tasks"]}')
    lines.append('')

    if not fails and not warns:
        lines.append('All commands are covered by allowlist entries.')
        lines.append('')
    else:
        if fails:
            lines += ['## Failures', '']
            for _, source, msg in fails:
                lines.append(f'- `{source}` — {msg}')
            lines.append('')
        if warns:
            lines += ['## Warnings', '']
            for _, source, msg in warns:
                lines.append(f'- `{source}` — {msg}')
            lines.append('')

    if verbose and infos:
        lines += ['## Passing checks', '']
        for _, source, msg in infos:
            lines.append(f'- `{source}` — {msg}')
        lines.append('')

    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description='Validate that all automated commands are covered by allowlist entries.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show passing checks in addition to failures.',
    )
    args = parser.parse_args()

    ts = now_ts()
    append_log(WORKFLOW_LOG, ts, 'started', 'Running settings coverage check.')

    findings, stats = run_check(verbose=args.verbose)
    report = format_report(findings, stats, verbose=args.verbose)
    print(report)

    fails = sum(1 for f in findings if f[0] == 'FAIL')
    note = (
        f'Settings check complete. {stats["hooks"]} hook command(s), '
        f'{stats["tasks"]} scheduled task command(s) checked. '
        f'{fails} failure(s).'
    )
    append_log(WORKFLOW_LOG, ts, 'completed', note)


if __name__ == '__main__':
    main()
