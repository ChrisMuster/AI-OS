# Certification

**Last modified:** 2026-08-02

## Purpose
Report store for the doc-sync guard's certification sweep. Certification is the one-off baseline pass that establishes whether each documented directory's ignored content was already in sync before going-forward policing starts; its reports quote directory and file names, which for a gitignored directory can be personal (a wiki page title, a conversation subject, a journal file name). The whole directory is therefore gitignored ahead of the first report being written, with only the structural `.gitkeep` and this `CONTEXT.md` negated back into git. The store exists before its writer does: the output paths are named and proved ignored first, so no report can ever land in a trackable path.

## Contents
- `.gitkeep` - `workflows/doc-sync-guard/certification/.gitkeep` [[workflows/doc-sync-guard/certification/CONTEXT]] - Keeps the directory skeleton in git while its contents stay local.
- Certification reports, once the sweep exists: a JSON form and a Markdown rendering per run, named `<run-id>-certification.json` and `<run-id>-certification.md`, where the run id is a timestamp plus a nonce.
- Judgement records, once the sweep exists: `acceptances.jsonl` (directories accepted as unprovable) and `adjudications.jsonl`.
  Report and record files are local only and are never listed individually here; they are data, and the filesystem is the authoritative source.

## Inputs
None. Files here are produced by the certification sweep; nothing in this directory is hand-authored.

## Outputs
None directly. This is the destination for the sweep's reports and judgement records, which the change-detection baseline reads when deciding whether a directory can be baselined.

## Steps
N/A. This is a generated-output directory, not a workflow.

## Dependencies
- `workflows/doc-sync-guard/scripts/` [[workflows/doc-sync-guard/scripts/CONTEXT]] - Will hold the sweep that writes here.
- `workflows/doc-sync-guard/config/` [[workflows/doc-sync-guard/config/CONTEXT]] - The classified output inventory records every path in this directory and whether it must be ignored or tracked.
- `.gitignore` (root) - Excludes this directory's contents while negating `.gitkeep` and `CONTEXT.md` back into git.

## Known Issues
- The directory is empty apart from its structural files until the certification sweep is built. That is the intended order: the output paths are proved ignored before anything writes to them.
- The ignore rule here is load-bearing for privacy, not just tidiness. A certification report can quote names drawn from gitignored personal directories, so the `.gitignore` block covering this directory must never be removed or narrowed.

## Revision History
- 2026-08-02 - Initial creation as the certification report store, with the directory contents gitignored and `.gitkeep` plus `CONTEXT.md` negated back into git.
