#!/usr/bin/env python3
"""Unit tests for the encoding-guard script.

Fixtures are built with chr()/bytes so this test file stays pure ASCII and is
never itself flagged by the guard.
"""

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
            'Path("x.md").write_text("a", encoding="utf-8", newline="\\n")\n',
            'open(path, "w", encoding="utf-8", newline="\\n")\n',
            # csv wants newline="" - the rule is that newline is passed
            # explicitly, not that it always carries one particular value.
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
