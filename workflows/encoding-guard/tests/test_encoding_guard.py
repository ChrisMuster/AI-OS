#!/usr/bin/env python3
"""Unit tests for the encoding-guard script.

Fixtures are built with chr()/bytes so this test file stays pure ASCII and is
never itself flagged by the guard.
"""

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
            (root / ".codex" / "config.toml").write_text("ok\n", encoding="utf-8")
            (root / ".codex" / ".sub").mkdir()
            (root / ".codex" / ".sub" / "CONTEXT.md").write_text("ok\n", encoding="utf-8")

            # A normal tracked file at the root.
            (root / "README.md").write_text("ok\n", encoding="utf-8")

            # A system dot-dir holding a text-extension file: must stay skipped.
            (root / ".venv").mkdir()
            (root / ".venv" / "pyvenv.cfg").write_text("x\n", encoding="utf-8")

            # A newly denylisted IDE dir: must stay skipped.
            (root / ".vscode").mkdir()
            (root / ".vscode" / "settings.json").write_text("{}\n", encoding="utf-8")

            # A verbatim-data exemption: must stay pruned.
            (root / "raw").mkdir()
            (root / "raw" / "scraped.md").write_text("x\n", encoding="utf-8")

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
