#!/usr/bin/env python3
"""
verify.py — Book Dragon setup verification (doctor pattern).

Checks that the project environment is correctly configured for a given AI.
Returns structured results: each check gets PASS, FAIL, or WARN status.

Usage:
  python workflows/biblio-tools/scripts/verify.py --ai "Claude Code"
  python workflows/biblio-tools/scripts/verify.py --ai "Gemini CLI" --json
  python workflows/biblio-tools/scripts/verify.py --list
  python workflows/biblio-tools/scripts/verify.py --ai "Cursor" --dry-run

Works on Python 3.9+ (standard library only — no MCP dependency).
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent

# ---------------------------------------------------------------------------
# AI requirements mapping
# ---------------------------------------------------------------------------
# Each entry maps an AI name to its requirements.
# wrapper:      path to the AI's wrapper/instruction file (relative to project root)
# mcp_support:  whether the AI supports MCP (and should have .mcp.json configured)
# config_files: additional config files the AI needs (relative to project root)
# agents_md:    how the AI loads AGENTS.md — "native", "import", or "manual"

AI_REQUIREMENTS: dict = {
    "Claude Code": {
        "wrapper": "CLAUDE.md",
        "mcp_support": True,
        "config_files": [".claude/settings.json"],
        "agents_md": "native",
    },
    "Claude Cowork": {
        "wrapper": "CLAUDE.md",
        "mcp_support": True,
        "config_files": [".claude/settings.json"],
        "agents_md": "native",
    },
    "Gemini CLI": {
        "wrapper": "GEMINI.md",
        "mcp_support": True,
        "config_files": [".gemini/settings.json"],
        "agents_md": "import",
    },
    "GitHub Copilot": {
        "wrapper": ".github/copilot-instructions.md",
        "mcp_support": True,
        "config_files": [],
        "agents_md": "native",
    },
    "Cursor": {
        "wrapper": ".cursor/rules/project.mdc",
        "mcp_support": True,
        "config_files": [],
        "agents_md": "native",
    },
    "Windsurf": {
        "wrapper": ".windsurf/rules/project.md",
        "mcp_support": True,
        "config_files": [],
        "agents_md": "native",
    },
    "Devin Desktop": {
        "wrapper": ".devin/rules/project.md",
        "mcp_support": True,
        "config_files": [],
        "agents_md": "native",
    },
    "Cline": {
        "wrapper": ".clinerules/00-project.md",
        "mcp_support": True,
        "config_files": [],
        "agents_md": "manual",
    },
    "Continue": {
        "wrapper": ".continue/rules/00-project.md",
        "mcp_support": True,
        "config_files": [],
        "agents_md": "native",
    },
    "Aider": {
        "wrapper": ".aider.conf.yml",
        "mcp_support": True,
        "config_files": [],
        "agents_md": "config",
    },
    "Codex CLI": {
        "wrapper": None,
        "mcp_support": True,
        "config_files": [".codex/config.toml"],
        "agents_md": "native",
    },
    "Codex Desktop": {
        "wrapper": None,
        "mcp_support": True,
        "config_files": [".codex/config.toml"],
        "agents_md": "native",
    },
    "OpenCode": {
        "wrapper": None,
        "mcp_support": True,
        "config_files": ["opencode.json"],
        "agents_md": "native",
    },
}


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------

def check_agents_md() -> dict:
    """Check that AGENTS.md exists at the project root."""
    path = PROJECT_ROOT / "AGENTS.md"
    if path.exists():
        return {"check": "AGENTS.md exists", "status": "PASS", "detail": str(path.relative_to(PROJECT_ROOT))}
    return {"check": "AGENTS.md exists", "status": "FAIL", "detail": "AGENTS.md not found at project root."}


def check_python_version() -> dict:
    """Check that Python 3.9+ is available."""
    version = sys.version_info
    version_str = f"{version.major}.{version.minor}.{version.micro}"
    if version >= (3, 10):
        return {"check": "Python 3.9+", "status": "PASS", "detail": f"Python {version_str}"}
    if version >= (3, 9):
        return {
            "check": "Python 3.9+",
            "status": "WARN",
            "detail": f"Python {version_str} — meets minimum requirement but 3.10+ recommended "
                      "for MCP support. Consider updating to the latest stable release.",
        }
    return {"check": "Python 3.9+", "status": "FAIL", "detail": f"Python {version_str} — 3.9 or later required."}


def check_wrapper_file(ai_name: str, wrapper_path: str | None) -> dict:
    """Check that the AI's wrapper file exists."""
    if wrapper_path is None:
        return {"check": "Wrapper file", "status": "PASS", "detail": f"{ai_name} reads AGENTS.md natively — no wrapper file needed."}
    path = PROJECT_ROOT / wrapper_path
    if path.exists():
        return {"check": "Wrapper file", "status": "PASS", "detail": wrapper_path}
    return {"check": "Wrapper file", "status": "FAIL", "detail": f"{wrapper_path} not found."}


def check_config_files(config_files: list) -> list:
    """Check that AI-specific config files exist."""
    results = []
    for config_path in config_files:
        path = PROJECT_ROOT / config_path
        if path.exists():
            results.append({"check": f"Config: {config_path}", "status": "PASS", "detail": config_path})
        else:
            results.append({"check": f"Config: {config_path}", "status": "FAIL", "detail": f"{config_path} not found."})
    return results


def check_mcp_json() -> dict:
    """Check that .mcp.json exists and contains the biblio-tools server."""
    path = PROJECT_ROOT / ".mcp.json"
    if not path.exists():
        return {"check": "MCP config (.mcp.json)", "status": "FAIL", "detail": ".mcp.json not found at project root."}
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        servers = config.get("mcpServers", {})
        if "biblio-tools" in servers:
            return {"check": "MCP config (.mcp.json)", "status": "PASS", "detail": "biblio-tools server registered."}
        return {"check": "MCP config (.mcp.json)", "status": "WARN", "detail": ".mcp.json exists but biblio-tools server not registered."}
    except (json.JSONDecodeError, OSError) as exc:
        return {"check": "MCP config (.mcp.json)", "status": "FAIL", "detail": f"Could not parse .mcp.json: {exc}"}


def check_mcp_package() -> dict:
    """Check that the mcp Python package is installed (requires Python 3.10+)."""
    if sys.version_info < (3, 10):
        return {
            "check": "MCP package",
            "status": "WARN",
            "detail": f"Python {sys.version_info.major}.{sys.version_info.minor} — MCP SDK requires 3.10+. "
                      "MCP tools unavailable; scripts still work via direct shell commands.",
        }
    spec = importlib.util.find_spec("mcp")
    if spec is not None:
        return {"check": "MCP package", "status": "PASS", "detail": "mcp package installed."}
    return {
        "check": "MCP package",
        "status": "WARN",
        "detail": "mcp package not installed. Run: pip install -r workflows/biblio-tools/requirements.txt",
    }


def check_env_file() -> dict:
    """Check that .env exists (optional but extends functionality)."""
    path = PROJECT_ROOT / ".env"
    if path.exists():
        return {"check": ".env file", "status": "PASS", "detail": ".env found."}
    return {
        "check": ".env file",
        "status": "WARN",
        "detail": ".env not found — web research will run in free-sources-only mode. See workflows/web-research/SETUP.md.",
    }


def check_soul_md() -> dict:
    """Check that SOUL.md exists."""
    path = PROJECT_ROOT / "SOUL.md"
    if path.exists():
        return {"check": "SOUL.md exists", "status": "PASS", "detail": "SOUL.md found."}
    return {"check": "SOUL.md exists", "status": "FAIL", "detail": "SOUL.md not found at project root."}


def check_user_md() -> dict:
    """Check that USER.md exists (may be absent on fresh clone — that triggers first-run init)."""
    path = PROJECT_ROOT / "USER.md"
    if path.exists():
        return {"check": "USER.md exists", "status": "PASS", "detail": "USER.md found."}
    return {
        "check": "USER.md exists",
        "status": "WARN",
        "detail": "USER.md not found — first-run initialisation will create it.",
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_checks(ai_name: str, dry_run: bool = False) -> list:
    """Run all checks for the given AI and return a list of result dicts."""
    reqs = AI_REQUIREMENTS.get(ai_name)
    if reqs is None:
        return [{"check": "AI recognised", "status": "FAIL", "detail": f"Unknown AI: {ai_name!r}. Use --list to see supported AIs."}]

    if dry_run:
        checks = [
            "AGENTS.md exists",
            "Python 3.9+",
            f"Wrapper file ({reqs['wrapper'] or 'none needed'})",
            "SOUL.md exists",
            "USER.md exists",
            ".env file",
        ]
        for cf in reqs["config_files"]:
            checks.append(f"Config: {cf}")
        if reqs["mcp_support"]:
            checks.append("MCP config (.mcp.json)")
            checks.append("MCP package")
        return [{"check": c, "status": "DRY RUN", "detail": "Would check."} for c in checks]

    results = []
    results.append(check_agents_md())
    results.append(check_python_version())
    results.append(check_wrapper_file(ai_name, reqs["wrapper"]))
    results.append(check_soul_md())
    results.append(check_user_md())
    results.append(check_env_file())
    results.extend(check_config_files(reqs["config_files"]))

    if reqs["mcp_support"]:
        results.append(check_mcp_json())
        results.append(check_mcp_package())

    return results


def format_results(results: list) -> str:
    """Format results as human-readable text."""
    lines = []
    fail_count = sum(1 for r in results if r["status"] == "FAIL")
    warn_count = sum(1 for r in results if r["status"] == "WARN")
    pass_count = sum(1 for r in results if r["status"] == "PASS")

    for r in results:
        icon = {"PASS": "[PASS]", "FAIL": "[FAIL]", "WARN": "[WARN]", "DRY RUN": "[DRY RUN]"}.get(r["status"], "[????]")
        lines.append(f"  {icon} {r['check']} — {r['detail']}")

    summary = f"\n{pass_count} passed, {warn_count} warning(s), {fail_count} failure(s)."
    if fail_count == 0 and warn_count == 0:
        summary += " Environment fully configured."
    elif fail_count == 0:
        summary += " No failures — warnings are non-blocking."
    else:
        summary += " Fix failures before proceeding. See AGENT-SETUP.md for remediation."

    lines.append(summary)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Book Dragon setup verification",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--ai",
        metavar="NAME",
        help='AI to verify setup for (e.g. "Claude Code", "Gemini CLI")',
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_ais",
        help="List all supported AIs and exit",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON (for MCP tool consumption)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be checked without running checks",
    )
    args = parser.parse_args()

    if args.list_ais:
        print("Supported AIs:")
        for name, reqs in AI_REQUIREMENTS.items():
            wrapper = reqs["wrapper"] or "(reads AGENTS.md natively)"
            mcp = "MCP" if reqs["mcp_support"] else "no MCP"
            print(f"  {name:<20} wrapper: {wrapper:<40} {mcp}")
        return

    if not args.ai:
        parser.error("--ai NAME is required (or use --list to see supported AIs)")

    results = run_checks(args.ai, dry_run=args.dry_run)

    if args.json_output:
        output = {
            "ai": args.ai,
            "checks": results,
            "summary": {
                "pass": sum(1 for r in results if r["status"] == "PASS"),
                "warn": sum(1 for r in results if r["status"] == "WARN"),
                "fail": sum(1 for r in results if r["status"] == "FAIL"),
            },
        }
        print(json.dumps(output, indent=2))
    else:
        print(f"Book Dragon setup check for: {args.ai}\n")
        print(format_results(results))

    # Exit with non-zero if any failures
    if any(r["status"] == "FAIL" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
