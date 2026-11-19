# Codex Rules

**Last modified:** 2026-11-19

## Purpose
Project-scoped Codex rules files: commands an interactive Codex session in this project may run outside its sandbox without an approval prompt. Codex reads every `*.rules` file here when the project is trusted.

## Contents
- review-orchestration.rules - `.codex/rules/review-orchestration.rules` - Seven `prefix_rule` entries allowing `python workflows/review-orchestration/scripts/start.py` (the orchestrator's starter, since orchestrator isolation stage S6a) with `--run`, `--resume`, `--stop`, `--approve`, `--amend`, `--measure` or `--review`, so Codex can start, resume, stop, approve and amend a review-orchestration run, start, resume or score a measurement (one `--measure` rule covers all three forms), and run one conversation review round or its build gate (`--review`, plan 10.12), which must run outside the sandbox because it starts Claude and Codex sessions and writes its run record; a review round also starts Docker and commits to the personal repository. No rule names `run.py`: a start goes through the starter's committed-tree check, and a resume or stop runs the run's trusted copy. The exception is `--review`: a review round reviews uncommitted work, so it has no committed-tree check and no trusted copy (plan 10.12), and its allow runs the live tree's `crossreview.py`, and any `Checks` files its brief names, as they stand, outside the sandbox. The counterpart of the Claude allowlist's rule for the same script.

## Inputs
- Codex loads the rules when a session starts in the project and the project is trusted in the user's Codex configuration.

## Outputs
- A command matching a rule runs with no approval prompt and outside Codex's sandbox. Nothing is written by the rules themselves.

## Steps
N/A. This is a configuration directory, not a workflow.

## Dependencies
- `workflows/review-orchestration/` [[workflows/review-orchestration/CONTEXT]] - the starter the rules name; `tests/test_codex_rules.py` checks the rules file with `codex execpolicy check`.
- Codex's rules support (`prefix_rule`, documented at developers.openai.com/codex/rules) in the `codex-cli` the project uses (0.157.0 when written; 0.170.0, bundled with `openai-codex` 0.170.0, since 2026-11-19).

## Known Issues
- Measured on one machine and one Codex version (2026-10-04, the review-orchestration decision log, "Probes for fixes 5 and 1, and the live rules turn"): a live Codex applies a rule to the PowerShell-wrapped form it issues commands in on Windows, an allowed command runs outside the sandbox, and a `;`-joined command does not inherit the allow. `codex execpolicy check` does not unwrap the wrapper, so it cannot show the first of these; the test checks plain tokens only.
- The starter rules have been matched by a live Codex for `--run` and `--resume`: in the orchestrator isolation proof run (2026-10-13), from the VS Code Codex panel, a start, a dry resume and a real resume ran with no approval prompt and outside the sandbox. `--stop`, `--approve`, `--amend`, `--measure` and `--review` have the same shape and have not been run from Codex.
- A command allowed here runs with the user's full permissions. Widening the rules beyond these seven modes is the user's decision.

## Revision History
- 2026-10-04 - Initial creation, with `review-orchestration.rules` (the review-orchestration chunk (d) amendment, fix 1).
- 2026-10-08 - The three rules name the starter, `start.py`, instead of `run.py` (orchestrator isolation stage S6a, plan 7.0 item 2).
- 2026-10-13 - Known Issues: the starter rules are now shown matched by a live Codex for a start and a resume (the orchestrator isolation proof run); no rule changed.
- 2026-10-18 - Two `prefix_rule` entries added by the user by hand, for `start.py --approve` and `--amend` (review orchestration A3 chunk 1, which no run may add, decision 33), and the file's opening comment names all five modes. Contents and Known Issues updated. Then, from Codex review R27-2 and at the user's word for this one edit, the comment's fourth line says the starter resumes, stops, approves or amends from the run's trusted copy.
- 2026-11-10 - A sixth `prefix_rule`, for `start.py --measure` (review orchestration A5, the hand entry no run may add, decision 33), added at the user's word; the opening comment names the measurement. Contents and Known Issues updated.
- 2026-11-18 - A seventh `prefix_rule`, for `start.py --review` (review orchestration A6, the hand entry no run may add, decision 33), added at the user's word; the opening comment names the review round. Contents and Known Issues updated. Then, from the second live cross-review round (R2-1), the comment and Contents say `--review` is the one allowed mode that runs uncommitted code, with no committed-tree check and no trusted copy.
- 2026-11-19 - Dependencies name `codex-cli` 0.170.0, which the review-orchestration SDK upgrade brings; no rule changed.
