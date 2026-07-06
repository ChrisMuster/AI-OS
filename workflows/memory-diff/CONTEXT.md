# Memory Diff

**Last modified:** 2026-07-06

## Purpose
Show what changed in `memory/` since the last session. At session startup a
read-only check reports any memory entries added, updated, or archived since the
diff was last surfaced, folded into the greeting only when there is something to
report; the same check answers the "what changed in memory" trigger on demand.
This is best-practices umbrella Bucket-1 child #5 (the OpenClaw "Memory Diff"
skill, Skill 91).

Like the weekly-review and handoff workflows there are no per-AI slash commands.
The work is a deterministic reader (the ~90%) plus a one-line AI summary (the
~10%), triggered by natural language documented in `AGENTS.md` [[AGENTS]], so it
works identically on every AGENTS-reading AI with no per-AI adapter.

## Contents
- scripts/ - `workflows/memory-diff/scripts/` [[workflows/memory-diff/scripts/CONTEXT]]
  - run.py (status / ack entry point), diff.py (reads and categorises
    `memory/LOG.md`), state.py (the content watermark), config.py (tunable
    constants).
- skills/ - `workflows/memory-diff/skills/` [[workflows/memory-diff/skills/CONTEXT]]
  - the memory-diff skill (the AI half: folds the delta into the greeting).
- tests/ - `workflows/memory-diff/tests/` [[workflows/memory-diff/tests/CONTEXT]]
  - hermetic unit tests for the reader/categoriser and the watermark logic.

## Inputs
- `memory/LOG.md` [[memory/CONTEXT]] - the append-only memory audit trail, read by
  the diff. No user flags are required for the startup check.
- Biblio, to fold a one-line summary of the delta into the greeting (see the skill).

## Outputs
- A grouped list of the memory changes since the watermark (stdout), or a "no
  changes" line. `--status --json` emits the same as machine-readable JSON, plus a
  `through` token (used by the ack handshake) and an `anomaly` flag.
- On an anomaly (corrupt state, missing/unreadable memory log, or a watermark that
  has vanished from the log) a loud WARNING and a non-zero exit (2); nothing is
  acknowledged, so no span of changes is silently skipped.
- `state.json` - the content watermark (the raw text of the last memory/LOG.md
  entry surfaced), advanced by `--ack` (gitignored, machine-local).

## Steps
1. **Startup (read-only):** the AGENTS.md startup sequence runs
   `run.py --status --json`. It reads `memory/LOG.md`, computes the entries after
   the watermark, and reports them grouped as Added / Updated / Archived, with a
   `through` token and an `anomaly` flag.
2. **Surface:** if `anomaly` is set, Biblio folds the warning into the greeting and
   does not acknowledge (the user resolves it, then resets with
   `--ack --force-baseline`). If there are changes, Biblio folds a one-line summary
   into the greeting. If there are none (or this is the first-run baseline), it
   says nothing.
3. **Acknowledge:** unless there was an anomaly, the startup sequence then runs
   `run.py --ack --through <through>`, advancing the watermark to the newest entry
   so the same changes are not surfaced again. `--through` makes the ack refuse to
   advance if `memory/LOG.md` changed between the status check and the ack, so a
   change appended in between is surfaced next time rather than skipped.
4. **On demand:** the "what changed in memory" trigger runs `run.py --status`
   (read-only, does not advance the watermark).
5. Append LOG.md with a completion or failure entry.

## Dependencies
- `workflows/memory-diff/scripts/` [[workflows/memory-diff/scripts/CONTEXT]] - the
  status / ack entry point and its modules.
- `workflows/memory-diff/skills/memory-diff/` [[workflows/memory-diff/skills/memory-diff/CONTEXT]] -
  the skill this workflow relies on to fold the delta into the greeting.
- `memory/LOG.md` [[memory/CONTEXT]] - the sole signal source; the diff is a view
  over it.
- `AGENTS.md` [[AGENTS]] - hosts the startup surfacing step and the memory-diff
  trigger.
- `workflows/triggers/` [[workflows/triggers/CONTEXT]] - registers the on-demand
  "what changed in memory" phrase.

## Known Issues
- **Removals are not a dedicated group (v1 scope).** A hard-deleted memory file
  has no standard log action; by the append-only rule a removal leaves a
  `modified` or `archived` note, so it surfaces under Archived/Updated rather than
  a "Removed" group. Detecting a truly deleted file (e.g. by diffing `MEMORY.md`)
  is a deliberate follow-up.
- **A lost watermark is an anomaly, not a silent re-baseline.** If the stored
  watermark line is edited out of `memory/LOG.md` (against the append-only rule) or
  the log is rotated, the diff cannot prove what changed. It reports this loudly
  (WARNING, non-zero exit) and refuses to acknowledge rather than silently
  advancing past a span of changes. The same applies to a corrupt/unreadable state
  file, a present-but-invalid state file (a JSON object with no usable `seen_line`
  watermark, e.g. `{}`), and a missing/unreadable memory log. Only a genuine first
  run (no state file at all) baselines silently. Resetting past an anomaly is a
  deliberate manual act: `run.py --ack --force-baseline`.
- The trigger is natural language, so on-demand recognition is softer than a
  slash command; the startup check is script-backed and reliable.

## Revision History
- 2026-07-06 - Initial creation. Content-watermark diff over `memory/LOG.md`
  (run/diff/state/config), Added/Updated/Archived categorisation, the AGENTS.md
  startup surfacing step, the on-demand trigger, and a hermetic test suite.
  Best-practices umbrella Bucket-1 child #5 (memory diff).
- 2026-07-06 - Codex cross-AI review fixes. Failure handling made loud: corrupt
  state, a missing/unreadable memory log, and a lost watermark now warn and exit
  non-zero instead of silently re-baselining, and `--ack` refuses to advance past
  them without `--force-baseline`. Added the status/ack race guard (`--status
  --json` emits a `through` token; `--ack --through` refuses if the log moved).
  AGENTS.md step 13 rewired to the JSON status + `--through` ack. Added run.py
  integration tests (test_run.py) and MEMORY_DIFF_* path overrides for hermetic
  testing; suite now 40 tests.
- 2026-07-06 - Codex second-review fix: `load_state` now validates the shape of an
  existing state file, not just its JSON syntax. A present-but-invalid state file
  (a JSON object with a missing, non-string, or empty/whitespace `seen_line`, e.g.
  `{}`) raises `StateError("malformed")` and routes to the loud anomaly path
  instead of falling through to a silent first-run re-baseline. Only a missing
  state file is a genuine first run. Added 5 unit tests and 3 run.py integration
  tests; suite now 48.
