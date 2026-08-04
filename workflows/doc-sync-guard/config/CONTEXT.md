# Config

**Last modified:** 2026-08-03

## Purpose
Holds the classified output inventory: the single tracked record of every path in `.gitignore`, plus the guard output paths named before they exist, each classified by what must be true of it. One row states whether a path must be ignored, must be tracked, or is out of scope for this project's judgement at all; whether it seeds the unregistered-output check; which documentation exception (if any) applies to it; and which derivation named it. The inventory is tracked rather than local because it stores paths only at `.gitignore` granularity and uses synthetic examples below personal containers, so it carries no personal content. It is read through one loader so that every consumer answers the same question the same way instead of each inventing its own reading of the same path list.

## Contents
- output-inventory.yaml - `workflows/doc-sync-guard/config/output-inventory.yaml` [[workflows/doc-sync-guard/config/CONTEXT]] - The classified inventory: a required top-level `version` plus a `rows:` list. Each row carries `path`, `assertion`, `seed`, `provenance`, and then either `doc_sync` (for an assertion row) or `reason` (for an out-of-scope row), with `example` present only on patterned assertion rows.

## Inputs
None. The file is hand-maintained; a row is added when a path is added to `.gitignore` or a new guard output destination is named.

## Outputs
None directly. The inventory is data: `workflows/doc-sync-guard/scripts/inventory.py` [[workflows/doc-sync-guard/scripts/CONTEXT]] parses it and answers on its behalf.

## Steps
N/A - this is a configuration directory, not a workflow.

## Dependencies
- `workflows/doc-sync-guard/scripts/inventory.py` [[workflows/doc-sync-guard/scripts/CONTEXT]] - The only reader. Nothing else parses this file.
- `.gitignore` (root) - The inventory's assertion rows are derived from it, one row per non-comment line, and the tests assert each row against `git check-ignore`.
- PyYAML, from the project `.venv` - the loader parses the file with it.

## Known Issues
- The inventory is hand-maintained. A test asserts both directions of the `.gitignore` mapping: every row's asserted path holds against `git check-ignore`, and every non-comment `.gitignore` line is answered by a row, so a line added without one fails the suite. What nothing yet catches is a script writing to a path nobody registered at all, which is a separate job from asserting a registered one.
- A row's example is synthetic wherever the real name would be personal. An example is a path shape, not a file that exists, so a passing assertion proves the pattern is ignored rather than that anything was written there.
- Signature exclusion is deliberately not stored here. It is derived by the loader from the row's assertion, its doc-sync answer, and its basename, because two hand-maintained columns that must agree is another way to encode drift.

## Revision History
- 2026-08-02 - Initial creation. Tracked `output-inventory.yaml` with 74 classified rows: 63 derived from `.gitignore` and 11 guard output paths named before they exist.
- 2026-08-03 - Code-review fix. The certification Markdown report row is now patterned (`certification/*.md` with the synthetic name moved into `example`) instead of concrete. `seed_paths()` returns row paths rather than examples, so as a concrete row it handed the future unregistered-output check a destination no writer will ever write; as a pattern the synthetic name stays in a field no seed view reads, and the row still asserts that a `.md` report shape is ignored, matching the `.json` row beside it. Row count and every other row unchanged. Known Issues corrected: a test does assert that every non-comment `.gitignore` line is answered by a row, which the previous wording denied.
