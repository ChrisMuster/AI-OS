#!/usr/bin/env python3
"""Offline, fake-backed tests for conversation cross-review (plan 10.12)."""

import asyncio
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


crossreview = load("crossreview")
probe = load("probe_server")
providers = load("providers")
container = load("container")


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


AUDIT_CLEAN = ("# Audit Report\n\n**Failures:** 0\n**Warnings:** 0\n"
               "**Degraded (did not run):** 0\n**Info:** 0\n\nAll structural checks passed.\n")


def passing(argv):
    """A passing step 5 result in the form the command prints: a guard's empty
    --json findings, the targeted audit's zero counts, or nothing for a script."""
    text = " ".join(map(str, argv)).replace("\\", "/")
    if "personal-data-guard" in text:
        # The real guard states that it scanned (R10-2).
        return SimpleNamespace(returncode=0, stdout='{"findings": [], "scanned": true}')
    return SimpleNamespace(returncode=0, stdout='{"findings": []}' if "--json" in argv
                           else AUDIT_CLEAN if "workflows/audit/" in text else "")


def warned(message):
    return SimpleNamespace(returncode=0, stdout=json.dumps({"findings": [
        {"severity": "WARN", "label": "fixture", "message": message}]}))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "memory").mkdir()
        (self.root / "code").mkdir()
        (self.root / "code/a.py").write_text("print('a')\n", encoding="utf-8", newline="\n")
        (self.root / "code/b.py").write_text("print('b')\n", encoding="utf-8", newline="\n")
        (self.root / "plan.md").write_text("a plan\n", encoding="utf-8", newline="\n")
        (self.root / ".gitignore").write_text("memory/\nplan.md\n", encoding="utf-8", newline="\n")
        git(self.root, "init", "-q")
        git(self.root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "add", "code", ".gitignore")
        git(self.root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "-qm", "start")

    def brief(self, *, kind="code", under="code", excluded="None", checks="None",
              context="None", changed="Changed code.", challenge="None"):
        text = ("# Review\n\n## Item\n\ntest_item\n\n## Kind\n\n" + kind +
                "\n\n## Under review\n\n" + under +
                "\n\n## Not under review\n\n" + excluded +
                "\n\n## Checks\n\n" + checks +
                "\n\n## Context directories\n\n" + context +
                "\n\n## What changed\n\n" + changed +
                "\n\n## Worth challenging\n\n" + challenge + "\n")
        (self.root / "memory/test_item_review_brief.md").write_text(text, encoding="utf-8", newline="\n")
        return "memory/test_item_review_brief.md"


class BriefTests(Fixture):
    def test_all_eight_sections_and_plan(self):
        value = crossreview.read_brief(self.root, self.brief())
        self.assertEqual(value["Item"], "test_item")
        plan = crossreview.read_brief(self.root, self.brief(kind="plan", under="plan.md"))
        self.assertEqual(plan["Kind"], "plan")

    def test_table_refusals(self):
        cases = [dict(kind="other"), dict(under="missing.py"),
                 dict(under="code/../code"), dict(under="code\\a.py"),
                 dict(under="./code"), dict(under=".env.local"),
                 dict(under="code\ncode"), dict(excluded="code - reason"),
                 dict(excluded="missing.py - "), dict(checks="missing.py"),
                 dict(context="missing"), dict(changed=""), dict(challenge="")]
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(crossreview.Refused):
                    crossreview.read_brief(self.root, self.brief(**case))

    def test_duplicate_heading_and_fence(self):
        path = self.root / self.brief()
        path.write_text(path.read_text(encoding="utf-8") + "\n## Item\nother\n", encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(crossreview.Refused, "appears twice"):
            crossreview.read_brief(self.root, path.relative_to(self.root).as_posix())
        path = self.root / self.brief(challenge="```\n## Item\n```")
        self.assertEqual(crossreview.read_brief(self.root,
                         path.relative_to(self.root).as_posix())["Item"], "test_item")
        path = self.root / self.brief(challenge="```\n## Item")
        with self.assertRaisesRegex(crossreview.Refused, "fence"):
            crossreview.read_brief(self.root, path.relative_to(self.root).as_posix())

    def test_missing_and_unknown_heading_refuse_with_correction(self):
        path = self.root / self.brief()
        original = path.read_text(encoding="utf-8")
        path.write_text(original.replace("## Item", "## Object", 1), encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(crossreview.Refused, "line .*write"):
            crossreview.read_brief(self.root, path.relative_to(self.root).as_posix())
        path.write_text(original.split("## Worth challenging")[0], encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(crossreview.Refused, "Worth challenging.*write"):
            crossreview.read_brief(self.root, path.relative_to(self.root).as_posix())

    def test_case_and_preamble(self):
        with self.assertRaisesRegex(crossreview.Refused, "wrong capitals"):
            crossreview.read_brief(self.root, self.brief(under="Code"))
        path = self.root / self.brief()
        path.write_text("stray text\n" + path.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(crossreview.Refused, "line 1"):
            crossreview.read_brief(self.root, path.relative_to(self.root).as_posix())

    def test_plan_refuses_lists_and_tracked_document(self):
        with self.assertRaises(crossreview.Refused):
            crossreview.read_brief(self.root, self.brief(kind="plan", under="code/a.py"))
        for field in (dict(excluded="code/a.py - reason"), dict(checks="code/a.py"),
                      dict(context="code")):
            with self.subTest(field=field), self.assertRaises(crossreview.Refused):
                crossreview.read_brief(self.root, self.brief(kind="plan", under="plan.md", **field))

    def test_refusals_name_line_and_correction(self):
        for case in (dict(kind="other"), dict(under="./code"),
                     dict(under="code\\a.py"), dict(under="Code"),
                     dict(excluded="code/a.py - "), dict(checks="missing.py"),
                     dict(changed="None"), dict(challenge="none")):
            with self.subTest(case=case), self.assertRaises(crossreview.Refused) as raised:
                crossreview.read_brief(self.root, self.brief(**case))
            self.assertRegex(str(raised.exception), r"line \d+")
            self.assertIn("write", str(raised.exception))

    def test_empty_spellings_duplicates_and_quoted_heading(self):
        for spelling in ("none", "None.", "N/A", ""):
            with self.subTest(spelling=spelling), self.assertRaises(crossreview.Refused):
                crossreview.read_brief(self.root, self.brief(checks=spelling))
        for under in ("code\ncode", "code/a.py\ncode/a.py"):
            with self.subTest(under=under), self.assertRaisesRegex(
                    crossreview.Refused, "duplicate"):
                crossreview.read_brief(self.root, self.brief(under=under))
        self.assertEqual(crossreview.read_brief(self.root, self.brief(
            challenge="````\n## Item\n````"))["Item"], "test_item")

    def test_env_is_refused_in_each_path_section_and_reason_splits_once(self):
        for key, value in (("under", ".env.local"),
                           ("excluded", ".env.local - reason"),
                           ("checks", ".env.local"),
                           ("context", ".env.local")):
            with self.subTest(key=key), self.assertRaisesRegex(
                    crossreview.Refused, "line"):
                crossreview.read_brief(self.root, self.brief(**{key: value}))
        brief = crossreview.read_brief(self.root, self.brief(
            excluded="code/a.py - reason - with more words"))
        self.assertEqual(brief["Not under review"][0]["reason"],
                         "reason - with more words")

    def test_deleted_exclusion_is_read_then_checked_against_base(self):
        (self.root / "code/a.py").unlink()
        (self.root / "code/b.py").write_text("changed\n", encoding="utf-8", newline="\n")
        brief = crossreview.read_brief(self.root, self.brief(
            under="code/b.py", excluded="code/a.py - deleted intentionally"))
        diff, reviewed, _ = crossreview.code_diff(self.root, brief,
                                                   git(self.root, "rev-parse", "HEAD"))
        self.assertEqual(reviewed, ["code/b.py"])
        self.assertNotIn("code/a.py", diff)
        self.assertEqual(git(self.root, "cat-file", "-e", "HEAD:code/a.py"), "")

    def test_more_specific_reviewed_file_wins_over_excluded_folder(self):
        (self.root / "code/a.py").write_text("changed a\n", encoding="utf-8", newline="\n")
        (self.root / "code/b.py").write_text("changed b\n", encoding="utf-8", newline="\n")
        brief = crossreview.read_brief(self.root, self.brief(
            under="code/a.py", excluded="code - unchanged scope"))
        diff, reviewed, _ = crossreview.code_diff(self.root, brief,
                                                   git(self.root, "rev-parse", "HEAD"))
        self.assertEqual(reviewed, ["code/a.py"])
        self.assertIn("changed a", diff)
        self.assertNotIn("changed b", diff)

    def test_symlink_component_is_refused_on_every_system(self):
        # The refusal is shown with is_symlink answering for one name, so it runs
        # where the system cannot make a link; the next test makes a real one.
        (self.root / "code/Link.py").write_text("print('link')\n", encoding="utf-8",
                                                 newline="\n")
        real = Path.is_symlink
        with mock.patch.object(Path, "is_symlink", autospec=True,
                               side_effect=lambda path: path.name == "Link.py" or real(path)), \
                self.assertRaisesRegex(crossreview.Refused, "symbolic link"):
            crossreview.read_brief(self.root, self.brief(under="code/Link.py"))

    def test_real_symlink_component_is_refused(self):
        link = self.root / "code/Link.py"
        try:
            link.symlink_to(self.root / "code/a.py")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"this system cannot make a symbolic link ({exc}); "
                          "the patched case covers the refusal")
        with self.assertRaisesRegex(crossreview.Refused, "symbolic link"):
            crossreview.read_brief(self.root, self.brief(under="code/Link.py"))

    def one_line_sections(self, item, kind="code"):
        path = self.root / self.brief()
        text = path.read_text(encoding="utf-8")
        text = text.replace("## Item\n\ntest_item\n", f"## Item\n\n{item}\n", 1)
        text = text.replace("## Kind\n\ncode\n", f"## Kind\n\n{kind}\n", 1)
        path.write_text(text, encoding="utf-8", newline="\n")
        return path.relative_to(self.root).as_posix()

    def test_item_and_kind_refusals_from_the_brief_table(self):
        # Item: one line of lower-case letters, digits or _, starting with a letter
        # or digit. Kind: one line, code or plan. Each refusal names its line and
        # the correction.
        for item, kind, reason in (("", "code", "empty section"),
                                   ("test_item\nother_item", "code", "more than one line"),
                                   ("Test_item", "code", "invalid item"),
                                   ("test-item", "code", "invalid item"),
                                   ("test item", "code", "invalid item"),
                                   ("_test", "code", "invalid item"),
                                   ("test_item", "code\nplan", "more than one line")):
            with self.subTest(item=item, kind=kind), self.assertRaisesRegex(
                    crossreview.Refused, rf"line \d+: {reason}; write "):
                crossreview.read_brief(self.root, self.one_line_sections(item, kind))
        self.assertEqual(crossreview.read_brief(
            self.root, self.one_line_sections("item_2"))["Item"], "item_2")

    def test_plan_under_review_is_exactly_one_existing_file(self):
        (self.root / "memory/second_plan.md").write_text("another plan\n",
                                                         encoding="utf-8", newline="\n")
        for under, reason in (("plan.md\nmemory/second_plan.md", "exactly one document"),
                              ("None", "no path"),
                              ("memory", "is not a file")):
            with self.subTest(under=under), self.assertRaisesRegex(
                    crossreview.Refused, reason):
                crossreview.read_brief(self.root, self.brief(kind="plan", under=under))
        self.assertEqual(crossreview.read_brief(self.root, self.brief(
            kind="plan", under="memory/second_plan.md"))["Under review"][0]["path"],
            "memory/second_plan.md")


class PacketTests(Fixture):
    def test_new_packet_template_has_four_sections(self):
        path, text, labels = crossreview.packet_status(self.root, "test_item")
        self.assertIsNone(text)
        value = crossreview.packet_template("test_item")
        self.assertIn("name: test-item-review-packet", value)
        for heading in crossreview.PACKET_HEADINGS:
            self.assertIn(f"## {heading}\n\nNone.", value)
        path.write_text(value, encoding="utf-8", newline="\n")
        self.assertEqual(crossreview.packet_status(self.root, "test_item")[2], [])

    def test_open_missing_duplicate_and_mixed_refuse(self):
        path = self.root / "memory/test_item_review_packet.md"
        base = crossreview.packet_template("test_item")
        for text in (base.replace("None.", "- **R1-1 - Open.**", 1),
                     base.replace("## Open - plan readiness", "## Other"),
                     base + "\n## Open - plan readiness\nNone.\n",
                     base.replace("None.", "### R1-1 - Heading\n", 1).replace(
                         "None.", "- **R1-2 - Bullet.**", 1)):
            path.write_text(text, encoding="utf-8", newline="\n")
            with self.assertRaises(crossreview.Refused):
                crossreview.packet_status(self.root, "test_item")

    def test_addressed_needs_own_dated_repair(self):
        path = self.root / "memory/test_item_review_packet.md"
        base = crossreview.packet_template("test_item")
        for repair in ("", "  Repair: done", "  Repair (2026-02-30): done"):
            text = base.replace("## Addressed - plan readiness\n\nNone.",
                "## Addressed - plan readiness\n\n- **R1-1 - Fixed.**\n" + repair)
            path.write_text(text, encoding="utf-8", newline="\n")
            with self.assertRaises(crossreview.Refused):
                crossreview.packet_status(self.root, "test_item")
        path.write_text(base.replace("## Addressed - plan readiness\n\nNone.",
            "## Addressed - plan readiness\n\n- **R1-1 - Fixed.**\n"
            "  Repair (2026-11-17): done"), encoding="utf-8", newline="\n")
        self.assertIn("R1-1", crossreview.packet_status(self.root, "test_item")[2])

    def test_orphan_repair_and_mixed_addressed_groups_refuse(self):
        path = self.root / "memory/test_item_review_packet.md"
        base = crossreview.packet_template("test_item")
        path.write_text(base.replace("## Open - plan readiness\n\nNone.",
            "## Open - plan readiness\n\nRepair (2026-11-17): stray"), encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(crossreview.Refused, "orphan"):
            crossreview.packet_status(self.root, "test_item")
        text = base.replace("## Addressed - plan readiness\n\nNone.",
            "## Addressed - plan readiness\n\n### R1-1 - Fixed\n"
            "Repair (2026-11-17): done").replace(
            "## Addressed - review process\n\nNone.",
            "## Addressed - review process\n\n- **R1-2 - Fixed.**\n"
            "Repair (2026-11-17): done")
        path.write_text(text, encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(crossreview.Refused, "mixed"):
            crossreview.packet_status(self.root, "test_item")

    def test_consecutive_reraise_follows_first_label(self):
        path = self.root / "memory/test_item_review_packet.md"
        text = crossreview.packet_template("test_item").replace(
            "## Open - plan readiness\n\nNone.",
            "## Open - plan readiness\n\n- **R1-1 - First.**\n"
            "- **R2-1 - Second.**\n  Re-raises: R1-1\n"
            "- **R3-1 - Third.**\n  Re-raises: R1-1")
        path.write_text(text, encoding="utf-8", newline="\n")
        self.assertEqual(crossreview.consecutive_reraises(path, 3,
                         [{"reraises": "R1-1"}]), ["R3-1"])
        self.assertEqual(crossreview.consecutive_reraises(path, 4,
                         [{"reraises": ""}]), [])


class RecordTests(Fixture):
    def record(self, name, *, item="test_item", kind="plan", started="2026-11-17T12:00:00+00:00",
               stop="clean", round_no=1):
        folder = self.root / "workflows/review-orchestration/runs" / name
        folder.mkdir(parents=True)
        value = {"item": item, "kind": kind, "started_at": started,
                 "stop": stop, "round": round_no, "document": "plan.md",
                 "reviewer": "codex", "capture_commit": "abc"}
        (folder / "round.json").write_text(json.dumps(value), encoding="utf-8", newline="\n")
        return folder / "round.json"

    def test_number_from_labels_and_completed_records(self):
        self.record("review-one", round_no=3)
        records = crossreview.round_records(self.root)
        self.assertEqual(crossreview.choose_round("test_item", ["R2-1"], records), 4)
        self.assertEqual(crossreview.choose_round("test_item", [], records, 7), 7)
        with self.assertRaises(crossreview.Refused):
            crossreview.choose_round("test_item", [], records, 3)

    def test_unfinished_reuses_number_and_tie_refuses(self):
        self.record("review-one", stop=None, round_no=4)
        self.assertEqual(crossreview.choose_round("test_item", [],
                         crossreview.round_records(self.root)), 1)
        self.record("review-two")
        with self.assertRaisesRegex(crossreview.Refused, "ties"):
            crossreview.round_records(self.root)

    def test_malformed_record_refuses(self):
        path = self.record("review-one")
        path.write_text("{", encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(crossreview.Refused, "damaged"):
            crossreview.round_records(self.root)

    def test_plan_diff_uses_latest_same_document_and_reviewer(self):
        self.record("review-one", started="2026-11-17T12:00:00+00:00")
        records = crossreview.round_records(self.root)
        brief = crossreview.read_brief(self.root,
                    self.brief(kind="plan", under="plan.md"))
        brief["reviewer"] = "codex"
        with mock.patch.object(crossreview, "personal", return_value=SimpleNamespace(stdout="diff")) as called:
            base, text = crossreview.plan_diff(self.root, "personal.git", brief,
                                               records, "new")
        self.assertEqual(base, "abc")
        self.assertIn("Read this diff", text)
        self.assertEqual(called.call_args.args[2:5], ("diff", "abc", "new"))

    def test_plan_base_given_with_base_wins_over_the_records(self):
        self.record("review-one")
        records = crossreview.round_records(self.root)
        brief = crossreview.read_brief(self.root,
                    self.brief(kind="plan", under="plan.md"))
        brief["reviewer"] = "codex"
        with mock.patch.object(crossreview, "personal",
                               return_value=SimpleNamespace(stdout="diff")) as called:
            base, text = crossreview.plan_diff(self.root, "personal.git", brief,
                                               records, "new", "given")
        self.assertEqual(base, "given")
        self.assertIn("Plan diff from given to new", text)
        self.assertEqual(called.call_args.args[2:5], ("diff", "given", "new"))

    def test_plan_base_absent_is_this_reviewers_first_read(self):
        # Records for another reviewer, another document, or an unfinished round
        # give no base, so the reviewer reads the whole document with no diff.
        self.record("review-claude")
        self.record("review-other", started="2026-11-17T13:00:00+00:00")
        self.record("review-open", started="2026-11-17T14:00:00+00:00", stop=None)
        records = crossreview.round_records(self.root)
        named = {record["_path"].parent.name: record for record in records}
        named["review-claude"]["reviewer"] = "claude"
        named["review-other"]["document"] = "other.md"
        brief = crossreview.read_brief(self.root,
                    self.brief(kind="plan", under="plan.md"))
        brief["reviewer"] = "codex"
        for given in ([], records):
            with self.subTest(records=len(given)), mock.patch.object(
                    crossreview, "personal",
                    side_effect=AssertionError("diffed with no base")):
                base, text = crossreview.plan_diff(self.root, "personal.git", brief,
                                                   given, "new")
            self.assertIsNone(base)
            self.assertEqual(text, "This is this reviewer's first read of the whole document.")

    def test_plan_base_resolves_in_the_personal_repository(self):
        seen = []
        def fake_git(root, *args, check=True):
            seen.append(args)
            return SimpleNamespace(returncode=0, stdout="b" * 40 + "\n")
        with mock.patch.object(crossreview, "git", side_effect=fake_git):
            self.assertEqual(crossreview.resolve_base(self.root, "personal.git",
                                                      "main", "plan"), "b" * 40)
            self.assertEqual(crossreview.resolve_base(self.root, "personal.git",
                                                      "main", "code"), "b" * 40)
        self.assertEqual(seen[0][0], "--git-dir=personal.git")
        self.assertNotIn("--git-dir=personal.git", seen[1])

    def test_gate_first_link_and_code_refusal(self):
        brief = crossreview.read_brief(self.root,
                    self.brief(kind="plan", under="plan.md"))
        self.assertIn("link 1", crossreview.build_gate(self.root, "personal.git", brief, []))
        with self.assertRaises(crossreview.Refused):
            crossreview.build_gate(self.root, "personal.git",
                crossreview.read_brief(self.root, self.brief()), [])

    def test_gate_five_links_in_order_and_pass(self):
        path = self.record("review-one")
        record = crossreview.round_records(self.root)[0]
        brief = crossreview.read_brief(self.root,
                    self.brief(kind="plan", under="plan.md"))
        record["document_blob"] = "blob"
        record["capture_commit"] = "abc"
        state = {"captured": False, "ancestor": False, "blob": "other"}
        def public(root, *args, **kwargs):
            return SimpleNamespace(stdout="blob" if args[1] == "plan.md" else "record",
                                   returncode=0)
        def personal(root, git_dir, *args, **kwargs):
            if args[:2] == ("merge-base", "--is-ancestor"):
                return SimpleNamespace(stdout="", returncode=0 if state["ancestor"] else 1)
            if args[0] == "rev-parse" and args[1].startswith("HEAD:"):
                return SimpleNamespace(stdout="record" if state["captured"] else "old",
                                       returncode=0)
            return SimpleNamespace(stdout=state["blob"], returncode=0)
        with mock.patch.object(crossreview, "git", side_effect=public), mock.patch.object(
                crossreview, "personal", side_effect=personal):
            self.assertIn("link 2", crossreview.build_gate(self.root, "personal.git",
                                                            brief, [record]))
            state["captured"] = True
            record["stop"] = "findings"
            self.assertIn("link 3", crossreview.build_gate(self.root, "personal.git",
                                                            brief, [record]))
            record["stop"] = "clean"
            self.assertIn("link 4", crossreview.build_gate(self.root, "personal.git",
                                                            brief, [record]))
            state["ancestor"] = True
            self.assertIn("link 5", crossreview.build_gate(self.root, "personal.git",
                                                            brief, [record]))
            state["blob"] = "blob"
            self.assertIsNone(crossreview.build_gate(self.root, "personal.git",
                                                       brief, [record]))

    def test_gate_closing_capture_and_document_selection(self):
        first = self.record("review-one", started="2026-11-17T10:00:00+00:00")
        second = self.record("review-two", started="2026-11-17T11:00:00+00:00")
        records = crossreview.round_records(self.root)
        records[0]["document"] = "other.md"
        records[1]["document"] = "plan.md"
        records[1]["stop"] = "clean"
        records[1]["document_blob"] = "blob"
        records[1]["closing_capture_failed"] = "failed"
        brief = crossreview.read_brief(self.root,
            self.brief(kind="plan", under="plan.md"))
        seen = []
        def public(root, *argv, **kwargs):
            return SimpleNamespace(stdout="blob" if argv[-1] == "plan.md" else "record",
                                   returncode=0)
        def personal(root, git_dir, *argv, **kwargs):
            seen.append(argv)
            if argv[0] == "rev-parse" and argv[1].startswith("HEAD:"):
                return SimpleNamespace(stdout="record" if captured[0] else "old",
                                       returncode=0)
            return SimpleNamespace(stdout="blob", returncode=0)
        captured = [False]
        with mock.patch.object(crossreview, "git", side_effect=public), \
                mock.patch.object(crossreview, "personal", side_effect=personal):
            self.assertIn("link 2", crossreview.build_gate(self.root, "personal.git",
                                                            brief, records))
            captured[0] = True
            self.assertIsNone(crossreview.build_gate(self.root, "personal.git",
                                                       brief, records))
        # git names paths with forward slashes on every system.
        self.assertTrue(any(second.relative_to(self.root).as_posix() in str(call)
                            for call in seen))
        self.assertFalse(any(first.relative_to(self.root).as_posix() in str(call)
                             for call in seen))


class ReplyTests(Fixture):
    def finding(self):
        return {"title": "A missing check", "class": "review-process",
                "section": "Code", "wrong": "A check is missing.",
                "consequence": "A wrong result can pass.", "triage": "Fix it.",
                "fix": "Add the check.", "reraises": ""}

    def test_every_required_field_and_extra_field_refuse(self):
        for key in ("title", "section", "wrong", "consequence", "triage", "fix"):
            finding = self.finding()
            finding[key] = "  "
            with self.subTest(key=key), self.assertRaisesRegex(
                    crossreview.RoundError, "blank"):
                crossreview.validate_reply({"summary": "A summary", "findings": [finding]},
                                           "plan", [], [])
        finding = self.finding()
        finding["extra"] = "unexpected"
        with self.assertRaisesRegex(crossreview.RoundError, "extra"):
            crossreview.validate_reply({"summary": "A summary", "findings": [finding]},
                                       "plan", [], [])

    def test_labels_and_missing_changed_file_refuse(self):
        finding = self.finding()
        finding["reraises"] = "R1-1"
        with self.assertRaisesRegex(crossreview.RoundError, "unknown label"):
            crossreview.validate_reply({"summary": "A summary", "findings": [finding]},
                                       "plan", [], [])
        finding["reraises"] = ""
        finding["title"] = "R2-1 - A missing check"
        with self.assertRaisesRegex(crossreview.RoundError, "label in its title"):
            crossreview.validate_reply({"summary": "A summary", "findings": [finding]},
                                       "plan", [], [])
        with self.assertRaises(crossreview.RoundError):
            crossreview.validate_reply({"summary": "A summary", "findings": [],
                                        "files_reviewed": []}, "code", [], ["code/a.py"])


class DiffTests(Fixture):
    def test_folder_precedence_and_untracked_text(self):
        (self.root / "code/a.py").write_text("changed\n", encoding="utf-8", newline="\n")
        (self.root / "code/new.py").write_text("new\n", encoding="utf-8", newline="\n")
        brief = crossreview.read_brief(self.root, self.brief(
            excluded="code/a.py - left out"))
        base = git(self.root, "rev-parse", "HEAD")
        diff, reviewed, inventory = crossreview.code_diff(self.root, brief, base)
        self.assertEqual(reviewed, ["code/new.py"])
        self.assertIn("+++ b/code/new.py", diff)
        self.assertNotIn("changed", diff)
        self.assertEqual(set(inventory), {"code/new.py"})

    def test_git_warnings_are_not_read_as_paths(self):
        # With core.autocrlf on (as Git for Windows ships it), git warns on stderr for
        # every changed LF file. crossreview.git keeps stderr out of stdout, so the
        # warning is never taken for a changed path, and a failure still shows it.
        git(self.root, "config", "core.autocrlf", "true")
        (self.root / "code/a.py").write_text("changed\n", encoding="utf-8", newline="\n")
        base = git(self.root, "rev-parse", "HEAD")
        result = crossreview.git(self.root, "diff", "--name-only", base)
        self.assertEqual(result.stdout.splitlines(), ["code/a.py"])
        brief = crossreview.read_brief(self.root, self.brief())
        _diff, reviewed, _inventory = crossreview.code_diff(self.root, brief, base)
        self.assertEqual(reviewed, ["code/a.py"])
        # git's own error text is on stderr; the failure message must still carry it.
        with self.assertRaisesRegex(crossreview.RoundError, "Needed a single revision"):
            crossreview.git(self.root, "rev-parse", "--verify", "not-a-revision")

    def test_uncovered_and_binary(self):
        (self.root / "outside.txt").write_text("outside", encoding="utf-8", newline="\n")
        brief = crossreview.read_brief(self.root, self.brief())
        with self.assertRaisesRegex(crossreview.Refused, "outside.txt"):
            crossreview.code_diff(self.root, brief, git(self.root, "rev-parse", "HEAD"))
        (self.root / "outside.txt").unlink()
        (self.root / "code/new.bin").write_bytes(b"\x00\xff")
        diff, _, _ = crossreview.code_diff(self.root, brief, git(self.root, "rev-parse", "HEAD"))
        self.assertIn("Binary file code/new.bin (2 bytes)", diff)

    def test_tree_state_sees_rewritten_untracked_file(self):
        file = self.root / "new.txt"
        file.write_text("first", encoding="utf-8", newline="\n")
        before = crossreview.tree_state(self.root)
        file.write_text("other", encoding="utf-8", newline="\n")
        self.assertNotEqual(before, crossreview.tree_state(self.root))

    def test_mechanical_refuses_tracked_and_untracked_mutation(self):
        brief = crossreview.read_brief(self.root, self.brief())
        for target in (self.root / "code/a.py", self.root / "new.txt"):
            target.write_text("before\n", encoding="utf-8", newline="\n")
            calls = []
            def change(argv, *, cwd, check):
                calls.append(argv)
                target.write_text("after\n", encoding="utf-8", newline="\n")
                return passing(argv)
            with self.subTest(target=target), self.assertRaisesRegex(
                    crossreview.Refused, "tree changed|content|paths changed"):
                crossreview.mechanical(self.root, brief, None, run=change)
            self.assertTrue(calls)
            target.unlink() if target.name == "new.txt" else target.write_text(
                "print('a')\n", encoding="utf-8", newline="\n")


class JudgeTests(Fixture):
    """Step 5 judges each guard and the audit by what it reports, as a run does."""

    def result(self, findings, code=0, **extra):
        return SimpleNamespace(returncode=code,
                               stdout=json.dumps({"findings": findings, **extra}))

    def test_guard_findings(self):
        info = {"severity": "INFO", "label": "x", "message": "noted"}
        self.assertIsNone(crossreview.judge("guard", self.result([])))
        self.assertIsNone(crossreview.judge("guard", self.result([info])))
        for severity in ("WARN", "FAIL", "DEGRADED"):
            with self.subTest(severity=severity):
                reason = crossreview.judge("guard", self.result(
                    [info, {"severity": severity, "label": "x", "message": "a drift"}],
                    code=1 if severity == "FAIL" else 0))
                self.assertEqual(reason, f"reported {severity}: a drift")
        self.assertIn("scan skipped", crossreview.judge("guard", self.result(
            [{"severity": "INFO", "label": "x", "message": "scan skipped - no git"}])))

    def test_guard_that_did_not_finish(self):
        for result in (SimpleNamespace(returncode=0, stdout="not json"),
                       SimpleNamespace(returncode=0, stdout='{"other": []}'),
                       SimpleNamespace(returncode=0, stdout='{"findings": ["text"]}'),
                       SimpleNamespace(returncode=2, stdout="Traceback")):
            with self.subTest(stdout=result.stdout):
                self.assertIn("could not be read", crossreview.judge("guard", result))
        self.assertEqual(crossreview.judge("guard", self.result([], code=1)), "exited 1")

    def test_personal_data_hit_is_named_by_file_and_kind_only(self):
        hit = {"severity": "FAIL", "label": "personal-data", "file": "code/a.py",
               "kind": "personal_name", "message": "code/a.py: matched SECRET-NAME"}
        reason = crossreview.judge("personal-data", self.result([hit], code=1, scanned=True))
        self.assertEqual(reason, "reported FAIL code/a.py (personal_name)")
        self.assertIsNone(crossreview.judge("personal-data", self.result([], scanned=True)))
        # A pass needs the guard's own statement that it scanned, as in a run (R10-2).
        for extra in ({"scanned": False}, {}, {"scanned": "yes"}):
            with self.subTest(extra=extra):
                self.assertEqual(crossreview.judge("personal-data", self.result([], **extra)),
                                 "it does not report a completed scan")
        brief = crossreview.read_brief(self.root, self.brief())
        def run(argv, **kwargs):
            if "personal-data-guard" in str(argv[1]):
                return self.result([hit], code=1, scanned=True)
            return passing(argv)
        with self.assertRaises(crossreview.Refused) as raised:
            crossreview.mechanical(self.root, brief, None, run=run)
        self.assertIn("code/a.py (personal_name)", str(raised.exception))
        self.assertNotIn("SECRET-NAME", str(raised.exception))

    def test_ai_style_configuration_not_read_is_a_check_not_run(self):
        # The real guard's own notes for a missing and an unreadable configuration,
        # taken from its load_config, refuse the round (R10-2).
        spec = importlib.util.spec_from_file_location(
            "ai_style_run", SCRIPTS.parents[1] / "ai-style-guard/scripts/run.py")
        style = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(style)
        bad = self.root / "bad.yaml"
        bad.write_text("words: [unclosed\n", encoding="utf-8", newline="\n")
        for path in (self.root / "missing.yaml", bad):
            _config, notes = style.load_config(path)
            with self.subTest(path=path.name):
                self.assertTrue(notes)
                reason = crossreview.judge("guard", self.result(
                    [{"severity": s, "label": l, "message": m} for s, l, m in notes]))
                self.assertTrue(reason and reason.startswith("reported INFO: config"), reason)

    def test_audit_counts(self):
        self.assertIsNone(crossreview.judge("audit", passing(["workflows/audit/x"])))
        for name in crossreview.AUDIT_COUNTS:
            with self.subTest(name=name):
                text = AUDIT_CLEAN.replace(f"**{name}:** 0", f"**{name}:** 2")
                self.assertEqual(crossreview.judge("audit", SimpleNamespace(
                    returncode=0, stdout=text)), f"reported {name} 2")
        self.assertIn("no Warnings count", crossreview.judge("audit", SimpleNamespace(
            returncode=0, stdout=AUDIT_CLEAN.replace("**Warnings:** 0\n", ""))))
        self.assertEqual(crossreview.judge("audit", SimpleNamespace(
            returncode=2, stdout=AUDIT_CLEAN)), "exited 2")

    def test_script_by_exit_code(self):
        self.assertIsNone(crossreview.judge("exit", SimpleNamespace(returncode=0, stdout="x")))
        self.assertEqual(crossreview.judge("exit", SimpleNamespace(returncode=3, stdout="")),
                         "exited 3")

    def test_every_guard_runs_with_json(self):
        brief = crossreview.read_brief(self.root, self.brief(context="code"))
        seen = []
        crossreview.mechanical(self.root, brief, None,
                               run=lambda argv, **kw: seen.append(argv) or passing(argv))
        guards = [argv for argv in seen if "-guard/" in str(argv[1])]
        self.assertEqual(len(guards), 4)
        self.assertTrue(all(argv[2:4] == ["--check", "--json"] for argv in guards))
        self.assertTrue(any("workflows/audit/" in str(argv[1]) for argv in seen))


class CaptureTests(Fixture):
    def test_chain_and_optional_commit(self):
        calls = []
        def fake(argv, *, cwd, check=True):
            calls.append(argv)
            code = 1 if "--quiet" in argv else 0
            return SimpleNamespace(returncode=code, stdout="capture")
        with mock.patch.object(crossreview, "personal", return_value=SimpleNamespace(stdout="abc")):
            head = crossreview.capture(self.root, "personal.git", "message",
                run=fake, after=lambda *_: [])
        self.assertEqual(head, "abc")
        self.assertEqual([c[-2:] for c in calls[:3]],
                         [["--compare-git-dir", "personal.git"],
                          ["--git-dir", "personal.git"], ["add", "-u"]])
        self.assertEqual(len([c for c in calls if "commit" in c]), 1)
        self.assertFalse(any("add" in c and "-u" not in c for c in calls))

    def test_every_failed_step_stops_chain(self):
        for failing in range(3):
            calls = []
            def fake(argv, *, cwd, check=True):
                calls.append(argv)
                if len(calls) == failing + 1:
                    raise crossreview.RoundError("failed")
                return SimpleNamespace(returncode=0, stdout="")
            with self.subTest(failing=failing), self.assertRaises(crossreview.RoundError):
                crossreview.capture(self.root, "personal.git", "message",
                                    run=fake, after=lambda *_: [])
            self.assertEqual(len(calls), failing + 1)

    def test_empty_stage_skips_commit_and_after_checks_fail(self):
        calls = []
        def fake(argv, *, cwd, check=True):
            calls.append(argv)
            return SimpleNamespace(returncode=0, stdout="")
        with mock.patch.object(crossreview, "personal", return_value=SimpleNamespace(stdout="abc")):
            self.assertEqual(crossreview.capture(self.root, "personal.git", "message",
                run=fake, after=lambda *_: []), "abc")
        self.assertFalse(any("commit" in call for call in calls))
        with self.assertRaisesRegex(crossreview.RoundError, "after-check"):
            crossreview.capture(self.root, "personal.git", "message", run=fake,
                                after=lambda *_: ["uncaptured path"])
        def fail_tracked(argv, *, cwd, check=True):
            if "--tracked-by-both" in argv:
                raise crossreview.RoundError("tracked by both")
            return SimpleNamespace(returncode=0, stdout="")
        with self.assertRaisesRegex(crossreview.RoundError, "tracked by both"):
            crossreview.capture(self.root, "personal.git", "message",
                                run=fail_tracked, after=lambda *_: [])

    def test_commit_failure_is_capture_failure(self):
        def fake(argv, *, cwd, check=True):
            if "commit" in argv:
                raise crossreview.RoundError("identity missing")
            return SimpleNamespace(returncode=1 if "--quiet" in argv else 0,
                                   stdout="")
        with self.assertRaisesRegex(crossreview.RoundError, "identity missing"):
            crossreview.capture(self.root, "personal.git", "message",
                                run=fake, after=lambda *_: [])


class RoundFlowTests(Fixture):
    def setUp(self):
        super().setUp()
        from shutil import copyfile
        folder = self.root / "workflows/review-orchestration"
        (folder / "config").mkdir(parents=True)
        (folder / "prompts").mkdir()
        (folder / "runs").mkdir()
        (folder / "LOG.md").write_text("# Log\n", encoding="utf-8", newline="\n")
        (folder / "config/settings.json").write_text("{}\n", encoding="utf-8", newline="\n")
        copyfile(SCRIPTS.parent / "prompts/cross-review.md",
                 folder / "prompts/cross-review.md")

    def deps(self, reply=None, *, fail_at=None):
        import limits
        reading = limits.Reading(limits.CODEX, limits.REPORTED, percent=1,
            raw={"rateLimits": {"credits": {"hasCredits": False,
                                               "unlimited": False}}})
        calls = []
        def command(argv, *, cwd, check=True):
            calls.append(("check", argv))
            return passing(argv)
        def capture(*args):
            calls.append(("capture", args[2]))
            if fail_at == "capture" and len([c for c in calls if c[0] == "capture"]) == 1:
                raise crossreview.RoundError("capture failed")
            if fail_at == "closing" and len([c for c in calls if c[0] == "capture"]) == 2:
                raise crossreview.RoundError("closing failed")
            return "a" * 40
        def make_copy(root, dest, public_head, floor):
            calls.append(("copy", None))
            dest.mkdir()
        def review(*args):
            calls.append(("review", None))
            if fail_at == "review":
                raise crossreview.RoundError("session failed")
            return reply or {"summary": "The plan is sound.", "findings": []}
        def ensure_docker():
            # As container.ensure_running: True only when this call started Docker.
            if deps.docker_running():
                return False
            calls.append(("docker", None))
            return True
        deps = SimpleNamespace(read_codex=lambda *a: reading,
            model_offered=lambda *a: True, command=command, capture=capture,
            docker_running=lambda: True, ensure_docker=ensure_docker,
            stop_docker=lambda: calls.append(("stop", None)),
            prepare_image=lambda *a, **kw: {"tag": "image", "would": "reuse"},
            start_ignored=lambda *a: set(), make_copy=make_copy,
            remove_copy=lambda dest: __import__("shutil").rmtree(dest),
            review_session=review, probe_claude=lambda *a: None)
        return deps, calls

    def args(self, *, dry=False):
        return SimpleNamespace(review=self.brief(kind="plan", under="plan.md"),
            reviewer="codex", git_dir="personal.git", round=None, base=None,
            allow_paid=False, reset_confirmed=False, dry_run=dry,
            build_ready=False)

    def test_real_session_wires_persistent_probe_record(self):
        import loop
        scripts = self.root / "workflows/review-orchestration/scripts"
        scripts.mkdir()
        (scripts / "tripwire.py").write_text("# fixture\n", encoding="utf-8",
                                               newline="\n")
        folder = self.root / "work"
        folder.mkdir()
        copy = folder / "copy"
        copy.mkdir()
        record_dir = self.root / "workflows/review-orchestration/runs/review-test"
        record_dir.mkdir()
        call_record = record_dir / "probe-calls.jsonl"
        call_record.write_text("", encoding="utf-8", newline="\n")
        record = {"_path": record_dir / "round.json"}
        brief = crossreview.read_brief(self.root,
                                      self.brief(kind="plan", under="plan.md"))
        usage = SimpleNamespace(claude_usage=object(), save=lambda: None)
        sent = []
        def factory(settings, reviewer, role, **kwargs):
            sent.append((reviewer, role, kwargs["probe_settings"]))
            return object()
        result = SimpleNamespace(reply={"summary": "Sound.", "findings": []},
            launch={"ok": True}, callbacks=None, thread_id="fixture", raw=[])
        with mock.patch.object(providers, "make_read_only", side_effect=factory), \
                mock.patch.object(loop, "call_reviewer", return_value=result):
            self.assertEqual(crossreview._review_session(self.root, brief, "codex",
                {}, usage, folder, copy, "image", "prompt", record), result.reply)
        self.assertEqual(sent[0][:2], ("codex", "plan_reviewer"))
        settings = probe.load_settings(sent[0][2])
        self.assertEqual(Path(settings["record"]), call_record)
        self.assertEqual(Path(settings["copy"]), copy)
        self.assertEqual(probe.run_probe("print(1)", settings,
            run=lambda *a, **kw: (0, "1\n", ""), clock=lambda: 0), "exit 0\n1\n")
        __import__("shutil").rmtree(folder)
        self.assertEqual(len(call_record.read_text(encoding="utf-8").splitlines()), 1)

    def test_real_session_selects_role_for_each_kind_and_reviewer(self):
        import loop
        scripts = self.root / "workflows/review-orchestration/scripts"
        scripts.mkdir()
        (scripts / "tripwire.py").write_text("# fixture\n", encoding="utf-8",
                                               newline="\n")
        sent = []
        result = SimpleNamespace(reply={"summary": "Sound.", "findings": []},
            launch={}, callbacks={}, thread_id="fixture", raw=[])
        def factory(settings, reviewer, role, **kwargs):
            sent.append((reviewer, role, kwargs["probe_settings"]))
            return object()
        with mock.patch.object(providers, "make_read_only", side_effect=factory), \
                mock.patch.object(loop, "call_reviewer", return_value=result):
            for kind in ("plan", "code"):
                for reviewer in ("claude", "codex"):
                    suffix = f"{kind}-{reviewer}"
                    folder = self.root / f"work-{suffix}"
                    folder.mkdir()
                    copy = folder / "copy"
                    copy.mkdir()
                    record_dir = (self.root /
                        f"workflows/review-orchestration/runs/review-{suffix}")
                    record_dir.mkdir()
                    (record_dir / "probe-calls.jsonl").write_text("",
                        encoding="utf-8", newline="\n")
                    brief = crossreview.read_brief(self.root, self.brief(kind=kind,
                        under="plan.md" if kind == "plan" else "code"))
                    usage = SimpleNamespace(claude_usage=object(), save=lambda: None)
                    crossreview._review_session(self.root, brief, reviewer, {}, usage,
                        folder, copy, "image", "prompt", {"_path": record_dir / "round.json"})
                    self.assertEqual(sent[-1][:2], (reviewer,
                        "plan_reviewer" if kind == "plan" else "reviewer"))
                    self.assertEqual(Path(probe.load_settings(sent[-1][2])["record"]),
                                     record_dir / "probe-calls.jsonl")

    def test_usage_preflight_refusal_exits_three_without_record(self):
        import limits
        deps, calls = self.deps()
        deps.read_codex = lambda *a: limits.Reading(limits.CODEX,
            limits.REPORTED, percent=100, raw={"rateLimits": {"credits": {
                "hasCredits": False, "unlimited": False}}})
        args = self.args()
        with self.assertRaisesRegex(crossreview.Refused, "usage preflight"):
            crossreview.run_round(args, root=self.root, deps=deps)
        self.assertNotIn("capture", [name for name, _ in calls])
        self.assertFalse(list((self.root / "workflows/review-orchestration/runs").iterdir()))
        original = crossreview.run_round
        with mock.patch.object(crossreview, "run_round",
                side_effect=lambda arg: original(arg, root=self.root, deps=deps)), \
                mock.patch.dict(os.environ, {crossreview.MARKER: "1"}), \
                mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as output:
            self.assertEqual(crossreview.main(["--review", args.review,
                "--reviewer", "codex", "--git-dir", "personal.git"]), 3)
        self.assertIn("usage preflight", output.getvalue())

    def test_reset_confirmed_is_claude_only(self):
        deps, calls = self.deps()
        args = self.args()
        args.reset_confirmed = True
        with self.assertRaisesRegex(crossreview.Refused, "only a Claude"):
            crossreview.run_round(args, root=self.root, deps=deps)
        self.assertNotIn("capture", [name for name, _ in calls])

    def test_codex_step_usage_can_stop_after_clean_preflight(self):
        import limits
        deps, calls = self.deps()
        reads = [1, 100]
        deps.read_codex = lambda *a: limits.Reading(limits.CODEX,
            limits.REPORTED, percent=reads.pop(0), raw={"rateLimits": {
                "credits": {"hasCredits": False, "unlimited": False}}})
        self.assertEqual(crossreview.run_round(self.args(), root=self.root, deps=deps), 1)
        self.assertEqual(reads, [])
        self.assertNotIn("review", [name for name, _ in calls])
        record = next((self.root / "workflows/review-orchestration/runs").glob(
            "review-*/round.json"))
        self.assertEqual(json.loads(record.read_text(encoding="utf-8"))["stop"],
                         "usage-limit")
        self.assertEqual(len([name for name, _ in calls if name == "capture"]), 2)

    def test_credit_balance_change_between_preflight_and_step_stops(self):
        import limits
        deps, calls = self.deps()
        balances = [10, 9]
        def reading(*args):
            return limits.Reading(limits.CODEX, limits.REPORTED, percent=1,
                raw={"rateLimits": {"credits": {"hasCredits": True,
                    "unlimited": False, "balance": balances.pop(0)}}})
        deps.read_codex = reading
        self.assertEqual(crossreview.run_round(self.args(), root=self.root, deps=deps), 1)
        self.assertEqual(balances, [])
        self.assertNotIn("review", [name for name, _ in calls])
        record = next((self.root / "workflows/review-orchestration/runs").glob(
            "review-*/round.json"))
        data = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(data["stop"], "usage-limit")
        self.assertIn("balance 10 -> 9", data["evidence"]["detail"])

    def test_seed_uses_latest_saved_claude_reading_across_record_types(self):
        runs = self.root / "workflows/review-orchestration/runs"
        round_dir = runs / "review-old"
        round_dir.mkdir()
        old = {"source": "reported", "percent": 12, "read_at": "2026-11-01T10:00:00+00:00"}
        (round_dir / "round.json").write_text(json.dumps({
            "item": "other_item", "kind": "code",
            "started_at": "2026-11-01T09:00:00+00:00",
            "usage": {"claude_last_reading": old}}),
            encoding="utf-8", newline="\n")
        run_dir = runs / "ordinary-old"
        run_dir.mkdir()
        newer = {"source": "reported", "percent": 23}
        (run_dir / "state.json").write_text(json.dumps({
            "created": "2026-11-02T09:00:00+00:00",
            "claude_last_reading": newer}), encoding="utf-8", newline="\n")
        self.assertEqual(crossreview.seed_claude(self.root,
            crossreview.round_records(self.root)), newer)
        latest = {"source": "reported", "percent": 34,
                  "read_at": "2026-11-03T09:00:00+00:00"}
        (round_dir / "round.json").write_text(json.dumps({
            "item": "other_item", "kind": "code",
            "started_at": "2026-11-01T09:00:00+00:00",
            "usage": {"claude_last_reading": latest}}),
            encoding="utf-8", newline="\n")
        self.assertEqual(crossreview.seed_claude(self.root,
            crossreview.round_records(self.root)), latest)
        deps, _ = self.deps()
        self.assertEqual(crossreview.run_round(self.args(), root=self.root, deps=deps), 0)
        current = next(path for path in runs.glob("review-*/round.json")
                       if path != round_dir / "round.json")
        data = json.loads(current.read_text(encoding="utf-8"))
        self.assertEqual(data["usage"]["claude_last_reading"], latest)

    def test_code_base_passes_since_to_only_two_guards(self):
        args = self.args()
        args.review = self.brief(kind="code", under="code")
        args.base = git(self.root, "rev-parse", "HEAD")
        brief = crossreview.read_brief(self.root, args.review)
        seen = []
        for blocked in ("doc-sync-guard", "ai-style-guard"):
            seen.clear()
            def check(argv, **kwargs):
                seen.append(argv)
                fail = blocked in argv[1] and "--since" in argv
                return warned("drift since the base") if fail else passing(argv)
            with self.subTest(blocked=blocked), self.assertRaisesRegex(
                    crossreview.Refused, blocked):
                crossreview.mechanical(self.root, brief, args.base, run=check)
            self.assertEqual([call[-2:] for call in seen if "--since" in call],
                [["--since", args.base]] if blocked == "doc-sync-guard" else
                [["--since", args.base], ["--since", args.base]])
        seen.clear()
        crossreview.mechanical(self.root, brief, None,
            run=lambda argv, **kw: (seen.append(argv) or passing(argv)))
        self.assertFalse(any("--since" in call for call in seen))

    def test_code_round_base_refuses_failed_since_guard_before_capture(self):
        args = self.args()
        args.review = self.brief(kind="code", under="code")
        args.base = git(self.root, "rev-parse", "HEAD")
        for blocked in ("doc-sync-guard", "ai-style-guard"):
            deps, calls = self.deps()
            def check(argv, *, cwd, check=True):
                calls.append(("check", argv))
                fail = blocked in argv[1] and "--since" in argv
                return warned("drift since the base") if fail else passing(argv)
            deps.command = check
            with self.subTest(blocked=blocked), self.assertRaisesRegex(
                    crossreview.Refused, blocked):
                crossreview.run_round(args, root=self.root, deps=deps)
            self.assertNotIn("capture", [name for name, _ in calls])
            self.assertTrue(any(blocked in argv[1] and argv[-2:] ==
                ["--since", args.base] for name, argv in calls if name == "check"))

    def test_round_calls_shared_usage_pieces_at_their_steps(self):
        import loop
        deps, calls = self.deps()
        traces = []
        preflight = loop.usage_preflight
        before = loop.Run.usage_before_step
        envelope = loop.usage_envelope
        def traced_preflight(*args, **kwargs):
            traces.append(("preflight", args[2]))
            return preflight(*args, **kwargs)
        def traced_before(*args, **kwargs):
            traces.append(("before", args[2]))
            return before(*args, **kwargs)
        async def traced_envelope(*args, **kwargs):
            traces.append(("envelope", args[2]))
            return await envelope(*args, **kwargs)
        with mock.patch.object(loop, "usage_preflight", side_effect=traced_preflight), \
                mock.patch.object(loop.Run, "usage_before_step",
                                  side_effect=traced_before), \
                mock.patch.object(loop, "usage_envelope", side_effect=traced_envelope):
            self.assertEqual(crossreview.run_round(self.args(), root=self.root, deps=deps), 0)
        self.assertEqual(traces, [("preflight", "review"),
                                  ("envelope", "review"), ("before", "review")])

    def test_prompt_claims_plan_code_and_named_test(self):
        for kind, checks, wanted in (("plan", "None", "No test file was run"),
                                     ("code", "None", "No test file was run"),
                                     ("code", "code/b.py", "Test files run: code/b.py")):
            with self.subTest(kind=kind, checks=checks):
                brief = crossreview.read_brief(self.root,
                    self.brief(kind=kind, under="plan.md" if kind == "plan" else "code",
                               checks=checks))
                rendered = crossreview._prompt(self.root, brief, 1, None, [], "diff",
                                                "codex", ["code/a.py"])
                self.assertIn(wanted, rendered)

    def test_repair_wording_follows_the_packets_history_not_the_round_number(self):
        # A by-hand finish starts its own empty packet at a round above the run's
        # labels (plan 10.5 "Finishing by hand"); its first round is no repair round,
        # and every round says reraises may name only this packet's labels (R11-1).
        brief = crossreview.read_brief(self.root, self.brief(kind="plan", under="plan.md"))
        empty = crossreview.packet_template("test_item")
        held = empty.replace("## Addressed - plan readiness\n\nNone.",
                             "## Addressed - plan readiness\n\n- **R11-1 - Fixed.**\n"
                             "  Repair (2026-11-18): done")
        for round_no, packet, repair in ((12, empty, False), (12, None, False),
                                         (1, empty, False), (12, held, True)):
            with self.subTest(round_no=round_no, packet=packet is held):
                rendered = crossreview._prompt(self.root, brief, round_no, packet, [],
                                               "diff", "codex", [])
                self.assertEqual("This is a repair round." in rendered, repair)
                self.assertIn("reraises may name only a label in this round's packet",
                              rendered)
                # The run's packet the brief names is read and checked (R12-1).
                self.assertIn("If the brief names another packet", rendered)
                self.assertIn("check each of its findings and dated Repair lines",
                              rendered)
                self.assertIn(f"R{round_no}", rendered)

    def test_findings_stop_survives_copy_cleanup_failure(self):
        reply = {"summary": "One failure.", "findings": [{
            "title": "A missing check", "class": "plan-readiness", "section": "Section 2",
            "wrong": "Absent.", "consequence": "False pass.", "triage": "Fix.",
            "fix": "Add check.", "reraises": ""}]}
        deps, calls = self.deps(reply)
        copy_path = []
        def fail(path):
            copy_path.append(path)
            raise OSError("copy removal failed")
        deps.remove_copy = fail
        self.assertEqual(crossreview.run_round(self.args(), root=self.root, deps=deps), 1)
        record = next((self.root / "workflows/review-orchestration/runs").glob(
            "review-*/round.json"))
        data = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(data["stop"], "findings")
        self.assertIn("copy removal", data["cleanup_error"])
        self.assertFalse(copy_path[0].exists())
        self.assertFalse(copy_path[0].parent.exists())

    def test_clean_round_has_two_captures_and_record(self):
        deps, calls = self.deps()
        code = crossreview.run_round(self.args(), root=self.root, deps=deps)
        self.assertEqual(code, 0)
        self.assertEqual([c[0] for c in calls if c[0] in ("capture", "review")],
                         ["capture", "review", "capture"])
        record = next((self.root / "workflows/review-orchestration/runs").glob(
            "review-*/round.json"))
        data = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(data["stop"], "clean")
        self.assertEqual(data["usage"]["status"], "clean")
        self.assertLessEqual(set(data["usage"]), {
            "credits", "paid_usage_pending", "paid_usage", "paid_usage_notes",
            "claude_unknown_reset_resume", "claude_last_reading", "readings",
            "usage_source", "codex_last_five_hour", "frozen_dir", "status"})
        self.assertTrue((record.parent / "probe-calls.jsonl").exists())

    def test_claude_round_uses_shared_probe_before_session(self):
        import limits
        deps, calls = self.deps()
        args = self.args()
        args.reviewer = "claude"
        probes = []
        def fake_probe(model, cwd, received):
            probes.append((model, cwd))
            return SimpleNamespace(reading=limits.Reading(limits.CLAUDE,
                limits.REPORTED, percent=1, raw={}), tokens=1, usage={},
                model=model, events=["rate-limit"])
        deps.probe_claude = fake_probe
        self.assertEqual(crossreview.run_round(args, root=self.root, deps=deps), 0)
        self.assertEqual(len(probes), 1)
        self.assertEqual([c[0] for c in calls if c[0] in ("capture", "review")],
                         ["capture", "review", "capture"])
        record = next((self.root / "workflows/review-orchestration/runs").glob(
            "review-*/round.json"))
        usage = json.loads(record.read_text(encoding="utf-8"))["usage"]
        self.assertNotIn("planning", usage)
        self.assertIn("usage_probe", usage["readings"][-1])

    def test_claude_ceiling_event_does_not_interrupt_a_reviewer(self):
        import limits
        deps, calls = self.deps()
        args = self.args()
        args.reviewer = "claude"
        deps.probe_claude = lambda model, cwd, received: SimpleNamespace(
            reading=limits.Reading(limits.CLAUDE, limits.REPORTED, percent=1,
                                   raw={}), tokens=1, usage={}, model=model, events=[])
        original = deps.review_session
        event = SimpleNamespace(rate_limit_info=SimpleNamespace(
            status="allowed", rate_limit_type="five_hour", resets_at=None,
            utilization=0.95, raw={"unifiedWindows": {
                "five_hour": {"utilization": 0.95, "resetsAt": None}}}))
        class Client:
            async def interrupt(self):
                raise AssertionError("reviewer was interrupted at the ceiling")
        def review(*values):
            usage = values[4]
            self.assertIsNone(usage.claude_usage.step_ceiling)
            asyncio.run(providers.watch_claude_event(Client(),
                usage.claude_usage, event))
            return original(*values)
        deps.review_session = review
        self.assertEqual(crossreview.run_round(args, root=self.root, deps=deps), 0)
        self.assertIn("review", [name for name, _ in calls])
        record = next((self.root / "workflows/review-orchestration/runs").glob(
            "review-*/round.json"))
        data = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(data["usage"]["claude_last_reading"]["percent"], 95.0)

    def test_claude_session_error_keeps_its_usage_event(self):
        import limits
        deps, _ = self.deps()
        args = self.args()
        args.reviewer = "claude"
        deps.probe_claude = lambda model, cwd, received: SimpleNamespace(
            reading=limits.Reading(limits.CLAUDE, limits.REPORTED, percent=1,
                                   raw={}), tokens=1, usage={}, model=model, events=[])
        event = SimpleNamespace(rate_limit_info=SimpleNamespace(
            status="allowed", rate_limit_type="five_hour", resets_at=None,
            utilization=0.2, raw={"unifiedWindows": {
                "five_hour": {"utilization": 0.2, "resetsAt": None}}}))
        def fail_after_event(*values):
            values[4].claude_usage.observe(event)
            raise crossreview.RoundError("session failed after the event")
        deps.review_session = fail_after_event
        self.assertEqual(crossreview.run_round(args, root=self.root, deps=deps), 1)
        record = next((self.root / "workflows/review-orchestration/runs").glob(
            "review-*/round.json"))
        data = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(data["stop"], "error")
        self.assertEqual(data["usage"]["claude_last_reading"]["percent"], 20.0)
        self.assertIn("session failed after the event", data["evidence"]["error"])

    def test_dry_run_stops_before_capture_and_writes_no_round(self):
        deps, calls = self.deps()
        code = crossreview.run_round(self.args(dry=True), root=self.root, deps=deps)
        self.assertEqual(code, 0)
        self.assertNotIn("capture", [c[0] for c in calls])
        self.assertFalse(list((self.root / "workflows/review-orchestration/runs").iterdir()))
        self.assertFalse((self.root / "memory/test_item_review_packet.md").exists())

    def test_claude_dry_run_sends_no_usage_probe(self):
        deps, calls = self.deps()
        args = self.args(dry=True)
        args.reviewer = "claude"
        deps.probe_claude = lambda *a: self.fail("dry run sent a Claude probe")
        self.assertEqual(crossreview.run_round(args, root=self.root, deps=deps), 0)
        self.assertNotIn("review", [name for name, _ in calls])
        self.assertNotIn("capture", [name for name, _ in calls])
        self.assertFalse(list((self.root / "workflows/review-orchestration/runs").iterdir()))

    def test_gitignored_only_check_write_is_accepted(self):
        brief = crossreview.read_brief(self.root, self.brief(
            kind="code", under="code", checks="code/b.py"))
        calls = []
        def check(argv, **kwargs):
            calls.append(argv)
            (self.root / "memory/ignored-check-output.txt").write_text(
                "check output\n", encoding="utf-8", newline="\n")
            return passing(argv)
        results = crossreview.mechanical(self.root, brief, None, run=check)
        self.assertEqual(len(calls), 5)
        self.assertTrue(all(line.startswith("PASS:") for line in results))
        self.assertTrue((self.root / "memory/ignored-check-output.txt").exists())

    def test_code_dry_run_names_test_without_running_it(self):
        (self.root / "code/a.py").write_text("changed\n", encoding="utf-8", newline="\n")
        args = self.args(dry=True)
        args.review = self.brief(kind="code", under="code", checks="code/b.py",
            excluded="workflows - temporary fixture files")
        deps, calls = self.deps()
        with mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as output:
            self.assertEqual(crossreview.run_round(args, root=self.root, deps=deps), 0)
        self.assertFalse(any(c[0] == "check" and Path(c[1][1]) == self.root / "code/b.py"
                             for c in calls))
        self.assertIn("NOT RUN:", output.getvalue())
        # The check is shown as its command, an absolute path in the system's form.
        self.assertIn("code/b.py", output.getvalue().replace("\\", "/"))
        self.assertNotIn("capture", [c[0] for c in calls])
        self.assertFalse((self.root / "memory/test_item_review_packet.md").exists())

    def test_windows_spelling_dry_run_never_runs_a_checks_file(self):
        brief = crossreview.read_brief(self.root,
            self.brief(kind="code", under="code", checks="code/b.py"))
        class WindowsSpelling:
            def __init__(self, value):
                self.value = str(value).replace("/", "\\")
            def __truediv__(self, item):
                return WindowsSpelling(self.value + "\\" + str(item))
            def __str__(self):
                return self.value
        calls = []
        def fake(argv, **kwargs):
            calls.append(argv)
            return passing(argv)
        with mock.patch.object(crossreview, "Path", WindowsSpelling), \
                mock.patch.object(crossreview, "tree_state", return_value=("", "", {})):
            result = crossreview.mechanical(self.root, brief, None, dry_run=True, run=fake)
        self.assertTrue(any(line.startswith("NOT RUN:") and "\\code\\b.py" in line
                            for line in result))
        self.assertFalse(any("\\code\\b.py" in str(call) for call in calls))

    def test_failed_mechanical_check_refuses_before_capture(self):
        deps, calls = self.deps()
        deps.command = lambda argv, *, cwd, check=True: SimpleNamespace(
            returncode=1, stdout="test failed")
        with self.assertRaisesRegex(crossreview.Refused, "test failed"):
            crossreview.run_round(self.args(), root=self.root, deps=deps)
        self.assertNotIn("capture", [c[0] for c in calls])

    def test_capture_failure_has_no_session_or_record(self):
        deps, calls = self.deps(fail_at="capture")
        self.assertEqual(crossreview.run_round(self.args(), root=self.root, deps=deps), 1)
        self.assertNotIn("review", [c[0] for c in calls])
        self.assertFalse(list((self.root / "workflows/review-orchestration/runs").iterdir()))

    def test_failed_closing_capture_keeps_stop_and_exits_one(self):
        deps, calls = self.deps(fail_at="closing")
        self.assertEqual(crossreview.run_round(self.args(), root=self.root, deps=deps), 1)
        record = next((self.root / "workflows/review-orchestration/runs").glob(
            "review-*/round.json"))
        data = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(data["stop"], "clean")
        self.assertIn("closing_capture_failed", data)

    def test_session_failure_still_closes_and_keeps_record(self):
        deps, calls = self.deps(fail_at="review")
        self.assertEqual(crossreview.run_round(self.args(), root=self.root, deps=deps), 1)
        self.assertEqual([c[0] for c in calls if c[0] == "capture"],
                         ["capture", "capture"])
        record = next((self.root / "workflows/review-orchestration/runs").glob(
            "review-*/round.json"))
        self.assertEqual(json.loads(record.read_text(encoding="utf-8"))["stop"], "error")

    def test_docker_preparation_failure_closes_without_session(self):
        deps, calls = self.deps()
        deps.docker_running = lambda: False
        deps.prepare_image = lambda *a, **kw: (_ for _ in ()).throw(
            crossreview.RoundError("image failed"))
        self.assertEqual(crossreview.run_round(self.args(), root=self.root, deps=deps), 1)
        self.assertNotIn("review", [c[0] for c in calls])
        self.assertEqual([c[0] for c in calls if c[0] in ("capture", "docker", "stop")],
                         ["capture", "docker", "stop", "capture"])

    def test_docker_already_running_when_ensured_is_not_stopped(self):
        # Code review R8-1: Docker Desktop still starting when the round looks, and
        # running by the time ensure_docker checks, was not started by the round, so
        # the round must not stop it.
        deps, calls = self.deps()
        deps.docker_running = lambda: False
        deps.ensure_docker = lambda: calls.append(("ensure", None)) or False
        self.assertEqual(crossreview.run_round(self.args(), root=self.root, deps=deps), 0)
        self.assertIn(("ensure", None), calls)
        self.assertNotIn(("stop", None), calls)

    def test_round_record_keeps_the_step_4_reading(self):
        # Code review R8-2: round.json holds step 4's reading, not only the console.
        deps, calls = self.deps()
        self.assertEqual(crossreview.run_round(self.args(), root=self.root, deps=deps), 0)
        record = next((self.root / "workflows/review-orchestration/runs").glob(
            "review-*/round.json"))
        kept = json.loads(record.read_text(encoding="utf-8"))["preflight_reading"]
        self.assertEqual(set(kept), {"codex", "claude_saved"})
        self.assertIn("1%", kept["codex"])
        self.assertTrue(kept["claude_saved"])

    def test_copy_failure_closes_without_session_or_docker_stop(self):
        deps, calls = self.deps()
        deps.make_copy = lambda *a: (_ for _ in ()).throw(
            crossreview.RoundError("copy failed"))
        self.assertEqual(crossreview.run_round(self.args(), root=self.root, deps=deps), 1)
        self.assertEqual([c[0] for c in calls if c[0] in ("capture", "review", "stop")],
                         ["capture", "capture"])

    def test_plan_diff_failure_is_recorded_and_closing_capture_runs(self):
        deps, calls = self.deps()
        with mock.patch.object(crossreview, "plan_diff", side_effect=OSError("diff failed")):
            self.assertEqual(crossreview.run_round(self.args(), root=self.root, deps=deps), 1)
        self.assertNotIn("review", [c[0] for c in calls])
        self.assertEqual(len([c for c in calls if c[0] == "capture"]), 2)
        record = next((self.root / "workflows/review-orchestration/runs").glob(
            "review-*/round.json"))
        data = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(data["stop"], "error")
        self.assertIn("plan diff", data["evidence"])

    def test_envelope_failure_is_recorded_and_closing_capture_runs(self):
        deps, calls = self.deps()
        with mock.patch("loop.usage_envelope", side_effect=RuntimeError("envelope failed")):
            self.assertEqual(crossreview.run_round(self.args(), root=self.root, deps=deps), 1)
        record = next((self.root / "workflows/review-orchestration/runs").glob(
            "review-*/round.json"))
        data = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(data["stop"], "error")
        self.assertIn("envelope failed", data["evidence"])
        self.assertEqual(len([c for c in calls if c[0] == "capture"]), 2)

    def test_reviewed_file_changed_before_or_during_session_is_error(self):
        for point in ("copy", "review"):
            (self.root / "code/a.py").write_text("changed\n", encoding="utf-8", newline="\n")
            deps, calls = self.deps()
            args = self.args()
            args.review = self.brief(kind="code", under="code",
                                     excluded="workflows - temporary fixture files")
            if point == "copy":
                original = deps.make_copy
                def change_after_copy(*values):
                    original(*values)
                    (self.root / "code/a.py").write_text("moved\n", encoding="utf-8", newline="\n")
                deps.make_copy = change_after_copy
            else:
                original = deps.review_session
                def change_during_review(*values):
                    (self.root / "code/a.py").write_text("moved\n", encoding="utf-8", newline="\n")
                    return original(*values)
                deps.review_session = change_during_review
            with self.subTest(point=point):
                self.assertEqual(crossreview.run_round(args, root=self.root, deps=deps), 1)
                self.assertEqual([c[0] for c in calls if c[0] == "review"],
                                 [] if point == "copy" else ["review"])
                self.assertEqual(len([c for c in calls if c[0] == "capture"]), 2)
                self.assertIn("None.", (self.root / "memory/test_item_review_packet.md").read_text(encoding="utf-8"))

    def test_plan_document_change_during_session_is_error_without_findings(self):
        deps, calls = self.deps()
        original = deps.review_session
        def change_plan(*values):
            (self.root / "plan.md").write_text("changed during review\n",
                                               encoding="utf-8", newline="\n")
            return original(*values)
        deps.review_session = change_plan
        self.assertEqual(crossreview.run_round(self.args(), root=self.root, deps=deps), 1)
        self.assertIn("review", [name for name, _ in calls])
        record = next((self.root / "workflows/review-orchestration/runs").glob(
            "review-*/round.json"))
        data = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(data["stop"], "error")
        self.assertIn("plan document changed during the session",
                      data["evidence"]["error"])
        self.assertEqual(data["findings"], [])
        self.assertIn("None.", (self.root / "memory/test_item_review_packet.md").read_text(
            encoding="utf-8"))

    def test_inventory_catches_binary_bytes_deletion_and_new_member(self):
        runs = self.root / "workflows/review-orchestration/runs"
        for case in ("binary", "delete", "new"):
            (self.root / "code/a.py").write_text("changed\n", encoding="utf-8", newline="\n")
            binary = self.root / "code/new.bin"
            if case == "binary":
                binary.write_bytes(b"\x00a")
            deps, calls = self.deps()
            args = self.args()
            args.review = self.brief(kind="code", under="code",
                                     excluded="workflows - temporary fixture files")
            original = deps.review_session
            def change(*values):
                if case == "binary":
                    binary.write_bytes(b"\x00b")
                elif case == "delete":
                    (self.root / "code/a.py").unlink()
                else:
                    (self.root / "code/extra.py").write_text("added\n", encoding="utf-8", newline="\n")
                return original(*values)
            deps.review_session = change
            before = set(runs.glob("review-*/round.json"))
            with self.subTest(case=case):
                self.assertEqual(crossreview.run_round(args, root=self.root, deps=deps), 1)
                record = (set(runs.glob("review-*/round.json")) - before).pop()
                evidence = json.loads(record.read_text(encoding="utf-8"))["evidence"]
                self.assertIn("code/new.bin" if case == "binary" else
                              "code/a.py" if case == "delete" else "code/extra.py",
                              str(evidence))
            if binary.exists():
                binary.unlink()
            extra = self.root / "code/extra.py"
            if extra.exists():
                extra.unlink()

    def test_cleanup_failure_keeps_clean_stop_but_exits_one(self):
        deps, calls = self.deps()
        deps.remove_copy = lambda path: (_ for _ in ()).throw(
            crossreview.RoundError("remove failed"))
        self.assertEqual(crossreview.run_round(self.args(), root=self.root, deps=deps), 1)
        record = next((self.root / "workflows/review-orchestration/runs").glob(
            "review-*/round.json"))
        data = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(data["stop"], "clean")
        self.assertIn("copy removal", data["cleanup_error"])

    def test_preexisting_work_folder_is_never_removed(self):
        deps, calls = self.deps()
        name = "review-fixed-id"
        folder = Path(self.temp.name) / "book-dragon-orchestration" / name
        folder.mkdir(parents=True)
        marker = folder / "keep.txt"
        marker.write_text("someone else's file", encoding="utf-8", newline="\n")
        with mock.patch.object(crossreview, "_id", return_value="fixed-id"), mock.patch.object(
                crossreview.tempfile, "gettempdir", return_value=self.temp.name):
            self.assertEqual(crossreview.run_round(self.args(), root=self.root, deps=deps), 1)
        self.assertTrue(marker.exists())
        self.assertNotIn("review", [c[0] for c in calls])
        self.assertEqual(len([c for c in calls if c[0] == "capture"]), 2)

    def test_paid_permission_covers_one_step_and_is_cleared(self):
        deps, calls = self.deps()
        args = self.args()
        args.allow_paid = True
        import limits
        balances = [10, 9, 8]
        deps.read_codex = lambda *a: limits.Reading(limits.CODEX, limits.REPORTED,
            percent=1, raw={"rateLimits": {"credits": {"hasCredits": True,
                "unlimited": False, "balance": balances.pop(0)}}})
        self.assertEqual(crossreview.run_round(args, root=self.root, deps=deps), 0)
        record = next((self.root / "workflows/review-orchestration/runs").glob(
            "review-*/round.json"))
        usage = json.loads(record.read_text(encoding="utf-8"))["usage"]
        self.assertEqual(usage["paid_usage"][0]["step"], "review")
        self.assertNotIn("paid_usage_pending", usage)
        self.assertEqual(usage["credits"]["balance"], 8)
        self.assertEqual(balances, [])

    def test_model_not_offered_refuses_before_capture(self):
        deps, calls = self.deps()
        deps.model_offered = lambda *a: False
        with self.assertRaisesRegex(crossreview.Refused, "not offered"):
            crossreview.run_round(self.args(), root=self.root, deps=deps)
        self.assertNotIn("capture", [c[0] for c in calls])
        self.assertFalse(list((self.root / "workflows/review-orchestration/runs").iterdir()))

    def test_failed_model_list_refuses_before_capture(self):
        deps, calls = self.deps()
        deps.model_offered = lambda *a: (_ for _ in ()).throw(OSError("list failed"))
        with self.assertRaisesRegex(crossreview.Refused, "model list could not be read"):
            crossreview.run_round(self.args(), root=self.root, deps=deps)
        self.assertNotIn("capture", [c[0] for c in calls])

    def test_deleted_exclusion_absent_from_base_refuses_before_capture(self):
        (self.root / "code/a.py").write_text("changed\n", encoding="utf-8", newline="\n")
        args = self.args()
        args.review = self.brief(kind="code", under="code",
            excluded="workflows - temporary fixture files\nmissing.py - deleted")
        deps, calls = self.deps()
        with self.assertRaisesRegex(crossreview.Refused, "missing.py"):
            crossreview.run_round(args, root=self.root, deps=deps)
        self.assertNotIn("capture", [c[0] for c in calls])

    def test_build_gate_checks_links_without_packet_reader(self):
        args = self.args()
        args.build_ready = True
        args.reviewer = None
        with mock.patch.object(crossreview, "packet_status",
                               side_effect=AssertionError("gate read the packet")):
            self.assertEqual(crossreview.run_round(args, root=self.root), 1)

    def test_findings_are_written_and_exit_two(self):
        reply = {"summary": "One realistic failure.", "findings": [{
            "title": "A missing check", "class": "plan-readiness", "section": "Section 2",
            "wrong": "The check is absent.", "consequence": "A false pass is possible.",
            "triage": "Fix before building.", "fix": "Add the check.", "reraises": ""}]}
        deps, calls = self.deps(reply)
        self.assertEqual(crossreview.run_round(self.args(), root=self.root, deps=deps), 2)
        text = (self.root / "memory/test_item_review_packet.md").read_text(encoding="utf-8")
        self.assertIn("R1-1 - A missing check.", text)
        self.assertEqual(len(crossreview._gather(self.root).review_packets(self.root)[0]["open"]), 1)

    def test_both_finding_classes_go_to_their_packet_sections(self):
        one = {"title": "A missing check", "class": "plan-readiness",
               "section": "Section 2", "wrong": "Missing check.",
               "consequence": "Wrong plan passes.", "triage": "Fix it.",
               "fix": "Add check.", "reraises": ""}
        two = {**one, "title": "A broken review", "class": "review-process"}
        deps, _ = self.deps({"summary": "Two faults.", "findings": [one, two]})
        self.assertEqual(crossreview.run_round(self.args(), root=self.root, deps=deps), 2)
        packet = (self.root / "memory/test_item_review_packet.md").read_text(
            encoding="utf-8")
        self.assertIn("R1-1 - A missing check.", packet.split("## Open - review process")[0])
        self.assertIn("R1-2 - A broken review.", packet.split("## Open - review process")[1])
        self.assertEqual(len(crossreview._gather(self.root).review_packets(self.root)[0]["open"]), 2)

    def test_reply_blank_field_ends_error_without_findings(self):
        reply = {"summary": " ", "findings": []}
        deps, calls = self.deps(reply)
        self.assertEqual(crossreview.run_round(self.args(), root=self.root, deps=deps), 1)
        self.assertIn("None.", (self.root / "memory/test_item_review_packet.md").read_text(encoding="utf-8"))

    def claude_probe(self, calls, *, fail=False):
        import limits
        def probe(model, cwd, received):
            calls.append(("probe", None))
            if fail:
                raise RuntimeError("probe failed")
            return SimpleNamespace(reading=limits.Reading(limits.CLAUDE,
                limits.REPORTED, percent=1, raw={}), tokens=1, usage={},
                model=model, events=[])
        return probe

    def latest_record(self):
        runs = self.root / "workflows/review-orchestration/runs"
        path = max(runs.glob("review-*/round.json"),
                   key=lambda p: json.loads(p.read_text(encoding="utf-8"))["started_at"])
        return json.loads(path.read_text(encoding="utf-8"))

    def test_check_creating_an_unignored_file_refuses_before_capture(self):
        (self.root / "code/a.py").write_text("changed\n", encoding="utf-8", newline="\n")
        args = self.args()
        args.review = self.brief(kind="code", under="code", checks="code/b.py",
                                 excluded="workflows - temporary fixture files")
        deps, calls = self.deps()
        created = self.root / "created-by-check.txt"
        def check(argv, *, cwd, check=True):
            calls.append(("check", argv))
            if Path(argv[1]) == self.root / "code/b.py":
                created.write_text("made by the check\n", encoding="utf-8", newline="\n")
            return passing(argv)
        deps.command = check
        with self.assertRaisesRegex(crossreview.Refused,
                                    "paths changed: created-by-check.txt"):
            crossreview.run_round(args, root=self.root, deps=deps)
        self.assertTrue(created.exists())
        self.assertNotIn("capture", [name for name, _ in calls])
        self.assertFalse(list((self.root / "workflows/review-orchestration/runs").iterdir()))

    def test_failed_claude_probe_ends_the_round_error(self):
        # The mapping table: a failed probe ends the round error, after the record
        # is written, with the closing capture run and no session.
        deps, calls = self.deps()
        args = self.args()
        args.reviewer = "claude"
        deps.probe_claude = self.claude_probe(calls, fail=True)
        self.assertEqual(crossreview.run_round(args, root=self.root, deps=deps), 1)
        self.assertEqual([c[0] for c in calls if c[0] in ("capture", "probe", "review")],
                         ["capture", "probe", "capture"])
        data = self.latest_record()
        self.assertEqual(data["stop"], "error")
        self.assertIn("probe failed", json.dumps(data["evidence"]))

    def test_claude_round_sends_its_probe_only_after_the_first_capture(self):
        # Step 4 sends no Claude probe (R82-1): the one probe comes at step 9,
        # after the first capture and straight before the session.
        deps, calls = self.deps()
        args = self.args()
        args.reviewer = "claude"
        deps.probe_claude = self.claude_probe(calls)
        self.assertEqual(crossreview.run_round(args, root=self.root, deps=deps), 0)
        self.assertEqual([c[0] for c in calls if c[0] in ("capture", "probe", "review")],
                         ["capture", "probe", "review", "capture"])

    def test_reset_confirmed_lets_a_claude_round_past_an_unknown_reset(self):
        # A saved paid-overage reading with no reset time refuses a Claude round at
        # step 4; --reset-confirmed sets the unknown-reset flag there and the round
        # goes on to its probe and session.
        import limits
        old = self.root / "workflows/review-orchestration/runs/review-old"
        old.mkdir()
        (old / "round.json").write_text(json.dumps({
            "item": "other_item", "kind": "code",
            "started_at": "2026-11-17T09:00:00+00:00",
            "usage": {"claude_last_reading": {
                "source": limits.LIMIT_REPORTED, "reason": "using paid overage",
                "percent": 95, "resets_at": None,
                "read_at": "2026-11-17T09:00:00+00:00"}}}),
            encoding="utf-8", newline="\n")
        args = self.args()
        args.reviewer = "claude"
        deps, calls = self.deps()
        deps.probe_claude = self.claude_probe(calls)
        with self.assertRaisesRegex(crossreview.Refused, "usage preflight"):
            crossreview.run_round(args, root=self.root, deps=deps)
        self.assertNotIn("capture", [name for name, _ in calls])
        args.reset_confirmed = True
        deps, calls = self.deps()
        deps.probe_claude = self.claude_probe(calls)
        import loop
        with mock.patch.object(loop, "usage_preflight",
                               wraps=loop.usage_preflight) as preflight:
            self.assertEqual(crossreview.run_round(args, root=self.root, deps=deps), 0)
        self.assertIs(preflight.call_args.kwargs["user_resume"], True)
        self.assertEqual([c[0] for c in calls if c[0] in ("capture", "probe", "review")],
                         ["capture", "probe", "review", "capture"])
        self.assertIn("unknown reset",
                      self.latest_record()["preflight_reading"]["claude_saved"])

    def test_plan_round_given_base_diffs_from_it(self):
        args = self.args()
        args.base = "main"
        deps, calls = self.deps()
        seen = []
        def fake_diff(root, git_dir, brief, records, capture_commit, explicit=None):
            seen.append((capture_commit, explicit))
            return explicit, f"Plan diff from {explicit} to {capture_commit}"
        with mock.patch.object(crossreview, "resolve_base",
                               return_value="c" * 40) as resolved, \
                mock.patch.object(crossreview, "plan_diff", side_effect=fake_diff):
            self.assertEqual(crossreview.run_round(args, root=self.root, deps=deps), 0)
        self.assertEqual(resolved.call_args.args,
                         (self.root, "personal.git", "main", "plan"))
        self.assertEqual(seen, [("a" * 40, "c" * 40)])
        self.assertEqual(self.latest_record()["base"], "c" * 40)

    def test_every_end_after_the_first_capture_closes_and_cleans_up(self):
        # Clean, error, usage-limit and findings: each has its closing capture
        # naming the stop, and the copy and work folder are gone afterwards.
        import limits
        finding = {"title": "A missing check", "class": "plan-readiness",
                   "section": "Section 2", "wrong": "Absent.",
                   "consequence": "False pass.", "triage": "Fix.",
                   "fix": "Add check.", "reraises": ""}
        def usage_limit(deps):
            reads = [1, 100]
            deps.read_codex = lambda *a: limits.Reading(limits.CODEX,
                limits.REPORTED, percent=reads.pop(0), raw={"rateLimits": {
                    "credits": {"hasCredits": False, "unlimited": False}}})
        # findings last: its open finding refuses any later round on this packet.
        # Code rounds, so each later round has no earlier plan capture to diff from.
        (self.root / "code/a.py").write_text("changed\n", encoding="utf-8", newline="\n")
        args = self.args()
        args.review = self.brief(kind="code", under="code",
                                 excluded="workflows - temporary fixture files")
        sound = {"summary": "Sound.", "findings": [], "files_reviewed": ["code/a.py"]}
        cases = (("clean", sound, None, None, 0), ("error", sound, "review", None, 1),
                 ("usage-limit", sound, None, usage_limit, 1),
                 ("findings", {**sound, "summary": "One fault.", "findings": [finding]},
                  None, None, 2))
        for stop, reply, fail_at, adjust, code in cases:
            deps, calls = self.deps(reply, fail_at=fail_at)
            if adjust:
                adjust(deps)
            copies = []
            original = deps.make_copy
            def make_copy(root, dest, head, floor, original=original, copies=copies):
                copies.append(dest)
                original(root, dest, head, floor)
            deps.make_copy = make_copy
            with self.subTest(stop=stop):
                self.assertEqual(crossreview.run_round(args, root=self.root,
                                                       deps=deps), code)
                data = self.latest_record()
                self.assertEqual(data["stop"], stop, data.get("evidence"))
                self.assertEqual(bool(copies), stop != "usage-limit", data.get("evidence"))
                self.assertFalse(any(copy.exists() for copy in copies))
                self.assertFalse(Path(data["usage"]["frozen_dir"]).exists())
                closing = [message for name, message in calls if name == "capture"]
                self.assertEqual(len(closing), 2)
                self.assertTrue(closing[1].endswith(f": {stop}"), closing[1])

    # R86-2 over the real guards. The fakes above stand in for the guards' exit
    # codes; these copy doc-sync-guard and ai-style-guard into the temporary
    # project and run them, over a change that is committed with no working-tree
    # edits, so only --since <base> can show it to them.

    CODE_CONTEXT = ("# Code\n\n**Last modified:** {date}\n\n## Purpose\n\nFixture code.\n\n"
                    "## Revision History\n\n{entries}\n")

    def commit_all(self, message):
        identity = ("-c", "user.name=Test", "-c", "user.email=test@example.invalid")
        git(self.root, *identity, "add", "-A")
        git(self.root, *identity, "commit", "-qm", message)
        return git(self.root, "rev-parse", "HEAD")

    def real_guard_project(self):
        from shutil import copytree, ignore_patterns
        project = SCRIPTS.parents[2]
        for guard in ("doc-sync-guard", "ai-style-guard"):
            copytree(project / "workflows" / guard, self.root / "workflows" / guard,
                     ignore=ignore_patterns("__pycache__", "LOG.md", "tests"))
        # ai-style-guard first hands itself to the project .venv. This temporary
        # project has none, and that hand-off is not what is under test.
        runtime = self.root / "workflows/biblio-tools/scripts/runtime.py"
        runtime.parent.mkdir(parents=True)
        runtime.write_text("def ensure_project_runtime():\n    pass\n",
                           encoding="utf-8", newline="\n")
        with open(self.root / ".gitignore", "a", encoding="utf-8", newline="\n") as handle:
            handle.write("LOG.md\n__pycache__/\n")
        (self.root / "code/CONTEXT.md").write_text(self.CODE_CONTEXT.format(
            date="2026-11-01", entries="- [2026-11-01] - Initial creation."),
            encoding="utf-8", newline="\n")
        (self.root / "code/LOG.md").write_text(
            "[2026-01-01T00:00:00+00:00] | Actor: Biblio | Action: created | "
            "Note: Fixture.\n", encoding="utf-8", newline="\n")
        return self.commit_all("guards and a documented directory")

    def change_code(self, text):
        (self.root / "code/a.py").write_text(text, encoding="utf-8", newline="\n")
        (self.root / "code/CONTEXT.md").write_text(self.CODE_CONTEXT.format(
            date="2026-11-02", entries="- [2026-11-01] - Initial creation.\n"
            "- [2026-11-02] - a.py changed."), encoding="utf-8", newline="\n")

    def guards_for_real(self, calls):
        def run(argv, *, cwd, check=True):
            calls.append(("check", argv))
            if any(name in str(argv[1]) for name in ("doc-sync-guard", "ai-style-guard")):
                return crossreview.command(argv, cwd=cwd, check=check)
            # encoding-guard and personal-data-guard judge files as they stand and
            # take no --since, so they are outside R86-2.
            return passing(argv)
        return run

    def assert_base_round_refused_by(self, guard, base):
        args = self.args()
        args.review = self.brief(kind="code", under="code",
                                 excluded="workflows - the copied guards and round fixture")
        args.base = base
        deps, calls = self.deps()
        deps.command = self.guards_for_real(calls)
        with mock.patch.dict(os.environ):
            for name in ("BOOK_DRAGON_ROOT", "BOOK_DRAGON_CLEAN_COPY"):
                os.environ.pop(name, None)
            with self.assertRaisesRegex(crossreview.Refused, guard):
                crossreview.run_round(args, root=self.root, deps=deps)
            self.assertNotIn("capture", [name for name, _ in calls])
            # The same checks without --since see no change, and pass.
            brief = crossreview.read_brief(self.root, args.review)
            results = crossreview.mechanical(self.root, brief, None, run=deps.command)
        self.assertTrue(results and all(line.startswith("PASS:") for line in results),
                        results)

    def test_base_round_refuses_a_committed_directory_change_with_no_log_entry(self):
        base = self.real_guard_project()
        self.change_code("print('changed')\n")
        self.commit_all("a change whose LOG.md has no entry")
        self.assert_base_round_refused_by("doc-sync-guard", base)

    def test_base_round_refuses_a_committed_line_carrying_an_enforced_marker(self):
        from datetime import datetime
        base = self.real_guard_project()
        # The marker is built from its code point, so this file carries none.
        self.change_code("print('a " + chr(0x2014) + " b')\n")
        with open(self.root / "code/LOG.md", "a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"[{datetime.now().astimezone().isoformat(timespec='seconds')}]"
                         " | Actor: Biblio | Action: modified | Note: a.py changed.\n")
        self.commit_all("a change carrying an enforced marker")
        self.assert_base_round_refused_by("ai-style-guard", base)


class UsageBoundaryTests(Fixture):
    def reading(self, percent=1, source=None):
        import limits
        return limits.Reading(limits.CODEX, source or limits.REPORTED,
            percent=percent, raw={"rateLimits": {"credits": {
                "hasCredits": False, "unlimited": False}}})

    def test_run_resume_and_round_preflight_share_state_writer(self):
        import loop
        reading = self.reading()
        settings = {"usage_ceiling_percent": 95,
                    "roles": {"reviewer": "codex", "builder": "claude"}}
        round_state = {"status": "running", "paid_usage_pending": "old"}
        run_state = {"status": "paused", "run_id": "fixture",
                     "step": "build", "paid_usage_pending": "old"}
        run = SimpleNamespace(state=run_state, deps=object(), log_path=self.root,
                              note_codex=lambda value: value, save=lambda: None)
        original = loop.apply_preflight
        with mock.patch.object(loop, "apply_preflight", wraps=original) as shared, \
                mock.patch.object(loop, "_docker_at_start",
                                  return_value=(False, "this run was running")), \
                mock.patch.object(loop, "_docker_event", return_value={}), \
                mock.patch.object(loop.sr, "continue_run",
                                  side_effect=lambda state: state.update(status="running")), \
                mock.patch.object(loop, "_log", return_value=None):
            loop.usage_preflight(round_state, settings, "review",
                read_codex=lambda *a: reading, note_codex=lambda value: value,
                now=lambda: 100, root=self.root)
            loop._resume_claimed(run, rounds=None, allow_paid=False,
                user_started=True, plain=False, reading=reading,
                credits=loop.credit_snapshot(reading))
        self.assertEqual(shared.call_count, 2)
        self.assertEqual(round_state["credits"], run_state["credits"])
        self.assertNotIn("paid_usage_pending", round_state)
        self.assertNotIn("paid_usage_pending", run_state)

    def test_envelope_clears_permission_on_every_exit_for_run_and_round(self):
        import limits
        import loop
        for context in ("run", "round"):
            for allowed in (False, True):
                for exit_case in ("before_error", "before_stop", "body_limit",
                                  "body_error", "success"):
                    with self.subTest(context=context, allowed=allowed, exit=exit_case):
                        state = {"status": "running", "frozen_dir": str(self.root)}
                        if allowed:
                            state["paid_usage_pending"] = "review"
                        holder = limits.ClaudeUsage()
                        seen = []
                        def before(step, provider):
                            seen.append(("before", holder.paid_allowed))
                            if exit_case == "before_error":
                                raise OSError("reading failed")
                            if exit_case == "before_stop":
                                return {"reason": "usage"}
                        async def body():
                            seen.append(("body", holder.paid_allowed))
                            if exit_case == "body_limit":
                                raise limits.UsageLimitError(limits.CLAUDE, "limit")
                            if exit_case == "body_error":
                                raise OSError("session failed")
                        def finish(reason, evidence, *args):
                            state["status"] = reason
                            seen.append(("finish", reason))
                        result = asyncio.run(loop.usage_envelope(state,
                            {"usage_ceiling_percent": 95}, "review", limits.CLAUDE,
                            claude_usage=holder, before_step=before, body=body,
                            finish=finish, read_codex=lambda *a: self.reading(),
                            note_codex=lambda reading: reading, root=self.root,
                            save=lambda: None))
                        self.assertIs(holder.paid_allowed, False)
                        self.assertIsNone(holder.step_ceiling)
                        self.assertNotIn("paid_usage_pending", state)
                        self.assertEqual(seen[0], ("before", allowed))
                        self.assertEqual(result is None, exit_case == "success")

    def test_preflight_unknown_reset_and_round_step_name(self):
        import limits
        import loop
        state = {"status": "running", "claude_last_reading": {
            "source": limits.LIMIT_REPORTED, "reason": "using paid overage",
            "percent": 95, "resets_at": None}}
        settings = {"usage_ceiling_percent": 95,
                    "roles": {"reviewer": "claude", "builder": "codex"}}
        round_usage = crossreview.RoundUsage(state, settings, self.root)
        self.assertEqual(loop.next_step_name(round_usage), "review")
        result = loop.usage_preflight(state, settings, "review",
            read_codex=lambda *a: self.reading(), note_codex=round_usage.note_codex,
            now=lambda: 100, root=self.root, user_resume=True)
        self.assertEqual(result[1].source, limits.UNAVAILABLE)
        self.assertTrue(state["claude_unknown_reset_resume"])

    def test_paid_overage_event_interrupts_without_permission(self):
        import limits
        event = SimpleNamespace(rate_limit_info=SimpleNamespace(
            status="allowed", rate_limit_type="overage", resets_at=None,
            utilization=None, raw={"isUsingOverage": True}))
        class Client:
            interrupted = False
            async def interrupt(self):
                self.interrupted = True
        for allowed in (False, True):
            with self.subTest(allowed=allowed):
                usage = limits.ClaudeUsage()
                usage.paid_allowed = allowed
                client = Client()
                if allowed:
                    asyncio.run(providers.watch_claude_event(client, usage, event))
                    self.assertFalse(client.interrupted)
                else:
                    with self.assertRaises(limits.UsageLimitError):
                        asyncio.run(providers.watch_claude_event(client, usage, event))
                    self.assertTrue(client.interrupted)

    def test_saved_rejection_and_empty_probe_match_run_adapter(self):
        import limits
        import loop
        reading = self.reading()
        for saved, probe_reading in (
            ({"source": limits.LIMIT_REPORTED, "reason": "request rejected",
              "percent": 100, "resets_at": None},
             limits.Reading(limits.CLAUDE, limits.REPORTED, percent=1)),
            (None, limits.Reading(limits.CLAUDE, limits.UNAVAILABLE,
                                  reason="probe event without a figure"))):
            with self.subTest(saved=saved):
                settings = {"usage_ceiling_percent": 95,
                            "usage_probe_model": "fixture"}
                def state():
                    value = {"status": "running", "frozen_dir": str(self.root)}
                    if saved:
                        value["claude_last_reading"] = saved
                    return value
                probe_result = SimpleNamespace(reading=probe_reading,
                    tokens=None, usage=None, model="fixture", events=["event"])
                common = dict(claude_usage=limits.ClaudeUsage(),
                    read_codex=lambda *a: reading, note_codex=lambda value: value,
                    probe_claude=lambda *a: probe_result,
                    now=lambda: 100, root=self.root)
                round_state = state()
                direct = loop.Run.usage_before_step(round_state, settings, "review",
                    limits.CLAUDE, **common)
                run_state = state()
                adapter = SimpleNamespace(state=run_state, settings=settings,
                    claude_usage=limits.ClaudeUsage(), root=self.root,
                    deps=SimpleNamespace(read_codex=lambda *a: reading,
                        probe_claude=lambda *a: probe_result, now=lambda: 100),
                    note_codex=lambda value: value,
                    usage_before_step=loop.Run.usage_before_step)
                self.assertEqual(loop.Run.before_step(adapter, "review", limits.CLAUDE),
                                 direct[0])
                self.assertEqual(round_state["readings"], run_state["readings"])

    def test_state_keys_and_run_adapter_match_round_for_both_reviewers(self):
        import limits
        import loop
        allowed_keys = {"credits", "paid_usage_pending", "paid_usage",
            "paid_usage_notes", "claude_unknown_reset_resume", "claude_last_reading",
            "readings", "usage_source", "codex_last_five_hour", "frozen_dir", "status"}
        class Tracked(dict):
            touched = None
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self.touched = set()
            def __getitem__(self, key):
                self.touched.add(key)
                return super().__getitem__(key)
            def get(self, key, default=None):
                self.touched.add(key)
                return super().get(key, default)
            def setdefault(self, key, default=None):
                self.touched.add(key)
                return super().setdefault(key, default)
            def __setitem__(self, key, value):
                self.touched.add(key)
                return super().__setitem__(key, value)
            def pop(self, key, *default):
                self.touched.add(key)
                return super().pop(key, *default)
            def __contains__(self, key):
                self.touched.add(key)
                return super().__contains__(key)
        self._tracked_pieces_1_and_2(Tracked, allowed_keys)
        for reviewer in (limits.CLAUDE, limits.CODEX):
            for allowed in (False, True):
                with self.subTest(reviewer=reviewer, allowed=allowed):
                    state = Tracked(status="running", frozen_dir=str(self.root))
                    if allowed:
                        state["paid_usage_pending"] = "review"
                    settings = {"usage_ceiling_percent": 95,
                                "usage_probe_model": "fixture",
                                "roles": {"reviewer": reviewer,
                                          "builder": limits.CODEX if reviewer == limits.CLAUDE else limits.CLAUDE}}
                    reading = self.reading()
                    probe_result = SimpleNamespace(reading=limits.Reading(
                        limits.CLAUDE, limits.REPORTED, percent=1),
                        tokens=1, usage={}, model="fixture", events=[])
                    common = dict(claude_usage=limits.ClaudeUsage(),
                        read_codex=lambda *a: reading, note_codex=lambda value: value,
                        probe_claude=lambda *a: probe_result,
                        now=lambda: 100, root=self.root)
                    direct = loop.Run.usage_before_step(state, settings, "review",
                                                        reviewer, **common)
                    other = Tracked(status="running", frozen_dir=str(self.root))
                    if allowed:
                        other["paid_usage_pending"] = "review"
                    adapter = SimpleNamespace(state=other, settings=settings,
                        claude_usage=limits.ClaudeUsage(), root=self.root,
                        deps=SimpleNamespace(read_codex=lambda *a: reading,
                            probe_claude=lambda *a: probe_result, now=lambda: 100),
                        note_codex=lambda value: value,
                        usage_before_step=loop.Run.usage_before_step)
                    self.assertEqual(loop.Run.before_step(adapter, "review", reviewer),
                                     direct[0])
                    self.assertEqual(other["readings"], state["readings"])
                    self.assertTrue(state.touched <= allowed_keys, state.touched - allowed_keys)
                    self.assertTrue(other.touched <= allowed_keys, other.touched - allowed_keys)

    def _tracked_pieces_1_and_2(self, Tracked, allowed_keys):
        """Code review R8-3: the preflight (piece 1, with apply_preflight and the
        round's grant_paid) and the envelope (piece 2, through a permitted step that
        completes, so its after-step reading runs) touch only the listed keys, for
        each reviewer, with and without paid permission (R86-1)."""
        import limits
        import loop
        for reviewer in (limits.CLAUDE, limits.CODEX):
            for allowed in (False, True):
                with self.subTest(piece="1 and 2", reviewer=reviewer, allowed=allowed):
                    state = Tracked(status="running", frozen_dir=str(self.root))
                    settings = {"usage_ceiling_percent": 95,
                                "usage_probe_model": "fixture",
                                "roles": {"reviewer": reviewer,
                                          "builder": limits.CODEX if reviewer == limits.CLAUDE else limits.CLAUDE}}
                    usage = crossreview.RoundUsage(state, settings, self.root)
                    readings = iter([self.reading(), self.reading()])
                    read_codex = lambda *a: next(readings)
                    loop.usage_preflight(state, settings, "review", read_codex=read_codex,
                        note_codex=usage.note_codex, now=lambda: 100, root=self.root,
                        allow_paid=allowed, grant_paid=usage.grant_paid)
                    self.assertEqual(state.get("paid_usage_pending") == "review", allowed)
                    ran = []
                    async def body():
                        ran.append(True)
                    finished = []
                    result = asyncio.run(loop.usage_envelope(state, settings, "review",
                        reviewer, claude_usage=usage.claude_usage,
                        before_step=lambda step, provider: None, body=body,
                        finish=lambda *a: finished.append(a), read_codex=read_codex,
                        note_codex=usage.note_codex, root=self.root,
                        save=lambda: None))
                    self.assertIsNone(result)
                    self.assertEqual((ran, finished), ([True], []))
                    self.assertNotIn("paid_usage_pending", dict(state))
                    # A permitted step takes the after-step reading, using the second.
                    self.assertEqual(next(readings, None) is None, allowed)
                    self.assertTrue(state.touched <= allowed_keys,
                                    state.touched - allowed_keys)


class ProbeTests(Fixture):
    def test_size_stream_tail_and_record(self):
        record = self.root / "probe-calls.jsonl"
        record.write_text("", encoding="utf-8", newline="\n")
        config = {"copy": str(self.root), "image": "tag", "record": str(record)}
        def fake(copy, argv, image, *, timeout, merged_stream):
            self.assertEqual(argv, ["python", "-B", "-c", "print(1)"])
            self.assertTrue(merged_stream)
            return 0, "x" * 7000, ""
        reply = probe.run_probe("print(1)", config, run=fake, clock=lambda: 1)
        self.assertTrue(reply.startswith("exit 0\n"))
        self.assertEqual(len(reply.split("\n", 1)[1]), 6000)
        self.assertNotIn("x" * 100, record.read_text(encoding="utf-8"))
        self.assertIn("print(1)", record.read_text(encoding="utf-8"))
        self.assertIn("error:", probe.run_probe("x" * 20001, config, run=fake))

    def test_docker_error(self):
        record = self.root / "probe-calls.jsonl"
        record.write_text("", encoding="utf-8", newline="\n")
        config = {"copy": str(self.root), "image": "tag", "record": str(record)}
        reply = probe.run_probe("pass", config, run=lambda *a, **kw: (
            (_ for _ in ()).throw(probe.container.ContainerError("Docker failed"))))
        self.assertIn("error: Docker failed", reply)
        self.assertIn('"exit_code": null', record.read_text(encoding="utf-8"))

    def test_timeout_is_an_error_and_is_recorded(self):
        record = self.root / "probe-calls.jsonl"
        record.write_text("", encoding="utf-8", newline="\n")
        config = {"copy": str(self.root), "image": "tag", "record": str(record)}
        def timed_out(*args, **kwargs):
            self.assertEqual(kwargs["timeout"], 120)
            raise probe.container.ContainerError("time limit passed")
        self.assertEqual(probe.run_probe("pass", config, run=timed_out),
                         "error: time limit passed")
        self.assertEqual(json.loads(record.read_text(encoding="utf-8"))["exit_code"], None)


class ReviewerOptionTests(Fixture):
    def test_claude_init_model_substitution_refuses_session(self):
        class SystemMessage:
            subtype = "init"
            data = {"model": "substituted"}
        class Fake:
            disconnected = False
            async def connect(self):
                pass
            async def get_mcp_status(self):
                return {"mcpServers": [{"name": "probes", "status": "connected",
                    "tools": [{"name": "run_probe"}]}]}
            async def query(self, prompt):
                pass
            async def receive_response(self):
                yield SystemMessage()
            async def disconnect(self):
                self.disconnected = True
        client = Fake()
        reviewer = providers.ClaudeReviewer("requested", "high", cwd=self.root,
            client_factory=lambda **kw: client, probe_settings=self.root / "probe.json")
        with self.assertRaisesRegex(providers.ProviderError, "substituted"):
            asyncio.run(reviewer._review("prompt", {"type": "object"}))
        self.assertTrue(client.disconnected)

    def test_claude_probe_option_is_default_off_and_allows_only_probe(self):
        import asyncio
        ordinary = providers.ClaudeReviewer("model", "high", cwd=self.root)
        hooked = providers.ClaudeReviewer("model", "high", cwd=self.root,
                                          probe_settings=self.root / "probe.json")
        self.assertEqual(ordinary.options({"type": "object"}).mcp_servers, {})
        options = hooked.options({"type": "object"})
        self.assertEqual(list(options.mcp_servers), ["probes"])
        self.assertIn(providers.PROBE_TOOL, options.allowed_tools)
        self.assertNotIn(providers.PROBE_TOOL,
                         ordinary.options({"type": "object"}).allowed_tools)
        allowed = asyncio.run(hooked.pre_tool_use({"tool_name": providers.PROBE_TOOL,
                                                  "tool_input": {"code": "pass"}}, "1", None))
        denied = asyncio.run(hooked.pre_tool_use({"tool_name": "Bash",
                                                 "tool_input": {}}, "2", None))
        self.assertEqual(allowed, {})
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_claude_probe_status_refuses_before_query(self):
        import asyncio
        status = {"mcpServers": []}
        queried = []
        class Fake:
            async def connect(self):
                pass
            async def get_mcp_status(self):
                return status
            async def query(self, prompt):
                queried.append(prompt)
            async def receive_response(self):
                if False:
                    yield None
            async def disconnect(self):
                pass
        reviewer = providers.ClaudeReviewer("model", "high", cwd=self.root,
            client_factory=lambda **kw: Fake(), probe_settings=self.root / "probe.json")
        for entries in ([], [{"name": "probes", "status": "failed",
                             "tools": [{"name": "run_probe"}]}],
                        [{"name": "probes", "status": "pending",
                          "tools": []}],
                        [{"name": "probes", "status": "connected",
                          "tools": [{"name": "other"}]}]):
            status["mcpServers"] = entries
            async def no_wait(seconds):
                pass
            with self.subTest(entries=entries), mock.patch.object(
                    providers.asyncio, "sleep", side_effect=no_wait), \
                    self.assertRaises(providers.ProviderError):
                asyncio.run(reviewer._review("review", {"type": "object"}))
            self.assertEqual(queried, [])
        status.clear()
        status["mcp_servers"] = [{"name": "probes", "status": "connected",
                                  "tools": [{"name": "run_probe"}]}]
        with self.assertRaisesRegex(providers.ProviderError, "mcpServers"):
            asyncio.run(reviewer._review("review", {"type": "object"}))
        self.assertEqual(queried, [])

    def test_claude_pending_probe_is_rechecked_before_query(self):
        statuses = ["pending", "connected"]
        queried = []
        class Fake:
            async def connect(self):
                pass
            async def get_mcp_status(self):
                state = statuses.pop(0)
                return {"mcpServers": [{"name": "probes", "status": state,
                    "tools": [{"name": "run_probe"}] if state == "connected" else []}]}
            async def query(self, prompt):
                queried.append(prompt)
            async def receive_response(self):
                if False:
                    yield None
            async def disconnect(self):
                pass
        async def no_wait(seconds):
            pass
        reviewer = providers.ClaudeReviewer("model", "high", cwd=self.root,
            client_factory=lambda **kw: Fake(), probe_settings=self.root / "probe.json")
        with mock.patch.object(providers.asyncio, "sleep", side_effect=no_wait), \
                self.assertRaisesRegex(providers.ProviderError, "without a result"):
            asyncio.run(reviewer._review("review", {"type": "object"}))
        self.assertEqual(len(queried), 1)
        self.assertEqual(statuses, [])

    def test_container_merged_stream_option_is_off_by_default(self):
        seen = []
        def fake(args, **kwargs):
            seen.append(kwargs)
            return SimpleNamespace(returncode=0, stdout="out", stderr="err")
        with mock.patch.object(container, "docker", side_effect=fake):
            self.assertEqual(container.run_in_container(self.root, ["true"], "tag"),
                             (0, "out", "err"))
            self.assertEqual(container.run_in_container(self.root, ["true"], "tag",
                                                        merged_stream=True),
                             (0, "out", ""))
        self.assertNotIn("merged_stream", seen[0])
        self.assertTrue(seen[1]["merged_stream"])

    def test_codex_probe_config_readback_accepts_only_exact_server(self):
        frozen = self.root / "frozen"
        frozen.mkdir()
        probe_file = Path(r"C:\Temp\probe.json")
        reviewer = providers.CodexReviewer("model", "high", cwd=self.root,
            frozen_dir=frozen, python=r"C:\Python\python.exe", probe_settings=probe_file)
        row = {"name": "probes", "tools": {"run_probe": {}}, "toolsError": None}
        state = {"entry": None, "row": row}
        opened = []
        class Fake:
            def __init__(self, overrides):
                self.overrides = overrides
                self._client = self
                self.closed = False
                opened.append(self)
            def _request_raw(self, name, params):
                if name == "hooks/list":
                    return {"data": []}
                if name == "mcpServerStatus/list":
                    return {"data": [state["row"]]}
                if name == "config/read":
                    override = next(part for part in self.overrides
                                    if part.startswith("mcp_servers.probes="))
                    match = re.search(r'command="([^"]+)", args=\["([^"]+)", "([^"]+)"\]',
                                      override)
                    if match is None:
                        raise AssertionError(override)
                    launched = {"command": match.group(1),
                                "args": [match.group(2), match.group(3)],
                                "default_tools_approval_mode": "approve"}
                    return {"config": {"mcp_servers": {"probes": state["entry"] or launched},
                        # Codex echoes the forward-slash path it was sent.
                        "shell_environment_policy": {"set": {"PYTHONPATH":
                            (frozen / "tripwire").as_posix()}}}}
                raise AssertionError(name)
            def close(self):
                self.closed = True
        reviewer._codex_factory = Fake
        with mock.patch.object(reviewer, "_self_test", return_value={"exitCode": 0}):
            client = reviewer._launch()
            self.assertIn('default_tools_approval_mode="approve"',
                          " ".join(opened[-1].overrides))
            override = next(part for part in opened[-1].overrides
                            if part.startswith("mcp_servers.probes="))
            match = re.search(r'command="([^"]+)", args=\["([^"]+)", "([^"]+)"\]',
                              override)
            expected = {"command": match.group(1),
                        "args": [match.group(2), match.group(3)],
                        "default_tools_approval_mode": "approve"}
            self.assertEqual(expected["command"], "C:/Python/python.exe")
            self.assertEqual(expected["args"][1], "C:/Temp/probe.json")
            client.close()
            for changed in ({**expected, "command": "other"},
                            {**expected, "args": ["other"]},
                            {"default_tools_approval_mode": "approve"},
                            {**expected, "default_tools_approval_mode": "never"}):
                state["entry"] = changed
                with self.subTest(changed=changed), self.assertRaisesRegex(
                        providers.ProviderError, "differ from launch"):
                    reviewer._launch()
                self.assertTrue(opened[-1].closed)
            state["entry"] = expected
            state["row"] = {**row, "tools": {"other": {}}}
            with self.assertRaisesRegex(providers.ProviderError, "exactly run_probe"):
                reviewer._launch()

    def test_model_list_uses_empty_folder_and_disables_counting_servers(self):
        import openai_codex
        opened = []
        class Fake:
            def __init__(self, config):
                self.config = config
                self._client = self
                opened.append(self)
            def _request_raw(self, name, params):
                self.assert_name = name
                return {"data": []} if len(opened) == 2 else {
                    "data": [{"name": "other", "tools": {"call": {}}}]}
            def models(self):
                return []
            def close(self):
                pass
        with mock.patch.object(openai_codex, "Codex", Fake), mock.patch.object(
                openai_codex, "CodexConfig", side_effect=lambda **kw: kw), mock.patch.object(
                providers, "_model_ids", return_value=["present"]):
            self.assertTrue(crossreview.model_offered(self.root, "present"))
        self.assertEqual(len(opened), 2)
        self.assertNotEqual(opened[0].config["cwd"], str(self.root))
        self.assertFalse(Path(opened[0].config["cwd"]).exists())
        self.assertIn("mcp_servers.other.enabled=false",
                      opened[1].config["config_overrides"])


class ProbeSettingsTests(Fixture):
    def test_missing_unreadable_and_valid_settings(self):
        path = self.root / "probe.json"
        with self.assertRaises(OSError):
            probe.load_settings(path)
        path.write_text("{", encoding="utf-8", newline="\n")
        with self.assertRaises(json.JSONDecodeError):
            probe.load_settings(path)
        record = self.root / "calls.jsonl"
        record.write_text("", encoding="utf-8", newline="\n")
        path.write_text(json.dumps({"copy": str(self.root), "image": "tag",
                                    "record": str(record)}), encoding="utf-8", newline="\n")
        settings = probe.load_settings(path)
        self.assertEqual(settings["image"], "tag")
        path.unlink()
        reply = probe.run_probe("pass", settings, run=lambda *a, **kw: (0, "ok", ""))
        self.assertEqual(reply, "exit 0\nok")
        self.assertEqual(len(record.read_text(encoding="utf-8").splitlines()), 1)


class EntryTests(unittest.TestCase):
    def test_direct_start_prints_starter_and_does_nothing(self):
        result = subprocess.run([sys.executable, str(SCRIPTS / "crossreview.py")],
                                capture_output=True, text=True, encoding="utf-8", env={k: v for k, v in
                                os.environ.items() if k != crossreview.MARKER})
        self.assertEqual(result.returncode, 3)
        self.assertIn("start.py --review", result.stdout)

    def test_marker_enters_isolated_stub_before_step_one(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "a" / "b" / "c"
            root.mkdir(parents=True)
            (root / "crossreview.py").write_bytes((SCRIPTS / "crossreview.py").read_bytes())
            stub = ("import importlib.util, os\n"
                    "spec=importlib.util.spec_from_file_location('copy', 'crossreview.py')\n"
                    "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)\n"
                    "module.run_round=lambda args: (print('entered stub before step 1') or 0)\n"
                    "raise SystemExit(module.main(['--review','brief.md','--reviewer','codex',"
                    "'--git-dir','personal.git']))\n")
            (root / "stub.py").write_text(stub, encoding="utf-8", newline="\n")
            result = subprocess.run([sys.executable, "stub.py"], cwd=root,
                env={**os.environ, crossreview.MARKER: "1"}, capture_output=True,
                text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("entered stub before step 1", result.stdout)

    def test_gate_refuses_every_round_option(self):
        for extra in ("--reviewer codex", "--round 2", "--base abc",
                      "--allow-paid", "--reset-confirmed", "--dry-run"):
            with self.subTest(extra=extra), mock.patch.object(crossreview,
                    "run_round") as called, mock.patch.dict(os.environ,
                    {crossreview.MARKER: "1"}):
                code = crossreview.main(["--review", "brief.md", "--build-ready",
                                         "--git-dir", "personal.git", *extra.split()])
                self.assertEqual(code, 3)
                called.assert_not_called()

    def test_missing_command_argument_is_a_pre_capture_refusal(self):
        with mock.patch.dict(os.environ, {crossreview.MARKER: "1"}), mock.patch.object(
                crossreview, "run_round") as called:
            self.assertEqual(crossreview.main(["--review", "brief.md",
                                                "--reviewer", "codex"]), 3)
            called.assert_not_called()


if __name__ == "__main__":
    unittest.main()
