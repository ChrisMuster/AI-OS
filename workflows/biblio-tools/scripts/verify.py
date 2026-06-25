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
import os
import shutil
import subprocess
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
        "mcp_config": ("mcp-json", ".mcp.json"),
    },
    "Claude Cowork": {
        "wrapper": "CLAUDE.md",
        "mcp_support": True,
        "config_files": [".claude/settings.json"],
        "agents_md": "native",
        "mcp_config": ("mcp-json", ".mcp.json"),
    },
    "Gemini CLI": {
        "wrapper": "GEMINI.md",
        "mcp_support": True,
        "config_files": [".gemini/settings.json"],
        "agents_md": "import",
        "mcp_config": ("gemini-json", ".gemini/settings.json"),
    },
    "Antigravity CLI": {
        "wrapper": "GEMINI.md",
        "mcp_support": False,
        "config_files": [],
        "agents_md": "import",
        "readiness_blocker": (
            "Antigravity MCP migration is not implemented or live-verified. "
            "The expected workspace config is .agents/mcp_config.json; see "
            "the transition notice in AGENT-SETUP.md."
        ),
    },
    "GitHub Copilot": {
        "wrapper": ".github/copilot-instructions.md",
        "mcp_support": True,
        "config_files": [],
        "agents_md": "native",
        "mcp_config": ("mcp-json", ".mcp.json"),
    },
    "Cursor": {
        "wrapper": ".cursor/rules/project.mdc",
        "mcp_support": True,
        "config_files": [],
        "agents_md": "native",
        "mcp_config": ("mcp-json", ".mcp.json"),
    },
    "Windsurf": {
        "wrapper": ".windsurf/rules/project.md",
        "mcp_support": True,
        "config_files": [],
        "agents_md": "native",
        "mcp_config": ("mcp-json", ".mcp.json"),
    },
    "Devin Desktop": {
        "wrapper": ".devin/rules/project.md",
        "mcp_support": True,
        "config_files": [],
        "agents_md": "native",
        "mcp_config": ("mcp-json", ".mcp.json"),
    },
    "Cline": {
        "wrapper": ".clinerules/00-project.md",
        "mcp_support": True,
        "config_files": [],
        "agents_md": "manual",
        "mcp_config": ("mcp-json", ".mcp.json"),
    },
    "Continue": {
        "wrapper": ".continue/rules/00-project.md",
        "mcp_support": True,
        "config_files": [],
        "agents_md": "native",
        "mcp_config": ("mcp-json", ".mcp.json"),
    },
    "Aider": {
        "wrapper": ".aider.conf.yml",
        "mcp_support": True,
        "config_files": [],
        "agents_md": "config",
        "mcp_config": ("aider-yaml", ".aider.conf.yml"),
    },
    "Codex CLI": {
        "wrapper": None,
        "mcp_support": True,
        "config_files": [".codex/config.toml"],
        "agents_md": "native",
        "mcp_config": ("codex-toml", ".codex/config.toml"),
    },
    "Codex Desktop": {
        "wrapper": None,
        "mcp_support": True,
        "config_files": [".codex/config.toml"],
        "agents_md": "native",
        "mcp_config": ("codex-toml", ".codex/config.toml"),
    },
    "OpenCode": {
        "wrapper": None,
        "mcp_support": True,
        "config_files": ["opencode.json"],
        "agents_md": "native",
        "mcp_config": ("opencode-json", "opencode.json"),
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


def _load_json(path: Path) -> dict:
    """Load a JSON config file."""
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_aider_command(path: Path) -> tuple[str, list[str]]:
    """Extract the biblio-tools command from the checked-in Aider config."""
    lines = path.read_text(encoding="utf-8").splitlines()
    in_server = False
    in_args = False
    command = None
    args = []

    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped == "- name: biblio-tools":
            in_server = True
            in_args = False
            continue
        if in_server and stripped.startswith("- name:"):
            break
        if not in_server:
            continue
        if stripped.startswith("command:"):
            command = stripped.split(":", 1)[1].strip()
            in_args = False
        elif stripped == "args:":
            in_args = True
        elif in_args and stripped.startswith("- "):
            args.append(stripped[2:].strip())

    if not command:
        raise ValueError("biblio-tools command not found")
    return command, args


def load_mcp_command(config_kind: str, config_path: str) -> tuple[str, list[str], Path]:
    """Read the selected AI's native config and return its stdio command."""
    path = PROJECT_ROOT / config_path
    if not path.exists():
        raise FileNotFoundError(f"{config_path} not found")
    cwd = PROJECT_ROOT

    if config_kind in {"mcp-json", "gemini-json"}:
        server = _load_json(path).get("mcpServers", {}).get("biblio-tools")
        if not isinstance(server, dict):
            raise ValueError("biblio-tools is not registered under mcpServers")
        command = server.get("command")
        args = server.get("args", [])
    elif config_kind == "opencode-json":
        server = _load_json(path).get("mcp", {}).get("biblio-tools")
        if not isinstance(server, dict):
            raise ValueError("biblio-tools is not registered under mcp")
        command_parts = server.get("command")
        if not isinstance(command_parts, list) or not command_parts:
            raise ValueError("OpenCode local command must be a non-empty array")
        command, *args = command_parts
    elif config_kind == "codex-toml":
        try:
            import tomllib
        except ImportError as exc:
            raise RuntimeError(
                "Python 3.11+ is required to parse Codex TOML config"
            ) from exc
        config = tomllib.loads(path.read_text(encoding="utf-8"))
        server = config.get("mcp_servers", {}).get("biblio-tools")
        if not isinstance(server, dict):
            raise ValueError("biblio-tools is not registered under mcp_servers")
        command = server.get("command")
        args = server.get("args", [])
        configured_cwd = server.get("cwd")
        if configured_cwd is not None:
            if not isinstance(configured_cwd, str) or not configured_cwd:
                raise ValueError("MCP cwd must be a non-empty string")
            cwd_path = Path(configured_cwd)
            cwd = cwd_path if cwd_path.is_absolute() else (path.parent / cwd_path).resolve()
    elif config_kind == "aider-yaml":
        command, args = _parse_aider_command(path)
    else:
        raise ValueError(f"Unsupported MCP config kind: {config_kind}")

    if not isinstance(command, str) or not command:
        raise ValueError("MCP command must be a non-empty string")
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise ValueError("MCP args must be a list of strings")
    return command, args, cwd


def check_mcp_config(
    config_kind: str, config_path: str
) -> tuple[dict, tuple[str, list[str], Path] | None]:
    """Validate the selected AI's native MCP configuration."""
    try:
        command = load_mcp_command(config_kind, config_path)
    except (json.JSONDecodeError, OSError, RuntimeError, ValueError) as exc:
        return (
            {
                "check": f"MCP config ({config_path})",
                "status": "FAIL",
                "detail": str(exc),
            },
            None,
        )

    rendered = " ".join([command[0], *command[1]])
    return (
        {
            "check": f"MCP config ({config_path})",
            "status": "PASS",
            "detail": f"biblio-tools registered: {rendered}; cwd={command[2]}",
        },
        command,
    )


def _codex_cli_env() -> tuple[dict[str, str], str]:
    """Return an environment that points Codex CLI at the likely user config."""
    env = os.environ.copy()
    configured = env.get("CODEX_HOME")
    if configured:
        return env, configured

    candidates: list[Path] = []
    if os.name == "nt":
        userprofile = env.get("USERPROFILE")
        if userprofile:
            candidates.append(Path(userprofile) / ".codex")

    home = env.get("HOME")
    if home:
        candidates.append(Path(home) / ".codex")

    try:
        candidates.append(Path.home() / ".codex")
    except RuntimeError:
        pass

    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if (candidate / "config.toml").exists():
            env["CODEX_HOME"] = str(candidate)
            return env, str(candidate)

    fallback = str(candidates[0]) if candidates else ""
    if fallback:
        env["CODEX_HOME"] = fallback
    return env, fallback or "(not set)"


def check_codex_mcp_registry() -> dict:
    """Ask the Codex CLI registry whether the plugin-backed Biblio server is enabled."""
    if shutil.which("codex") is None:
        return {
            "check": "Codex MCP registry",
            "status": "WARN",
            "detail": "codex executable not found on PATH; native registry check skipped.",
        }

    env, codex_home = _codex_cli_env()
    try:
        result = subprocess.run(
            ["codex", "mcp", "get", "biblio_tools"],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "check": "Codex MCP registry",
            "status": "FAIL",
            "detail": f"codex mcp get biblio_tools failed from CODEX_HOME={codex_home}: {exc}",
        }

    output = "\n".join(part.strip() for part in [result.stdout, result.stderr] if part.strip())
    if result.returncode != 0:
        detail = output or f"codex exited with {result.returncode}"
        return {
            "check": "Codex MCP registry",
            "status": "FAIL",
            "detail": f"CODEX_HOME={codex_home}; {detail}",
        }

    if "biblio_tools" not in result.stdout or "enabled: true" not in result.stdout:
        return {
            "check": "Codex MCP registry",
            "status": "FAIL",
            "detail": f"CODEX_HOME={codex_home}; biblio_tools was not reported as enabled.",
        }

    return {
        "check": "Codex MCP registry",
        "status": "PASS",
        "detail": f"Codex CLI reports plugin-backed biblio_tools enabled from CODEX_HOME={codex_home}.",
    }


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

    local_pythons = [
        PROJECT_ROOT / ".venv" / "Scripts" / "python.exe",
        PROJECT_ROOT / ".venv" / "bin" / "python",
    ]
    for python in local_pythons:
        if not python.exists():
            continue
        try:
            result = subprocess.run(
                [str(python), "-c", "import mcp"],
                capture_output=True,
                timeout=45,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return {
                "check": "MCP package",
                "status": "PASS",
                "detail": "mcp package installed in project .venv.",
            }

    return {
        "check": "MCP package",
        "status": "WARN",
        "detail": (
            "mcp package not installed. Run: "
            "python workflows/biblio-tools/scripts/setup.py"
        ),
    }


def check_pdf_extraction() -> dict:
    """Check the shared Create Wiki PDF extraction capability."""
    script = PROJECT_ROOT / "workflows" / "create-wiki" / "scripts" / "extract_pdf.py"
    if not script.is_file():
        return {
            "check": "PDF extraction",
            "status": "FAIL",
            "detail": "workflows/create-wiki/scripts/extract_pdf.py not found.",
        }

    local_pythons = [
        PROJECT_ROOT / ".venv" / "Scripts" / "python.exe",
        PROJECT_ROOT / ".venv" / "bin" / "python",
    ]
    for python in local_pythons:
        if not python.exists():
            continue
        try:
            result = subprocess.run(
                [str(python), "-c", "import pypdf; print(pypdf.__version__)"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            version = result.stdout.strip() or "installed"
            return {
                "check": "PDF extraction",
                "status": "PASS",
                "detail": (
                    f"pypdf {version} installed in project .venv; "
                    "shared extractor available."
                ),
            }

    return {
        "check": "PDF extraction",
        "status": "FAIL",
        "detail": (
            "pypdf is not installed in the project .venv. Run: "
            "python workflows/biblio-tools/scripts/setup.py"
        ),
    }


def check_project_runtime() -> dict:
    """Check the canonical .venv and all packages in root requirements.txt."""
    setup_script = SCRIPT_DIR / "setup.py"
    try:
        result = subprocess.run(
            [sys.executable, str(setup_script), "--check"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "check": "Project Python runtime",
            "status": "FAIL",
            "detail": f"Could not check .venv: {exc}",
        }

    if result.returncode == 0:
        return {
            "check": "Project Python runtime",
            "status": "PASS",
            "detail": "Canonical .venv and all declared packages are available.",
        }

    detail = result.stdout.strip() or result.stderr.strip()
    return {
        "check": "Project Python runtime",
        "status": "FAIL",
        "detail": (
            f"{detail} Run: python workflows/biblio-tools/scripts/setup.py"
        ),
    }


def _mcp_python() -> Path | None:
    """Return an interpreter capable of importing the MCP SDK."""
    candidates = [
        PROJECT_ROOT / ".venv" / "Scripts" / "python.exe",
        PROJECT_ROOT / ".venv" / "bin" / "python",
    ]
    for python in candidates:
        if python.exists():
            return python
    if importlib.util.find_spec("mcp") is not None:
        return Path(sys.executable)
    return None


def check_mcp_handshake(command: str, args: list[str], cwd: Path) -> dict:
    """Prove the configured server starts, speaks MCP, and exposes all tools."""
    python = _mcp_python()
    if python is None:
        return {
            "check": "MCP handshake and tools",
            "status": "WARN",
            "detail": "MCP SDK unavailable, so the protocol smoke test was skipped.",
        }

    smoke_cmd = [
        str(python),
        str(SCRIPT_DIR / "mcp_smoke.py"),
        "--command",
        command,
        "--cwd",
        str(cwd),
    ]
    for arg in args:
        smoke_cmd.extend(["--arg", arg])

    try:
        result = subprocess.run(
            smoke_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=90,
            cwd=PROJECT_ROOT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "check": "MCP handshake and tools",
            "status": "FAIL",
            "detail": f"Could not run smoke test: {exc}",
        }

    output_lines = [line for line in result.stdout.splitlines() if line.strip()]
    try:
        payload = json.loads(output_lines[-1]) if output_lines else {}
    except json.JSONDecodeError:
        payload = {}

    if result.returncode == 0 and payload.get("success"):
        return {
            "check": "MCP handshake and tools",
            "status": "PASS",
            "detail": (
                f"Protocol {payload.get('protocol_version')}; "
                f"{len(payload.get('tools', []))} tools discovered; "
                "read-only call and rejection checks passed."
            ),
        }

    error = payload.get("error") or result.stderr.strip() or "Unknown MCP failure."
    return {
        "check": "MCP handshake and tools",
        "status": "FAIL",
        "detail": error,
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
            "Project Python runtime",
            "PDF extraction",
        ]
        for cf in reqs["config_files"]:
            checks.append(f"Config: {cf}")
        if reqs["mcp_support"]:
            checks.append(f"MCP config ({reqs['mcp_config'][1]})")
            checks.append("MCP package")
            if ai_name in {"Codex CLI", "Codex Desktop"}:
                checks.append("Codex MCP registry")
            checks.append("MCP handshake and tools")
        return [{"check": c, "status": "DRY RUN", "detail": "Would check."} for c in checks]

    results = []
    results.append(check_agents_md())
    results.append(check_python_version())
    results.append(check_wrapper_file(ai_name, reqs["wrapper"]))
    results.append(check_soul_md())
    results.append(check_user_md())
    results.append(check_env_file())
    results.append(check_project_runtime())
    results.append(check_pdf_extraction())
    results.extend(check_config_files(reqs["config_files"]))

    if reqs.get("readiness_blocker"):
        results.append({
            "check": "MCP integration",
            "status": "WARN",
            "detail": reqs["readiness_blocker"],
        })
    elif reqs["mcp_support"]:
        results.append(check_mcp_package())
        config_result, command = check_mcp_config(*reqs["mcp_config"])
        results.append(config_result)
        if ai_name in {"Codex CLI", "Codex Desktop"}:
            results.append(check_codex_mcp_registry())
        if command is not None:
            results.append(check_mcp_handshake(*command))

    return results


def format_results(results: list) -> str:
    """Format results as human-readable text."""
    lines = []
    fail_count = sum(1 for r in results if r["status"] == "FAIL")
    warn_count = sum(1 for r in results if r["status"] == "WARN")
    pass_count = sum(1 for r in results if r["status"] == "PASS")
    dry_run_count = sum(1 for r in results if r["status"] == "DRY RUN")

    for r in results:
        icon = {"PASS": "[PASS]", "FAIL": "[FAIL]", "WARN": "[WARN]", "DRY RUN": "[DRY RUN]"}.get(r["status"], "[????]")
        lines.append(f"  {icon} {r['check']} — {r['detail']}")

    if dry_run_count:
        lines.append(f"\n{dry_run_count} check(s) would run.")
        return "\n".join(lines)

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
        print("Known AI profiles:")
        for name, reqs in AI_REQUIREMENTS.items():
            wrapper = reqs["wrapper"] or "(reads AGENTS.md natively)"
            if reqs.get("readiness_blocker"):
                status = "MCP pending"
            else:
                status = "MCP" if reqs["mcp_support"] else "no MCP"
            print(f"  {name:<20} wrapper: {wrapper:<40} {status}")
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
