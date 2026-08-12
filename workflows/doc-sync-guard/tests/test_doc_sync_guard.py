#!/usr/bin/env python3
"""Unit and integration tests for the doc-sync guard.

Pure helpers (CONTEXT parsing, LOG timestamp parsing, ownership, diff parsing)
are tested with fixtures. The end-to-end behaviour is tested against real
throwaway git repositories built in a temp directory, so scope collection,
ownership, and the mtime-based LOG check are exercised the way they run for
real. Git is required (as it is for the guard itself).
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import context_parse  # noqa: E402
import logtime  # noqa: E402
import run  # noqa: E402


# ---------------------------------------------------------------------------
# Pure: context_parse
# ---------------------------------------------------------------------------
CONTEXT_TEMPLATE = """# Foo

**Last modified:** {last_modified}

## Purpose
{purpose}

## Contents
None

## Revision History
{archive}{entries}
"""


def make_context(last_modified="2026-07-06", entries=("2026-07-06",),
                 purpose="A thing.", archive=""):
    body = "\n".join(f"- {d} - Entry for {d}." for d in entries)
    return CONTEXT_TEMPLATE.format(
        last_modified=last_modified, purpose=purpose, archive=archive, entries=body)


class TestContextParse(unittest.TestCase):
    def test_last_modified_date(self):
        text = make_context(last_modified="2026-07-06")
        self.assertEqual(context_parse.last_modified_date(text), "2026-07-06")

    def test_last_modified_missing(self):
        self.assertIsNone(context_parse.last_modified_date("# Foo\n\nno date\n"))

    def test_revision_history_dates_in_order(self):
        text = make_context(entries=("2026-07-01", "2026-07-04", "2026-07-06"))
        self.assertEqual(
            context_parse.revision_history_dates(text),
            ["2026-07-01", "2026-07-04", "2026-07-06"])

    def test_newest_revision_history_date(self):
        text = make_context(entries=("2026-07-01", "2026-07-06", "2026-07-04"))
        self.assertEqual(context_parse.newest_revision_history_date(text),
                         "2026-07-06")

    def test_archive_line_is_ignored(self):
        # An archive reference line dated far in the future must not be counted.
        text = make_context(
            entries=("2026-07-01", "2026-07-06"),
            archive="Earlier history archived to LOG.md on 2099-01-01.\n")
        self.assertEqual(context_parse.newest_revision_history_date(text),
                         "2026-07-06")

    def test_section_bounded_by_next_heading(self):
        text = (
            "# Foo\n\n## Revision History\n- 2026-07-06 - Entry.\n\n"
            "## Something Else\n- 2099-01-01 - not an RH entry\n")
        self.assertEqual(context_parse.revision_history_dates(text),
                         ["2026-07-06"])

    def test_bracketed_entry_is_counted(self):
        text = (
            "# Foo\n\n**Last modified:** 2026-07-06\n\n## Revision History\n"
            "- [2026-07-06] - bracketed entry\n")
        self.assertEqual(context_parse.revision_history_dates(text),
                         ["2026-07-06"])

    def test_gained_rh_entry_true_when_count_grows(self):
        old = make_context(entries=("2026-07-01",))
        new = make_context(entries=("2026-07-01", "2026-07-06"))
        self.assertTrue(context_parse.gained_rh_entry(old, new))

    def test_gained_rh_entry_false_when_count_same(self):
        old = make_context(entries=("2026-07-01",))
        new = make_context(entries=("2026-07-01",), purpose="Changed prose only.")
        self.assertFalse(context_parse.gained_rh_entry(old, new))

    def test_gained_rh_entry_same_day_second_entry(self):
        # A legitimate same-day edit adds a second entry with the same date; the
        # count still grows, so it must register as gained (a set diff would not).
        old = make_context(entries=("2026-07-01",))
        new = make_context(entries=("2026-07-01", "2026-07-01"))
        self.assertTrue(context_parse.gained_rh_entry(old, new))

    def test_gained_rh_entry_ignores_dated_bullet_outside_section(self):
        # The finding-2 regression: a dated bullet added under Contents (not the
        # Revision History section) must NOT count as a gained entry.
        old = make_context(entries=("2026-07-01",))
        new = (
            "# Foo\n\n**Last modified:** 2026-07-01\n\n## Contents\n"
            "- 2026-07-06 - a dated thing that is not an RH entry\n\n"
            "## Revision History\n- 2026-07-01 - Entry for 2026-07-01.\n")
        self.assertFalse(context_parse.gained_rh_entry(old, new))

    def test_gained_rh_entry_untracked_new_file(self):
        new = make_context(entries=("2026-07-06",))
        self.assertTrue(context_parse.gained_rh_entry("", new))

    def test_gained_rh_entry_false_when_old_entry_date_edited_forward(self):
        # The remaining review finding: editing an existing entry's date forward
        # (no line appended) must NOT count as a gained entry. A date-only
        # comparison returned True here because the newest date advanced.
        old = make_context(last_modified="2026-07-01", entries=("2026-07-01",))
        new = make_context(last_modified="2026-07-07", entries=("2026-07-07",))
        self.assertFalse(context_parse.gained_rh_entry(old, new))

    def test_gained_rh_entry_false_when_old_entry_text_edited(self):
        # Editing an entry's text without changing its date is also not a gain.
        old = (
            "# Foo\n\n**Last modified:** 2026-07-01\n\n## Revision History\n"
            "- 2026-07-01 - Original wording.\n")
        new = (
            "# Foo\n\n**Last modified:** 2026-07-01\n\n## Revision History\n"
            "- 2026-07-01 - Corrected wording.\n")
        self.assertFalse(context_parse.gained_rh_entry(old, new))

    def test_gained_rh_entry_flat_count_archive_plus_add(self):
        # Archive exactly one and add one: count is flat, but the archive
        # reference line was added, so a genuine new entry was gained.
        old = make_context(entries=("2026-07-01", "2026-07-02"))
        new = make_context(entries=("2026-07-02", "2026-07-06"),
                           archive="Earlier history archived to LOG.md on "
                                   "2026-07-06.\n")
        self.assertEqual(len(context_parse.revision_history_entry_lines(new)),
                         len(context_parse.revision_history_entry_lines(old)))
        self.assertTrue(context_parse.gained_rh_entry(old, new))

    def test_gained_rh_entry_archive_plus_add_does_not_grow_count(self):
        # A change adds a newer entry AND archives the oldest ones, so the count
        # does not grow (here it drops) but the newest date advances. The
        # count test alone would miss it; the newest-date test must catch it.
        old = make_context(entries=("2026-07-01", "2026-07-02", "2026-07-03"))
        new = make_context(entries=("2026-07-03", "2026-07-06"),
                           archive="Earlier history archived to LOG.md on "
                                   "2026-07-06.\n")
        self.assertLessEqual(len(context_parse.revision_history_dates(new)),
                             len(context_parse.revision_history_dates(old)))
        self.assertTrue(context_parse.gained_rh_entry(old, new))

    def test_gained_rh_entry_false_when_archive_line_masks_inplace_edit(self):
        # Third-review finding: an in-place edit of the only entry, dressed up
        # with an "Earlier history archived..." line, must NOT read as a gained
        # entry. A changed archive reference line alone does not prove an entry
        # was appended, so the previous archive branch passed this silently.
        old = (
            "# Foo\n\n**Last modified:** 2026-07-01\n\n## Revision History\n"
            "- 2026-07-01 - Original wording.\n")
        new = (
            "# Foo\n\n**Last modified:** 2026-07-07\n\n## Revision History\n"
            "Earlier history archived to LOG.md on 2026-07-07.\n"
            "- 2026-07-07 - Original wording edited, not a new appended entry.\n")
        self.assertFalse(context_parse.gained_rh_entry(old, new))

    def test_gained_rh_entry_false_when_all_entries_archived(self):
        # Archiving *every* old entry and replacing it is indistinguishable from
        # an in-place rewrite plus a bogus archive line, so it biases to "not
        # gained" (a loud warning) rather than trusting the archive claim.
        old = make_context(entries=("2026-07-01",))
        new = make_context(entries=("2026-07-07",),
                           archive="Earlier history archived to LOG.md on "
                                   "2026-07-07.\n")
        self.assertFalse(context_parse.gained_rh_entry(old, new))

    def test_gained_rh_entry_archive_plus_append_keeps_a_suffix(self):
        # The genuine archive-plus-append shape that must still count: trim the
        # oldest entries, keep a non-empty suffix unchanged, append a newer one,
        # and add the archive reference line.
        old = make_context(entries=("2026-07-01", "2026-07-02", "2026-07-03"))
        new = make_context(entries=("2026-07-02", "2026-07-03", "2026-07-07"),
                           archive="Earlier history archived to LOG.md on "
                                   "2026-07-07.\n")
        self.assertTrue(context_parse.gained_rh_entry(old, new))

    def test_revision_history_dates_ignores_indented_bullet(self):
        # A dated bullet indented under a real entry is a sub-bullet, not a
        # separate Revision History entry, so its date must not be collected.
        text = (
            "# Foo\n\n## Revision History\n"
            "- 2026-07-06 - top-level entry\n"
            "  - 2026-07-02 - indented detail, not an entry\n")
        self.assertEqual(context_parse.revision_history_dates(text),
                         ["2026-07-06"])

    def test_gained_rh_entry_ignores_indented_sub_bullet(self):
        # Adding only an indented dated sub-bullet under the existing entry is
        # not a new top-level entry, so it must not read as a gain (the anchored
        # entry regex is what prevents the silent miss here).
        old = make_context(entries=("2026-07-01",))
        new = (
            "# Foo\n\n**Last modified:** 2026-07-01\n\n## Revision History\n"
            "- 2026-07-01 - Entry for 2026-07-01.\n"
            "  - 2026-07-02 - indented detail under the entry\n")
        self.assertFalse(context_parse.gained_rh_entry(old, new))

    def test_gained_rh_entry_false_on_reorder_edit_with_fake_archive(self):
        # Moving an edited old entry to the bottom with a bogus archive line looks
        # structurally like archive-plus-append, but the "appended" entry is
        # dated older than the retained ones, so it is not a genuine new entry -
        # it is the oldest entry rewritten and moved down. Must not read as a gain.
        old = make_context(entries=("2026-07-01", "2026-07-02", "2026-07-03"))
        new = (
            "# Foo\n\n**Last modified:** 2026-07-03\n\n## Revision History\n"
            "Earlier history archived to LOG.md on 2026-07-07.\n"
            "- 2026-07-02 - Entry for 2026-07-02.\n"
            "- 2026-07-03 - Entry for 2026-07-03.\n"
            "- 2026-07-01 - Entry for 2026-07-01 rewritten and moved down.\n")
        self.assertFalse(context_parse.gained_rh_entry(old, new))


class SameDayReArchiveTest(unittest.TestCase):
    """Guard-coverage stage 14b: the same-day re-archive false positive.

    `gained_rh_entry` used to ask whether the archive reference line *changed*,
    which reads an unchanged line as proof that no archiving happened. A
    directory archived twice on the same date leaves that line already reading
    that date, so a genuine archive-plus-append was reported as an in-place
    edit. The question is now whether an archive was *recorded* and did not move
    backwards, with `_is_archive_plus_append` left to reject an in-place edit.

    The positive control below is the defect. The negatives are what the "must
    have changed" test used to buy, and each one has to keep holding without it.
    """

    ARCHIVE_07 = "Earlier history archived to LOG.md on 2026-07-07.\n"

    def test_same_day_re_archive_is_a_gain(self):
        # The defect, at parser level: the archive line already reads 2026-07-07
        # from an earlier archive on the same day, so the second archive leaves
        # it untouched while genuinely trimming the oldest entry and appending a
        # new one. Fails on the pre-14b code, which sees an unchanged line.
        old = make_context(entries=("2026-07-01", "2026-07-02", "2026-07-03"),
                           archive=self.ARCHIVE_07)
        new = make_context(entries=("2026-07-02", "2026-07-03", "2026-07-07"),
                           archive=self.ARCHIVE_07)
        self.assertTrue(context_parse.gained_rh_entry(old, new))

    def test_inplace_edit_under_a_preexisting_archive_line_is_not_a_gain(self):
        # The regression that matters most: the 2026-07-07 silent miss in the
        # shape 14b creates, where the archive line is pre-existing and unchanged
        # rather than newly added. Dropping the "must have changed" test is only
        # safe because `_is_archive_plus_append` rejects this shape - no old
        # entry survives, so there is nothing an append could follow.
        old = make_context(entries=("2026-07-01",), archive=self.ARCHIVE_07)
        new = make_context(entries=("2026-07-07",), archive=self.ARCHIVE_07,
                           purpose="Edited in place, not appended.")
        self.assertFalse(context_parse.gained_rh_entry(old, new))

    def test_inplace_edit_of_newest_entry_under_an_archive_line_is_not_a_gain(self):
        # The same miss with more than one entry, so the retained-suffix search
        # has somewhere to look: the newest entry is edited in place and the
        # retained prefix therefore does not line up. Must still not be a gain.
        old = make_context(entries=("2026-07-01", "2026-07-02"),
                           archive=self.ARCHIVE_07)
        new = make_context(entries=("2026-07-01", "2026-07-07"),
                           archive=self.ARCHIVE_07)
        self.assertFalse(context_parse.gained_rh_entry(old, new))

    def test_entries_removed_with_no_archive_line_is_not_a_gain(self):
        # Entries vanished with nothing recording that they were archived: a
        # genuine schema violation, and 7 of the 41 historical warnings. Without
        # the "an archive must be recorded" clause this shape would newly pass.
        old = make_context(entries=("2026-07-01", "2026-07-02"))
        new = make_context(entries=("2026-07-02", "2026-07-07"))
        self.assertFalse(context_parse.gained_rh_entry(old, new))

    def test_archive_date_moved_backwards_is_not_a_gain(self):
        # An archive line whose date went backwards is not a record of an
        # archive that just happened. This tightens behaviour: the old code read
        # any changed line as evidence and passed this shape.
        old = make_context(entries=("2026-07-01", "2026-07-02"),
                           archive="Earlier history archived to LOG.md on "
                                   "2026-07-09.\n")
        new = make_context(entries=("2026-07-02", "2026-07-07"),
                           archive=self.ARCHIVE_07)
        self.assertFalse(context_parse.gained_rh_entry(old, new))

    def test_archive_line_with_no_readable_date_is_not_a_gain(self):
        # A malformed archive line records nothing that can be compared, so it
        # biases to a loud warning like every other ambiguous shape here. Also a
        # tightening: the old code counted the line's mere appearance.
        old = make_context(entries=("2026-07-01", "2026-07-02"))
        new = make_context(entries=("2026-07-02", "2026-07-07"),
                           archive="Earlier history archived to LOG.md.\n")
        self.assertFalse(context_parse.gained_rh_entry(old, new))

    def test_first_archive_still_reads_as_a_gain(self):
        # The ordinary first archive, where no line existed before: there is no
        # older date to compare against, so a missing old date must not be read
        # as a backwards move.
        old = make_context(entries=("2026-07-01", "2026-07-02", "2026-07-03"))
        new = make_context(entries=("2026-07-02", "2026-07-03", "2026-07-07"),
                           archive=self.ARCHIVE_07)
        self.assertTrue(context_parse.gained_rh_entry(old, new))


# ---------------------------------------------------------------------------
# Pure: logtime
# ---------------------------------------------------------------------------
class TestLogTime(unittest.TestCase):
    def test_newest_entry_is_last(self):
        text = (
            "# Foo - Log\n\n"
            "[2026-07-01T10:00:00+01:00] | Actor: Biblio | Action: created | Note: a.\n"
            "[2026-07-06T20:00:00+01:00] | Actor: Biblio | Action: modified | Note: b.\n")
        dt = logtime.newest_log_datetime(text)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 7)
        self.assertEqual(dt.day, 6)

    def test_legacy_date_only_entry(self):
        text = "[2026-05-27] | Actor: Biblio | Action: modified | Note: legacy.\n"
        dt = logtime.newest_log_datetime(text)
        self.assertIsNotNone(dt)
        self.assertEqual(dt.day, 27)

    def test_no_parseable_entry(self):
        self.assertIsNone(logtime.newest_log_datetime("# Foo - Log\n\nnothing here\n"))

    def test_log_is_behind(self):
        self.assertTrue(logtime.log_is_behind(100.0, 200.0, tolerance=2.0))

    def test_log_not_behind_within_tolerance(self):
        self.assertFalse(logtime.log_is_behind(199.0, 200.0, tolerance=2.0))

    def test_log_ahead_not_behind(self):
        self.assertFalse(logtime.log_is_behind(300.0, 200.0, tolerance=2.0))

    def test_missing_log_epoch_is_behind(self):
        self.assertTrue(logtime.log_is_behind(None, 200.0))


# ---------------------------------------------------------------------------
# Pure: ownership and diff parsing
# ---------------------------------------------------------------------------
class TestOwnership(unittest.TestCase):
    def setUp(self):
        self.ctx_dirs = {"", "workflows", "workflows/foo", "workflows/foo/scripts"}

    def test_owner_is_immediate_dir(self):
        self.assertEqual(
            run.owner_dir("workflows/foo/scripts/run.py", self.ctx_dirs),
            "workflows/foo/scripts")

    def test_owner_walks_up_when_no_context(self):
        self.assertEqual(
            run.owner_dir("workflows/foo/data/x.json", self.ctx_dirs),
            "workflows/foo")

    def test_committable_context_dirs(self):
        paths = ["workflows/CONTEXT.md", "workflows/foo/CONTEXT.md",
                 "workflows/foo/run.py", "README.md"]
        self.assertEqual(run.committable_context_dirs(paths),
                         {"workflows", "workflows/foo"})

    def test_deleted_context_dirs(self):
        changes = [
            ("D", "workflows/foo/CONTEXT.md", False),
            ("D", "workflows/foo/run.py", False),
            ("M", "workflows/bar/CONTEXT.md", False),
        ]
        self.assertEqual(run.deleted_context_dirs(changes), {"workflows/foo"})

    def test_path_under_dir(self):
        self.assertTrue(run.path_under_dir("workflows/foo/run.py",
                                           "workflows/foo"))
        self.assertFalse(run.path_under_dir("workflows/foobar/run.py",
                                            "workflows/foo"))


class TestDiffParsing(unittest.TestCase):
    def test_name_status_simple(self):
        out = "M\0workflows/foo/run.py\0A\0workflows/foo/new.py\0"
        self.assertEqual(
            run._parse_name_status(out),
            [("M", "workflows/foo/run.py"), ("A", "workflows/foo/new.py")])

    def test_name_status_rename_keys_on_new_path(self):
        # Defensive: callers pass --no-renames so R/C rarely appear, but the
        # parser still keys a rename on the new path if one does.
        out = "R100\0old/path.py\0new/path.py\0"
        self.assertEqual(run._parse_name_status(out), [("R", "new/path.py")])


# ---------------------------------------------------------------------------
# Integration: real throwaway git repositories
# ---------------------------------------------------------------------------
def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True, encoding="utf-8")


def _iso(epoch):
    return datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


class RepoFixture:
    """A tiny git repo with a documented workflow directory tree."""

    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="docsync-test-"))
        _git(self.dir, "init", "-q")
        _git(self.dir, "config", "user.email", "t@example.com")
        _git(self.dir, "config", "user.name", "Test")
        self.write(".gitignore", "**/LOG.md\nignored/\n")
        # workflows/ and workflows/foo/ as documented directories.
        self.write("workflows/CONTEXT.md",
                   make_context(purpose="Workflows container."))
        self.write("workflows/LOG.md", "# Workflows - Log\n")
        self.write("workflows/foo/CONTEXT.md",
                   make_context(last_modified="2026-07-01",
                                entries=("2026-07-01",), purpose="Foo does a thing."))
        self.write("workflows/foo/LOG.md",
                   "[2026-07-01T09:00:00+01:00] | Actor: Biblio | "
                   "Action: created | Note: init.\n")
        self.write("workflows/foo/run.py", "print('v1')\n")
        _git(self.dir, "add", "-A")
        _git(self.dir, "commit", "-q", "-m", "base", "--no-verify")

    def write(self, rel, text):
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))

    def append_log(self, rel, epoch):
        p = self.dir / rel
        line = (f"[{_iso(epoch)}] | Actor: Biblio | Action: modified | "
                f"Note: change.\n")
        with p.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(line)

    def set_mtime(self, rel, epoch):
        os.utime(self.dir / rel, (epoch, epoch))

    def check(self, **kw):
        return run.run_check(self.dir, **kw)

    def cleanup(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)


class IntegrationTest(unittest.TestCase):
    def setUp(self):
        self.repo = RepoFixture()
        self.now = time.time()

    def tearDown(self):
        self.repo.cleanup()

    def _messages(self, findings):
        return " || ".join(m for _, _, m in findings)

    # --- CONTEXT cases ---
    def test_clean_change_passes(self):
        r = self.repo
        r.write("workflows/foo/run.py", "print('v2')\n")
        r.write("workflows/foo/CONTEXT.md",
                make_context(last_modified="2026-07-06",
                             entries=("2026-07-01", "2026-07-06"),
                             purpose="Foo does a thing, now v2."))
        r.set_mtime("workflows/foo/run.py", self.now)
        r.set_mtime("workflows/foo/CONTEXT.md", self.now)
        r.append_log("workflows/foo/LOG.md", self.now + 5)
        self.assertEqual(r.check(), [])

    def test_context_not_updated_warns(self):
        r = self.repo
        r.write("workflows/foo/run.py", "print('v2')\n")
        r.set_mtime("workflows/foo/run.py", self.now)
        r.append_log("workflows/foo/LOG.md", self.now + 5)
        msgs = self._messages(r.check())
        self.assertIn("CONTEXT.md not updated", msgs)

    def test_context_changed_without_rh_entry_warns(self):
        r = self.repo
        r.write("workflows/foo/run.py", "print('v2')\n")
        # Change CONTEXT purpose but add no RH entry; keep dates internally
        # consistent so only the missing-entry finding fires.
        r.write("workflows/foo/CONTEXT.md",
                make_context(last_modified="2026-07-01", entries=("2026-07-01",),
                             purpose="Foo does a different thing."))
        r.set_mtime("workflows/foo/run.py", self.now)
        r.set_mtime("workflows/foo/CONTEXT.md", self.now)
        r.append_log("workflows/foo/LOG.md", self.now + 5)
        msgs = self._messages(r.check())
        self.assertIn("Revision History gained no entry", msgs)

    def test_editing_old_rh_entry_forward_warns(self):
        # A real content change where the only RH edit moves the existing entry's
        # date forward instead of appending a new one. Last modified stays
        # consistent with the (edited) newest date, so ONLY the gained-no-entry
        # finding should fire - the review's silent-miss case, now caught.
        r = self.repo
        r.write("workflows/foo/run.py", "print('v2')\n")
        r.write("workflows/foo/CONTEXT.md",
                make_context(last_modified="2026-07-06", entries=("2026-07-06",),
                             purpose="Foo v2, entry edited not appended."))
        r.set_mtime("workflows/foo/run.py", self.now)
        r.set_mtime("workflows/foo/CONTEXT.md", self.now)
        r.append_log("workflows/foo/LOG.md", self.now + 5)
        msgs = self._messages(r.check())
        self.assertIn("Revision History gained no entry", msgs)
        self.assertNotIn("Last modified", msgs)

    def test_archive_line_masking_inplace_edit_warns(self):
        # The third review's silent-miss case end to end: the only Revision
        # History change edits the existing entry in place and adds an archive
        # reference line, but appends no genuine new entry. It must be flagged as
        # gaining no entry, not pass silently on the strength of the archive line.
        r = self.repo
        r.write("workflows/foo/run.py", "print('v2')\n")
        r.write("workflows/foo/CONTEXT.md",
                make_context(last_modified="2026-07-06", entries=("2026-07-06",),
                             purpose="Foo v2, entry edited with a bogus archive.",
                             archive="Earlier history archived to LOG.md on "
                                     "2026-07-06.\n"))
        r.set_mtime("workflows/foo/run.py", self.now)
        r.set_mtime("workflows/foo/CONTEXT.md", self.now)
        r.append_log("workflows/foo/LOG.md", self.now + 5)
        msgs = self._messages(r.check())
        self.assertIn("Revision History gained no entry", msgs)
        self.assertNotIn("Last modified", msgs)

    def test_last_modified_mismatch_warns(self):
        r = self.repo
        r.write("workflows/foo/run.py", "print('v2')\n")
        # Add an RH entry dated 2026-07-06 but leave Last modified at 2026-07-01.
        r.write("workflows/foo/CONTEXT.md",
                make_context(last_modified="2026-07-01",
                             entries=("2026-07-01", "2026-07-06"),
                             purpose="Foo v2."))
        r.set_mtime("workflows/foo/run.py", self.now)
        r.set_mtime("workflows/foo/CONTEXT.md", self.now)
        r.append_log("workflows/foo/LOG.md", self.now + 5)
        msgs = self._messages(r.check())
        self.assertIn("Last modified", msgs)
        self.assertIn("does not match", msgs)

    def test_same_day_edit_passes(self):
        r = self.repo
        # Base RH newest is 2026-07-01 with Last modified 2026-07-01; a same-day
        # edit adds another 2026-07-01 entry and leaves Last modified 2026-07-01.
        r.write("workflows/foo/run.py", "print('v2')\n")
        r.write("workflows/foo/CONTEXT.md",
                make_context(last_modified="2026-07-01",
                             entries=("2026-07-01", "2026-07-01"),
                             purpose="Foo v2 same day."))
        r.set_mtime("workflows/foo/run.py", self.now)
        r.set_mtime("workflows/foo/CONTEXT.md", self.now)
        r.append_log("workflows/foo/LOG.md", self.now + 5)
        self.assertEqual(r.check(), [])

    # --- untracked / ownership (R2-2) ---
    def test_untracked_new_file_needs_context(self):
        r = self.repo
        r.write("workflows/foo/helper.py", "print('new')\n")  # untracked
        r.set_mtime("workflows/foo/helper.py", self.now)
        r.append_log("workflows/foo/LOG.md", self.now + 5)
        msgs = self._messages(r.check())
        self.assertIn("CONTEXT.md not updated", msgs)

    def test_new_untracked_directory_owns_itself(self):
        r = self.repo
        # A brand-new untracked workflow dir with its own CONTEXT/LOG/script,
        # all internally consistent -> the new dir must NOT be charged to its
        # tracked parent, and (being self-consistent) produces no own finding.
        r.write("workflows/bar/CONTEXT.md",
                make_context(last_modified="2026-07-06", entries=("2026-07-06",),
                             purpose="Bar, brand new."))
        r.write("workflows/bar/run.py", "print('bar')\n")
        r.write("workflows/bar/LOG.md", "")  # created below with a fresh entry
        r.set_mtime("workflows/bar/run.py", self.now)
        r.set_mtime("workflows/bar/CONTEXT.md", self.now)
        r.append_log("workflows/bar/LOG.md", self.now + 5)
        # The parent workflows/CONTEXT.md was NOT updated, so the only expected
        # finding is the coarse parent-propagation one against workflows/.
        findings = r.check()
        msgs = self._messages(findings)
        self.assertNotIn("workflows/bar: CONTEXT.md not updated", msgs)
        self.assertIn("added child directory", msgs)
        self.assertIn("workflows/bar", msgs)

    def test_parent_propagation_satisfied_when_parent_updated(self):
        r = self.repo
        r.write("workflows/bar/CONTEXT.md",
                make_context(last_modified="2026-07-06", entries=("2026-07-06",),
                             purpose="Bar, brand new."))
        r.write("workflows/bar/run.py", "print('bar')\n")
        r.write("workflows/bar/LOG.md", "")
        # Update the parent Contents + Revision History too. The parent's own
        # entry must genuinely be NEW: this fixture used to rewrite the prose
        # while leaving the entry line byte-identical, which passed only because
        # the parent - whose sole changed file is its own CONTEXT.md - opened no
        # case at all before stage 14. That is the defect this suite now covers,
        # so the fixture has to describe a correct propagation rather than the
        # one the guard used to be unable to see.
        r.write("workflows/CONTEXT.md",
                make_context(last_modified="2026-07-07",
                             entries=("2026-07-06", "2026-07-07"),
                             purpose="Workflows container; now includes bar."))
        for f in ("workflows/bar/run.py", "workflows/bar/CONTEXT.md",
                  "workflows/CONTEXT.md"):
            r.set_mtime(f, self.now)
        r.append_log("workflows/bar/LOG.md", self.now + 5)
        r.append_log("workflows/LOG.md", self.now + 5)
        self.assertEqual(r.check(), [])

    # --- rename / move (finding 1) ---
    def test_move_between_dirs_flags_both_source_and_dest(self):
        # A move changes BOTH directories: the source loses content, the dest
        # gains it. --no-renames turns the move into D (source) + A (dest) so
        # both owners are checked; keying only on the new path would miss baz.
        r = self.repo
        r.write("workflows/baz/CONTEXT.md",
                make_context(last_modified="2026-07-01", entries=("2026-07-01",),
                             purpose="Baz."))
        r.write("workflows/baz/LOG.md",
                "[2026-07-01T09:00:00+01:00] | Actor: Biblio | "
                "Action: created | Note: init.\n")
        r.write("workflows/baz/data.txt", "hello\n")
        _git(r.dir, "add", "-A")
        _git(r.dir, "commit", "-q", "-m", "add baz", "--no-verify")
        # Move the data file baz -> foo without touching either CONTEXT.md.
        _git(r.dir, "mv", "workflows/baz/data.txt", "workflows/foo/data.txt")
        r.set_mtime("workflows/foo/data.txt", self.now)
        msgs = self._messages(r.check())
        self.assertIn("workflows/baz: CONTEXT.md not updated", msgs)
        self.assertIn("workflows/foo: CONTEXT.md not updated", msgs)

    def test_removed_child_directory_flags_parent(self):
        # Removing a child directory (its CONTEXT.md disappears) must flag the
        # parent's coarse Contents propagation without also producing an
        # impossible own-directory warning for the deleted child CONTEXT.md.
        r = self.repo
        for f in ("workflows/foo/CONTEXT.md", "workflows/foo/LOG.md",
                  "workflows/foo/run.py"):
            os.remove(r.dir / f)
        msgs = self._messages(r.check())
        self.assertIn("removed child directory", msgs)
        self.assertIn("workflows/foo", msgs)
        self.assertNotIn("workflows/foo: CONTEXT.md changed", msgs)
        self.assertNotIn("workflows/foo: CONTEXT.md not updated", msgs)

    def test_git_failure_is_warn_not_info(self):
        # A scan that did not run must not look clean to consumers. The guard
        # reports it as a doc-sync WARN, which audit/close-out/handoff/precommit
        # already surface.
        with mock.patch.object(run, "changed_name_status",
                               side_effect=run.GitError("git unavailable")):
            findings = run.run_check(self.repo.dir)
        self.assertEqual(findings, [
            ("WARN", "doc-sync", "scan skipped - git unavailable")
        ])

    # --- LOG (mtime) cases ---
    def test_log_behind_warns(self):
        r = self.repo
        r.write("workflows/foo/run.py", "print('v2')\n")
        r.write("workflows/foo/CONTEXT.md",
                make_context(last_modified="2026-07-06",
                             entries=("2026-07-01", "2026-07-06"), purpose="v2"))
        r.set_mtime("workflows/foo/run.py", self.now)
        r.set_mtime("workflows/foo/CONTEXT.md", self.now)
        # LOG newest entry is older than the changed files -> behind.
        r.append_log("workflows/foo/LOG.md", self.now - 100)
        msgs = self._messages(r.check())
        self.assertIn("LOG.md has no entry for this change", msgs)

    def test_trailing_source_edit_after_log_is_caught(self):
        # R2-1: source -> CONTEXT -> LOG -> one more source edit. The final
        # source edit post-dates the LOG; max(CONTEXT, source) must catch it.
        r = self.repo
        r.write("workflows/foo/run.py", "print('v2')\n")
        r.write("workflows/foo/CONTEXT.md",
                make_context(last_modified="2026-07-06",
                             entries=("2026-07-01", "2026-07-06"), purpose="v2"))
        r.set_mtime("workflows/foo/CONTEXT.md", self.now)
        r.append_log("workflows/foo/LOG.md", self.now + 5)
        # The trailing source edit lands AFTER the log entry.
        r.set_mtime("workflows/foo/run.py", self.now + 60)
        msgs = self._messages(r.check())
        self.assertIn("LOG.md has no entry for this change", msgs)

    def test_deleted_file_skipped_in_reference(self):
        # A deletion-only change: remove a tracked file. It has no mtime; the
        # reference falls back to CONTEXT.md's mtime, and a fresh LOG passes.
        r = self.repo
        os.remove(r.dir / "workflows/foo/run.py")
        r.write("workflows/foo/CONTEXT.md",
                make_context(last_modified="2026-07-06",
                             entries=("2026-07-01", "2026-07-06"),
                             purpose="Foo, run.py removed."))
        r.set_mtime("workflows/foo/CONTEXT.md", self.now)
        r.append_log("workflows/foo/LOG.md", self.now + 5)
        self.assertEqual(r.check(), [])

    def test_missing_log_warns(self):
        r = self.repo
        os.remove(r.dir / "workflows/foo/LOG.md")
        r.write("workflows/foo/run.py", "print('v2')\n")
        r.write("workflows/foo/CONTEXT.md",
                make_context(last_modified="2026-07-06",
                             entries=("2026-07-01", "2026-07-06"), purpose="v2"))
        r.set_mtime("workflows/foo/run.py", self.now)
        r.set_mtime("workflows/foo/CONTEXT.md", self.now)
        msgs = self._messages(r.check())
        self.assertIn("no LOG.md", msgs)

    # --- carve-out and contract ---
    def test_gitignored_file_is_invisible(self):
        r = self.repo
        r.write("ignored/thing.py", "print('hidden')\n")  # under ignored/
        self.assertEqual(r.check(), [])

    def test_json_contract(self):
        r = self.repo
        r.write("workflows/foo/run.py", "print('v2')\n")
        r.set_mtime("workflows/foo/run.py", self.now)
        r.append_log("workflows/foo/LOG.md", self.now + 5)
        payload = json.loads(run.findings_json(r.check()))
        self.assertIn("findings", payload)
        self.assertTrue(all(
            set(f) == {"severity", "label", "message"} for f in payload["findings"]))
        self.assertTrue(all(f["label"] == "doc-sync" for f in payload["findings"]))

    def test_staged_mode(self):
        r = self.repo
        r.write("workflows/foo/run.py", "print('v2')\n")
        _git(r.dir, "add", "workflows/foo/run.py")
        r.set_mtime("workflows/foo/run.py", self.now)
        r.append_log("workflows/foo/LOG.md", self.now + 5)
        msgs = self._messages(r.check(staged=True))
        self.assertIn("CONTEXT.md not updated", msgs)

    def test_staged_reads_index_not_working_tree(self):
        # Fix 3: in --staged mode the guard must judge the STAGED CONTEXT.md (what
        # is being committed), not the working tree. Stage a proper update, then
        # diverge the working tree by stripping the new entry without staging it.
        # The staged commit is correct, so the guard must NOT warn.
        r = self.repo
        r.write("workflows/foo/run.py", "print('v2')\n")
        r.write("workflows/foo/CONTEXT.md",
                make_context(last_modified="2026-07-06",
                             entries=("2026-07-01", "2026-07-06"),
                             purpose="Foo v2."))
        _git(r.dir, "add", "workflows/foo/run.py", "workflows/foo/CONTEXT.md")
        # Working tree now diverges: strip the new entry, leave it unstaged.
        r.write("workflows/foo/CONTEXT.md",
                make_context(last_modified="2026-07-01", entries=("2026-07-01",),
                             purpose="Foo v2."))
        r.set_mtime("workflows/foo/run.py", self.now)
        r.set_mtime("workflows/foo/CONTEXT.md", self.now)
        r.append_log("workflows/foo/LOG.md", self.now + 5)
        msgs = self._messages(r.check(staged=True))
        self.assertNotIn("gained no entry", msgs)
        self.assertNotIn("Last modified", msgs)

    def test_staged_warns_when_entry_only_in_working_tree(self):
        # The dangerous direction the old code missed: the new entry exists only
        # in the working tree, not in the staged blob, so the commit would be
        # missing it. Reading the working tree would pass silently; reading the
        # staged blob must warn.
        r = self.repo
        r.write("workflows/foo/run.py", "print('v2')\n")
        r.write("workflows/foo/CONTEXT.md",
                make_context(last_modified="2026-07-01", entries=("2026-07-01",),
                             purpose="Foo v2, entry not staged."))
        _git(r.dir, "add", "workflows/foo/run.py", "workflows/foo/CONTEXT.md")
        # Working tree gains the entry, but it is NOT staged.
        r.write("workflows/foo/CONTEXT.md",
                make_context(last_modified="2026-07-06",
                             entries=("2026-07-01", "2026-07-06"),
                             purpose="Foo v2."))
        r.set_mtime("workflows/foo/run.py", self.now)
        r.set_mtime("workflows/foo/CONTEXT.md", self.now)
        r.append_log("workflows/foo/LOG.md", self.now + 5)
        msgs = self._messages(r.check(staged=True))
        self.assertIn("Revision History gained no entry", msgs)


class ContextOnlyOwnerTest(unittest.TestCase):
    """A directory whose only changed file is its own CONTEXT.md (stage 14).

    Before this stage the guard opened no case at all for such a directory, so
    its CONTEXT.md could be edited with no new Revision History entry and a
    stale `Last modified` while the guard stayed silent. Widening the population
    routes these owners into the existing `_check_directory` assertions; it
    invents none of its own.

    Clauses A and B report at WARN, consistent with every other drift finding.
    The LOG clause (C) reports at INFO for this population only - recording
    mode - because it was never measured against a real change stream and a
    `doc-sync` WARN is a close-out hard fail. The two severity controls below
    are the ones a suite most easily skips, because what they pin looks like an
    absence of behaviour.
    """

    def setUp(self):
        self.repo = RepoFixture()
        self.now = time.time()

    def tearDown(self):
        self.repo.cleanup()

    def _messages(self, findings):
        return " || ".join(m for _, _, m in findings)

    def _edit_context(self, **kw):
        """Rewrite foo's CONTEXT.md and nothing else, then stamp its mtime."""
        self.repo.write("workflows/foo/CONTEXT.md", make_context(**kw))
        self.repo.set_mtime("workflows/foo/CONTEXT.md", self.now)

    def _log_is_current(self):
        self.repo.append_log("workflows/foo/LOG.md", self.now + 5)

    # --- one positive control per clause ---
    def test_clause_a_context_only_edit_without_rh_entry_warns(self):
        # Clause A: the CONTEXT.md changed but gained no Revision History entry.
        # Dates stay internally consistent and the LOG is current, so this is
        # the missing-entry finding on its own.
        self._edit_context(last_modified="2026-07-01", entries=("2026-07-01",),
                           purpose="Foo, described differently.")
        self._log_is_current()
        findings = self.repo.check()
        msgs = self._messages(findings)
        self.assertIn("Revision History gained no entry", msgs)
        self.assertNotIn("Last modified", msgs)

    def test_clause_b_context_only_last_modified_mismatch_warns(self):
        # Clause B, tested independently of the missing-entry case: a genuine
        # new entry IS appended, so clause A is satisfied and only the stale
        # `Last modified` is left to fire.
        self._edit_context(last_modified="2026-07-01",
                           entries=("2026-07-01", "2026-07-06"),
                           purpose="Foo v2.")
        self._log_is_current()
        findings = self.repo.check()
        msgs = self._messages(findings)
        self.assertIn("does not match", msgs)
        self.assertNotIn("gained no entry", msgs)

    def test_clause_c_context_only_edit_with_stale_log_is_reported(self):
        # Clause C: the CONTEXT.md edit is correct in itself, but the
        # directory's LOG.md has no entry for it. The clause ships switched off
        # (INFO, below) - it is still built, and an untested disabled clause
        # gets enabled later on nothing.
        self._edit_context(last_modified="2026-07-06",
                           entries=("2026-07-01", "2026-07-06"),
                           purpose="Foo v2.")
        # LOG.md deliberately left at its 2026-07-01 entry.
        msgs = self._messages(self.repo.check())
        self.assertIn("LOG.md has no entry for this change", msgs)

    # --- recording mode ---
    def test_clause_c_reports_info_and_never_warn(self):
        # Asserted on the severity rather than on the message: recording mode is
        # the whole point of the clause shipping switched off, and a message
        # match would pass just as happily at WARN.
        self._edit_context(last_modified="2026-07-06",
                           entries=("2026-07-01", "2026-07-06"),
                           purpose="Foo v2.")
        findings = self.repo.check()
        log_findings = [f for f in findings if "LOG.md" in f[2]]
        self.assertTrue(log_findings, "expected the LOG clause to report")
        for severity, _, message in log_findings:
            self.assertEqual(severity, "INFO", f"expected INFO, got {severity}: {message}")
        self.assertFalse([f for f in findings if f[0] == "WARN"],
                         f"recording mode must emit no WARN: {self._messages(findings)}")

    def test_strict_passes_over_a_recorded_finding(self):
        # The property that keeps recording mode out of close-out: `doc-sync` is
        # a blocking label there, so an INFO that tripped --strict would hard-fail
        # every close-out for a clause nobody switched on.
        self._edit_context(last_modified="2026-07-06",
                           entries=("2026-07-01", "2026-07-06"),
                           purpose="Foo v2.")
        findings = self.repo.check()
        self.assertTrue([f for f in findings if f[0] == "INFO"],
                        "expected a recorded finding to gate on")
        self.assertFalse(run.strict_failed(findings))

    # --- stage 14b, at guard level rather than parser level ---
    def test_same_day_re_archive_context_only_is_silent(self):
        # The false positive end to end, on the population that made it
        # reachable: a CONTEXT-only change that archives for the second time on
        # a date the reference line already carries. Everything else about the
        # edit is correct, so a clean guard run is the whole assertion.
        r = self.repo
        archive = "Earlier history archived to LOG.md on 2026-07-07.\n"
        r.write("workflows/foo/CONTEXT.md",
                make_context(last_modified="2026-07-03",
                             entries=("2026-07-01", "2026-07-02", "2026-07-03"),
                             archive=archive))
        _git(r.dir, "add", "-A")
        _git(r.dir, "commit", "-q", "-m", "already archived once today",
             "--no-verify")
        self._edit_context(last_modified="2026-07-07",
                           entries=("2026-07-02", "2026-07-03", "2026-07-07"),
                           archive=archive, purpose="Foo v2.")
        self._log_is_current()
        self.assertEqual(r.check(), [])

    # --- negative control ---
    def test_correct_context_only_edit_is_silent_at_every_severity(self):
        self._edit_context(last_modified="2026-07-06",
                           entries=("2026-07-01", "2026-07-06"),
                           purpose="Foo v2.")
        self._log_is_current()
        self.assertEqual(self.repo.check(), [])


if __name__ == "__main__":
    unittest.main()
