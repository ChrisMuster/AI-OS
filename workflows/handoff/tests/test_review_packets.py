#!/usr/bin/env python3
"""Unit tests for the handoff review-packet reader and its packet section.

The reader exists so a session boundary is never silent about a review round that
is only part-way through. The distinction most of these tests pin is None versus
[]: a packet whose shape has drifted must report as unreadable, never as zero open
findings, because "0 open" reads as "nothing left to do" and that is precisely the
failure mode the packet was created to end.
"""
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent.parent.parent
sys.path.insert(0, str(TESTS_DIR.parent / "scripts"))

import gather  # noqa: E402


class TestReviewPackets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "memory").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, lines):
        body = "\n".join(lines) + "\n"
        (self.root / "memory" / name).write_bytes(body.encode("utf-8"))

    def test_positive_control_both_finding_shapes(self):
        """Headings under Open and bold bullets under Addressed both count.

        Built from the shape the real packets use rather than from the regex, so
        this fails if the reader understands only one of the two forms.
        """
        self._write("sync_architecture_review_packet.md", [
            "# Packet",
            "## Provenance",
            "- R99 mentioned outside any counted section",
            "## Open - this round's scope",
            "### R4 - High: privacy requirement",
            "Some prose naming R4 again, which must not be counted twice.",
            "### R8 - Medium: diagram contradiction",
            "## Addressed",
            "- **R1 - Critical: git context mismatch** fixed in Tier 1.",
            "- **R2 - High: remote allowlist** fixed in Tier 2.",
        ])
        packets = gather.review_packets(self.root)
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0]["open"], ["R4", "R8"])
        self.assertEqual(packets[0]["addressed"], ["R1", "R2"])
        self.assertEqual(packets[0]["item"], "sync-architecture")
        self.assertEqual(packets[0]["path"],
                         "memory/sync_architecture_review_packet.md")

    def test_missing_open_section_is_none_not_empty(self):
        """A drifted shape reports as unreadable, never as silently zero."""
        self._write("thing_review_packet.md", [
            "# Packet", "## Findings", "### R1 - something",
        ])
        packets = gather.review_packets(self.root)
        self.assertIsNone(packets[0]["open"])
        self.assertIsNone(packets[0]["addressed"])

    def test_present_but_empty_open_section_is_empty_list(self):
        """A round with everything addressed is genuinely zero, and says so.

        The counterpart to the test above: [] and None must not collapse into one
        another in either direction.
        """
        self._write("thing_review_packet.md", [
            "# Packet", "## Open", "All closed.",
            "## Addressed", "- **R1 - done**",
        ])
        packets = gather.review_packets(self.root)
        self.assertEqual(packets[0]["open"], [])
        self.assertEqual(packets[0]["addressed"], ["R1"])

    def test_h3_does_not_reset_the_section(self):
        """A finding heading is not a new section, so R8 stays under Open."""
        self._write("thing_review_packet.md", [
            "# Packet", "## Open", "### R4 - one", "### R8 - two",
        ])
        self.assertEqual(gather.review_packets(self.root)[0]["open"],
                         ["R4", "R8"])

    def test_duplicate_ids_counted_once(self):
        self._write("thing_review_packet.md", [
            "# Packet", "## Open", "### R4 - one", "- **R4 - restated**",
        ])
        self.assertEqual(gather.review_packets(self.root)[0]["open"], ["R4"])

    def test_nested_numbered_subitems_are_not_findings(self):
        """R12 carries five numbered sub-items; they must not inflate the count."""
        self._write("thing_review_packet.md", [
            "# Packet", "## Open", "### R12 - five stale wordings",
            "1. git add -f", "2. authored text", "3. current figures",
        ])
        self.assertEqual(gather.review_packets(self.root)[0]["open"], ["R12"])

    def test_missing_memory_dir_degrades_to_empty(self):
        self.assertEqual(gather.review_packets(self.root / "nowhere"), [])

    def test_ignores_other_memory_files(self):
        """Only *_review_packet.md is a packet; a ledger with the same headings is not."""
        self._write("feedback_something.md", ["## Open", "### R1 - x"])
        self._write("thing_review_ledger.md", ["## Open", "### R2 - x"])
        self.assertEqual(gather.review_packets(self.root), [])


class TestReviewPacketsAgainstLivePacket(unittest.TestCase):
    """Positive control against the real document rather than a fixture.

    A parser tested only against fixtures its own author wrote validates its own
    definition by construction. This asserts the reader recognises the shape of the
    packet actually in use. It asserts recognition and not counts on purpose, since
    the counts move as a round is worked through and a test that tracked them would
    fail for the wrong reason.
    """

    def test_live_packets_are_recognised(self):
        packets = gather.review_packets(PROJECT_ROOT)
        if not packets:
            self.skipTest("no review packet in memory/ on this machine")
        for entry in packets:
            with self.subTest(item=entry["item"]):
                self.assertIsNotNone(
                    entry["open"],
                    "{0} has no recognised '## Open' section, so every handoff "
                    "would report it as unreadable".format(entry["path"]))
                self.assertIsNotNone(entry["addressed"], entry["path"])


class TestReviewSectionInPacket(unittest.TestCase):
    def _packet(self, **kwargs):
        base = dict(
            timestamp="2026-08-27T15:00:00+01:00", branch="main",
            status=[], diffstat="", commits=[], dir_logs=[], backlog=[],
            sessions={"total": 0, "by_ai": {}, "titles": []})
        base.update(kwargs)
        return gather.build_packet(**base)

    def test_no_packets_says_no_round_in_progress(self):
        self.assertIn("no round is part-way through", self._packet())

    def test_counts_ids_and_path_are_rendered(self):
        out = self._packet(review=[{
            "item": "sync-architecture",
            "path": "memory/sync_architecture_review_packet.md",
            "open": ["R4", "R8"], "addressed": ["R1"]}])
        self.assertIn("2 open (R4, R8), 1 addressed", out)
        self.assertIn("memory/sync_architecture_review_packet.md", out)

    def test_section_never_tells_the_ai_to_withhold_the_handoff(self):
        """Reporting, not gating. The user rejected the blocking form explicitly."""
        out = self._packet(review=[{
            "item": "thing", "path": "memory/thing_review_packet.md",
            "open": ["R4"], "addressed": []}])
        self.assertIn("never blocks a handoff", out.lower())

    def test_unrecognised_shape_is_reported_as_such(self):
        out = self._packet(review=[{
            "item": "thing", "path": "memory/thing_review_packet.md",
            "open": None, "addressed": None}])
        self.assertIn("shape not recognised", out)
        self.assertNotIn("0 open", out)


if __name__ == "__main__":
    unittest.main()
