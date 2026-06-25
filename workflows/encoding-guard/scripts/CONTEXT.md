# Scripts

**Last modified:** 2026-06-25

## Purpose
Holds the encoding-guard implementation. `run.py` is both a CLI and an importable module: it walks the project tree (pruning system, generated, and scraped-data directories, while scanning authored hidden config dirs such as `.codex/` and `.github/`), classifies each text file for UTF-8 validity, mojibake signatures, and unexpected BOMs, and in fix mode rewrites damaged files to clean UTF-8 with LF line endings and ASCII punctuation. Detection signatures are built from Unicode escapes so this source stays pure ASCII and never flags or repairs itself.

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
- The subprocess and file-open code checks are heuristic (regex over `.py` source). They flag a missing `encoding=` on text-mode `subprocess` calls (WARN) and on `open`/`read_text`/`write_text` calls (INFO); an unusual call layout could be missed or, rarely, mis-flagged.
- Repair maps cover the common Windows-1252 punctuation set. Other invalid bytes are reported but not auto-repaired, to avoid corrupting content by guessing.
- Directory coverage is a denylist (`SKIP_DIRS`), not an allowlist: any future hidden dir not on the denylist is scanned. This is intentional so new AI config dirs are covered automatically, but a new tooling dir that should be skipped must be added to `SKIP_DIRS`. The `TEXT_EXTS` gate still prevents reading binary files in any case.

## Revision History
- 2026-06-24 - Initial creation. Check/fix script with ASCII-safe detection signatures.
- 2026-06-25 - Blind-spot fix: `iter_text_files` no longer prunes every dot-dir. It now descends into authored hidden config dirs (`.codex/`, `.github/`, `.windsurf/`, etc.) and prunes only system/tooling dot-dirs, with `.idea`, `.vscode`, `.cache`, `.tox`, `.svn`, `.hg` added to `SKIP_DIRS`. Adds 36 previously-unscanned hidden-dir text files to coverage; 0 new findings on the current clean tree.
