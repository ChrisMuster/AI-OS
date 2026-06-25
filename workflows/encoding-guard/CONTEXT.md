# Encoding Guard

**Last modified:** 2026-06-25

## Purpose
Keeps the project's text encoding clean and consistent. It is the single source of truth for encoding hygiene: a deterministic, script-driven check that scans the project for files that are not valid UTF-8, for mojibake (double-encoded or stray Windows-1252 punctuation), and for unexpected byte-order marks, plus a repair mode that normalises the damaged files back to clean UTF-8 with LF line endings and plain ASCII punctuation. Scraped and imported third-party data (Reddit collections, wiki `raw/` imports) is deliberately exempt so verbatim source data is never rewritten. The full audit consumes the check as an additive, advisory hook, so an encoding regression surfaces at close-out alongside the structural and knowledge-graph findings.

## Contents
- scripts/ - `workflows/encoding-guard/scripts/` [[workflows/encoding-guard/scripts/CONTEXT]] - The `run.py` entry point (check and fix modes) plus shared detection/repair helpers.
- tests/ - `workflows/encoding-guard/tests/` [[workflows/encoding-guard/tests/CONTEXT]] - Unit tests for the detection signatures, the repair maps, and idempotency, plus a one-command runner.

## Inputs
None required. The check walks the project tree relative to the project root. Optional flags select check vs fix mode, JSON output, and dry-run.

## Outputs
- A findings report printed to stdout (human text, or `--json` for programmatic consumers such as the audit hook).
- In fix mode: repaired files written in place as UTF-8 with LF line endings. Read-only check mode writes nothing.
- LOG.md entries (workflow and root) on a real fix run. The read-only check does not log.

## Steps
1. Scan the project for encoding problems (read-only):
   `python workflows/encoding-guard/scripts/run.py --check [--json]`
2. Preview repairs without writing anything:
   `python workflows/encoding-guard/scripts/run.py --fix --dry-run`
3. Repair the flagged files in place:
   `python workflows/encoding-guard/scripts/run.py --fix`
4. Append LOG.md with a completion or failure entry.

## Dependencies
- `AGENTS.md` [[AGENTS]] (root) - Defines the encoding and text-I/O rules and the em-dash-avoidance rule this workflow enforces.
- `workflows/audit/` [[workflows/audit/CONTEXT]] - The full audit shells out to this workflow's `--check --json` and merges its WARN/FAIL findings under an `encoding` label.
- Python 3.9+ standard library only.

## Known Issues
- The check classifies legitimate Unicode (for example correctly encoded accented characters or em-dashes) as clean; it targets encoding corruption, not style. The em-dash-avoidance rule is a writing convention for authored content, not an automated failure here.
- Display-layer artifact, not corruption: a valid UTF-8 em-dash (bytes E2 80 94) renders as a mojibake-like sequence ("a-tilde, euro, double-quote") in any terminal or editor still using the Windows cp1252 code page. The file is clean and the guard correctly reports it clean; what looks like mojibake is the viewer decoding good UTF-8 with the wrong code page. This is the expected explanation when the guard passes but the screen looks wrong; it is not a scanner blind spot.
- Repair handles the common Windows-1252 punctuation cases (dashes, smart quotes, ellipsis, non-breaking space). A file with other invalid bytes that cannot be mapped confidently is reported but left untouched rather than guessed at.
- Exemption is by directory: `raw/` import folders and `collections/`/`archive/` data are pruned from the walk. Authored hidden config dirs (`.codex/`, `.github/`, `.windsurf/`, etc.) are scanned; only system/tooling dot-dirs on the `SKIP_DIRS` denylist are pruned. A scraped data file stored outside the exempt conventions would be scanned like authored content.

## Revision History
- 2026-06-24 - Initial creation. Check/fix script, audit-hook integration, and tests.
- 2026-06-25 - Fixed the hidden-directory blind spot: the walk now scans authored dot-dir config (`.codex/`, `.github/`, `.windsurf/`, `.clinerules/`, `.continue/`, etc.) instead of skipping every dot-dir, pruning only system/tooling dot-dirs via `SKIP_DIRS`. Added a directory-walk coverage test. Documented the cp1252 display-layer artifact as the explanation for "guard clean but terminal shows mojibake".
