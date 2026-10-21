# Review Orchestration - Config

**Last modified:** 2026-10-20

## Purpose
Holds the review-orchestration settings, the files the user edits to change how runs behave. Settings live here rather than in the scripts so that nothing a run depends on is hard-coded.

## Contents
- settings.json - `workflows/review-orchestration/config/settings.json` - The run settings: `usage_ceiling_percent` (default 80), `max_turns` (Claude builder 80, reviewer 200), `usage_probe_model` (default `claude-haiku-5-5`), `round_caps` (3 build-review rounds, 2 plan-review rounds), and `models`, the model and effort each provider uses in each role. Which provider builds is named for each run with `run.py --run --builder claude|codex`; a file that still holds `roles` is refused.
- verify-commands.txt - `workflows/review-orchestration/config/verify-commands.txt` - The commands an orchestrated Claude builder may run to verify its own work: one regular expression per line, the whole command must match one. Since stage S7 every line runs in the container: the builder runs them only through the check helper's `run_check`, which judges them by the run's frozen copy of this list, and its header says so (decision 27). The starting set is the B2 rerun's list, tightened so that no line accepts a flag it does not name. The targeted audit and the close-out verifier are accepted only with `--read-only`, so neither writes a log or result file (orchestrator isolation plan 6).
- codex-verify-commands.txt - `workflows/review-orchestration/config/codex-verify-commands.txt` - The verification commands a Codex builder may run, in the same format, since S7 only through the check helper and in the container, as its header says. Since S7 it holds the targeted audit and the close-out verifier in their read-only forms only (inside the clean copy neither writes anywhere that matters nor reads a personal file), and it still names each workflow check it allows instead of accepting any workflow's `--check`.
- codex-read-commands.txt - `workflows/review-orchestration/config/codex-read-commands.txt` - The read commands a Codex builder may run, since it reads files through the shell: one regular expression per line with the tokens `PATH` and `QUOTED` replaced before compiling, and one exact command that gives the time for a LOG.md entry. The lines are for PowerShell. `Get-Content` is accepted only with `[Console]::OutputEncoding` set to UTF-8 and `-Encoding UTF8` together, because Windows PowerShell 5.1 returns non-ASCII text wrongly with either alone (measured 2026-10-04).

- provider-identifiers.txt - `workflows/review-orchestration/config/provider-identifiers.txt` - The identity check's lists (plan 10.3 step 3, A3 chunk 2), read by `scripts/planning.py` before the two planners' plans reach the synthesiser: one identifier per line under `[names]` (matched without case as whole words, with every model name in `settings.json` added by the script), `[distinctive]` (tool names that are not ordinary words, matched with case anywhere), `[common]` (tool names that are ordinary words, matched only where written as a tool: in a backtick span on one line, or followed by `(`) and `[prefixes]` (`mcp__`, any identifier beginning with it). Blank lines and lines starting `#` are ignored; a line outside a section, a section named otherwise or given twice, an identifier holding whitespace, or a section missing or empty refuses the run.

## Inputs
None. The files are edited by hand.

## Outputs
None directly. `workflows/review-orchestration/scripts/settings.py` [[workflows/review-orchestration/scripts/CONTEXT]] reads and checks `settings.json`, `approver.py` in the same folder reads `verify-commands.txt`, `codex_rules.py` reads the two Codex lists, and `planning.py` reads `provider-identifiers.txt` from beside its own code, so a run uses its trusted copy's. Every run's frozen folder, made by the orchestrator when the run starts (`scripts/frozen.py`), holds all three lists as the start commit has them (the Claude list for the check helper, `scripts/check_server.py`), and the builder is judged by those copies, so a change here takes effect on the next run after it is committed, not on one in progress.

## Steps
N/A - this is a configuration directory, not a workflow.

## Dependencies
- `workflows/review-orchestration/scripts/settings.py` [[workflows/review-orchestration/scripts/CONTEXT]] - reads `settings.json`. It refuses a value outside its allowed range rather than replacing it with the default.
- `workflows/review-orchestration/scripts/approver.py` [[workflows/review-orchestration/scripts/CONTEXT]] - reads `verify-commands.txt`, refusing a line that is not a valid regular expression and a file that lists nothing.
- `workflows/review-orchestration/scripts/codex_rules.py` [[workflows/review-orchestration/scripts/CONTEXT]] - reads the two Codex lists, with the same refusals.
- `workflows/review-orchestration/scripts/planning.py` [[workflows/review-orchestration/scripts/CONTEXT]] - reads `provider-identifiers.txt`, refusing a malformed file before any planning session; `tests/test_planning.py` checks the shipped lists against plan 10.3 step 3.
- `workflows/review-orchestration/tests/test_codex_rules.py` [[workflows/review-orchestration/tests/CONTEXT]] - holds one case per line of each Codex list and fails when a line is added without one.

## Known Issues
- `codex-verify-commands.txt` is a measurement, not a proof that a line writes nothing: on 2026-10-02 the commands it accepts changed no tracked or hand-written file, but a test file can write anywhere. Since S7 every line runs in the container over a clean copy that is removed afterwards, so a write lands in the copy, not the project.
- A workflow check is added to the Codex list by adding its name, after reading what it does: it must not reach the network, read the project's `.env` file, or write a tracked or hand-written file. The Reddit collector's check makes an HTTP request, and the web-research and personal-data guard checks read `.env`, so all three are left off.
- `codex-read-commands.txt` is written for PowerShell, the shell Codex uses on Windows. On another system its lines need rewriting, and the every-line tests with them. The `Get-Content` line's two encoding settings are for Windows PowerShell 5.1 on a console whose code page is 850; under PowerShell 7, which reads and writes UTF-8 by default, they would be unnecessary but harmless.
- `provider-identifiers.txt` lists what plan 10.3 step 3 names on 2026-10-14. A tool a provider adds later is not caught until its name is added here, as `[distinctive]` unless it is an ordinary word; `tests/test_planning.py` asserts the shipped lists, so such a change updates the test too.
- Model names are the defaults on 2026-09-24 and are checked at run time; a model a provider no longer offers stops the run rather than being substituted.
- A pattern in `verify-commands.txt` that is too broad widens what the builder may run; an argument class that allows `-` lets in flags the line never meant, which is how the audit's `--save` got through (R2-2). The approver and the check helper still refuse a `..` path component, a `.env` file, more than one line and quoting they cannot read whatever a pattern allows, but anything else a pattern matches runs, in the container.

## Revision History
- 2026-09-25 - Initial creation with `settings.json` holding `usage_ceiling_percent`, for the usage-limit reading of Stage A2's first chunk.
- 2026-09-26 - Stage A2, chunk (b): `settings.json` gained `roles`, `round_caps` and `models` (plan section 11), and `verify-commands.txt` was added for the builder's approver.
- 2026-09-26 - `verify-commands.txt` tightened after code review round R2 (R2-2): a free argument can no longer start with `-`, so the audit's `--save`, close-out's `--repair` and `py_compile`'s flags are refused; the audit line gained an optional `--no-graph`. The file's header states the rule.
- 2026-09-26 - The test-runner line brought under that rule after code review round R3 (R3-1): it takes test names and an optional `-v` only, where it had still accepted any flag.
- 2026-10-02 - Stage A2, chunk (d): `settings.json` gained `models.codex.builder` and `models.claude.reviewer`, so either provider can hold either role; added `codex-verify-commands.txt` and `codex-read-commands.txt`, a Codex builder's two command lists.
- 2026-10-04 - Fixes from the chunk (d) proof run: `settings.json` lost `roles`, since the builder is named per run with `--builder` (editing the file to swap roles had refused every run); the `Get-Content` line of `codex-read-commands.txt` now requires both encoding settings, after the proof run's reads came back garbled.
- 2026-10-08 - Orchestrator isolation stage S5: `verify-commands.txt`'s targeted-audit and close-out lines require `--read-only`, and its header says so. The Codex list's two new lines wait for S6 (decision 27, the user's option A).
- 2026-10-08 - Outputs corrected for orchestrator isolation stage S6a: the orchestrator now makes every run's frozen folder at the start, with the lists as the start commit has them, rather than a Codex builder copying them at its first launch (code review R18-3). Decision 28 has since moved the Codex list's two new lines to S7.
- 2026-10-11 - Orchestrator isolation stage S7: `codex-verify-commands.txt` gained the read-only close-out verifier and targeted audit (plan 6), and both verification lists' headers say every line runs in the container, through the check helper only (decision 27). Contents and Known Issues updated.
- 2026-10-19 - Added `max_turns` and `usage_probe_model` to the resolved run settings, with the plan's defaults. Contents updated.
- 2026-10-20 - A3 chunk 2: added `provider-identifiers.txt`, the identity check's four lists (plan 10.3 step 3). Contents, Outputs, Dependencies and Known Issues updated.
