#!/usr/bin/env python3
"""Tests for link-check's --link mode, and specifically for the regions it must
not rewrite.

``add_links_to_file`` TRANSFORMS rather than counts or validates, so the three
controls are stated against **the subject** - a backtick path reference that
resolves to a real project target - and read as follows:

    positive control  a LEGAL instance: an eligible reference in a walkable
                      region, which must be FOUND and linked. This is the
                      control that catches a skip written too wide, where a
                      guard against rewriting history quietly stops the tool
                      doing its job at all.
    rejection control an ILLEGAL instance: an eligible reference sitting inside
                      a dated Revision History entry, which must be REFUSED.
                      This is the real defect - before the fix it was rewritten
                      on every --link run, twice in live CONTEXT.md files.
    negative control  something that is NOT an instance of the subject: a
                      document with no Revision History section at all, so a
                      clean result is known not to be silence from the skip
                      over-matching.

The boundary tests are separate from the three controls on purpose. The section
is defined as ending at the next ``## `` heading, matching the definition the
audit's own section parser uses at ``workflows/audit/scripts/run.py:167``; the
whole point of the stage is that the two tools agree on whether history is
editable, so the boundary is a property under test rather than an implementation
detail.

Fixtures are written with ``write_bytes`` rather than ``write_text``: on Windows
a text-mode write converts every ``\\n`` to CRLF, which would put the line
endings under test instead of the property under test.

No git, no network. Link resolution reads the real repository, because
``resolve_link_target`` answers "does this path exist" against the project root
by design; the fixtures therefore cite long-lived project directories and are
written into a temporary directory that is never part of the walk.

    python workflows/link-check/tests/test_link_check.py
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run.py"


def load_run():
    spec = importlib.util.spec_from_file_location("link_check_run", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["link_check_run"] = module
    spec.loader.exec_module(module)
    return module


run = load_run()

# A directory that exists, carries a CONTEXT.md, and is not going away.
TARGET_PATH = "workflows/audit/"
TARGET_LINK = "[[workflows/audit/CONTEXT]]"


def write_fixture(directory, text):
    """Write a CONTEXT.md fixture with LF endings and return its path."""
    path = Path(directory) / "CONTEXT.md"
    path.write_bytes(text.encode("utf-8"))
    return path


def link_file(text):
    """Run add_links_to_file over ``text``. Returns (changes, resulting_text)."""
    with tempfile.TemporaryDirectory() as td:
        path = write_fixture(td, text)
        changes, _removed = run.add_links_to_file(path, dry_run=False)
        return changes, path.read_text(encoding="utf-8")


def link_file_owning(text, own_target):
    """Run add_links_to_file with the file's own link target forced.

    Fixtures live in a temp directory, so their real own-target is an absolute
    path that no resolved reference can ever equal, and the self-link behaviour
    would be untestable end to end. Forcing the own-target is what lets a
    fixture citing a real project directory be treated as citing itself.

    Returns (insertions, removals, resulting_text, was_written).
    """
    with tempfile.TemporaryDirectory() as td:
        path = write_fixture(td, text)
        before = path.read_bytes()
        original = run.own_link_target
        run.own_link_target = lambda _p: own_target
        try:
            insertions, removals = run.add_links_to_file(path, dry_run=False)
        finally:
            run.own_link_target = original
        after = path.read_bytes()
        return (insertions, removals, path.read_text(encoding="utf-8"),
                after != before)


def line_starting(text, prefix):
    """Return the single line of ``text`` starting with ``prefix``."""
    matches = [ln for ln in text.splitlines() if ln.startswith(prefix)]
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly one line starting {prefix!r}, found {len(matches)}"
        )
    return matches[0]


def line_containing(text, needle):
    """Return the single line of ``text`` containing ``needle``.

    Used where the assertion is about a line the tool may have rewritten: an
    inserted link lands immediately after the backtick reference, so a prefix
    that spans it stops matching the moment the tool does its job, and the test
    fails for the wrong reason. Anchor on trailing prose the insertion cannot
    move instead.
    """
    matches = [ln for ln in text.splitlines() if needle in ln]
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly one line containing {needle!r}, found {len(matches)}"
        )
    return matches[0]


class RevisionHistorySkipTests(unittest.TestCase):
    """The three controls for the region skip added by stage 4a."""

    def test_positive_control_reference_in_contents_is_linked(self):
        # POSITIVE CONTROL: a legal instance of the subject - an eligible
        # reference in a walkable region - which the transformer must link. A
        # skip that also swallowed this would pass every rejection control while
        # having disabled the tool.
        changes, result = link_file(
            "# Demo\n"
            "\n"
            "## Contents\n"
            f"- `{TARGET_PATH}` - the audit workflow.\n"
            "\n"
            "## Revision History\n"
            "- 2026-08-04 - Initial creation.\n"
        )
        self.assertEqual(len(changes), 1)
        self.assertIn(TARGET_LINK, line_starting(result, "- `"))

    def test_rejection_control_reference_in_dated_history_entry_is_not_linked(self):
        # REJECTION CONTROL: an illegal instance - the same eligible reference,
        # inside a dated Revision History entry. This is the defect the stage
        # exists to close; before the fix this fixture reported two changes and
        # rewrote the dated line.
        changes, result = link_file(
            "# Demo\n"
            "\n"
            "## Contents\n"
            f"- `{TARGET_PATH}` - the audit workflow.\n"
            "\n"
            "## Revision History\n"
            f"- 2026-08-04 - Added a path check to `{TARGET_PATH}` during review.\n"
        )
        self.assertEqual(len(changes), 1)
        self.assertNotIn(TARGET_LINK, line_starting(result, "- 2026-08-04"))

    def test_negative_control_document_with_no_history_section_is_unaffected(self):
        # NEGATIVE CONTROL: not an instance of the subject at all. No Revision
        # History section exists, so a linked result proves the clean answers
        # above are the skip firing where it should rather than the skip never
        # firing.
        changes, result = link_file(
            "# Demo\n"
            "\n"
            "## Contents\n"
            f"- `{TARGET_PATH}` - the audit workflow.\n"
            "\n"
            "## Known Issues\n"
            f"- Depends on `{TARGET_PATH}` staying where it is.\n"
        )
        self.assertEqual(len(changes), 2)
        self.assertIn(TARGET_LINK, line_starting(result, "- `"))
        self.assertIn(TARGET_LINK, line_starting(result, "- Depends"))


class SectionBoundaryTests(unittest.TestCase):
    """Where the skipped region starts and stops."""

    def test_section_ends_at_next_heading_so_a_later_section_is_processed(self):
        # The required assertion from the stage spec. A skip that ran to end of
        # file would pass every test above and silently stop linking anything
        # written after the history section.
        changes, result = link_file(
            "# Demo\n"
            "\n"
            "## Revision History\n"
            f"- 2026-08-04 - Touched `{TARGET_PATH}`.\n"
            "\n"
            "## Dependencies\n"
            f"- `{TARGET_PATH}` - the audit workflow.\n"
        )
        self.assertEqual(len(changes), 1)
        self.assertNotIn(TARGET_LINK, line_starting(result, "- 2026-08-04"))
        self.assertIn(TARGET_LINK, line_starting(result, "- `"))

    def test_subheading_does_not_close_the_section(self):
        # The boundary is a level-two heading. A ### inside the section is part
        # of the section, so entries beneath it stay protected.
        changes, result = link_file(
            "# Demo\n"
            "\n"
            "## Revision History\n"
            "\n"
            "### 2026\n"
            f"- 2026-08-04 - Touched `{TARGET_PATH}`.\n"
        )
        self.assertEqual(changes, [])
        self.assertNotIn(TARGET_LINK, result)

    def test_heading_line_itself_is_preserved_verbatim(self):
        # The heading sets the flag and is then skipped like the lines under it.
        # Asserted because "set the flag" and "emit the line" are separate steps
        # and dropping the second would delete the heading.
        _, result = link_file(
            "# Demo\n"
            "\n"
            "## Revision History\n"
            "- 2026-08-04 - Initial creation.\n"
        )
        self.assertIn("## Revision History\n", result)


class ExistingBehaviourTests(unittest.TestCase):
    """Properties that predate stage 4a and must survive it."""

    def test_fenced_code_block_is_still_skipped(self):
        # The new flag sits in the same loop as in_code_block and is evaluated
        # after it. This pins the ordering: a heading written inside a fence is
        # sample text, not a section boundary.
        changes, result = link_file(
            "# Demo\n"
            "\n"
            "## Contents\n"
            "```\n"
            "## Revision History\n"
            f"- `{TARGET_PATH}` - sample text inside a fence.\n"
            "```\n"
            f"- `{TARGET_PATH}` - the audit workflow.\n"
        )
        self.assertEqual(len(changes), 1)
        self.assertNotIn(TARGET_LINK, line_containing(result, "sample text inside a fence"))
        self.assertIn(TARGET_LINK, line_containing(result, "the audit workflow"))

    def test_file_is_not_rewritten_when_only_history_would_have_changed(self):
        # The second-order consequence recorded in the review ledger: a rewritten
        # history entry makes doc-sync-guard report the directory as gaining no
        # Revision History entry, because it compares entry lines as a multiset.
        # No reported change means no write at all, so the file is untouched.
        with tempfile.TemporaryDirectory() as td:
            path = write_fixture(
                td,
                "# Demo\n"
                "\n"
                "## Revision History\n"
                f"- 2026-08-04 - Touched `{TARGET_PATH}`.\n",
            )
            before = path.read_bytes()
            changes, removed = run.add_links_to_file(path, dry_run=False)
            self.assertEqual(changes, [])
            self.assertEqual(removed, [])
            self.assertEqual(path.read_bytes(), before)

    def test_dry_run_reports_without_writing(self):
        # The project-wide dry-run contract, asserted here because this suite is
        # the only place link-check's file-mutating function is exercised.
        with tempfile.TemporaryDirectory() as td:
            path = write_fixture(
                td,
                "# Demo\n"
                "\n"
                "## Contents\n"
                f"- `{TARGET_PATH}` - the audit workflow.\n",
            )
            before = path.read_bytes()
            changes, _removed = run.add_links_to_file(path, dry_run=True)
            self.assertEqual(len(changes), 1)
            self.assertEqual(path.read_bytes(), before)


class SelfLinkSkipTests(unittest.TestCase):
    """A reference resolving to the containing file's own target is not linked.

    The subject is the same as the suite's other class: a backtick path
    reference that resolves to a real project target. The three controls are
    stated against it:

    - **Positive control** - a legal instance: a reference resolving to a
      DIFFERENT target, which must still be linked. This catches a skip written
      too wide, which would stop the tool doing its job at all.
    - **Rejection control** - an illegal instance: a reference resolving to the
      file's own target, which must be refused.
    - **Negative control** - not an instance: a file whose own target nothing
      resolves to, so a clean result is known not to be the skip over-matching.
    """

    OTHER = "workflows/close-out/CONTEXT"

    def test_rejection_control_a_reference_to_its_own_file_is_not_linked(self):
        line, changes = run.process_line(
            f"- `{TARGET_PATH}` - the audit workflow.\n",
            own_target="workflows/audit/CONTEXT")
        self.assertEqual(changes, [])
        self.assertNotIn(TARGET_LINK, line)

    def test_positive_control_a_reference_to_another_file_is_linked(self):
        line, changes = run.process_line(
            f"- `{TARGET_PATH}` - the audit workflow.\n",
            own_target=self.OTHER)
        self.assertEqual(len(changes), 1)
        self.assertIn(TARGET_LINK, line)

    def test_negative_control_an_unrelated_own_target_changes_nothing(self):
        # The own-target plays no part unless something resolves to it, so this
        # is the same line as the positive control with a third target: a clean
        # insertion here proves the skip is keyed on equality rather than on the
        # mere presence of an own-target.
        line, changes = run.process_line(
            f"- `{TARGET_PATH}` - the audit workflow.\n",
            own_target="workflows/link-check/tests/CONTEXT")
        self.assertEqual(len(changes), 1)
        self.assertIn(TARGET_LINK, line)


class SelfLinkRemovalTests(unittest.TestCase):
    """An existing self-link is removed, but only from a file already being
    rewritten for an insertion. That scoping IS the cleanup policy: stripping
    project-wide would rewrite 63 tracked CONTEXT.md files and owe a Revision
    History and LOG.md entry for each."""

    OWN = "workflows/audit/CONTEXT"
    OTHER_PATH = "workflows/close-out/"
    OTHER_LINK = "[[workflows/close-out/CONTEXT]]"

    def test_the_helper_removes_the_link_and_the_space_before_it(self):
        line, removed = run.strip_self_links(
            f"- `{TARGET_PATH}` {TARGET_LINK} - the audit workflow.\n", self.OWN)
        self.assertEqual(len(removed), 1)
        self.assertEqual(line, f"- `{TARGET_PATH}` - the audit workflow.\n")

    def test_the_helper_leaves_a_link_to_another_file_alone(self):
        original = f"- `{self.OTHER_PATH}` {self.OTHER_LINK} - close-out.\n"
        line, removed = run.strip_self_links(original, self.OWN)
        self.assertEqual(removed, [])
        self.assertEqual(line, original)

    def test_positive_control_a_self_link_goes_when_the_file_is_rewritten(self):
        insertions, removals, text, written = link_file_owning(
            "# Demo\n"
            "\n"
            "## Contents\n"
            f"- `{TARGET_PATH}` {TARGET_LINK} - names itself.\n"
            f"- `{self.OTHER_PATH}` - earns the insertion.\n",
            self.OWN)
        self.assertTrue(written)
        self.assertEqual(len(insertions), 1)
        self.assertEqual(len(removals), 1)
        self.assertNotIn(TARGET_LINK, text)
        self.assertIn(self.OTHER_LINK, text)

    def test_rejection_control_a_removal_alone_never_earns_a_write(self):
        # The file carries a self-link and nothing to insert. It must be left
        # byte-identical: a write here would make every close-out link pass a
        # 63-file sweep with the documentation cost that implies.
        insertions, removals, text, written = link_file_owning(
            "# Demo\n"
            "\n"
            "## Contents\n"
            f"- `{TARGET_PATH}` {TARGET_LINK} - names itself.\n",
            self.OWN)
        self.assertFalse(written)
        self.assertEqual(insertions, [])
        self.assertEqual(removals, [])
        self.assertIn(TARGET_LINK, text)

    def test_negative_control_a_rewrite_with_no_self_link_removes_nothing(self):
        insertions, removals, text, written = link_file_owning(
            "# Demo\n"
            "\n"
            "## Contents\n"
            f"- `{self.OTHER_PATH}` - earns the insertion.\n",
            self.OWN)
        self.assertTrue(written)
        self.assertEqual(len(insertions), 1)
        self.assertEqual(removals, [])
        self.assertIn(self.OTHER_LINK, text)

    def test_a_self_link_inside_revision_history_is_left_alone(self):
        # The region guards apply to the removal as well as the insertion,
        # because the strip runs inside the same walk. History is a dated
        # record; a link in it is part of what was written that day.
        insertions, removals, text, written = link_file_owning(
            "# Demo\n"
            "\n"
            "## Contents\n"
            f"- `{self.OTHER_PATH}` - earns the insertion.\n"
            "\n"
            "## Revision History\n"
            f"- 2026-08-04 - Touched `{TARGET_PATH}` {TARGET_LINK}.\n",
            self.OWN)
        self.assertTrue(written)
        self.assertEqual(removals, [])
        self.assertIn(TARGET_LINK, text)


class OwnLinkTargetTests(unittest.TestCase):
    def test_a_context_path_maps_to_its_own_link_target(self):
        target = run.own_link_target(
            run.PROJECT_ROOT / "workflows" / "audit" / "CONTEXT.md")
        self.assertEqual(target, "workflows/audit/CONTEXT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
