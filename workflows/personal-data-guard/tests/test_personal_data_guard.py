#!/usr/bin/env python3
"""Unit tests for the personal-data guard.

The pure helpers (marker derivation, e-mail allowlisting, text classification)
are exercised with in-memory fixtures using invented names, so the tests never
embed real personal data and never touch the real project tree.
"""

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import run  # noqa: E402

# Trigger strings are assembled from fragments so the literal personal-looking
# token never appears verbatim in this tracked source file - the guard would
# otherwise flag its own test fixtures (the same trick encoding-guard uses to
# avoid flagging its own corruption signatures).
FAKE_USER = "j" + "faketon"
FAKE_EMAIL = "jane" + "@" + "realmail.test"
OTHER_EMAIL = "some" + "one" + "@" + "gmail.com"
ALT_EMAIL = "x" + "@" + "y.co"


def home(prefix, sep="/"):
    return prefix + sep + FAKE_USER + sep + "thing"


# Invented markers used across the classification tests.
MARKERS = {
    "name_terms": ["Jane Faketon", "Faketon", "Jane"],
    "env_emails": [FAKE_EMAIL],
    "os_user": FAKE_USER,
    "denylist": ["Wobblethorpe", "Dinglebrook"],
}


class TestMarkerDerivation(unittest.TestCase):
    def test_name_from_user_md(self):
        text = "# User Profile\n\n**Name:** Jane Faketon\n**Location:** Nowhere\n"
        terms = run.derive_name_markers(text)
        self.assertIn("Jane Faketon", terms)
        self.assertIn("Faketon", terms)
        self.assertIn("Jane", terms)

    def test_name_drops_short_tokens(self):
        # A two-letter particle should not become its own noisy marker.
        terms = run.derive_name_markers("**Name:** Jo X de Faketon")
        self.assertNotIn("X", terms)
        self.assertNotIn("de", terms)
        self.assertIn("Faketon", terms)

    def test_no_name_line(self):
        self.assertEqual(run.derive_name_markers("nothing here"), [])
        self.assertEqual(run.derive_name_markers(""), [])

    def test_env_emails(self):
        env = f"API_KEY=abc\nOWNER_EMAIL={FAKE_EMAIL}\nOTHER={ALT_EMAIL}\n"
        emails = run.derive_env_emails(env)
        self.assertIn(FAKE_EMAIL, emails)
        self.assertIn(ALT_EMAIL, emails)

    def test_denylist_parsing(self):
        text = "# comment line\nWobblethorpe\n  Dinglebrook  # trailing\n\n"
        self.assertEqual(run.load_denylist(text), ["Wobblethorpe", "Dinglebrook"])


class TestEmailAllowlist(unittest.TestCase):
    def test_placeholders_allowed(self):
        for addr in ("you@example.com", "user@example.com",
                     "noreply@anthropic.com", "name@example.org",
                     "your-email@sample.com"):
            self.assertTrue(run._email_allowed(addr), addr)

    def test_real_email_not_allowed(self):
        self.assertFalse(run._email_allowed(FAKE_EMAIL))
        self.assertFalse(run._email_allowed(OTHER_EMAIL))


class TestHomePathDetection(unittest.TestCase):
    def test_real_username_path_flagged(self):
        for line in (home("C:\\Users", "\\"),
                     home("C:/Users"),
                     home("/home"),
                     home("/Users")):
            f = run.scan_text("f.md", line, {"name_terms": [], "denylist": []})
            self.assertTrue(any("personal home path" in m for _, _, m in f), line)

    def test_regex_fragment_not_flagged_as_username(self):
        # A path literal inside another script's regex (capture is "|" or
        # "[A-Za-z") must not be mistaken for a real account name.
        for seg in ("|", "[A-Za-z", "$1"):
            self.assertFalse(run._is_real_username(seg), seg)

    def test_placeholder_path_not_flagged(self):
        for line in ("C:/Users/Name/Desktop/AI-Work/AI-OS",
                     r"C:\Users\<username>\thing",
                     "/home/user/project"):
            f = run.scan_text("f.md", line, {"name_terms": [], "denylist": []})
            self.assertEqual([x for x in f if "home path" in x[2]], [], line)


class TestScanText(unittest.TestCase):
    def test_email_is_fail(self):
        f = run.scan_text("doc.md", f"contact {FAKE_EMAIL} today", MARKERS)
        self.assertTrue(any(s == "FAIL" and "email" in m for s, _, m in f))

    def test_allowlisted_email_clean(self):
        f = run.scan_text("doc.md", "e.g. you@example.com", MARKERS)
        self.assertEqual(f, [])

    def test_name_is_fail(self):
        f = run.scan_text("doc.md", "written by Jane Faketon", MARKERS)
        self.assertTrue(any(s == "FAIL" and "personal name" in m for s, _, m in f))

    def test_os_username_is_fail(self):
        f = run.scan_text("doc.md", f"ran as {FAKE_USER} yesterday", MARKERS)
        self.assertTrue(any(s == "FAIL" and "OS username" in m for s, _, m in f))

    def test_denylist_is_warn_not_fail(self):
        f = run.scan_text("doc.md", "the town of Wobblethorpe", MARKERS)
        warns = [x for x in f if x[2].endswith("`Wobblethorpe` present")]
        self.assertEqual(len(warns), 1)
        self.assertEqual(warns[0][0], "WARN")

    def test_word_boundary_no_substring_match(self):
        # "Jane" must not match inside "Janet" / "Janeway".
        f = run.scan_text("doc.md", "Janet flew the Janeway", MARKERS)
        self.assertEqual([x for x in f if "personal name `Jane`" in x[2]], [])

    def test_clean_text_no_findings(self):
        text = "A generic workflow that processes the user's data. See AGENTS.md."
        self.assertEqual(run.scan_text("doc.md", text, MARKERS), [])

    def test_dedupes_repeats(self):
        f = run.scan_text("doc.md", f"{FAKE_EMAIL} {FAKE_EMAIL}", MARKERS)
        emails = [x for x in f if "email" in x[2]]
        self.assertEqual(len(emails), 1)


class TestSourceStaysGeneric(unittest.TestCase):
    """The guard's own source must carry no real personal data, so it can never
    flag itself. Every concrete e-mail/path token in run.py must be a
    placeholder that its own allowlist accepts."""

    def test_run_source_is_clean_of_personal_markers(self):
        src = run.Path(run.__file__).read_text(encoding="utf-8")
        # No home path in the source captures a real (non-placeholder) username.
        for m in run.HOME_PATH_RE.finditer(src):
            self.assertFalse(run._is_real_username(m.group(1)),
                             f"non-placeholder path in source: {m.group(0)}")
        # Every e-mail literal in the source is allowlisted.
        for m in run.EMAIL_RE.finditer(src):
            self.assertTrue(run._email_allowed(m.group(0)),
                            f"non-placeholder email in source: {m.group(0)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
