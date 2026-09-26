# Review Orchestration - Tests

**Last modified:** 2026-09-26

## Purpose
Hermetic unittest suites for the review-orchestration scripts. The checker validates, so each rule carries up to three controls, named in the test names: a **positive control** (a legal instance, built from the specification, which must produce nothing), a **rejection control** (an illegal instance, which must be refused with its reason named) and a **negative control** (something that looks like the subject but is not an instance of it, such as an operator inside quotes, a heading inside a comment, or a dash line under a list item, which must not be refused for it).

## Contents
- test_brief.py - `workflows/review-orchestration/tests/test_brief.py` - 105 tests in twelve classes: `ReadingTests` (path, encoding, line endings), `HeadingTests`, `EmptyTests` (placeholders, fences, deeper headings), `AcceptanceTests`, `CommandTests` (the one-command rule: operators, substitution, quoting, every refused wrapper, and every refused launcher), `EditPathTests`, `OpenQuestionTests`, `SourceTests`, `OutputTests` (every problem reported, and in the right order), `TemplateTests` (the shipped template refused only for emptiness), `CommandLineTests` (logging, `--dry-run`, and `run.py --check-brief` returning the checker's result unchanged) and `SmokeTests` (both scripts as subprocesses).
- test_limits.py - `workflows/review-orchestration/tests/test_limits.py` - 48 tests in four classes: `CodexReadingTests` (the live response shape, one window, ties, usage not allowed with and without windows, the three unavailable causes with a validation failure naming its field, a closed transport raised, the exact request sent, a negative result holding for the rest of its run but not the next and never softening a reported limit, and which turn errors are usage limits), `ClaudeReadingTests` (the event recorded by the B1 probe pushed through the SDK's own parser, the top-level fallback, windows accumulating, a rejection and its clearing, paid overage stopping with the included window's reset, even when the event is also a rejection, never borrowing another window's reset, and not stopping once ended or when merely available, and which message errors are usage limits), `CeilingTests` and `SettingsTests` (the shipped file, defaults, and every refused value).
- test_runrecord.py - `workflows/review-orchestration/tests/test_runrecord.py` - 10 tests in two classes: `RunRecordTests` (the ID form, creation and its first state, refusal of an existing run or a malformed ID, `--dry-run`, and the atomic LF UTF-8 rewrite) and `ClassificationTests` (asks the real repository's `git check-ignore` that a run record is ignored before any exists, and that the workflow's own files beside it are not).

## Inputs
- The scripts under test, loaded by file path from `workflows/review-orchestration/scripts/` [[workflows/review-orchestration/scripts/CONTEXT]].
- `workflows/review-orchestration/prompts/brief.md` [[workflows/review-orchestration/prompts/brief]], read in place by `TemplateTests` and `SmokeTests`.
- `workflows/review-orchestration/config/settings.json` [[workflows/review-orchestration/config/CONTEXT]], read in place by `SettingsTests`.
- The two AI SDKs, for `test_limits.py`'s Codex tests and its B1-event test, which build their inputs with the SDKs' own models and parser.
- The real repository's ignore rules, for `ClassificationTests`.

## Outputs
- Test results on stdout, exit code 0 when all pass. Every other fixture is written into a temporary directory with `write_bytes`, so no CRLF is introduced on Windows. Nothing writes to the real `LOG.md` or the real `runs/`: in-process calls inject a temporary log path or runs folder and subprocess calls pass `--dry-run`.

## Steps
1. Run each suite, for example `python workflows/review-orchestration/tests/test_brief.py`, under the project `.venv` so the SDKs are present.
2. Confirm every test passes (exit 0) and that none was skipped.
3. When a rule in a script changes, change or add its controls here in the same change.
4. Append LOG.md only when run as a deliberate, logged workflow step.

## Dependencies
- `workflows/review-orchestration/scripts/` [[workflows/review-orchestration/scripts/CONTEXT]] - the modules under test.
- `workflows/review-orchestration/prompts/` [[workflows/review-orchestration/prompts/CONTEXT]] - the template the suite asserts is refused.
- `workflows/review-orchestration/config/` [[workflows/review-orchestration/config/CONTEXT]] - the settings file the suite asserts loads.
- Python 3.13+ standard library, plus `openai-codex` and `claude-agent-sdk` from `workflows/review-orchestration/requirements.txt` [[workflows/review-orchestration/CONTEXT]] for `test_limits.py`, and the `git` CLI for `ClassificationTests`.
- `workflows/close-out/` [[workflows/close-out/CONTEXT]] discovers and runs these suites as part of the close-out verifier.

## Known Issues
- Nothing enforces the control naming. A test added without a control prefix is simply an unlabelled test, held to the convention by review alone.
- `TemplateTests` and `SmokeTests` read the real template, and `SettingsTests` the real settings file, so they depend on the tree rather than on a fixture. That is deliberate: the property being proved is about those files.
- Without the SDKs installed, the tests that need them are skipped, with the install instruction as the reason. A run where they were skipped has not proved the Codex reading or the B1 event; the test output reports the skip count.
- No test calls a live provider. The Codex response shape was confirmed live on 2026-09-25 by one `account/rateLimits/read` request with no model call, outside the suite.

## Revision History
- 2026-09-24 - Initial creation with `test_brief.py`, 97 tests.
- 2026-09-24 - Code review round R1 repairs: negative controls for a shell name used as data and a `-c` passed to a script, rejection controls for a command option after a valued option and behind a launcher, lone-CR line-ending controls, and the unwritable-log test now expects a FAIL. 97 to 102 tests.
- 2026-09-25 - Code review round R2 repairs: a rejection control for every launcher on the list, a rejection control for a launcher hiding a wrapper (including `env -S` and `--split-string`), and a negative control for launcher and shell names used as data. The launcher cases left the wrapper controls, since a launcher is now refused for itself. 102 to 105 tests.
- 2026-09-25 - Stage A2, chunk (a): added `test_limits.py` (38 tests) and `test_runrecord.py` (10 tests).
- 2026-09-25 - Code review round R1 repairs for chunk (a): three `RunUsage` controls (a new run applies the ceiling; a negative result holds for the rest of the run and not the next; it never softens a reported limit), and the validation test now requires the failing field's name. `test_limits.py` 38 to 41 tests. Then four controls for the user's paid-overage rule (the B1 event with overage on, through the SDK parser; an overage-window event; overage ended; overage available but unused). 41 to 45.
- 2026-09-25 - Code review round R2 repair for chunk (a): a rejection control for a rejected event on the overage window with overage in use, through the SDK parser, which must report paid overage and the included window's reset (R2-1). 45 to 46 tests.
- 2026-09-26 - Code review round R3 repair for chunk (a): a rejection control where the fullest window has no reset and the event names an emptier one (the reset must stay unknown), and a positive control where the event names the fullest window (its reset is used), both through the SDK parser (R3-1). 46 to 48 tests.
