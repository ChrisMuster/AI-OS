# Scripts

**Last modified:** 2026-08-04

## Purpose
Holds the encoding-guard implementation. `run.py` is both a CLI and an importable module: it walks the project tree (pruning system, generated, and scraped-data directories, while scanning authored hidden config dirs such as `.codex/` and `.github/`), classifies each text file for UTF-8 validity, mojibake signatures, unexpected BOMs, and CR line endings, checks Python sources for text I/O calls missing an explicit `encoding=` or `newline=`, and in fix mode rewrites damaged files to clean UTF-8 with LF line endings and ASCII punctuation. Detection signatures are built from Unicode escapes so this source stays pure ASCII and never flags or repairs itself.

## Contents
- run.py - `workflows/encoding-guard/scripts/run.py` [[workflows/encoding-guard/scripts/CONTEXT]] - Entry point and library. Provides `--check` (read-only scan, with `--json`) and `--fix` (in-place repair, with `--dry-run`). The directory walk prunes system/tooling dot-dirs via a `SKIP_DIRS` denylist but descends into authored hidden config dirs so their tracked prose is covered. The pure helpers `scan_text`, `repair_text`, and `repair_bytes` are unit-tested without touching the filesystem.

## Inputs
No required inputs. Optional flags:
- `--check` - Read-only scan (default when no mode is given).
- `--json` - Emit findings as a JSON object on stdout (for the audit hook).
- `--fix` - Repair flagged files in place.
- `--dry-run` - With `--fix`, print each repair without writing.

## Outputs
- Findings report on stdout (human or JSON).
- In fix mode: repaired files (UTF-8, LF). Read-only check writes nothing.
- LOG.md entries (workflow and root) on a real fix run.

## Steps
Run from anywhere:

```
python workflows/encoding-guard/scripts/run.py --check [--json]
python workflows/encoding-guard/scripts/run.py --fix [--dry-run]
```

## Dependencies
- `AGENTS.md` [[AGENTS]] (root) - The encoding and em-dash rules this script enforces.
- `workflows/encoding-guard/LOG.md` - Appended on every real fix run.
- `LOG.md` (root) - Appended on every real fix run.
- Python 3.9+ standard library only (argparse, os, sys, json, pathlib).

## Known Issues
- The subprocess and file-open code checks are heuristic (regex over `.py` source). They flag a missing `encoding=` on text-mode `subprocess` calls (WARN) and on `open`/`read_text`/`write_text` calls (INFO), and a missing `newline=` on a text-mode write (WARN); an unusual call layout could be missed or, rarely, mis-flagged.
- The newline check needs an `open()` call's mode to decide whether it is a text write. `_open_mode` reads a literal second positional argument or a literal `mode=` keyword; a **computed** mode (`open(p, mode, ...)`) is deliberately not guessed at and is left unflagged, so an unreadable call is a false negative rather than a false positive. `.write_text` needs no mode inspection and is always a text write.
- Repair maps cover the common Windows-1252 punctuation set. Other invalid bytes are reported but not auto-repaired, to avoid corrupting content by guessing.
- Directory coverage is a denylist (`SKIP_DIRS`), not an allowlist: any future hidden dir not on the denylist is scanned. This is intentional so new AI config dirs are covered automatically, but a new tooling dir that should be skipped must be added to `SKIP_DIRS`. The `TEXT_EXTS` gate still prevents reading binary files in any case.

## Revision History
- 2026-06-24 - Initial creation. Check/fix script with ASCII-safe detection signatures.
- 2026-06-25 - Blind-spot fix: `iter_text_files` no longer prunes every dot-dir. It now descends into authored hidden config dirs (`.codex/`, `.github/`, `.windsurf/`, etc.) and prunes only system/tooling dot-dirs, with `.idea`, `.vscode`, `.cache`, `.tox`, `.svn`, `.hg` added to `SKIP_DIRS`. Adds 36 previously-unscanned hidden-dir text files to coverage; 0 new findings on the current clean tree.
- 2026-08-04 - `scan_text` now returns a `crlf` code and `classify_file` reports it as a WARN, closing the gap between what this workflow is documented to enforce and what `--check` actually looked at: LF normalisation lived only in the `--fix` path, so a CRLF file was silently repaired if someone ran `--fix` and completely invisible if they ran `--check`, including the audit's `encoding` hook. Measured at the moment of the fix: 138 files in the tree were CRLF while `--check` reported clean. `scan_text` gained a docstring warning that its caller must decode bytes rather than open in text mode, since text mode strips the CR on the way in and would make the check permanently silent. Two new helpers back the repair side: `problem_codes` returns which faults a file has, and `repair_newlines` normalises endings and nothing else, so a CRLF-only file is no longer routed through the full `repair_bytes` path that would also fold its legitimate em dashes to ASCII. A `--preserve-mtime` flag was added for bulk passes, because doc-sync-guard reads LOG.md mtime as evidence a directory logged its change and an unqualified normalisation of 71 LOG.md files would have made every directory look freshly logged. The `_log` append was itself pinned to `newline="\n"`.
- 2026-08-04 - `check_python_code` restructured so the encoding and newline clauses are independent checks rather than one early return. It previously did `if "encoding=" in call: continue`, which meant a text write that pinned the encoding and omitted `newline=` never reached any further check and was reported clean - the single line that left the newline half of the AGENTS.md rule unenforced across the project. Three helpers back the new clause: `_split_args` (quote- and bracket-aware top-level argument splitting, so a comma inside a string cannot split an argument), `_literal_value`, and `_open_mode`, which together decide whether an `open()` call is a text write without resorting to a substring test that cannot tell a mode from any other short literal. `_is_text_mode_write` excludes reads, binary modes, and `subprocess`, because the newline rule governs what is written rather than what is read.
