# Codex Rules

**Last modified:** 2026-10-04

## Purpose
Project-scoped Codex rules files: commands an interactive Codex session in this project may run outside its sandbox without an approval prompt. Codex reads every `*.rules` file here when the project is trusted.

## Contents
- review-orchestration.rules - `.codex/rules/review-orchestration.rules` - Three `prefix_rule` entries allowing `python workflows/review-orchestration/scripts/run.py` with `--run`, `--resume` or `--stop`, so Codex can start, resume and stop a review-orchestration run, which must run outside the sandbox because it starts Claude and Codex sessions and writes its run record. The counterpart of the Claude allowlist's rule for the same script.

## Inputs
- Codex loads the rules when a session starts in the project and the project is trusted in the user's Codex configuration.

## Outputs
- A command matching a rule runs with no approval prompt and outside Codex's sandbox. Nothing is written by the rules themselves.

## Steps
N/A. This is a configuration directory, not a workflow.

## Dependencies
- `workflows/review-orchestration/` [[workflows/review-orchestration/CONTEXT]] - the entry point the rules name; `tests/test_codex_rules.py` checks the rules file with `codex execpolicy check`.
- Codex's rules support (`prefix_rule`, documented at developers.openai.com/codex/rules) in the `codex-cli` the project uses (0.157.0 when written).

## Known Issues
- Measured on one machine and one Codex version (2026-10-04, the review-orchestration decision log, "Probes for fixes 5 and 1, and the live rules turn"): a live Codex applies a rule to the PowerShell-wrapped form it issues commands in on Windows, an allowed command runs outside the sandbox, and a `;`-joined command does not inherit the allow. `codex execpolicy check` does not unwrap the wrapper, so it cannot show the first of these; the test checks plain tokens only.
- Only the `run` rule's shape was measured live; the `resume` and `stop` rules are the same shape.
- A command allowed here runs with the user's full permissions. Widening the rules beyond these three modes is the user's decision.

## Revision History
- 2026-10-04 - Initial creation, with `review-orchestration.rules` (the review-orchestration chunk (d) amendment, fix 1).
