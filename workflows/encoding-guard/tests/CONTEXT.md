# Tests

**Last modified:** 2026-08-04

## Purpose
Unit tests for the encoding-guard script. They exercise the pure detection and repair helpers without touching the real project tree, verify the directory walk scans authored hidden config dirs while pruning system and verbatim-data dirs, and assert the guard's own source stays pure ASCII so it can never flag or repair itself.

## Contents
- test_encoding_guard.py - `workflows/encoding-guard/tests/test_encoding_guard.py` [[workflows/encoding-guard/tests/CONTEXT]] - Tests for signature generation, `scan_text`, `repair_text`, `repair_bytes`, `_has_encoding_problem`, the code-pattern check, the hidden-dir walk filtering (`iter_text_files`), and the pure-ASCII-source guarantee.
- run_tests.py - `workflows/encoding-guard/tests/run_tests.py` [[workflows/encoding-guard/tests/CONTEXT]] - One-command unittest runner for this directory.

## Inputs
None. Tests construct their own in-memory fixtures and a throwaway temp tree for the directory-walk test.

## Outputs
None. Prints a unittest summary; exits non-zero on failure.

## Steps
Run from anywhere:

```
python workflows/encoding-guard/tests/run_tests.py
```

## Dependencies
- `workflows/encoding-guard/scripts/run.py` [[workflows/encoding-guard/scripts/CONTEXT]] - The module under test, imported directly.
- Python 3.9+ standard library (unittest, tempfile).

## Known Issues
None.

## Revision History
- 2026-06-24 - Initial creation.
- 2026-06-25 - Added TestHiddenDirWalk covering the blind-spot fix: `iter_text_files` now scans authored hidden config dirs (e.g. `.codex`) while still pruning system dot-dirs (`.venv`, `.vscode`) and the verbatim-data exemptions (`raw/`).
- 2026-08-04 - Added 17 tests for the CRLF check and the narrowed repair, taking the suite from 26 to 43. Written against the stated rule rather than the implementation, with a positive control (a CRLF file is reported), a negative control (an LF file is not), and a lone-CR case. Three cover the traps rather than the feature: that the real pipeline reads bytes, so a later change to text-mode reading cannot make the check silently unfireable; that a CRLF-only file keeps its em dash through `--fix`, which is the regression that would follow from routing it through `repair_bytes`; and that `--preserve-mtime` holds a LOG.md timestamp while its absence moves it, since that flag exists to stop a bulk pass falsifying doc-sync-guard's evidence.
- 2026-08-04 - Added `TestNewlineCheck` (8 tests, suite 43 to 51) for the newline clause of the text-I/O rule, after it turned out the code check returned early on `encoding=` and so never enforced it. The positive controls are constructed from the rule as AGENTS.md states it rather than copied out of the tree, which is the half that proves a checker reads the right definition instead of merely firing on something: `write_text` with an encoding and no newline, the five `open()` write modes, and the `mode=` keyword form. The negative controls pin what must stay silent - compliant writes, `csv`-style `newline=""`, reads, binary writes, `subprocess`, prose in a docstring, and a computed mode the checker cannot read and must not guess at. One test asserts the structural guarantee directly: a write missing both arguments reports one finding per clause, so neither check can ever swallow the other again. The six local fixture writes in this file were converted to `write_bytes` in the same pass, since a test suite for this rule breaking it was the first thing a reader would notice.
