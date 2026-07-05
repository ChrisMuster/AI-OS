#!/usr/bin/env python3
"""Unit tests for the AI-style guard.

Typographic trigger characters are built from code points (never written
literally) so this tracked source file stays plain ASCII and could not itself be
a source of the markers it hunts for. The guard also exempts its own workflow
directory, so these fixtures would not be scanned in any case.
"""

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import run  # noqa: E402

EM_DASH = chr(0x2014)
SMART_RQUOTE = chr(0x2019)
ELLIPSIS = chr(0x2026)

# A small in-memory config used by the classification tests.
CONFIG = {
    "typographic": [("em dash", EM_DASH), ("ellipsis character", ELLIPSIS)],
    "phrases": [("it's worth noting", run.re.compile(r"\bit's\s+worth\s+noting\b",
                                                     run.re.IGNORECASE))],
    "words": ["delve", "leverage"],
}
WORD_RES = [(w, run._word_re(w)) for w in CONFIG["words"]]


class TestHunkParser(unittest.TestCase):
    def test_single_addition(self):
        diff = (
            "diff --git a/foo.md b/foo.md\n"
            "--- a/foo.md\n"
            "+++ b/foo.md\n"
            "@@ -0,0 +1 @@\n"
            "+hello world\n"
        )
        added = run.parse_diff(diff)
        self.assertEqual(added["foo.md"], [(1, "hello world")])

    def test_line_numbers_track_hunk_start(self):
        diff = (
            "diff --git a/foo.md b/foo.md\n"
            "+++ b/foo.md\n"
            "@@ -10,0 +11,2 @@\n"
            "+line eleven\n"
            "+line twelve\n"
        )
        added = run.parse_diff(diff)
        self.assertEqual(added["foo.md"], [(11, "line eleven"), (12, "line twelve")])

    def test_deletions_do_not_advance_or_count(self):
        diff = (
            "diff --git a/foo.md b/foo.md\n"
            "+++ b/foo.md\n"
            "@@ -5,2 +5,1 @@\n"
            "-old one\n"
            "-old two\n"
            "+new five\n"
        )
        added = run.parse_diff(diff)
        self.assertEqual(added["foo.md"], [(5, "new five")])

    def test_multiple_files(self):
        diff = (
            "diff --git a/a.md b/a.md\n+++ b/a.md\n@@ -0,0 +1 @@\n+aaa\n"
            "diff --git a/b.md b/b.md\n+++ b/b.md\n@@ -0,0 +1 @@\n+bbb\n"
        )
        added = run.parse_diff(diff)
        self.assertEqual(added["a.md"], [(1, "aaa")])
        self.assertEqual(added["b.md"], [(1, "bbb")])

    def test_file_deletion_skipped(self):
        diff = (
            "diff --git a/gone.md b/gone.md\n"
            "--- a/gone.md\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-was here\n"
        )
        added = run.parse_diff(diff)
        self.assertNotIn("gone.md", added)
        self.assertEqual(added, {})

    def test_plus_header_not_treated_as_addition(self):
        diff = (
            "diff --git a/foo.md b/foo.md\n"
            "+++ b/foo.md\n"
            "@@ -0,0 +1 @@\n"
            "+real line\n"
        )
        added = run.parse_diff(diff)
        # The "+++ b/foo.md" header must not appear as content.
        self.assertEqual(added["foo.md"], [(1, "real line")])


class TestDetectors(unittest.TestCase):
    def test_em_dash_warns(self):
        line = "this is a clause" + EM_DASH + "and another"
        found = run.scan_line("x.md", 3, line, CONFIG, WORD_RES)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0], "WARN")
        self.assertIn("x.md:3", found[0][2])
        self.assertIn("em dash", found[0][2])

    def test_ellipsis_char_warns(self):
        found = run.scan_line("x.md", 1, "wait for it" + ELLIPSIS, CONFIG, WORD_RES)
        self.assertEqual([f[0] for f in found], ["WARN"])

    def test_three_dots_is_not_flagged(self):
        # An ASCII "..." is not the ellipsis character and must not match.
        found = run.scan_line("x.md", 1, "wait for it...", CONFIG, WORD_RES)
        self.assertEqual(found, [])

    def test_stock_phrase_warns_case_insensitive(self):
        found = run.scan_line("x.md", 7, "Now, It's Worth Noting that", CONFIG, WORD_RES)
        self.assertEqual([f[0] for f in found], ["WARN"])
        self.assertIn("stock phrase", found[0][2])

    def test_denylist_word_is_info(self):
        found = run.scan_line("x.md", 2, "let us delve into this", CONFIG, WORD_RES)
        self.assertEqual([f[0] for f in found], ["INFO"])
        self.assertIn("delve", found[0][2])

    def test_denylist_word_boundary(self):
        # "delver" / "leveraged" should still match on stem? No - whole word only.
        found = run.scan_line("x.md", 2, "the leverages were", CONFIG, WORD_RES)
        self.assertEqual(found, [])

    def test_clean_line_no_findings(self):
        found = run.scan_line("x.md", 1, "a perfectly ordinary sentence.", CONFIG, WORD_RES)
        self.assertEqual(found, [])


class TestEligibility(unittest.TestCase):
    def test_self_directory_excluded(self):
        self.assertFalse(run._eligible("workflows/ai-style-guard/config/ai-tells.yaml"))
        self.assertFalse(run._eligible("workflows/ai-style-guard/scripts/run.py"))

    def test_text_extension_included(self):
        self.assertTrue(run._eligible("README.md"))
        self.assertTrue(run._eligible("workflows/audit/scripts/run.py"))

    def test_binary_extension_excluded(self):
        self.assertFalse(run._eligible("assets/logo.png"))


class TestConfigLoader(unittest.TestCase):
    def test_loads_codepoints_and_phrases(self):
        text = (
            "typographic:\n"
            "  - {label: em dash, codepoint: 0x2014}\n"
            "phrases:\n"
            "  - {text: plays a crucial role}\n"
            "words:\n"
            "  - delve\n"
        )
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ai-tells.yaml"
            p.write_text(text, encoding="utf-8")
            cfg, info = run.load_config(p)
        self.assertEqual(info, [])
        self.assertEqual(cfg["typographic"][0], ("em dash", EM_DASH))
        self.assertTrue(cfg["phrases"][0][1].search("this plays a crucial role here"))
        self.assertEqual(cfg["words"], ["delve"])

    def test_missing_file_degrades_to_info(self):
        cfg, info = run.load_config(Path("nope/does-not-exist.yaml"))
        self.assertEqual(cfg, {"typographic": [], "phrases": [], "words": []})
        self.assertEqual(len(info), 1)
        self.assertEqual(info[0][0], "INFO")

    def test_real_project_config_is_loadable(self):
        # The shipped SSOT must parse and define markers.
        cfg, info = run.load_config(run.CONFIG_FILE)
        self.assertEqual(info, [])
        labels = [lab for lab, _ in cfg["typographic"]]
        self.assertIn("em dash", labels)
        self.assertTrue(cfg["phrases"])
        self.assertIn("delve", cfg["words"])


class BootstrapTests(unittest.TestCase):
    """This guard's job IS the check, so it must bootstrap, not degrade.

    A guard that silently skips for want of PyYAML would report a false clean.
    It must hand off to the project .venv (ensure_project_runtime), not carry a
    "degrades without pyyaml" marker like a genuinely-optional dependency.
    """

    def test_guard_bootstraps_and_does_not_degrade_on_missing_yaml(self):
        src = Path(run.__file__).read_text(encoding="utf-8")
        self.assertIn("ensure_project_runtime()", src)
        self.assertNotIn("degrades without pyyaml", src)
        self.assertNotIn("PyYAML not available", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
