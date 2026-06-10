"""
biblio-tools MCP server — exposes Book Dragon project scripts as typed tools.

Usage (standalone):
    python workflows/biblio-tools/scripts/server.py

Normally started automatically by the AI's MCP client via the registration
in settings (e.g. .claude/settings.json, .gemini/settings.json, etc.).

This server wraps existing project scripts as MCP tools with typed parameters.
The underlying scripts are unchanged and still work via direct shell commands
for AIs without MCP support.
"""

import asyncio
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
PYTHON = sys.executable

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "biblio-tools",
    description="Book Dragon project tools — typed wrappers for project scripts.",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _run_script(cmd: list[str]) -> dict:
    """Run a project script and return structured output."""
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            cwd=str(PROJECT_ROOT),
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        return {
            "success": process.returncode == 0,
            "stdout": stdout_bytes.decode("utf-8", errors="replace").strip(),
            "stderr": stderr_bytes.decode("utf-8", errors="replace").strip(),
            "return_code": process.returncode,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Tools — script wrappers
# ---------------------------------------------------------------------------
@mcp.tool()
async def run_audit(save: bool = False) -> dict:
    """Run the structural audit across all project directories.

    Checks for: missing CONTEXT.md/LOG.md files, missing required sections,
    broken Contents paths, unlisted subdirectories, dead Obsidian links,
    AGENTS.md line-count threshold, and code hygiene issues.

    Args:
        save: Save the report to workflows/audit/last-report.md.
    """
    cmd = [PYTHON, str(PROJECT_ROOT / "workflows/audit/scripts/run.py")]
    if save:
        cmd.append("--save")
    return await _run_script(cmd)


@mcp.tool()
async def run_link_check(
    mode: Literal["link", "audit", "fix"] = "audit",
    dry_run: bool = False,
    save: bool = False,
) -> dict:
    """Manage Obsidian wiki links in CONTEXT.md files.

    Args:
        mode: Operation mode.
            - link: Insert [[links]] after backtick path references.
            - audit: Report dead [[links]] (default).
            - fix: Auto-fix dead links where possible.
        dry_run: Preview changes without writing.
        save: Save the report to workflows/link-check/last-report.md.
    """
    cmd = [PYTHON, str(PROJECT_ROOT / "workflows/link-check/scripts/run.py")]
    cmd.append(f"--{mode}")
    if dry_run:
        cmd.append("--dry-run")
    if save:
        cmd.append("--save")
    return await _run_script(cmd)


@mcp.tool()
async def run_new_month(
    month: str,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    """Create the journal entry file for a specified month.

    Args:
        month: Target month in YYYY-MM format (e.g. "2026-07").
        dry_run: Preview without creating files.
        force: Create even if the month is more than one month ahead.
    """
    # Validate month format (basic check — the script validates further)
    if len(month) != 7 or month[4] != "-":
        return {
            "success": False,
            "error": f"Invalid month format: {month!r}. Expected YYYY-MM.",
        }
    cmd = [
        PYTHON,
        str(PROJECT_ROOT / "journal/scripts/new-month.py"),
        "--month",
        month,
    ]
    if dry_run:
        cmd.append("--dry-run")
    if force:
        cmd.append("--force")
    return await _run_script(cmd)


@mcp.tool()
async def run_session_search_index(
    rebuild: bool = False,
    dry_run: bool = False,
) -> dict:
    """Archive completed sessions and refresh the session search index.

    Args:
        rebuild: Rebuild the entire index from scratch.
        dry_run: Preview without modifying the index.
    """
    cmd = [PYTHON, str(PROJECT_ROOT / "workflows/session-search/scripts/index.py")]
    if rebuild:
        cmd.append("--rebuild")
    if dry_run:
        cmd.append("--dry-run")
    return await _run_script(cmd)


@mcp.tool()
async def run_settings_check(verbose: bool = False) -> dict:
    """Validate that automated commands are covered by allowlist entries.

    Checks hooks and scheduled tasks against settings.json allowlist patterns.
    Catches permission-prompt bugs before they occur.

    Args:
        verbose: Show detailed output including passing checks.
    """
    cmd = [PYTHON, str(PROJECT_ROOT / "workflows/settings-check/scripts/run.py")]
    if verbose:
        cmd.append("--verbose")
    return await _run_script(cmd)


# ---------------------------------------------------------------------------
# Tools — setup verification
# ---------------------------------------------------------------------------
@mcp.tool()
async def verify_setup(ai_name: str) -> dict:
    """Verify that the project environment is correctly configured for a given AI.

    Runs deterministic checks: AGENTS.md exists, Python version, wrapper file,
    MCP configuration, .env, and AI-specific config files. Returns structured
    results with PASS/FAIL/WARN status for each check.

    Args:
        ai_name: The AI to verify setup for (e.g. "Claude Code", "Gemini CLI").
            Use the exact name as shown in AGENT-SETUP.md.
    """
    cmd = [
        PYTHON,
        str(PROJECT_ROOT / "workflows/biblio-tools/scripts/verify.py"),
        "--ai",
        ai_name,
        "--json",
    ]
    return await _run_script(cmd)


# ---------------------------------------------------------------------------
# Tools — utilities
# ---------------------------------------------------------------------------
@mcp.tool()
async def get_timestamp() -> str:
    """Get the current timestamp in ISO 8601 format with timezone offset.

    Returns a string like "2026-06-09T23:20:28+01:00".
    Cross-platform replacement for date +"%Y-%m-%dT%H:%M:%S%:z".
    """
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@mcp.tool()
async def append_log(
    directory: str,
    actor: str,
    action: Literal[
        "created", "modified", "started", "completed", "failed", "archived"
    ],
    note: str,
) -> dict:
    """Append a formatted entry to a directory's LOG.md file.

    Generates the timestamp automatically and formats the entry per
    project conventions. Saves two tool calls (fetch timestamp + edit file)
    every time a log entry is written.

    Args:
        directory: Project-root-relative path to the directory containing the
            LOG.md file (e.g. "workflows/audit", "skills/web-research").
            Use "." for the root LOG.md.
        actor: Who performed the action (usually "Biblio" or the user's name).
        action: One of: created, modified, started, completed, failed, archived.
        note: Short description of what happened.
    """
    # Resolve and validate path
    if directory == ".":
        log_path = PROJECT_ROOT / "LOG.md"
    else:
        resolved = (PROJECT_ROOT / directory).resolve()
        if not str(resolved).startswith(str(PROJECT_ROOT.resolve())):
            return {
                "success": False,
                "error": "Path traversal detected — directory must be within the project.",
            }
        log_path = resolved / "LOG.md"

    if not log_path.exists():
        rel = log_path.relative_to(PROJECT_ROOT)
        return {"success": False, "error": f"LOG.md not found at {rel}"}

    # Generate timestamp and format entry
    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    entry = f"[{ts}] | Actor: {actor} | Action: {action} | Note: {note}"

    # Append — ensure a leading newline if the file doesn't end with one
    try:
        content = log_path.read_text(encoding="utf-8")
        sep = "" if content.endswith("\n") else "\n"
        log_path.write_text(content + sep + entry + "\n", encoding="utf-8")
        return {"success": True, "entry": entry}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mcp.run()
