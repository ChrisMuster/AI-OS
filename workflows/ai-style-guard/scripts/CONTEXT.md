# Scripts

**Last modified:** 2026-06-25

## Purpose
Holds the AI-style guard's entry point and its pure, unit-tested helpers. The script collects added or changed lines from `git diff` (plus new untracked files), then runs the tier-1 and tier-2 detectors against each added line, reporting file:line locations. It is read-only by design.

## Contents
- run.py - `workflows/ai-style-guard/scripts/run.py` [[workflows/ai-style-guard/scripts/CONTEXT]] - The entry point and all logic: the `git diff --unified=0` hunk parser (`parse_diff`), the change collector (`collect_changes`), the config loader (`load_config`), the per-line detectors (`scan_line`), and the CLI (`--check`, `--json`, `--since`, `--base`, `--strict`).

## Inputs
- `git diff` output and new untracked files under the project root.
- `workflows/ai-style-guard/config/ai-tells.yaml` [[workflows/ai-style-guard/config/CONTEXT]] for the tells definition.

## Outputs
- A human report or `--json` payload on stdout. No files written.

## Steps
1. Resolve the diff base (default HEAD; `--since REF` or `--base BRANCH` to override; the audit uses `--base main`).
2. Collect added lines from the diff and from new untracked text files, skipping the guard's own directory and non-text extensions.
3. Run the detectors against each added line and print the report (or JSON).
4. Exit 1 under `--strict` if any WARN exists, else 0. (Read-only: no LOG.md entry on a check run.)

## Dependencies
- `workflows/ai-style-guard/config/ai-tells.yaml` [[workflows/ai-style-guard/config/CONTEXT]] - The tells definition, read at runtime.
- PyYAML (project dependency) and the `git` CLI; otherwise the Python 3.9+ standard library.

## Known Issues
- The hunk parser assumes `--unified=0` output (no context lines). It stays robust to stray context lines but is exercised and tuned for the zero-context form the script itself requests.
- File discovery depends on git. Outside a git checkout the guard scans nothing and emits a single INFO note, by design.

## Revision History
- 2026-06-25 - Initial creation. `run.py` with the diff hunk parser, change collector, config loader, tier-1/tier-2 detectors, and the CLI flag set.
