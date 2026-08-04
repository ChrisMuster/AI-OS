# Encoding Guard

**Last modified:** 2026-08-04

## Purpose
Keeps the project's text encoding clean and consistent. It is the single source of truth for encoding hygiene: a deterministic, script-driven check that scans the project for files that are not valid UTF-8, for mojibake (double-encoded or stray Windows-1252 punctuation), for unexpected byte-order marks, and for CR line endings where the project requires LF, plus a repair mode that normalises the damaged files back to clean UTF-8 with LF line endings and plain ASCII punctuation. Alongside the file scan it runs code-pattern checks over Python sources for the two ways a script reintroduces the problem it just repaired: a text-mode call with no explicit `encoding=`, and a text-mode **write** with no explicit `newline=`. The two are checked independently, so pinning one never masks the other. Scraped and imported third-party data (Reddit collections, wiki `raw/` imports) is deliberately exempt so verbatim source data is never rewritten. The full audit consumes the check as an additive, advisory hook, so an encoding regression surfaces at close-out alongside the structural and knowledge-graph findings.

## Contents
- scripts/ - `workflows/encoding-guard/scripts/` [[workflows/encoding-guard/scripts/CONTEXT]] - The `run.py` entry point (check and fix modes) plus shared detection/repair helpers.
- tests/ - `workflows/encoding-guard/tests/` [[workflows/encoding-guard/tests/CONTEXT]] - Unit tests for the detection signatures, the repair maps, and idempotency, plus a one-command runner.

## Inputs
None required. The check walks the project tree relative to the project root. Optional flags select check vs fix mode, JSON output, and dry-run.

## Outputs
- A findings report printed to stdout (human text, or `--json` for programmatic consumers such as the audit hook).
- In fix mode: repaired files written in place as UTF-8 with LF line endings. A file whose only fault is its line endings gets the newline normalisation alone, never the punctuation folding a corrupted file receives. With `--preserve-mtime`, repaired files keep their original modification times. Read-only check mode writes nothing.
- LOG.md entries (workflow and root) on a real fix run. The read-only check does not log.

## Steps
1. Scan the project for encoding problems (read-only):
   `python workflows/encoding-guard/scripts/run.py --check [--json]`
2. Preview repairs without writing anything:
   `python workflows/encoding-guard/scripts/run.py --fix --dry-run`
3. Repair the flagged files in place:
   `python workflows/encoding-guard/scripts/run.py --fix [--preserve-mtime]`
   Use `--preserve-mtime` for a bulk line-ending pass, so repaired `LOG.md` files keep the timestamps doc-sync-guard reads as evidence.
4. Append LOG.md with a completion or failure entry.

## Dependencies
- `AGENTS.md` [[AGENTS]] (root) - Defines the encoding and text-I/O rules and the em-dash-avoidance rule this workflow enforces.
- `workflows/audit/` [[workflows/audit/CONTEXT]] - The full audit shells out to this workflow's `--check --json` and merges its WARN/FAIL findings under an `encoding` label.
- Python 3.9+ standard library only.

## Known Issues
- The check classifies legitimate Unicode (for example correctly encoded accented characters or em-dashes) as clean; it targets encoding corruption, not style. The em-dash-avoidance rule is a writing convention for authored content, not an automated failure here.
- Display-layer artifact, not corruption: a valid UTF-8 em-dash (bytes E2 80 94) renders as a mojibake-like sequence ("a-tilde, euro, double-quote") in any terminal or editor still using the Windows cp1252 code page. The file is clean and the guard correctly reports it clean; what looks like mojibake is the viewer decoding good UTF-8 with the wrong code page. This is the expected explanation when the guard passes but the screen looks wrong; it is not a scanner blind spot.
- Repair handles the common Windows-1252 punctuation cases (dashes, smart quotes, ellipsis, non-breaking space). A file with other invalid bytes that cannot be mapped confidently is reported but left untouched rather than guessed at.
- The line-ending check is only as good as its caller: it reads bytes and decodes them, because opening a file in text mode strips the CR on the way in and would make the check permanently silent. Any future refactor that reaches for `read_text` breaks it without failing a test that reads the file the same wrong way; the pipeline test asserts against the real read path for that reason.
- The directory exemption is coarser than the rule it implements. AGENTS.md exempts scraped and imported third-party data, but the exemption is applied per directory, so an authored `CONTEXT.md` or `LOG.md` living inside a `raw/` folder is unscanned too. Several exist and are CRLF today. They are documentation rather than verbatim source data, so the rule would cover them; the implementation does not.
- Exemption is by directory: `raw/` import folders and `collections/`/`archive/` data are pruned from the walk. Authored hidden config dirs (`.codex/`, `.github/`, `.windsurf/`, etc.) are scanned; only system/tooling dot-dirs on the `SKIP_DIRS` denylist are pruned. A scraped data file stored outside the exempt conventions would be scanned like authored content.
- The newline check currently reports a standing set of WARN findings in workflow **test** files, which the 2026-08-04 write-site pass did not cover: that pass fixed the production scripts and left test fixtures untouched. Every one of them writes into a temporary directory rather than the repository, so none of them can put CRLF into a project file, but they are real violations of the AGENTS.md rule and the guard is right to report them. They are being cleared as a separate piece of work; until then the count is the honest measure of the remaining gap, not noise to be filtered out.
- The newline check tests for the *presence* of a `newline=` argument, not for its value, so `newline=""` (which `csv` requires) passes. AGENTS.md asks for `newline="\n"` specifically; pinning the value would fail the legitimate csv case, so the checkable shape is deliberately the weaker one.

## Revision History
- 2026-06-24 - Initial creation. Check/fix script, audit-hook integration, and tests.
- 2026-06-25 - Fixed the hidden-directory blind spot: the walk now scans authored dot-dir config (`.codex/`, `.github/`, `.windsurf/`, `.clinerules/`, `.continue/`, etc.) instead of skipping every dot-dir, pruning only system/tooling dot-dirs via `SKIP_DIRS`. Added a directory-walk coverage test. Documented the cp1252 display-layer artifact as the explanation for "guard clean but terminal shows mojibake".
- 2026-08-04 - Closed the workflow's largest documented-but-unenforced gap: `--check` now reports CR line endings as a WARN. AGENTS.md states that all project text is UTF-8 with LF and that this workflow enforces all of it, but LF normalisation existed only inside `--fix`, so the LF half was never checked - a CRLF regression was silently repaired if anyone ran `--fix` and completely invisible if they ran `--check`, including the audit's `encoding` hook. Found with a live positive control rather than by reading code: 138 files in the working tree were CRLF at the moment `--check` reported no problems. Three connected changes shipped with it. The repair path was split, so a file whose only fault is its endings is normalised and nothing else, instead of being routed through the full repair that also folds legitimate em dashes to ASCII - the difference between fixing 138 files' newlines and rewriting 138 files' content. A `--preserve-mtime` flag was added for bulk passes, because doc-sync-guard reads LOG.md mtime as its evidence that a directory logged its change, and normalising 71 LOG.md files without it would have made every directory look freshly logged and masked real drift. And the root cause was fixed rather than only the symptom: all 48 text-write sites across the project now pass `newline="\n"` explicitly, since Windows text mode translates every `\n` to `\r\n` and would have reproduced the entire defect within a day. Tests 26 -> 43; the tree is now clean under both this check and an independent CR scan sharing no code with it.
- 2026-08-04 - Correction to the entry above, and the fix for what it got wrong. That entry claimed all 48 text-write sites across the project now pass `newline="\n"` explicitly. The 48 were real and were fixed, but they were the production scripts only: 67 further text writes sat in workflow test files, and nothing could see them because `check_python_code` returned as soon as it found `encoding=` in a call, so a write that pinned the encoding and omitted the newline was reported clean. The claim that the root cause was closed was therefore false when written, and the guard AGENTS.md names as the enforcer of the newline rule was not enforcing it. The two clauses are now checked independently and a text-mode write missing `newline=` is a WARN. Found by Codex's review of stage 3A, reproduced here with the same positive control. 17 of the 67 sites (those in the actively-edited files) are fixed in this pass, using `write_bytes` rather than `Path.write_text(newline=...)`, which is a 3.10 API against a stated 3.9 floor; the remaining 50 are recorded in Known Issues. Tests 43 -> 51, the new ones built as five positive controls drawn from the rule in AGENTS.md rather than from the tree, plus negative controls for reads, binary writes, `csv`-style `newline=""`, a computed mode, and prose.
