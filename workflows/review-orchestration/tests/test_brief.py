#!/usr/bin/env python3
"""Hermetic tests for the review-orchestration brief checker.

The checker validates, so the three controls read as follows:

    positive control  a LEGAL brief, or a legal instance of one rule's subject,
                      built from the specification. It must produce nothing.
    rejection control an ILLEGAL instance, which the checker must refuse with the
                      reason named.
    negative control  something that looks like the subject but is not an instance
                      of it (an operator inside quotes, a heading inside a comment,
                      a list item above a dash line). It must not be refused for it.

Each brief is written into a temporary project root with ``write_bytes``, so no
test depends on the real tree, and nothing writes to the real LOG.md: the command
line is exercised with an injected log path or with ``--dry-run``. The one
exception is the shipped template, which is read from where it lives, because
the proof that an unfilled brief cannot start a run is about that file.

    python workflows/review-orchestration/tests/test_brief.py
"""

import contextlib
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent
SCRIPTS = WORKFLOW / "scripts"
PROJECT_ROOT = WORKFLOW.parent.parent
TEMPLATE = "workflows/review-orchestration/prompts/brief.md"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


brief = _load("brief")
run = _load("run")

VALID = {
    "Goal": "The checker refuses an unready brief.",
    "Constraints": "Standard library only.",
    "Out of scope": "None",
    "Acceptance checks": "- python workflows/x/tests/test_x.py",
    "Edit paths": "- workflows/x/ | Measure: git ls-files workflows/x",
    "Open questions": "",
    "Source": "- `docs/plan.md`, section 10.2\n- `memory/`\n- The backlog item",
}


def compose(sections=None, order=None, preamble="# Brief: test\n"):
    """Build a brief from section bodies. A value of None drops that section."""
    bodies = dict(VALID)
    bodies.update(sections or {})
    parts = [preamble]
    for title in order or brief.HEADINGS:
        if bodies.get(title) is None:
            continue
        parts.append(f"## {title}\n{bodies[title]}\n")
    return "\n".join(parts)


class BriefCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "docs").mkdir()
        (self.root / "docs" / "plan.md").write_bytes(b"# Plan\n")
        (self.root / "memory").mkdir()
        (self.root / "runs").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, text, name="runs/brief.md"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
        return name

    def check(self, text, name="runs/brief.md"):
        lines, code = brief.check_brief(self.write(text, name), self.root)
        return lines, code

    def assertPasses(self, text):
        lines, code = self.check(text)
        self.assertEqual((lines, code), ([f"PASS: runs/brief.md"], 0))

    def assertRefused(self, text, heading, fragment):
        lines, code = self.check(text)
        self.assertEqual(code, 1, lines)
        matching = [line for line in lines
                    if line.startswith(f"FAIL: {heading}: ") and fragment in line]
        self.assertTrue(matching, f"no `{heading}` line containing {fragment!r} in {lines}")
        return lines


class ReadingTests(BriefCase):
    def test_positive_control_valid_brief_passes(self):
        self.assertPasses(compose())

    def test_positive_control_crlf_line_endings_accepted(self):
        self.assertPasses(compose().replace("\n", "\r\n"))

    def test_positive_control_lone_cr_line_endings_accepted(self):
        self.assertPasses(compose().replace("\n", "\r"))

    def test_rejection_control_lone_cr_still_separates_sections(self):
        text = compose({"Goal": ""}).replace("\n", "\r")
        self.assertRefused(text, "Goal", "section is empty")

    def test_positive_control_backslash_argument_accepted(self):
        self.write(compose())
        lines, code = brief.check_brief("runs\\brief.md", self.root)
        self.assertEqual(code, 0, lines)

    def test_rejection_control_absolute_path(self):
        self.write(compose())
        lines, code = brief.check_brief(str(self.root / "runs" / "brief.md"), self.root)
        self.assertEqual(code, 1)
        self.assertIn("not a project-relative path", lines[0])

    def test_rejection_control_leading_slash(self):
        lines, _ = brief.check_brief("/runs/brief.md", self.root)
        self.assertIn("not a project-relative path", lines[0])

    def test_rejection_control_escapes_project(self):
        lines, code = brief.check_brief("../outside.md", self.root)
        self.assertEqual(code, 1)
        self.assertIn("resolves outside the project", lines[0])

    def test_rejection_control_missing_file(self):
        lines, code = brief.check_brief("runs/nope.md", self.root)
        self.assertEqual((code, lines), (1, ["FAIL: Brief: `runs/nope.md` does not exist"]))

    def test_rejection_control_folder(self):
        lines, _ = brief.check_brief("runs", self.root)
        self.assertIn("is a folder", lines[0])

    def test_rejection_control_empty_argument(self):
        lines, _ = brief.check_brief("", self.root)
        self.assertEqual(lines, ["FAIL: Brief: no brief path given"])

    def test_rejection_control_invalid_utf8(self):
        lines, code = self.check(compose().encode("utf-8") + b"\xff\n")
        self.assertEqual(code, 1)
        self.assertEqual(len(lines), 1)
        self.assertIn("not valid UTF-8", lines[0])

    def test_rejection_control_byte_order_mark_still_checks_rest(self):
        lines, code = self.check(b"\xef\xbb\xbf" + compose({"Goal": ""}).encode("utf-8"))
        self.assertEqual(code, 1)
        self.assertEqual(lines[0], "FAIL: Brief: starts with a byte-order mark")
        self.assertIn("FAIL: Goal: section is empty", lines)


class HeadingTests(BriefCase):
    def test_rejection_control_missing_heading(self):
        lines = self.assertRefused(compose({"Constraints": None}), "Headings",
                                   "missing `## Constraints`")
        self.assertEqual(len(lines), 1)

    def test_rejection_control_repeated_heading(self):
        text = compose() + "\n## Goal\nAgain.\n"
        self.assertRefused(text, "Headings", "`## Goal` appears more than once")

    def test_rejection_control_misspelt_heading_names_the_right_one(self):
        text = compose().replace("## Out of scope", "## Out Of Scope")
        lines = self.assertRefused(text, "Headings", "(expected `## Out of scope`)")
        self.assertIn("FAIL: Headings: missing `## Out of scope`", lines)

    def test_rejection_control_extra_heading(self):
        text = compose() + "\n## Notes\nSomething.\n"
        self.assertRefused(text, "Headings", "unexpected heading `## Notes`")

    def test_rejection_control_out_of_order(self):
        order = list(brief.HEADINGS)
        order[0], order[1] = order[1], order[0]
        self.assertRefused(compose(order=order), "Headings",
                           "`## Goal` is out of order (it must come before `## Constraints`)")

    def test_rejection_control_underlined_heading(self):
        text = compose({"Goal": "Done is checkable.\n\nNotes\n-----\nMore."})
        self.assertRefused(text, "Headings", "underlined heading `Notes`")

    def test_rejection_control_level_one_heading_after_first_section(self):
        text = compose({"Constraints": "# Stray title\nStandard library only."})
        self.assertRefused(text, "Headings", "level-1 heading `Stray title`")

    def test_positive_control_level_one_title_before_first_section(self):
        self.assertPasses(compose(preamble="# Title\n\nIntro text.\n\nTitle two\n=====\n"))

    def test_positive_control_deeper_headings_inside_a_section(self):
        self.assertPasses(compose({"Goal": "### Part one\nChecked.\n#### Detail\nMore."}))

    def test_negative_control_heading_inside_comment_does_not_count(self):
        text = compose({"Goal": "Checkable.\n<!--\n## Notes\n-->"})
        self.assertPasses(text)

    def test_negative_control_heading_inside_fence_does_not_count(self):
        text = compose({"Goal": "Checkable.\n```\n## Notes\n```"})
        lines = self.assertRefused(text, "Goal", "fenced code block")
        self.assertFalse([line for line in lines if line.startswith("FAIL: Headings")])

    def test_rejection_control_unclosed_comment_hides_the_rest(self):
        text = compose({"Edit paths": "- a/ | Measure: git ls-files a\n<!-- never closed"})
        lines = self.assertRefused(text, "Headings", "missing `## Open questions`")
        self.assertIn("FAIL: Headings: missing `## Source`", lines)

    def test_rejection_control_unclosed_fence_hides_the_rest(self):
        text = compose({"Edit paths": "- a/ | Measure: git ls-files a\n~~~"})
        self.assertRefused(text, "Headings", "missing `## Source`")

    def test_rejection_control_closing_hashes_are_part_of_the_title(self):
        text = compose().replace("## Goal\n", "## Goal ##\n")
        self.assertRefused(text, "Headings", "unexpected heading `## Goal ##`")

    def test_positive_control_indented_heading_and_trailing_spaces(self):
        self.assertPasses(compose().replace("## Goal\n", "   ## Goal   \n"))

    def test_negative_control_hash_without_space_is_not_a_heading(self):
        self.assertPasses(compose({"Goal": "##Goal is text here."}))

    def test_negative_control_dash_line_under_list_item_is_not_a_heading(self):
        text = compose({"Constraints": "- Standard library only.\n---"})
        self.assertPasses(text)

    def test_negative_control_dash_line_under_indented_code_is_not_a_heading(self):
        text = compose({"Goal": "Checkable.\n\n    indented\n---"})
        self.assertPasses(text)


class EmptyTests(BriefCase):
    def test_rejection_control_comment_only_section(self):
        self.assertRefused(compose({"Goal": "<!-- to fill -->"}), "Goal", "section is empty")

    def test_rejection_control_placeholders_count_as_empty(self):
        for body in ("...", "- ...", "<goal>", "- <goal>", "TODO", "TBD later",
                     "- TODO: write it", "TODOs"):
            with self.subTest(body=body):
                self.assertRefused(compose({"Constraints": body}), "Constraints",
                                   "section is empty")

    def test_negative_control_lowercase_todo_is_text(self):
        self.assertPasses(compose({"Constraints": "todo lists are out of bounds."}))

    def test_rejection_control_headings_alone_are_empty(self):
        self.assertRefused(compose({"Goal": "### Part one"}), "Goal", "section is empty")

    def test_rejection_control_fenced_block_refused(self):
        self.assertRefused(compose({"Constraints": "Rules.\n~~~\ncode\n~~~"}),
                           "Constraints", "line ")

    def test_positive_control_open_questions_may_be_blank(self):
        self.assertPasses(compose({"Open questions": ""}))

    def test_positive_control_out_of_scope_none(self):
        self.assertPasses(compose({"Out of scope": "None"}))


class AcceptanceTests(BriefCase):
    def test_positive_control_several_checks(self):
        self.assertPasses(compose({"Acceptance checks":
                                   "- python a.py\n- git diff --exit-code\n### More\n- ls"}))

    def test_positive_control_indented_bullet(self):
        self.assertPasses(compose({"Acceptance checks": "- python a.py\n  - python b.py"}))

    def test_rejection_control_wrapped_continuation_line(self):
        self.assertRefused(compose({"Acceptance checks": "- python a.py\n  --flag"}),
                           "Acceptance checks", "not a bullet")

    def test_rejection_control_not_a_bullet(self):
        self.assertRefused(compose({"Acceptance checks": "python a.py"}),
                           "Acceptance checks", "not a bullet written `- <command>`")

    def test_rejection_control_star_bullet(self):
        self.assertRefused(compose({"Acceptance checks": "* python a.py"}),
                           "Acceptance checks", "not a bullet")

    def test_rejection_control_empty_bullet(self):
        self.assertRefused(compose({"Acceptance checks": "- python a.py\n-"}),
                           "Acceptance checks", "empty command")

    def test_rejection_control_operator(self):
        self.assertRefused(compose({"Acceptance checks": "- python a.py && echo ok"}),
                           "Acceptance checks", "unquoted shell operator `&&`")


class CommandTests(unittest.TestCase):
    def refused(self, command, fragment):
        reasons = brief.check_command(command)
        self.assertTrue(any(fragment in reason for reason in reasons),
                        f"{command!r} -> {reasons}")

    def test_positive_control_single_commands(self):
        for command in ("python a.py --flag value", "git log -1 --format=%H",
                        "pwsh -File check.ps1", "powershell -NoProfile -File check.ps1",
                        "bash script.sh", "cmd /d", "python -m unittest"):
            with self.subTest(command=command):
                self.assertEqual(brief.check_command(command), [])

    def test_negative_control_operators_inside_quotes_are_data(self):
        for command in ("grep 'a|b' file.txt", 'echo "x; y && z > w"',
                        "printf 'a<b'", "grep a\\|b file.txt"):
            with self.subTest(command=command):
                self.assertEqual(brief.check_command(command), [])

    def test_rejection_control_each_operator(self):
        for command, operator in (("a ; b", ";"), ("a && b", "&&"), ("a || b", "||"),
                                  ("a | b", "|"), ("a &", "&"), ("a > out", ">"),
                                  ("a < in", "<"), ("a 2>&1", ">&"), ("a>>log", ">>")):
            with self.subTest(command=command):
                self.refused(command, f"`{operator}`")

    def test_rejection_control_substitution(self):
        self.refused("echo $(whoami)", "command substitution")
        self.refused("echo `whoami`", "command substitution")
        self.refused("echo '$(quoted)'", "command substitution")

    def test_rejection_control_unbalanced_quote(self):
        for command in ("echo 'open", 'echo "open', "echo trailing\\"):
            with self.subTest(command=command):
                self.refused(command, "unbalanced quote")

    def test_rejection_control_line_break(self):
        self.refused("echo a\nb", "line break")

    def test_rejection_control_empty(self):
        self.assertEqual(brief.check_command("   "), ["empty command"])

    def test_rejection_control_posix_shell_wrappers(self):
        for command in ("sh -c 'a'", "bash -c 'a'", "dash -c a", "zsh -c a", "ksh -c a",
                        "fish -c a", "BASH.EXE -c a", "/bin/bash -c a", "bash -lc 'a'",
                        "bash -ec a", "fish --command a", "fish --command=a",
                        "C:\\Git\\bin\\bash.exe -c a"):
            with self.subTest(command=command):
                self.refused(command, "runs a command string")

    def test_rejection_control_cmd_wrapper(self):
        for command in ("cmd /c dir", "cmd /k dir", "CMD.exe /C dir", "cmd /d /c dir"):
            with self.subTest(command=command):
                self.refused(command, "runs a command string")

    def test_rejection_control_powershell_wrappers(self):
        for command in ("powershell -Command Get-Date", "powershell -c Get-Date",
                        "pwsh -EncodedCommand AAAA", "pwsh -e AAAA", "pwsh -enc AAAA",
                        "pwsh -ec AAAA", "pwsh -Com Get-Date", "pwsh /Command Get-Date",
                        "PWSH.EXE -command Get-Date", "pwsh -CommandWithArgs x",
                        "pwsh -cwa x", "pwsh --command Get-Date"):
            with self.subTest(command=command):
                self.refused(command, "runs a command string")

    def test_rejection_control_windows_powershell_bare_argument(self):
        self.refused("powershell Get-Date", "as a command string; use `-File`")

    def test_negative_control_powershell_parameters_that_share_a_letter(self):
        for command in ("pwsh -ExecutionPolicy Bypass -File a.ps1",
                        "pwsh -ConfigurationName x -File a.ps1", "pwsh -NoLogo a.ps1"):
            with self.subTest(command=command):
                self.assertEqual(brief.check_command(command), [])

    def test_negative_control_wrapper_name_inside_quoted_data(self):
        self.assertEqual(brief.check_command('grep "bash -c" notes.txt'), [])
        self.assertEqual(brief.check_command("grep 'C:\\bin\\sh -c' notes.txt"), [])

    def test_negative_control_shell_name_as_data_or_script_argument(self):
        for command in ("rg bash -c README.md", "grep -c bash notes.txt",
                        "bash scripts/check.sh -c", "sh -e scripts/check.sh -c x",
                        "bash -C scripts/check.sh", "bash -- scripts/check.sh -c",
                        "pwsh -File a.ps1 -Command x", "powershell -File a.ps1 extra"):
            with self.subTest(command=command):
                self.assertEqual(brief.check_command(command), [])

    def test_rejection_control_command_option_after_valued_option(self):
        for command in ("bash -o pipefail -c 'a'", "bash --rcfile x -c a",
                        "fish -C 'a' script.fish", "fish --init-command=a"):
            with self.subTest(command=command):
                self.refused(command, "runs a command string")

    def test_rejection_control_every_launcher(self):
        for launcher in sorted(brief._LAUNCHERS):
            with self.subTest(launcher=launcher):
                self.refused(f"{launcher} python a.py",
                             f"`{launcher}` launches another program")

    def test_rejection_control_launcher_hiding_a_wrapper(self):
        # R2-2: `env -S` splits one argument into a whole command.
        for command in ('env -S "bash -c a"', "env --split-string='bash -c a'",
                        "env bash -c 'a'", "sudo -u x bash -c a", "timeout 10 sh -c a",
                        "xargs -n1 sh -c a", "/usr/bin/env bash -c a", "SUDO.EXE bash -c a",
                        "C:\\Windows\\System32\\runas.exe /user:x cmd"):
            with self.subTest(command=command):
                self.refused(command, "launches another program")

    def test_negative_control_launcher_name_as_data(self):
        # R2-1: only the program is examined, so a launcher or shell name later in
        # the arguments is data.
        for command in ("rg bash -c README.md", "rg env -S README.md",
                        "grep sudo notes.txt", "python a.py --timeout 10 env",
                        "git log --format=%H -- time"):
            with self.subTest(command=command):
                self.assertEqual(brief.check_command(command), [])

    def test_negative_control_other_programs_with_c_flag(self):
        self.assertEqual(brief.check_command("git -c core.x=1 status"), [])
        self.assertEqual(brief.check_command("python -c pass"), [])


class EditPathTests(BriefCase):
    def edit(self, body):
        return compose({"Edit paths": body})

    def test_positive_control_file_and_folder(self):
        self.assertPasses(self.edit(
            "- workflows/x/ | Measure: git ls-files workflows/x\n"
            "- README.md | Measure: grep -n Workflows README.md\n"
            "- new/file.py | Measure: git ls-files new"))

    def test_positive_control_folder_and_file_beneath_it_are_not_duplicates(self):
        self.assertPasses(self.edit("- a/ | Measure: ls a\n- a/b.py | Measure: ls a"))

    def test_rejection_control_unmeasured(self):
        self.assertRefused(self.edit("- workflows/x/"), "Edit paths", "no measuring command")

    def test_rejection_control_empty_measure(self):
        self.assertRefused(self.edit("- workflows/x/ | Measure:"), "Edit paths",
                           "measuring command empty command")

    def test_rejection_control_measuring_command_is_one_command(self):
        self.assertRefused(self.edit("- a/ | Measure: grep -r x a | wc -l"), "Edit paths",
                           "measuring command contains the unquoted shell operator `|`")

    def test_negative_control_quoted_pipe_in_measure(self):
        self.assertPasses(self.edit("- a/ | Measure: grep -r 'x|y' a"))

    def test_rejection_control_empty_path(self):
        self.assertRefused(self.edit("- | Measure: ls"), "Edit paths", "empty path")

    def test_rejection_control_path_rules(self):
        cases = {
            "a/*.py": "wildcard", "a/?.py": "wildcard", "a/[ab].py": "wildcard",
            "a/../b": "contains `..`", "C:/x": "not project-relative",
            "/x": "not project-relative", "a\\b": "backslash",
            ".git/hooks": "under `.git/`", ".GIT/": "under `.git/`",
            "sub/.git/x": "under `.git/`", ".git./x": "under `.git/`",
            ".env": "`.env` file", ".ENV.local": "`.env` file", "a/.env.example": "`.env` file",
            "a//b": "empty or `.` component", "./a": "empty or `.` component",
        }
        for path, fragment in cases.items():
            with self.subTest(path=path):
                self.assertRefused(self.edit(f"- {path} | Measure: ls"), "Edit paths", fragment)

    def test_negative_control_names_that_resemble_protected_ones(self):
        self.assertPasses(self.edit("- .github/x.yml | Measure: ls .github\n"
                                    "- docs/.environment | Measure: ls docs\n"
                                    "- a..b.txt | Measure: ls"))

    def test_rejection_control_duplicates(self):
        for second in ("A/B.py", "a/b.py.", "a/b.py"):
            with self.subTest(second=second):
                self.assertRefused(self.edit(f"- a/b.py | Measure: ls\n- {second} | Measure: ls"),
                                   "Edit paths", "duplicates line")

    def test_rejection_control_folder_and_bare_name_are_duplicates(self):
        self.assertRefused(self.edit("- docs/ | Measure: ls\n- docs | Measure: ls"),
                           "Edit paths", "duplicates line")


class OpenQuestionTests(BriefCase):
    def test_positive_control_none_and_answered(self):
        self.assertPasses(compose({"Open questions": "None"}))
        self.assertPasses(compose({"Open questions":
                                   "- Which branch? | Answer: feature/x\n- Why? | Answer: Speed."}))

    def test_rejection_control_unanswered(self):
        self.assertRefused(compose({"Open questions": "- Which branch?"}),
                           "Open questions", "question has no answer")
        self.assertRefused(compose({"Open questions": "- Which branch? | Answer:"}),
                           "Open questions", "question has no answer")

    def test_rejection_control_empty_question(self):
        self.assertRefused(compose({"Open questions": "- | Answer: yes"}),
                           "Open questions", "empty question")

    def test_rejection_control_not_a_bullet(self):
        self.assertRefused(compose({"Open questions": "Which branch? | Answer: x"}),
                           "Open questions", "not a bullet")

    def test_rejection_control_none_with_questions(self):
        self.assertRefused(compose({"Open questions": "None\n- Why? | Answer: x"}),
                           "Open questions", "not a bullet")

    def test_rejection_control_lowercase_none(self):
        self.assertRefused(compose({"Open questions": "none"}), "Open questions",
                           "not a bullet")


class SourceTests(BriefCase):
    def source(self, body):
        return compose({"Source": body})

    def test_positive_control_existing_file_folder_and_plain_text(self):
        self.assertPasses(self.source("- `docs/plan.md` sections 3 and 10\n- `docs/`\n"
                                      "- `memory` and `run.py`\n- The backlog item title"))

    def test_rejection_control_missing_file(self):
        self.assertRefused(self.source("- `docs/missing.md`"), "Source",
                           "`docs/missing.md` does not exist")

    def test_rejection_control_case_mismatch(self):
        self.assertRefused(self.source("- `Docs/plan.md`"), "Source", "does not exist")

    def test_rejection_control_trailing_slash_on_a_file(self):
        self.assertRefused(self.source("- `docs/plan.md/`"), "Source", "does not exist")

    def test_rejection_control_markdown_name_in_backticks_must_exist(self):
        self.assertRefused(self.source("- `other.md`"), "Source", "`other.md` does not exist")
        self.assertRefused(self.source("- `OTHER.MD`"), "Source", "does not exist")

    def test_rejection_control_unquoted_paths(self):
        for body in ("- docs/plan.md", "- see plan.md.", "- (docs/plan.md)",
                     "- https://example.com/x", "- [[memory/backlog]]", "- and/or"):
            with self.subTest(body=body):
                self.assertRefused(self.source(body), "Source", "put it in backticks")

    def test_rejection_control_escaping_tokens(self):
        for token, fragment in (("/etc/passwd", "not project-relative"),
                                ("C:/x.md", "not project-relative"),
                                ("~/x.md", "not project-relative"),
                                ("../x.md", "contains `..`"),
                                ("docs\\plan.md", "backslash")):
            with self.subTest(token=token):
                self.assertRefused(self.source(f"- `{token}`"), "Source", fragment)

    def test_rejection_control_unbalanced_backtick(self):
        self.assertRefused(self.source("- `docs/plan.md"), "Source", "unbalanced backtick")

    def test_rejection_control_not_a_bullet(self):
        self.assertRefused(self.source("`docs/plan.md`"), "Source", "not a bullet")

    def test_rejection_control_empty_bullet(self):
        self.assertRefused(self.source("- `docs/`\n-"), "Source", "empty bullet")


class OutputTests(BriefCase):
    def test_every_problem_reported_in_order(self):
        text = compose({"Goal": "", "Constraints": None,
                        "Acceptance checks": "- a ; b\n- c | d",
                        "Source": "- `nope.md`"}) + "\n## Extra\nx\n"
        lines, code = self.check(text)
        self.assertEqual(code, 1)
        self.assertEqual([line.split(":")[1].strip() for line in lines],
                         ["Headings", "Headings", "Goal", "Acceptance checks",
                          "Acceptance checks", "Source"])
        self.assertIn("unexpected heading `## Extra`", lines[0])
        self.assertIn("missing `## Constraints`", lines[1])
        self.assertTrue(lines[3].startswith("FAIL: Acceptance checks: line "))

    def test_section_lines_follow_document_order_when_out_of_order(self):
        order = list(brief.HEADINGS)
        order.remove("Source")
        order.insert(0, "Source")
        text = compose({"Source": "- `nope.md`", "Goal": ""}, order=order)
        lines, _ = self.check(text)
        sections = [line.split(":")[1].strip() for line in lines if "Headings" not in line]
        self.assertEqual(sections, ["Source", "Goal"])

    def test_brief_lines_come_first(self):
        lines, _ = self.check(b"\xef\xbb\xbf" + compose({"Goal": None}).encode("utf-8"))
        self.assertTrue(lines[0].startswith("FAIL: Brief: "))
        self.assertTrue(lines[1].startswith("FAIL: Headings: "))


class TemplateTests(unittest.TestCase):
    def test_rejection_control_shipped_template_is_refused_only_for_emptiness(self):
        lines, code = brief.check_brief(TEMPLATE, PROJECT_ROOT)
        self.assertEqual(code, 1)
        expected = [f"FAIL: {title}: section is empty"
                    for title in brief.HEADINGS if title != "Open questions"]
        self.assertEqual(lines, expected)


class CommandLineTests(BriefCase):
    def invoke(self, entry, argv):
        out, err = io.StringIO(), io.StringIO()
        log = self.root / "LOG.md"
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = entry(argv, root=self.root, log_path=log)
        text = log.read_text(encoding="utf-8") if log.exists() else ""
        return code, out.getvalue(), err.getvalue(), text

    def test_pass_logs_started_and_completed_without_the_path(self):
        name = self.write(compose(), "runs/secret-name/brief.md")
        code, out, _, log = self.invoke(brief.main, ["--check", name])
        self.assertEqual((code, out), (0, f"PASS: {name}\n"))
        self.assertIn("Action: started", log)
        self.assertIn("Action: completed", log)
        self.assertNotIn("secret-name", log)
        self.assertEqual(len(log.splitlines()), 2)

    def test_refusal_logs_failed(self):
        name = self.write(compose({"Goal": ""}))
        code, out, _, log = self.invoke(brief.main, ["--check", name])
        self.assertEqual((code, out), (1, "FAIL: Goal: section is empty\n"))
        self.assertIn("Action: failed", log.splitlines()[-1])

    def test_dry_run_writes_nothing_and_keeps_stdout_clean(self):
        name = self.write(compose())
        code, out, err, log = self.invoke(brief.main, ["--check", name, "--dry-run"])
        self.assertEqual((code, out, log), (0, f"PASS: {name}\n", ""))
        self.assertEqual(len([line for line in err.splitlines()
                              if line.startswith("[DRY RUN] ")]), 2)

    def test_run_check_brief_returns_the_checker_result_unchanged(self):
        for body in (compose(), compose({"Goal": "", "Source": "- x/y"})):
            with self.subTest(body=body[:20]):
                name = self.write(body)
                direct = self.invoke(brief.main, ["--check", name, "--dry-run"])
                routed = self.invoke(run.main, ["--check-brief", name, "--dry-run"])
                self.assertEqual(direct[:2], routed[:2])

    def test_rejection_control_unwritable_log_fails_a_passing_brief(self):
        name = self.write(compose())
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = brief.main(["--check", name], root=self.root, log_path=self.root)
        self.assertEqual(code, 1)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("FAIL: Brief: could not append to the workflow "
                                            "LOG.md"), lines)

    def test_rejection_control_unwritable_log_comes_first_on_a_refused_brief(self):
        name = self.write(compose({"Goal": ""}))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = brief.main(["--check", name], root=self.root, log_path=self.root)
        lines = out.getvalue().splitlines()
        self.assertEqual(code, 1)
        self.assertTrue(lines[0].startswith("FAIL: Brief: could not append"))
        self.assertEqual(lines[1:], ["FAIL: Goal: section is empty"])

    def test_unexpected_error_is_logged_then_raised(self):
        name = self.write(compose())
        log = self.root / "LOG.md"
        original = brief.check_brief
        brief.check_brief = lambda *args: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            with self.assertRaises(RuntimeError):
                brief.run_check(name, root=self.root, log_path=log)
        finally:
            brief.check_brief = original
        self.assertIn("Action: failed | Note: Brief check stopped on an unexpected error "
                      "(RuntimeError).", log.read_text(encoding="utf-8"))

    def test_usage_error_exits_2(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                run.main([], root=self.root, log_path=self.root / "LOG.md")
        self.assertEqual(caught.exception.code, 2)


class SmokeTests(unittest.TestCase):
    """The real scripts as subprocesses, always with --dry-run."""

    def call(self, script, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / script), *args], cwd=PROJECT_ROOT,
            capture_output=True, text=True, encoding="utf-8")

    def test_both_entry_points_refuse_the_template_identically(self):
        direct = self.call("brief.py", "--check", TEMPLATE, "--dry-run")
        routed = self.call("run.py", "--check-brief", TEMPLATE, "--dry-run")
        self.assertEqual(direct.returncode, 1)
        self.assertEqual((direct.returncode, direct.stdout), (routed.returncode, routed.stdout))
        self.assertTrue(all(line.startswith("FAIL: ") for line in direct.stdout.splitlines()))
        self.assertIn("[DRY RUN] ", routed.stderr)

    def test_no_mode_is_a_usage_error(self):
        self.assertEqual(self.call("run.py").returncode, 2)


if __name__ == "__main__":
    unittest.main()
