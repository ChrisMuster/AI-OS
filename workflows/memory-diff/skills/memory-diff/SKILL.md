# memory-diff - Skill Specification

**Last modified:** 2026-07-06

## Purpose
Turn the deterministic memory delta from `run.py --status` into a single honest
sentence for the session greeting, so the user opens each session knowing what
changed in `memory/` since the last one without reading the raw log.

## When to use
- At session startup, as AGENTS.md step 13: after running `run.py --status`, when
  it reports one or more memory changes.
- On demand, when the user asks "what changed in memory", "memory diff", "show
  memory changes", or similar (the registered trigger).

Do not use it when the status check reports no changes or is establishing its
first-run baseline - in those cases say nothing.

## Inputs
- The output of `python workflows/memory-diff/scripts/run.py --status` (text, or
  `--json` for structured groups): the entries added, updated, or archived since
  the watermark, already grouped as Added / Updated / Archived.

## How to run
1. At startup, run `python workflows/memory-diff/scripts/run.py --status --json`
   and read the JSON. On demand ("what changed in memory"), plain `--status` is
   fine (no ack needed).
2. If `anomaly` is true, the diff could not be computed reliably (corrupt state, a
   missing/unreadable memory log, or a lost watermark). Surface the `detail` as a
   one-line warning in the greeting and do NOT acknowledge - leave it for the user
   to resolve (they can reset with `--ack --force-baseline`).
3. If there are no changes (or `baseline` is true), stop - surface nothing.
4. If there are changes, fold one line into the opening greeting, after any
   journal, backlog, update, review, or handoff notes. Summarise faithfully from
   the Notes the script printed - do not invent detail the log does not carry.
   Keep it to a sentence: how many entries, and what they were (e.g. "Since last
   session, memory gained 2 entries: a new project note on X and an update to the
   backlog."). For a long list, summarise by group rather than reciting every line.
5. At startup only, after surfacing (and only when there was no anomaly), the
   AGENTS.md step runs `run.py --ack --through <through>` (the `through` token from
   the JSON) to advance the watermark. `--through` makes the ack refuse if the log
   moved between status and ack, so nothing is skipped. The on-demand trigger does
   not ack (it stays read-only), so a change is still surfaced at the next startup.
6. Do not paraphrase away specifics the user would want (a named memory file, a
   decision recorded); the point is a quick, accurate "here is what moved".

## Outputs
- One sentence in the greeting summarising the memory delta, or nothing when there
  is no delta. This skill writes no files.

## Verification
- `python workflows/memory-diff/scripts/run.py --status --json` returns
  `"has_changes": true` with a `count` and `groups` that match the sentence you
  wrote; every entry you mention appears in the JSON Notes. A failed check looks
  like a summary that names a change absent from the JSON, or a claimed count that
  differs from `count` - rewrite the sentence to match the data rather than
  asserting it.

## Dependencies
- `workflows/memory-diff/scripts/` - the status reader that produces the delta.
- `AGENTS.md` - the startup surfacing step (13) and the memory-diff trigger.

## Known Issues
- The summary is only as complete as `memory/LOG.md`: a hard-deleted memory file
  with no log entry will not appear (v1 scope; see the workflow CONTEXT.md).
