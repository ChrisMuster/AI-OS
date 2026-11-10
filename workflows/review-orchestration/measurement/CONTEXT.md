# Review Orchestration - Measurement

**Last modified:** 2026-11-10

## Purpose
The fixtures of the measurement harness (plan 10.8, A5): the replay set and the planted set, each defined before anything runs. They live here, in the harness's own files, outside every restored tree, so no reviewer under measurement can read them from its working folder. `workflows/review-orchestration/scripts/measure.py` [[workflows/review-orchestration/scripts/CONTEXT]] reads them from its trusted copy, so a measurement uses the fixtures of the commit it started from.

## Contents
A flat folder with no subfolders: besides this file and LOG.md it holds exactly eight fixtures, all tracked and none matching a design-time ignore pattern.
- replays.json - `workflows/review-orchestration/measurement/replays.json` - The two replay rounds of section 11 item 4. `note` is the one fixed note put before each round's own brief. `rounds.code` and `rounds.plan` each hold `title`, `public_ref`, `personal_ref`, `brief` (the code round's `path` in the restored tree; the plan round's personal `ref`, `path` and `sha256`), `answer_labels` (the answer-key labels of its recorded findings), `review_process` (those reported apart), `original_could_run` and `drift` (reported beside the round). The code round also holds `new_files` (the four scripts and their four tests, left out of its baseline commit so they are new), `required_plans` (the two plans its reply must list in `files_reviewed`) and `plan_slot` (the text of its `$plan` slot naming them); the plan round holds `plan_path`, the plan under review.
- planted-<kind>.json - `workflows/review-orchestration/measurement/` - One file per planted change, named by its kind: `logic-error`, `missed-reference`, `stale-document`, `scope-breach`, `metadata-drift`, `wrong-test` and `control`. Each holds exactly four fields. `brief` is the change's short run brief, as text in the seven-section form `brief.py` checks. `definition` is null for the control, and otherwise holds `file`, `catch` (what a finding must identify) and `check`, null or the expected mechanical check as `prefix` (a finding title's prefix, such as `doc-sync:`) and `file` (the finding's `file`; for doc-sync the directory). `patch` is a list of operations the harness applies: `replace` (exact search text that must occur once, its replacement, and `base_blob`, the git blob id of the target at the fixed public ref, or the all-zero placeholder until it has been validated there; see Known Issues), `new` (a file absent at the ref, with its text) and `append` (text added to the end of a file, the only operation on a LOG.md, its `{{TIMESTAMP}}` filled with the time it is applied). An operation may carry `defect`, text the result must hold. `acceptance` lists the change's acceptance commands, run in the container.

## Inputs
None. The fixtures are data, written by hand and checked by the user before any measurement (plan 10.8 item 2).

## Outputs
None. The harness reads them; its results go to `workflows/review-orchestration/runs/measure-<id>/`.

## Steps
1. The user checks every brief, definition and patch for whether the change is believable, gives nothing away and is defined tightly enough, before any measurement. A changed fixture's `base_blob` values are written again from the target's blob id at the public ref (Known Issues).
2. `measure.py` checks each fixture's shape at its preflight, and before any session confirms every replace target's blob id against `base_blob`, every appended file present at the personal ref, and every patch applying, refusing on any mismatch.
3. Append LOG.md with a completion or failure entry when a fixture changes.

## Dependencies
- `workflows/review-orchestration/scripts/measure.py` [[workflows/review-orchestration/scripts/CONTEXT]] - the one reader of these files; its `KINDS`, `PLANTED_PUBLIC` and `PLANTED_PERSONAL` must match them.
- `workflows/review-orchestration/scripts/brief.py` [[workflows/review-orchestration/scripts/CONTEXT]] - each planted brief must pass its checker; the harness reads the brief's edit paths with `brief_edit_paths`.
- The public ref `65d9c6249eea918afa548fa3c8c60541654c5b10` and the personal ref `58e97f604380d4a685fbf76b85d507c7c1812656` (plan 10.8 item 2): every patch is written against the files there, and the replay refs are those of section 11 item 4.
- The project's `.gitignore`: no fixture name may match a design-time pattern, or it would never be published.
- `workflows/review-orchestration/tests/test_measure.py` [[workflows/review-orchestration/tests/CONTEXT]] - the fixture tests.

## Known Issues
- **The patches were validated against the fixed refs by hand, after the build run.** The build run that wrote them had no git, so it left every `replace` operation's `base_blob` as the all-zero placeholder (`measure.UNVALIDATED_BLOB`), which `measure.py`'s pre-session check refuses by name. On 2026-11-10 each was filled in by hand from `git rev-parse 65d9c62:<path>`, after confirming that the bytes hash to that id both through `git show` and through `git archive` (the harness's restore route), that every patch applies to the ref's files with `measure.apply_ops` (each search text found exactly once, each new file absent, each appended LOG.md present at the personal ref), and that `git check-ignore` ignores none of the eight fixtures. A fixture edited later needs the same validation again, or the harness refuses on the blob mismatch or the failed apply.
- Each planted kind has one change, so each result is a single reading, not a rate.
- A brief held as a JSON string is harder to read than a Markdown file; the user reads them in one sitting before a measurement.
- A change to a file the patches touch, at the fixed refs, cannot happen (the refs are fixed); a change to the refs themselves needs every `base_blob` and search text written again.

## Revision History
- 2026-11-09 - Initial creation (A5, plan 10.8): `replays.json` and the seven `planted-<kind>.json` fixtures.
- 2026-11-10 - The build-time validation against the fixed public ref could not be made in the build run (no git; the check-container route declined at the user's direction), so every `replace` operation's `base_blob` is the placeholder the harness refuses by name, and Known Issues and Steps say how to fill it in; the stale-document fixture's optional `defect` field removed after the AI-style guard read its escaped en dash as one.
- 2026-11-10 - Every placeholder `base_blob` replaced with the target's blob id at public ref `65d9c62`, after each patch was validated against the fixed refs by hand (review finding R6-2); Known Issues and Steps updated to match.
