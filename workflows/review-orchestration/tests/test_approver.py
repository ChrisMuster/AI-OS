#!/usr/bin/env python3
"""Hermetic tests for the builder's approver (approver.py).

Same three controls as test_brief.py:

    positive control  a call the plan allows (a read, an edit inside the edit paths,
                      a listed verification command). It must be allowed.
    rejection control a call the plan refuses. It must be refused, the reason named,
                      and the refusal recorded for the run report.
    negative control  something that looks like a refused shape but is not one (a
                      `.venv` path that is not `.env`, `...` that is not `..`). It
                      must not be refused for it.

The project root is a temporary directory, so path resolution is real but nothing
outside it is touched. The verification commands are the shipped file's.

    python workflows/review-orchestration/tests/test_approver.py
"""

import asyncio
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent
SCRIPTS = WORKFLOW / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


brief = _load("brief")
approver = _load("approver")
# The read rules have one implementation and three users; the reviewer's hook is the
# one outside this module, so it is loaded to be driven here too.
limits = _load("limits")
settings = _load("settings")
codex_rules = _load("codex_rules")
providers = _load("providers")

EDIT_PATHS = ["workflows/doc-sync-guard/", "notes/plan.md"]


class ApproverTestCase(unittest.TestCase):

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        (self.root / "workflows" / "doc-sync-guard" / "scripts").mkdir(parents=True)
        (self.root / "notes").mkdir()
        self.approver = approver.Approver(EDIT_PATHS, approver.load_verify_commands(),
                                          root=self.root)

    def allowed(self, tool, tool_input):
        decision = self.approver.decide(tool, tool_input)
        self.assertTrue(decision.allowed, f"{tool} {tool_input} was refused: "
                        f"{decision.reason}")
        return decision

    def refused(self, tool, tool_input, reason):
        before = len(self.approver.refusals)
        decision = self.approver.decide(tool, tool_input)
        self.assertFalse(decision.allowed, f"{tool} {tool_input} was allowed")
        self.assertIn(reason, decision.reason)
        self.assertTrue(decision.reason.startswith("Refused by the orchestrator"))
        self.assertEqual(len(self.approver.refusals), before + 1,
                         "every refusal must be recorded for the run report")
        self.assertEqual(self.approver.refusals[-1]["tool"], tool)
        return decision


class ReadToolTests(ApproverTestCase):

    def test_positive_read_tools_are_allowed(self):
        # Allowed subject to the read rules (ReadRuleTests): these calls break neither.
        for tool in ("Read", "Grep", "Glob", "TodoWrite", "ToolSearch"):
            with self.subTest(tool=tool):
                self.allowed(tool, {"file_path": "AGENTS.md"})
        self.assertEqual(self.approver.refusals, [])

    def test_rejection_every_other_tool_is_refused_by_default(self):
        for tool in ("PowerShell", "WebFetch", "WebSearch", "Agent", "Task", "Skill",
                     "mcp__other__tool", "", None):
            with self.subTest(tool=tool):
                self.refused(tool, {}, "is not permitted in this run")


class ReadRuleTests(ApproverTestCase):
    """The read rules (short chunk (d) plan, 5.1 rules 1 and 2), through both of their
    callers: ``Approver.decide``, which binds a builder, and the Claude reviewer's
    hook."""

    def setUp(self):
        super().setUp()
        self.reviewer = providers.ClaudeReviewer("claude-opus-5-5", "high", cwd=self.root)

    def hook(self, tool, tool_input):
        return asyncio.run(self.reviewer.pre_tool_use(
            {"tool_name": tool, "tool_input": tool_input}, "use-1", None))

    def both_refuse(self, tool, tool_input, reason):
        self.refused(tool, tool_input, reason)
        before = len(self.reviewer.refusals)
        specific = self.hook(tool, tool_input)["hookSpecificOutput"]
        self.assertEqual(specific["permissionDecision"], "deny")
        self.assertIn(reason, specific["permissionDecisionReason"])
        self.assertEqual(len(self.reviewer.refusals), before + 1)
        self.assertEqual(self.reviewer.refusals[-1]["tool"], tool)

    def both_allow(self, tool, tool_input):
        self.allowed(tool, tool_input)
        self.assertEqual(self.hook(tool, tool_input), {}, f"{tool} {tool_input}")

    def test_rejection_a_read_or_search_that_names_an_env_file(self):
        for tool, tool_input in (
                ("Read", {"file_path": ".env"}),
                ("Read", {"file_path": "workflows/x/.ENV"}),
                ("Read", {"file_path": "sub/.env.local"}),
                ("Read", {"file_path": str(self.root / ".env")}),
                ("Grep", {"pattern": "KEY", "path": ".env"}),
                ("Grep", {"pattern": "KEY", "path": "sub/.Env.Local"}),
                ("Glob", {"pattern": ".env"}),
                ("Glob", {"pattern": "**/.env"}),
                ("Glob", {"pattern": "**/.ENV.local"}),
                ("Glob", {"pattern": "*.py", "path": "sub/.env"})):
            with self.subTest(tool=tool, tool_input=tool_input):
                self.both_refuse(tool, tool_input, "may not name a `.env` file")

    def test_positive_searching_for_the_word_is_not_naming_the_file(self):
        # A Grep's pattern is the text searched for. A reviewer must be able to search
        # the code for the word, and a folder search cannot return the file's contents.
        self.both_allow("Grep", {"pattern": ".env", "path": "workflows"})
        self.both_allow("Grep", {"pattern": "\\.env\\.local"})

    def test_rejection_a_search_with_a_file_wildcard_whatever_its_value(self):
        # An explicit wildcard overrides the ignore rules that keep a .env file out of
        # a folder search, a bare * included, so the field itself is refused.
        for glob in (".env*", "**/.env*", "*", "*.py"):
            with self.subTest(glob=glob):
                self.both_refuse("Grep", {"pattern": "KEY", "glob": glob},
                                 "may not carry a file wildcard")
        decision = self.approver.decide("Grep", {"pattern": "KEY", "glob": "*.py"})
        self.assertIn("Search by folder, file or file type", decision.reason)

    def test_positive_a_search_by_folder_or_type_and_a_listing(self):
        self.both_allow("Grep", {"pattern": "KEY", "path": "workflows"})
        self.both_allow("Grep", {"pattern": "KEY", "type": "py"})
        self.both_allow("Grep", {"pattern": "KEY", "path": "notes/plan.md", "-i": True})
        self.both_allow("Glob", {"pattern": "**/*.py"})
        self.both_allow("Read", {"file_path": "AGENTS.md"})

    def test_negative_names_that_only_look_like_an_env_file(self):
        self.both_allow("Read", {"file_path": "workflows/x/.venv/site.py"})
        self.both_allow("Read", {"file_path": "docs/.environment"})
        self.both_allow("Glob", {"pattern": "**/*.env.md"})

    def test_positive_the_shell_checks_are_one_function(self):
        # The Codex builder's read commands pass through the same three checks.
        self.assertIsNone(approver.shell_checks("rg -n \"x\" workflows"))
        self.assertIn("one line", approver.shell_checks("a\nb"))
        self.assertIn("`..` path component", approver.shell_checks("rg \"x\" ../y"))
        self.assertIn("`.env` file", approver.shell_checks("Get-Content -LiteralPath .env"))


class EditTests(ApproverTestCase):

    def test_positive_inside_a_folder_edit_path(self):
        for tool, key in (("Edit", "file_path"), ("Write", "file_path"),
                          ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path")):
            with self.subTest(tool=tool):
                self.allowed(tool, {key: "workflows/doc-sync-guard/scripts/run.py"})

    def test_positive_absolute_path_inside_the_project(self):
        self.allowed("Edit", {"file_path": str(self.root / "workflows/doc-sync-guard/x.py")})

    def test_positive_a_file_edit_path_itself(self):
        self.allowed("Write", {"file_path": "notes/plan.md"})

    @unittest.skipUnless(sys.platform == "win32",
                         "a backslash is a folder separator only on Windows")
    def test_positive_windows_spelling_is_folded(self):
        self.allowed("Edit", {"file_path": "Workflows\\Doc-Sync-Guard\\CONTEXT.md"})
        self.allowed("Edit", {"file_path": "notes/PLAN.md"})

    def test_positive_case_is_folded_on_any_system(self):
        # The any-system half of the Windows test above: case is folded everywhere.
        self.allowed("Edit", {"file_path": "Workflows/Doc-Sync-Guard/CONTEXT.md"})
        self.allowed("Edit", {"file_path": "notes/PLAN.md"})
        self.refused("Edit", {"file_path": "Workflows/Audit/scripts/run.py"},
                     "outside this run's edit paths")

    def test_rejection_outside_the_edit_paths(self):
        self.refused("Edit", {"file_path": "workflows/audit/scripts/run.py"},
                     "outside this run's edit paths")
        self.refused("Write", {"file_path": "notes/other.md"},
                     "outside this run's edit paths")

    def test_negative_a_sibling_sharing_the_prefix_is_not_inside(self):
        # workflows/doc-sync-guard-old/ starts with the edit path's text but is not in it.
        self.refused("Edit", {"file_path": "workflows/doc-sync-guard-old/run.py"},
                     "outside this run's edit paths")
        self.refused("Edit", {"file_path": "notes/plan.md.bak"},
                     "outside this run's edit paths")

    def test_rejection_escaping_the_project(self):
        self.refused("Edit", {"file_path": "workflows/doc-sync-guard/../../../x.py"},
                     "is outside the project")
        self.refused("Edit", {"file_path": str(self.root.parent / "x.py")},
                     "is outside the project")

    def test_rejection_dotdot_that_lands_elsewhere_in_the_project(self):
        self.refused("Edit", {"file_path": "workflows/doc-sync-guard/../audit/run.py"},
                     "outside this run's edit paths")

    def test_positive_dotdot_that_lands_back_inside(self):
        self.allowed("Edit", {"file_path": "workflows/audit/../doc-sync-guard/run.py"})

    def test_rejection_env_and_git_inside_an_edit_path(self):
        self.refused("Write", {"file_path": "workflows/doc-sync-guard/.env"},
                     "is a `.env` file")
        self.refused("Write", {"file_path": "workflows/doc-sync-guard/.ENV.local"},
                     "is a `.env` file")
        self.refused("Write", {"file_path": "workflows/doc-sync-guard/.git/config"},
                     "is under `.git/`")

    def test_negative_a_venv_folder_is_not_an_env_file(self):
        self.allowed("Write", {"file_path": "workflows/doc-sync-guard/.venv/x.py"})

    def test_rejection_alternate_data_stream(self):
        self.refused("Write", {"file_path": "workflows/doc-sync-guard/run.py:hidden"},
                     "alternate data stream")

    def test_rejection_no_path_or_the_root(self):
        self.refused("Edit", {}, "no file path given")
        self.refused("Edit", {"file_path": "   "}, "no file path given")
        self.refused("Edit", {"file_path": "."}, "is the project root")

    @unittest.skipUnless(hasattr(os, "symlink"), "no symlink support")
    def test_rejection_a_link_inside_the_edit_path_pointing_out(self):
        outside = self.root / "workflows" / "audit"
        outside.mkdir()
        link = self.root / "workflows" / "doc-sync-guard" / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("creating a symlink needs a privilege this machine lacks")
        self.refused("Edit", {"file_path": "workflows/doc-sync-guard/link/run.py"},
                     "outside this run's edit paths")

    def test_rejection_a_bad_edit_path_refuses_to_build_the_approver(self):
        for path in ("../x", "C:/x", "a/.env", "a\\b", ".git/"):
            with self.subTest(path=path):
                with self.assertRaises(approver.ApproverError):
                    approver.Approver([path], [], root=self.root)

    def test_positive_no_edit_paths_means_nothing_is_editable(self):
        empty = approver.Approver([], approver.load_verify_commands(), root=self.root)
        self.assertFalse(empty.decide("Edit", {"file_path": "notes/plan.md"}).allowed)
        self.assertIn("(nothing)", empty.refusals[-1]["reason"])


class ShellTests(ApproverTestCase):

    def test_positive_every_listed_command_shape(self):
        for command in (
                "python -m py_compile workflows/doc-sync-guard/scripts/run.py",
                "python workflows/doc-sync-guard/tests/test_run.py",
                "python workflows/doc-sync-guard/tests/test_run.py -v",
                "python workflows/audit/scripts/run.py --context workflows/doc-sync-guard",
                "python workflows/close-out/scripts/run.py",
                "python workflows/close-out/scripts/run.py --scope all --json",
                "python workflows/doc-sync-guard/scripts/run.py --check",
                "python workflows/encoding-guard/scripts/run.py --check",
                "python workflows/doc-sync-guard/scripts/run.py",
                "  python workflows/close-out/scripts/run.py  "):
            with self.subTest(command=command):
                self.allowed("Bash", {"command": command})

    def test_rejection_anything_not_listed(self):
        for command in ("ls", "git status", "python -c 'print(1)'",
                        "python workflows/close-out/scripts/run.py --scope all --fix",
                        "rm -rf workflows"):
            with self.subTest(command=command):
                self.refused("Bash", {"command": command}, "only these verification commands")

    def test_positive_the_tightened_lines_still_take_their_arguments(self):
        for command in (
                "python workflows/audit/scripts/run.py --context workflows/doc-sync-guard "
                "workflows/doc-sync-guard/scripts",
                "python workflows/audit/scripts/run.py --context workflows/x --no-graph",
                "python -m py_compile workflows/x/a.py workflows/x/b.py",
                "python workflows/close-out/scripts/run.py --scope doc-sync-guard",
                "python workflows/x/tests/test_run.py RunTests",
                "python workflows/x/tests/test_run.py RunTests.test_one OtherTests -v"):
            with self.subTest(command=command):
                self.allowed("Bash", {"command": command})

    def test_positive_every_line_obeys_the_no_unnamed_flag_rule(self):
        # The file's header claims it for every line (R3-1). This checks it for these
        # flag shapes, bare and with a value, inserted at every position after the
        # program in a command each line otherwise accepts (R4-1). It checks those
        # shapes; it does not prove the rule for every possible input.
        base = {
            0: "python -m py_compile workflows/x/a.py",
            1: "python workflows/x/tests/test_run.py RunTests",
            2: "python workflows/audit/scripts/run.py --context workflows/x",
            3: "python workflows/close-out/scripts/run.py",
            4: "python workflows/x/scripts/run.py --check",
            5: "python workflows/encoding-guard/scripts/run.py --check",
            6: "python workflows/doc-sync-guard/scripts/run.py --check",
        }
        patterns = self.approver.verify_patterns
        self.assertEqual(len(patterns), len(base), "a new line needs its own case here")
        for index, pattern in enumerate(patterns):
            with self.subTest(line=pattern.pattern):
                self.assertTrue(pattern.fullmatch(base[index]))
                words = base[index].split(" ")
                for flag in ("--help", "--save", "-q", "--help x", "--save out.md",
                             "-k name", "--scope=all"):
                    for position in range(2, len(words) + 1):
                        command = " ".join(words[:position] + flag.split(" ")
                                           + words[position:])
                        self.assertIsNone(pattern.fullmatch(command), command)

    def test_rejection_a_writing_flag_on_a_listed_command(self):
        # R2-2: the audit's --save writes workflows/audit/last-report.md, and
        # close-out's --repair runs setup; neither is inside an edit path.
        for command in (
                "python workflows/audit/scripts/run.py --context workflows/doc-sync-guard --save",
                "python workflows/audit/scripts/run.py --context --save",
                "python workflows/audit/scripts/run.py --context workflows/x --save --no-graph",
                "python workflows/close-out/scripts/run.py --repair",
                "python workflows/close-out/scripts/run.py --scope --repair",
                "python -m py_compile -q workflows/x/a.py",
                "python workflows/doc-sync-guard/tests/run_tests.py --help",
                "python workflows/x/tests/test_run.py -k missing",
                "python workflows/x/tests/test_run.py --save",
                "python workflows/x/tests/test_run.py -v RunTests"):
            with self.subTest(command=command):
                self.refused("Bash", {"command": command}, "only these verification commands")

    def test_rejection_chaining_is_not_a_whole_match(self):
        for command in ("python workflows/close-out/scripts/run.py; rm -rf x",
                        "python workflows/close-out/scripts/run.py && git push",
                        "python workflows/close-out/scripts/run.py | tee out"):
            with self.subTest(command=command):
                self.refused("Bash", {"command": command}, "only these verification commands")

    def test_rejection_more_than_one_line(self):
        self.refused("Bash", {"command": "python workflows/close-out/scripts/run.py\nls"},
                     "one command per call")

    def test_rejection_a_dotdot_component_even_when_a_pattern_matches(self):
        command = "python workflows/doc-sync-guard/tests/../../../x/tests/evil.py"
        self.assertTrue(any(p.fullmatch(command) for p in self.approver.verify_patterns),
                        "the control needs a command a listed pattern would allow")
        self.refused("Bash", {"command": command}, "`..` path component")

    def test_negative_three_dots_is_not_a_parent_component(self):
        self.allowed("Bash", {"command": "python -m py_compile workflows/doc-sync-guard/a...py"})

    def test_rejection_naming_an_env_file(self):
        self.refused("Bash", {"command": "python -m py_compile .env"}, "`.env` file")
        self.refused("Bash", {"command": "python -m py_compile workflows/.env.local"},
                     "`.env` file")

    def test_negative_venv_is_not_env(self):
        self.allowed("Bash", {"command": "python -m py_compile .venv/x.py"})

    def test_rejection_leaving_the_sandbox(self):
        self.refused("Bash", {"command": "python workflows/close-out/scripts/run.py",
                              "dangerouslyDisableSandbox": True}, "may not leave the sandbox")

    def test_rejection_no_command(self):
        self.refused("Bash", {}, "no command given")
        self.refused("Bash", {"command": 5}, "no command given")


class BiblioToolTests(ApproverTestCase):

    P = "mcp__biblio-tools__"

    def test_positive_read_only_forms(self):
        self.allowed(self.P + "get_timestamp", {})
        self.allowed(self.P + "run_audit", {})
        self.allowed(self.P + "run_audit", {"save": False, "with_graph": True})
        self.allowed(self.P + "run_link_check", {})
        self.allowed(self.P + "run_link_check", {"mode": "audit"})

    def test_rejection_writing_forms(self):
        self.refused(self.P + "run_audit", {"save": True}, "may not save its report")
        self.refused(self.P + "run_link_check", {"mode": "link"}, "may only audit")
        self.refused(self.P + "run_link_check", {"mode": "fix"}, "may only audit")
        self.refused(self.P + "run_link_check", {"save": True}, "may only audit")

    def test_positive_append_log_inside_the_edit_paths(self):
        self.allowed(self.P + "append_log", {"directory": "workflows/doc-sync-guard",
                                             "actor": "Biblio", "action": "modified",
                                             "note": "x"})
        self.allowed(self.P + "append_log", {"directory": "workflows/doc-sync-guard/scripts/"})

    def test_rejection_append_log_elsewhere(self):
        for directory in (".", "workflows/audit", "memory", "", None):
            with self.subTest(directory=directory):
                self.refused(self.P + "append_log", {"directory": directory}, "append_log")

    def test_rejection_other_biblio_tools(self):
        for name in ("run_new_month", "build_knowledge_graph", "run_session_search_index",
                     "verify_setup"):
            with self.subTest(name=name):
                self.refused(self.P + name, {}, "is not permitted in this run")


class VerifyCommandsFileTests(unittest.TestCase):

    def write(self, text):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "verify-commands.txt"
        path.write_bytes(text.encode("utf-8"))
        return path

    def test_positive_the_shipped_file_is_the_b2_starting_set(self):
        patterns = approver.load_verify_commands()
        self.assertEqual(len(patterns), 7)
        raw = approver.VERIFY_COMMANDS_PATH.read_bytes()
        self.assertNotIn(b"\r", raw)

    def test_positive_comments_and_blank_lines_are_ignored(self):
        patterns = approver.load_verify_commands(self.write("# a comment\n\n  ls  \n"))
        self.assertEqual([p.pattern for p in patterns], ["ls"])

    def test_rejection_a_bad_pattern_names_its_line(self):
        with self.assertRaisesRegex(approver.ApproverError, "line 2 is not a valid"):
            approver.load_verify_commands(self.write("ls\npython (\n"))

    def test_rejection_an_empty_list(self):
        with self.assertRaisesRegex(approver.ApproverError, "lists no commands"):
            approver.load_verify_commands(self.write("# nothing\n"))

    def test_rejection_a_missing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(approver.ApproverError, "cannot read"):
                approver.load_verify_commands(Path(folder) / "none.txt")


class RefusalRecordTests(ApproverTestCase):

    def test_positive_a_refusal_records_tool_detail_and_reason(self):
        self.approver.decide("Bash", {"command": "git status"})
        record = self.approver.refusals[-1]
        self.assertEqual(record["tool"], "Bash")
        self.assertEqual(record["detail"], "git status")
        self.assertIn("only these verification commands", record["reason"])

    def test_positive_a_long_detail_is_cut_short(self):
        self.approver.decide("Bash", {"command": "x" * 1000})
        self.assertEqual(len(self.approver.refusals[-1]["detail"]), 300)


if __name__ == "__main__":
    unittest.main()
