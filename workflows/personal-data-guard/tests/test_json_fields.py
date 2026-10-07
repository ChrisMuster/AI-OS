#!/usr/bin/env python3
"""Tests for the ``file`` and ``kind`` fields on the guard's findings.

The review orchestrator's trusted host check names the file and the kind of hit
to an AI from these two fields, never from the message, which holds the matched
value. So each kind is tested to carry the right file and kind, the message is
tested to be unchanged, and a finding is tested to still be the plain
three-item tuple every existing caller unpacks.

Trigger strings are assembled from fragments, as in test_personal_data_guard.py,
so this tracked file never holds a personal-looking literal.

    python workflows/personal-data-guard/tests/test_json_fields.py
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run  # noqa: E402

FAKE_USER = "j" + "faketon"
FAKE_EMAIL = "jane" + "@" + "realmail.test"
NO_MARKERS = {"name_terms": [], "denylist": []}

# (kind, text that triggers only it, the markers it needs, the message as the
# guard wrote it before these fields existed)
CASES = [
    ("email", f"contact {FAKE_EMAIL} today", NO_MARKERS,
     f"doc.md: email address `{FAKE_EMAIL}`"),
    ("home_path", "see C:/Users/" + FAKE_USER + "/thing", NO_MARKERS,
     f"doc.md: personal home path with username `{FAKE_USER}` "
     f"(`C:/Users/{FAKE_USER}`)"),
    ("os_username", f"ran as {FAKE_USER} yesterday",
     {**NO_MARKERS, "os_user": FAKE_USER},
     f"doc.md: OS username `{FAKE_USER}` present"),
    ("personal_name", "written by Jane Faketon",
     {"name_terms": ["Faketon"], "denylist": []},
     "doc.md: personal name `Faketon` present"),
    ("denylisted_term", "the town of Wobblethorpe",
     {"name_terms": [], "denylist": ["Wobblethorpe"]},
     "doc.md: denylisted personal term `Wobblethorpe` present"),
]


class FieldTests(unittest.TestCase):

    def test_every_kind_is_covered(self):
        self.assertEqual(sorted(c[0] for c in CASES), sorted(run.KINDS))

    def test_positive_control_each_hit_carries_its_file_and_kind(self):
        for kind, text, markers, _ in CASES:
            with self.subTest(kind=kind):
                findings = run.scan_text("doc.md", text, markers)
                self.assertEqual([(f.file, f.kind) for f in findings],
                                 [("doc.md", kind)])

    def test_positive_control_json_carries_file_and_kind(self):
        for kind, text, markers, _ in CASES:
            with self.subTest(kind=kind):
                rows = json.loads(run.findings_json(
                    run.scan_text("sub/doc.md", text, markers)))["findings"]
                self.assertEqual([(r["file"], r["kind"]) for r in rows],
                                 [("sub/doc.md", kind)])

    def test_negative_control_the_message_is_unchanged(self):
        for kind, text, markers, message in CASES:
            with self.subTest(kind=kind):
                (finding,) = run.scan_text("doc.md", text, markers)
                self.assertEqual(finding[2], message)

    def test_negative_control_a_finding_is_still_the_plain_tuple(self):
        _, text, markers, message = CASES[0]
        (finding,) = run.scan_text("doc.md", text, markers)
        self.assertEqual(finding, ("FAIL", "personal-data", message))
        severity, label, msg = finding
        self.assertEqual((severity, label, msg), ("FAIL", "personal-data", message))

    def test_negative_control_a_run_level_note_carries_neither_field(self):
        note = run.Finding("INFO", run.LABEL, "scan skipped - could not run git (x)")
        (row,) = json.loads(run.findings_json([note]))["findings"]
        self.assertEqual(row, {"severity": "INFO", "label": "personal-data",
                               "message": "scan skipped - could not run git (x)"})

    def test_rejection_control_an_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            run.Finding("FAIL", run.LABEL, "x", file="a.md", kind="phone")


if __name__ == "__main__":
    unittest.main()
