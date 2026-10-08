#!/usr/bin/env python3
"""Hermetic tests for a Codex builder's tool-call decisions (codex_rules.py), the hook
Codex runs before every call (codex_hook.py) and the approval adapter.

No test starts Codex or makes a model call. The decisions are plain functions, tested
directly; the hook is run as Codex runs it, as a subprocess from a frozen folder in a
temporary directory, and judged by what it prints and the line it records.

Same three controls as the other suites:

    positive control  a call the plan allows (a listed read command, a listed
                      verification command, a patch inside the edit paths). It must be
                      allowed.
    rejection control a call the plan refuses. It must be refused, the reason named.
    negative control  something that looks like a refused shape but is not one.

    python workflows/review-orchestration/tests/test_codex_rules.py
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent
SCRIPTS = WORKFLOW / "scripts"
CONFIG = WORKFLOW / "config"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


brief = _load("brief")
limits = _load("limits")
settings = _load("settings")
approver = _load("approver")
codex_rules = _load("codex_rules")
providers = _load("providers")
runrecord = _load("runrecord")

EDIT_PATHS = ["workflows/doc-sync-guard/", "notes/plan.md"]
RUN_ID = "20261002-101500-ab12"

# The 20 distinct shell commands Codex ran in the review rounds recorded in runs/, as
# the model wrote them (short plan fact 10): the read list's positive control.
RECORDED = [
    'Get-Content -LiteralPath memory/review_process.md',
    'rg -n -C 12 "triag|likely|harmful|refus" memory/review_process.md '
    'memory/feedback_realistic_break_test.md',
    'Get-Content -LiteralPath workflows/doc-sync-guard/scripts/context_parse.py',
    'git status --short',
    'rg -n "Revision History|15|cap|archive" workflows/audit/scripts '
    'workflows/doc-sync-guard/scripts/context_parse.py',
    'Get-Content -LiteralPath workflows/doc-sync-guard/tests/test_doc_sync_guard.py '
    '| Select-Object -First 115',
    'git diff -- workflows/doc-sync-guard',
    'Get-Content -LiteralPath workflows/doc-sync-guard/tests/test_doc_sync_guard.py '
    '| Select-Object -Skip 925 -First 180',
    'git ls-files workflows/doc-sync-guard',
    'Get-Content -LiteralPath workflows/doc-sync-guard/tests/test_doc_sync_guard.py '
    '| Select-Object -Skip 400 -First 110',
    'rg -n -C 8 "archiv.*LOG.md|15 entries|Revision History.*cap" AGENTS.md',
    'rg -n "def _guard_messages|append_log\\(" '
    'workflows/doc-sync-guard/tests/test_doc_sync_guard.py',
    "Get-Content -LiteralPath 'memory/review_process.md'",
    "Get-Content -LiteralPath 'memory/feedback_realistic_break_test.md'",
    "Get-Content -LiteralPath 'workflows/doc-sync-guard/scripts/context_parse.py'; "
    "Get-Content -LiteralPath 'workflows/doc-sync-guard/tests/test_doc_sync_guard.py' "
    "-TotalCount 125",
    'git diff -- workflows/doc-sync-guard; git status --short -- '
    'workflows/doc-sync-guard; git ls-files workflows/doc-sync-guard',
    'rg -n -C 9 "Revision History cap|archiv|15 entr|15-line" AGENTS.md',
    "git diff --check -- workflows/doc-sync-guard; Get-Content -LiteralPath "
    "'workflows/doc-sync-guard/LOG.md' -Tail 6; Get-Content -LiteralPath "
    "'workflows/doc-sync-guard/tests/LOG.md' -Tail 6",
    "Get-Content -LiteralPath 'workflows/doc-sync-guard/tests/test_doc_sync_guard.py' "
    "| Select-Object -Skip 350 -First 120; Get-Content -LiteralPath "
    "'workflows/doc-sync-guard/tests/test_doc_sync_guard.py' | Select-Object -Skip 930 "
    "-First 200",
    "Get-Content -LiteralPath 'AGENTS.md' | Select-Object -Skip 257 -First 36; "
    "Get-Content -LiteralPath 'workflows/doc-sync-guard/tests/test_doc_sync_guard.py' "
    "| Select-Object -Skip 320 -First 45; git status --short",
]

# Fix 5 (short plan 3.7 and 11.5): Get-Content is accepted only with both encodings.
BOTH = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "


def with_both_encodings(command):
    """A recorded command with each Get-Content written in the one accepted form."""
    return re.sub(r"Get-Content -LiteralPath ('[^']*'|[^\s;']+)",
                  lambda m: f"{BOTH}Get-Content -LiteralPath {m.group(1)} -Encoding UTF8",
                  command)


# One command each line of the read list accepts, in file order, for the every-line
# flag test. A new line needs its own case here.
READ_BASE = [
    f"{BOTH}Get-Content -LiteralPath workflows/x/a.py -Encoding UTF8 -TotalCount 5 "
    "| Select-Object -Skip 1 -First 2",
    'rg -n "needle" workflows/x workflows/y',
    "git status --short -- workflows/x",
    "git diff --check -- workflows/x workflows/y",
    "git ls-files workflows/x workflows/y",
    'Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz"',
]

# One command each line of the Codex verification list accepts, in file order.
VERIFY_BASE = [
    "python -m py_compile workflows/x/a.py",
    "python workflows/x/tests/test_run.py RunTests",
    "python workflows/encoding-guard/scripts/run.py --check",
    "python workflows/doc-sync-guard/scripts/run.py --check",
]
NAMED_GUARDS = ("ai-style-guard", "backlog-guard", "encoding-guard",
                "skill-hardening-guard", "sync-architecture")
FLAGS = ("--help", "--save", "-q", "--help x", "--save out.md", "-k name", "--scope=all")


def patch(*lines):
    return "\n".join(("*** Begin Patch", *lines, "*** End Patch")) + "\n"


class RulesCase(unittest.TestCase):

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        (self.root / "workflows" / "doc-sync-guard" / "scripts").mkdir(parents=True)
        (self.root / "notes").mkdir()
        self.approver = approver.Approver(EDIT_PATHS, codex_rules.load_verify_commands(),
                                          root=self.root)
        self.pattern = codex_rules.load_read_pattern()

    def decide(self, tool, tool_input):
        return codex_rules.decide(tool, tool_input, self.approver, self.pattern)

    def allowed(self, tool, tool_input):
        decision = self.decide(tool, tool_input)
        self.assertTrue(decision.allowed, f"{tool} {tool_input} was refused: "
                        f"{decision.reason}")

    def refused(self, tool, tool_input, reason=""):
        decision = self.decide(tool, tool_input)
        self.assertFalse(decision.allowed, f"{tool} {tool_input} was allowed")
        self.assertTrue(decision.reason.startswith("Refused by the orchestrator:"),
                        decision.reason)
        self.assertIn(reason, decision.reason)
        return decision

    def shell(self, command):
        self.allowed("Bash", {"command": command})

    def no_shell(self, command, reason=""):
        return self.refused("Bash", {"command": command}, reason)


class ReadCommandTests(RulesCase):

    def test_positive_the_recorded_commands_are_accepted_with_both_encodings(self):
        # Since fix 5 the positive control is the recorded commands with each
        # Get-Content in the accepted form (short plan 3.7).
        self.assertEqual(len(RECORDED), 20)
        for command in RECORDED:
            with self.subTest(command=command):
                self.shell(with_both_encodings(command))

    def test_rejection_the_recorded_get_content_forms_are_refused_and_told_the_form(self):
        recorded = [c for c in RECORDED if "Get-Content" in c]
        self.assertEqual(len(recorded), 11)
        for command in recorded:
            with self.subTest(command=command):
                self.no_shell(command, "Read a file with Get-Content only as `"
                                       + codex_rules.GET_CONTENT_FORM + "`")

    def test_rejection_either_encoding_alone(self):
        # Fact 16: each half alone came back wrong through Codex's command runner.
        for command in ("Get-Content -LiteralPath notes/a.md",
                        "Get-Content -LiteralPath notes/a.md -Encoding UTF8",
                        f"{BOTH}Get-Content -LiteralPath notes/a.md",
                        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
                        "Get-Content -LiteralPath notes/a.md -Encoding utf8",
                        f"{BOTH}Get-Content -LiteralPath notes/a.md -Encoding Default"):
            with self.subTest(command=command):
                self.no_shell(command, codex_rules.GET_CONTENT_FORM)

    def test_negative_the_form_is_not_told_for_another_reason(self):
        decision = self.no_shell(f"{BOTH}Get-Content -LiteralPath .env -Encoding UTF8",
                                 "`.env` file")
        self.assertNotIn(codex_rules.GET_CONTENT_FORM, decision.reason)
        decision = self.no_shell("git status --short; rm x")
        self.assertNotIn(codex_rules.GET_CONTENT_FORM, decision.reason)

    def test_positive_the_prompt_shows_the_form_the_line_accepts(self):
        text = (WORKFLOW / "prompts" / "builder-tools-codex.md").read_text(encoding="utf-8")
        shown = re.search(r"`(\[Console\][^`]+)`", text).group(1)
        self.shell(shown)
        self.assertTrue(shown.startswith(BOTH))

    def test_positive_every_line_in_a_form_it_names(self):
        lines = codex_rules.read_command_lines()
        self.assertEqual(len(lines), len(READ_BASE), "a new line needs its own case here")
        for line, command in zip(lines, READ_BASE):
            with self.subTest(line=line):
                self.assertTrue(re.fullmatch(codex_rules.expand(line), command), command)
                self.shell(command)
        self.shell("  git status --short  ")
        self.shell("; ".join(READ_BASE))

    def test_rejection_no_line_accepts_a_flag_it_does_not_name(self):
        # Seven flag shapes, bare and with a value, at every position after the
        # program, in a command each line otherwise accepts. It checks those shapes on
        # every line of the file; it does not prove the rule for every possible input.
        for base in READ_BASE:
            words = base.split(" ")
            for flag in FLAGS:
                for position in range(1, len(words) + 1):
                    command = " ".join(words[:position] + flag.split(" ")
                                       + words[position:])
                    with self.subTest(command=command):
                        self.no_shell(command)

    def test_rejection_a_flag_as_or_at_the_start_of_a_path_or_a_pattern(self):
        # Nothing the builder supplies may begin with `-`: as the whole of, and at the
        # start of, every PATH and every QUOTED token, in both quote forms.
        for command in (
                f"{BOTH}Get-Content -LiteralPath -Raw -Encoding UTF8",
                f"{BOTH}Get-Content -LiteralPath '-Raw' -Encoding UTF8",
                f"{BOTH}Get-Content -LiteralPath -x/a.py -Encoding UTF8",
                f"{BOTH}Get-Content -LiteralPath '-x/a.py' -Encoding UTF8",
                'rg "--pre=python" a b',
                "rg '--pre=python' a b",
                'rg "-e" a',
                'rg -n "--files" workflows',
                'rg "needle" --pre=python',
                'rg "needle" -g',
                "rg \"needle\" '--pre=python'",
                'rg "needle" a -uu',
                "git status --short -- -x",
                "git diff -- --output=out.txt",
                "git diff -- '--output=out.txt'",
                "git diff --check -- a --no-index",
                "git ls-files --others",
                "git ls-files '--others'",
                "git ls-files a --stage"):
            with self.subTest(command=command):
                self.no_shell(command)

    def test_rejection_a_read_joined_with_anything_else(self):
        for command in (
                "git status --short; rm -rf workflows",
                "git status --short;git status --short",
                "git status --short | Out-File x.txt",
                "git status --short && git push",
                "git status --short > out.txt",
                f"{BOTH}Get-Content -LiteralPath a.py -Encoding UTF8 | Set-Content b.py",
                f"{BOTH}Get-Content -LiteralPath a.py -Encoding UTF8 | Select-Object "
                "-First 3 | iex",
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; rm x",
                "git diff -- a; ",
                "; git status --short"):
            with self.subTest(command=command):
                self.no_shell(command)

    def test_rejection_what_the_shell_would_expand_in_double_quotes(self):
        self.no_shell('rg -n "$(Remove-Item x)" workflows')
        self.no_shell('rg -n "`whoami`" workflows')
        self.no_shell('rg -n "$env:SECRET" workflows')

    def test_negative_a_single_quoted_pattern_keeps_its_dollar_sign(self):
        # PowerShell expands nothing inside single quotes.
        self.shell("rg -n '$total' workflows")

    def test_rejection_a_path_outside_the_project(self):
        for path in ("/etc/passwd", "~/secret", "D:/data/a.txt", "D:\\data",
                     "workflows/x/../../../y", "..", "'../y'"):
            with self.subTest(path=path):
                self.no_shell(f"{BOTH}Get-Content -LiteralPath {path} -Encoding UTF8")

    def test_rejection_a_read_that_names_an_env_file(self):
        # The approver's shell checks apply to a read command as to any other.
        self.no_shell(f"{BOTH}Get-Content -LiteralPath .env -Encoding UTF8", "`.env` file")
        self.no_shell(f"{BOTH}Get-Content -LiteralPath 'sub/.env.local' -Encoding UTF8",
                      "`.env` file")
        self.no_shell('rg -n "KEY" .env', "`.env` file")
        self.no_shell("git diff -- workflows/.ENV", "`.env` file")

    def test_negative_a_venv_path_is_not_an_env_file(self):
        self.shell("git ls-files .venv/x")

    def test_rejection_more_than_one_line(self):
        self.no_shell("git status --short\ngit status --short")

    def test_rejection_no_command(self):
        self.no_shell(None, "no command given")
        self.no_shell("   ", "no command given")
        self.refused("Bash", {}, "no command given")

    def test_rejection_a_list_file_that_cannot_be_used(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "codex-read-commands.txt"
        with self.assertRaisesRegex(approver.ApproverError, "cannot read"):
            codex_rules.load_read_pattern(path)
        path.write_bytes(b"# nothing\n")
        with self.assertRaisesRegex(approver.ApproverError, "lists no commands"):
            codex_rules.load_read_pattern(path)
        path.write_bytes(b"git status\nrg (\n")
        with self.assertRaisesRegex(approver.ApproverError, "command 2 is not a valid"):
            codex_rules.load_read_pattern(path)


class ShellOverrideTests(RulesCase):
    """A shell call asking for more than the sandbox allows is refused on both allow
    paths, whatever its command (code review finding R1-2)."""

    READ = with_both_encodings("Get-Content -LiteralPath notes/a.txt")
    VERIFY = "python workflows/x/tests/test_run.py"

    OVERRIDES = [("sandbox_permissions", "require_escalated"),
                 ("additional_permissions", {"network": True}),
                 ("with_escalated_permissions", True),
                 ("prefix_rule", ["python", "workflows"]),
                 ("dangerouslyDisableSandbox", True)]

    def test_positive_both_allow_paths_take_a_plain_call(self):
        self.shell(self.READ)
        self.shell(self.VERIFY)

    def test_rejection_an_override_is_refused_on_a_listed_read_and_a_listed_check(self):
        self.assertEqual([field for field, _ in self.OVERRIDES],
                         list(codex_rules.SHELL_OVERRIDE_FIELDS))
        for command in (self.READ, self.VERIFY):
            for field, value in self.OVERRIDES:
                with self.subTest(command=command, field=field):
                    self.refused("Bash", {"command": command, field: value},
                                 f"`{field}`")

    def test_negative_a_default_value_is_not_an_override(self):
        for command in (self.READ, self.VERIFY):
            for field, value in (("sandbox_permissions", "use_default"),
                                 ("with_escalated_permissions", False),
                                 ("additional_permissions", None),
                                 ("prefix_rule", [])):
                with self.subTest(command=command, field=field):
                    self.allowed("Bash", {"command": command, field: value})

    def test_positive_the_whole_call_reaches_the_approver(self):
        # A field the approver judges, and this module does not, still counts.
        seen = []
        real = self.approver.decide

        def spy(tool, tool_input):
            seen.append(dict(tool_input))
            return real(tool, tool_input)
        self.approver.decide = spy
        self.decide("Bash", {"command": self.VERIFY, "timeout_ms": 5000})
        self.assertEqual(seen, [{"command": self.VERIFY, "timeout_ms": 5000}])


class ShellFolderTests(RulesCase):
    """Every listed command is judged as run from the project root, so a shell call
    naming another working folder is refused on both allow paths (code review finding
    R2-1)."""

    READ = ShellOverrideTests.READ
    VERIFY = ShellOverrideTests.VERIFY

    def test_positive_the_project_root_in_any_spelling_is_accepted(self):
        for field in codex_rules.SHELL_FOLDER_FIELDS:
            for value in (None, "", ".", str(self.root), self.root.as_posix(),
                          "notes/..", str(self.root) + "/"):
                for command in (self.READ, self.VERIFY):
                    with self.subTest(field=field, value=value, command=command):
                        self.allowed("Bash", {"command": command, field: value})

    def test_rejection_another_folder_is_refused_on_a_listed_read_and_a_listed_check(self):
        outside = self.root.parent
        for field in codex_rules.SHELL_FOLDER_FIELDS:
            for value in ("notes", "workflows/x", str(outside), "..", "C:/Windows",
                          5, ["notes"]):
                for command in (self.READ, self.VERIFY):
                    with self.subTest(field=field, value=value, command=command):
                        self.refused("Bash", {"command": command, field: value},
                                     f"`{field}`")


class VerifyListTests(RulesCase):
    """The Codex builder's own verification list (short plan 3.7)."""

    def setUp(self):
        super().setUp()
        self.codex = codex_rules.load_verify_commands()
        self.claude = approver.load_verify_commands()

    def test_positive_every_line_and_every_named_guard_is_accepted(self):
        self.assertEqual(len(self.codex), len(VERIFY_BASE),
                         "a new line needs its own case here")
        for pattern, command in zip(self.codex, VERIFY_BASE):
            with self.subTest(line=pattern.pattern):
                self.assertTrue(pattern.fullmatch(command), command)
                self.shell(command)
        for guard in NAMED_GUARDS:
            with self.subTest(guard=guard):
                self.shell(f"python workflows/{guard}/scripts/run.py --check")
        self.shell("python workflows/doc-sync-guard/scripts/run.py")
        self.shell("python workflows/x/tests/test_run.py RunTests.test_one -v")
        self.shell("python -m py_compile workflows/x/a.py workflows/x/b.py")

    def test_positive_every_form_it_names_is_also_on_the_claude_builders_list(self):
        # The Codex list only ever takes away from verify-commands.txt.
        forms = VERIFY_BASE + [f"python workflows/{guard}/scripts/run.py --check"
                               for guard in NAMED_GUARDS]
        forms.append("python workflows/doc-sync-guard/scripts/run.py")
        for command in forms:
            with self.subTest(command=command):
                self.assertTrue(any(p.fullmatch(command) for p in self.codex))
                self.assertTrue(any(p.fullmatch(command) for p in self.claude), command)

    def test_rejection_no_line_accepts_a_flag_it_does_not_name(self):
        # The every-line flag test of verify-commands.txt, over this list too.
        for pattern, base in zip(self.codex, VERIFY_BASE):
            words = base.split(" ")
            for flag in FLAGS:
                for position in range(2, len(words) + 1):
                    command = " ".join(words[:position] + flag.split(" ")
                                       + words[position:])
                    with self.subTest(command=command):
                        self.assertIsNone(pattern.fullmatch(command), command)
                        self.no_shell(command)

    def test_rejection_the_audit_and_close_out_in_every_form_the_other_list_takes(self):
        # Read-only, neither writes a log, but on the host the close-out verifier still
        # reads personal files; the Codex list gains both only once its commands run in
        # the container (isolation decision 27, stage S6).
        for command in (
                "python workflows/audit/scripts/run.py --context workflows/doc-sync-guard "
                "--read-only",
                "python workflows/audit/scripts/run.py --context workflows/x workflows/y "
                "--read-only",
                "python workflows/audit/scripts/run.py --context workflows/x --no-graph "
                "--read-only",
                "python workflows/close-out/scripts/run.py --read-only",
                "python workflows/close-out/scripts/run.py --read-only --scope all",
                "python workflows/close-out/scripts/run.py --read-only --json",
                "python workflows/close-out/scripts/run.py --read-only --scope doc-sync-guard "
                "--json"):
            with self.subTest(command=command):
                self.assertTrue(any(p.fullmatch(command) for p in self.claude),
                                "the control needs a command the Claude list accepts")
                self.no_shell(command, "only these verification commands")

    def test_rejection_a_check_the_list_does_not_name(self):
        # The Reddit collector's check makes an HTTP request; web-research's and the
        # personal-data guard's read the project's .env file. A workflow that does not
        # exist is refused too: the list is names, not a pattern.
        for workflow in ("reddit-collector", "web-research", "personal-data-guard",
                         "no-such-workflow", "audit", "close-out"):
            command = f"python workflows/{workflow}/scripts/run.py --check"
            with self.subTest(command=command):
                self.assertTrue(any(p.fullmatch(command) for p in self.claude),
                                "the control needs a command the Claude list accepts")
                self.no_shell(command, "only these verification commands")
        self.no_shell("python skills/web-research/scripts/run.py --check")

    def test_rejection_a_named_guard_with_another_flag(self):
        self.no_shell("python workflows/encoding-guard/scripts/run.py --fix")
        self.no_shell("python workflows/encoding-guard/scripts/run.py --check --fix")
        self.no_shell("python workflows/backlog-guard/scripts/run.py --snapshot")

    def test_positive_the_shipped_files_are_lf(self):
        for name in codex_rules.FROZEN_LISTS:
            self.assertNotIn(b"\r", (CONFIG / name).read_bytes())


class PatchTests(RulesCase):

    def edit(self, text):
        self.allowed("apply_patch", {"command": text})

    def no_edit(self, text, reason=""):
        return self.refused("apply_patch", {"command": text}, reason)

    def test_positive_one_file_inside_the_edit_paths(self):
        self.edit(patch("*** Update File: workflows/doc-sync-guard/scripts/run.py",
                        "@@", "-old", "+new"))
        self.edit(patch("*** Add File: notes/plan.md", "+text"))
        self.edit(patch("*** Delete File: workflows/doc-sync-guard/old.py"))

    def test_positive_several_files_and_a_move(self):
        self.edit(patch("*** Add File: workflows/doc-sync-guard/a.py", "+x",
                        "*** Update File: workflows/doc-sync-guard/b.py",
                        "*** Move to: workflows/doc-sync-guard/c.py", "@@", "-x", "+y",
                        "*** End of File",
                        "*** Delete File: workflows/doc-sync-guard/d.py"))
        self.assertEqual(codex_rules.patch_files(patch(
            "*** Update File: a.py", "*** Move to: b.py")), ["a.py", "b.py"])

    def test_positive_an_absolute_path_inside_the_edit_paths(self):
        self.edit(patch(f"*** Add File: {self.root / 'workflows/doc-sync-guard/a.py'}",
                        "+x"))

    def test_rejection_a_file_outside_the_edit_paths(self):
        self.no_edit(patch("*** Update File: workflows/audit/scripts/run.py"),
                     "outside this run's edit paths")
        self.no_edit(patch("*** Add File: workflows/doc-sync-guard/a.py", "+x",
                           "*** Add File: notes/other.md", "+x"),
                     "outside this run's edit paths")

    def test_rejection_a_move_out_of_the_edit_paths(self):
        self.no_edit(patch("*** Update File: workflows/doc-sync-guard/a.py",
                           "*** Move to: workflows/audit/a.py"),
                     "outside this run's edit paths")

    def test_rejection_env_git_and_outside_the_project(self):
        self.no_edit(patch("*** Add File: workflows/doc-sync-guard/.env", "+K=1"),
                     "is a `.env` file")
        self.no_edit(patch("*** Add File: workflows/doc-sync-guard/.git/config", "+x"),
                     "is under `.git/`")
        self.no_edit(patch(f"*** Add File: {self.root.parent / 'x.py'}", "+x"),
                     "is outside the project")

    def test_rejection_an_unknown_header_line(self):
        self.no_edit(patch("*** Update File: workflows/doc-sync-guard/a.py",
                           "*** Copy File: workflows/audit/a.py"),
                     "a line the orchestrator does not recognise")
        self.no_edit(patch("*** Rename: a b"), "does not recognise")

    def test_negative_a_content_line_that_looks_like_a_header(self):
        # Inside a patch a content line carries a + or - before its text.
        self.edit(patch("*** Add File: workflows/doc-sync-guard/a.md",
                        "+*** Add File: workflows/audit/x.py", "+*** anything"))

    def test_rejection_no_file_no_begin_and_not_text(self):
        self.no_edit(patch(), "names no file")
        self.no_edit(patch("*** Add File: "), "names no file")
        self.no_edit("*** Update File: workflows/doc-sync-guard/a.py\n",
                     "does not start with `*** Begin Patch`")
        self.no_edit("", "does not start with")
        self.refused("apply_patch", {}, "the patch is not text")
        self.refused("apply_patch", {"command": 7}, "the patch is not text")

    def test_rejection_a_file_in_a_run_record_whatever_the_edit_paths(self):
        # A builder whose edit paths cover this workflow still may not write a record.
        wide = approver.Approver(["workflows/review-orchestration/"],
                                 codex_rules.load_verify_commands(), root=self.root)
        inside = patch("*** Add File: workflows/review-orchestration/scripts/x.py", "+x")
        record = patch("*** Update File: workflows/review-orchestration/runs/"
                       f"{RUN_ID}/state.json")
        self.assertTrue(codex_rules.decide("apply_patch", {"command": inside}, wide,
                                           self.pattern).allowed)
        for text in (record,
                     patch("*** Add File: Workflows/Review-Orchestration/RUNS/x/y.md", "+x"),
                     patch("*** Add File: workflows/review-orchestration/scripts/x.py",
                           "+x", "*** Move to: workflows/review-orchestration/runs/x.py")):
            decision = codex_rules.decide("apply_patch", {"command": text}, wide,
                                          self.pattern)
            self.assertFalse(decision.allowed, text)
            self.assertIn("run record", decision.reason)


class OtherToolTests(RulesCase):

    def test_rejection_every_other_tool(self):
        for tool in ("webrun", "clockcurr_time", "spawn_agent", "image_gen", "js_repl",
                     "mcp__biblio-tools__append_log", "update_goal", "Read", "Write",
                     "", None):
            with self.subTest(tool=tool):
                decision = self.refused(tool, {"command": "git status --short"},
                                        "is not permitted in this run")
                self.assertIn("apply_patch", decision.reason)

    def test_positive_the_detail_is_short_and_names_a_patchs_files(self):
        self.assertEqual(codex_rules.detail("Bash", {"command": " git   status "}),
                         "git status")
        self.assertEqual(codex_rules.detail("apply_patch", {"command": patch(
            "*** Add File: a.py", "+x", "*** Delete File: b.py")}), "a.py, b.py")
        self.assertEqual(len(codex_rules.detail("Bash", {"command": "x" * 900})), 300)
        self.assertEqual(codex_rules.detail("webrun", None), "")

    def test_positive_the_run_id_form_is_runrecords(self):
        self.assertEqual(codex_rules.RUN_ID.pattern, runrecord.RUN_ID.pattern)


class ApprovalTests(unittest.TestCase):
    """The approval handler's three rows (short plan 3.4)."""

    LINES = [{"tool_use_id": "exec-allowed", "decision": "allow"},
             {"tool_use_id": "exec-denied", "decision": "deny"},
             {"tool_use_id": "exec-both", "decision": "allow"},
             {"tool_use_id": "exec-both", "decision": "deny"}]

    def test_positive_a_request_the_hook_allowed_is_accepted(self):
        for method in codex_rules.APPROVAL_METHODS:
            reply, refusal, failed = codex_rules.approval_reply(
                method, {"itemId": "exec-allowed", "command": "whatever it says"},
                self.LINES)
            self.assertEqual((reply, refusal, failed), ({"decision": "accept"}, None, False))

    def test_rejection_no_line_or_a_deny_line_is_declined_and_recorded(self):
        for call in ("exec-unknown", "exec-denied", "exec-both", None):
            for method, tool in zip(codex_rules.APPROVAL_METHODS, ("Bash", "apply_patch")):
                with self.subTest(call=call, method=method):
                    reply, refusal, failed = codex_rules.approval_reply(
                        method, {"itemId": call, "command": "git push"}, self.LINES)
                    self.assertEqual(reply, {"decision": "decline"})
                    self.assertFalse(failed)
                    self.assertEqual(refusal["tool"], tool)
                    self.assertIn("no hook decision for this call", refusal["reason"])
                    self.assertEqual(refusal["detail"], "git push")

    def test_negative_the_command_text_is_never_read(self):
        # An allowed id is accepted whatever the request's command says, and a listed
        # command is declined when the hook recorded nothing for its id.
        reply, _, _ = codex_rules.approval_reply(
            codex_rules.APPROVAL_METHODS[0],
            {"itemId": "exec-new", "command": "git status --short"}, self.LINES)
        self.assertEqual(reply, {"decision": "decline"})

    def test_rejection_any_other_request_marks_the_session_failed(self):
        for method in ("item/permissions/requestApproval", "item/tool/requestUserInput",
                       "item/tool/call", "mcpServer/elicitation/request",
                       "execCommandApproval", "applyPatchApproval", "something/new"):
            with self.subTest(method=method):
                self.assertEqual(codex_rules.approval_reply(method, {"itemId": "exec-allowed"},
                                                            self.LINES), ({}, None, True))

    def test_positive_reading_a_decisions_file(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "hook-decisions.jsonl"
        self.assertEqual(codex_rules.read_decisions(path), [])
        path.write_bytes(b'{"tool_use_id": "a", "decision": "allow"}\nnot json\n[1]\n')
        lines = codex_rules.read_decisions(path)
        self.assertEqual(lines, [{"tool_use_id": "a", "decision": "allow"}])
        self.assertEqual(codex_rules.decision_for(lines, "a"), "allow")
        self.assertIsNone(codex_rules.decision_for(lines, "b"))
        self.assertIsNone(codex_rules.decision_for(lines, None))


class HookTests(unittest.TestCase):
    """codex_hook.py, run as Codex runs it: a subprocess from the run's frozen folder,
    made by the builder session's own first launch."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        base = Path(folder.name).resolve()
        self.root = base / "project"
        (self.root / "workflows" / "doc-sync-guard").mkdir(parents=True)
        (self.root / "notes").mkdir()
        self.temp = base / "temp"
        self.builder = providers.CodexBuilder(
            "gpt-6-sol", "high", approver.Approver(EDIT_PATHS, [], root=self.root),
            cwd=self.root, run_dir=base / "runs" / RUN_ID, temp_root=self.temp)
        self.assertTrue(self.builder._freeze())
        self.frozen = self.builder.frozen_dir
        self.decisions = self.frozen / "hook-decisions.jsonl"

    def event(self, tool="Bash", tool_input=None, call="exec-1", session="thread-1"):
        return {"hook_event_name": "PreToolUse", "tool_name": tool,
                "tool_input": tool_input or {"command": "git status --short"},
                "tool_use_id": call, "turn_id": "turn-1", "session_id": session,
                "cwd": str(self.root), "model": "gpt-6-sol"}

    def run_hook(self, stdin, run_id=RUN_ID, env=None, cwd=None):
        done = subprocess.run(
            [sys.executable, str(self.frozen / "codex_hook.py"), run_id],
            input=stdin if isinstance(stdin, str) else json.dumps(stdin),
            capture_output=True, encoding="utf-8", timeout=60, env=env,
            cwd=str(cwd or self.temp))
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def lines(self):
        return codex_rules.read_decisions(self.decisions)

    def denied(self, out, reason):
        specific = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(specific["hookEventName"], "PreToolUse")
        self.assertEqual(specific["permissionDecision"], "deny")
        self.assertTrue(specific["permissionDecisionReason"]
                        .startswith("Refused by the orchestrator:"))
        self.assertIn(reason, specific["permissionDecisionReason"])

    def test_positive_the_frozen_folder_holds_the_rules_and_the_check_helper(self):
        # The check helper and the container module run from here, with the Claude
        # list, since the helper judges either builder (isolation plan 6, item 1).
        names = {path.name for path in self.frozen.iterdir()}
        self.assertEqual(names, {"codex_hook.py", "codex_rules.py", "approver.py",
                                 "brief.py", "check_server.py", "container.py",
                                 "codex-verify-commands.txt", "codex-read-commands.txt",
                                 "verify-commands.txt", "hook.json"})
        config = json.loads((self.frozen / "hook.json").read_text(encoding="utf-8"))
        self.assertEqual(config, {"run_id": RUN_ID, "edit_paths": EDIT_PATHS,
                                  "project_root": str(self.root)})
        for name in codex_rules.FROZEN_MODULES:
            self.assertEqual((self.frozen / name).read_bytes(), (SCRIPTS / name).read_bytes())

    def test_positive_a_decision_line_escapes_non_ascii(self):
        # The file is copied into the run record (the chunk (d) short plan, 11.4).
        command = "git diff -- notes/café.md"
        self.run_hook(self.event(tool_input={"command": command}))
        raw = self.decisions.read_bytes()
        self.assertTrue(raw.isascii(), raw)
        self.assertIn("café", self.lines()[-1]["detail"])

    def test_positive_an_allowed_call_prints_nothing_and_records_allow(self):
        self.assertEqual(self.run_hook(self.event()), "")
        line, = self.lines()
        self.assertEqual((line["tool_use_id"], line["turn_id"], line["session_id"],
                          line["tool"], line["decision"], line["detail"]),
                         ("exec-1", "turn-1", "thread-1", "Bash", "allow",
                          "git status --short"))

    def test_positive_a_patch_and_a_verification_command_are_judged_by_the_run(self):
        # The project root and the edit paths come from hook.json, never from where the
        # frozen copies sit.
        inside = patch("*** Add File: workflows/doc-sync-guard/a.py", "+x")
        self.assertEqual(self.run_hook(self.event("apply_patch", {"command": inside})), "")
        self.assertEqual(self.run_hook(self.event(tool_input={
            "command": "python workflows/doc-sync-guard/scripts/run.py --check"},
            call="exec-2")), "")
        outside = patch("*** Add File: workflows/audit/a.py", "+x")
        self.denied(self.run_hook(self.event("apply_patch", {"command": outside},
                                             call="exec-3")),
                    "outside this run's edit paths")
        self.assertEqual([line["decision"] for line in self.lines()],
                         ["allow", "allow", "deny"])
        self.assertEqual(self.lines()[0]["detail"], "workflows/doc-sync-guard/a.py")

    def test_rejection_a_refused_call_prints_the_refusal_and_records_deny(self):
        for number, (tool, tool_input, reason) in enumerate((
                ("Bash", {"command": "curl https://example.com"},
                 "only these verification commands"),
                ("Bash", {"command": "python workflows/audit/scripts/run.py --context x"},
                 "only these verification commands"),
                ("webrun", {"query": "example.com"}, "is not permitted in this run"),
                ("clockcurr_time", {}, "is not permitted in this run"))):
            with self.subTest(tool=tool, tool_input=tool_input):
                self.denied(self.run_hook(self.event(tool, tool_input,
                                                     call=f"exec-{number}")), reason)
                line = self.lines()[-1]
                self.assertEqual((line["tool_use_id"], line["decision"]),
                                 (f"exec-{number}", "deny"))
                self.assertIn(reason, line["reason"])

    def test_rejection_a_bad_run_id(self):
        for run_id in ("not-a-run", "20261002-101500-zzzz", "20261002-101500-ab13", ""):
            with self.subTest(run_id=run_id):
                self.denied(self.run_hook(self.event(), run_id=run_id),
                            "was not started for this run")
                self.assertEqual(self.lines()[-1]["decision"], "deny")

    def test_rejection_a_missing_or_unreadable_hook_json(self):
        (self.frozen / "hook.json").unlink()
        self.denied(self.run_hook(self.event()), "hook settings cannot be read")
        (self.frozen / "hook.json").write_bytes(b"{not json")
        self.denied(self.run_hook(self.event(call="exec-2")), "hook settings cannot be read")
        (self.frozen / "hook.json").write_bytes(b'{"edit_paths": []}')
        self.denied(self.run_hook(self.event(call="exec-3")), "hook settings cannot be read")
        self.assertEqual([line["decision"] for line in self.lines()], ["deny"] * 3)

    def test_rejection_stdin_that_is_not_a_json_object(self):
        for stdin in ("", "not json", "[1, 2]", '"text"'):
            with self.subTest(stdin=stdin):
                self.denied(self.run_hook(stdin), "was not sent a JSON object")
        self.assertEqual([line["decision"] for line in self.lines()], ["deny"] * 4)
        # A line with no session id cannot become the session every later call must
        # match.
        self.assertEqual(self.run_hook(self.event()), "")

    def test_rejection_a_changed_session_id(self):
        self.assertEqual(self.run_hook(self.event()), "")
        self.denied(self.run_hook(self.event(call="exec-2", session="another-thread")),
                    "a session other than the run's builder session")
        self.assertEqual(self.run_hook(self.event(call="exec-3")), "")
        self.assertEqual([line["decision"] for line in self.lines()],
                         ["allow", "deny", "allow"])

    def test_rejection_an_allow_that_cannot_be_recorded_is_refused(self):
        # The line is written before anything is printed, so an allow with no line
        # cannot happen: where the file cannot be written, the call is refused.
        self.decisions.mkdir()
        self.denied(self.run_hook(self.event()), "could not record its decision")

    def test_rejection_any_exception_refuses(self):
        (self.frozen / "codex-read-commands.txt").write_bytes(b"rg (\n")
        self.denied(self.run_hook(self.event()), "could not judge this call")
        (self.frozen / "codex_rules.py").write_bytes(b"raise SystemError('broken')\n")
        self.denied(self.run_hook(self.event(call="exec-2")), "could not judge this call")

    def test_positive_the_frozen_copies_run_whatever_the_projects_copies_are(self):
        # A decoy folder holding broken copies of the same modules, put where Python
        # would look next, and used as the working folder: the frozen copies beside the
        # hook are the ones that run.
        decoy = self.temp / "decoy"
        decoy.mkdir()
        for name in ("codex_rules.py", "approver.py", "brief.py"):
            (decoy / name).write_bytes(b"raise SystemExit(3)\n")
        env = {**os.environ, "PYTHONPATH": str(decoy)}
        self.assertEqual(self.run_hook(self.event(), env=env, cwd=decoy), "")
        self.assertEqual(self.lines()[-1]["decision"], "allow")
        # And they need nothing but the standard library and themselves: no site
        # packages, no user site, no environment.
        isolated = subprocess.run(
            [sys.executable, "-E", "-s", "-S", str(self.frozen / "codex_hook.py"), RUN_ID],
            input=json.dumps(self.event(call="exec-2")), capture_output=True,
            encoding="utf-8", timeout=60, cwd=str(decoy))
        self.assertEqual((isolated.returncode, isolated.stdout), (0, ""), isolated.stderr)


class ProjectRulesFileTests(unittest.TestCase):
    """The project's Codex rules entry for starting, resuming and stopping a run (the
    chunk (d) short plan, 11.1), checked with ``codex execpolicy check`` from the binary
    the pinned ``openai-codex`` installs. That command matches plain tokens only and
    does not unwrap the ``powershell.exe -Command`` wrapper Codex uses on Windows, so
    these tests cannot show the wrapped form; a live Codex matching it is fact 17 of the
    short plan, measured, and live check 7."""

    RULES = WORKFLOW.parent.parent / ".codex" / "rules" / "review-orchestration.rules"
    RUN_PY = "workflows/review-orchestration/scripts/run.py"

    def check(self, *tokens):
        import codex_cli_bin  # the package that ships the binary; missing fails the test
        done = subprocess.run(
            [str(codex_cli_bin.bundled_codex_path()), "execpolicy", "check", "--rules",
             str(self.RULES), *tokens],
            capture_output=True, encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_positive_the_three_modes_are_allowed(self):
        for argv in (("--run", "BRIEF.md", "--builder", "codex", "--item", "x",
                      "--git-dir", "C:/BookDragon/personal-work.git"),
                     ("--resume", "20261004-000000-abcd", "--rounds", "1"),
                     ("--stop", "20261004-000000-abcd")):
            with self.subTest(mode=argv[0]):
                result = self.check("python", self.RUN_PY, *argv)
                self.assertEqual(result.get("decision"), "allow", result)

    def test_rejection_nothing_else_is_matched(self):
        for tokens in (("python", self.RUN_PY, "--check-brief", "BRIEF.md"),
                       ("python", self.RUN_PY),
                       ("python", "workflows/audit/scripts/run.py", "--run"),
                       ("python", "-c", "print(1)"),
                       ("py", self.RUN_PY, "--run", "BRIEF.md")):
            with self.subTest(tokens=tokens):
                result = self.check(*tokens)
                self.assertEqual(result.get("matchedRules"), [], result)
                self.assertNotIn("decision", result)

    def test_positive_the_file_holds_only_allow_rules_for_this_script(self):
        text = self.RULES.read_text(encoding="utf-8")
        patterns = re.findall(r"pattern = \[(.*?)\]", text)
        self.assertEqual(len(patterns), 3)
        for pattern in patterns:
            self.assertTrue(pattern.startswith(f'"python", "{self.RUN_PY}", "--'), pattern)
        self.assertEqual(text.count('decision = "allow"'), 3)


if __name__ == "__main__":
    unittest.main()
