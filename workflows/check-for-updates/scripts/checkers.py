"""Checkers that gather installed-versus-latest versions for each update source.

Two shapes only: pip wraps the Python environment, and a single generic
CLI-tool checker is driven entirely by config/sources.yaml so adding a tool is a
config entry rather than new code.
"""
import json
import re
import shutil
import subprocess

import registries
import versions


def check_python_deps(python_exe):
    """Run 'pip list --outdated' against *python_exe*. Returns (results, status).

    pip only lists outdated packages, so an empty result means everything is
    current. status is "ok" or a short error string.
    """
    try:
        proc = subprocess.run(
            [str(python_exe), "-m", "pip", "list", "--outdated", "--format=json"],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return [], f"pip call failed ({exc})"
    if proc.returncode != 0:
        return [], f"pip exited {proc.returncode}"
    try:
        outdated = json.loads(proc.stdout or "[]")
    except ValueError as exc:
        return [], f"unparseable pip output ({exc})"

    results = []
    for pkg in outdated:
        installed = pkg.get("version", "")
        latest = pkg.get("latest_version", "")
        results.append({
            "category": "Python packages",
            "name": pkg.get("name", "?"),
            "installed": installed,
            "latest": latest,
            "change": versions.classify(installed, latest),
            "note": "",
        })
    return results, "ok"


def _installed_version(command, flag, regex):
    """Return the installed version string of *command*, or None if absent/unreadable."""
    if not command or shutil.which(command) is None:
        return None
    try:
        proc = subprocess.run(
            [command, flag], capture_output=True, text=True,
            encoding="utf-8", timeout=20,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if not output:
        return None
    if regex:
        match = re.search(regex, output)
        if match:
            return match.group(1)
    return output.splitlines()[0].strip()


def check_cli_tool(spec):
    """Check one CLI tool spec from config.

    Returns (result, source, error). source/error describe the registry lookup
    and feed the report's source-status line; both are None when the tool is not
    installed (nothing was looked up).
    """
    name = spec.get("name", "?")
    command = spec.get("command")
    installed = _installed_version(
        command, spec.get("version_flag", "--version"), spec.get("version_regex"))
    if installed is None:
        return ({
            "category": "AI CLI tools", "name": name,
            "installed": None, "latest": None, "change": "not installed", "note": "",
        }, None, None)

    latest_cfg = spec.get("latest", {}) or {}
    source = latest_cfg.get("source")
    identifier = latest_cfg.get("id")
    latest, error = registries.resolve_latest(source, identifier)
    result = {
        "category": "AI CLI tools", "name": name,
        "installed": installed, "latest": latest,
        "change": versions.classify(installed, latest) if latest else "unknown",
        "note": "" if latest else "latest unavailable",
    }
    if source == "npm" and identifier:
        result["upgrade"] = f"npm install -g {identifier}@latest"
    return (result, source, error)
