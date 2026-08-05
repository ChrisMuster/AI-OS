#!/usr/bin/env python3
"""Unit tests for the encoding-guard script.

Fixtures are built with chr()/bytes so this test file stays pure ASCII and is
never itself flagged by the guard.
"""

import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import run  # noqa: E402

EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)
RSQUO = chr(0x2019)
LDQUO = chr(0x201C)
ELLIPSIS = chr(0x2026)
NBSP = chr(0x00A0)
BOM = chr(0xFEFF)


class TestSignatureGeneration(unittest.TestCase):
    def test_em_dash_mojibake_matches_real_corruption(self):
        # Em dash bytes E2 80 94 mis-read as Windows-1252 -> these three chars.
        key = run._mojibake(EM_DASH)
        self.assertEqual([ord(c) for c in key], [0x00E2, 0x20AC, 0x201D])
        self.assertEqual(run.DOUBLE_ENCODED[key], "-")

    def test_every_smart_char_has_a_mojibake_key(self):
        for ch in run.SMART_PUNCT:
            self.assertIn(run._mojibake(ch), run.DOUBLE_ENCODED)

    def test_cp1252_undefined_byte_falls_back_to_latin1(self):
        # 0x9D is undefined in cp1252; _cp1252_char must not raise.
        self.assertEqual(run._cp1252_char(0x9D), chr(0x9D))


class TestScanText(unittest.TestCase):
    def test_clean_text_has_no_codes(self):
        self.assertEqual(run.scan_text("plain ascii text"), [])

    def test_legit_em_dash_is_not_mojibake(self):
        self.assertEqual(run.scan_text(f"a {EM_DASH} b"), [])

    def test_mojibake_detected(self):
        corrupt = "title " + run._mojibake(EM_DASH) + " desc"
        self.assertIn("mojibake", run.scan_text(corrupt))

    def test_bom_detected(self):
        self.assertIn("bom", run.scan_text(BOM + "text"))


class TestRepairText(unittest.TestCase):
    def test_folds_mojibake_to_ascii(self):
        corrupt = "x " + run._mojibake(EM_DASH) + " y"
        self.assertEqual(run.repair_text(corrupt), "x - y")

    def test_folds_legit_smart_punct(self):
        s = f"{LDQUO}hi{RSQUO} {EM_DASH}{ELLIPSIS}{NBSP}end"
        self.assertEqual(run.repair_text(s), '"hi\' -... end')

    def test_strips_bom_and_normalises_newlines(self):
        self.assertEqual(run.repair_text(BOM + "a\r\nb\rc"), "a\nb\nc")

    def test_idempotent(self):
        corrupt = "x " + run._mojibake(EM_DASH) + " y\r\n"
        once = run.repair_text(corrupt)
        self.assertEqual(run.repair_text(once), once)


class TestRepairBytes(unittest.TestCase):
    def test_recovers_raw_cp1252_byte(self):
        raw = b"Note created " + bytes([0x97]) + b" archived\n"
        fixed, note = run.repair_bytes(raw)
        self.assertEqual(note, "recovered")
        self.assertEqual(fixed.decode("utf-8"), "Note created - archived\n")

    def test_preserves_then_folds_legit_utf8(self):
        # A valid UTF-8 em dash plus a stray cp1252 byte: the legit one must be
        # decoded (not byte-mangled) and then folded with the rest.
        raw = ("a " + EM_DASH + " b").encode("utf-8") + bytes([0x97])
        fixed, _ = run.repair_bytes(raw)
        self.assertEqual(fixed.decode("utf-8"), "a - b-")

    def test_unmappable_byte_returns_none(self):
        # 0x81 is undefined in cp1252 and not in the punctuation map.
        fixed, note = run.repair_bytes(b"bad " + bytes([0x81]) + b" byte")
        self.assertIsNone(fixed)
        self.assertEqual(note, "unmapped-byte")

    def test_clean_bytes_unchanged(self):
        raw = "clean ascii\n".encode("utf-8")
        self.assertEqual(run.repair_bytes(raw)[0], raw)


class TestHasEncodingProblem(unittest.TestCase):
    def test_clean_ascii_is_not_a_problem(self):
        self.assertFalse(run._has_encoding_problem(b"clean text\n"))

    def test_legit_em_dash_is_not_a_problem(self):
        # The whole point: --fix must not touch files that merely use real
        # Unicode punctuation.
        self.assertFalse(
            run._has_encoding_problem(("a " + EM_DASH + " b").encode("utf-8"))
        )

    def test_mojibake_is_a_problem(self):
        self.assertTrue(
            run._has_encoding_problem(("x " + run._mojibake(EM_DASH)).encode("utf-8"))
        )

    def test_invalid_utf8_is_a_problem(self):
        self.assertTrue(run._has_encoding_problem(b"bad " + bytes([0x97])))


class TestCrlfDetection(unittest.TestCase):
    """The line-ending half of the AGENTS.md 'UTF-8 with LF' rule.

    Until 2026-08-04 the LF half was enforced only inside --fix, so --check (and
    therefore the audit hook that shells out to it) reported a project full of
    CRLF files as clean. These are written against the stated rule rather than
    against the implementation.
    """

    def test_positive_control_crlf_is_reported(self):
        self.assertIn("crlf", run.scan_text("line one\r\nline two\n"))

    def test_positive_control_lone_cr_is_reported(self):
        # Old-Mac endings are equally off-policy and equally invisible before.
        self.assertIn("crlf", run.scan_text("line one\rline two"))

    def test_negative_control_lf_only_is_clean(self):
        self.assertEqual(run.scan_text("line one\nline two\n"), [])

    def test_crlf_is_independent_of_the_other_codes(self):
        codes = run.scan_text(BOM + "a " + run._mojibake(EM_DASH) + " b\r\n")
        self.assertEqual(sorted(codes), ["bom", "crlf", "mojibake"])

    def test_classify_file_reports_crlf_as_warn(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "note.md"
            target.write_bytes(b"first\r\nsecond\r\n")
            findings = run.classify_file(root, target)
            self.assertEqual(
                [(sev, lab) for sev, lab, _ in findings], [("WARN", "encoding")]
            )
            self.assertIn("CR line endings", findings[0][2])

    def test_check_reads_bytes_so_text_mode_cannot_hide_the_defect(self):
        # The trap this check is most likely to be broken by later: reading the
        # file in text mode strips \r on the way in, so a CRLF file would decode
        # to LF and scan clean. Assert the real pipeline sees the CR.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.md").write_bytes(b"x\r\ny\r\n")
            findings = run.run_check(root)
            self.assertTrue(any("CR line endings" in m for _, _, m in findings))


class TestProblemCodes(unittest.TestCase):
    def test_crlf_only_file_reports_just_crlf(self):
        self.assertEqual(run.problem_codes(b"a\r\nb\r\n"), ["crlf"])

    def test_invalid_utf8_short_circuits(self):
        self.assertEqual(run.problem_codes(b"bad " + bytes([0x97])), ["invalid-utf8"])

    def test_clean_file_has_no_codes(self):
        self.assertEqual(run.problem_codes(b"clean\n"), [])


class TestNewlineOnlyRepair(unittest.TestCase):
    """A CRLF file is not a corrupted file, and must not be repaired like one."""

    def test_repair_newlines_leaves_legitimate_punctuation_alone(self):
        raw = ("title " + EM_DASH + " subtitle\r\n").encode("utf-8")
        self.assertEqual(
            run.repair_newlines(raw).decode("utf-8"),
            "title " + EM_DASH + " subtitle\n",
        )

    def test_repair_newlines_is_idempotent(self):
        once = run.repair_newlines(b"a\r\nb\rc\n")
        self.assertEqual(run.repair_newlines(once), once)

    def test_fix_uses_the_narrow_path_for_a_crlf_only_file(self):
        # The regression that matters: routing a CRLF-only file through
        # repair_bytes would silently fold its em dash to a hyphen, rewriting
        # content in a pass the user asked for line endings.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "doc.md"
            target.write_bytes(("keep " + EM_DASH + " me\r\n").encode("utf-8"))
            changed, skipped = run.run_fix(root, dry_run=False)
            self.assertEqual(changed, ["doc.md"])
            self.assertEqual(skipped, [])
            self.assertEqual(
                target.read_bytes().decode("utf-8"), "keep " + EM_DASH + " me\n"
            )

    def test_fix_still_fully_repairs_a_genuinely_corrupted_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "doc.md"
            target.write_bytes(("x " + run._mojibake(EM_DASH) + " y\r\n").encode("utf-8"))
            run.run_fix(root, dry_run=False)
            self.assertEqual(target.read_bytes(), b"x - y\n")

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "doc.md"
            target.write_bytes(b"a\r\nb\r\n")
            changed, _ = run.run_fix(root, dry_run=True)
            self.assertEqual(changed, ["doc.md"])
            self.assertEqual(target.read_bytes(), b"a\r\nb\r\n")

    def test_preserve_mtime_keeps_the_original_timestamp(self):
        # doc-sync-guard reads LOG.md mtime as evidence a directory logged its
        # change. A bulk newline pass without this flag would touch every
        # LOG.md and make every directory look freshly logged.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "LOG.md"
            target.write_bytes(b"entry\r\n")
            os.utime(target, (1_000_000_000, 1_000_000_000))
            run.run_fix(root, dry_run=False, preserve_mtime=True)
            self.assertEqual(target.read_bytes(), b"entry\n")
            self.assertEqual(int(target.stat().st_mtime), 1_000_000_000)

    def test_without_the_flag_mtime_moves(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "LOG.md"
            target.write_bytes(b"entry\r\n")
            os.utime(target, (1_000_000_000, 1_000_000_000))
            run.run_fix(root, dry_run=False)
            self.assertNotEqual(int(target.stat().st_mtime), 1_000_000_000)


class TestCodeCheck(unittest.TestCase):
    def test_flags_text_mode_subprocess_without_encoding(self):
        src = "import subprocess\nsubprocess.run(cmd, capture_output=True, text=True)\n"
        findings = run.check_python_code("x.py", src)
        self.assertTrue(any(f[0] == "WARN" for f in findings))

    def test_does_not_flag_with_encoding(self):
        src = ('subprocess.run(cmd, text=True, encoding="utf-8")\n')
        self.assertEqual(run.check_python_code("x.py", src), [])

    def test_does_not_flag_bytes_mode(self):
        # No text=True means bytes are returned; encoding is irrelevant.
        src = "subprocess.run(cmd, capture_output=True)\n"
        self.assertEqual(run.check_python_code("x.py", src), [])

    def test_ignores_calls_in_strings_and_comments(self):
        src = (
            '"""doc mentions open() and subprocess.run(text=True)"""\n'
            "# subprocess.run(text=True) in a comment\n"
            'x = "subprocess.run(text=True)"\n'
        )
        self.assertEqual(run.check_python_code("x.py", src), [])

    def test_flags_open_without_encoding(self):
        src = "open(path, 'w')\n"
        findings = run.check_python_code("x.py", src)
        self.assertTrue(any(f[0] == "INFO" for f in findings))

    def test_does_not_flag_binary_open(self):
        src = "open(path, 'wb')\n"
        self.assertEqual(run.check_python_code("x.py", src), [])


class TestNewlineCheck(unittest.TestCase):
    """The newline clause of the project's text I/O rule.

    Until 2026-08-04 ``check_python_code`` returned as soon as it saw
    ``encoding=``, so a text write that pinned the encoding and omitted
    ``newline=`` was reported clean - the exact class the rule exists to stop,
    because Windows text mode turns every LF into CRLF on the way out. The
    positive controls below are constructed from the rule in AGENTS.md rather
    than copied out of the tree, so they prove the check reads the right
    definition and not merely that it fires on something.
    """

    def _newline_findings(self, src):
        return [f for f in run.check_python_code("x.py", src) if "newline=" in f[2]]

    # -- positive controls: each MUST be caught --------------------------------
    def test_flags_write_text_with_encoding_but_no_newline(self):
        src = 'Path("x.md").write_text("a\\n", encoding="utf-8")\n'
        findings = self._newline_findings(src)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][0], "WARN")

    def test_flags_open_write_modes_with_encoding_but_no_newline(self):
        for mode in ("'w'", "'a'", "'x'", "'w+'", "'r+'"):
            with self.subTest(mode=mode):
                src = f'open(path, {mode}, encoding="utf-8")\n'
                self.assertEqual(len(self._newline_findings(src)), 1)

    def test_flags_open_with_mode_keyword(self):
        src = 'open(path, mode="w", encoding="utf-8")\n'
        self.assertEqual(len(self._newline_findings(src)), 1)

    # -- negative controls: none may fire --------------------------------------
    def test_compliant_writes_are_clean(self):
        for src in (
            # NOT a recommendation of this spelling: Path.write_text accepts
            # newline only on Python 3.10+, and AGENTS.md tells authors to use
            # open(..., newline="\\n") or write_bytes to stay inside the stated
            # 3.9 floor. It is kept because it is the negative control for the
            # .write_text shape, and because the guard's answer is correct for
            # the question the guard asks: the newline argument is passed. The
            # version question belongs to the "Test the stated Python floor"
            # backlog item, and building it in here would make this guard
            # answer two rules at once.
            'Path("x.md").write_text("a", encoding="utf-8", newline="\\n")\n',
            'open(path, "w", encoding="utf-8", newline="\\n")\n',
            # csv wants newline="" - it is the second member of the allowed set,
            # not proof that the value goes unchecked. See TestArgumentValues.
            'open(path, "w", encoding="utf-8", newline="")\n',
        ):
            with self.subTest(src=src):
                self.assertEqual(self._newline_findings(src), [])

    def test_reads_and_binary_writes_are_clean(self):
        for src in (
            'Path("x.md").read_text(encoding="utf-8")\n',
            'open(path, encoding="utf-8")\n',
            'open(path, "r", encoding="utf-8")\n',
            'open(path, "rb")\n',
            'open(path, "wb")\n',
            'subprocess.run(cmd, text=True, encoding="utf-8")\n',
        ):
            with self.subTest(src=src):
                self.assertEqual(self._newline_findings(src), [])

    def test_computed_mode_is_not_guessed_at(self):
        # An unreadable mode must stay silent rather than guess and misreport.
        src = 'open(path, mode, encoding="utf-8")\n'
        self.assertEqual(self._newline_findings(src), [])

    def test_prose_is_not_flagged(self):
        src = '"""write_text(p, encoding="utf-8") in a docstring"""\nx = 1\n'
        self.assertEqual(self._newline_findings(src), [])

    # -- the Path.open form ----------------------------------------------------
    # Added 2026-08-04. The scanner matched builtin `open`, `.read_text` and
    # `.write_text` but not `.open`, so a `p.open("a", encoding="utf-8")` append
    # was reported clean while writing CRLF on Windows - one such write survived
    # the whole newline clearance pass. These controls are the same rule applied
    # to the call shape the earlier ones happened not to cover.
    def test_flags_path_open_append_with_encoding_but_no_newline(self):
        src = 'Path("x.md").open("a", encoding="utf-8")\n'
        findings = self._newline_findings(src)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][0], "WARN")

    def test_flags_path_open_write_modes_on_a_variable(self):
        for mode in ("'w'", "'a'", "'x'", "'w+'", "'r+'"):
            with self.subTest(mode=mode):
                src = f'p.open({mode}, encoding="utf-8")\n'
                self.assertEqual(len(self._newline_findings(src)), 1)

    def test_flags_path_open_with_mode_keyword(self):
        src = 'p.open(mode="w", encoding="utf-8")\n'
        self.assertEqual(len(self._newline_findings(src)), 1)

    def test_flags_path_open_on_an_expression_receiver_across_lines(self):
        # The shape used in the tree: a joined path, call arguments wrapped.
        src = (
            'with (d / "f.md").open(\n'
            '    "w", encoding="utf-8"\n'
            ") as fh:\n"
            '    fh.write("a")\n'
        )
        self.assertEqual(len(self._newline_findings(src)), 1)

    def test_compliant_path_open_writes_are_clean(self):
        for src in (
            'p.open("w", encoding="utf-8", newline="\\n")\n',
            'p.open("a", encoding="utf-8", newline="\\n")\n',
            'p.open("r", encoding="utf-8")\n',
            'p.open("rb")\n',
            'p.open("wb")\n',
            "p.open()\n",
        ):
            with self.subTest(src=src):
                self.assertEqual(self._newline_findings(src), [])

    def test_the_mode_is_read_from_the_right_argument_position(self):
        """``Path.open`` takes the mode first, builtin ``open`` second.

        Reading the wrong position is silent rather than loud, so it needs a
        control on each side: a builtin read whose *path* would look like a
        write mode must stay clean, and a dotted write must still fire.
        """
        self.assertEqual(self._newline_findings('open("wax.md", encoding="utf-8")\n'), [])
        self.assertEqual(len(self._newline_findings('p.open("w", encoding="utf-8")\n')), 1)

    def test_unrelated_dot_open_calls_are_not_read_as_file_modes(self):
        """``.open`` is not owned by pathlib.

        A first argument that is not mode-shaped means the call is something
        else, and guessing produces false positives: read as a mode, the URL
        "http://x" is a write, on the 'x' in it.
        """
        for src in (
            'webbrowser.open("http://x")\n',
            "webbrowser.open(url)\n",
            "os.open(path, flags)\n",
            'tarfile.open("archive.tar")\n',
        ):
            with self.subTest(src=src):
                self.assertEqual(run.check_python_code("x.py", src), [])

    # -- the two clauses are independent ---------------------------------------
    def test_missing_both_reports_both_clauses(self):
        """A write missing encoding AND newline reports one finding per clause.

        This is the structural guarantee: neither check may swallow the other,
        which is how the newline clause went unenforced in the first place.
        """
        findings = run.check_python_code("x.py", 'Path("x.md").write_text("a")\n')
        self.assertEqual(len(findings), 2)
        self.assertEqual(
            {"encoding=" in f[2] and "newline=" not in f[2] for f in findings},
            {True, False},
        )


class TestArgumentPresence(unittest.TestCase):
    """Whether an argument is *present*, as opposed to whether its name appears.

    Until 2026-08-04 all three of these questions - is ``encoding=`` passed, is
    ``newline=`` passed, is ``mode=`` passed - were answered by looking for
    characters in the raw call text. That is wrong in both directions inside the
    write shapes the guard claims to cover: the sentinel appears in a string
    value, in a comment, and inside a longer keyword such as ``file_encoding=``,
    and it fails to appear when someone writes ``newline = "\\n"`` with spaces.
    A checker that can be talked out of a finding by a comment is not enforcing
    the rule, it is pattern-matching near it.

    Arguments are now parsed, so these controls pin the parse rather than the
    spelling. Each is written from the rule, not from the tree.
    """

    def _labels(self, src):
        """The clauses that fired, as a set: 'newline', 'encoding', or empty."""
        return {
            "newline" if "newline=" in msg else "encoding"
            for _sev, _cat, msg in run.check_python_code("x.py", src)
        }

    # -- the sentinel appears but the argument does not (must still fire) ------
    def test_sentinel_inside_a_string_value_is_not_a_keyword(self):
        src = 'Path("x.md").write_text("newline=", encoding="utf-8")\n'
        self.assertEqual(self._labels(src), {"newline"})

    def test_sentinel_inside_a_comment_is_not_a_keyword(self):
        src = 'Path("x.md").write_text(\n    "a", encoding="utf-8"  # newline= later\n)\n'
        self.assertEqual(self._labels(src), {"newline"})

    def test_encoding_sentinel_inside_a_string_value_is_not_a_keyword(self):
        src = 'open(path, "w", note="encoding=", newline="\\n")\n'
        self.assertEqual(self._labels(src), {"encoding"})

    def test_a_longer_keyword_ending_in_the_sentinel_is_a_different_argument(self):
        # `file_encoding=` contains "encoding=" but is not it.
        self.assertEqual(
            self._labels('open(path, "w", file_encoding="utf-8", newline="\\n")\n'),
            {"encoding"},
        )
        self.assertEqual(
            self._labels('Path("x").write_text("a", encoding="utf-8", the_newline="\\n")\n'),
            {"newline"},
        )

    # -- the argument is present but spaced (must stay silent) -----------------
    def test_whitespace_around_the_keyword_assignment_is_still_the_keyword(self):
        for src in (
            'open(path, "w", encoding="utf-8", newline = "\\n")\n',
            'open(path, "w", encoding = "utf-8", newline="\\n")\n',
            'open(path, "w", encoding = "utf-8", newline = "\\n")\n',
            'Path("x").write_text("a", encoding = "utf-8", newline = "\\n")\n',
        ):
            with self.subTest(src=src):
                self.assertEqual(self._labels(src), set())

    def test_a_spaced_mode_keyword_is_still_read_as_the_mode(self):
        """The mode decides whether the newline clause applies at all.

        A mode read as absent is read as Python's default (a text *read*), which
        silently skips the newline check on a real write - a false negative on
        the plainest write shape there is.
        """
        self.assertEqual(self._labels('open(path, mode = "w", encoding="utf-8")\n'),
                         {"newline"})
        # And the other direction: a binary write has no encoding and no newline.
        self.assertEqual(self._labels('open(path, mode = "wb")\n'), set())

    def test_a_text_mode_subprocess_is_detected_by_keyword_not_substring(self):
        self.assertEqual(self._labels('subprocess.run(cmd, text = True)\n'),
                         {"encoding"})
        self.assertEqual(self._labels('subprocess.run(cmd, note="text=True")\n'), set())

    # -- shapes whose arguments cannot be read (must stay silent, not guess) ---
    def test_a_splatted_call_is_unreadable_rather_than_clean(self):
        """A splat makes the argument list unprovable, so the call is skipped.

        ``**opts`` may carry either keyword. ``*parts`` may supply either one
        positionally, because both ``open`` and ``write_text`` accept
        ``encoding`` by position as well - so a positional splat is no more
        readable than a keyword one.

        Silence here is the honest answer and matches how the guard already
        treats a mode it cannot read. It is a real limit of the check, recorded
        in Known Issues rather than papered over - the alternative is reporting
        a violation the source may not contain.
        """
        for src in (
            'open(path, "w", **opts)\n',
            'Path("x").write_text("a", **opts)\n',
            "subprocess.run(cmd, **opts)\n",
            'open(*parts, encoding="utf-8", newline="\\n")\n',
            "open(*parts)\n",
            'Path("x").write_text(*args)\n',
        ):
            with self.subTest(src=src):
                self.assertEqual(self._labels(src), set())


class TestArgumentValues(unittest.TestCase):
    """Whether an argument carries the value the rule names, not merely a value.

    The three rounds before this one were all about *presence*: is the call
    matched, is the keyword really there. Presence is not the rule. AGENTS.md
    states both clauses as values - `encoding="utf-8"` and `newline="\\n"` - so
    until 2026-08-05 `encoding=None`, `encoding="cp1252"` and `newline=None` all
    read as compliant, and the guard AGENTS.md names as the enforcer was
    enforcing a weaker rule than the one written down.

    The two clauses have different allowed sets, and that asymmetry is the
    reason the values are pinned separately rather than by one shared helper:
    `newline=""` is legitimate (csv needs it), while there is no legitimate
    non-UTF-8 encoding in this project. Controls are written from the rule, and
    split by direction, because a checker that reports every value passes a
    suite built only from violations.
    """

    def _findings(self, src):
        return run.check_python_code("x.py", src)

    def _labels(self, src):
        return {
            "newline" if "newline=" in msg else "encoding"
            for _sev, _cat, msg in self._findings(src)
        }

    # -- encoding: present but not UTF-8 (must fire) ---------------------------
    def test_flags_encoding_none(self):
        """`encoding=None` is a violation, not an omission.

        It names the platform default explicitly, which on Windows is cp1252 -
        the decode trap this whole workflow exists to stop. A presence-only
        check reads it as the most compliant call in the file.
        """
        findings = self._findings('open(path, "w", encoding=None, newline="\\n")\n')
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][0], "WARN")
        self.assertIn("not utf-8", findings[0][2])

    def test_flags_a_wrong_codec(self):
        for src in (
            'open(path, "w", encoding="cp1252", newline="\\n")\n',
            'open(path, "w", encoding="latin-1", newline="\\n")\n',
            'Path("x").write_text("a", encoding="ascii", newline="\\n")\n',
        ):
            with self.subTest(src=src):
                self.assertEqual(self._labels(src), {"encoding"})

    def test_the_encoding_clause_covers_reads_too(self):
        """Unlike newline, the encoding rule governs reads as well as writes.

        A read decoded as cp1252 is how mojibake gets *in*, so the clause must
        not inherit the newline clause's write-only scope.
        """
        self.assertEqual(self._labels('Path("x").read_text(encoding="cp1252")\n'),
                         {"encoding"})
        self.assertEqual(self._labels('open(path, "r", encoding="cp1252")\n'),
                         {"encoding"})

    def test_flags_a_wrong_codec_on_a_text_mode_subprocess(self):
        src = 'subprocess.run(cmd, text=True, encoding="cp1252")\n'
        findings = self._findings(src)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][0], "WARN")

    # -- encoding: every spelling of UTF-8 (must stay silent) ------------------
    def test_utf8_spellings_are_accepted(self):
        """Python's codec lookup folds case and hyphens, so these are one codec.

        Pinning the value must pin the codec, not one way of typing it, or the
        check invents a house style the language does not have.
        """
        for value in ('"utf-8"', '"UTF-8"', '"utf8"', '"utf_8"', '"Utf-8"',
                      '"u8"', '"utf"', "'utf-8'"):
            with self.subTest(value=value):
                src = f'open(path, "w", encoding={value}, newline="\\n")\n'
                self.assertEqual(self._labels(src), set())

    # -- newline: present but not an allowed value (must fire) -----------------
    def test_flags_newline_none(self):
        """`newline=None` is the default the clause exists to stop.

        On a write it translates every LF to os.linesep, which is the CRLF the
        file half of this guard reports. Passing it explicitly is not compliance.
        """
        findings = self._findings('open(path, "w", encoding="utf-8", newline=None)\n')
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][0], "WARN")

    def test_flags_a_newline_that_writes_cr(self):
        for src in (
            'open(path, "w", encoding="utf-8", newline="\\r\\n")\n',
            'open(path, "w", encoding="utf-8", newline="\\r")\n',
            'Path("x").write_text("a", encoding="utf-8", newline="\\r\\n")\n',
        ):
            with self.subTest(src=src):
                self.assertEqual(self._labels(src), {"newline"})

    def test_the_csv_value_stays_allowed(self):
        """`newline=""` is the one non-LF value the rule permits.

        csv writers emit their own line endings and need the file object not to
        translate. This is why the allowed set has two members rather than one,
        and why pinning newline to a single value would be wrong.
        """
        self.assertEqual(
            self._labels('open(path, "w", encoding="utf-8", newline="")\n'), set())

    # -- values the scanner cannot read (must stay silent, not guess) ----------
    def test_an_unreadable_value_is_not_judged(self):
        """A value that is not a plain literal is left alone.

        Deciding it would mean resolving names across the call site, which is a
        different kind of check from the single-call inspection this guard does.
        Silence matches how it already treats a computed mode and a splatted
        call: an honest gap rather than a guess in either direction.
        """
        for src in (
            'open(path, "w", encoding=enc, newline=nl)\n',
            'open(path, "w", encoding=DEFAULT_ENCODING, newline="\\n")\n',
            'open(path, "w", encoding=cfg["enc"], newline="\\n")\n',
            'open(path, "w", encoding=f"{base}-8", newline="\\n")\n',
        ):
            with self.subTest(src=src):
                self.assertEqual(self._labels(src), set())

    # -- the value is what the literal evaluates to, not how it was typed ------
    def test_an_escaped_spelling_of_the_right_value_is_accepted(self):
        """A value is judged by what it produces, not by how it was written.

        Comparing the source text instead would make these false positives:
        both spell exactly the value the rule names.
        """
        for src in (
            'open(path, "w", encoding="utf-8", newline="\\x0a")\n',
            'open(path, "w", encoding="\\x75tf-8", newline="\\n")\n',
        ):
            with self.subTest(src=src):
                self.assertEqual(self._labels(src), set())

    def test_a_raw_literal_that_is_not_the_right_value_still_fires(self):
        """`r"\\n"` is a backslash and an 'n', not a line feed.

        It is a readable literal carrying the wrong value, so it is a finding
        rather than an unreadable shape. open() rejects it at runtime; the guard
        should not be more forgiving than the function it guards.
        """
        self.assertEqual(
            self._labels('open(path, "w", encoding="utf-8", newline=r"\\n")\n'),
            {"newline"},
        )

    # -- the value clauses must not swallow the presence clauses ---------------
    def test_a_wrong_value_and_a_missing_argument_both_report(self):
        """One finding per clause, the same structural guarantee as before.

        The value check is a branch of the encoding clause, so the risk it adds
        is the old one in a new place: an argument that is present but wrong
        must not consume the *other* clause's finding.
        """
        findings = self._findings('open(path, "w", encoding="cp1252")\n')
        self.assertEqual(len(findings), 2)
        self.assertEqual(
            {"newline" if "newline=" in f[2] else "encoding" for f in findings},
            {"encoding", "newline"},
        )

    def test_a_wrong_value_is_blocking_where_a_missing_one_is_advisory(self):
        """Severity is load-bearing: only WARN hard-fails the close-out gate.

        A missing `encoding=` on an open() stays INFO, because it is often a
        read of a file the author never thought about. A *wrong* one is a
        deliberate statement that contradicts the rule, so it blocks.
        """
        missing = self._findings('open(path, "r")\n')
        self.assertEqual([f[0] for f in missing], ["INFO"])
        wrong = self._findings('open(path, "r", encoding="cp1252")\n')
        self.assertEqual([f[0] for f in wrong], ["WARN"])


class TestCallDiscovery(unittest.TestCase):
    """*Which* constructs are calls at all, as distinct from what their
    arguments say.

    Every earlier round in this file is about reading a call correctly. This one
    is about the step before that: a regex matched call-shaped text, and a
    definition is call-shaped text. `def open(self, mode="w")` was read as a
    text-mode write with no `newline=`, which is a WARN, and an `encoding` WARN
    hard-fails close-out - so a harmless method named `open` would have blocked
    the gate on code doing no text I/O at all.

    The controls are written from that boundary rather than from the fix: the
    question is "is this a call", so each case names a construct and the answer
    the language gives, not a code path.
    """

    def _findings(self, src):
        return run.check_python_code("x.py", src)

    # -- a definition is not a call -------------------------------------------
    def test_a_function_named_open_is_not_a_call(self):
        self.assertEqual(self._findings("def open(path):\n    pass\n"), [])

    def test_a_method_named_open_with_a_mode_default_is_not_a_call(self):
        """The exact false positive: mode-shaped default, so the old scanner
        read it as a write and produced a close-out-blocking WARN."""
        src = 'class X:\n    def open(self, mode="w"):\n        pass\n'
        self.assertEqual(self._findings(src), [])

    def test_an_async_definition_named_open_is_not_a_call(self):
        self.assertEqual(self._findings('async def open(p, mode="w"):\n    pass\n'), [])

    def test_a_class_named_open_is_not_a_call(self):
        self.assertEqual(self._findings("class open(Base):\n    pass\n"), [])

    def test_a_nested_definition_named_open_is_not_a_call(self):
        src = 'def outer():\n    def open(mode="w"):\n        pass\n'
        self.assertEqual(self._findings(src), [])

    def test_definitions_of_the_other_covered_names_are_not_calls(self):
        for src in ("def read_text(self):\n    pass\n",
                    "def write_text(self, s):\n    pass\n"):
            with self.subTest(src=src):
                self.assertEqual(self._findings(src), [])

    # -- a non-path receiver is not Path.open ---------------------------------
    def test_a_mode_shaped_argument_to_a_non_path_open_is_not_a_mode(self):
        """`.open` is shared, and the sibling functions do not take a mode
        first. The mode-shape test alone cannot separate them: read as a mode,
        the tar member name "w" is a text write."""
        for src in ('import tarfile\ntarfile.open("w")\n',
                    'import zipfile\nzipfile.ZipFile("x").open("w")\n',
                    'import webbrowser\nwebbrowser.open("w")\n',
                    'import shutil\nshutil.open("w")\n'):
            with self.subTest(src=src):
                self.assertEqual(self._findings(src), [])

    def test_the_receiver_rule_reads_the_leftmost_name_not_the_nearest(self):
        """`zipfile.ZipFile("x").open(...)` has a *call* as its receiver, so the
        name to judge is at the root of the expression, not next to the dot."""
        self.assertIsNone(run._receiver_root(ast.parse("(a + b).open").body[0].value.value))
        self.assertEqual(
            run._receiver_root(ast.parse('zipfile.ZipFile("x")').body[0].value),
            "zipfile")
        self.assertEqual(run._receiver_root(ast.parse("Path(p)").body[0].value), "Path")

    def test_open_style_modules_with_the_builtin_signature_stay_silent(self):
        """`io`, `codecs` and `gzip` take (file, mode) rather than (mode), so
        argument 0 is a filename. They are excluded by name rather than left to
        the mode shape, and this is a documented false negative: a real text
        write through one of them is not checked."""
        for src in ('import io\nio.open(f, "w")\n',
                    'import codecs\ncodecs.open(f, "w", "utf-8")\n',
                    'import gzip\ngzip.open(f, "wt")\n'):
            with self.subTest(src=src):
                self.assertEqual(self._findings(src), [])

    # -- the receiver rule must not delete real coverage -----------------------
    def test_real_path_open_writes_still_fire_on_every_receiver_shape(self):
        """The negative controls above are only safe if the positive ones hold.

        A rule that dropped calls by receiver could pass every test in the block
        above by checking nothing at all, so each receiver shape the project
        actually writes is asserted to still report. The expression receiver
        matters most: it has no plain name at its root, which is the case a
        "skip anything I cannot name" rule would silently lose.
        """
        for src in ('p.open("w", encoding="utf-8")\n',
                    'Path(x).open("w", encoding="utf-8")\n',
                    '(base / name).open("w", encoding="utf-8")\n',
                    'paths[0].open("w", encoding="utf-8")\n',
                    'self.path.open("w", encoding="utf-8")\n'):
            with self.subTest(src=src):
                findings = self._findings(src)
                self.assertEqual([f[0] for f in findings], ["WARN"])
                self.assertIn("newline=", findings[0][2])

    def test_a_local_variable_named_like_nothing_in_particular_is_still_checked(self):
        """The stated boundary, pinned so it is a decision rather than a bug.

        `thing.open("w")` and `p.open("w")` are the same shape. Telling a Path
        receiver from any other object needs type inference, so a bare name is
        checked and the residual false positive is accepted: the alternative is
        dropping the dotted write check entirely, which is the case the whole
        clause exists for. Known Issues records it.
        """
        findings = self._findings('thing.open("w", encoding="utf-8")\n')
        self.assertEqual([f[0] for f in findings], ["WARN"])

    # -- an unreadable file is reported unchecked, not scanned -----------------
    def test_a_file_that_does_not_parse_degrades_instead_of_reporting(self):
        """Prose in an unparseable file used to become findings.

        The blanking pass returned the source untouched when it could not
        tokenise it, so strings and comments were then read as code. That is the
        one degradation direction a blocking label cannot afford: it invents
        WARNs out of text that is not even Python.
        """
        src = ("= Notes =\n"
               "Call open(path, 'w') without newline= and you get CRLF.\n"
               "!!! not python\n")
        findings = self._findings(src)
        self.assertEqual([f[0] for f in findings], ["DEGRADED"])
        self.assertIn("code checks skipped", findings[0][2])

    def test_the_degraded_finding_names_the_file_and_the_reason(self):
        findings = self._findings('open(p, "w\n')
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0][2].startswith("x.py:"))
        self.assertIn("does not parse as Python", findings[0][2])

    def test_degraded_is_not_a_blocking_severity(self):
        """Close-out blocks on WARN for this label and routes DEGRADED down its
        non-blocking path, so an unparseable file must never arrive as WARN."""
        findings = self._findings("!!! not python\n")
        self.assertNotIn("WARN", [f[0] for f in findings])
        self.assertNotIn("FAIL", [f[0] for f in findings])

    # -- offsets ---------------------------------------------------------------
    def test_a_call_after_non_ascii_text_is_read_at_the_right_offset(self):
        """`ast` column offsets are UTF-8 *byte* offsets, not character ones, so
        a line carrying accented text before the call shifts every later index
        unless the prefix is re-decoded."""
        src = ('label = "caf' + chr(0xE9) + " na" + chr(0xEF) + 've"\n'
               'open(p, "w", encoding="utf-8")\n')
        findings = self._findings(src)
        self.assertEqual([f[0] for f in findings], ["WARN"])
        self.assertIn("newline=", findings[0][2])

    def test_a_parenthesis_in_a_trailing_comment_is_not_the_call(self):
        src = 'open  # see (note)\nif False:\n    pass\n'
        self.assertEqual(self._findings(src), [])


class TestHiddenDirWalk(unittest.TestCase):
    """The blind-spot fix: iter_text_files must descend into authored hidden
    config dirs (.codex, .github, ...) while still pruning system/tooling
    dot-dirs (SKIP_DIRS) and the verbatim-data exemptions."""

    def test_scans_hidden_config_dir_but_skips_system_and_exempt_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            # Authored hidden config dir with a tracked text file, plus a
            # nested hidden dir under it (mirrors .codex/plugins/.agents/).
            (root / ".codex").mkdir()
            (root / ".codex" / "config.toml").write_bytes(b"ok\n")
            (root / ".codex" / ".sub").mkdir()
            (root / ".codex" / ".sub" / "CONTEXT.md").write_bytes(b"ok\n")

            # A normal tracked file at the root.
            (root / "README.md").write_bytes(b"ok\n")

            # A system dot-dir holding a text-extension file: must stay skipped.
            (root / ".venv").mkdir()
            (root / ".venv" / "pyvenv.cfg").write_bytes(b"x\n")

            # A newly denylisted IDE dir: must stay skipped.
            (root / ".vscode").mkdir()
            (root / ".vscode" / "settings.json").write_bytes(b"{}\n")

            # A verbatim-data exemption: must stay pruned.
            (root / "raw").mkdir()
            (root / "raw" / "scraped.md").write_bytes(b"x\n")

            found = {p.relative_to(root).as_posix() for p in run.iter_text_files(root)}

        self.assertIn(".codex/config.toml", found)
        self.assertIn(".codex/.sub/CONTEXT.md", found)
        self.assertIn("README.md", found)
        self.assertNotIn(".venv/pyvenv.cfg", found)
        self.assertNotIn(".vscode/settings.json", found)
        self.assertNotIn("raw/scraped.md", found)


class TestSourceIsPureAscii(unittest.TestCase):
    def test_guard_source_is_ascii(self):
        data = (SCRIPTS_DIR / "run.py").read_bytes()
        data.decode("ascii")  # raises if any non-ASCII byte is present


if __name__ == "__main__":
    unittest.main(verbosity=2)
