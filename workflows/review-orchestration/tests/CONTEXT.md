# Review Orchestration - Tests

**Last modified:** 2026-09-25

## Purpose
Hermetic unittest suite for the review-orchestration scripts. The checker validates, so each rule carries up to three controls, named in the test names: a **positive control** (a legal instance, built from the specification, which must produce nothing), a **rejection control** (an illegal instance, which must be refused with its reason named) and a **negative control** (something that looks like the subject but is not an instance of it, such as an operator inside quotes, a heading inside a comment, or a dash line under a list item, which must not be refused for it).

## Contents
- test_brief.py - `workflows/review-orchestration/tests/test_brief.py` - 105 tests in twelve classes: `ReadingTests` (path, encoding, line endings), `HeadingTests`, `EmptyTests` (placeholders, fences, deeper headings), `AcceptanceTests`, `CommandTests` (the one-command rule: operators, substitution, quoting, every refused wrapper, and every refused launcher), `EditPathTests`, `OpenQuestionTests`, `SourceTests`, `OutputTests` (every problem reported, and in the right order), `TemplateTests` (the shipped template refused only for emptiness), `CommandLineTests` (logging, `--dry-run`, and `run.py --check-brief` returning the checker's result unchanged) and `SmokeTests` (both scripts as subprocesses).

## Inputs
- The scripts under test, loaded by file path from `workflows/review-orchestration/scripts/` [[workflows/review-orchestration/scripts/CONTEXT]].
- `workflows/review-orchestration/prompts/brief.md` [[workflows/review-orchestration/prompts/brief]], read in place by `TemplateTests` and `SmokeTests`.

## Outputs
- Test results on stdout, exit code 0 when all pass. Every other fixture is written into a temporary directory with `write_bytes`, so no CRLF is introduced on Windows. Nothing writes to the real `LOG.md`: in-process calls inject a temporary log path and subprocess calls pass `--dry-run`.

## Steps
1. Run `python workflows/review-orchestration/tests/test_brief.py`.
2. Confirm every test passes (exit 0).
3. When a rule in `brief.py` changes, change or add its controls here in the same change.
4. Append LOG.md only when run as a deliberate, logged workflow step.

## Dependencies
- `workflows/review-orchestration/scripts/` [[workflows/review-orchestration/scripts/CONTEXT]] - the modules under test.
- `workflows/review-orchestration/prompts/` [[workflows/review-orchestration/prompts/CONTEXT]] - the template the suite asserts is refused.
- Python 3.13+ standard library only.
- `workflows/close-out/` [[workflows/close-out/CONTEXT]] discovers and runs this suite as part of the close-out verifier.

## Known Issues
- Nothing enforces the control naming. A test added without a control prefix is simply an unlabelled test, held to the convention by review alone.
- `TemplateTests` and `SmokeTests` read the real template, so they are the one part of the suite that depends on the tree rather than on a fixture. That is deliberate: the property being proved is about that file.

## Revision History
- 2026-09-24 - Initial creation with `test_brief.py`, 97 tests.
- 2026-09-24 - Code review round R1 repairs: negative controls for a shell name used as data and a `-c` passed to a script, rejection controls for a command option after a valued option and behind a launcher, lone-CR line-ending controls, and the unwritable-log test now expects a FAIL. 97 to 102 tests.
- 2026-09-25 - Code review round R2 repairs: a rejection control for every launcher on the list, a rejection control for a launcher hiding a wrapper (including `env -S` and `--split-string`), and a negative control for launcher and shell names used as data. The launcher cases left the wrapper controls, since a launcher is now refused for itself. 102 to 105 tests.
