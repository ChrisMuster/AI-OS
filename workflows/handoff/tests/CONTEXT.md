# Handoff - Tests

**Last modified:** 2026-08-28

## Purpose
Hermetic unit tests for the handoff gather readers and the seen-watermark logic.

## Contents
- run_tests.py - single entry point; discovers and runs every `test_*.py`.
- test_state.py - load/save, handoff-timestamp parsing (`**Created:**` line and
  mtime fallback), and the is_unread freshness logic.
- test_gather.py - changed-directory derivation, LOG tail + entry filtering,
  Active-backlog parsing, recent-session reads (temp sqlite shard), git degrade
  paths, the doc-sync drift reader's degrade-to-empty path and its WARN-only
  severity filter (a stubbed guard payload: drift alone is returned, a DEGRADED
  finding alone is not, and a degrade never masks real drift), and packet
  assembly (including the "CONTEXT/LOG drift (doc-sync)" section, clean and
  populated).
- test_review_packets.py - the `review_packets` reader and the packet's "Open
  review findings" section: both finding shapes, the absent-section `None` versus
  present-but-empty `[]` distinction, an H3 finding heading not resetting its
  section, duplicate and nested-numbered items not inflating a count, the
  degrade-to-empty paths, and a live positive control asserting the reader
  recognises the shape of the real packets in `memory/` rather than only the
  fixtures written alongside it. `TestReviewPacketToleratesAnyPacketShape` covers
  the free-form packet layouts a reviewer may write (a good / okay / bad split,
  section-name synonyms, unlabelled findings, `Open questions` as a negative
  control), and `TestUnreadableLabelsAreNeverRenderedAsZero` covers the rendering
  rule that an item the reader cannot label is still counted rather than shown as
  zero.

## Inputs
None. Tests build their own temporary fixtures (temp dirs and an in-memory-style
sqlite shard on disk).

## Outputs
Test results to stdout; exit code 0 on success, non-zero on failure.

## Steps
1. `python workflows/handoff/tests/run_tests.py`.
2. Fails the run if any test fails (used by close-out).

## Dependencies
- `workflows/handoff/scripts/` [[workflows/handoff/scripts/CONTEXT]] - the modules
  under test.
- Python standard library `unittest`, `sqlite3`, `tempfile`.

## Known Issues
- git-backed readers are only tested for the degrade-to-empty path; their happy
  path is exercised by the live smoke test rather than a fixture repo.

## Revision History
- 2026-07-03 - Initial creation. 22 tests across test_state.py and test_gather.py.
- 2026-07-07 - Added coverage for the doc-sync drift reader (degrade-to-empty) and
  the packet's "CONTEXT/LOG drift (doc-sync)" section (clean and populated). 22 ->
  24 tests (doc-sync-guard build part 5).
- 2026-08-04 - Added `TestDocSyncDriftSeverity` (3 tests) pinning the reader's
  WARN-only filter, so the boundary documented in the workflow's and the scripts
  directory's CONTEXT.md rests on a test rather than on a code read. The guard is
  stubbed at the subprocess boundary, with drift alone as the positive control.
  24 -> 27 tests.
- 2026-08-04 - The three fixture writes in `test_gather.py` converted from
  `Path.write_text(..., encoding="utf-8")` to `write_bytes`, so the suite stops
  writing CRLF fixtures on Windows and stops breaking the newline half of the
  AGENTS.md text-I/O rule. These fixtures are `LOG.md` and `backlog.md` bodies
  parsed line by line, so CRLF endings were feeding the parser a different input
  here than it sees in the real tree. `write_bytes` rather than
  `Path.write_text(newline=...)`, which is a 3.10 API against the stated 3.9
  floor. Test count unchanged at 27; no assertion touched.
- 2026-08-04 - The three fixture writes in `test_state.py` converted to
  `write_bytes` on the same reasoning (the earlier pass covered `test_gather.py`
  only). One of them is a `HANDOVER.md` body whose `**Created:**` line the
  parser reads, so a CRLF ending was feeding it a different input here than it
  sees in the real tree. Part of the pass clearing the last 50 sites
  project-wide; `encoding` became a close-out blocking label in the same change.
  Test count unchanged at 27; no assertion touched.
- 2026-08-27 - Added `test_review_packets.py` (13 tests, 27 -> 40) for the new
  `review_packets` reader and its packet section. The load-bearing pair is
  `test_missing_open_section_is_none_not_empty` and
  `test_present_but_empty_open_section_is_empty_list`, which pin `None` and `[]`
  in both directions so a drifted packet can never report as zero open findings.
  The suite also carries a live positive control,
  `TestReviewPacketsAgainstLivePacket`, running the reader over the real
  `memory/*_review_packet.md` files rather than over fixtures alone, because a
  parser tested only against fixtures its own author wrote validates its own
  definition by construction. It asserts recognition and deliberately not counts,
  since the counts move as a round is worked through, and skips cleanly on a
  machine with no packet. Four mutations were run on the way in and all four were
  caught: collapsing `None` into `[]`, letting an H3 heading reset its section,
  dropping the duplicate-id guard, and dropping the bullet form from the finding
  pattern.
- 2026-08-28 - Added `TestReviewPacketToleratesAnyPacketShape` and `TestUnreadableLabelsAreNeverRenderedAsZero`, taking the suite from 40 to 50 tests, alongside the reader change that lets a review packet be written in any shape. The regression control is built from the packet that actually broke the mechanism on 2026-08-28 rather than from the new implementation: good / okay / bad sections with findings labelled F1 to F5, which the old reader saw as zero open findings. It is deliberately paired with a control asserting that those free-form sections do not contribute to the counts, because tolerating extra sections is only safe if they are ignored rather than absorbed, and fixing an uncountable packet by inflating the count would trade one wrong number for another. The second class covers the silent half, that unlabelled findings must render their count rather than "0 open", with a counterpart control that a genuinely finished round can still say zero. Both mutations were run: reverting the label pattern to `R<n>` fires five tests, and removing the open-questions guard fires one.
