#!/usr/bin/env python3
"""Hermetic tests for the skill-hardening guard.

Unit tests for the pure parser/checker (check_skill, extract_hardening_section,
_field_content) and integration tests for find_skill_files over a throwaway tree.
No git, no network, no dependence on the real project's SKILL.md files.

    python workflows/skill-hardening-guard/tests/test_run.py
"""

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "run.py"
)


def load_run():
    spec = importlib.util.spec_from_file_location("shg_run", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run = load_run()


# A complete, valid Hardening section used as the happy-path baseline.
COMPLETE = """# Example - Skill Specification

## Purpose
Do a thing.

## Verification
Check the thing.

## Hardening
Safety envelope for this skill. All five fields are required.

- **Allowed tool intent:** Read-only file access.
- **Never:** Delete anything.
- **Approval-gated:** None.
- **Write boundaries:** None.
- **Verification / escape hatch:** A reviewer confirms nothing was written.

## Dependencies
None.
"""


class CheckSkillTests(unittest.TestCase):
    def test_complete_section_passes(self):
        self.assertEqual(run.check_skill("skills/x/SKILL.md", COMPLETE), [])

    def test_missing_section_flagged(self):
        # A SKILL.md with no Hardening heading at all.
        text = "# X\n\n## Purpose\nDo a thing.\n\n## Dependencies\nNone.\n"
        findings = run.check_skill("skills/x/SKILL.md", text)
        self.assertEqual(len(findings), 1)
        self.assertIn("missing `## Hardening` section", findings[0][2])

    def test_missing_single_field_flagged(self):
        text = COMPLETE.replace(
            "- **Approval-gated:** None.\n", ""
        )
        findings = run.check_skill("skills/x/SKILL.md", text)
        self.assertEqual(len(findings), 1)
        self.assertIn("missing the `Approval-gated` field", findings[0][2])

    def test_empty_field_flagged(self):
        text = COMPLETE.replace(
            "- **Never:** Delete anything.\n", "- **Never:**\n"
        )
        findings = run.check_skill("skills/x/SKILL.md", text)
        self.assertEqual(len(findings), 1)
        self.assertIn("`Never` is empty", findings[0][2])

    def test_placeholder_field_flagged(self):
        text = COMPLETE.replace(
            "- **Write boundaries:** None.\n",
            "- **Write boundaries:** {{HARDENING_WRITE_BOUNDARIES}}\n",
        )
        findings = run.check_skill("skills/x/SKILL.md", text)
        self.assertEqual(len(findings), 1)
        self.assertIn("unfilled template placeholder", findings[0][2])

    def test_none_is_valid_content(self):
        # "None" is a legitimate value for a field that genuinely does not apply.
        self.assertEqual(run.check_skill("skills/x/SKILL.md", COMPLETE), [])

    def test_all_fields_missing_reports_five(self):
        text = "# X\n\n## Hardening\nSome prose but no fields.\n\n## Dependencies\nNone.\n"
        findings = run.check_skill("skills/x/SKILL.md", text)
        self.assertEqual(len(findings), 5)

    def test_section_stops_at_next_heading(self):
        # A field that appears only AFTER the section (under Dependencies) must
        # not count toward the Hardening section.
        text = (
            "# X\n\n## Hardening\n"
            "- **Allowed tool intent:** Read only.\n"
            "- **Never:** Nothing.\n"
            "- **Approval-gated:** None.\n"
            "- **Write boundaries:** None.\n\n"
            "## Dependencies\n"
            "- **Verification / escape hatch:** this is in the wrong section.\n"
        )
        findings = run.check_skill("skills/x/SKILL.md", text)
        self.assertEqual(len(findings), 1)
        self.assertIn("missing the `Verification / escape hatch` field",
                      findings[0][2])

    def test_extract_returns_none_when_absent(self):
        self.assertIsNone(run.extract_hardening_section("# X\n\n## Purpose\nHi.\n"))

    def test_field_content_present_vs_empty_vs_absent(self):
        section = ("- **Never:** something\n"
                   "- **Write boundaries:**\n")
        self.assertEqual(run._field_content(section, "Never"), "something")
        self.assertEqual(run._field_content(section, "Write boundaries"), "")
        self.assertIsNone(run._field_content(section, "Approval-gated"))

    def test_multiline_field_value_not_empty(self):
        # A field whose value wraps onto the next (indented) line must be read in
        # full, not judged empty. Regression for the single-line-only parser.
        text = COMPLETE.replace(
            "- **Verification / escape hatch:** A reviewer confirms nothing was "
            "written.\n",
            "- **Verification / escape hatch:**\n"
            "  A reviewer confirms nothing was written, and if a boundary turns\n"
            "  out to be insufficient they widen it in the SKILL.md first.\n",
        )
        self.assertEqual(run.check_skill("skills/x/SKILL.md", text), [])
        section = run.extract_hardening_section(text)
        self.assertIn("A reviewer confirms",
                      run._field_content(section, "Verification / escape hatch"))

    def test_colon_outside_bold_format_accepted(self):
        # AGENTS.md prose documents fields as `**Never** - ...` (colon/dash
        # outside the bold). A skill hand-authored to that wording must pass.
        text = (
            "# X\n\n## Hardening\n"
            "- **Allowed tool intent** - Read-only file access.\n"
            "- **Never** - Delete anything.\n"
            "- **Approval-gated** - None.\n"
            "- **Write boundaries** - None.\n"
            "- **Verification / escape hatch** - A reviewer checks.\n\n"
            "## Dependencies\nNone.\n"
        )
        self.assertEqual(run.check_skill("skills/x/SKILL.md", text), [])

    def test_fenced_hardening_example_is_ignored(self):
        # A fenced code block containing a COMPLETE `## Hardening` example placed
        # before the real, incomplete section must not be mistaken for it.
        text = (
            "# X\n\n## Purpose\nDo a thing.\n\n"
            "Here is the required shape:\n\n"
            "```\n"
            "## Hardening\n"
            "- **Allowed tool intent:** Read only.\n"
            "- **Never:** Delete.\n"
            "- **Approval-gated:** None.\n"
            "- **Write boundaries:** None.\n"
            "- **Verification / escape hatch:** A reviewer checks.\n"
            "```\n\n"
            "## Hardening\n"
            "- **Allowed tool intent:** Read only.\n\n"
            "## Dependencies\nNone.\n"
        )
        findings = run.check_skill("skills/x/SKILL.md", text)
        # The real section has only 1 of 5 fields, so 4 must be flagged missing.
        self.assertEqual(len(findings), 4)

    def test_angle_bracket_stub_flagged(self):
        text = COMPLETE.replace(
            "- **Never:** Delete anything.\n", "- **Never:** <fill in>\n"
        )
        findings = run.check_skill("skills/x/SKILL.md", text)
        self.assertEqual(len(findings), 1)
        self.assertIn("unfilled template placeholder", findings[0][2])

    def test_embedded_angle_token_not_flagged(self):
        # An angle token inside real prose (a path pattern) is legitimate content,
        # not a stub. Regression for the over-broad `<...>` match.
        text = COMPLETE.replace(
            "- **Write boundaries:** None.\n",
            "- **Write boundaries:** `reviews/<label>.md` and nothing else.\n",
        )
        self.assertEqual(run.check_skill("skills/x/SKILL.md", text), [])


class FindSkillFilesTests(unittest.TestCase):
    def _write(self, root, rel, text="x"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_finds_top_level_and_workflow_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "skills/a/SKILL.md")
            self._write(root, "workflows/w/skills/b/SKILL.md")
            found = {p.relative_to(root).as_posix() for p in run.find_skill_files(root)}
            self.assertEqual(found, {"skills/a/SKILL.md",
                                     "workflows/w/skills/b/SKILL.md"})

    def test_skips_archived_and_self_and_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "skills/live/SKILL.md")
            self._write(root, "skills/archived/old/SKILL.md")
            self._write(root, "workflows/skill-hardening-guard/tests/fixtures/SKILL.md")
            self._write(root, "templates/SKILL.md.template")
            found = {p.relative_to(root).as_posix() for p in run.find_skill_files(root)}
            self.assertEqual(found, {"skills/live/SKILL.md"})

    def test_prunes_skipped_and_hidden_dirs_before_descent(self):
        # A SKILL.md living inside a pruned subtree (node_modules, a hidden
        # directory, or archived/) must not be found: the walk should never
        # descend into those directories in the first place.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "skills/live/SKILL.md")
            self._write(root, "skills/node_modules/pkg/SKILL.md")
            self._write(root, "skills/.hidden/SKILL.md")
            self._write(root, "workflows/w/archived/old/SKILL.md")
            found = {p.relative_to(root).as_posix() for p in run.find_skill_files(root)}
            self.assertEqual(found, {"skills/live/SKILL.md"})

    def test_prunes_underscore_prefixed_dirs(self):
        # A private/scratch dir (leading `_`) is the portable, no-git
        # "out of scope" signal: a SKILL.md inside one must not be found.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "skills/live/SKILL.md")
            self._write(root, "skills/_scratch/SKILL.md")
            found = {p.relative_to(root).as_posix() for p in run.find_skill_files(root)}
            self.assertEqual(found, {"skills/live/SKILL.md"})

    def test_run_check_over_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "skills/good/SKILL.md", COMPLETE)
            self._write(root, "skills/bad/SKILL.md", "# Bad\n\n## Purpose\nx\n")
            findings = run.run_check(root)
            # Only the bad skill (missing whole section) should be flagged.
            self.assertEqual(len(findings), 1)
            self.assertIn("skills/bad/SKILL.md", findings[0][2])

    def test_unreadable_file_is_degraded_not_warn(self):
        # A SKILL.md the guard cannot decode is a can't-run condition (DEGRADED,
        # non-blocking), never a Hardening gap (WARN, which close-out hard-fails).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = self._write(root, "skills/good/SKILL.md", COMPLETE)
            bad = root / "skills" / "bad" / "SKILL.md"
            bad.parent.mkdir(parents=True, exist_ok=True)
            bad.write_bytes(b"\xff\xfe\x00\x00 not valid utf-8 \x81\x82")
            findings = run.run_check(root)
            self.assertEqual(len(findings), 1)
            sev, _label, msg = findings[0]
            self.assertEqual(sev, "DEGRADED")
            self.assertIn("skills/bad/SKILL.md", msg)
            self.assertIn("could not read", msg)


class TemplateDriftTests(unittest.TestCase):
    """REQUIRED_FIELDS is a hardcoded copy of the template's Hardening labels.

    Nothing at runtime keeps the two in sync, so this test is the drift guard: if
    someone adds, renames, or removes a field in templates/SKILL.md.template, the
    hardcoded list must move with it or this fails.
    """

    def test_required_fields_match_template(self):
        template = run.PROJECT_ROOT / "templates" / "SKILL.md.template"
        text = template.read_text(encoding="utf-8")
        section = run.extract_hardening_section(text)
        self.assertIsNotNone(section, "template has no ## Hardening section")
        labels = re.findall(r"^-[ \t]*\*\*([^*]+?):\*\*", section, re.MULTILINE)
        self.assertEqual(labels, run.REQUIRED_FIELDS)


class SmokeTests(unittest.TestCase):
    """Runs the real guard as a subprocess against the real tree - the contract
    the audit hook depends on (valid JSON on stdout, advisory exit code)."""

    def test_real_guard_emits_valid_json_and_exits_zero(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--check", "--json"],
            capture_output=True, text=True, encoding="utf-8",
            cwd=str(run.PROJECT_ROOT),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("findings", payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
