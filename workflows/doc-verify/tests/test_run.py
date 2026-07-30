#!/usr/bin/env python3
"""Hermetic tests for doc-verify.

Every check carries the three controls the verification protocol requires, and the
taxonomy is the point of this suite. All three are stated against **the subject** -
the thing the check is about - because that is the only phrasing that survives the
split below:

    positive control  a LEGAL instance of the subject, constructed by reading the
                      spec rather than by copying an example out of a document.
                      This is the control that catches a checker whose definition
                      is too narrow: the failure that produced a wrong table count
                      and propagated it to five surfaces before a reviewer caught
                      it.
    rejection control an ILLEGAL instance of the subject, which the checker must
                      refuse. It proves the refusal path fires and says nothing
                      about whether the definition is right.
    negative control  something that is NOT AN INSTANCE of the subject at all, so
                      a clean result is known not to be silence from over-matching:
                      a clock time is not a citation, two tables each numbered from
                      1 are not duplicates of each other.

**A counting checker and a validating checker read "positive control" oppositely,
and conflating them is how an illegal instance gets filed as a positive one.**

    counting     ``find_tables``, ``check_distance``, ``find_citations``. The
                 subject is the thing being counted, so a legal instance must be
                 FOUND: a two-space-indented separator row is a table; "the rule
                 two bullets down" is a distance reference.
    validating   ``check_hygiene``, ``check_tables``, ``check_sequences``,
                 ``check_heading_order``, ``check_citations``. The subject is a
                 well-formed document, so a legal instance must produce NOTHING:
                 a clean LF file, a uniform table, a contiguous ascending sequence,
                 a citation whose range sits inside a real file. A CRLF file or a
                 table numbered 1, 3, 2 is an ILLEGAL instance and belongs under
                 ``rejection_control``, however much "the checker found it" reads
                 like a positive result.

**A counting checker that refuses nothing has no rejection control**, and that is a
property of the check rather than a gap in this file. ``check_distance`` reports
INFO candidates and never issues a verdict, so there is no refusal path to prove;
``DistanceTests`` therefore carries a positive and a negative control and no third.
Inventing one would mean asserting a refusal the checker is specified not to make.

No git, no network, no dependence on any real document.

    python workflows/doc-verify/tests/test_run.py
"""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run.py"


def load_run():
    spec = importlib.util.spec_from_file_location("doc_verify_run", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run = load_run()


def lines_of(text):
    return run.blank_fenced_lines(text.split("\n"))


def tables_of(text):
    return run.find_tables(lines_of(text))


class HygieneTests(unittest.TestCase):
    """``check_hygiene`` VALIDATES, so its positive control is a clean file."""

    def test_positive_control_clean_ascii_lf_file_passes(self):
        # POSITIVE CONTROL: a legal instance of the subject - an ASCII, LF, newline
        # terminated file - which a validating checker must pass in silence. Every
        # test below feeds an ILLEGAL instance and is therefore a rejection control,
        # however much "the checker found the thing" reads like a positive result.
        self.assertEqual(run.check_hygiene(b"# Title\n\nClean prose.\n"), [])

    def test_rejection_control_crlf_file_is_flagged(self):
        # The defect that prompted this check: a tool writing the file in text mode
        # on Windows converts every line ending to CRLF. A checker that decodes
        # before looking cannot see it and reports clean.
        findings = run.check_hygiene(b"# T\r\nprose\r\n")
        self.assertTrue(any("CR byte" in m for _, m in findings))

    def test_rejection_control_tab_is_flagged(self):
        findings = run.check_hygiene(b"# T\n\tindented\n")
        self.assertTrue(any("tab character" in m for _, m in findings))

    def test_rejection_control_trailing_whitespace_is_flagged(self):
        findings = run.check_hygiene(b"# T\nprose   \n")
        self.assertTrue(any("trailing whitespace" in m for _, m in findings))

    def test_rejection_control_missing_final_newline_is_flagged(self):
        findings = run.check_hygiene(b"# T\nprose")
        self.assertTrue(any("no final newline" in m for _, m in findings))

    def test_rejection_control_em_dash_is_flagged_by_codepoint(self):
        # Built with an escape so this test file stays pure ASCII and cannot flag
        # itself, matching the convention encoding-guard uses.
        data = ("# T\nan em dash " + chr(0x2014) + " here\n").encode("utf-8")
        findings = run.check_hygiene(data)
        self.assertTrue(any("U+2014" in m for _, m in findings))

    def test_rejection_control_non_breaking_space_is_flagged(self):
        data = ("# T\ntwo" + chr(0x00A0) + "words\n").encode("utf-8")
        findings = run.check_hygiene(data)
        self.assertTrue(any("U+00A0" in m for _, m in findings))

    def test_negative_control_blank_lines_are_not_trailing_whitespace(self):
        self.assertEqual(run.check_hygiene(b"# T\n\nprose\n\n"), [])

    def test_whitespace_only_line_is_trailing_whitespace(self):
        # An empty line has nothing to strip; a line of spaces does, and the two
        # must not be conflated - the guard that skipped "blank" lines was hiding
        # this one.
        findings = run.check_hygiene(b"# T\n   \nprose\n")
        self.assertTrue(any("line 2: trailing whitespace" in m
                            for _, m in findings))

    def test_crlf_does_not_double_report_every_line_as_trailing_whitespace(self):
        # A single trailing CR is the line-ending artefact, reported once by the CR
        # check. Reporting it again per line buries the real finding.
        findings = run.check_hygiene(b"# T\r\nprose\r\nmore\r\n")
        self.assertEqual(
            [m for _, m in findings if "trailing whitespace" in m], [])
        self.assertEqual(len([m for _, m in findings if "CR byte" in m]), 1)

    def test_real_trailing_space_still_found_in_a_crlf_file(self):
        findings = run.check_hygiene(b"# T\r\nprose  \r\n")
        self.assertTrue(any("line 2: trailing whitespace" in m
                            for _, m in findings))

    def test_rejection_control_invalid_utf8(self):
        findings = run.check_hygiene(b"# T\n\xff\xfe bad\n")
        self.assertTrue(any("not valid UTF-8" in m for _, m in findings))

    def test_empty_file_reports_nothing(self):
        self.assertEqual(run.check_hygiene(b""), [])

    def test_hygiene_without_bytes_fails_loudly(self):
        # A silent skip would make the check pass by not running, which is the
        # failure shape this whole workflow exists to prevent.
        findings, _ = run.check_document("# T\n", ".", only=["hygiene"])
        self.assertTrue(any("requires the raw bytes" in m for _, m in findings))

    def test_check_file_reads_binary_so_crlf_survives_to_the_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "crlf.md"
            doc.write_bytes(b"# T\r\nprose\r\n")
            findings, _ = run.check_file(doc, tmp, only=["hygiene"])
            self.assertTrue(any("CR byte" in m for _, m in findings))


class FenceTests(unittest.TestCase):
    def test_fenced_content_blanked_and_line_numbers_preserved(self):
        text = "a\n```\nb\n```\nc"
        self.assertEqual(lines_of(text), ["a", "", "", "", "c"])

    def test_tilde_fence_blanked(self):
        text = "a\n~~~\nb\n~~~\nc"
        self.assertEqual(lines_of(text), ["a", "", "", "", "c"])

    def test_unterminated_fence_blanks_to_end_of_file(self):
        text = "a\n```\nb\nc"
        self.assertEqual(lines_of(text), ["a", "", "", ""])

    def test_backtick_fence_not_closed_by_tilde(self):
        # A different marker must not close the fence, or content after it is
        # treated as prose and re-enters every check.
        text = "a\n```\nb\n~~~\nc\n```\nd"
        self.assertEqual(lines_of(text), ["a", "", "", "", "", "", "d"])


class TableDetectionTests(unittest.TestCase):
    def test_positive_control_two_space_indented_separator_is_a_table(self):
        # POSITIVE CONTROL, from the CommonMark spec: up to three leading spaces
        # are permitted before a table row. A checker anchored at column 1 misses
        # this and undercounts, which is the exact defect this control exists for.
        text = "  | A | B |\n  |---|---|\n  | 1 | 2 |"
        tables = tables_of(text)
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["width"], 2)
        self.assertEqual(len(tables[0]["body"]), 1)

    def test_positive_control_three_space_indent_is_the_boundary(self):
        text = "   | A | B |\n   |---|---|\n   | 1 | 2 |"
        self.assertEqual(len(tables_of(text)), 1)

    def test_rejection_control_four_space_indent_is_not_a_table(self):
        # REJECTION CONTROL: four spaces is an indented code block per the spec.
        text = "    | A | B |\n    |---|---|\n    | 1 | 2 |"
        self.assertEqual(tables_of(text), [])

    def test_negative_control_hyphen_rule_without_pipes_is_not_a_table(self):
        self.assertEqual(tables_of("A\n---\ntext"), [])

    def test_negative_control_separator_inside_fence_is_not_a_table(self):
        text = "```\n| A | B |\n|---|---|\n| 1 | 2 |\n```"
        self.assertEqual(tables_of(text), [])

    def test_alignment_colons_accepted(self):
        text = "| A | B |\n|:--|--:|\n| 1 | 2 |"
        self.assertEqual(len(tables_of(text)), 1)

    def test_body_stops_at_first_non_row(self):
        text = "| A |\n|---|\n| 1 |\n\nprose\n| B |\n|---|\n| 2 |"
        tables = tables_of(text)
        self.assertEqual(len(tables), 2)
        self.assertEqual(len(tables[0]["body"]), 1)


class CellCountTests(unittest.TestCase):
    def test_leading_and_trailing_pipes_do_not_count_as_cells(self):
        self.assertEqual(run.cell_count("| a | b | c |"), 3)

    def test_escaped_pipe_inside_a_cell_is_not_a_boundary(self):
        self.assertEqual(run.cell_count(r"| a \| b | c |"), 2)

    def test_missing_trailing_pipe_still_counts(self):
        self.assertEqual(run.cell_count("| a | b"), 2)


class TableUniformityTests(unittest.TestCase):
    """``check_tables`` VALIDATES: a uniform table is the positive control."""

    def check(self, text):
        lines = lines_of(text)
        return run.check_tables(lines, run.find_tables(lines))

    def test_positive_control_uniform_table_passes(self):
        # POSITIVE CONTROL: a legal table, which a validator must pass in silence.
        self.assertEqual(self.check("| A | B |\n|---|---|\n| 1 | 2 |"), [])

    def test_rejection_control_ragged_body_row_flagged_with_its_line_number(self):
        findings = self.check("| A | B |\n|---|---|\n| 1 | 2 |\n| 3 |")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][0], "FAIL")
        self.assertIn("line 4", findings[0][1])

    def test_rejection_control_ragged_header_flagged(self):
        findings = self.check("| A |\n|---|---|\n| 1 | 2 |")
        self.assertTrue(any("header row has 1 cells" in m for _, m in findings))

    def test_rejection_control_separator_with_no_header_flagged(self):
        findings = self.check("|---|---|\n| 1 | 2 |")
        self.assertEqual(len(findings), 1)
        self.assertIn("no header row above it", findings[0][1])


class SequenceTests(unittest.TestCase):
    """``check_sequences`` VALIDATES: a contiguous, ascending, repeat-free table is
    the positive control, and every table carrying a defect is a rejection control."""

    def check(self, text):
        return run.check_sequences(tables_of(text))

    def test_positive_control_contiguous_sequence_passes(self):
        # POSITIVE CONTROL: a legal enumerated table, which must produce nothing.
        text = ("| # | X |\n|---|---|\n| 1 | a |\n| 2 | b |\n| 3 | c |")
        self.assertEqual(self.check(text), [])

    def test_rejection_control_gap_is_reported(self):
        text = ("| # | X |\n|---|---|\n| 1 | a |\n| 2 | b |\n| 4 | d |")
        findings = self.check(text)
        self.assertEqual(len(findings), 1)
        self.assertIn("missing 3", findings[0][1])

    def test_rejection_control_duplicate_enumerator_flagged(self):
        text = ("| # | X |\n|---|---|\n| 1 | a |\n| 2 | b |\n| 2 | c |\n| 3 | d |")
        findings = self.check(text)
        self.assertTrue(any("duplicate enumerator `2`" in m for _, m in findings))

    def test_prefixed_sequence_supported(self):
        text = ("| ID | X |\n|---|---|\n| E10 | a |\n| E11 | b |\n| E13 | c |")
        findings = self.check(text)
        self.assertTrue(any("missing E12" in m for _, m in findings))

    def test_lettered_sub_item_does_not_create_a_false_gap(self):
        text = ("| # | X |\n|---|---|\n| 12 | a |\n| 13 | b |\n| 13a | c |\n"
                "| 14 | d |")
        self.assertEqual(self.check(text), [])

    def test_negative_control_two_tables_each_starting_at_one(self):
        # NEGATIVE CONTROL for the pooling defect: counted per table, two
        # independent sequences are not duplicates of each other.
        text = ("| # | X |\n|---|---|\n| 1 | a |\n| 2 | b |\n| 3 | c |\n\n"
                "| # | Y |\n|---|---|\n| 1 | d |\n| 2 | e |\n| 3 | f |")
        self.assertEqual(self.check(text), [])

    def test_negative_control_non_enumerated_table_ignored(self):
        text = ("| Column | Question |\n|---|---|\n| Assertion | why |\n"
                "| Seed | who |\n| Doc-sync | which |")
        self.assertEqual(self.check(text), [])

    def test_negative_control_mixed_first_column_ignored(self):
        text = ("| # | X |\n|---|---|\n| 1 | a |\n| prose | b |\n| 3 | c |")
        self.assertEqual(self.check(text), [])

    def test_negative_control_below_row_threshold_ignored(self):
        text = "| # | X |\n|---|---|\n| 1 | a |\n| 3 | c |"
        self.assertEqual(self.check(text), [])

    def test_rejection_control_out_of_order_row(self):
        # The exact case gaps and duplicates cannot see: 1, 3, 2 is complete and has
        # no repeat, so it passed silently until order was checked. A rejection
        # control rather than a positive one, because the input is illegal.
        text = ("| # | X |\n|---|---|\n| 1 | a |\n| 3 | c |\n| 2 | b |")
        findings = self.check(text)
        self.assertEqual(len(findings), 1)
        self.assertIn("out of order", findings[0][1])
        self.assertIn("`2` appears after `3`", findings[0][1])

    def test_one_misplaced_row_reports_once_not_once_per_later_row(self):
        # The value just read, not a high-water mark. 1, 5, 2, 3, 4 is ONE row in
        # the wrong place; a high-water mark blames 2, 3 and 4 in turn and buries
        # the defect under its own consequences.
        text = ("| # | X |\n|---|---|\n| 1 | a |\n| 5 | e |\n| 2 | b |\n"
                "| 3 | c |\n| 4 | d |")
        findings = self.check(text)
        self.assertEqual(len(findings), 1)
        self.assertIn("`2` appears after `5`", findings[0][1])

    def test_successive_lettered_sub_items_are_in_order(self):
        # Two suffixes on one number, which no other fixture reaches: 13a must
        # sort before 13b, and both between 13 and 14.
        text = ("| # | X |\n|---|---|\n| 13 | b |\n| 13a | c |\n| 13b | d |\n"
                "| 14 | e |")
        self.assertEqual(self.check(text), [])

    def test_rejection_control_lettered_sub_item_before_its_number(self):
        # REJECTION CONTROL: 13a written above 13 is out of order even though the
        # integers alone read 12, 13, 13, 14.
        text = ("| # | X |\n|---|---|\n| 12 | a |\n| 13a | c |\n| 13 | b |\n"
                "| 14 | d |")
        findings = self.check(text)
        self.assertTrue(any("out of order" in m for _, m in findings))

    def test_descending_table_is_reported_as_out_of_order(self):
        # The documented limitation, pinned as a test so it is a decision rather
        # than an accident: order means ASCENDING, matching the heading half, so a
        # deliberately reversed table is refused. See the workflow's Known Issues.
        text = ("| # | X |\n|---|---|\n| 3 | c |\n| 2 | b |\n| 1 | a |")
        findings = self.check(text)
        self.assertTrue(any("out of order" in m for _, m in findings))

    def test_negative_control_duplicate_is_not_also_called_out_of_order(self):
        # A repeat is not a descent. 1, 2, 2, 3 must produce the duplicate finding
        # and nothing else, or every duplicate is reported twice under two names.
        text = ("| # | X |\n|---|---|\n| 1 | a |\n| 2 | b |\n| 2 | c |\n| 3 | d |")
        findings = self.check(text)
        self.assertEqual(len(findings), 1)
        self.assertIn("duplicate enumerator `2`", findings[0][1])


class HeadingOrderTests(unittest.TestCase):
    """``check_heading_order`` VALIDATES, so the same split as ``SequenceTests``."""

    def check(self, text):
        return run.check_heading_order(lines_of(text))

    def test_positive_control_contiguous_ascending_passes(self):
        # POSITIVE CONTROL: a legal heading sequence, which must produce nothing.
        text = ("## 9 Nine\n\n### 9.1 One\n\n### 9.2 Two\n\n### 9.3 Three\n")
        self.assertEqual(self.check(text), [])

    def test_rejection_control_out_of_order_subsection(self):
        # A real defect: a new subsection inserted before an existing one, so the
        # numbering descends. A reader skims past it and a table-scoped sequence
        # check cannot see it at all.
        text = ("### 9.4 Four\n\n### 9.6 Six\n\n### 9.5 Five\n")
        findings = self.check(text)
        self.assertTrue(any("out of order" in m for _, m in findings))

    def test_rejection_control_gap_in_subsections(self):
        text = "### 9.1 One\n\n### 9.2 Two\n\n### 9.4 Four\n"
        findings = self.check(text)
        self.assertTrue(any("missing 9.3" in m for _, m in findings))

    def test_rejection_control_duplicate_number(self):
        text = "### 9.1 One\n\n### 9.2 Two\n\n### 9.2 Two again\n"
        findings = self.check(text)
        self.assertTrue(any("duplicate heading number `9.2`" in m
                            for _, m in findings))

    def test_negative_control_separate_parents_not_pooled(self):
        text = ("### 4.1 A\n\n### 4.2 B\n\n### 9.1 C\n\n### 9.2 D\n")
        self.assertEqual(self.check(text), [])

    def test_negative_control_unnumbered_headings_ignored(self):
        text = "## Purpose\n\n## Contents\n\n### Notes\n"
        self.assertEqual(self.check(text), [])

    def test_sequence_may_start_at_any_number(self):
        text = "### 9.5 Five\n\n### 9.6 Six\n"
        self.assertEqual(self.check(text), [])

    def test_top_level_numbered_sections_checked(self):
        text = "## 1. One\n\n## 2. Two\n\n## 4. Four\n"
        findings = self.check(text)
        self.assertTrue(any("missing 3" in m for _, m in findings))

    def test_negative_control_heading_inside_fence_ignored(self):
        text = "```\n### 9.6 Six\n### 9.5 Five\n```\n"
        self.assertEqual(self.check(text), [])

    def test_one_misplaced_section_reports_once(self):
        # The same recovery rule as check_sequences, asserted here so the two
        # halves of one check cannot drift apart: 9.1, 9.5, 9.2, 9.3, 9.4 is one
        # section in the wrong place, not four.
        text = ("### 9.1 A\n\n### 9.5 E\n\n### 9.2 B\n\n### 9.3 C\n\n"
                "### 9.4 D\n")
        findings = self.check(text)
        self.assertEqual(len(findings), 1)
        self.assertIn("`9.2` appears after `9.5`", findings[0][1])


class DistanceTests(unittest.TestCase):
    def check(self, text):
        return run.check_distance(lines_of(text))

    def test_positive_control_cardinal_unit_direction(self):
        # POSITIVE CONTROL, drawn from the protocol's own worked example.
        findings = self.check("See the rule two bullets down for the discriminator.")
        self.assertTrue(any("distance reference candidate" in m
                            for _, m in findings))

    def test_positive_control_digits(self):
        findings = self.check("The definition sits 3 lines below this one.")
        self.assertTrue(any("distance reference candidate" in m
                            for _, m in findings))

    def test_positive_control_ordinal_into_a_growing_list(self):
        findings = self.check("The fifth condition must not retake decisions.")
        self.assertTrue(any("ordinal-into-a-list candidate" in m
                            for _, m in findings))

    def test_negative_control_bare_direction_not_flagged(self):
        # Out of scope by decision: a bare direction carries no count to rot.
        self.assertEqual(self.check("The paragraph above says otherwise."), [])

    def test_negative_control_direction_in_a_later_sentence_not_swept(self):
        self.assertEqual(
            self.check("It has two columns. Above all, keep it short."), [])

    def test_negative_control_count_without_direction_not_flagged(self):
        self.assertEqual(self.check("The register has six entries in total."), [])

    def test_reference_split_by_a_hard_wrap_is_found(self):
        # The defect that prompted logical-line joining: the patterns cannot match
        # across a newline, so a wrapped document hid candidates by where the wrap
        # fell. This is the checker's own name-sake phrase, broken mid-way.
        findings = self.check("the rule two\nbullets down says otherwise")
        self.assertTrue(any("two bullets down" in m for _, m in findings))

    def test_split_reference_is_reported_at_the_line_it_starts_on(self):
        findings = self.check("filler\nthe rule two\nbullets down\n")
        self.assertTrue(any("line 2" in m for _, m in findings))

    def test_negative_control_join_does_not_cross_a_blank_line(self):
        # A blank line ends a paragraph, so these are two unrelated sentences and
        # joining them would invent a reference neither one makes.
        self.assertEqual(self.check("it has two columns\n\nbelow, the rest"), [])

    def test_negative_control_join_does_not_cross_two_table_rows(self):
        # A table row is its own logical line: joining adjacent rows would match a
        # count in one cell against a direction in another.
        text = "| a | it has two rows |\n| b | above all, be brief |"
        self.assertEqual(self.check(text), [])

    def test_negative_control_sentence_end_still_blocks_a_join(self):
        # The join must not weaken the rule the newline was accidentally enforcing:
        # a full stop still ends the span, on one line or across two.
        self.assertEqual(self.check("It has two columns.\nAbove all, be brief."), [])

    def test_all_findings_are_info_tier(self):
        findings = self.check("The rule two bullets down and the fifth step.")
        self.assertTrue(findings)
        self.assertTrue(all(sev == "INFO" for sev, _ in findings))


class CitationParseTests(unittest.TestCase):
    """``find_citations`` COUNTS, so a legal citation must be FOUND here. The
    resolution half validates and is tested in the class below."""

    def test_positive_control_long_form_and_range_parsed(self):
        citations, shorthand = run.find_citations(
            ["see `workflows/x/scripts/run.py:12` and `docs/y.md:3-9`"])
        self.assertEqual(
            [(c[2], c[3], c[4]) for c in citations],
            [("workflows/x/scripts/run.py", 12, 12), ("docs/y.md", 3, 9)])
        self.assertEqual(shorthand, [])

    def test_shorthand_detected_separately(self):
        citations, shorthand = run.find_citations(["and again at `:265`"])
        self.assertEqual(citations, [])
        self.assertEqual([s[1] for s in shorthand], ["`:265`"])

    def test_negative_control_clock_time_not_a_citation(self):
        citations, _ = run.find_citations(["the run started at 12:30 today"])
        self.assertEqual(citations, [])

    def test_negative_control_version_and_section_numbers_not_citations(self):
        citations, _ = run.find_citations(
            ["pypdf 6.14.2 and section 4.7a and decision 13a and E11"])
        self.assertEqual(citations, [])

    def test_negative_control_citation_inside_fence_ignored(self):
        citations, _ = run.find_citations(lines_of("```\na/b.py:5\n```"))
        self.assertEqual(citations, [])

    def test_longer_path_is_not_truncated_into_a_shorter_one(self):
        citations, _ = run.find_citations(["`workflows/knowledge-graph/scripts/run.py:213`"])
        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0][2], "workflows/knowledge-graph/scripts/run.py")

    def test_extensionless_dotfile_supported(self):
        citations, _ = run.find_citations(["`.gitignore:5`"])
        self.assertEqual([(c[2], c[3]) for c in citations], [(".gitignore", 5)])


class CitationResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "target.md").write_text(
            "one\ntwo\nthree\nfour\nfive\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, text):
        return run.check_citations(lines_of(text), self.root)

    def test_positive_control_legal_citation_resolves(self):
        # POSITIVE CONTROL: a legal instance - the file exists and the range is
        # inside it - must produce nothing. Proving only that bad input is
        # rejected would leave a checker that rejects everything looking correct.
        self.assertEqual(self.check("see `target.md:2-4`"), [])

    def test_positive_control_last_line_is_inside_the_file(self):
        self.assertEqual(self.check("see `target.md:5`"), [])

    def test_rejection_control_one_line_past_end_of_file(self):
        findings = self.check("see `target.md:6`")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][0], "FAIL")
        self.assertIn("runs past the end", findings[0][1])

    def test_rejection_control_range_end_past_end_of_file(self):
        findings = self.check("see `target.md:4-9`")
        self.assertTrue(any("runs past the end" in m for _, m in findings))

    def test_rejection_control_missing_file(self):
        findings = self.check("see `nope.md:1`")
        self.assertEqual(len(findings), 1)
        self.assertIn("does not exist", findings[0][1])

    def test_unreadable_file_is_not_reported_as_missing(self):
        # A file that exists but cannot be read is a different fact from one that
        # is absent, and it must produce exactly one finding saying so - not two,
        # and not the wrong one, which would send a reader hunting for a path that
        # is sitting exactly where the citation says.
        import unittest.mock as mock
        (self.root / "locked.md").write_bytes(b"a\nb\n")
        with mock.patch("builtins.open", side_effect=OSError("locked")):
            findings = self.check("see `locked.md:1`")
        self.assertEqual(len(findings), 1)
        self.assertIn("exists but could not be read", findings[0][1])

    def test_repeated_citation_to_one_file_reads_it_once(self):
        findings = self.check("`target.md:1` and `target.md:2` and `target.md:9`")
        self.assertEqual(len(findings), 1)
        self.assertIn("runs past the end", findings[0][1])

    def test_rejection_control_reversed_range(self):
        findings = self.check("see `target.md:4-2`")
        self.assertTrue(any("impossible range" in m for _, m in findings))

    def test_shorthand_is_warn_not_fail(self):
        findings = self.check("and again at `:265`")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][0], "WARN")

    def test_file_with_no_trailing_newline_counts_its_last_line(self):
        (self.root / "tail.md").write_text("a\nb", encoding="utf-8")
        self.assertEqual(self.check("see `tail.md:2`"), [])


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stats_count_the_indented_table(self):
        text = ("| A | B |\n|---|---|\n| 1 | 2 |\n\n"
                "  | C | D |\n  |---|---|\n  | 3 | 4 |\n")
        _, stats = run.check_document(text, self.root)
        self.assertEqual(stats["tables"], 2)
        self.assertEqual(stats["table_rows"], 2)

    def test_only_selects_a_single_check(self):
        text = "The rule two bullets down.\n| A |\n|---|---|\n| 1 | 2 |\n"
        findings, _ = run.check_document(text, self.root, only=["distance"])
        self.assertTrue(findings)
        self.assertTrue(all(sev == "INFO" for sev, _ in findings))

    def test_clean_document_reports_nothing(self):
        text = "# Title\n\nProse with no tables and no citations.\n"
        findings, stats = run.check_document(
            text, self.root, data=text.encode("utf-8"))
        self.assertEqual(findings, [])
        self.assertEqual(stats["tables"], 0)


class LineCountTests(unittest.TestCase):
    """The reported line count is a physical line count.

    ``len(text.split("\\n"))`` counts a phantom final element on any file ending
    with a newline, and the hygiene check requires a final newline, so the wrong
    spelling is wrong for every valid document. The stats block used it while the
    citation resolver did not, which is two definitions of one quantity inside one
    script.
    """

    def test_positive_control_newline_terminated_file(self):
        # POSITIVE CONTROL: three lines, each terminated. Not four.
        self.assertEqual(run.physical_line_count("a\nb\nc\n"), 3)

    def test_unterminated_final_line_still_counts(self):
        self.assertEqual(run.physical_line_count("a\nb\nc"), 3)

    def test_negative_control_empty_document_has_no_lines(self):
        self.assertEqual(run.physical_line_count(""), 0)

    def test_single_newline_is_one_empty_line(self):
        self.assertEqual(run.physical_line_count("\n"), 1)

    def test_stats_report_the_physical_count(self):
        # The defect as a caller sees it: the stats block is what gets quoted.
        text = "# T\n\nProse.\n"
        with tempfile.TemporaryDirectory() as tmp:
            _, stats = run.check_document(text, Path(tmp),
                                          data=text.encode("utf-8"))
        self.assertEqual(stats["lines"], 3)

    def test_citation_to_the_last_line_of_a_terminated_file_resolves(self):
        # The half that was already right, pinned so consolidating the two
        # definitions into one cannot regress it: line 3 of a 3-line file is
        # inside the file, and line 4 is not.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "t.py").write_bytes(b"a\nb\nc\n")
            self.assertEqual(run.check_citations(["see t.py:3"], root), [])
            findings = run.check_citations(["see t.py:4"], root)
            self.assertEqual(len(findings), 1)
            self.assertIn("runs past the end", findings[0][1])


class SmokeTests(unittest.TestCase):
    """The real script as a subprocess: the CLI contract a caller depends on.

    Every fixture is written with ``write_bytes``, never ``write_text``. On Windows
    a text-mode write converts each ``\\n`` to CRLF, so a text-mode fixture makes
    the hygiene check fire on the test's own file rather than on what the test is
    about - which is exactly what happened when the hygiene check was added, in
    three of these four tests at once.
    """

    def _run(self, args):
        return subprocess.run(
            [sys.executable, str(SCRIPT)] + args,
            capture_output=True, text=True, encoding="utf-8")

    def test_clean_file_exits_zero_with_valid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "clean.md"
            doc.write_bytes(b"# T\n\nProse only.\n")
            result = self._run(["--check", "--json", str(doc)])
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["documents"][0]["findings"], [])

    def test_strict_exits_one_on_a_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "ragged.md"
            doc.write_bytes(b"| A | B |\n|---|---|\n| 1 |\n")
            result = self._run(["--check", "--strict", str(doc)])
            self.assertEqual(result.returncode, 1)
            self.assertIn("FAIL", result.stdout)

    def test_info_alone_does_not_gate_under_strict(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "distance.md"
            doc.write_bytes(b"The rule two bullets down.\n")
            result = self._run(["--check", "--strict", str(doc)])
            self.assertEqual(result.returncode, 0, result.stdout)

    def test_missing_file_exits_one(self):
        result = self._run(["--check", "no-such-file-here.md"])
        self.assertEqual(result.returncode, 1)

    def test_invalid_utf8_uses_hygiene_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "invalid.md"
            doc.write_bytes(b"# T\n\xff\xfe\n")
            result = self._run(["--check", "--strict", str(doc)])
            self.assertEqual(result.returncode, 1)
            self.assertIn("not valid UTF-8", result.stdout)
            self.assertNotIn("could not read", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
