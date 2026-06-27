# Check For Updates

**Last modified:** 2026-06-27

## Purpose
Standalone workflow that reports whether the project's Python packages and installed AI CLI tools have newer versions available. It is read-only: it gathers installed-versus-latest versions, classifies the gap, and reports with recommendations. It never updates anything itself; the user reviews the report and decides, and Biblio then updates only what the user names. Phase 1 is the version checker (the default behaviour). Phase 2 adds an opt-in advisory "landscape" mode (`--landscape`) that watches for product-status changes in the supported AI tools (renames, deprecations, replacements) by running focused web-research queries and scanning the results for signal keywords, surfaced as a separate, advisory report section. Landscape findings are candidate flags to verify, never conclusions.

## Contents
- scripts/ - `workflows/check-for-updates/scripts/` [[workflows/check-for-updates/scripts/CONTEXT]] - The `run.py` entry point plus the checker, registry-resolver, version-classification, landscape-watch, and report-formatting modules.
- config/ - `workflows/check-for-updates/config/` [[workflows/check-for-updates/config/CONTEXT]] - Holds `sources.yaml`, the single source of truth for what is checked, where "latest" comes from, and the landscape watch list.
- tests/ - `workflows/check-for-updates/tests/` [[workflows/check-for-updates/tests/CONTEXT]] - The unittest suite and its `run_tests.py` runner.

## Inputs
- `config/sources.yaml` - what to check (the Python-deps toggle, the AI CLI tool list, the staleness threshold, and the `landscape` block: enabled toggle, signal keywords, and watch list). Read at runtime.
- The canonical `.venv` - the entry point hands off to it for pip and PyYAML.
- Network access - PyPI (via pip), the npm registry, and the GitHub releases API for the version check; the web-research sources for landscape mode. An unreachable source degrades to a partial report, never a crash.
- AI CLI tools must be on PATH to be version-checked; an absent tool is reported as "not installed".
- `--landscape` only: the `skills/web-research/` [[skills/web-research/CONTEXT]] skill and its sources (some keyed via `.env`).

## Outputs
- A grouped report on stdout (or `--json`), each row showing name / installed / latest / change (up to date, patch, minor, major, unknown, not installed). With `--suggest-commands` it also prints the upgrade command per outdated item (it does not run them).
- With `--landscape`, an additional advisory "AI TOOL LANDSCAPE" section listing any watched product whose research surfaced status-change signals, with the source titles and URLs behind each; a zero-coverage run is reported as inconclusive rather than clean. The `--json` output gains a parallel `landscape` block.
- `.last-run` - a gitignored timestamp file recording when the workflow last ran; the session-startup staleness reminder in `AGENTS.md` [[AGENTS]] reads it.
- `LOG.md` started/completed entries (skipped on `--dry-run`).

## Steps
1. Hand off to the canonical `.venv` so pip and PyYAML are available.
2. Load `config/sources.yaml`.
3. Check Python packages via `pip list --outdated` against PyPI.
4. For each configured AI CLI tool found on PATH, read its installed version and resolve the latest from its registry (npm or GitHub).
5. Classify each result by semver gap; a pre-release latest never marks a stable install as behind.
6. If `--landscape` was passed, run the landscape watch: for each configured product, run a focused web-research query and scan the returned source content for the configured signal keywords (plus the product's aliases). Web-research failure degrades to a noted status, never a crash.
7. Print the grouped report (or `--json`); with `--suggest-commands`, append the upgrade command per outdated item; with `--landscape`, append the advisory landscape section.
8. Record the run by writing the `.last-run` timestamp, unless `--dry-run` was passed.
9. Append LOG.md with a completion or failure entry.

## Dependencies
- `AGENTS.md` [[AGENTS]] (root) - Defines the workflow structure rules, and hosts the session-startup staleness reminder that reads this workflow's `.last-run`.
- `workflows/biblio-tools/` [[workflows/biblio-tools/CONTEXT]] - `runtime.py` provides the `.venv` hand-off (`ensure_project_runtime`, `venv_python`).
- PyYAML (project dependency) - reads `config/sources.yaml`.
- External services: pip/PyPI, the npm registry, and the GitHub releases API. If a source is unreachable the run degrades to a partial report with the failure noted in the SOURCE STATUS line.
- `skills/web-research/` [[skills/web-research/CONTEXT]] - imported by landscape mode (`--landscape` only) to gather sources for each watched product. An absent or failing skill degrades to a noted status, not a crash.

## Known Issues
- Network is required for live results; an unreachable source is reported in the SOURCE STATUS line rather than raised.
- npm package ids in `config/sources.yaml` can drift (for example the Gemini to Antigravity transition); confirm against the live registry if a tool reports unexpectedly.
- An AI CLI tool is only checked if its command is on PATH; otherwise it shows "not installed", which is also the correct result on a machine that does not use that tool.
- GitHub's unauthenticated releases API is rate-limited (about 60 requests per hour); fine for a handful of tools.
- Landscape mode is only as good as web-research coverage. If sources fail (for example rate-limit errors), the section reports the run as inconclusive rather than clean. To cut noise, a source is only considered if it mentions the product (name variant or alias) and the match is made on word boundaries, so an unrelated story that merely uses a keyword is filtered out. Even so, an on-topic source can mention a keyword benignly (for example a changelog noting an unrelated deprecation), and an unusually phrased change can be missed - the section stays advisory and lists its sources for verification.

## Revision History
- 2026-06-26 - Initial creation (Phase 1: version checker). pip/PyPI Python-deps check, config-driven AI CLI tool checks (npm and GitHub resolvers), stdlib semver classification, text and `--json` report, `--suggest-commands`, `--dry-run`, the `.last-run` timestamp, and a 21-test unittest suite.
- 2026-06-27 - Added Phase 2 landscape mode (`--landscape`): new `landscape.py` runs focused web-research queries per watched product and scans results for status-change signal keywords; new `landscape` config block (enabled toggle, signal keywords, 9-product watch list); advisory report section with per-source detail and a zero-coverage inconclusive note; `--json` gains a `landscape` block. Renamed `sources.py` to `registries.py` to avoid a module-name clash with the web-research `sources` package. Suite grown to 36 tests.
- 2026-06-27 - Tightened landscape precision: a source now only counts if it mentions the product (name variant or alias), and all matching is on alphanumeric word boundaries (so "Devin" no longer matches "Devine", etc.). Removes the off-topic keyword false positives seen on the first live run. Suite grown to 40 tests.
