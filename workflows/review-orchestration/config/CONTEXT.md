# Review Orchestration - Config

**Last modified:** 2026-10-04

## Purpose
Holds the review-orchestration settings, the files the user edits to change how runs behave. Settings live here rather than in the scripts so that nothing a run depends on is hard-coded.

## Contents
- settings.json - `workflows/review-orchestration/config/settings.json` - The run settings: `usage_ceiling_percent` (default 80, the usage level at which a run stops before its next step rather than spend allowance needed for interactive work); `round_caps` (3 build-review rounds, 2 plan-review rounds); and `models`, the model and effort each provider uses in each role, as plan section 11 settled them. Which provider builds is not here: the user names it for each run with `run.py --run --builder claude|codex`, and a file that still holds `roles` is refused.
- verify-commands.txt - `workflows/review-orchestration/config/verify-commands.txt` - The shell commands an orchestrated Claude builder may run to verify its own work: one regular expression per line, the whole command must match one. The starting set is the B2 rerun's list, tightened so that no line accepts a flag it does not name.
- codex-verify-commands.txt - `workflows/review-orchestration/config/codex-verify-commands.txt` - The verification commands a Codex builder may run, in the same format. It leaves out the targeted audit and the close-out verifier, which write log files the check after every Codex builder turn compares, and it names each workflow check it allows instead of accepting any workflow's `--check`.
- codex-read-commands.txt - `workflows/review-orchestration/config/codex-read-commands.txt` - The read commands a Codex builder may run, since it reads files through the shell: one regular expression per line with the tokens `PATH` and `QUOTED` replaced before compiling, and one exact command that gives the time for a LOG.md entry. The lines are for PowerShell. `Get-Content` is accepted only with `[Console]::OutputEncoding` set to UTF-8 and `-Encoding UTF8` together, because Windows PowerShell 5.1 returns non-ASCII text wrongly with either alone (measured 2026-10-04).

## Inputs
None. The files are edited by hand.

## Outputs
None directly. `workflows/review-orchestration/scripts/settings.py` [[workflows/review-orchestration/scripts/CONTEXT]] reads and checks `settings.json`, `approver.py` in the same folder reads `verify-commands.txt`, and `codex_rules.py` reads the two Codex lists. A run with a Codex builder copies those two lists into its frozen folder at its first launch and judges the builder by the copies, so a change here takes effect on the next run, not on one in progress.

## Steps
N/A - this is a configuration directory, not a workflow.

## Dependencies
- `workflows/review-orchestration/scripts/settings.py` [[workflows/review-orchestration/scripts/CONTEXT]] - reads `settings.json`. It refuses a value outside its allowed range rather than replacing it with the default.
- `workflows/review-orchestration/scripts/approver.py` [[workflows/review-orchestration/scripts/CONTEXT]] - reads `verify-commands.txt`, refusing a line that is not a valid regular expression and a file that lists nothing.
- `workflows/review-orchestration/scripts/codex_rules.py` [[workflows/review-orchestration/scripts/CONTEXT]] - reads the two Codex lists, with the same refusals.
- `workflows/review-orchestration/tests/test_codex_rules.py` [[workflows/review-orchestration/tests/CONTEXT]] - holds one case per line of each Codex list and fails when a line is added without one.

## Known Issues
- `codex-verify-commands.txt` is a measurement, not a proof that a line writes nothing: on 2026-10-02 the commands it accepts changed no tracked or hand-written file, but a test file can write anywhere in the project. The check after every Codex builder turn is what catches a line that should not be there, and it ends the run.
- A workflow check is added to the Codex list by adding its name, after reading what it does: it must not reach the network, read the project's `.env` file, or write a tracked or hand-written file. The Reddit collector's check makes an HTTP request, and the web-research and personal-data guard checks read `.env`, so all three are left off.
- `codex-read-commands.txt` is written for PowerShell, the shell Codex uses on Windows. On another system its lines need rewriting, and the every-line tests with them. The `Get-Content` line's two encoding settings are for Windows PowerShell 5.1 on a console whose code page is 850; under PowerShell 7, which reads and writes UTF-8 by default, they would be unnecessary but harmless.
- Model names are the defaults on 2026-09-24 and are checked at run time; a model a provider no longer offers stops the run rather than being substituted.
- A pattern in `verify-commands.txt` that is too broad widens what the builder may run; an argument class that allows `-` lets in flags the line never meant, which is how the audit's `--save` got through (R2-2). The approver still refuses a `..` path component, a `.env` file, more than one line and leaving the sandbox whatever a pattern allows, but anything else a pattern matches runs.

## Revision History
- 2026-09-25 - Initial creation with `settings.json` holding `usage_ceiling_percent`, for the usage-limit reading of Stage A2's first chunk.
- 2026-09-26 - Stage A2, chunk (b): `settings.json` gained `roles`, `round_caps` and `models` (plan section 11), and `verify-commands.txt` was added for the builder's approver.
- 2026-09-26 - `verify-commands.txt` tightened after code review round R2 (R2-2): a free argument can no longer start with `-`, so the audit's `--save`, close-out's `--repair` and `py_compile`'s flags are refused; the audit line gained an optional `--no-graph`. The file's header states the rule.
- 2026-09-26 - The test-runner line brought under that rule after code review round R3 (R3-1): it takes test names and an optional `-v` only, where it had still accepted any flag.
- 2026-10-02 - Stage A2, chunk (d): `settings.json` gained `models.codex.builder` and `models.claude.reviewer`, so either provider can hold either role; added `codex-verify-commands.txt` and `codex-read-commands.txt`, a Codex builder's two command lists.
- 2026-10-04 - Fixes from the chunk (d) proof run: `settings.json` lost `roles`, since the builder is named per run with `--builder` (editing the file to swap roles had refused every run); the `Get-Content` line of `codex-read-commands.txt` now requires both encoding settings, after the proof run's reads came back garbled.
