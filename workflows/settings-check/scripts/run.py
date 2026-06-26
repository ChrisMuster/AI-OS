#!/usr/bin/env python3
"""
run.py — Settings coverage validator for Book Dragon.

Verifies that every command configured to run automatically — hook commands
in .claude/settings.json and Bash commands in scheduled task SKILL.md files —
is covered by at least one allowlist entry across both the project settings
(.claude/settings.json) and the global user settings (~/.claude/settings.json).

Scheduled tasks are also checked against global settings specifically, because
they run outside the project context and may not load project-level settings.
A command covered only by project settings will still prompt for permission
when fired from a scheduled task session.

Additionally checks:
  - All scripts referenced in hook and task commands exist on disk.
  - All Python scripts in workflows/ and journal/scripts/ pass syntax check.
  - No tracked files contain hardcoded absolute paths that would break on
    another machine.

Usage:
    python workflows/settings-check/scripts/run.py [--verbose]

Options:
    --verbose    Show passing checks in addition to failures.
"""

import argparse
import fnmatch
import json
import py_compile
import re
import subprocess
import warnings
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR      = Path(__file__).resolve().parent
WORKFLOW_DIR    = SCRIPT_DIR.parent
PROJECT_ROOT    = WORKFLOW_DIR.parent.parent
SETTINGS_FILE   = PROJECT_ROOT / '.claude' / 'settings.json'
GLOBAL_SETTINGS = Path.home() / '.claude' / 'settings.json'
TASKS_DIR       = Path.home() / '.claude' / 'scheduled-tasks'
WORKFLOW_LOG    = WORKFLOW_DIR / 'LOG.md'

# Directories to scan for Python syntax checking
PYTHON_SCAN_DIRS = [
    PROJECT_ROOT / 'workflows',
    PROJECT_ROOT / 'journal' / 'scripts',
]

# Patterns that indicate a hardcoded absolute path tied to a specific machine.
# Each captures the account-name segment so a placeholder can be skipped.
ABS_PATH_PATTERNS = [
    re.compile(r'C:[/\\]Users[/\\](<?[A-Za-z][\w.\-]*>?)'),
    re.compile(r'/home/(<?[A-Za-z][\w.\-]*>?)'),
]

# Account-name segments that are placeholders, not a real machine account, so a
# path like C:/Users/Name/... in documentation or a test fixture is not a real
# hardcoded value. Kept as a small local copy rather than cross-importing the
# personal-data-guard workflow (same convention, decoupled).
PLACEHOLDER_USERS = {
    "name", "names", "yourname", "your-name", "username", "user", "users",
    "you", "youruser", "example", "someone", "admin", "administrator",
    "public", "default", "me", "home",
}


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
def load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
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
# Global settings coverage for scheduled tasks
# ---------------------------------------------------------------------------
def check_global_task_coverage(
    task_commands: list[tuple[str, str]],
    project_allowlist: list[str],
    global_allowlist: list[str],
) -> list:
    """
    Scheduled tasks run outside the project context and may not load project
    settings. If a task command is covered only by the project allowlist and
    NOT by the global allowlist, emit a warning — it may still prompt at runtime.
    """
    findings = []
    for task_name, command in task_commands:
        proj_ok, _ = is_covered(command, project_allowlist)
        glob_ok, _ = is_covered(command, global_allowlist)
        if proj_ok and not glob_ok:
            findings.append(('WARN', f'Task:{task_name}',
                f'Covered by project settings only — scheduled tasks may not load '
                f'project settings and could still prompt. '
                f'Add a matching entry to ~/.claude/settings.json.'))
    return findings


# ---------------------------------------------------------------------------
# Script existence check
# ---------------------------------------------------------------------------
def extract_script_path(command: str) -> str | None:
    """Extract the .py script path from a python invocation like 'python path/script.py args'."""
    parts = command.strip().split()
    if not parts or parts[0] not in ('python', 'python3', 'py'):
        return None
    if len(parts) < 2:
        return None
    script_arg = parts[1].strip('"\'')
    if script_arg.endswith('.py'):
        return script_arg
    return None


def check_script_existence(
    hook_commands: list[tuple[str, str]],
    task_commands: list[tuple[str, str]],
) -> list:
    findings = []
    for label, command in hook_commands + task_commands:
        script_str = extract_script_path(command)
        if not script_str:
            continue
        script_path = Path(script_str)
        if not script_path.is_absolute():
            script_path = PROJECT_ROOT / script_path
        if not script_path.exists():
            findings.append(('FAIL', label, f'Script not found on disk: {script_str}'))
    return findings


# ---------------------------------------------------------------------------
# Python syntax check
# ---------------------------------------------------------------------------
def check_script_syntax() -> tuple[list, int]:
    """
    Run py_compile on every .py file in PYTHON_SCAN_DIRS.
    Reports both hard errors (SyntaxError) and warnings (SyntaxWarning).
    Returns (findings, count_of_files_checked).
    """
    findings = []
    count = 0
    for scan_dir in PYTHON_SCAN_DIRS:
        if not scan_dir.exists():
            continue
        for py_file in sorted(scan_dir.rglob('*.py')):
            count += 1
            caught_warnings = []
            try:
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter('always')
                    py_compile.compile(str(py_file), doraise=True)
                caught_warnings = [
                    w for w in caught if issubclass(w.category, SyntaxWarning)
                ]
            except py_compile.PyCompileError as e:
                findings.append(('FAIL', rel(py_file), f'Syntax error: {e}'))
                continue
            for w in caught_warnings:
                findings.append(('WARN', rel(py_file),
                    f'Syntax warning ({w.category.__name__}): {w.message}'))
    return findings, count


# ---------------------------------------------------------------------------
# Absolute path audit on tracked files
# ---------------------------------------------------------------------------
def _is_documentation_line(line: str) -> bool:
    """Return True for lines that mention absolute paths as examples, not real hardcoded values."""
    stripped = line.strip()
    if stripped.startswith('#'):   # Python / shell comment
        return True
    if '→' in line:               # before → after transformation example
        return True
    if 'e.g.' in line:            # explicit example marker in prose
        return True
    return False


def _is_placeholder_user(seg: str) -> bool:
    """True for an account-name segment that is a documentation/fixture
    placeholder (Name, <username>, user, ...) rather than a real account."""
    s = seg.strip().lower()
    return s.startswith('<') or s in PLACEHOLDER_USERS


def _line_has_real_abs_path(line: str) -> bool:
    """True if the line carries an absolute home path with a real account name.
    Paths whose account segment is a placeholder are treated as examples or test
    fixtures, not machine-specific values, so they are not flagged."""
    for pattern in ABS_PATH_PATTERNS:
        for m in pattern.finditer(line):
            if not _is_placeholder_user(m.group(1)):
                return True
    return False


def check_absolute_paths() -> tuple[list, int]:
    """
    List all files tracked by git and scan each line for hardcoded absolute paths.
    Lines that are comments or labelled as documentation examples are skipped to
    avoid false positives from explanatory text.
    Returns (findings, count_of_files_checked).
    """
    findings = []
    try:
        result = subprocess.run(
            ['git', 'ls-files'],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        tracked = [f for f in result.stdout.strip().splitlines() if f]
    except Exception as exc:
        return [('WARN', 'git ls-files', f'Could not list tracked files: {exc}')], 0

    count = 0
    for file_str in tracked:
        full_path = PROJECT_ROOT / file_str
        if not full_path.exists() or not full_path.is_file():
            continue
        try:
            content = full_path.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue
        count += 1
        for line_num, line in enumerate(content.splitlines(), 1):
            if _is_documentation_line(line):
                continue
            if _line_has_real_abs_path(line):
                findings.append(('WARN', file_str,
                    f'Line {line_num}: hardcoded absolute path - will break on another machine'))
                break  # stop scanning this file after first real match
    return findings, count


# ---------------------------------------------------------------------------
# Check runner
# ---------------------------------------------------------------------------
Finding = tuple[str, str, str]  # (level, source_label, message)


def run_check(verbose: bool = False) -> tuple[list[Finding], dict]:
    findings: list[Finding] = []
    stats = {
        'project_allowlist': 0,
        'global_allowlist': 0,
        'hooks': 0,
        'tasks': 0,
        'tasks_dir_missing': False,
        'scripts_checked': 0,
        'tracked_files_checked': 0,
    }

    if not SETTINGS_FILE.exists():
        findings.append(('FAIL', rel(SETTINGS_FILE), '.claude/settings.json not found'))
        return findings, stats

    project_settings = load_settings(SETTINGS_FILE)
    if not project_settings:
        findings.append(('FAIL', rel(SETTINGS_FILE), 'Could not parse .claude/settings.json'))
        return findings, stats

    global_settings = load_settings(GLOBAL_SETTINGS)
    project_allowlist = get_allowlist(project_settings)
    global_allowlist  = get_allowlist(global_settings)
    combined_allowlist = project_allowlist + global_allowlist

    stats['project_allowlist'] = len(project_allowlist)
    stats['global_allowlist']  = len(global_allowlist)

    if not combined_allowlist:
        findings.append(('WARN', rel(SETTINGS_FILE),
            'permissions.allow is empty in both project and global settings — '
            'all commands will prompt for permission'))

    # --- Hook commands (checked against combined allowlist) ---
    hook_commands = get_hook_commands(project_settings)
    stats['hooks'] = len(hook_commands)
    for label, command in hook_commands:
        covered, pattern = is_covered(command, combined_allowlist)
        if covered:
            if verbose:
                findings.append(('INFO', label,
                    f'OK — "{command}" matched by {pattern}'))
        else:
            findings.append(('FAIL', label,
                f'No allowlist entry covers hook command: "{command}"'))

    # --- Scheduled task commands ---
    task_commands: list[tuple[str, str]] = []
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
            covered, pattern = is_covered(command, combined_allowlist)
            if covered:
                if verbose:
                    findings.append(('INFO', f'Task:{task_name}',
                        f'OK — "{command}" matched by {pattern}'))
            else:
                findings.append(('FAIL', f'Task:{task_name}',
                    f'No allowlist entry covers scheduled task command: "{command}"'))

        # Extra: warn if task is covered only by project settings, not global
        findings.extend(check_global_task_coverage(
            task_commands, project_allowlist, global_allowlist
        ))

    # --- Script existence ---
    existence_findings = check_script_existence(hook_commands, task_commands)
    findings.extend(existence_findings)
    if verbose and not existence_findings:
        findings.append(('INFO', 'Script existence',
            'All referenced scripts found on disk'))

    # --- Python syntax ---
    syntax_findings, scripts_checked = check_script_syntax()
    stats['scripts_checked'] = scripts_checked
    findings.extend(syntax_findings)
    if verbose and not syntax_findings and scripts_checked > 0:
        findings.append(('INFO', 'Python syntax',
            f'All {scripts_checked} scripts passed syntax check'))

    # --- Absolute path audit ---
    abs_findings, files_checked = check_absolute_paths()
    stats['tracked_files_checked'] = files_checked
    findings.extend(abs_findings)
    if verbose and not abs_findings and files_checked > 0:
        findings.append(('INFO', 'Absolute paths',
            f'All {files_checked} tracked files clean'))

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
        f'**Allowlist entries:** {stats["project_allowlist"]} project, '
        f'{stats["global_allowlist"]} global',
        f'**Hook commands checked:** {stats["hooks"]}',
    ]
    if stats['tasks_dir_missing']:
        lines.append('**Scheduled tasks:** directory not found (fresh clone — skipped)')
    else:
        lines.append(f'**Scheduled task commands checked:** {stats["tasks"]}')
    lines.append(f'**Python scripts syntax-checked:** {stats["scripts_checked"]}')
    lines.append(f'**Tracked files checked for absolute paths:** {stats["tracked_files_checked"]}')
    lines.append('')

    if not fails and not warns:
        lines.append('All checks passed.')
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
        description='Validate settings coverage, script health, and path safety.',
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
    warns = sum(1 for f in findings if f[0] == 'WARN')
    note = (
        f'Settings check complete. {stats["hooks"]} hook(s), '
        f'{stats["tasks"]} task(s), '
        f'{stats["scripts_checked"]} script(s), '
        f'{stats["tracked_files_checked"]} tracked file(s) checked. '
        f'{fails} failure(s), {warns} warning(s).'
    )
    append_log(WORKFLOW_LOG, ts, 'completed', note)


if __name__ == '__main__':
    main()
