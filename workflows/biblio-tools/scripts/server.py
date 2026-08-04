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
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP  # runtime-guard: launched via launch.py

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
PYTHON = sys.executable

# ---------------------------------------------------------------------------
# Lifecycle — watchdog for orphan prevention
# ---------------------------------------------------------------------------
_last_activity = time.monotonic()
_IDLE_TIMEOUT_SECS = 4 * 3600  # 4 hours

def _touch_activity():
    global _last_activity
    _last_activity = time.monotonic()


def _stdin_watchdog():
    """Exit if stdin closes or no tool calls arrive before the idle timeout."""
    while True:
        time.sleep(10)
        try:
            if sys.stdin.closed:
                os._exit(0)
            if hasattr(sys.stdin, "buffer") and sys.stdin.buffer.closed:
                os._exit(0)
        except Exception:
            os._exit(0)
        if time.monotonic() - _last_activity > _IDLE_TIMEOUT_SECS:
            os._exit(0)


threading.Thread(target=_stdin_watchdog, daemon=True).start()

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "biblio-tools",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _run_script(cmd: list[str]) -> dict:
    """Run a project script and return structured output."""
    _touch_activity()
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


async def _run_json_script(cmd: list[str]) -> dict:
    """Run a project script that emits JSON on stdout (via --json) and parse it.

    On success returns {"success": True, "result": <parsed JSON>}. On a non-zero
    exit or unparseable output, returns {"success": False, "stderr", "return_code"}
    so the caller gets a structured error. The knowledge-graph CLI's unknown-id
    case (exit 2, stderr carries difflib suggestions) surfaces naturally here.
    """
    raw = await _run_script(cmd)
    if not raw.get("success"):
        return {
            "success": False,
            "stderr": raw.get("stderr") or raw.get("error", ""),
            "return_code": raw.get("return_code"),
        }
    try:
        return {"success": True, "result": json.loads(raw["stdout"])}
    except (ValueError, KeyError) as exc:
        return {
            "success": False,
            "stderr": f"could not parse JSON output: {exc}",
            "return_code": raw.get("return_code"),
        }


# ---------------------------------------------------------------------------
# Knowledge-graph query dispatcher — pure argv assembly (unit-testable)
# ---------------------------------------------------------------------------
_KG_RUN_PY = "workflows/knowledge-graph/scripts/run.py"
_KG_NEEDS_ID = ("node", "neighbors", "impact", "subtree", "path", "sessions")


def build_kg_query_argv(
    command: str,
    *,
    id: str | None = None,
    target: str | None = None,
    direction: str = "both",
    edge_type: str | None = None,
    undirected: bool = False,
    no_backrefs: bool = False,
    save: bool = False,
    from_index: bool = False,
    include_memory: bool = False,
    include_wiki: bool = False,
    include_journal: bool = False,
    include_conversation: bool = False,
    terms: str | None = None,
    limit: int = 10,
    since: str | None = None,
    ai: str | None = None,
    source: str | None = None,
) -> tuple[list[str] | None, str | None]:
    """Validate required args and assemble the run.py argv for a read-only
    knowledge-graph query command.

    Returns (argv, None) on success or (None, error_message) when a required
    argument is missing. Pure (no I/O) so the required-arg matrix and argv
    construction can be unit-tested without spawning a subprocess.
    """
    if command in _KG_NEEDS_ID and not id:
        role = "source" if command == "path" else "id"
        return None, f"command {command!r} requires `{role}` (the `id` argument)."
    if command == "path" and not target:
        return None, "command 'path' requires both `id` (source) and `target`."

    argv = [PYTHON, str(PROJECT_ROOT / _KG_RUN_PY), command]

    # Positional argument(s)
    if command == "path":
        argv += [id, target]
    elif command in _KG_NEEDS_ID:
        argv.append(id)

    # Command-specific flags
    if command == "neighbors":
        if direction == "in":
            argv.append("--in")
        elif direction == "out":
            argv.append("--out")
        if edge_type:
            argv += ["--type", edge_type]
    if command == "path" and undirected:
        argv.append("--undirected")
    if command == "validate":
        if no_backrefs:
            argv.append("--no-backrefs")
        if save:
            argv.append("--save")
    if command == "sessions":
        argv += ["--limit", str(limit)]
        if terms:
            argv += ["--terms", terms]
        if since:
            argv += ["--since", since]
        if ai:
            argv += ["--ai", ai]
        if source:
            argv += ["--source", source]

    # Flags shared by every read-only command
    argv.append("--json")
    if from_index:
        argv.append("--from-index")
    if include_memory:
        argv += ["--layer", "memory"]
    if include_wiki:
        argv += ["--layer", "wiki"]
    if include_journal:
        argv += ["--layer", "journal"]
    if include_conversation:
        argv += ["--layer", "conversation"]
    return argv, None


# ---------------------------------------------------------------------------
# Tools — script wrappers
# ---------------------------------------------------------------------------
@mcp.tool()
async def run_audit(save: bool = False, with_graph: bool = True) -> dict:
    """Run the structural audit across all project directories.

    Checks for: missing CONTEXT.md/LOG.md files, missing required sections,
    broken Contents paths, unlisted subdirectories, dead Obsidian links,
    AGENTS.md line-count threshold, and code hygiene issues. By default it also
    validates the structural knowledge graph and merges its actionable (WARN/FAIL)
    findings under a `knowledge-graph` label, so a graph regression surfaces in
    the same report; this is additive and advisory (the result is unchanged when
    the graph is clean) and reports a DEGRADED finding if the graph cannot be
    validated.

    Args:
        save: Save the report to workflows/audit/last-report.md.
        with_graph: Also validate the structural knowledge graph (default True).
            Set False to skip it (passes --no-graph) for a faster structural-only run.
    """
    cmd = [PYTHON, str(PROJECT_ROOT / "workflows/audit/scripts/run.py")]
    if save:
        cmd.append("--save")
    if not with_graph:
        cmd.append("--no-graph")
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
# Tools — knowledge graph
# ---------------------------------------------------------------------------
@mcp.tool()
async def build_knowledge_graph(
    dry_run: bool = False,
    include_memory: bool = False,
    include_wiki: bool = False,
    include_journal: bool = False,
    include_conversation: bool = False,
) -> dict:
    """Build (or rebuild) the knowledge-graph index from the project tree and
    write nodes.json / edges.json / meta.json to the gitignored index directory.
    Logs start/finish to the workflow and root LOG.md.

    The graph turns existing project connections (directory hierarchy, Contents
    references, Dependencies, and Obsidian [[links]]) into queryable indexes.
    Run this once per session before relying on query_knowledge_graph with
    from_index=True; the query tool otherwise rebuilds in memory each call.

    By default only the structural (tracked-content) layer is built. Set
    include_memory=True to additionally index the gitignored memory/ store (each
    memory a node, [[links]] between memories as edges). Set include_wiki=True to
    additionally index each wiki's internal pages as a namespaced sub-graph (each
    page a node, intra-wiki [[links]] resolved per-wiki). Set include_journal=True
    to additionally index the gitignored journal entries (each month file a node
    with its outbound links). Set include_conversation=True to additionally index
    the gitignored saved conversations (standalone files and doc-set pages as
    nodes with their outbound links). The flags combine; all four layers are
    opt-in and purely additive, and their output stays in the gitignored index.

    Note: requires Python 3.10+ for this MCP server. If running 3.9, call the
    underlying CLI directly: python workflows/knowledge-graph/scripts/run.py build

    Args:
        dry_run: Report what would be written without modifying files or logs.
        include_memory: Also index the opt-in memory layer (--layer memory).
        include_wiki: Also index the opt-in wiki sub-graph layer (--layer wiki).
        include_journal: Also index the opt-in journal layer (--layer journal).
        include_conversation: Also index the opt-in conversation layer
            (--layer conversation).
    """
    cmd = [
        PYTHON,
        str(PROJECT_ROOT / "workflows/knowledge-graph/scripts/run.py"),
        "build",
    ]
    if dry_run:
        cmd.append("--dry-run")
    if include_memory:
        cmd += ["--layer", "memory"]
    if include_wiki:
        cmd += ["--layer", "wiki"]
    if include_journal:
        cmd += ["--layer", "journal"]
    if include_conversation:
        cmd += ["--layer", "conversation"]
    return await _run_script(cmd)


@mcp.tool()
async def query_knowledge_graph(
    command: Literal[
        "validate", "node", "neighbors", "impact",
        "path", "subtree", "stats", "orphans", "broken", "sessions",
    ],
    id: str | None = None,
    target: str | None = None,
    direction: Literal["in", "out", "both"] = "both",
    edge_type: str | None = None,
    undirected: bool = False,
    no_backrefs: bool = False,
    save: bool = False,
    from_index: bool = False,
    include_memory: bool = False,
    include_wiki: bool = False,
    include_journal: bool = False,
    include_conversation: bool = False,
    terms: str | None = None,
    limit: int = 10,
    since: str | None = None,
    ai: str | None = None,
    source: str | None = None,
) -> dict:
    """Query and traverse the knowledge graph. A single dispatcher over all ten
    read-only commands, returning parsed JSON in a `result` field.

    By default the graph is rebuilt in memory on every call (sub-second, never
    stale). Pass from_index=True to read the saved index after an explicit
    build_knowledge_graph — faster, but only as fresh as the last build.

    By default only the structural layer is queried. Set include_memory=True,
    include_wiki=True, include_journal=True, and/or include_conversation=True to
    rebuild with the opt-in memory, wiki, journal, and/or conversation layers so
    their nodes are inspectable (all ignored when from_index=True, which returns
    whatever layers the last build wrote).

    Commands and their required arguments:
        - stats / orphans / broken / validate — no id needed.
        - node / neighbors / impact / subtree — require `id`.
        - path — requires `id` (the source) and `target`.
        - sessions — requires `id`; returns the session transcripts that mention
          the node (a read-only, best-effort cross-reference into the
          session-search index). An absent or unreadable index returns an empty
          session list, never an error; nothing is persisted.

    Unknown node ids return success=False with the CLI's stderr, which includes
    closest-match suggestions.

    Note: requires Python 3.10+ for this MCP server. If running 3.9, call the
    underlying CLI directly: python workflows/knowledge-graph/scripts/run.py <command>

    Args:
        command: Which read-only graph command to run.
        id: Node id for node/neighbors/impact/subtree, and the source for path
            (e.g. "workflows/audit" or "AGENTS.md").
        target: Target node id for the path command.
        direction: For neighbors — inbound, outbound, or both edges.
        edge_type: For neighbors — restrict to one edge type
            (child|contains|depends_on|references|links_to).
        undirected: For path — treat every edge as bidirectional.
        no_backrefs: For validate — skip the dependency back-reference gap check.
        save: For validate — also write the report to the gitignored
            last-report.md.
        from_index: Read the saved index instead of rebuilding in memory.
        include_memory: Rebuild with the opt-in memory layer (--layer memory).
        include_wiki: Rebuild with the opt-in wiki sub-graph layer (--layer wiki).
        include_journal: Rebuild with the opt-in journal layer (--layer journal).
        include_conversation: Rebuild with the opt-in conversation layer
            (--layer conversation).
        terms: For sessions — override the FTS5 search terms (default: derived
            from the node title).
        limit: For sessions — maximum sessions to return (default: 10).
        since: For sessions — only sessions on or after this YYYY-MM-DD date.
        ai: For sessions — only sessions from this AI (e.g. "Claude Code").
        source: For sessions — only sessions from this source
            (claude-code|cowork).
    """
    argv, error = build_kg_query_argv(
        command,
        id=id,
        target=target,
        direction=direction,
        edge_type=edge_type,
        undirected=undirected,
        no_backrefs=no_backrefs,
        save=save,
        from_index=from_index,
        include_memory=include_memory,
        include_wiki=include_wiki,
        include_journal=include_journal,
        include_conversation=include_conversation,
        terms=terms,
        limit=limit,
        since=since,
        ai=ai,
        source=source,
    )
    if error is not None:
        return {"success": False, "error": error}
    return await _run_json_script(argv)


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
    _touch_activity()
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
    _touch_activity()
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
        with log_path.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(content + sep + entry + "\n")
        return {"success": True, "entry": entry}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mcp.run()
