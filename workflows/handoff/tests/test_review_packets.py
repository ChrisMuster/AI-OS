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


class TestReviewPacketToleratesAnyPacketShape(unittest.TestCase):
    """The reader must survive a packet written to somebody else's taste.

    Added 2026-08-28 after the mechanism failed on first contact with an outside
    reviewer. The user asked a reviewing AI for a good / okay / bad split; it
    produced exactly that, labelled its findings F1 to F5, and the reader could not
    read the result. The requirement it had been enforcing (headings named exactly
    Open and Addressed, labels matching R<n>) was written down nowhere and lived
    only in a regex, so nobody could have complied with it.

    The rule now is the one the user stated: the packet must say whether something
    is open, and everything else is free-form. These tests are the executable form
    of that sentence.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "memory").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, lines):
        body = "\n".join(lines) + "\n"
        (self.root / "memory" / "thing_review_packet.md").write_bytes(
            body.encode("utf-8"))
        return gather.review_packets(self.root)[0]

    def test_regression_control_the_exact_shape_that_broke_it(self):
        """The 2026-08-28 packet: good / okay / bad sections and F-numbered findings.

        This is the positive control taken from the real failure rather than from
        the implementation. Before the fix the F labels were invisible to the reader
        and this reported zero open findings while five were outstanding.
        """
        packet = self._write([
            "# Packet",
            "## Overall judgement",
            "- Not build-ready yet.",
            "## Good - keep these",
            "- The stage-letter convention is consistent.",
            "- The baseline convention is correct.",
            "## Okay, but improve if touching the text",
            "- Current figures should be dated.",
            "## Open",
            "### F1 - High: selection prose names the unsafe mechanism",
            "### F2 - High: the positive control is weaker than the claim",
            "### F5 - Medium: both plans overclaim the checker",
            "## Addressed",
            "- **F3 - Medium: two failure contracts** fixed in both plans.",
        ])
        self.assertEqual(packet["open"], ["F1", "F2", "F5"])
        self.assertEqual(packet["addressed"], ["F3"])

    def test_free_form_sections_do_not_contribute_to_the_counts(self):
        """The good / okay / bad bullets above must not be read as findings.

        Paired with the test above deliberately: tolerating extra sections is only
        safe if they are ignored rather than absorbed. Without this, the fix for one
        failure (an uncountable packet) would create the other (an inflated count).
        """
        packet = self._write([
            "# Packet",
            "## Good - keep these",
            "- one", "- two", "- three",
            "## Open",
            "- **F1 - the only real finding**",
        ])
        self.assertEqual(packet["open"], ["F1"])

    def test_unlabelled_findings_are_counted_not_dropped(self):
        """A reviewer who numbers nothing still has their findings counted.

        The count is the load-bearing number; a label is a convenience. Dropping an
        unlabelled bullet would reproduce the original defect for any reviewer who
        simply writes prose bullets.
        """
        packet = self._write([
            "# Packet", "## Open",
            "- The plans overclaim what the checker proves.",
            "- The shadow plan still reads as permission.",
        ])
        self.assertEqual(len(packet["open"]), 2)
        self.assertEqual(packet["open"], [None, None])

    def test_mixed_labelled_and_unlabelled_keeps_every_item(self):
        packet = self._write([
            "# Packet", "## Open",
            "- **R1 - labelled**",
            "- unlabelled but real",
        ])
        self.assertEqual(packet["open"], ["R1", None])

    def test_section_synonyms_are_understood(self):
        """"Outstanding" and "Fixed" are natural words for these two sections."""
        packet = self._write([
            "# Packet",
            "## Outstanding", "- **F1 - still broken**",
            "## Fixed", "- **F2 - done**", "- **F3 - done**",
        ])
        self.assertEqual(packet["open"], ["F1"])
        self.assertEqual(packet["addressed"], ["F2", "F3"])

    def test_negative_control_open_questions_is_not_a_findings_section(self):
        """Not everything starting with "open" is a list of findings.

        A packet may carry open *questions*, which are questions for the user rather
        than defects to fix. Counting them as findings would overstate the round.
        """
        packet = self._write([
            "# Packet",
            "## Open questions", "- Which contract should the gate use?",
            "## Addressed", "- **F1 - done**",
        ])
        self.assertIsNone(packet["open"])
        self.assertEqual(packet["addressed"], ["F1"])

    def test_indented_sub_bullets_are_prose_not_findings(self):
        packet = self._write([
            "# Packet", "## Open",
            "- **F1 - one finding**",
            "  - a detail of it",
            "  - another detail",
        ])
        self.assertEqual(packet["open"], ["F1"])


class TestLabelsCarryingARoundNumber(unittest.TestCase):
    """Findings labelled R13-1, R13-2, ... are distinct findings.

    Added 2026-09-18, after a real packet of twelve addressed findings reported
    one. The label pattern stopped at the first run of digits, so every finding in
    a round labelled this way carried the label of the round itself and
    de-duplication collapsed them. The dangerous direction is the open list: nine
    open findings would have rendered as "1 open", which reads as almost nothing
    left to do.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "memory").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, lines):
        body = "\n".join(lines) + "\n"
        (self.root / "memory" / "thing_review_packet.md").write_bytes(
            body.encode("utf-8"))
        return gather.review_packets(self.root)[0]

    def test_regression_control_the_real_packet_shape(self):
        """Taken from the packet that miscounted, not from the new pattern."""
        packet = self._write([
            "# Packet",
            "## Open",
            "- **R13-1, BLOCKER, A9 false negative: node inline flags.**",
            "- **R13-2, MAJOR, A9 false positive: flags after a script.**",
            "- **R13-9, BOUNDARY, A3 false positive: stack positions.**",
            "## Addressed",
            "- **R13-10, A9 false negative: an option value read as a script.**",
            "- **R13-11, A9 false positive: node -r names a module.**",
        ])
        self.assertEqual(packet["open"], ["R13-1", "R13-2", "R13-9"])
        self.assertEqual(packet["addressed"], ["R13-10", "R13-11"])

    def test_a_round_label_and_a_finding_label_are_different(self):
        """`R13` and `R13-1` must not collapse into one another."""
        packet = self._write([
            "# Packet", "## Open",
            "- **R13 - the round as a whole**",
            "- **R13-1 - one finding in it**",
        ])
        self.assertEqual(packet["open"], ["R13", "R13-1"])

    def test_a_repeated_finding_label_is_still_counted_once(self):
        """The de-duplication itself must survive the fix."""
        packet = self._write([
            "# Packet", "## Open",
            "### R13-1 - one finding",
            "- **R13-1 - restated in the body**",
        ])
        self.assertEqual(packet["open"], ["R13-1"])

    def test_control_a_spaced_dash_is_prose_not_part_of_the_label(self):
        """`R1 - description` labels R1; only an attached `-N` joins the label.

        Without this the widened pattern could swallow the description of every
        finding written in the older style, which is most of them.
        """
        packet = self._write([
            "# Packet", "## Open",
            "- **R1 - 2 of the plans disagree**",
            "- **F3.2 - a dotted sub-number also reads**",
        ])
        self.assertEqual(packet["open"], ["R1", "F3.2"])


class TestALabelIsTakenWholeOrNotAtAll(unittest.TestCase):
    """A label the reader cannot accept must cost the label, never the finding.

    Added 2026-09-18 as R14-3. The round-number fix above stopped the pattern
    truncating at the first run of digits, but `\\b` still let it give ground: with
    a suffix it could not accept attached, it backtracked to whatever shorter
    prefix ended on a word boundary, so `R13-1a` and `R13-2a` both matched as
    `R13` and de-duplication merged two findings into one. That is the same
    collapse, through the one route the earlier fix left open, and it fails in the
    dangerous direction: the finding leaves the count rather than merely losing
    its label.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "memory").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, lines):
        body = "\n".join(lines) + "\n"
        (self.root / "memory" / "thing_review_packet.md").write_bytes(
            body.encode("utf-8"))
        return gather.review_packets(self.root)[0]

    def test_regression_control_alpha_suffixes_do_not_collapse(self):
        """The reported shape: two findings, two items, not one."""
        packet = self._write([
            "# Packet", "## Open",
            "- **R13-1a - the first sublabelled finding**",
            "- **R13-2a - the second sublabelled finding**",
        ])
        self.assertEqual(len(packet["open"]), 2)
        self.assertNotIn("R13", packet["open"])

    def test_regression_control_slash_suffixes_do_not_collapse(self):
        """The same backtrack reached through `/` rather than a letter."""
        packet = self._write([
            "# Packet", "## Open",
            "- **R13/1 - the first finding**",
            "- **R13/2 - the second finding**",
        ])
        self.assertEqual(len(packet["open"]), 2)
        self.assertNotIn("R13", packet["open"])

    def test_a_refused_label_is_reported_rather_than_swallowed(self):
        """The count is kept whole, and the bad spelling is named."""
        packet = self._write([
            "# Packet", "## Open",
            "- **R13-1a - a sublabelled finding**",
        ])
        self.assertEqual(packet["labels_noncanonical"], ["R13-1a"])

    def test_a_readable_but_non_canonical_label_is_still_counted(self):
        """A complaint must never cost a finding its label or its place.

        The dotted form reads fine and is merely not the agreed spelling, so it
        is counted, labelled, and complained about, all three.
        """
        packet = self._write([
            "# Packet", "## Open",
            "- **F3.2 - a dotted sub-number**",
        ])
        self.assertEqual(packet["open"], ["F3.2"])
        self.assertEqual(packet["labels_noncanonical"], ["F3.2"])

    def test_control_the_canonical_forms_raise_nothing(self):
        """Both accepted spellings, in both sections, and a bolded one."""
        packet = self._write([
            "# Packet", "## Open",
            "- **R14-1 - a round-scoped finding**",
            "- **D9 - an item-scoped running sequence**",
            "## Addressed",
            "- **BUG12 - letters and digits, no group**",
        ])
        self.assertEqual(packet["open"], ["R14-1", "D9"])
        self.assertEqual(packet["addressed"], ["BUG12"])
        self.assertEqual(packet["labels_noncanonical"], [])

    def test_control_an_unlabelled_finding_is_not_a_complaint(self):
        """No label at all is allowed. Only a broken one is worth reporting."""
        packet = self._write([
            "# Packet", "## Open",
            "- the reviewer wrote no label here",
            "- nor here",
        ])
        self.assertEqual(packet["open"], [None, None])
        self.assertEqual(packet["labels_noncanonical"], [])

    def test_control_a_spaced_dash_still_labels_and_raises_nothing(self):
        """`R1 - description` is prose after a canonical label, not a complaint."""
        packet = self._write([
            "# Packet", "## Open",
            "- **R1 - 2 of the plans disagree**",
        ])
        self.assertEqual(packet["open"], ["R1"])
        self.assertEqual(packet["labels_noncanonical"], [])

    def test_a_complaint_is_never_raised_about_a_discarded_bullet(self):
        """Headings win over bullets, so the complaint set must follow them.

        Otherwise the warning names findings that were never counted, which sends
        a reader looking for an item the packet section does not show.
        """
        packet = self._write([
            "# Packet", "## Open",
            "### R14-1 - the finding, as a heading",
            "- **F3.2 - a bullet in its body, discarded by the count**",
        ])
        self.assertEqual(packet["open"], ["R14-1"])
        self.assertEqual(packet["labels_noncanonical"], [])


class TestNonCanonicalLabelsAreRendered(unittest.TestCase):
    """The complaint has to reach the packet, or the standard decays again.

    The user's call on 2026-09-18: a non-canonical label should not be there at
    all, so when one appears he wants to be told rather than have it quietly
    tolerated.
    """

    def _packet(self, **kwargs):
        base = dict(
            timestamp="2026-09-18T11:00:00+01:00", branch="main",
            status=[], diffstat="", commits=[], dir_logs=[], backlog=[],
            sessions={"total": 0, "by_ai": {}, "titles": []})
        base.update(kwargs)
        return gather.build_packet(**base)

    def test_the_offending_tokens_are_named(self):
        out = self._packet(review=[{
            "item": "thing", "path": "memory/thing_review_packet.md",
            "open": ["R14-1"], "addressed": [],
            "labels_noncanonical": ["R13-1a", "F3.2"]}])
        self.assertIn("Non-canonical finding labels", out)
        self.assertIn("`R13-1a`", out)
        self.assertIn("`F3.2`", out)

    def test_it_is_raised_even_when_the_open_section_is_unreadable(self):
        """The drifted packet is the one most worth complaining about."""
        out = self._packet(review=[{
            "item": "thing", "path": "memory/thing_review_packet.md",
            "open": None, "addressed": None,
            "labels_noncanonical": ["R13-1a"]}])
        self.assertIn("shape not recognised", out)
        self.assertIn("Non-canonical finding labels", out)

    def test_control_a_clean_packet_says_nothing_about_labels(self):
        out = self._packet(review=[{
            "item": "thing", "path": "memory/thing_review_packet.md",
            "open": ["R14-1"], "addressed": [], "labels_noncanonical": []}])
        self.assertNotIn("Non-canonical", out)

    def test_control_an_entry_with_no_such_key_is_not_a_crash(self):
        """Every other caller and test builds the entry without the key."""
        out = self._packet(review=[{
            "item": "thing", "path": "memory/thing_review_packet.md",
            "open": ["R14-1"], "addressed": []}])
        self.assertIn("1 open", out)
        self.assertNotIn("Non-canonical", out)


class TestUnreadableLabelsAreNeverRenderedAsZero(unittest.TestCase):
    """The silent half of the defect, which is the more dangerous half.

    A missing section already reported "shape not recognised", which is loud and
    correct. A section that was present but whose labels the reader did not
    understand reported "0 open", which is silent and confidently wrong: it reads
    as "nothing left to do" at exactly the moment work is outstanding.
    """

    def _packet(self, **kwargs):
        base = dict(
            timestamp="2026-08-28T11:00:00+01:00", branch="main",
            status=[], diffstat="", commits=[], dir_logs=[], backlog=[],
            sessions={"total": 0, "by_ai": {}, "titles": []})
        base.update(kwargs)
        return gather.build_packet(**base)

    def _render(self, open_items):
        return self._packet(review=[{
            "item": "thing", "path": "memory/thing_review_packet.md",
            "open": open_items, "addressed": []}])

    def test_unlabelled_findings_render_their_count_not_zero(self):
        out = self._render([None, None, None])
        self.assertIn("3 open", out)
        self.assertNotIn("0 open", out)

    def test_mixed_render_names_both_halves(self):
        out = self._render(["R1", None])
        self.assertIn("2 open", out)
        self.assertIn("1 labelled: R1", out)
        self.assertIn("1 unlabelled", out)

    def test_genuinely_empty_still_renders_zero(self):
        """The counterpart control: a finished round must still be able to say zero."""
        out = self._render([])
        self.assertIn("0 open", out)


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
