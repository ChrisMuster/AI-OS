#!/usr/bin/env python3
"""
CLI entry point for the check-for-updates workflow (Phase 1: version checker).

Reports whether the project's Python packages and installed AI CLI tools have
newer versions available. It is read-only: it never updates anything. You review
the report and decide; Biblio then updates only what you name.

Usage:
    python workflows/check-for-updates/scripts/run.py
    python workflows/check-for-updates/scripts/run.py --json
    python workflows/check-for-updates/scripts/run.py --suggest-commands
    python workflows/check-for-updates/scripts/run.py --dry-run
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).parent
_WORKFLOW_DIR = _SCRIPTS_DIR.parent                       # workflows/check-for-updates/
_PROJECT_ROOT = _WORKFLOW_DIR.parent.parent               # AI-OS/
_RUNTIME_SCRIPTS = _PROJECT_ROOT / "workflows" / "biblio-tools" / "scripts"

sys.path.insert(0, str(_RUNTIME_SCRIPTS))
sys.path.insert(0, str(_SCRIPTS_DIR))

_CONFIG_PATH = _WORKFLOW_DIR / "config" / "sources.yaml"
_LAST_RUN_PATH = _WORKFLOW_DIR / ".last-run"
_LOG_PATH = _WORKFLOW_DIR / "LOG.md"


def _timestamp():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _append_log(action, note):
    line = f"[{_timestamp()}] | Actor: Biblio | Action: {action} | Note: {note}\n"
    with open(_LOG_PATH, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)


def main():
    parser = argparse.ArgumentParser(
        description="Check whether project Python packages and AI CLI tools have newer versions available.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    parser.add_argument(
        "--suggest-commands", action="store_true",
        help="Print the upgrade command for each outdated item (does not run them).")
    parser.add_argument(
        "--landscape", action="store_true",
        help="Also run the advisory AI-tool landscape watch via web research (slower).")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Produce the report without recording the run (no last-run timestamp, no log).")
    args = parser.parse_args()

    # Hand off to the canonical .venv so PyYAML and the project pip are available.
    from runtime import ensure_project_runtime, venv_python  # noqa: E402
    ensure_project_runtime()

    import yaml  # noqa: E402
    import checkers  # noqa: E402
    import report as report_mod  # noqa: E402

    if not _CONFIG_PATH.is_file():
        print(f"Config not found: {_CONFIG_PATH}", file=sys.stderr)
        return 1
    with open(_CONFIG_PATH, encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}

    if not args.dry_run:
        _append_log("started", "Began update check.")

    results = []
    source_status = {}

    if config.get("python_deps", {}).get("enabled", True):
        py_results, py_status = checkers.check_python_deps(venv_python())
        results.extend(py_results)
        source_status["PyPI"] = py_status

    for spec in config.get("cli_tools", []) or []:
        result, source, error = checkers.check_cli_tool(spec)
        results.append(result)
        if source:
            label = {"npm": "npm", "github": "GitHub"}.get(source, source)
            if error:
                source_status[label] = error
            else:
                source_status.setdefault(label, "ok")

    landscape_findings, landscape_status = None, None
    if args.landscape:
        import landscape as landscape_mod  # noqa: E402
        landscape_findings, landscape_status = landscape_mod.check_landscape(config)
        source_status["Web research"] = landscape_status

    timestamp = _timestamp()
    if args.json:
        payload = {"generated": timestamp, "results": results, "source_status": source_status}
        if args.landscape:
            payload["landscape"] = {"status": landscape_status, "findings": landscape_findings}
        print(json.dumps(payload, indent=2))
    else:
        report_text = report_mod.build_report(
            results, source_status, timestamp, suggest_commands=args.suggest_commands)
        if args.landscape:
            report_text += "\n\n" + report_mod.build_landscape_report(
                landscape_findings, landscape_status)
        print(report_text)

    if args.dry_run:
        print("\n[DRY RUN] Run not recorded (no last-run timestamp written, no log entry).")
    else:
        with open(_LAST_RUN_PATH, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(timestamp + "\n")
        _append_log("completed",
                    f"Update check complete. {report_mod.count_updates(results)} update(s) available.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
