# Check For Updates - Scripts

**Last modified:** 2026-06-27

## Purpose
The scripts that implement the check-for-updates workflow. Kept lean and split by concern: orchestration, checking, registry resolution, version classification, landscape watch, and report formatting.

## Contents
- run.py - `workflows/check-for-updates/scripts/run.py` [[workflows/check-for-updates/scripts/CONTEXT]] - CLI entry point; hands off to the `.venv`, loads the config, runs the checks (and the landscape watch with `--landscape`), prints the report, and records the run.
- checkers.py - `workflows/check-for-updates/scripts/checkers.py` [[workflows/check-for-updates/scripts/CONTEXT]] - `check_python_deps` (wraps `pip list --outdated`) and the generic config-driven `check_cli_tool`.
- registries.py - `workflows/check-for-updates/scripts/registries.py` [[workflows/check-for-updates/scripts/CONTEXT]] - Latest-version resolvers (npm registry, GitHub releases) with graceful failure returned as a status string.
- versions.py - `workflows/check-for-updates/scripts/versions.py` [[workflows/check-for-updates/scripts/CONTEXT]] - Minimal stdlib semver parse and change classification.
- landscape.py - `workflows/check-for-updates/scripts/landscape.py` [[workflows/check-for-updates/scripts/CONTEXT]] - Phase 2 advisory landscape watch: imports the web-research skill, researches each watched product, and scans the results for status-change signal keywords. Degrades to a status string on failure.
- report.py - `workflows/check-for-updates/scripts/report.py` [[workflows/check-for-updates/scripts/CONTEXT]] - Text and `--json` report formatting, the update count, and the advisory landscape section.

## Inputs
- `workflows/check-for-updates/config/sources.yaml` [[workflows/check-for-updates/config/CONTEXT]] - the tools and settings to check.
- The canonical `.venv` (pip, PyYAML); network access for the registry lookups.

## Outputs
- The report on stdout (or `--json`), the `workflows/check-for-updates/.last-run` timestamp, and `workflows/check-for-updates/LOG.md` entries.

## Steps
N/A. This directory holds the workflow's scripts; the run sequence is documented in the parent workflow CONTEXT.md `workflows/check-for-updates/CONTEXT.md` [[workflows/check-for-updates/CONTEXT]].

## Dependencies
- `workflows/biblio-tools/scripts/runtime.py` [[workflows/biblio-tools/scripts/CONTEXT]] - imported for the `.venv` hand-off.
- `skills/web-research/scripts/research.py` [[skills/web-research/scripts/CONTEXT]] [[skills/web-research/CONTEXT]] - imported lazily by `landscape.py` for `--landscape` only.
- PyYAML - config parsing; the standard library for everything else.
- External: pip/PyPI, the npm registry, and the GitHub releases API; the web-research sources for landscape mode.

## Known Issues
- The npm and GitHub resolvers depend on network reachability; failures are caught and surfaced as a source status, not raised.
- `_installed_version` parses a tool's `--version` output with a configured regex; an unusual version-string format would need a config tweak.
- `landscape.py` imports the web-research skill, whose `sources` package would clash with a `sources` module here; the registry module is named `registries.py` for that reason. Keep new module names clear of the skill's top-level names (`research`, `compile`, `exceptions`, `sources`).

## Revision History
- 2026-06-26 - Initial creation. run.py orchestration, checkers.py, sources.py, versions.py, report.py.
- 2026-06-27 - Added landscape.py (Phase 2 advisory watch). Renamed sources.py to registries.py to avoid a clash with the web-research skill's `sources` package. report.py gained the landscape section; run.py gained `--landscape`.
