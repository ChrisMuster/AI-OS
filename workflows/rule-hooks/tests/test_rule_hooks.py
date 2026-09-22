#!/usr/bin/env python3
"""Synthetic-event tests for the rule-hooks evaluator.

Two layers (HOOKS-PLAN Task 8):
  - rule logic via the in-process Context -> evaluate pipeline (fast, the bulk);
  - the per-AI adapters' input parsing and output block-contract, including a
    subprocess check that the right process exit code / output shape is emitted.

Every rule has a must-block (positive) case AND must-allow (negative) cases -
the false-positive guards. Destructive cases use safe synthetic strings only;
nothing here runs a real destructive command.

Run: python workflows/rule-hooks/tests/test_rule_hooks.py
"""

import ast
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
PROJECT_ROOT = SCRIPTS.parent.parent.parent
RUN_PY = SCRIPTS / "run.py"
CODEX_CONFIG = PROJECT_ROOT / ".codex" / "config.toml"
sys.path.insert(0, str(SCRIPTS))

# A guard-detectable email, assembled at runtime so the literal address never
# appears in this file's source. This file is itself scanned by
# personal-data-guard and the git pre-commit gate; a hardcoded real-looking
# email here would (correctly) trip them and block every commit.
FAKE_EMAIL = "rulehookstester" + "@" + "gmail" + ".com"

import core  # noqa: E402
from core import Context  # noqa: E402
import run as run_mod  # noqa: E402
from adapters import claude as claude_adapter  # noqa: E402
from adapters import codex as codex_adapter  # noqa: E402

# ---------------------------------------------------------------------------
# Fire-log containment. This block must sit above every test in the file.
# ---------------------------------------------------------------------------
# The suite must never append to the real fire-log. That store is read as
# evidence for decisions such as promoting a trial rule to blocking, and a
# fixture row is indistinguishable from a genuine fire, so contaminating it
# corrupts the evidence rather than merely adding noise. Measured on 2026-09-21,
# before this containment existed: 15,827 rows, of which 2,096 were the A6
# fixture `rm -rf x` and 1,092 the A7 fixture `cp secrets.txt .env`.
#
# TWO channels have to be closed, and closing one looks exactly like closing
# both, which is why this went unnoticed for three months:
#   * in-process - a test calls evaluate / run_ai / main / _precommit_doc_sync,
#     so the module constant is repointed here;
#   * subprocess - a test launches run.py as its own program, which re-resolves
#     that constant in its own memory, so a monkeypatch cannot reach it. run.py
#     reads BOOK_DRAGON_FIRE_LOG at import, and setting it in os.environ means
#     every child inherits it (including the one place that builds its own env,
#     which spreads os.environ).
#
# Done once at module level rather than per test, deliberately: a per-test
# redirect is a rule every future test author has to remember, and the test that
# forgets is the test that writes.
REAL_FIRE_LOG = SCRIPTS.parent / "fire-log.jsonl"
FIRE_LOG_SINK = Path(tempfile.mkdtemp(prefix="rule-hooks-fire-log-")) / "fire-log.jsonl"
run_mod.FIRE_LOG = FIRE_LOG_SINK
os.environ["BOOK_DRAGON_FIRE_LOG"] = str(FIRE_LOG_SINK)

# Recorded before any test runs so tearDownModule can prove the real store did
# not move. This is the runtime half of the guarantee and it observes what the
# source-level check cannot: the source check proves the redirects are written,
# this proves they worked.
_REAL_FIRE_LOG_SIZE_AT_IMPORT = (
    REAL_FIRE_LOG.stat().st_size if REAL_FIRE_LOG.exists() else None)


def tearDownModule():
    """Fail the run if anything in it appended to the real fire-log."""
    after = REAL_FIRE_LOG.stat().st_size if REAL_FIRE_LOG.exists() else None
    if after != _REAL_FIRE_LOG_SIZE_AT_IMPORT:
        raise AssertionError(
            "the suite wrote to the real fire-log at {}: {} -> {} bytes. Both "
            "redirect channels must be in place - see the fire-log containment "
            "block at the top of this file.".format(
                REAL_FIRE_LOG, _REAL_FIRE_LOG_SIZE_AT_IMPORT, after))


def shell_ctx(command, ai="claude"):
    return Context(ai_id=ai, category="shell", tool_name="Bash",
                   command=command, project_root=PROJECT_ROOT)


def write_ctx(file_path, content, ai="claude"):
    return Context(ai_id=ai, category="write", tool_name="Write",
                   file_path=file_path, content=content, project_root=PROJECT_ROOT)


def decision_for(ctx):
    block, warns = run_mod.evaluate(ctx)
    return block, warns


def a9_permits(mode):
    """A9's write-mode predicate, reached without importing the rule by path."""
    from rules.interpreter_write import _permits_writing
    return _permits_writing(mode)


def a9_splits_a_literal(text):
    """A9's adjacent-string-literal predicate, reached the same way."""
    from rules.interpreter_write import _splits_a_literal
    return _splits_a_literal(text)


def local_module_for(source_dir, name):
    """The file a local import resolves to, or None if it is not local.

    A local module is `name.py` OR a `name/__init__.py` package. Resolving only
    the first left both adapters and the rules registry unreachable, so the
    code that makes the hook fire at all could have been untracked with the
    deliverability check green.
    """
    for candidate in (source_dir / f"{name}.py",
                      source_dir / name / "__init__.py",
                      SCRIPTS / f"{name}.py",
                      SCRIPTS / name / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def imported_names(source):
    """The top-level module names *source* imports.

    A module-level function rather than a closure inside the deliverability
    test, so it can be tested on synthetic source directly. It could not be
    before: that test fails for an unrelated reason (a module awaiting a
    decision to track it), so mutating its import reader changed nothing
    observable and every improvement to it was unverifiable.

    **What this half is actually for, stated accurately.** An earlier note
    claimed the adapters could have been untracked with the check green. They
    could not: the check's second loop requires every `.py` under `scripts/` to
    be in the index, and that alone catches an untracked module. Since
    `local_module_for` only ever returns paths under `scripts/`, this half can
    never produce a detection the second loop misses. It earns its place by
    naming the *importer* in the failure message, which is what tells a reader
    why the missing file matters, and by extending to an import that resolves
    outside `scripts/` if one is ever added.
    """
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                # `from .alpha import thing` names the module alpha. `thing` is
                # a name inside it, not a module, and adding it too made this
                # reader claim an import that does not exist.
                names.add(node.module.split(".")[0])
            elif node.level:  # `from . import alpha` names alpha itself
                names.update(alias.name for alias in node.names)
    return names


class TestA7Env(unittest.TestCase):
    def test_block_env(self):
        block, _ = decision_for(write_ctx(".env", "X=1"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A7")

    def test_block_env_suffix(self):
        block, _ = decision_for(write_ctx("config/.env.production", "X=1"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A7")

    def test_allow_env_example(self):
        block, _ = decision_for(write_ctx(".env.example", "X="))
        self.assertIsNone(block)

    def test_allow_normal_file(self):
        block, _ = decision_for(write_ctx("app.py", "print(1)"))
        self.assertIsNone(block)

    def test_block_env_redirect(self):
        block, _ = decision_for(shell_ctx("printf 'X=1\\n' > .env"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A7")

    def test_block_env_suffix_append_redirect(self):
        block, _ = decision_for(shell_ctx("echo X=1 >> config/.env.production"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A7")

    def test_allow_env_example_redirect(self):
        block, _ = decision_for(shell_ctx("echo X= > .env.example"))
        self.assertIsNone(block)

    def test_allow_env_mention_in_quotes(self):
        # A quoted mention with no real redirect must not trip A7.
        block, _ = decision_for(shell_ctx('echo "writing > .env is banned"'))
        self.assertIsNone(block)

    def test_block_env_cp(self):
        block, _ = decision_for(shell_ctx("cp secrets.txt .env"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A7")

    def test_block_env_mv(self):
        block, _ = decision_for(shell_ctx("mv staged.txt config/.env.production"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A7")

    def test_block_env_tee(self):
        block, _ = decision_for(shell_ctx("echo X=1 | tee .env"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A7")

    def test_allow_env_as_cp_source(self):
        # Copying .env to a non-.env destination is a read, not a rewrite.
        block, _ = decision_for(shell_ctx("cp .env env_snapshot.txt"))
        self.assertIsNone(block)

    def test_block_env_sed_inplace(self):
        block, _ = decision_for(shell_ctx("sed -i 's/A=1/A=2/' .env"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A7")

    def test_block_env_sed_inplace_backup_suffix(self):
        block, _ = decision_for(shell_ctx("sed -i.bak 's/A=1/A=2/' .env"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A7")

    def test_block_env_ampersand_redirect(self):
        block, _ = decision_for(shell_ctx("printf 'X=1\\n' &> .env"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A7")

    def test_block_env_cp_target_directory(self):
        block, _ = decision_for(shell_ctx("cp --target-directory=cfg .env"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A7")

    def test_allow_sed_mentions_env_in_script(self):
        # .env only inside the sed script, no -i: a transform, not a rewrite.
        block, _ = decision_for(shell_ctx("sed 's/x/.env/' notes.txt"))
        self.assertIsNone(block)

    def test_allow_sed_inplace_other_file(self):
        block, _ = decision_for(shell_ctx("sed -i 's/a/b/' notes.txt"))
        self.assertIsNone(block)


class TestA4LogRedirect(unittest.TestCase):
    def test_block_append_logmd(self):
        block, _ = decision_for(shell_ctx("echo hi >> workflows/audit/LOG.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A4")

    def test_block_overwrite_logmd(self):
        block, _ = decision_for(shell_ctx("echo hi > LOG.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A4")

    def test_allow_redirect_other_file(self):
        block, _ = decision_for(shell_ctx("echo hi >> notes.txt"))
        self.assertIsNone(block)

    def test_allow_mention_in_quotes(self):
        # A quoted mention with no real redirect must not trip A4.
        block, _ = decision_for(shell_ctx('echo "writing >> LOG.md is banned"'))
        self.assertIsNone(block)

    def test_block_cp_logmd(self):
        block, _ = decision_for(shell_ctx("cp notes.txt workflows/audit/LOG.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A4")

    def test_block_tee_logmd(self):
        block, _ = decision_for(shell_ctx("echo hi | tee LOG.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A4")

    def test_block_ampersand_redirect_logmd(self):
        block, _ = decision_for(shell_ctx("echo hi &> LOG.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A4")


class TestA6DangerousBash(unittest.TestCase):
    def test_block_rm_rf(self):
        for cmd in ("rm -rf build", "rm -fr build", "rm -r -f build",
                    "rm --recursive --force build"):
            block, _ = decision_for(shell_ctx(cmd))
            self.assertIsNotNone(block, cmd)
            self.assertEqual(block.rule, "A6", cmd)

    def test_block_git_force_push(self):
        for cmd in ("git push --force origin main", "git push -f origin main"):
            block, _ = decision_for(shell_ctx(cmd))
            self.assertIsNotNone(block, cmd)
            self.assertEqual(block.rule, "A6")

    def test_block_git_reset_hard_and_clean(self):
        self.assertIsNotNone(decision_for(shell_ctx("git reset --hard HEAD~1"))[0])
        self.assertIsNotNone(decision_for(shell_ctx("git clean -fdx"))[0])

    def test_block_destructive_sql(self):
        block, _ = decision_for(shell_ctx('sqlite3 db.sqlite "DROP TABLE users"'))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A6")

    def test_allow_force_with_lease(self):
        block, _ = decision_for(shell_ctx("git push --force-with-lease origin main"))
        self.assertIsNone(block)

    def test_allow_echo_mentioning_rm(self):
        # PARSE, do not substring-match: a quoted mention is not a command.
        block, _ = decision_for(shell_ctx('echo "never run rm -rf here"'))
        self.assertIsNone(block)

    def test_allow_plain_rm(self):
        block, _ = decision_for(shell_ctx("rm temp.txt"))
        self.assertIsNone(block)

    def test_allow_git_status(self):
        block, _ = decision_for(shell_ctx("git status"))
        self.assertIsNone(block)


class TestB3PersonalData(unittest.TestCase):
    def test_block_email_in_committable(self):
        block, _ = decision_for(
            write_ctx("zz_rulehooks_sample.py", f"OWNER='{FAKE_EMAIL}'"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "B3")

    def test_allow_email_in_gitignored(self):
        # USER.md is gitignored - personal data belongs there, do not block.
        block, _ = decision_for(
            write_ctx("USER.md", f"email {FAKE_EMAIL}"))
        self.assertIsNone(block)

    def test_allow_clean_content(self):
        block, _ = decision_for(
            write_ctx("zz_rulehooks_sample.py", "print('hello world')"))
        self.assertIsNone(block)


class TestA2TrialWarnOnly(unittest.TestCase):
    def test_a2_backslash_warns_not_blocks(self):
        block, warns = decision_for(shell_ctx("python workflows\\audit\\run.py"))
        self.assertIsNone(block)
        self.assertTrue(any(w.rule == "A2" for w in warns))


class TestA3DedicatedTools(unittest.TestCase):
    """A3, blocking since 2026-09-02.

    **The load-bearing controls are the must-allow ones**, and that is not the
    usual shape. A3 spent its trial period unable to distinguish a shell
    file-read from a filter on a command's output, and promoting it without
    that distinction would have refused work with no compliant alternative,
    which is how a hook becomes something to route around. So the pair that
    matters is `test_blocks_grep_reading_a_file` against
    `test_allows_grep_after_a_pipe`: same program, opposite verdicts, and a rule
    that got either one wrong would be worse than the trial it replaced.

    **What actually separates them is the file operand, not the pipe.** The
    first implementation exempted anything sitting after a pipe. A mutation
    removing that exemption broke no test, which showed the suite was not
    measuring it - and inspecting why exposed that the exemption was wrong:
    `something | cat README.md` reads the file and ignores stdin, so a
    pipe-based exemption waves through a real file read.
    `test_blocks_cat_naming_a_file_even_after_a_pipe` is the control that
    pins the correct behaviour and would have failed under the first design.
    """

    # ---- must block: a file is named and a dedicated tool exists
    def test_blocks_grep_reading_a_file(self):
        block, _ = decision_for(shell_ctx("grep -n pattern workflows/audit/CONTEXT.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")
        self.assertIn("Grep", block.reason)

    def test_blocks_cat(self):
        block, _ = decision_for(shell_ctx("cat workflows/audit/CONTEXT.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_blocks_head_with_a_numeric_flag_before_the_file(self):
        """`-30` must not be mistaken for the file operand."""
        block, _ = decision_for(shell_ctx("head -30 README.md"))
        self.assertIsNotNone(block)
        self.assertIn("README.md", block.reason)

    def test_blocks_sed_addressing_a_file(self):
        """sed's first operand is the script, not a file; the file is second."""
        block, _ = decision_for(shell_ctx("sed -n '56p' workflows/audit/CONTEXT.md"))
        self.assertIsNotNone(block)
        self.assertIn("workflows/audit/CONTEXT.md", block.reason)

    def test_warns_but_does_not_block_echo_redirected_into_a_file(self):
        """The blocking half of A3 is the read half, where the dedicated tool
        is unambiguous. Shell writes stay a warn here: A4, A7 and A9 cover the
        cases that actually cause harm, and a redirect legitimately creates
        scratch files that this rule cannot tell from a document edit."""
        block, warns = decision_for(shell_ctx("echo hi > notes.txt"))
        self.assertIsNone(block)
        self.assertTrue(any(w.rule == "A3" for w in warns))

    def test_negative_control_a_later_line_is_not_the_greps_operand(self):
        """Found live, blocking a real command. shlex is whitespace-splitting,
        so it never emits a newline as a token and split_segments cannot break
        on one: a multi-line script merged into a single segment, and a piped
        `grep` on line one collected the filename operand of a `cp` on line
        two. The grep here names no file and must be allowed."""
        command = (
            "python tests/test_x.py | grep -E '^Ran'\n"
            "cp /tmp/backup.orig workflows/rule-hooks/scripts/rules/shell_style.py"
        )
        block, _ = decision_for(shell_ctx(command))
        self.assertIsNone(block)

    def test_a_file_read_on_a_later_line_is_still_caught(self):
        """The other direction of the same fix: splitting by line must not lose
        a violation that happens not to be on the first line."""
        block, _ = decision_for(shell_ctx("echo starting\ncat README.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_negative_control_a_named_file_that_does_not_exist_is_allowed(self):
        """Requiring the operand to exist is what stops stray shell tokens - a
        heredoc terminator, a fragment of a quoted body - reading as filenames
        and blocking a command that names no file at all."""
        block, _ = decision_for(shell_ctx("grep pattern no/such/file.md"))
        self.assertIsNone(block)

    # ---- must allow: no file is being read, so no dedicated tool applies
    def test_allows_grep_after_a_pipe(self):
        """THE control. Filtering a command's output has no tool equivalent:
        Read and Grep read files, and this output never exists on disk. It
        passes because no file is named, which is the property the rule tests."""
        block, warns = decision_for(
            shell_ctx("python workflows/audit/scripts/run.py | grep -E '^Ran'"))
        self.assertIsNone(block)
        self.assertFalse(any(w.rule == "A3" for w in warns))

    def test_blocks_cat_naming_a_file_even_after_a_pipe(self):
        """A pipe does not make it a filter. `cat` given a file operand ignores
        stdin and reads the file, so this is a file read and must block. An
        earlier design exempted everything after a pipe and would have allowed
        it; this control is what makes that design impossible to reintroduce."""
        block, _ = decision_for(shell_ctx("echo hi | cat README.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_allows_tail_after_a_pipe(self):
        block, _ = decision_for(shell_ctx("python tests/test_x.py 2>&1 | tail -4"))
        self.assertIsNone(block)

    def test_allows_a_dedicated_tool_name_late_in_a_pipeline(self):
        block, _ = decision_for(shell_ctx("git log --oneline | head -5 | cat"))
        self.assertIsNone(block)

    def test_allows_grep_reading_stdin_with_no_file(self):
        block, _ = decision_for(shell_ctx("grep pattern"))
        self.assertIsNone(block)

    def test_negative_control_a_command_after_a_semicolon_is_not_piped(self):
        """A `;` is not a pipe. The second command reads a file and must block,
        or the pipe test would be satisfied by any separator at all."""
        block, _ = decision_for(shell_ctx("echo start ; grep x README.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_negative_control_an_unrelated_program_is_untouched(self):
        block, warns = decision_for(shell_ctx("python workflows/audit/scripts/run.py"))
        self.assertIsNone(block)
        self.assertFalse(any(w.rule == "A3" for w in warns))

    def test_allows_echo_to_dev_null(self):
        block, _ = decision_for(shell_ctx("echo hi > /dev/null"))
        self.assertIsNone(block)

    # ---- the byte-level allowance
    def test_byte_level_pattern_is_allowed_and_warned(self):
        """Counting CR bytes is a check the review ledger requires and the
        dedicated tools cannot perform. Allowed - and warned, so the fire-log
        shows whether the allowance is being leaned on."""
        block, warns = decision_for(shell_ctx("grep -c $'\\r' README.md"))
        self.assertIsNone(block)
        self.assertTrue(any(w.rule == "A3" and "allowance" in w.reason
                            for w in warns))

    def test_rejection_control_an_ordinary_pattern_is_not_byte_level(self):
        """The allowance must not swallow the rule: a normal search still blocks."""
        block, _ = decision_for(shell_ctx("grep -c pattern README.md"))
        self.assertIsNotNone(block)

    # ---- the three underblocking gaps found by review on 2026-09-04.
    # Each pairs the gap with the control that must NOT move, because every one
    # of these fixes widens what the rule catches and the rule was narrowed to
    # its current shape precisely to stop it refusing legitimate work.
    def test_a_read_after_cd_blocks(self):
        """`cd` changed where the read resolves and the rule kept resolving
        against the project root, so a real file looked imaginary."""
        block, _ = decision_for(shell_ctx(
            "cd workflows/audit && grep pattern CONTEXT.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_control_a_read_after_cd_of_an_absent_file_is_allowed(self):
        """The cd fix must not turn the rule into "any operand is a file"."""
        block, _ = decision_for(shell_ctx(
            "cd workflows/audit && grep pattern no-such-file.md"))
        self.assertIsNone(block)

    def test_control_cd_with_no_operand_does_not_guess(self):
        """`cd` alone goes home. The rule must not invent a directory.

        The operand here EXISTS at the project root, which is what makes this a
        real control. It originally named an absent file, so it passed however
        the rule behaved and proved nothing: the "does not guess" claim was
        never tested. With a real root-level file, a rule that quietly kept
        resolving against the project root blocks a command that, having moved
        to a home directory, would never have found it.
        """
        block, _ = decision_for(shell_ctx("cd && grep pattern README.md"))
        self.assertIsNone(block)

    def test_control_cd_dash_does_not_guess(self):
        """`cd -` goes wherever the shell was last, which only the shell knows."""
        block, _ = decision_for(shell_ctx("cd - && grep pattern README.md"))
        self.assertIsNone(block)

    def test_control_a_relative_cd_from_an_unknown_place_stays_unknown(self):
        """Unknown plus a relative move is still unknown, not a fresh start at
        the project root."""
        block, _ = decision_for(shell_ctx(
            "cd && cd workflows && grep pattern audit/CONTEXT.md"))
        self.assertIsNone(block)

    def test_an_absolute_path_does_not_depend_on_the_working_directory(self):
        """The control above must not become "any cd disables the rule"."""
        target = str(PROJECT_ROOT / "README.md").replace("\\", "/")
        block, _ = decision_for(shell_ctx("cd && cat " + target))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    # ---- a `cd` that fails does not move the shell (2026-09-04, round two)
    def test_a_read_after_a_failed_cd_and_a_semicolon_blocks(self):
        """`cd nowhere ; grep p README.md` really does read README.md from
        where it started: the cd reports an error and the shell stays put. The
        rule applied the move regardless, so the read looked like a read of a
        file somewhere else and was allowed."""
        block, _ = decision_for(shell_ctx(
            "cd no-such-dir ; grep pattern README.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_a_read_after_a_failed_cd_and_an_or_blocks(self):
        """`||` runs its right side precisely because the left side failed."""
        block, _ = decision_for(shell_ctx(
            "cd no-such-dir || grep pattern README.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_control_a_failed_cd_stops_an_and_chain(self):
        """The other direction, and the one that matters more: after a failed
        `cd &&` the command never runs, so there is nothing to block. Blocking
        it would refuse work the shell would not even attempt."""
        block, _ = decision_for(shell_ctx(
            "cd no-such-dir && grep pattern ../README.md"))
        self.assertIsNone(block)

    def test_control_a_failed_cd_stops_the_whole_and_chain(self):
        """`cd bad && a && b` runs neither a nor b."""
        block, _ = decision_for(shell_ctx(
            "cd no-such-dir && cd .. && grep pattern README.md"))
        self.assertIsNone(block)

    def test_a_failed_cd_stops_only_its_own_and_chain(self):
        """The control above must not become "one failed cd disables the line"."""
        block, _ = decision_for(shell_ctx(
            "cd no-such-dir ; cd workflows/audit && grep p CONTEXT.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_control_cd_to_a_file_is_a_failed_cd(self):
        """A path that exists but is not a directory does not move the shell."""
        block, _ = decision_for(shell_ctx(
            "cd README.md && grep pattern CONTEXT.md"))
        self.assertIsNone(block)

    def test_control_a_cd_in_a_pipeline_does_not_move_the_shell(self):
        """A pipeline stage runs in a child process, so `cd x | grep y` leaves
        the shell where it was and `y` is looked for there."""
        block, _ = decision_for(shell_ctx(
            "cd workflows/audit | grep pattern CONTEXT.md"))
        self.assertIsNone(block)

    def test_a_cd_inside_a_subshell_applies_within_that_subshell(self):
        """`( cd x && grep p y )` really does read x/y: the subshell moves even
        though the outer shell does not."""
        block, _ = decision_for(shell_ctx(
            "(cd workflows/audit && grep pattern CONTEXT.md)"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_control_a_cd_does_not_escape_its_subshell(self):
        """The pair to the test above: once the subshell closes, its directory
        is gone and the read resolves from the outer shell again."""
        block, _ = decision_for(shell_ctx(
            "(cd workflows/audit) && grep pattern CONTEXT.md"))
        self.assertIsNone(block)

    def test_a_cd_in_a_brace_group_does_move_the_shell(self):
        """`{ ...; }` does not fork, which is why only parenthesis depth is
        counted. Treating braces as a subshell would under-report this read."""
        block, _ = decision_for(shell_ctx(
            "{ cd workflows/audit; grep pattern CONTEXT.md; }"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_chained_cds_accumulate(self):
        block, _ = decision_for(shell_ctx(
            "cd workflows && cd audit && grep pattern CONTEXT.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    # ---- the pattern can arrive by flag (2026-09-04, round two)
    def test_blocks_grep_whose_pattern_came_from_dash_e(self):
        """grep's first plain operand is the pattern and was dropped on that
        basis. Once `-e` supplies the pattern the first operand is already a
        file, and dropping it hid a real read."""
        block, _ = decision_for(shell_ctx("grep -e pattern README.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_blocks_grep_whose_pattern_came_from_long_regexp(self):
        block, _ = decision_for(shell_ctx("grep --regexp=pattern README.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_blocks_grep_whose_pattern_came_from_a_pattern_file(self):
        block, _ = decision_for(shell_ctx(
            "grep -f README.md workflows/audit/CONTEXT.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_blocks_sed_whose_script_came_from_dash_e(self):
        """sed has the identical fault, and it was not in the review."""
        block, _ = decision_for(shell_ctx("sed -e 's/a/b/' README.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_control_a_pattern_flag_with_no_real_file_is_allowed(self):
        """Widening the operand list must not start naming files that are not
        there: the existence test is still what decides."""
        block, _ = decision_for(shell_ctx("grep -e pattern no-such-file.txt"))
        self.assertIsNone(block)

    def test_control_a_piped_grep_with_a_pattern_flag_is_allowed(self):
        block, _ = decision_for(shell_ctx("build | grep -e pattern"))
        self.assertIsNone(block)

    def test_control_a_plain_grep_still_drops_its_pattern_operand(self):
        """The pattern of an ordinary `grep pattern file` must still not be
        mistaken for a file, or a pattern that happens to name one would block
        a command that reads nothing else."""
        block, _ = decision_for(shell_ctx("build | grep README.md"))
        self.assertIsNone(block)

    # ---- find with no path operand (2026-09-04, round two)
    def test_blocks_find_with_no_path_operand(self):
        """`find` has no stdin form: given no path it walks the current
        directory. The rule read "no path operand" as "no file read"."""
        block, _ = decision_for(shell_ctx("find -name README.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_blocks_find_with_no_path_operand_after_flags(self):
        block, _ = decision_for(shell_ctx("find -maxdepth 1 -name README.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_control_find_help_reads_nothing(self):
        """The implicit path must not turn every find invocation into a read."""
        block, _ = decision_for(shell_ctx("find --help"))
        self.assertIsNone(block)
        block, _ = decision_for(shell_ctx("find --version"))
        self.assertIsNone(block)

    # ---- the byte-level marker is read off the tokens (2026-09-04, round two)
    def test_the_allowance_does_not_leak_through_an_escaped_quote(self):
        """The previous fix cut the raw line a second time and fell back to
        judging the whole line whenever the two cuts disagreed. An escaped
        quote made them disagree, and the fallback then let one legitimate CR
        check excuse an ordinary read beside it."""
        block, _ = decision_for(shell_ctx(
            "grep -c $'\\r' README.md && grep \"a \\\"&&\\\" b\" README.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_the_allowance_does_not_leak_through_an_escaped_operator(self):
        block, _ = decision_for(shell_ctx(
            "grep -c $'\\r' README.md && grep a\\&\\&b README.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_an_unquoted_backslash_r_is_the_letter_r_not_a_byte_check(self):
        """Reading the marker off the tokens is not only simpler than reading
        it off the raw text, it is more accurate. The shell turns an unquoted
        `\\r` into the letter r, so this is an ordinary read that the raw-text
        test wrongly allowed."""
        block, _ = decision_for(shell_ctx("grep -c \\r README.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_control_the_quoted_escape_forms_are_still_allowed(self):
        """The allowance itself must survive: these are the reads the review
        ledger requires and the dedicated tools cannot perform."""
        for command in ("grep -c $'\\r' README.md",
                        "grep -c $'\\t' README.md",
                        "grep -P '\\x0d' README.md"):
            with self.subTest(command=command):
                block, warns = decision_for(shell_ctx(command))
                self.assertIsNone(block)
                self.assertTrue(any(w.rule == "A3" and "allowance" in w.reason
                                    for w in warns))

    # ---- grouping tokens no longer hide the program (2026-09-04, round two)
    def test_blocks_a_read_inside_a_subshell(self):
        """`(` was handed to the rules as the program being run, so every rule
        that asks "what command is this?" saw a bracket instead of `cat`."""
        block, _ = decision_for(shell_ctx("echo start && (cat README.md)"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_blocks_a_read_inside_command_substitution(self):
        block, _ = decision_for(shell_ctx("echo $(cat README.md)"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_control_a_stray_closing_bracket_is_not_a_file_operand(self):
        """The decomposition must not leave `)` in an argv, where it would be
        stat-ed as a filename."""
        block, _ = decision_for(shell_ctx("(cat no-such-file.md)"))
        self.assertIsNone(block)

    def test_a_glob_operand_blocks(self):
        """The shell has not expanded the glob yet, so the literal token never
        exists and a plain stat read every globbed read as naming nothing."""
        block, _ = decision_for(shell_ctx("cat workflows/audit/*.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_control_a_glob_matching_nothing_is_allowed(self):
        """A glob that matches no file reads no file."""
        block, _ = decision_for(shell_ctx("cat workflows/audit/*.nosuchext"))
        self.assertIsNone(block)

    def test_byte_level_allowance_does_not_cover_a_later_ordinary_read(self):
        """The allowance was judged once for the whole command, so one
        legitimate CR check excused every other read on the same line."""
        block, _ = decision_for(shell_ctx(
            "grep -c $'\\r' README.md && grep pattern README.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_control_the_allowance_still_holds_for_its_own_segment(self):
        """Narrowing the allowance must not remove it: a byte-level read
        alongside a non-read still warns rather than blocking."""
        block, warns = decision_for(shell_ctx(
            "grep -c $'\\r' README.md && echo done"))
        self.assertIsNone(block)
        self.assertTrue(any(w.rule == "A3" and "allowance" in w.reason
                            for w in warns))

    def test_control_an_operator_inside_quotes_does_not_split_the_line(self):
        """`&&` occurs inside search patterns as ordinary text. Cutting the raw
        line there would pair the wrong halves and judge a segment that never
        runs."""
        block, _ = decision_for(shell_ctx("grep 'a && b' README.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")


class TestA9InterpreterWrite(unittest.TestCase):
    """A9: inline interpreter code writing a project document.

    This is the exact 2026-09-02 incident: three CONTEXT.md appends batched into
    one `python - <<'EOF'` call, which never reached the B3 personal-data check
    because B3 guards the Edit and Write tools, and a personal name went into a
    publicly tracked file.
    """

    HEREDOC = (
        "python - <<'PYEOF'\n"
        "with open('workflows/x/CONTEXT.md', 'a', encoding='utf-8') as fh:\n"
        "    fh.write('entry')\n"
        "PYEOF"
    )

    def test_blocks_a_heredoc_appending_to_a_context_file(self):
        block, _ = decision_for(shell_ctx(self.HEREDOC))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A9")
        self.assertIn("Edit tool", block.reason)

    def test_blocks_python_dash_c_writing_markdown(self):
        block, _ = decision_for(shell_ctx(
            "python -c \"open('README.md','w').write('x')\""))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A9")

    def test_blocks_write_text(self):
        block, _ = decision_for(shell_ctx(
            "python - <<'EOF'\nPath('a/CONTEXT.md').write_text('x')\nEOF"))
        self.assertIsNotNone(block)

    def test_negative_control_reading_markdown_inline_is_allowed(self):
        """Reading is not this rule's business; only a write verb triggers it."""
        block, _ = decision_for(shell_ctx(
            "python - <<'EOF'\nprint(open('README.md').read())\nEOF"))
        self.assertIsNone(block)

    def test_negative_control_writing_a_non_document_is_allowed(self):
        """A script writing a JSON index is doing something Edit is not for."""
        block, _ = decision_for(shell_ctx(
            "python - <<'EOF'\nopen('index.json','w').write('{}')\nEOF"))
        self.assertIsNone(block)

    def test_negative_control_a_saved_script_is_out_of_scope(self):
        """The documented boundary: a saved script's writes cannot be read from
        the command, and this rule does not pretend otherwise."""
        block, _ = decision_for(shell_ctx("python workflows/audit/scripts/run.py"))
        self.assertIsNone(block)

    # ---- the false positive found by review on 2026-09-04
    def test_a_non_document_write_whose_content_names_markdown_is_allowed(self):
        """The rule asked whether a `.md` appeared anywhere and a write appeared
        anywhere, which treats them as related when they need not be. Writing a
        JSON index whose contents mention README.md writes no document."""
        block, _ = decision_for(shell_ctx(
            "python - <<'EOF'\nopen('index.json', 'w').write('README.md')\nEOF"))
        self.assertIsNone(block)

    def test_control_a_computed_target_still_blocks(self):
        """When a write's target is a variable it cannot be read from the
        command, and the rule stays conservative rather than opening the exact
        hole a narrower test would create."""
        block, _ = decision_for(shell_ctx(
            "python - <<'EOF'\np = 'notes.md'\nopen(p, 'w').write('x')\nEOF"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A9")

    def test_control_a_markdown_write_beside_a_json_write_still_blocks(self):
        """One readable non-document target must not excuse a document written
        alongside it."""
        block, _ = decision_for(shell_ctx(
            "python - <<'EOF'\n"
            "open('index.json', 'w').write('{}')\n"
            "open('notes.md', 'w').write('x')\n"
            "EOF"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A9")

    # ---- the rule reads the interpreter's code, not the whole command line
    # (2026-09-04, round two)
    def test_a_quoted_sample_beside_a_harmless_interpreter_is_allowed(self):
        """The rule scanned the entire command as soon as an interpreter was
        present anywhere on it, so text quoted into a neighbouring `echo` was
        read as though the interpreter had written it. That shape is common in
        review, test and documentation work, which is exactly where an
        over-block gets a hook routed around."""
        block, _ = decision_for(shell_ctx(
            "python -c \"print('ok')\" && echo \"open('README.md','w').write('x')\""))
        self.assertIsNone(block)

    def test_control_the_interpreter_still_blocks_when_it_is_the_writer(self):
        """The pair to the test above: same text, but now it is the code."""
        block, _ = decision_for(shell_ctx(
            "python -c \"open('README.md','w').write('x')\" && echo done"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A9")

    def test_a_heredoc_belongs_to_the_command_that_opened_it(self):
        """Interpreter code quoted into a `cat` heredoc is data being written
        by a shell redirect, not code being run. A4 and A7 own that route.

        This case never reaches the association check (no interpreter appears
        anywhere, so the rule returns early), which mutation testing exposed:
        removing the association entirely left it passing. It is kept as the
        simple form and paired with the test below, which is the one that
        actually measures the association.
        """
        block, _ = decision_for(shell_ctx(
            "cat <<'EOF' > notes.txt\nopen('notes.md','w').write('x')\nEOF"))
        self.assertIsNone(block)

    def test_a_cat_heredoc_is_not_read_as_a_neighbouring_interpreters_code(self):
        """The association test proper: an interpreter is present, so the rule
        does not return early, and the `cat` heredoc beside it carries text
        that looks exactly like a markdown write. Taking every heredoc on the
        command would read that body as the interpreter's script and block a
        command that writes a `.txt`."""
        block, _ = decision_for(shell_ctx(
            "python -c \"print('ok')\" && cat <<'EOF' > notes.txt\n"
            "open('notes.md','w').write('x')\n"
            "EOF"))
        self.assertIsNone(block)

    def test_control_an_interpreter_heredoc_is_still_read(self):
        """The pair to the test above, and the original 2026-09-02 shape."""
        block, _ = decision_for(shell_ctx(
            "python - <<'EOF'\nopen('notes.md','w').write('x')\nEOF"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A9")

    def test_a_keyword_mode_write_reads_its_target_like_a_positional_one(self):
        """`open(p, mode='w')` matched the verb pattern but not the target
        pattern, and counting the two separately dropped the command into the
        blunt whole-text fallback, where a mentioned `.md` blocked a JSON
        write. Finding the call and its target in one pass removes the
        mismatch rather than adding another pattern to keep in step."""
        block, _ = decision_for(shell_ctx(
            "python -c \"open('index.json', mode='w').write('README.md')\""))
        self.assertIsNone(block)

    def test_control_a_keyword_mode_write_to_a_document_still_blocks(self):
        block, _ = decision_for(shell_ctx(
            "python -c \"open('notes.md', mode='w').write('x')\""))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A9")

    def test_control_a_nested_call_target_is_computed_not_missing(self):
        """`open(os.path.join(d, 'x'), 'w')` must be seen as a write whose
        target cannot be read, so the conservative fallback applies. Failing to
        match it at all would allow it outright."""
        block, _ = decision_for(shell_ctx(
            "python -c \"open(os.path.join(d,'x'),'w').write('notes.md')\""))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A9")

    def test_blocks_a_node_write_and_allows_its_json_control(self):
        block, _ = decision_for(shell_ctx(
            "node -e \"require('fs').writeFileSync('notes.md','x')\""))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A9")
        block, _ = decision_for(shell_ctx(
            "node -e \"require('fs').writeFileSync('i.json','README.md')\""))
        self.assertIsNone(block)

    def test_blocks_an_interpreter_write_inside_a_subshell(self):
        """`(` used to be read as the program, so no interpreter was found."""
        block, _ = decision_for(shell_ctx(
            "(python -c \"open('notes.md','w').write('x')\")"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A9")


class TestShellParsingContract(unittest.TestCase):
    """The shared parser in core.py, tested directly.

    It feeds five rules (A3, A4, A6, A7, A9), so a defect here is a defect in
    all of them at once and is worth pinning where it lives rather than only
    through whichever rule happens to expose it. Every case below was a real
    misparse: `(` arriving as the program name, a stray `))` arriving as a
    filename, and `2>&1` contributing a `2` and a `1` as though they were
    operands.
    """

    def test_grouping_tokens_do_not_become_the_program(self):
        self.assertEqual(core.split_segments("(rm -rf build)"),
                         [["rm", "-rf", "build"]])

    def test_a_combined_closing_run_is_decomposed(self):
        """shlex glues a run of operator characters into one token, so `))`
        arrives as a single token that no membership test matches."""
        self.assertEqual(core.split_segments("( ( cat f ) )"), [["cat", "f"]])
        self.assertEqual(core.split_segments("cat f))"), [["cat", "f"]])

    def test_a_file_descriptor_is_part_of_the_redirection(self):
        self.assertEqual(core.split_segments("make build 2>&1"), [["make", "build"]])
        self.assertEqual(core.split_segments("make build 2>/dev/null"),
                         [["make", "build"]])

    def test_a_quoted_operator_is_not_an_operator(self):
        """The decomposition only ever touches a token made entirely of
        operator characters, and shlex has already resolved quoting by then."""
        self.assertEqual(core.split_segments("grep 'a && b' README.md"),
                         [["grep", "a && b", "README.md"]])

    def test_separators_and_depth_are_reported(self):
        detailed = core.split_segments_detailed("a && (b | c) ; d")
        self.assertEqual([s.argv[0] for s in detailed], ["a", "b", "c", "d"])
        self.assertEqual([s.separator for s in detailed], [None, "(", "|", ";"])
        self.assertEqual([s.depth for s in detailed], [0, 1, 1, 0])

    def test_only_parentheses_change_depth(self):
        """`{ ...; }` does not fork, so it is a boundary and not a depth."""
        detailed = core.split_segments_detailed("{ cd x; cat y; }")
        self.assertEqual([s.depth for s in detailed], [0, 0])

    def test_a_newline_ends_a_command(self):
        """The single largest hole the old reader had: a newline was consumed
        as ordinary whitespace, so every line of a multi-line command merged
        into one argv and only the first line's program was ever inspected."""
        self.assertEqual(core.split_segments("echo cleaning\nrm -rf build"),
                         [["echo", "cleaning"], ["rm", "-rf", "build"]])

    def test_a_newline_after_a_control_operator_continues_the_command(self):
        """`cd nowhere &&` on one line and the command on the next are one
        chain, so the `&&` must survive the line break."""
        detailed = core.split_segments_detailed("cd nowhere &&\ncat README.md")
        self.assertEqual([s.separator for s in detailed], [None, "&&"])

    def test_a_line_continuation_joins_the_lines(self):
        self.assertEqual(core.split_segments("grep -n \\\n  p \\\n  README.md"),
                         [["grep", "-n", "p", "README.md"]])

    def test_a_hash_starts_a_comment_only_at_the_start_of_a_word(self):
        """`a#b` is one word in the shell. Reading it as a comment discarded
        the rest of the line, taking any command on it."""
        self.assertEqual(core.split_segments("echo done#now && rm -rf build"),
                         [["echo", "done#now"], ["rm", "-rf", "build"]])
        self.assertEqual(core.split_segments("echo hi # a comment"),
                         [["echo", "hi"]])

    def test_a_quoted_operator_stays_an_argument(self):
        """Both directions of the same defect: the operator must not be lost
        (so `rm "&" -rf build` is still a recursive forced delete) and must not
        be promoted (so `echo ">" .env` writes nothing)."""
        self.assertEqual(core.split_segments('rm "&" -rf build'),
                         [["rm", "&", "-rf", "build"]])
        self.assertEqual(core.split_segments('echo ">" .env'),
                         [["echo", ">", ".env"]])
        self.assertEqual(core.redirect_targets('echo ">" .env'), [])

    def test_a_substitution_is_a_command_in_its_own_right(self):
        """Quoting a substitution hid the command inside it completely."""
        self.assertIn(["cat", "README.md"],
                      core.split_segments('echo "$(cat README.md)"'))
        self.assertIn(["cat", "README.md"],
                      core.split_segments("echo `cat README.md`"))

    def test_both_sides_of_a_redirection_are_kept(self):
        detailed = core.split_segments_detailed("cat < in.txt > out.txt")
        self.assertEqual(detailed[0].stdin, ["in.txt"])
        self.assertEqual(detailed[0].stdout, ["out.txt"])

    def test_a_genuine_numeric_argument_is_not_a_file_descriptor(self):
        """Adjacency decides: `2>&1` is a redirection, `-n 2 >` is an argument
        followed by one."""
        self.assertEqual(core.split_segments("head -n 2 > out.txt"),
                         [["head", "-n", "2"]])
        self.assertEqual(core.split_segments("make build 2>&1"),
                         [["make", "build"]])

    def test_a_brace_is_a_group_only_when_it_stands_alone(self):
        """`{}` is an ordinary argument, and `find -exec rm {} \\;` would
        otherwise be cut in half at it."""
        self.assertEqual(core.split_segments("find . -exec rm {} \\;"),
                         [["find", ".", "-exec", "rm", "{}", ";"]])

    def test_an_unbalanced_quote_is_unparseable(self):
        self.assertIsNone(core.split_segments("grep 'unterminated README.md"))

    def test_heredoc_bodies_come_back_with_their_opening_line(self):
        opener, body = core.heredoc_bodies("python - <<'EOF'\nx = 1\nEOF")[0]
        self.assertIn("python", opener)
        self.assertEqual(body, "x = 1")

    def test_heredoc_bodies_is_the_inverse_of_stripping_them(self):
        command = "cat <<'EOF' > f\nbody line\nEOF\necho done"
        self.assertNotIn("body line", core.strip_heredoc_bodies(command))
        self.assertIn("body line",
                      [b for _, b in core.heredoc_bodies(command)][0])


class TestGroupingReachesEveryShellRule(unittest.TestCase):
    """The parenthesis defect was found through A3, A6 and A9, but the parser
    is shared, so A4 and A7 were bypassable the same way and nobody had looked.
    Each rule gets its own case rather than trusting that one fix covers all
    five."""

    def test_a6_blocks_a_destructive_command_in_a_subshell(self):
        block, _ = decision_for(shell_ctx("(rm -rf build)"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A6")

    def test_a6_blocks_a_force_push_in_a_subshell(self):
        block, _ = decision_for(shell_ctx("true && (git push --force origin main)"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A6")

    def test_a6_blocks_a_destructive_command_in_a_brace_group(self):
        block, _ = decision_for(shell_ctx("{ rm -rf build; }"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A6")

    def test_a7_blocks_an_env_write_in_a_subshell(self):
        block, _ = decision_for(shell_ctx("(echo x > .env)"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A7")

    def test_a4_blocks_a_log_write_in_a_subshell(self):
        block, _ = decision_for(shell_ctx("(echo x >> workflows/audit/LOG.md)"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A4")

    def test_control_a_quoted_destructive_command_still_does_not_fire(self):
        """The whole reason A6 parses rather than substring-matches. Treating
        grouping characters as operators must not reintroduce a text match."""
        block, _ = decision_for(shell_ctx('echo "(rm -rf build)"'))
        self.assertIsNone(block)

    def test_control_a_file_descriptor_is_not_read_as_a_filename(self):
        block, _ = decision_for(shell_ctx("make build 2>&1"))
        self.assertIsNone(block)

    def test_a_newline_reaches_every_shell_rule(self):
        """The largest hole the shlex-based reader had. A newline was consumed
        as whitespace, so a multi-line command arrived as one command named by
        its first line, and four of the five rules never saw the second line.
        A3 alone had a private line-splitting workaround, which is why this was
        invisible for as long as it was: the rule most exercised looked fine."""
        cases = [
            ("echo cleaning\nrm -rf build", "A6"),
            ("echo a\ngit push --force origin main", "A6"),
            ("echo a\ncp /tmp/s .env", "A7"),
            ("echo a\ncp /tmp/s memory/LOG.md", "A4"),
            ("echo a\npython3 -c 'open(\"notes.md\",\"w\").write(1)'", "A9"),
            ("echo a\ncat README.md", "A3"),
        ]
        for command, rule in cases:
            with self.subTest(rule=rule):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, rule)

    def test_a_hash_inside_a_word_does_not_hide_the_rest_of_the_line(self):
        """`#` starts a comment only at the start of a word. Treating it as one
        anywhere discarded everything after it, including a real command."""
        for command, rule in (("echo done#now && rm -rf build", "A6"),
                              ("echo a#b > .env", "A7")):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block)
                self.assertEqual(block.rule, rule)

    def test_a_quoted_operator_neither_smuggles_nor_false_blocks(self):
        """Both halves of one defect. Quoting was resolved and discarded, so a
        quoted operator could not be told from a real one: it was re-promoted,
        which let `rm "&" -rf build` past A6, and it also fired A7 and A4 on
        commands that print text and write nothing. Both of those rules'
        docstrings promised the quoted case was safe."""
        block, _ = decision_for(shell_ctx('rm "&" -rf build'))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A6")
        for command in ('echo ">" .env', 'echo ">>" memory/LOG.md',
                        'echo "use" ">" ".env" "to overwrite"'):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNone(block)

    def test_a_substitution_hides_nothing(self):
        """A command inside `$(...)` or backticks runs. Quoting it made the
        whole thing a single inert word."""
        for command in ('echo "$(rm -rf build)"', "echo `rm -rf build`",
                        "X=$(rm -rf build)"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A6")


class TestA3RoundThree(unittest.TestCase):
    """A3 findings from the third review round.

    Grouped separately from `TestA3DedicatedTools` because they share a cause
    rather than a symptom: each is a fact about the shell that the reader did
    not know, and all of them were closed by replacing the reader rather than
    by adding a case to the rule.
    """

    def test_an_input_redirection_names_a_file(self):
        """`cat < README.md` reads the file. The redirection and its target
        were both discarded, so the command named nothing at all."""
        for command in ("cat < README.md", "grep pattern < README.md",
                        "head -5 < README.md", "sed 's/a/b/' < README.md"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A3")

    def test_the_command_is_not_always_the_first_word(self):
        """A wrapper, an assignment or a shell keyword sits where the program
        is expected, and reading argv[0] found `sudo`, `LC_ALL=C` or `if`."""
        for command in ("sudo cat README.md", "env cat README.md",
                        "command cat README.md", "nohup cat README.md",
                        "timeout 5 cat README.md", "LC_ALL=C cat README.md",
                        "if grep -q TODO README.md; then echo yes; fi",
                        "while grep -q TODO README.md; do sleep 1; done",
                        "for f in a b; do cat README.md; done"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A3")

    def test_control_a_wrapper_alone_reads_nothing(self):
        """Stripping prefixes must not invent a read where there is none."""
        for command in ("sudo make install", "env python3 build.py",
                        "if true; then echo hi; fi"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNone(block, command)

    def test_grep_dash_r_with_no_path_searches_the_directory(self):
        """The same rule `find` was given, left off `grep`. It is checked after
        the pattern operand is dropped, because `grep -r pattern` has exactly
        one operand and it is the pattern."""
        for command in ("grep -r pattern", "grep -rn TODO"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A3")

    def test_control_a_non_recursive_grep_with_no_path_still_reads_stdin(self):
        block, _ = decision_for(shell_ctx("build | grep pattern"))
        self.assertIsNone(block)

    def test_end_of_options_does_not_eat_the_file(self):
        """`--` was treated as a flag and dropped, and then the pattern-drop
        removed the real file instead of the dash-leading pattern."""
        block, _ = decision_for(shell_ctx("grep -- -pattern README.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_a_dash_leading_pattern_is_not_a_bundle_of_flags(self):
        """Found by mutation testing, as a test passing for the wrong reason.
        The case above blocked even with end-of-options handling removed,
        because `-pattern` was being read as short flags, one of which is `r`,
        which made grep look recursive and substituted the directory as the
        thing being read. The file must be named because it IS the operand,
        not because the command was mistaken for a directory walk."""
        from rules import shell_style
        self.assertEqual(
            shell_style.file_operands("grep", ["grep", "--", "-pattern",
                                               "README.md"]),
            ["README.md"])
        self.assertFalse(
            shell_style._is_recursive(["grep", "--", "-rpattern", "f"]))
        self.assertTrue(shell_style._is_recursive(["grep", "-rn", "p"]))

    def test_a_windows_path_does_not_buy_the_byte_level_allowance(self):
        """The marker was matched anywhere in any word, and on this platform a
        path is full of backslashes: `workflows\\rule-hooks\\CONTEXT.md`
        contains `\\r`, that file exists, and a plain read of it was downgraded
        to a warning. A byte pattern is the whole argument."""
        block, _ = decision_for(shell_ctx(
            'cat "workflows\\rule-hooks\\CONTEXT.md"'))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_control_a_real_byte_pattern_still_holds_the_allowance(self):
        block, warns = decision_for(shell_ctx("grep -c $'\\r' README.md"))
        self.assertIsNone(block)
        self.assertTrue(any(w.rule == "A3" and "allowance" in w.reason
                            for w in warns))

    def test_find_given_an_action_is_not_a_read(self):
        """`find . -name '*.pyc' -delete` deletes; it does not list, and Glob
        cannot do it. Blocking it and naming Glob is the over-block that gets a
        hook routed around.

        **A3 is asked directly here, and that is the point of the test.** Until
        2026-09-20 both specimens were allowed end to end, so running them
        through the pipeline tested the waiver. A6 now blocks every `-delete` on
        the verb and runs BEFORE A3 in the registry, so a pipeline assertion
        about the first specimen would be satisfied by A6 short-circuiting and
        would never reach the rule it names. The waiver itself is unchanged and
        still has to hold: if A3 ever stopped waiving, the command would be
        blocked for the wrong reason and told to use Glob, which cannot delete.
        """
        from rules.shell_style import check_dedicated_tools as a3_check
        self.assertIsNone(a3_check(shell_ctx("find . -name '*.pyc' -delete")))
        block, _ = decision_for(shell_ctx("find . -name '*.tmp' -exec rm {} +"))
        self.assertIsNone(block)

    def test_control_find_listing_still_blocks(self):
        block, _ = decision_for(shell_ctx("find . -name README.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_a_directory_operand_is_a_read_only_where_it_can_be(self):
        """`cat workflows` errors and reads nothing; `find workflows` and
        `grep -r pattern workflows` really do read it."""
        block, _ = decision_for(shell_ctx("cat workflows"))
        self.assertIsNone(block)
        for command in ("find workflows -name CONTEXT.md",
                        "grep -r pattern workflows"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)

    def test_a_line_continuation_is_not_a_parse_failure(self):
        """An unparseable command is allowed, so a trailing backslash used to
        disable the rule for everything that followed it."""
        block, _ = decision_for(shell_ctx(
            "grep -n \\\n  pattern \\\n  README.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_control_an_and_chain_survives_a_line_break(self):
        """`cd nowhere &&` then the command on the next line runs nothing, and
        the `&&` has to survive the newline for the rule to know that."""
        block, _ = decision_for(shell_ctx("cd nowhere &&\ncat README.md"))
        self.assertIsNone(block)

    def test_pushd_moves_the_shell_like_cd(self):
        block, _ = decision_for(shell_ctx(
            "pushd workflows/audit && cat CONTEXT.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_control_cd_to_home_is_resolvable_not_a_failure(self):
        """`cd ~` moves somewhere real, so the following read resolves there
        and finds nothing. Classifying it as a failed cd both blocked this and
        wrongly skipped the segment after `cd ~ &&`."""
        block, _ = decision_for(shell_ctx("cd ~ ; cat README.md"))
        self.assertIsNone(block)

    def test_a_read_inside_a_quoted_substitution_blocks(self):
        for command in ('echo "$(cat README.md)"', "echo `cat README.md`",
                        'X="$(cat README.md)"'):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A3")

    def test_a_quoted_heredoc_operator_opens_no_heredoc(self):
        """`echo "shift << bits"` opens nothing. Reading it as a heredoc
        swallowed every following line as a body, taking a real command."""
        block, _ = decision_for(shell_ctx('echo "shift << bits"\ncat README.md'))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_control_a_heredoc_with_an_unusual_delimiter_is_still_a_heredoc(self):
        """The delimiter is any word, not an identifier. Requiring one meant
        the body of `<<'END-OF-FILE'` was scanned as commands."""
        for delimiter in ("END-OF-FILE", "EOF.md", "9EOF"):
            with self.subTest(delimiter=delimiter):
                block, _ = decision_for(shell_ctx(
                    f"cat > notes.md <<'{delimiter}'\n"
                    f"cat README.md\n{delimiter}"))
                self.assertIsNone(block)

    def test_the_echo_warning_names_its_own_redirect(self):
        """The target was taken from the whole command, so any leading `echo`
        inherited a later command's redirection."""
        block, warns = decision_for(shell_ctx(
            "echo start; python build.py > log.txt"))
        self.assertIsNone(block)
        self.assertFalse([w for w in warns if w.rule == "A3"])


class TestA9RoundThree(unittest.TestCase):
    """A9 findings from the third review round."""

    WRITE = "open(\"notes.md\",\"w\").write(1)"

    def test_a_prefix_or_version_suffix_still_runs_an_interpreter(self):
        for prefix in ("env python3", "PYTHONPATH=. python3",
                       "timeout 5 python3", "python3.11", "/usr/bin/python3"):
            command = f"{prefix} -c '{self.WRITE}'"
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A9")

    def test_control_a_similarly_named_program_is_not_an_interpreter(self):
        """The stem is matched whole, so `pytest` is not `py`."""
        block, _ = decision_for(shell_ctx(
            "pytest -c 'open(\"notes.md\",\"w\").write(1)'"))
        self.assertIsNone(block)

    def test_write_idioms_beyond_the_first_four(self):
        for command in (
            "php -r 'file_put_contents(\"notes.md\",\"x\");'",
            "ruby -e 'File.write(\"notes.md\",\"x\")'",
            "perl -e 'open(my $fh, \">\", \"notes.md\");'",
            "node -e 'fs.writeFile(\"notes.md\",\"x\",cb)'",
            "python3 -c 'import shutil; shutil.copy(\"a.txt\",\"notes.md\")'",
            "python3 -c 'import os; os.rename(\"a.txt\",\"notes.md\")'",
        ):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A9")

    def test_a_computed_target_with_no_path_character_still_falls_back(self):
        """The fallback pattern required a path character before `.md`, so
        every f-string, `.format()` and `%` construction escaped the very
        fallback the rule's docstring says covers them."""
        for command in (
            "python3 -c 'n=\"notes\"; open(f\"{n}.md\",\"w\").write(1)'",
            "python3 -c 'open(\"{}.md\".format(\"notes\"),\"w\").write(1)'",
            "python3 -c 'open(\"%s.md\" % \"notes\",\"w\").write(1)'",
        ):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A9")

    def test_case_and_query_suffixes_do_not_defeat_the_document_test(self):
        for command in ("python3 -c 'open(\"NOTES.MD\",\"w\").write(1)'",
                        "python3 -c 'open(\"a.md?raw=1\",\"w\").write(1)'"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A9")

    def test_an_unbalanced_quote_in_a_body_does_not_fail_open(self):
        """The shell does not quote-parse a heredoc body, so an apostrophe in
        one is legal. It made the whole command unreadable, and unreadable
        means allowed."""
        block, _ = decision_for(shell_ctx(
            "python3 - <<'PY'\nopen(\"notes.md\",\"w\").write('it\\'s')\nPY"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A9")

    def test_an_unusual_heredoc_delimiter_is_still_read(self):
        for delimiter in ("PY-CODE", "9EOF"):
            with self.subTest(delimiter=delimiter):
                block, _ = decision_for(shell_ctx(
                    f"python3 - <<'{delimiter}'\n"
                    f"open(\"notes.md\",\"w\").write(1)\n{delimiter}"))
                self.assertIsNotNone(block)
                self.assertEqual(block.rule, "A9")

    def test_a_second_heredoc_on_one_line_is_still_read(self):
        """Only the first heredoc opener per line was found, so stacking one
        behind a harmless `cat` hid the interpreter's body entirely."""
        block, _ = decision_for(shell_ctx(
            "cat <<X && python3 <<Y\nhello\nX\nopen(\"notes.md\",\"w\").write(1)\nY"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A9")

    def test_control_a_saved_script_given_heredoc_data_is_not_inline_code(self):
        """`python render.py <<EOF` runs a saved script and the body is that
        script's input. Claiming it collapsed the documented saved-script
        boundary the moment a heredoc was attached."""
        for body in ("open(\"notes.md\",\"w\")",
                     "See open(\"README.md\",\"w\") in the docs"):
            with self.subTest(body=body):
                block, _ = decision_for(shell_ctx(
                    f"python3 tools/render.py <<EOF\n{body}\nEOF"))
                self.assertIsNone(block)

    def test_control_writing_to_stdout_is_not_writing_a_file(self):
        """`sys.stdout.writelines(open("README.md"))` prints a document and
        writes nothing. It was read as a write with an unreadable target, sent
        to the fallback, and blocked with a message naming the Edit tool."""
        for command in (
            "python3 -c 'import sys; sys.stdout.writelines(open(\"README.md\"))'",
            "python3 -c 'import sys; sys.stderr.writelines([\"no README.md\"])'",
        ):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNone(block, command)


class TestFabricatedHeredocs(unittest.TestCase):
    """A `<<` that does not open a heredoc.

    The worst defect class the reader has had, because it hides commands rather
    than misreading one: a fabricated heredoc has no terminator, so its body
    runs to the end of the input and every command after it disappears. Each
    case below suppressed a real A6 block.
    """

    def test_a_double_shift_in_a_comment_opens_nothing(self):
        block, _ = decision_for(shell_ctx(
            "echo hi # we used to write << EOF here\nrm -rf build"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A6")

    def test_a_left_shift_in_arithmetic_opens_nothing(self):
        """`$((1<<2))` is a shift, not a redirection."""
        for command in ("echo $((1<<2))\nrm -rf build",
                        "echo $(( 1 << 3 ))\nrm -rf build"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block)
                self.assertEqual(block.rule, "A6")

    def test_a_double_shift_in_a_multi_line_quote_opens_nothing(self):
        """Quote state carries across a line break. Scanning line by line
        reset it, so a `<<` on the second line of a quoted string looked like
        an opener."""
        block, _ = decision_for(shell_ctx(
            'echo "line a\nb << EOF\nc"\nrm -rf build'))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A6")

    def test_control_a_real_heredoc_still_has_its_body_removed(self):
        block, _ = decision_for(shell_ctx(
            "cat <<'EOF' > notes.txt\nrm -rf build\nEOF"))
        self.assertIsNone(block)

    def test_ansi_c_quoting_with_an_escaped_quote_still_parses(self):
        """A backslash escapes the closing quote inside `$'...'`. Treating it
        as an ordinary quote left the command unbalanced, and an unparseable
        command is allowed, which disables every parse-based rule at once."""
        block, _ = decision_for(shell_ctx("echo $'don\\'t' && rm -rf build"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A6")

    def test_control_ansi_c_quoting_still_yields_the_byte_pattern(self):
        """The content of `$'...'` is kept verbatim rather than decoded, so
        `$'\\r'` is still recognisable as a byte-level pattern and A3's
        allowance survives the parsing fix."""
        block, warns = decision_for(shell_ctx("grep -c $'\\r' README.md"))
        self.assertIsNone(block)
        self.assertTrue(any(w.rule == "A3" and "allowance" in w.reason
                            for w in warns))


class TestWrappersReachEveryRule(unittest.TestCase):
    """A6, A7 and A4 read argv[0] directly while A3 and A9 stripped wrappers.

    The helper existed and two rules used it, which is the shape that makes an
    omission easy to miss: the rules most exercised looked correct.
    """

    def test_a6_sees_through_a_wrapper_or_keyword(self):
        for command in ("sudo rm -rf build", "env rm -rf build",
                        "command rm -rf build", "nice rm -rf build",
                        "timeout 5 rm -rf build", "! rm -rf build",
                        "if true; then rm -rf build; fi",
                        "echo build | xargs rm -rf"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A6")

    def test_a7_and_a4_see_through_a_wrapper(self):
        """The write-target helper reads argv[0] too, so this one omission
        disabled both rules at once."""
        block, _ = decision_for(shell_ctx("echo X=1 | sudo tee .env"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A7")
        block, _ = decision_for(shell_ctx("echo x | sudo tee -a memory/LOG.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A4")

    def test_git_global_options_do_not_hide_the_subcommand(self):
        """`-C` and `-c` take a value, and the first non-dash word was that
        value rather than the subcommand."""
        for command in ("git -C . push --force origin main",
                        "git -C . reset --hard HEAD~1",
                        "git -c core.pager=cat reset --hard HEAD~1"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A6")

    def test_control_a_harmless_git_command_still_passes(self):
        for command in ("git -C . status", "git push --force-with-lease"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNone(block, command)


class TestA4LogNameBoundary(unittest.TestCase):
    """A4 matched any name ENDING in `log.md`.

    Worth its own class because the over-block hit a documented procedure:
    `memory/backlog.md` is a live file and AGENTS.md says to restore it from
    `memory/backlog-backups/`, so the recovery command was blocked, with a
    message about the append-only LOG.md entry format that does not apply to
    that file at all.
    """

    def test_a_file_that_merely_ends_in_log_md_is_not_a_log(self):
        for command in (
            "cp memory/backlog-backups/backlog-2026-09-01.md memory/backlog.md",
            "echo x > CHANGELOG.md", "cp a.md dialog.md", "mv notes.md prolog.md",
        ):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNone(block, command)

    def test_control_a_real_log_still_blocks(self):
        for command in ("echo x > memory/LOG.md", "cp a.md workflows/audit/LOG.md",
                        "echo x > session-log.md"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A4")


class TestA3RoundThreeExtras(unittest.TestCase):
    def test_brace_expansion_names_real_files(self):
        """The same gap already recorded for globs, left open for braces: the
        literal `{README,AGENTS}.md` is not any file's name."""
        block, _ = decision_for(shell_ctx("cat {README,AGENTS}.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_control_a_brace_list_matching_nothing_is_allowed(self):
        block, _ = decision_for(shell_ctx("cat {nope,alsonope}.md"))
        self.assertIsNone(block)

    def test_follow_mode_has_no_dedicated_tool(self):
        """The Read tool cannot follow a growing file, so blocking `tail -f`
        and naming Read refuses work with no compliant alternative."""
        for command in ("tail -f README.md", "tail -F README.md",
                        "tail -n 20 -f README.md"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNone(block, command)

    def test_control_a_plain_tail_still_blocks(self):
        block, _ = decision_for(shell_ctx("tail -n 5 README.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_the_path_backslash_scan_is_linear(self):
        """The A2 pattern was quadratic on an unbroken run with no backslash in
        it - a hash, a token, a base64 blob. Measured at 4.9 seconds for 20,000
        characters against a 5-second hook timeout, and a hook that times out
        has allowed the command. The bound here is deliberately loose; it is
        the growth curve that matters, and the old form could not pass it."""
        import time
        blob = "a" * 40000
        start = time.perf_counter()
        core.PATH_BACKSLASH_RE.search(blob)
        self.assertLess(time.perf_counter() - start, 0.5)

    def test_control_a_path_backslash_is_still_reported_with_its_word(self):
        """Narrowing the pattern to the backslash alone must not reduce the
        warning to a lone character."""
        _, warns = decision_for(shell_ctx("python workflows\\audit\\run.py"))
        matching = [w for w in warns if w.rule == "A2"]
        self.assertTrue(matching)
        self.assertIn("workflows\\audit", matching[0].reason)


class TestCarriedCommands(unittest.TestCase):
    """A command carried as a STRING argument still runs.

    The same defect as an unparsed substitution, one step further out: the
    text is sitting right there, wrapped in something that makes it look like
    an argument, and every rule went blind at once because none of them read
    inside it.
    """

    def test_a_shell_dash_c_string_is_a_command(self):
        for command, rule in (('sh -c "rm -rf build"', "A6"),
                              ("bash -c 'rm -rf build'", "A6"),
                              ("bash -c'rm -rf build'", "A6"),
                              ("bash -c 'echo K=V > .env'", "A7"),
                              ("eval 'rm -rf build'", "A6"),
                              ('sh -c "cat README.md"', "A3")):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, rule)

    def test_control_a_shell_running_a_script_file_is_out_of_scope(self):
        """The same boundary A9 keeps for a saved script: what `build.sh` does
        cannot be known from the command."""
        for command in ("sh build.sh", "bash scripts/deploy.sh"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNone(block, command)


class TestFindActionsAreExamined(unittest.TestCase):
    """A3 waives `find` with an action, correctly, because `-delete` is not a
    read and Glob cannot do it. That left the action itself unexamined by
    anyone: a waiver in one rule is not a waiver in all of them.

    The `-delete` contract itself lives in `TestFindDeleteBlocksOnTheVerb`; what
    this class holds is the `-exec` half, where A6 reads what the clause runs."""

    def test_a6_reads_what_find_exec_runs(self):
        block, _ = decision_for(shell_ctx("find . -type d -exec rm -rf {} +"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A6")

    def test_an_unfiltered_delete_is_destructive(self):
        block, _ = decision_for(shell_ctx("find . -delete"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A6")

    def test_control_a_harmless_exec_is_not_destructive(self):
        block, _ = decision_for(shell_ctx("find . -name '*.md' -exec cat {} ;"))
        self.assertIsNone(block)


class TestFindDeleteBlocksOnTheVerb(unittest.TestCase):
    """`-delete` is blocked on the verb, like every other destructive command.

    2026-09-20. This class replaces four classes and 33 tests that pinned an
    adjudication engine: A6 used to ask whether a narrowing predicate ran before
    the action, and allow the command when one did. Rounds 14 to 18 each found a
    defect in that engine, and round 18 showed the question is not answerable
    from the argv at all. `-fstype ntfs` is a genuine test that narrows nothing
    on a single-filesystem tree; so are `-name '*'`, `-size +0c`, `-type f` on a
    tree of files, and any all-matching regex. `narrows` was a property of the
    predicate, and safety is a property of the operand supplied.

    So the contract is now the same one `rm -rf` has always had, and the tests
    are correspondingly short: the presence of the verb decides, and none of the
    expression's shape is consulted. The cost is accepted rather than unnoticed
    and `test_a_filtered_delete_blocks_too` is where it is recorded.
    """

    def test_an_unfiltered_delete_blocks(self):
        block, _ = decision_for(shell_ctx("find . -delete"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A6")

    def test_a_filtered_delete_blocks_too(self):
        """**This is the accepted cost of the 2026-09-20 change, not a defect.**

        Each of these was allowed until then and is an ordinary cleanup. It now
        blocks, and the block message names the manual path. The measured
        frequency of paying this cost is zero: across 82 days of fire-log and
        the whole session-search index, no session had ever run a `find -delete`
        as real work.
        """
        for command in ("find . -name '*.pyc' -delete",
                        "find build -type f -name '*.tmp' -delete",
                        "find . -name .git -prune -o -name '*.pyc' -delete"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A6")

    def test_the_round_eighteen_specimens_block(self):
        """R18-1 and R18-2, the two open findings this change closes.

        `-D` is a value-taking global option, so GNU find consumes `-name` as
        its debug flag and the traversal is a bare `.`; `-fstype ntfs` matched
        the whole tested traversal on this tree. Both were ALLOW before.
        """
        for command in ("find -D -name . -delete",
                        "find . -fstype ntfs -delete"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A6")

    def test_every_shape_the_old_engine_adjudicated_now_blocks(self):
        """The rounds 14 to 17 specimens, blocking for one reason instead of four.

        Kept as one test rather than four classes because the distinctions
        between them are exactly what stopped being load-bearing: the comma, the
        `-o` branch, the negated predicate, the broad group, the word inside an
        `-exec` argv and the second action are now all just commands carrying
        `-delete`.
        """
        for command in ("find . -delete -name '*.pyc'",
                        "find . -name '*.pyc' -o -delete",
                        "find . -name '*.pyc' , -delete",
                        "find . -name '*.pyc' -delete -o -delete",
                        "find . \\( -name a -o -true \\) -delete",
                        "find rules -maxdepth 1 ! -name '*.py' -delete",
                        "find . -exec echo -name {} \\; -delete",
                        "find . -fprintf out -name -delete"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A6")

    def test_an_operand_spelled_delete_blocks_as_well(self):
        """The membership test is wider than the action, deliberately.

        `find . -name -delete -print` names a FILE and deletes nothing - round
        seventeen measured that, creating a file called `-delete` and watching
        real find print it and nothing else - and it now blocks. `rm` already
        makes the same trade, `rm -- -rf` being blocked on a filename too. It
        costs one retry against a message naming the manual path, and pinning it
        here is what stops a later reader taking it for a defect.
        """
        block, _ = decision_for(shell_ctx("find . -name -delete -print"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A6")

    def test_control_a_find_without_delete_is_not_blocked_by_a6(self):
        """The failure direction that matters: this must not become "any find".

        `-exec rm {} +` runs `rm` without `-rf`, so A6 reads the command, finds
        nothing destructive in it, and allows it. A change that blocked every
        `find` carrying an action would pass every other test in this class.
        """
        block, _ = decision_for(shell_ctx("find . -name '*.tmp' -exec rm {} +"))
        self.assertIsNone(block)


class TestNegatedDirectoryVerbs(unittest.TestCase):
    """A leading `!` inverts what a pipeline REPORTS, so it decides reachability.

    R14-2, 2026-09-18. `effective_argv` strips the `!` to find the program, which
    is right for identifying the command and wrong for judging its exit status.
    A3 then read a successful `cd` as ending the `||` chain after it while bash,
    having inverted the status, runs that branch: the read went unexamined.

    The directory move still follows the REAL result of the `cd`. Only the chain
    state is inverted, which is the whole of the distinction.

    Every specimen below was run in Git Bash with `echo RAN` in place of the read,
    and bash's answer matches each assertion here.
    """

    def test_regression_control_a_negated_success_leaves_the_or_branch_live(self):
        """`! cd workflows || cat ../README.md`. Bash runs the read."""
        block, _ = decision_for(shell_ctx(
            "! cd workflows || cat ../README.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_regression_control_a_negated_failure_leaves_the_and_branch_live(self):
        """`! cd no_such_dir && cat README.md`. Bash runs the read."""
        block, _ = decision_for(shell_ctx(
            "! cd no_such_dir && cat README.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_the_negation_is_carried_across_a_pipeline(self):
        """`!` negates a PIPELINE, whose status is its LAST stage's.

        Reading the `!` off the settling segment instead of the pipeline's first
        would fix the two cases above and leave this one broken, which is the
        half-applied shape this rule set keeps producing. The read is written
        relative to the unchanged directory because a pipelined `cd` moves
        nothing.
        """
        block, _ = decision_for(shell_ctx(
            "! echo hi | cd workflows || cat README.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_the_mirror_cases_suppress_the_read(self):
        """The other direction, which a blanket inversion would get wrong.

        A negated success ends an `&&` chain and a negated failure satisfies an
        `||`, so in both of these bash never reaches the read and blocking would
        be a false positive.
        """
        for command in ("! cd workflows && cat ../README.md",
                        "! cd no_such_dir || cat README.md",
                        "echo hi | cd workflows || cat README.md"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNone(block, command)

    def test_negating_twice_is_not_negating(self):
        """Parity, not presence. `! ! cmd` reports what `cmd` reported."""
        block, _ = decision_for(shell_ctx(
            "! ! cd workflows || cat ../README.md"))
        self.assertIsNone(block)

    def test_control_the_unnegated_chains_are_unchanged(self):
        """The behaviours the fix must not move, in both directions."""
        for command, blocks in (
                ("cd workflows || cat ../README.md", False),
                ("cd no_such_dir && cat README.md", False),
                ("cd workflows && cat ../README.md", True),
        ):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertEqual(block is not None, blocks, command)

    def test_control_direct_negation_of_a_read_still_blocks(self):
        """`!` changes what a command reports, never whether it ran."""
        block, _ = decision_for(shell_ctx("! grep TODO README.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")


class TestHeredocTerminator(unittest.TestCase):
    def test_an_indented_delimiter_does_not_close_a_plain_heredoc(self):
        """POSIX wants the delimiter alone on its line. Matching on a stripped
        line ended the body early, and the rest of the body was then read as
        commands - which is what happens when this project documents its own
        heredoc convention inside a heredoc."""
        block, _ = decision_for(shell_ctx(
            "cat > docs/danger.md <<'EOF'\nNever run:\n\n  EOF\n"
            "  rm -rf build\nEOF"))
        self.assertIsNone(block)

    def test_control_a_dashed_heredoc_still_strips_leading_tabs(self):
        """`<<-` strips leading tabs, and only tabs, so that half must keep
        working."""
        block, _ = decision_for(shell_ctx(
            "cat > d.md <<-'EOF'\n\ttext\n\tEOF"))
        self.assertIsNone(block)


class TestA9RoundThreeExtras(unittest.TestCase):
    def test_an_attached_inline_flag_still_carries_code(self):
        """The shell concatenates, so `python -c'code'` is one word."""
        for command in ("python -c'open(\"a.md\",\"w\").write(1)'",
                        "perl -e'open(FH,\">a.md\")'"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A9")

    def test_two_more_write_shapes(self):
        for command in (
            "python -c \"__import__('pathlib').Path('a.md').write_bytes(b'')\"",
            "python -c \"Path('a.md').open('w').write('x')\"",
        ):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A9")

    def test_control_a_read_of_a_w_named_file_is_not_a_write(self):
        """The mode string has to close. Without the closing quote,
        `io.open('workflows/x.md', encoding='utf-8')` matched the one-argument
        `.open("w")` shape, because the PATH begins with a `w`: every read of a
        file whose name starts with w, a or x was read as a write. The rule
        blocked one of this project's own commands that way, which is how it
        was found."""
        for command in (
            "python -c \"import io; io.open('workflows/a.md', encoding='utf-8')\"",
            "python -c \"open('notes.md').read()\"",
        ):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNone(block, command)

    def test_control_a_real_one_argument_open_still_blocks(self):
        block, _ = decision_for(shell_ctx(
            "python -c \"Path('a.md').open('w').write('x')\""))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A9")

    def test_control_the_same_shapes_on_a_non_document_are_allowed(self):
        for command in (
            "python -c \"__import__('pathlib').Path('a.json').write_bytes(b'')\"",
            "python -c \"Path('a.json').open('w').write('x')\"",
        ):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNone(block, command)


class TestHookCostIsBounded(unittest.TestCase):
    """The hook has a 5-second timeout and a timed-out hook has ALLOWED the
    command, so a slow path is a hole rather than only friction."""

    def test_a_recursive_glob_operand_does_not_walk_the_project(self):
        import time
        for command in ("cat **", "grep -rn TODO **/*.py", "cat **/**/*"):
            with self.subTest(command=command):
                start = time.perf_counter()
                decision_for(shell_ctx(command))
                self.assertLess(time.perf_counter() - start, 1.0, command)


class TestModulesImportCleanly(unittest.TestCase):
    """A hook writes to stderr to explain a block, so anything else arriving
    there is noise on the channel that carries the decision."""

    def test_no_rule_module_emits_a_syntax_warning(self):
        import warnings
        for name in ("core.py", "run.py"):
            path = SCRIPTS / name
            with self.subTest(module=name):
                with warnings.catch_warnings():
                    warnings.simplefilter("error", SyntaxWarning)
                    compile(path.read_text(encoding="utf-8"), str(path), "exec")
        for path in sorted((SCRIPTS / "rules").glob("*.py")):
            with self.subTest(module=path.name):
                with warnings.catch_warnings():
                    warnings.simplefilter("error", SyntaxWarning)
                    compile(path.read_text(encoding="utf-8"), str(path), "exec")


class TestWrapperOptions(unittest.TestCase):
    """A wrapper's own options put the wrapper's FLAG in argv[0].

    The single highest-value finding of the fourth round: `effective_argv`
    advanced exactly one word past a wrapper, so `sudo -u root rm -rf /srv`
    left `-u` as the program name and all five rules read it as such. Eight of
    the ten wrappers take options; the two that do not are `builtin` and
    `nohup`, which is precisely why the existing tests, written with bare
    `sudo` and bare `xargs`, all passed.
    """

    def test_every_rule_sees_through_a_wrapper_with_options(self):
        for command, rule in (
            ("sudo -u root rm -rf /srv", "A6"),
            ("echo b | xargs -I {} rm -rf {}", "A6"),
            ("nice -n 10 rm -rf build", "A6"),
            ("command -p rm -rf build", "A6"),
            ("env -u FOO rm -rf build", "A6"),
            ("stdbuf -o0 grep TODO README.md", "A3"),
            ("sudo -u me cat README.md", "A3"),
            ("nice -n 5 python -c \"open('notes.md','w').write('x')\"", "A9"),
            ("echo K=v | sudo -u me tee .env", "A7"),
            ("echo x | nice -n 5 tee memory/LOG.md", "A4"),
        ):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, rule)

    def test_control_a_wrapper_with_options_and_no_danger_is_allowed(self):
        for command in ("sudo -u root make install", "nice -n 10 pytest",
                        "xargs -I {} echo {}", "env -u FOO python build.py"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNone(block, command)


class TestA9AdjacentStringLiterals(unittest.TestCase):
    """R14-5: Python glues adjacent string literals at compile time, so
    `open('notes.' 'md', 'w')` writes notes.md while no `.md` text exists in the
    command for the computed-target fallback to find.

    The shape is REFUSED rather than assembled, at the user's decision on
    2026-09-21 under the triage rule in memory/review_process.md. The
    measurement that settled it: across 27,278 indexed transcript messages
    spanning 2026-05-18, every occurrence of this shape in the project's history
    is the review that reported it, so nothing has ever written one. Assembling
    the parts instead would mean matching Python's own lexer exactly, and every
    difference is a fresh way through - the argument that ended the A6
    `find -delete` series and settled A10.

    Half the class is controls, for the usual reason: the fix widens what A9
    blocks, and a false block is what gets a hook routed around. The controls
    carry the boundary of a refusal that never reads what it refuses, so what
    holds it to its intended width is the set of shapes that must NOT be read as
    a split."""

    SPLIT_MD = "python -c \"open('notes.' 'md', 'w').write('x')\""
    SPLIT_WRITE_TEXT = ("python -c \"from pathlib import Path; "
                        "Path('notes.' 'md').write_text('x')\"")
    SPLIT_ENV = "python -c \"open('.' 'env', 'w').write('X=1')\""
    SPLIT_THREE = "python -c \"open('notes' '.' 'md', 'w').write('x')\""
    SPLIT_NEWLINE = "python -c \"open('notes.'\n'md', 'w').write('x')\""

    def test_split_literals_are_refused(self):
        for command, label in (
                (self.SPLIT_MD, "builtin open"),
                (self.SPLIT_WRITE_TEXT, "Path.write_text receiver"),
                (self.SPLIT_ENV, "the .env spelling"),
                (self.SPLIT_THREE, "three parts rather than two"),
                (self.SPLIT_NEWLINE, "split across a newline")):
            with self.subTest(label=label):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, label)
                self.assertEqual(block.rule, "A9", label)

    def test_the_block_message_names_the_single_literal_remedy(self):
        """A refusal has to carry the manual path, or it is a dead end. The
        remedy for this shape is not the Edit tool alone: the caller has to
        respell the filename before any advice about tools applies."""
        block, _ = decision_for(shell_ctx(self.SPLIT_MD))
        self.assertIn("single literal", block.reason)

    def test_accepted_cost_a_split_non_document_is_refused_too(self):
        """Pinned as a decided trade rather than left to be rediscovered as a
        defect. The refusal never reads what the parts spell, so it cannot tell
        `.txt` from `.md`; that is the whole reason it cannot be wrong about a
        meaning. Recorded in the owning workflow's Known Issues."""
        block, _ = decision_for(shell_ctx(
            "python -c \"open('notes.' 'txt', 'w').write('x')\""))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A9")

    def test_control_contiguous_targets_still_block(self):
        """The ordinary spellings must not move. A refusal added in front of the
        literal/computed branch could shadow it."""
        for command in (
                "python -c \"open('notes.md', 'w').write('x')\"",
                "python -c \"open('.env', 'w').write('X=1')\""):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A9", command)

    def test_the_backstop_asks_the_mode_before_the_split_question(self):
        """The regex refusal is consulted only for a call already judged a
        write, so a read carrying the split spelling does not reach it.

        **Changed on 2026-09-22, and the change is the point.** This was a
        control asserting that the read is allowed. Since PP1 the parser-based
        check refuses a hidden document name whether it is written or read, as
        a decided cost, so for code Python can parse the read now blocks, which
        `TestA9HiddenDocumentNames` pins. What stays true is the backstop's own
        ordering, and that is only observable in code the parser rejects: the
        trailing `)` below makes it a syntax error, so the regex is the only
        check left, and it must still let a read through."""
        block, _ = decision_for(shell_ctx(
            "python -c \"print(open('notes.' 'md', 'r').read()))\""))
        self.assertIsNone(block)
        block, _ = decision_for(shell_ctx(
            "python -c \"print(open('notes.' 'md', 'r').read())\""))
        self.assertIsNotNone(block)
        self.assertIn("without writing it out", block.reason)

    def test_control_adjacent_literals_outside_a_target_are_allowed(self):
        """Two literals side by side are ordinary Python - a regex built in
        parts, for instance. Only a write call's TARGET is refused.

        The specimen carries a real write beside the adjacent literals, and that
        is the whole point of it. The obvious version, code with no write at all,
        cannot fail: `check` returns on `if not writes` before the refusal is
        ever consulted, so it passes whether the scope is the target or the whole
        code. The mutation pass caught that - a scope-widening mutant fired
        nothing - which is the control-that-proves-nothing trap this file already
        records, met again while writing a control against it. The `.json` target
        keeps the write out of A9's document set, so only a widened scope can
        block this."""
        block, _ = decision_for(shell_ctx(
            "python -c \"import re; s = re.sub(r'a' r'b', 'c', 'd'); "
            "open('out.json', 'w').write(s)\""))
        self.assertIsNone(block)
        # The no-write case is still worth stating, as the boundary it is: A9
        # does not fire on inline code that writes nothing.
        block, _ = decision_for(shell_ctx(
            "python -c \"import re; print(re.sub(r'a' r'b', 'c', 'd'))\""))
        self.assertIsNone(block)

    def test_control_a_computed_target_is_still_computed(self):
        """An operator between the parts means the target really is assembled at
        runtime, which is what the fallback exists for. Reading it as a split
        would refuse a shape the rule already handles correctly, and would do it
        by a path that reports the wrong reason."""
        self.assertFalse(a9_splits_a_literal("'%s.md' % 'notes'"))
        self.assertFalse(a9_splits_a_literal("'notes.' + 'md'"))
        block, _ = decision_for(shell_ctx(
            "python -c \"import sys; open('notes.' + sys.argv[1], 'w')"
            ".write('x')\""))
        self.assertIsNone(block)

    def test_the_predicate_itself_separates_the_two_classes(self):
        """The pipeline cannot tell "refused as a split" from "blocked by the
        fallback", because both end in an A9 block, so the predicate is asserted
        directly. This is the control that stops the refusal quietly widening
        into every target that is not one bare literal."""
        for text in ("'notes.' 'md'", "'notes.''md'", "'notes' '.' 'md'",
                     "'notes.'\n'md'", '"notes." "md"', "r'notes.' r'md'",
                     "Path('notes.' 'md')"):
            with self.subTest(text=text):
                self.assertTrue(a9_splits_a_literal(text), text)
        for text in ("'notes.md'", "'%s.md' % 'notes'", "'notes.' + 'md'",
                     "f'{x}' 'md'", "os.path.join('a', 'md')", ""):
            with self.subTest(text=text):
                self.assertFalse(a9_splits_a_literal(text), text)


def a9_hidden_documents(code):
    """A9's parser-based hidden-name check, reached the same way."""
    from rules.interpreter_write import _hidden_documents
    return _hidden_documents(code)


class TestA9HiddenDocumentNames(unittest.TestCase):
    """PP1, 2026-09-22: a document name Python builds but the text never spells.

    The regex refusal above recognised one join, whitespace, and Codex found
    `open('notes.' #c` newline `'md', 'w')` going through, Python ignoring a
    comment between adjacent literals. Measuring the family before fixing it
    found nine spellings through, not one, and two of them join nothing: a
    single literal hides the dot behind `\\x2e` or `\\N{FULL STOP}`. Widening
    the regex's separator would have closed four of the nine, because the rest
    fail on the regex's reading of the literal itself or on the write shapes
    missing the call.

    **So the question moved to Python's own parser**, which is the reader that
    runs the code and therefore has no difference from it to exploit. It is
    still a refusal: nothing about the write is inferred, and a read spelled the
    same way is refused too, as a decided cost.

    Every specimen goes through a quoted heredoc, so the Python source the rule
    reads is exactly the text written here, with no shell unquoting between."""

    @staticmethod
    def heredoc(code):
        return "python - <<'PY'\n" + code + "\nPY"

    HIDDEN = (
        ("open('notes.' #c\n 'md', 'w')", "PP1: a comment between the parts"),
        ("open('notes.' # it's\n 'md', 'w')", "a comment holding a quote"),
        ("open('notes.' # a, b\n 'md', 'w')",
         "a comment holding a comma, which also hides the write shape"),
        ("open('notes.' \\\n'md', 'w')", "a backslash continuation"),
        ("open(\"a'b.\" \"md\", 'w')", "a part holding the other quote"),
        ("open('notes\\x2emd', 'w')", "a hex escape in ONE literal"),
        ("open('notes\\N{FULL STOP}md', 'w')", "a named escape in ONE literal"),
        ("open(f'notes.' 'md', 'w')", "an f-string whose parts are constant"),
        ("from pathlib import Path\nPath('notes.' #c\n 'md').write_text('x')",
         "a Path receiver"),
        ("open('.' #c\n 'env', 'w')", "the .env spelling"),
        # Armed against comparing with the whole program rather than with the
        # string's own text: the name appears in full, but in a comment, and
        # the comma in the second comment keeps the write shapes from seeing
        # the call, so nothing else in the rule would catch it.
        ("# notes.md\nopen('notes.' # a, b\n 'md', 'w')",
         "the name written out elsewhere, in a comment"),
    )

    def test_hidden_names_are_refused(self):
        """Each was ALLOW before this fix, measured against the hook. The
        reason is asserted as well as the rule, because the whitespace backstop
        and the computed-target fallback also end in an A9 block, and only the
        reason shows which check caught it."""
        for code, label in self.HIDDEN:
            with self.subTest(label=label):
                block, _ = decision_for(shell_ctx(self.heredoc(code)))
                self.assertIsNotNone(block, label)
                self.assertEqual(block.rule, "A9", label)
                self.assertIn("without writing it out", block.reason, label)

    def test_a_dash_c_string_is_read_too(self):
        """The heredoc and `-c` routes reach the rule by different code, so
        each is shown to arrive. Inside double quotes the shell leaves `\\x`
        alone, so Python receives the escape."""
        block, _ = decision_for(shell_ctx(
            "python -c \"open('notes\\x2emd', 'w').write('x')\""))
        self.assertIsNotNone(block)
        self.assertIn("without writing it out", block.reason)

    def test_the_block_message_names_the_remedy(self):
        block, _ = decision_for(shell_ctx(self.heredoc(self.HIDDEN[0][0])))
        self.assertIn("single literal", block.reason)
        self.assertIn("notes.md", block.reason)

    def test_accepted_cost_a_hidden_read_is_refused(self):
        """Pinned as a decided trade. The check runs before the write shapes
        because one of the spellings hides the write from them, so it cannot
        ask whether the call writes. Measured before it was accepted: no
        tracked Python file, read as though it were inline code, spells a
        document name this way."""
        block, _ = decision_for(shell_ctx(self.heredoc(
            "print(open('notes\\x2emd').read())")))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A9")

    def test_control_names_written_out_in_full_are_not_hidden(self):
        """The width of the refusal, asked of the predicate directly. Only the
        filename at the END of a string is compared, against that string's own
        text: comparing the whole string refused the first two, whose names are
        written out in full beside an escape. An f-string placeholder stays a
        computed target, since its constant part `.md` is visible text."""
        for code in ("print('\\nwrote notes.md')",
                     "open('C:\\\\docs\\\\notes.md').read()",
                     "import re\nre.compile(r'\\.md$')",
                     "open('notes.md', 'w')",
                     "open('out.json', 'w')",
                     "name = 'notes.' + 'md'",
                     "open(f'{name}.md', 'w')"):
            with self.subTest(code=code):
                self.assertEqual(a9_hidden_documents(code), [], code)

    def test_control_an_escape_beside_a_non_document_write_is_allowed(self):
        """The same width end to end: a program printing an escaped message
        that names a document, while writing only a `.json`, is ordinary work
        and must not move."""
        block, _ = decision_for(shell_ctx(self.heredoc(
            "print('\\nwrote notes.md')\nopen('out.json', 'w').write('{}')")))
        self.assertIsNone(block)

    def test_control_unparseable_code_keeps_the_backstop(self):
        """Code the parser rejects returns None rather than a verdict, and the
        whitespace refusal still stands behind it. The stray `)` is what makes
        this a syntax error, so this is the only specimen that reaches the
        backstop on its own."""
        code = "open('notes.' 'md', 'w'))"
        self.assertIsNone(a9_hidden_documents(code))
        block, _ = decision_for(shell_ctx(self.heredoc(code)))
        self.assertIsNotNone(block)
        self.assertIn("adjacent string literals", block.reason)

    def test_each_program_is_parsed_on_its_own(self):
        """Two programs joined are rarely one valid module, so parsing the
        joined text would let a node segment hide a python one behind a syntax
        error. The node code here is not valid Python."""
        block, _ = decision_for(shell_ctx(
            "node -e \"const x = 1;\" && " + self.heredoc(self.HIDDEN[5][0])))
        self.assertIsNotNone(block)
        self.assertIn("without writing it out", block.reason)

    def test_the_hidden_name_check_is_linear_in_the_size_of_the_code(self):
        """An order-of-magnitude canary, like the other per-rule cost tests:
        the first implementation took 5.08 seconds on 2,000 document-named
        strings, past the hook's five-second budget, and a hook that runs out
        of time has allowed the command. Correct code measured 0.27 seconds on
        the 8,000 used here, so 3.0 sits an order of magnitude from both."""
        import time
        code ="\n".join(f"paths.append('docs/file{i}.md')" for i in range(8000))
        start = time.perf_counter()
        a9_hidden_documents(code)
        self.assertLess(time.perf_counter() - start, 3.0)


class TestRoundFour(unittest.TestCase):
    def test_an_unquoted_heredoc_body_is_expanded(self):
        """`<<'EOF'` is literal text; `<<EOF` is not - the shell runs any
        substitution in the body before writing it. Bodies are stripped before
        parsing, which is right for the quoted case and discarded a real
        command in the unquoted one."""
        block, _ = decision_for(shell_ctx("cat <<EOF\n$(rm -rf build)\nEOF"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A6")

    def test_control_a_quoted_heredoc_body_stays_inert(self):
        block, _ = decision_for(shell_ctx("cat <<'EOF'\n$(rm -rf build)\nEOF"))
        self.assertIsNone(block)

    def test_a7_is_case_folded_like_the_other_rules(self):
        """The only rule of the five that compared exactly. On this platform
        `.ENV` IS `.env`, and the Edit/Write path was affected too, so it was a
        bypass of the tool-level guard rather than a shell-parsing nicety."""
        for command in ("echo K=v | tee .ENV", "cp t .ENV"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A7")
        for path in (".ENV", ".ENV.LOCAL"):
            with self.subTest(path=path):
                block, _ = decision_for(write_ctx(path, "K=v"))
                self.assertIsNotNone(block, path)
                self.assertEqual(block.rule, "A7")

    def test_control_env_example_survives_case_folding(self):
        block, _ = decision_for(write_ctx(".env.example", "K=v"))
        self.assertIsNone(block)

    def test_the_byte_allowance_belongs_to_the_pattern_not_to_any_word(self):
        """A one-token bypass: appending a byte escape as a spare operand
        bought the allowance while the real read went ahead. `cat` has no
        pattern at all, so no `cat` can claim it."""
        for command in ("cat README.md $'\\r'",
                        "grep -e TODO README.md $'\\t'"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A3")

    def test_control_the_allowance_still_holds_for_a_real_byte_pattern(self):
        for command in ("grep -c $'\\r' README.md", "grep -P '\\x0d' README.md",
                        "grep -e $'\\r' README.md"):
            with self.subTest(command=command):
                block, warns = decision_for(shell_ctx(command))
                self.assertIsNone(block, command)
                self.assertTrue(any(w.rule == "A3" and "allowance" in w.reason
                                    for w in warns), command)

    def test_a_bundled_shell_flag_still_carries_the_command(self):
        """`bash -lc` and `sh -ec` are ordinary spellings; only the last flag
        in a bundle takes a value, and an exact `-c` test missed both."""
        for command in ("bash -lc 'rm -rf build'", "sh -ec 'rm -rf build'",
                        "bash -c -- 'rm -rf build'", "bash -xc 'cat README.md'"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)

    def test_a_heredoc_opened_inside_a_dash_c_string(self):
        """The `<<` is inside quotes at the outer level, so it correctly opens
        nothing there - and was never looked for at the inner level either."""
        block, _ = decision_for(shell_ctx(
            "bash -c \"python - <<'PY'\nopen('notes.md','w').write('x')\nPY\n\""))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A9")

    def test_an_interpreter_write_to_env_is_covered(self):
        """Guarded by nobody: A7 reads the Edit/Write path and the shell write
        verbs and never reads interpreter code, while A9 narrowed to
        documents. `LOG.md` was covered here only by its extension."""
        block, _ = decision_for(shell_ctx(
            "python -c \"open('.env','w').write('K=v')\""))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A9")

    def test_control_an_interpreter_write_to_env_example_is_allowed(self):
        block, _ = decision_for(shell_ctx(
            "python -c \"open('.env.example','w').write('K=')\""))
        self.assertIsNone(block)

    def test_an_attached_pattern_flag_does_not_swallow_the_file(self):
        """Not a near-miss but an inversion: the rule concluded no flag had
        supplied the pattern, dropped the leading operand as the pattern, and
        the leading operand was the file."""
        for command in ("grep -eTODO README.md", "sed -e's/a/b/' README.md"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A3")

    def test_a_plus_refspec_forces_a_push(self):
        for command in ("git push origin +main",
                        "git push origin +refs/heads/main:main"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A6")

    def test_control_an_ordinary_push_is_allowed(self):
        for command in ("git push origin main", "git push --force-with-lease"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNone(block, command)

    def test_find_exec_running_a_shell_is_descended_into(self):
        """A3 waives find-with-an-action on the grounds that A6 examines it, so
        a gap here is a gap in both rules at once."""
        for command in ("find . -exec sh -c 'rm -rf build' ;",
                        "find . -exec bash -lc 'rm -rf x' ;"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A6")

    def test_sed_in_place_writes_every_file_operand(self):
        """The protected file is named FIRST on purpose. With it last, the
        test passed even when only the last operand was recorded, which is the
        wrong-reason pass mutation testing exists to expose."""
        block, _ = decision_for(shell_ctx(
            "sed -i 's/a/b/' memory/LOG.md notes.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A4")

    def test_follow_with_an_attached_value_is_still_follow(self):
        block, _ = decision_for(shell_ctx("tail --follow=name README.md"))
        self.assertIsNone(block)

    def test_popd_returns_to_where_pushd_left(self):
        """Modelling the push without the pop left the rule resolving reads in
        a directory the command had already come back from."""
        block, _ = decision_for(shell_ctx(
            "pushd workflows/audit && popd && grep p CONTEXT.md"))
        self.assertIsNone(block)

    def test_popd_returns_to_the_right_place_not_merely_to_nowhere(self):
        """The test above cannot tell "popd went back to the root" from "popd
        gave up and the directory became unknown": both allow. This one can,
        because the file it reads exists at the root and nowhere else, so only
        an accurate return blocks it. Mutation testing found the gap, by
        deleting the push and watching the first test still pass."""
        block, _ = decision_for(shell_ctx(
            "pushd workflows/audit && popd && cat README.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_control_pushd_without_popd_still_moves(self):
        block, _ = decision_for(shell_ctx(
            "pushd workflows/audit && grep p CONTEXT.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_a9_scanning_is_linear_in_the_size_of_the_code(self):
        """The same quadratic shape A2 was rewritten to avoid, left standing in
        A9's patterns. They run over every inline body whether or not it
        contains a write, so no write was needed to trigger it: measured at
        four minutes on a 40,000-character script against a 5-second hook
        budget, and a hook that times out has allowed the command."""
        import time
        start = time.perf_counter()
        decision_for(shell_ctx("python3 -c '" + "a" * 40000 + "'"))
        self.assertLess(time.perf_counter() - start, 2.0)


class TestClaudeAdapter(unittest.TestCase):
    def test_parse_bash(self):
        ctx = claude_adapter.parse_event(
            {"tool_name": "Bash", "tool_input": {"command": "ls"}}, PROJECT_ROOT)
        self.assertEqual(ctx.category, "shell")
        self.assertEqual(ctx.command, "ls")

    def test_parse_edit_uses_new_string(self):
        ctx = claude_adapter.parse_event(
            {"tool_name": "Edit",
             "tool_input": {"file_path": "a.py", "new_string": "inserted text"}},
            PROJECT_ROOT)
        self.assertEqual(ctx.category, "write")
        self.assertEqual(ctx.content, "inserted text")

    def test_unguarded_tool_returns_none(self):
        self.assertIsNone(claude_adapter.parse_event(
            {"tool_name": "WebSearch", "tool_input": {}}, PROJECT_ROOT))


class TestCodexAdapter(unittest.TestCase):
    def test_parse_shell_list_command(self):
        ctx = codex_adapter.parse_event(
            {"tool_name": "shell",
             "tool_input": {"command": ["rm", "-rf", "x"]}, "cwd": "."},
            PROJECT_ROOT)
        self.assertEqual(ctx.category, "shell")
        self.assertEqual(ctx.command, "rm -rf x")

    def test_parse_apply_patch(self):
        ctx = codex_adapter.parse_event(
            {"tool_name": "apply_patch",
             "tool_input": {"patch": "*** Begin Patch\n*** Add File: .env\n+S=1\n*** End Patch"}},
            PROJECT_ROOT)
        self.assertEqual(ctx.category, "write")
        self.assertEqual(ctx.file_path, ".env")

    def test_parse_apply_patch_command_field(self):
        ctx = codex_adapter.parse_event(
            {"tool_name": "apply_patch",
             "tool_input": {"command": "*** Begin Patch\n*** Add File: .env.hooktest\n+S=1\n*** End Patch"}},
            PROJECT_ROOT)
        self.assertEqual(ctx.category, "write")
        self.assertEqual(ctx.file_path, ".env.hooktest")


class TestSubprocessContracts(unittest.TestCase):
    """End-to-end through run.py: the real exit code / output contract."""

    def _run(self, ai, event):
        result = subprocess.run(
            [sys.executable, str(RUN_PY), "--ai", ai],
            input=json.dumps(event), capture_output=True, encoding="utf-8")
        return result

    def test_claude_block_exit_2_stderr(self):
        r = self._run("claude", {
            "tool_name": "Write",
            "tool_input": {"file_path": ".env", "content": "X=1"}})
        self.assertEqual(r.returncode, 2)
        self.assertIn("BLOCKED", r.stderr)

    def test_claude_allow_exit_0(self):
        r = self._run("claude", {
            "tool_name": "Write",
            "tool_input": {"file_path": "app.py", "content": "print(1)"}})
        self.assertEqual(r.returncode, 0)

    def test_codex_block_json_deny(self):
        r = self._run("codex", {
            "tool_name": "shell",
            "tool_input": {"command": "rm -rf x"}, "cwd": "."})
        self.assertEqual(r.returncode, 0)
        payload = json.loads(r.stdout)
        hook_output = payload["hookSpecificOutput"]
        self.assertEqual(hook_output["hookEventName"], "PreToolUse")
        self.assertEqual(hook_output["permissionDecision"], "deny")
        self.assertIn("BLOCKED", hook_output["permissionDecisionReason"])

    def test_codex_block_cp_into_env(self):
        # Codex shares the rule layer: cp into .env must block on its shell tool.
        r = self._run("codex", {
            "tool_name": "shell",
            "tool_input": {"command": ["cp", "secrets.txt", ".env"]}, "cwd": "."})
        self.assertEqual(r.returncode, 0)
        payload = json.loads(r.stdout)
        hook_output = payload["hookSpecificOutput"]
        self.assertEqual(hook_output["permissionDecision"], "deny")
        self.assertIn("BLOCKED", hook_output["permissionDecisionReason"])
        self.assertIn(".env", hook_output["permissionDecisionReason"])

    def test_codex_block_apply_patch_command_field(self):
        r = self._run("codex", {
            "tool_name": "apply_patch",
            "tool_input": {"command": "*** Begin Patch\n*** Add File: .env.hooktest\n+S=1\n*** End Patch"},
            "cwd": ".",
        })
        self.assertEqual(r.returncode, 0)
        payload = json.loads(r.stdout)
        hook_output = payload["hookSpecificOutput"]
        self.assertEqual(hook_output["permissionDecision"], "deny")
        self.assertIn(".env.hooktest", hook_output["permissionDecisionReason"])

    def test_unknown_ai_allows(self):
        r = self._run("someother", {"tool_name": "Bash",
                                     "tool_input": {"command": "rm -rf x"}})
        self.assertEqual(r.returncode, 0)

    def test_reinject_prints_reminder(self):
        result = subprocess.run(
            [sys.executable, str(RUN_PY), "--reinject"],
            capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Permission gate", result.stdout)

    def test_malformed_json_allows(self):
        result = subprocess.run(
            [sys.executable, str(RUN_PY), "--ai", "claude"],
            input="{not json", capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0)


class TestA8PowerShellHereString(unittest.TestCase):
    """A8: a PowerShell here-string passed to the Bash tool.

    This rule exists because the prose version of it demonstrably does not work.
    A memory records the rule, and a handover warned about this exact fault in
    the exact context it then recurred in, hours later, in the same session. The
    competing pattern is reinforced at the moment of failure: the PowerShell
    tool's own instructions demonstrate `git commit -m @'...'@` for multi-line
    commit messages, so on Windows the wrong template has the strongest and most
    task-specific cue available at precisely the point the mistake is made.

    Detection keys on the *opening delimiter at end of line*, which is what
    PowerShell's here-string syntax requires and what ordinary Bash never has.
    `echo @'hi'` is a legitimate (if odd) Bash argument and must not fire.
    Heredoc bodies are stripped first, so text quoted INTO a command - including
    documentation about this very syntax - is not scanned.
    """

    FAILING_COMMIT = (
        "git commit -m @'\n"
        "Commit message here.\n"
        "Second line.\n"
        "'@"
    )

    def test_blocks_the_real_failing_shape(self):
        block, _ = decision_for(shell_ctx(self.FAILING_COMMIT))
        self.assertIsNotNone(block, "the PowerShell here-string was not blocked")
        self.assertEqual(block.rule, "A8")

    def test_blocks_the_double_quoted_form(self):
        block, _ = decision_for(shell_ctx('cat > f <<X\n@"\ntext\n"@\nX'))
        # The above is inside a heredoc, so it must NOT fire; the real
        # double-quoted case is an argument-position opener:
        self.assertIsNone(block)
        block, _ = decision_for(shell_ctx('git commit -m @"\nmessage\n"@'))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A8")

    def test_block_message_names_the_heredoc_fix(self):
        block, _ = decision_for(shell_ctx(self.FAILING_COMMIT))
        text = block.reason.lower()
        self.assertIn("heredoc", text)
        self.assertIn("powershell", text)

    # --- false-positive guards ---
    def test_allows_the_correct_heredoc_form(self):
        block, _ = decision_for(shell_ctx(
            "git commit -F - <<'EOF'\nCommit message here.\nEOF"))
        self.assertIsNone(block, "the correct POSIX form must not be blocked")

    def test_allows_powershell_syntax_inside_a_heredoc_body(self):
        # Documenting the syntax (as GUARD-COVERAGE-PLAN.md does) is not using it.
        block, _ = decision_for(shell_ctx(
            "python - <<'EOF'\n"
            "text = \"\"\"\n"
            "@'\n"
            "import json\n"
            "'@ | & $py -\n"
            "\"\"\"\n"
            "EOF"))
        self.assertIsNone(block, "a heredoc body must not be scanned")

    def test_allows_an_at_quote_that_is_not_an_opener(self):
        # `@'hi'` on one line is a plain Bash argument, not a here-string opener.
        block, _ = decision_for(shell_ctx("echo @'hi'"))
        self.assertIsNone(block)

    def test_allows_ordinary_commands(self):
        for cmd in ("git status", "python run.py --check",
                    "grep -n \"@\" file.txt", "echo done"):
            block, _ = decision_for(shell_ctx(cmd))
            self.assertIsNone(block, cmd)


class TestPrecommitDocSync(unittest.TestCase):
    """The pre-commit gate: personal data hard-blocks, doc-sync drift is a
    warn-not-block advisory that never stops the commit."""

    def test_advisory_lists_dirs_and_action_required(self):
        warns = [
            ("WARN", "doc-sync",
             "workflows/foo: CONTEXT.md not updated for changes in this directory"),
            ("WARN", "doc-sync",
             "workflows/foo: LOG.md has no entry for this change"),
        ]
        text = run_mod.format_doc_sync_advisory(warns)
        self.assertIn("workflows/foo: CONTEXT.md not updated", text)
        self.assertIn("LOG.md has no entry", text)
        self.assertIn("ACTION REQUIRED", text)
        self.assertIn("NOT blocked", text)

    def test_personal_data_block_skips_doc_sync(self):
        # A personal-data block (exit 1) short-circuits before the advisory.
        with mock.patch.object(run_mod, "_precommit_personal_data",
                               return_value=1), \
             mock.patch.object(run_mod, "_precommit_doc_sync") as ds, \
             mock.patch.object(run_mod, "_git_toplevel", return_value=PROJECT_ROOT):
            code = run_mod.run_precommit()
        self.assertEqual(code, 1)
        ds.assert_not_called()

    def test_clean_personal_data_runs_doc_sync_and_allows(self):
        # No personal data -> run the advisory, but always allow the commit.
        with mock.patch.object(run_mod, "_precommit_personal_data",
                               return_value=0), \
             mock.patch.object(run_mod, "_precommit_doc_sync") as ds, \
             mock.patch.object(run_mod, "_git_toplevel", return_value=PROJECT_ROOT):
            code = run_mod.run_precommit()
        self.assertEqual(code, 0)
        ds.assert_called_once()

    def test_doc_sync_guard_crash_is_swallowed(self):
        # A guard bug must never disrupt commits: _precommit_doc_sync swallows it.
        # FIRE_LOG is redirected even though this path returns before writing, so
        # the rule "every call site redirects it" has no exceptions to reason about.
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(run_mod, "FIRE_LOG",
                               Path(tmp) / "fire-log.jsonl"), \
             mock.patch.object(run_mod, "_load_guard",
                               side_effect=OSError("boom")):
            # Must not raise; returns None (advisory skipped).
            self.assertIsNone(run_mod._precommit_doc_sync(PROJECT_ROOT))


class TestPrecommitDocSyncSeverity(unittest.TestCase):
    """The advisory reports drift and nothing else.

    `_precommit_doc_sync` keeps the guard's WARN findings only, so a DEGRADED
    finding - a component of the guard that could not run, today its
    output-inventory probe on an interpreter without PyYAML - prints nothing at
    commit time. That is deliberate while nothing consumes the inventory
    answers, and it is the documented boundary in this workflow's CONTEXT.md and
    in `workflows/rule-hooks/git-hooks/CONTEXT.md`. Without these tests the
    documentation rests on a code read, so a later change to the filter would
    silently contradict three CONTEXT.md files.
    """

    DEGRADE = ("DEGRADED", "doc-sync",
               "output inventory unavailable - no doc-sync exceptions applied")
    DRIFT = ("WARN", "doc-sync",
             "workflows/foo: CONTEXT.md not updated for changes in this directory")

    def setUp(self):
        # _precommit_doc_sync appends a fire-log record, so the fire-log must be
        # redirected or this suite writes junk samples into the real store the
        # recording-mode observation period counts. These tests predate that
        # write and were silent side-effect-free calls until it was added.
        self.tmp = tempfile.TemporaryDirectory()
        self.fire_log = Path(self.tmp.name) / "fire-log.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def _advisory_for(self, findings):
        """Run the advisory against a stubbed guard; return what it printed."""
        guard = mock.Mock()
        guard.run_check.return_value = list(findings)
        err = io.StringIO()
        with mock.patch.object(run_mod, "_load_guard", return_value=guard), \
             mock.patch.object(run_mod, "FIRE_LOG", self.fire_log), \
             mock.patch.object(run_mod.sys, "stderr", err):
            run_mod._precommit_doc_sync(PROJECT_ROOT)
        return err.getvalue()

    def test_drift_alone_prints_the_advisory(self):
        # The positive control: the stub reaches the real formatter, so a silent
        # run below means the filter dropped the finding, not that the harness
        # never printed anything.
        text = self._advisory_for([self.DRIFT])
        self.assertIn("workflows/foo: CONTEXT.md not updated", text)
        self.assertIn("ACTION REQUIRED", text)

    def test_a_degrade_alone_prints_nothing(self):
        self.assertEqual(self._advisory_for([self.DEGRADE]), "")

    def test_a_degrade_never_masks_real_drift(self):
        text = self._advisory_for([self.DEGRADE, self.DRIFT])
        self.assertIn("workflows/foo: CONTEXT.md not updated", text)
        self.assertNotIn("output inventory unavailable", text)


class TestPrecommitDocSyncRecording(unittest.TestCase):
    """The collection path for doc-sync recording mode (stage 14).

    The guard's CONTEXT-only LOG clause ships switched off, reporting INFO. The
    guard holds no state between runs and its scope is the current change set,
    so a single command run at the end of an observation period would report on
    whatever happened to be uncommitted at that moment and every earlier finding
    would be gone. The pre-commit hook is therefore the collector: it already
    runs the guard over the staged change set on every commit, and already
    writes per-fire records to the gitignored fire-log.

    What these pin is the shape of the evidence rather than the advisory. The
    printed output is unchanged - WARN and nothing else - and the counts are
    read independently of it.
    """

    INFO_C_CASE = ("INFO", "doc-sync",
                   "workflows/foo: LOG.md has no entry for this change "
                   "(newest entry predates the changed files)")
    DEGRADE = ("DEGRADED", "doc-sync",
               "output inventory unavailable - no doc-sync exceptions applied")
    DRIFT = ("WARN", "doc-sync",
             "workflows/foo: CONTEXT.md not updated for changes in this directory")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fire_log = Path(self.tmp.name) / "fire-log.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def _rows(self):
        if not self.fire_log.exists():
            return []
        return [json.loads(line)
                for line in self.fire_log.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def _recording_rows(self):
        return [r for r in self._rows() if r.get("event") == "doc_sync_recording"]

    def _fire(self, findings):
        """One hook fire over a stubbed guard returning `findings`."""
        guard = mock.Mock()
        guard.run_check.return_value = list(findings)
        err = io.StringIO()
        with mock.patch.object(run_mod, "_load_guard", return_value=guard), \
             mock.patch.object(run_mod, "FIRE_LOG", self.fire_log), \
             mock.patch.object(run_mod.sys, "stderr", err):
            run_mod._precommit_doc_sync(PROJECT_ROOT)
        return err.getvalue()

    def test_a_fire_records_both_counts(self):
        # The C case alongside a degraded probe: one row, carrying this fire's
        # INFO count and its DEGRADED count. Both are needed at read time - the
        # first is the measurement, the second is what excludes the sample.
        self._fire([self.INFO_C_CASE, self.DEGRADE])
        rows = self._recording_rows()
        self.assertEqual(len(rows), 1, f"expected exactly one row, got {rows}")
        self.assertEqual(rows[0]["info"], 1)
        self.assertEqual(rows[0]["degraded"], 1)

    def test_a_fire_with_no_findings_records_zeroes(self):
        # The denominator. Recording only the non-zero commits would leave a
        # total that cannot be turned into a rate.
        self._fire([])
        rows = self._recording_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["info"], 0)
        self.assertEqual(rows[0]["degraded"], 0)

    def test_a_degraded_fire_is_recorded_not_dropped(self):
        # Excluded at read time, not at write time, so a thin observation period
        # is visible as an exclusion rather than as a short log.
        self._fire([self.DEGRADE])
        rows = self._recording_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["degraded"], 1)
        self.assertEqual(rows[0]["info"], 0)

    def test_a_gate_blocked_fire_records_nothing(self):
        # run_precommit() returns on a non-zero personal-data code before
        # _precommit_doc_sync is ever called, so a blocked commit writes no row.
        # This pins an absence produced by code this stage does not touch: it is
        # what lets the read treat rows as landed commits rather than as every
        # time the hook was entered.
        with mock.patch.object(run_mod, "_precommit_personal_data",
                               return_value=1), \
             mock.patch.object(run_mod, "FIRE_LOG", self.fire_log), \
             mock.patch.object(run_mod, "_git_toplevel", return_value=PROJECT_ROOT):
            code = run_mod.run_precommit()
        self.assertEqual(code, 1)
        self.assertEqual(self._recording_rows(), [])

    def test_the_advisory_still_prints_warn_only(self):
        # Recording mode adds a silent record, not commit-time output about a
        # clause that is switched off. Drift is present as the positive control
        # that the stub reaches the real formatter.
        text = self._fire([self.INFO_C_CASE, self.DEGRADE, self.DRIFT])
        self.assertIn("workflows/foo: CONTEXT.md not updated", text)
        self.assertNotIn("LOG.md has no entry", text)
        self.assertNotIn("output inventory unavailable", text)

    def test_the_guard_writes_no_file_during_a_fire(self):
        # The guard's read-only contract, asserted rather than assumed, against
        # the REAL guard over a throwaway repository carrying the C case: a
        # CONTEXT-only staged edit whose LOG.md is stale.
        repo = Path(tempfile.mkdtemp(prefix="rulehooks-docsync-"))
        try:
            self._build_context_only_repo(repo)
            before = self._snapshot(repo)
            real_load = run_mod._load_guard
            guard_path = (PROJECT_ROOT / "workflows" / "doc-sync-guard"
                          / "scripts" / "run.py")
            err = io.StringIO()
            with mock.patch.object(
                    run_mod, "_load_guard",
                    side_effect=lambda _p, name: real_load(guard_path, name)), \
                 mock.patch.object(run_mod, "FIRE_LOG", self.fire_log), \
                 mock.patch.object(run_mod.sys, "stderr", err):
                run_mod._precommit_doc_sync(repo)
            self.assertEqual(self._snapshot(repo), before,
                             "the guard modified the tree it was scanning")
            # And the fire really happened, so an unchanged tree is evidence of
            # a read-only guard rather than of a run that never took place.
            self.assertEqual(len(self._recording_rows()), 1)
        finally:
            import shutil
            shutil.rmtree(repo, ignore_errors=True)

    def test_a_failing_fire_log_write_leaves_the_commit_unaffected(self):
        # fire_log() never raises, and _precommit_doc_sync never blocks. A
        # broken recording channel must cost a sample, never a commit.
        unwritable = Path(self.tmp.name) / "no-such-dir" / "fire-log.jsonl"
        guard = mock.Mock()
        guard.run_check.return_value = [self.INFO_C_CASE]
        err = io.StringIO()
        with mock.patch.object(run_mod, "_load_guard", return_value=guard), \
             mock.patch.object(run_mod, "FIRE_LOG", unwritable), \
             mock.patch.object(run_mod.sys, "stderr", err):
            self.assertIsNone(run_mod._precommit_doc_sync(PROJECT_ROOT))
        self.assertFalse(unwritable.exists())
        with mock.patch.object(run_mod, "_precommit_personal_data",
                               return_value=0), \
             mock.patch.object(run_mod, "_load_guard", return_value=guard), \
             mock.patch.object(run_mod, "FIRE_LOG", unwritable), \
             mock.patch.object(run_mod, "_git_toplevel", return_value=PROJECT_ROOT), \
             mock.patch.object(run_mod.sys, "stderr", err):
            self.assertEqual(run_mod.run_precommit(), 0)

    # --- helpers for the read-only control ---
    @staticmethod
    def _build_context_only_repo(repo):
        def git(*args):
            subprocess.run(["git", *args], cwd=repo, check=True,
                           capture_output=True, encoding="utf-8")

        def write(rel, text):
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)

        context = ("# Foo\n\n**Last modified:** {lm}\n\n## Purpose\n{purpose}\n\n"
                   "## Revision History\n{entries}\n")
        git("init", "-q")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "Test")
        write(".gitignore", "**/LOG.md\n")
        write("workflows/CONTEXT.md",
              context.format(lm="2026-07-01", purpose="Container.",
                             entries="- 2026-07-01 - Initial creation."))
        write("workflows/LOG.md", "# Workflows - Log\n")
        write("workflows/foo/CONTEXT.md",
              context.format(lm="2026-07-01", purpose="Foo does a thing.",
                             entries="- 2026-07-01 - Initial creation."))
        write("workflows/foo/LOG.md",
              "[2026-07-01T09:00:00+01:00] | Actor: Biblio | Action: created "
              "| Note: init.\n")
        git("add", "-A")
        git("commit", "-q", "-m", "base", "--no-verify")
        # The C case: a correct CONTEXT-only edit, staged, with a stale LOG.md.
        write("workflows/foo/CONTEXT.md",
              context.format(lm="2026-07-06", purpose="Foo does a thing, v2.",
                             entries="- 2026-07-01 - Initial creation.\n"
                                     "- 2026-07-06 - Purpose clarified."))
        git("add", "workflows/foo/CONTEXT.md")

    def test_fire_log_containment_is_intact(self):
        # FOUR functions in run.py append to the fire-log: evaluate (a rule
        # crash), run_ai (warns, blocks and four error events), main (a fatal),
        # and _precommit_doc_sync (the recording row).
        #
        # This test used to scan for test functions mentioning
        # _precommit_doc_sync or run_precommit and require each to redirect
        # FIRE_LOG. That covered ONE writer of the four and skipped every other
        # test in the file, while its name and comment promised the general
        # guarantee - so the guarantee was never held. Measured on 2026-09-21:
        # 15,827 rows in the real store, 2,096 of them the A6 fixture
        # `rm -rf x` and 1,092 the A7 fixture `cp secrets.txt .env`.
        #
        # The approach changed rather than the filter widening. Enumerating
        # callers of `evaluate` would put a redirect in nearly every test in this
        # file, and the test that forgets is the test that writes. Containment is
        # now done once at module level, which covers all four writers by
        # construction, and this asserts it is intact. The runtime proof that it
        # WORKED, rather than merely being written, is tearDownModule.
        self.assertNotEqual(
            Path(run_mod.FIRE_LOG).resolve(), REAL_FIRE_LOG.resolve(),
            "the in-process channel is not redirected")
        self.assertNotEqual(
            Path(os.environ["BOOK_DRAGON_FIRE_LOG"]).resolve(),
            REAL_FIRE_LOG.resolve(),
            "the subprocess channel is not redirected")

        # Source-level clause for the subprocess channel: a test that launches
        # run.py while building its own env would hand the child an environment
        # with no override in it and silently reopen the channel. The override
        # travels by inheritance, so any such call must derive its env from
        # os.environ. Checked per enclosing function, because the env is
        # normally built into a local and passed as `env=env`.
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        offenders = []
        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef)]:
            dumped = ast.dump(fn)
            if "RUN_PY" not in dumped:
                continue
            builds_env = any(
                kw.arg == "env"
                for call in ast.walk(fn) if isinstance(call, ast.Call)
                for kw in call.keywords)
            if builds_env and "environ" not in dumped:
                offenders.append(fn.name)
        self.assertEqual(
            sorted(offenders), [],
            "these launch run.py with an env that does not derive from "
            "os.environ, so BOOK_DRAGON_FIRE_LOG never reaches the child")

    @staticmethod
    def _snapshot(repo):
        out = {}
        for path in sorted(repo.rglob("*")):
            if ".git" in path.parts or not path.is_file():
                continue
            stat = path.stat()
            out[str(path.relative_to(repo))] = (stat.st_size, stat.st_mtime_ns)
        return out


class TestCwdIndependence(unittest.TestCase):
    """The hook launches run.py by absolute path - settings.json anchors it to
    $CLAUDE_PROJECT_DIR - so evaluation must work regardless of the shell's
    working directory. Guards the fix for the cwd-drift failure, where a
    relative hook script path resolved to a nonexistent file once the Bash cwd
    moved off the project root and blocked every subsequent tool call."""

    def _run_from(self, cwd, event):
        # Mirror the fixed hook: absolute path to run.py, CLAUDE_PROJECT_DIR set
        # (as Claude Code sets it), and an arbitrary working directory.
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(PROJECT_ROOT)}
        return subprocess.run(
            [sys.executable, str(RUN_PY), "--ai", "claude"],
            input=json.dumps(event), capture_output=True, encoding="utf-8",
            cwd=cwd, env=env)

    def test_blocks_from_foreign_cwd(self):
        # A .env write must still block when run.py is launched from a directory
        # that is not the project root - the real-world drift scenario.
        with tempfile.TemporaryDirectory() as foreign:
            r = self._run_from(foreign, {
                "tool_name": "Write",
                "tool_input": {"file_path": ".env", "content": "X=1"}})
        self.assertEqual(r.returncode, 2)
        self.assertIn("BLOCKED", r.stderr)

    def test_allows_from_foreign_cwd(self):
        with tempfile.TemporaryDirectory() as foreign:
            r = self._run_from(foreign, {
                "tool_name": "Write",
                "tool_input": {"file_path": "app.py", "content": "print(1)"}})
        self.assertEqual(r.returncode, 0)

    def test_settings_hooks_anchor_project_dir(self):
        # The shipped hook commands must anchor run.py to $CLAUDE_PROJECT_DIR, or
        # a drifted cwd resolves the relative script path to a nonexistent file
        # and every tool call is blocked. Guards against a revert to a bare path.
        settings = json.loads(
            (PROJECT_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
        commands = [
            hook["command"]
            for entries in settings.get("hooks", {}).values()
            for entry in entries
            for hook in entry.get("hooks", [])
            if hook.get("type") == "command" and hook.get("command")
        ]
        run_py_cmds = [c for c in commands if "rule-hooks/scripts/run.py" in c]
        self.assertTrue(run_py_cmds,
                        "expected rule-hooks hook commands in settings.json")
        for cmd in run_py_cmds:
            self.assertIn("$CLAUDE_PROJECT_DIR", cmd,
                          f"hook command not anchored to $CLAUDE_PROJECT_DIR: {cmd}")

    def _codex_commands(self, *needles):
        commands = []
        for line in CODEX_CONFIG.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if (
                not stripped.startswith("command = ")
                or not all(needle in stripped for needle in needles)
            ):
                continue
            commands.append(ast.literal_eval(stripped.split("=", 1)[1].strip()))
        return commands

    def test_codex_hooks_anchor_git_root(self):
        # Codex has no project-root environment variable, so the shipped hook
        # commands resolve the repo with git before launching the real scripts.
        commands = (
            self._codex_commands("rule-hooks", "run.py")
            + self._codex_commands("session-search", "index.py")
        )
        self.assertEqual(len(commands), 4)
        for cmd in commands:
            self.assertNotIn("python workflows/", cmd,
                             f"hook command reverted to cwd-relative path: {cmd}")
            self.assertIn("git", cmd)
            self.assertIn("rev-parse", cmd)
            self.assertIn("--show-toplevel", cmd)

    def test_codex_hook_blocks_from_project_subdir(self):
        commands = [
            c for c in self._codex_commands("rule-hooks", "run.py")
            if "--ai" in c and "codex" in c
        ]
        self.assertTrue(commands, "expected Codex PreToolUse rule-hook command")
        event = {
            "tool_name": "shell",
            "tool_input": {"command": "rm -rf x"},
            "cwd": str(PROJECT_ROOT / "workflows"),
        }
        r = subprocess.run(
            commands[0], input=json.dumps(event), capture_output=True,
            encoding="utf-8", shell=True, cwd=PROJECT_ROOT / "workflows")
        self.assertEqual(r.returncode, 0)
        payload = json.loads(r.stdout)
        hook_output = payload["hookSpecificOutput"]
        self.assertEqual(hook_output["permissionDecision"], "deny")
        self.assertIn("BLOCKED", hook_output["permissionDecisionReason"])


class TestRoundFive(unittest.TestCase):
    """The ten findings of the closing cross-AI review, 2026-09-07.

    Every fix here WIDENS what a rule catches or NARROWS a false positive, and
    both directions are dangerous in their own way, so each finding ships with
    the control that must not move beside it.
    """

    # --- CR1: destructive SQL fed to a client as input rather than argument ---
    def test_destructive_sql_in_a_heredoc_is_blocked(self):
        block, _ = decision_for(shell_ctx(
            "sqlite3 db <<SQL\nDROP TABLE users;\nSQL"))
        self.assertIsNotNone(block, "a heredoc body is executable input to a "
                                    "SQL client, not inert data")
        self.assertEqual(block.rule, "A6")

    def test_destructive_sql_in_a_quoted_heredoc_is_blocked(self):
        """A quoted delimiter stops the SHELL expanding the body. It does not
        stop the client executing it."""
        block, _ = decision_for(shell_ctx(
            "psql mydb <<'EOF'\nTRUNCATE TABLE audit;\nEOF"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A6")

    def test_destructive_sql_in_a_here_string_is_blocked(self):
        block, _ = decision_for(shell_ctx(
            "sqlite3 db <<< 'DROP TABLE users;'"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A6")

    def test_control_a_heredoc_to_a_non_sql_client_is_not_sql(self):
        """The body is only executable input because of what it is fed TO.
        The same text handed to `cat` is a document being written."""
        block, _ = decision_for(shell_ctx(
            "cat <<TXT\nDROP TABLE is a phrase in this note.\nTXT"))
        self.assertIsNone(block)

    def test_control_a_harmless_heredoc_to_a_sql_client_is_allowed(self):
        block, _ = decision_for(shell_ctx(
            "sqlite3 db <<SQL\nSELECT count(*) FROM users;\nSQL"))
        self.assertIsNone(block)

    def test_control_inline_sql_still_blocks(self):
        block, _ = decision_for(shell_ctx("sqlite3 db 'DROP TABLE users;'"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A6")

    # --- CR2: a comment inside a command substitution ---
    def test_a_paren_inside_a_comment_does_not_close_a_substitution(self):
        """Bash keeps reading past a `)` that sits in a comment and runs the
        line after it. Closing the substitution there left the `rm` inside an
        ordinary quoted word, where no rule looks."""
        block, _ = decision_for(shell_ctx('echo "$(# )\nrm -rf build\n)"'))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A6")

    def test_control_a_hash_inside_a_word_is_not_a_comment(self):
        """The word-start half of the comment rule, and it has to be tested
        with a command that BLOCKS.

        `echo "$(echo a#b)"` is allowed whether `#` opens a comment at a word
        boundary or anywhere at all, so it could not tell the two apart: a
        mutation making `#` a comment everywhere broke no test in the suite.
        With a real command after the `#`, treating it as a comment truncates
        the substitution, the `$(` never closes, the command becomes
        unparseable, and unparseable means allowed - so the block disappears
        and the control can fail."""
        block, _ = decision_for(shell_ctx('echo "$(rm -rf a#b)"'))
        self.assertIsNotNone(block, "`a#b` is one word, so the rm is real")
        self.assertEqual(block.rule, "A6")
        allowed, _ = decision_for(shell_ctx('echo "$(echo a#b)"'))
        self.assertIsNone(allowed)

    def test_control_an_ordinary_substitution_still_runs(self):
        block, _ = decision_for(shell_ctx('echo "$(rm -rf build)"'))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A6")

    # --- CR3: the directory stack is per shell depth ---
    def test_a_subshell_pushd_does_not_move_the_parent_shell(self):
        """`( pushd x )` pushes onto the subshell's stack and that stack dies
        with it, so the later `popd` returns the parent to where it was. One
        shared stack sent the parent somewhere else and a real read of a
        project file resolved in a directory it would never have looked in."""
        block, _ = decision_for(shell_ctx(
            "pushd workflows; ( pushd rule-hooks ); popd; "
            "grep p workflows/CONTEXT.md"))
        self.assertIsNotNone(block, "the grep reads a real file from the root")
        self.assertEqual(block.rule, "A3")

    def test_control_a_plain_pushd_still_resolves_reads(self):
        """The inverse control. Depth-scoping must not stop an ordinary
        `pushd` from moving the shell it really is in."""
        block, _ = decision_for(shell_ctx("pushd workflows && grep p CONTEXT.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_control_a_subshell_cd_still_ends_with_the_subshell(self):
        block, _ = decision_for(shell_ctx(
            "( cd workflows ); grep p README.md"))
        self.assertIsNotNone(block, "the grep runs at the root, where "
                                    "README.md is")
        self.assertEqual(block.rule, "A3")

    # --- CR4: a mode permits writing, asked as a question not a list ---
    def test_a_read_write_mode_is_a_write(self):
        for command in (
                "python -c \"open('README.md', 'r+').write('x')\"",
                "python -c \"Path('README.md').open('r+').write('x')\"",
                "python -c \"open('README.md', 'rb+').write(b'x')\"",
                "python -c \"open('README.md', mode='r+').write('x')\""):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A9")

    def test_control_a_plain_read_mode_is_not_a_write(self):
        for command in (
                "python -c \"open('README.md', 'r').read()\"",
                "python -c \"open('README.md', 'rb').read()\"",
                "python -c \"Path('README.md').open('r').read()\""):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNone(block, command)

    def test_control_a_filename_of_mode_letters_is_not_a_mode(self):
        """The closing quote is what separates a mode from a path that happens
        to be spelled out of mode letters. Without it, any read of a file whose
        name starts with w, a or x read as a write, and that once blocked one
        of this project's own commands."""
        block, _ = decision_for(shell_ctx(
            "python -c \"io.open('workflows/x.md', encoding='utf-8').read()\""))
        self.assertIsNone(block)

    def test_control_a_read_write_mode_on_a_non_document_is_out_of_scope(self):
        block, _ = decision_for(shell_ctx(
            "python -c \"open('notes.json', 'r+').write('x')\""))
        self.assertIsNone(block)

    # --- CR5: a lease does not cancel a plain force ---
    def test_force_with_lease_and_plain_force_is_still_a_force_push(self):
        block, _ = decision_for(shell_ctx("git push --force-with-lease --force"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A6")

    def test_control_force_with_lease_alone_stays_allowed(self):
        for command in ("git push --force-with-lease",
                        "git push --force-with-lease=main:abc123"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNone(block, command)

    # --- CR6: arithmetic expansion is not command substitution ---
    def test_arithmetic_expansion_is_not_a_command(self):
        """`$((...))` is arithmetic syntax. Bash rejects `$((rm -rf build))`
        as a syntax error rather than running it, so blocking it refused a
        command that could not do the thing it was blocked for.

        Quoted AND unquoted, because the scanner reads those on two separate
        code paths and only one of them was covered. Mutation testing found
        that: putting the defect back in the unquoted branch broke no test at
        all, because every case here had been written with quotes."""
        for command in ('echo "$((rm -rf build))"',
                        'echo $((rm -rf build))',
                        'echo "$((cat < README.md))"',
                        'echo $((cat < README.md))',
                        'echo "$((1 > 2))"',
                        'echo $((1 > 2))',
                        'echo $((1 > 2)) && echo done'):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNone(block, command)

    def test_control_arithmetic_does_not_swallow_what_follows_it(self):
        """The inverse. Skipping to the end of the expansion must land in the
        right place: landing past it would hide the rest of the command, which
        is the worse failure of the two."""
        block, _ = decision_for(shell_ctx('echo $((1+2)) && rm -rf build'))
        self.assertIsNotNone(block, "the rm after the expansion still runs")
        self.assertEqual(block.rule, "A6")

    def test_control_a_left_shift_still_opens_no_heredoc(self):
        block, _ = decision_for(shell_ctx('echo "$((1<<2))"; echo done'))
        self.assertIsNone(block)

    # --- CR10: a successful cd ends its || chain ---
    def test_a_successful_cd_stops_the_or_branch(self):
        block, _ = decision_for(shell_ctx("cd workflows || grep p ../README.md"))
        self.assertIsNone(block, "the grep does not run when the cd succeeds")

    def test_control_a_failed_cd_still_runs_the_or_branch(self):
        block, _ = decision_for(shell_ctx(
            "cd nowhere-at-all || grep p README.md"))
        self.assertIsNotNone(block, "the cd fails, so the grep really runs")
        self.assertEqual(block.rule, "A3")

    def test_control_a_successful_cd_still_runs_the_and_branch(self):
        block, _ = decision_for(shell_ctx(
            "cd workflows && grep p rule-hooks/CONTEXT.md"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A3")

    def test_control_an_unknown_status_decides_neither_branch(self):
        """Only a `cd` this rule actually modelled settles a branch. Any other
        command's exit status is unknown, and an unknown status is not a
        success any more than it is a failure, so the `||` branch still runs
        and a read inside it still blocks.

        Written against a non-`cd` command deliberately. The obvious version,
        `cd || grep p README.md`, cannot fail: `cd` with no operand also makes
        the working directory unknown, so the read resolves to nothing and the
        command is allowed whatever the branch logic does. That is the
        name-a-file-that-exists-nowhere trap this suite already records under
        Known Issues, and it caught me writing this control."""
        block, _ = decision_for(shell_ctx("build-something || grep p README.md"))
        self.assertIsNotNone(block, "unknown status must not skip the branch")
        self.assertEqual(block.rule, "A3")


class TestRoundSix(unittest.TestCase):
    """The findings of the review of round five's repairs.

    Recorded as its own class because of what it is: five of these six are
    defects in the previous round's FIXES rather than in the original code, and
    the worst of them was introduced by the refactor that was meant to prevent
    exactly its defect class.
    """

    def test_arithmetic_expansion_is_consumed_whole(self):
        """`_arithmetic_end` returned the index OF the closing paren instead of
        past it, leaving a stray `)` in the stream. That one character closed
        the enclosing `$(...)` early and everything after it became inert text,
        so a command hidden there reached no rule at all.

        Asserted on the helper as well as through the rules, because this is a
        one-character boundary error and the helper is where it is legible."""
        self.assertEqual(core._arithmetic_end("$((1+2))", 1), 8)
        self.assertEqual(core._arithmetic_end("$(( (1+2) * 3 ))", 1), 16)

    def test_arithmetic_does_not_hide_a_following_command(self):
        """All six shell rules, because all six were switched off by it. The
        earlier control used `echo $((1+2)) && rm -rf build`, which passes even
        with the defect present: at top level the stray `)` becomes a grouping
        token and `&&` still separates the segments. It only bites inside a
        substitution, and the control had been written in the shape that
        survives."""
        for rule, command in (
                ("A6", 'echo "$(: $((0)) ; rm -rf build)"'),
                ("A6", 'echo "$(: $((0)) ; git push --force)"'),
                ("A3", 'echo "$(: $((0)) ; cat README.md)"'),
                ("A4", 'echo "$(: $((0)) ; echo hi > LOG.md)"'),
                ("A7", 'echo "$(: $((0)) ; echo hi > .env)"')):
            with self.subTest(rule=rule):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, rule)

    def test_arithmetic_does_not_corrupt_subshell_depth(self):
        block, _ = decision_for(shell_ctx(
            "( echo $((1)) ; cd workflows ) ; grep TODO README.md"))
        self.assertIsNotNone(block, "the grep runs at the root, where "
                                    "README.md is; the subshell cd does not "
                                    "reach it")
        self.assertEqual(block.rule, "A3")

    def test_ansi_c_quoting_inside_a_substitution_is_read(self):
        """`$'...'` lets a backslash escape the closing quote. Without that
        branch the quote closed early, the paren was never found, and the
        command became unparseable - which means allowed, for every rule at
        once. The third instance of one shell fact reaching some of this
        module's character-walkers and not others."""
        for rule, command in (("A6", "echo \"$(echo $'\\'' ; rm -rf build)\""),
                              ("A3", "echo \"$(echo $'\\'' ; cat README.md)\"")):
            with self.subTest(rule=rule):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, rule)

    def test_control_a_bare_ansi_c_word_still_parses(self):
        block, warns = decision_for(shell_ctx("grep $'\\r' README.md"))
        self.assertIsNone(block, "the byte-level allowance still applies")
        self.assertTrue(warns)

    def test_popd_on_an_empty_stack_does_not_move_the_shell(self):
        """Bash's `popd` with nothing to pop fails and leaves the shell where
        it was. Modelling it as an unknown directory resolved no operand, and
        an unresolved operand is allowed - so a bare `popd` in front of any
        command was a one-word bypass of A3."""
        for command in ("popd ; grep TODO README.md",
                        "( pushd workflows ) ; popd ; grep TODO README.md",
                        "pushd workflows ; popd ; popd ; grep TODO README.md"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A3")

    def test_a_cd_the_shell_expands_does_not_kill_its_chain(self):
        """`cd $PWD` succeeds in any real shell. Resolving the literal string
        finds no directory, and calling that a FAILURE killed the rest of the
        `&&` chain, so everything after it went unexamined - including a read
        naming an absolute path, which needs no working directory at all.

        The absolute path is the point of this test rather than an accident of
        it. A previous version used one without saying why, which made it read
        as a test of the directory model; it is not, because `_resolved_paths`
        short-circuits on an absolute path before the directory is consulted.
        Named here so nobody 'tidies' it into a relative path and quietly
        turns this into the test below."""
        absolute = (PROJECT_ROOT / "README.md").as_posix()
        for command in (f"cd $PWD && grep TODO {absolute}",
                        f'cd "$(pwd)" && grep TODO {absolute}',
                        f"cd $REPO && grep TODO {absolute}"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A3")

    def test_a_relative_read_after_an_expanded_cd_is_a_known_gap(self):
        """The limit of the fix above, asserted rather than left implied.

        After `cd $PWD` the working directory is unknown, so a RELATIVE operand
        resolves to nothing and the read is allowed. That is a real gap and it
        is deliberate: the alternative is to assume the `cd` did not move the
        shell, which catches this and wrongly blocks a read after a `cd` that
        really did move. The user chose to keep the gap on 2026-09-07, on the
        grounds that a guard refusing honest work is the more expensive
        failure.

        This test exists so the gap cannot close by accident and go unnoticed,
        and so a future reader meets the decision rather than a surprise."""
        block, _ = decision_for(shell_ctx("cd $PWD ; cat README.md"))
        self.assertIsNone(block, "documented gap: an unknown directory "
                                 "resolves no relative operand")
        control, _ = decision_for(shell_ctx("cat README.md"))
        self.assertIsNotNone(control, "the same read without the cd must "
                                      "still block, or this proves nothing")

    def test_control_a_genuinely_failed_cd_still_ends_its_chain(self):
        block, _ = decision_for(shell_ctx(
            "cd nowhere-at-all && grep TODO README.md"))
        self.assertIsNone(block, "the cd really fails, so the grep never runs")

    def test_write_modes_are_order_independent(self):
        """Python parses mode characters in any order. Writing the question as
        a regex re-encoded the assumption that the r/w/x/a letter comes first,
        which is the same list-instead-of-a-question defect in a different
        notation, and it missed seven writable modes."""
        for mode in ("w", "a", "x", "r+", "bw", "+w", "+r", "br+", "b+r",
                     "ba+", "t+r", "w+b"):
            with self.subTest(mode=mode):
                self.assertTrue(a9_permits(mode), mode)
                block, _ = decision_for(shell_ctx(
                    f"python -c \"open('README.md', '{mode}').write('x')\""))
                self.assertIsNotNone(block, mode)
                self.assertEqual(block.rule, "A9")

    def test_control_read_only_and_non_modes_are_not_writes(self):
        for mode in ("r", "rb", "rt", "U"):
            with self.subTest(mode=mode):
                self.assertFalse(a9_permits(mode), mode)
        # Not modes at all: more than one base letter means it is a word.
        for word in ("war", "wax", "rw", "arw"):
            with self.subTest(word=word):
                self.assertFalse(a9_permits(word), word)

    def test_sql_piped_into_a_client_is_blocked(self):
        """The third ordinary way to feed a client, alongside an argument and a
        heredoc. The statements are in the command text, which is the reason a
        heredoc body is read in the first place."""
        for command in (
                "echo 'DROP TABLE users;' | sqlite3 db.sqlite",
                "echo 'DROP TABLE users;' | tr -d x | sqlite3 db.sqlite",
                "cat <<SQL | sqlite3 db.sqlite\nDROP TABLE users;\nSQL"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A6")

    def test_control_a_pipeline_with_no_sql_client_is_allowed(self):
        for command in ("echo 'DROP TABLE users;' | grep DROP",
                        "echo 'SELECT 1;' | sqlite3 db.sqlite",
                        "cat <<TXT\nDROP TABLE is a phrase.\nTXT"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNone(block, command)

    def test_a_pipeline_popd_does_not_move_the_parent_shell(self):
        """The other half of the pipeline guard. It was added to `pushd` and
        not to `popd`, so `pushd x ; popd | cat` popped the parent's stack and
        moved the parent back - exempting every read after it. The same
        one-of-two-parallel-cases shape as the directory stack itself."""
        block, _ = decision_for(shell_ctx(
            "pushd workflows ; popd | cat ; cat rule-hooks/CONTEXT.md"))
        self.assertIsNotNone(block, "the parent shell is still in workflows, "
                                    "where rule-hooks/CONTEXT.md really is")
        self.assertEqual(block.rule, "A3")

    def test_a_failed_popd_ends_its_and_chain(self):
        """`popd` with nothing to pop returns non-zero, so bash never runs the
        `&&` branch. Leaving the directory unchanged was right; treating the
        command as having succeeded was not."""
        block, _ = decision_for(shell_ctx("popd && cat README.md"))
        self.assertIsNone(block, "bash never reaches the cat")

    def test_a_cd_at_the_end_of_a_pipeline_does_settle_the_branch(self):
        """A pipeline's exit status is its LAST stage's, so a `cd` standing at
        the end of one settles the `||` that follows even though its directory
        change dies with the child.

        This test previously asserted the opposite and was wrong. Its own
        stated reason - that the status belongs to the last stage - is the
        argument that the branch IS settled, and a reviewer confirmed against
        real bash that `echo hi | cd /etc || echo OR-RAN` prints nothing. It
        pinned a false block, which is the direction this rule set twice
        states is the more expensive failure."""
        block, _ = decision_for(shell_ctx(
            "echo hi | cd workflows || grep TODO README.md"))
        self.assertIsNone(block, "the cd succeeds, so the pipeline succeeds, "
                                 "so bash never runs the grep")

    def test_a_cd_in_the_middle_of_a_pipeline_settles_nothing(self):
        """The control for the case above. A `cd` that still feeds another
        stage is not the last one, so the pipeline's status is not its own and
        the branch after it is not settled."""
        block, _ = decision_for(shell_ctx(
            "cd workflows | cat || grep TODO README.md"))
        self.assertIsNotNone(block, "the pipeline's status is cat's, not the "
                                    "cd's, so the || branch is examined")
        self.assertEqual(block.rule, "A3")

    def test_an_escaped_character_is_not_a_word_break_before_a_hash(self):
        """`echo \\>#a` prints `>#a`: the `>` is escaped, so it is an ordinary
        literal and the `#` continues the word. Reading it as a comment
        swallowed the closing paren of the enclosing substitution, which made
        the whole command unparseable - and unparseable means allowed, for
        every rule at once."""
        for command in ('echo "$(echo \\>#a ; rm -rf build)"',
                        'echo "$(echo \\<#a ; rm -rf build)"'):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A6")

    def test_control_an_unescaped_redirection_still_opens_a_comment(self):
        """The inverse. `>` and `<` are genuine word breaks when not escaped,
        which is why they were added; the fix must not take that back."""
        self.assertTrue(core._opens_comment("echo hi >#tmp", 9))
        self.assertFalse(core._opens_comment("echo hi \\>#tmp", 10))

    def test_a_sql_client_as_the_pipeline_source_is_not_input(self):
        """Text flowing OUT of a client is not text fed INTO it. Auditing a
        dump for DROP statements is ordinary read-only work, and reading the
        whole pipeline blocked it."""
        for command in ("sqlite3 db.sqlite .dump | grep -c 'DROP TABLE'",
                        "psql -c '\\dt' mydb | grep 'DROP TABLE'"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNone(block, command)

    def test_control_text_piped_into_a_client_still_blocks(self):
        block, _ = decision_for(shell_ctx(
            "git log | grep 'DROP TABLE' | sqlite3 db"))
        self.assertIsNotNone(block, "matching lines really are fed to the "
                                    "client")
        self.assertEqual(block.rule, "A6")

    def test_a_pipeline_pushd_does_not_move_the_parent_stack(self):
        block, _ = decision_for(shell_ctx(
            "pushd workflows | cat ; cd workflows ; popd ; "
            "grep TODO README.md"))
        self.assertIsNone(block, "the pipeline pushd saved nothing in the "
                                 "parent, so the popd leaves the shell in "
                                 "workflows, where README.md is not")


class TestRoundEight(unittest.TestCase):
    """Findings of the review of round seven's repairs.

    **Every backslash here is built with `chr(92)` rather than written as a
    literal.** A run of backslashes loses characters to any shell, heredoc or
    Windows command-line re-parse it passes through, and the PARITY of the run
    is the entire question these tests ask - so a case that arrives one
    backslash shorter tests the opposite thing while still looking correct. A
    reviewer hit this and got a false clean result from it, and so did the pass
    that verified this fix.
    """

    BS = chr(92)

    def test_comment_escaping_depends_on_the_backslash_runs_parity(self):
        """Only an ODD run escapes the character in front of the `#`; each pair
        is one escaped backslash and escapes nothing further.

        The first version asked whether the character two back was a backslash,
        which is a fixed two-character question about a run of unbounded
        length. Right for one, wrong for two."""
        for run in range(7):
            text = "x " + (self.BS * run) + " #"
            with self.subTest(backslashes=run):
                self.assertEqual(core._opens_comment(text, len(text) - 1),
                                 run % 2 == 0)

    def test_an_even_backslash_run_does_not_fabricate_a_heredoc(self):
        """With an even run the `#` really is a comment, so the `<<EOF` inside
        it opens nothing and the next line is a command in its own right.
        Missing that fabricated a heredoc whose body ran to the end of the
        input looking for a terminator that never came, hiding every command
        after it from all six rules."""
        for run in (2, 4):
            command = "echo a " + (self.BS * run) + " # <<EOF\nrm -rf build"
            with self.subTest(backslashes=run):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A6")

    def test_control_an_odd_backslash_run_still_opens_a_real_heredoc(self):
        """The fix must not overshoot. With an odd run the space IS escaped, so
        `#` continues the word, the heredoc genuinely opens, and the next line
        is its body rather than a command. Bash agrees, so allowing it is
        correct rather than a miss."""
        for run in (1, 3):
            command = "echo a " + (self.BS * run) + " # <<EOF\nrm -rf build"
            with self.subTest(backslashes=run):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNone(block, command)

    def test_bare_pushd_fails_and_leaves_the_shell_where_it_is(self):
        """`pushd` with no operand is not `cd` with no operand. `cd` goes to a
        home directory, which is unknowable but real; `pushd` swaps the top two
        stack entries and, with nothing on the stack, fails and stays put.
        Treating it as unknowable blanked the directory, and a blank directory
        resolves no relative operand - the same one-word bypass just fixed for
        `popd`, left standing on the other half of the pair."""
        block, _ = decision_for(shell_ctx("pushd ; cat README.md"))
        self.assertIsNotNone(block, "the read happens at the project root")
        self.assertEqual(block.rule, "A3")

    def test_a_failed_pushd_pushes_nothing(self):
        """State changed before its status was known: the push happened before
        the move was attempted, so a failed `pushd` left a spurious entry for a
        later `popd` to return to. Here the `popd` should fail and kill the
        chain, so the read never runs."""
        block, _ = decision_for(shell_ctx(
            "pushd nowhere_zzz ; popd && grep pattern README.md"))
        self.assertIsNone(block, "bash never reaches the grep")

    def test_every_sql_client_in_a_pipeline_is_examined(self):
        """Taking the first client left the upstream empty whenever a pipeline
        BEGAN with one - which the idiomatic drop-every-table one-liner does."""
        for command in (
                "sqlite3 db .tables | awk '{print \"DROP TABLE \" $1 \";\"}' "
                "| sqlite3 db",
                "sqlite3 a.db .dump | sqlite3 b.db <<SQL\n"
                "DROP TABLE users;\nSQL"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A6")

    def test_control_two_harmless_clients_are_allowed(self):
        for command in ("sqlite3 a.db .dump | sqlite3 b.db",
                        "sqlite3 db.sqlite .dump | grep -c 'DROP TABLE'"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNone(block, command)


class TestSharedParseIsSafe(unittest.TestCase):
    """The parse is memoised so one hook fire pays for one parse instead of
    five. Sharing is only safe while nothing downstream can edit what it was
    handed, so that is tested rather than asserted in a comment."""

    def test_shared_parse_does_not_leak_mutation(self):
        """`split_segments_detailed` mutates `segment.depth` during its own
        recursion. If two callers were handed the same Segment objects, the
        first rule's parse would change the depths the second rule sees - and
        `depth` is precisely what A3 uses to tell a subshell from its parent.
        Caching the segment builder would have re-created CR3 by another
        route, so the cache sits on the scanners underneath it."""
        command = "echo hi && (cd workflows && grep p CONTEXT.md)"
        expected = [s.depth for s in core.split_segments_detailed(command)]
        self.assertTrue(expected, "the fixture must produce segments")

        first = core.split_segments_detailed(command)
        for segment in first:
            segment.depth += 99

        third = core.split_segments_detailed(command)
        # Compared against a value captured BEFORE the mutation, not against
        # another live call. Comparing two post-mutation calls is what the
        # first version of this test did, and under the very cache it forbids
        # (memoising the segment builder) those two calls return the SAME list
        # object - so the assertion compared a list to itself and passed with
        # the depths visibly corrupted. The test that proves a property must
        # hold a copy of that property from before the thing that could break
        # it.
        self.assertEqual(expected, [s.depth for s in third],
                         "a caller editing its segments changed a later "
                         "caller's parse")

    def test_a_substitution_list_is_not_aliased_into_the_parse(self):
        """The other half of "nothing downstream can edit what it was handed".

        `split_segments_detailed` copies the cached substitution list before
        extending it. Aliasing it instead grows the cached parse on every call,
        so the same command yields more segments the second time it is seen.

        The command needs BOTH a `$(...)`, so the cached substitution list is
        non-empty, AND a `sh -c`, so there is something for the extend to
        append. A first version of this test had only substitutions: nothing
        was ever appended, so aliasing changed nothing and the test could not
        fail - the same defect the reviewer had just found twice elsewhere,
        committed while writing the test for it.
        """
        command = 'echo "$(cat README.md)" && sh -c "echo hi"'
        first = len(core.split_segments_detailed(command))
        for _ in range(5):
            core.split_segments_detailed(command)
        self.assertEqual(first, len(core.split_segments_detailed(command)),
                         "the parse grew across repeated calls, so a cached "
                         "list is being mutated in place")

    def test_tokenize_hands_out_a_copy(self):
        command = "grep -rn TODO README.md"
        tokens = core.tokenize(command)
        tokens.append("junk")
        self.assertNotIn("junk", [t for t in core.tokenize(command)],
                         "a caller appending to the token list edited the "
                         "cached parse for everyone after it")

    def test_the_cache_keys_on_the_exact_text(self):
        """The scanner is called recursively on different strings - the outer
        command, each `$(...)`, each `sh -c` payload. A cache that confused
        them would be a silent false-negative machine."""
        self.assertIsNotNone(core.split_segments("rm -rf build"))
        outer = core.split_segments("echo safe")
        inner = core.split_segments("rm -rf build")
        self.assertEqual([a[0] for a in outer], ["echo"])
        self.assertEqual([a[0] for a in inner], ["rm"])


class TestHookCostEndToEnd(unittest.TestCase):
    """A hook that does not answer within its budget has ALLOWED the command,
    so cost is a correctness property and not a comfort.

    **There is deliberately no wall-clock assertion here any more.** Three were
    tried: half the budget, then 70% of it, then the budget itself. All three
    were flaky on correct code, the last one observed failing at 5.07s and
    5.97s against a 5.0s line purely because the machine was busy. The measured
    spread on identical code is roughly four-fold, and no threshold inside a
    four-fold range is stable - so each attempt produced a test that fails at
    random, which teaches people to rerun the suite rather than to read it.

    What the cost regression actually is, is deterministic: the command being
    re-read once per rule instead of once. That is measured below in characters
    walked, which does not depend on the machine, the load, or the clock. The
    seconds are left to the operator: the real budget lives in
    `.claude/settings.json` and a genuine breach shows up as a hook that times
    out, which is visible in the fire-log.
    """

    def test_the_whole_command_is_scanned_once(self):
        """The deterministic form of the budget test, and the one that should
        be trusted.

        Two previous attempts asserted a wall-clock ceiling, and both were
        wrong in the same way: a timing assertion measures the machine as much
        as the code. The second was tuned to 3.5 seconds after measurement and
        a reviewer still observed it failing five times in ten runs, once at
        6.98 seconds - over the real hook budget, on correct code, purely from
        load. A test that fails at random teaches people to rerun it, which is
        worse than not having it.

        What the cost regression actually is, is deterministic: the command
        being re-lexed once per rule instead of once. So count that. The number
        of characters the scanner walks per fire should be about the length of
        the command, not five times it, and no clock is involved.
        """
        command = "echo " + ("a && b | c ; " * 2_000)
        walked = {}
        for name in ("_scan", "_split_heredocs"):
            walked[name] = self._characters_walked(name, command)
        for name, total in walked.items():
            with self.subTest(scanner=name):
                self.assertTrue(total, f"{name} was never reached")
                self.assertLessEqual(
                    total, len(command) * 2,
                    f"{name} walked {total} characters for a "
                    f"{len(command)}-character command, so it is running per "
                    f"rule rather than once")

    def _characters_walked(self, name, command):
        """Total characters *name* walks during one whole fire.

        **The wrapper is rebuilt only if production actually has one**, which
        is the whole point and was the defect in the first version of this
        test. That version did `lru_cache(...)(counting)` unconditionally, so a
        cache was present during the test whether or not production had one -
        and the test therefore passed with the memoisation removed, which is
        precisely the regression its docstring says it exists to catch. It only
        ever detected a re-parse of *different* text.

        Both scanners are measured. Only `_scan` was, and a mutation removing
        `_split_heredocs`' cache broke no test in the suite while costing 15
        heredoc splits per fire instead of 1 - the two are documented together
        as one fix and only one of them was defended.
        """
        target = getattr(core, name)
        cached = hasattr(target, "cache_info")
        inner = target.__wrapped__ if cached else target
        seen = []

        def counting(text):
            seen.append(len(text))
            return inner(text)

        replacement = core.lru_cache(maxsize=16)(counting) if cached \
            else counting
        if cached:
            core._scan.cache_clear()
            core._split_heredocs.cache_clear()
        with mock.patch.object(core, name, replacement):
            decision_for(shell_ctx(command))
        return sum(seen)

    def test_the_command_is_parsed_once_not_once_per_rule(self):
        """The mechanism behind the budget test, pinned separately so a
        regression says WHICH thing broke. Seven shell rules each ask core for
        their own view; core must answer the second and later asks from the
        first parse."""
        core._scan.cache_clear()
        core._split_heredocs.cache_clear()
        decision_for(shell_ctx("grep -rn TODO workflows/rule-hooks/scripts"))
        self.assertEqual(core._scan.cache_info().misses, 1,
                         "the command was lexed more than once")
        self.assertGreater(core._scan.cache_info().hits, 0,
                           "nothing reused the parse, so the cache is not "
                           "actually in the path")


class TestRoundNine(unittest.TestCase):
    """The 2026-09-11 external review, eight findings.

    The first of them is the previous round's repair. CR6 stopped the reader
    descending into arithmetic expansion, which was right - `$((rm -rf build))`
    is a syntax error rather than a command bash runs - and it went one step too
    far, because bash DOES run a command substitution written inside arithmetic.
    That is the seventh consecutive round to find its worst defect in the last
    round's fix, and it is the shape this file keeps recording: a shell fact
    handled at one level and not in the sibling construct where code still runs.

    Six of the eight widen what a rule catches and two narrow an over-block, so
    both directions ship with the control that must not move.
    """

    # ---- C9-1: arithmetic expansion hid a real command substitution --------
    def test_a_substitution_inside_arithmetic_is_still_a_command(self):
        """`echo $(( $(rm -rf build) + 1 ))` runs the `rm`. Proven in Git Bash:
        `echo $(( $(printf 5 > marker; printf 7) + 1 ))` prints 8 and leaves the
        marker file behind, so the substitution really executed. The reader
        skipped the whole `$((...))` body, so five rules never saw it."""
        for command, rule in (
                ("echo $(( $(rm -rf build) + 1 ))", "A6"),
                ("echo $(( $(cat README.md) + 1 ))", "A3"),
                ("echo $(( $(printf X > .env) + 1 ))", "A7"),
                ("echo $(( `rm -rf build` + 1 ))", "A6"),
                ('echo "$(( $(rm -rf build) + 1 ))"', "A6"),
        ):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(
                    block, f"a command substitution bash executes went unseen: "
                           f"{command}")
                self.assertEqual(block.rule, rule, command)

    def test_arithmetic_itself_is_still_not_a_command(self):
        """CR6's control, and it must not move. The expression inside `$(( ))`
        is arithmetic syntax: bash rejects `$((rm -rf build))` rather than
        running it, and `1<<2` is a left shift rather than a heredoc opener."""
        for command in ('echo "$((rm -rf build))"',
                        'echo "$((cat < README.md))"',
                        'echo "$((1 > .env))"',
                        "echo $((1<<2))",
                        "echo $((1<<2)) ; echo done"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNone(block, command)

    # ---- C9-2: A9 missed ordinary Path.open write spellings ---------------
    def test_a9_reads_every_ordinary_path_open_write(self):
        """Only the bare one-argument `.open("w")` was recognised. The keyword
        form and the form carrying an encoding are the spellings this project's
        own rules require, so the two most likely ways to write the call were
        the two it missed."""
        for code in ('Path("README.md").open("w")',
                     'Path("README.md").open(mode="w")',
                     'Path("README.md").open("w", encoding="utf-8")',
                     'Path("README.md").open(mode="w", encoding="utf-8")',
                     'Path("README.md").open("a", encoding="utf-8")',
                     'Path("README.md").open("r+")'):
            with self.subTest(code=code):
                block, _ = decision_for(
                    shell_ctx(f"python -c 'from pathlib import Path; {code}'"))
                self.assertIsNotNone(block, code)
                self.assertEqual(block.rule, "A9", code)

    def test_a9_still_allows_a_read_and_a_non_document(self):
        """The controls for the widening above. A read mode is not a write; a
        `.json` is not a document; and `io.open(path, encoding=...)` must not be
        read as `.open(mode)` just because the path begins with a `w`, which is
        the false block this pattern caused once already."""
        for code in ('Path("README.md").open("r")',
                     'Path("README.md").open(mode="r", encoding="utf-8")',
                     'Path("README.md").open("rb")',
                     'Path("notes.json").open(mode="w")',
                     "io.open('workflows/x.md', encoding='utf-8')",
                     "io.open('workflows/x.md', 'r', encoding='utf-8')"):
            with self.subTest(code=code):
                block, _ = decision_for(
                    shell_ctx(f"python -c 'import io; from pathlib import "
                              f"Path; {code}'"))
                self.assertIsNone(block, code)

    # ---- C9-3: ruby File.open defaults to READ ----------------------------
    def test_a9_does_not_treat_a_ruby_read_as_a_write(self):
        """`File.open(path)` and `File.open(path, "r")` are reads: ruby's
        default mode is read, exactly as python's is. A9 treated every
        `File.open` as a write, so the ordinary way of reading a document in a
        one-liner was blocked - and an over-block on a rule with a known
        over-block surface is what gets a hook routed around."""
        for code in ('File.open("README.md") { |f| puts f.read }',
                     'File.open("README.md", "r") { |f| puts f.read }',
                     'File.open("README.md", "rb").read'):
            with self.subTest(code=code):
                block, _ = decision_for(shell_ctx(f"ruby -e '{code}'"))
                self.assertIsNone(block, code)

    def test_a9_still_blocks_a_ruby_write(self):
        """The control for the narrowing above: the write modes and
        `File.write` still block, so the route A9 exists to close is open in
        neither direction."""
        for code in ('File.open("README.md", "w") { |f| f.write("x") }',
                     'File.open("README.md", "a") { |f| f.write("x") }',
                     'File.open("README.md", "r+") { |f| f.write("x") }',
                     'File.write("README.md", "x")'):
            with self.subTest(code=code):
                block, _ = decision_for(shell_ctx(f"ruby -e '{code}'"))
                self.assertIsNotNone(block, code)
                self.assertEqual(block.rule, "A9", code)

    # ---- C9-4: a directory verb's exit status settles the chain ------------
    def test_a_successful_directory_verb_satisfies_a_following_or(self):
        """Bash does not run the right side of `||` after a successful left
        side. `cd` had been taught that and the directory-stack verbs had not,
        which is the fix-one-of-two-symmetrical-cases shape this rule keeps
        producing - the third time in this file alone."""
        for command in (
                "pushd workflows ; popd || grep TODO workflows/CONTEXT.md",
                "pushd workflows ; echo hi | popd || grep TODO CONTEXT.md",
                "pushd workflows ; echo hi | pushd || grep TODO CONTEXT.md",
                "cd workflows || grep TODO ../README.md",
        ):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNone(block, command)

    def test_a_failing_directory_verb_still_leaves_the_read_visible(self):
        """The controls. A `popd` with nothing on the stack really does fail,
        so the `||` branch really does run; a `&&` after a successful `pushd`
        really does run; and `popd` really does return the shell to where it
        was, so the read after it resolves at the root."""
        for command in ("popd || grep TODO README.md",
                        "pushd workflows && grep TODO CONTEXT.md",
                        "pushd workflows ; popd ; grep TODO README.md"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A3", command)

    # ---- C9-5: upstream ARGV is not upstream OUTPUT -----------------------
    def test_a6_ignores_a_matcher_that_emits_something_else(self):
        """Half of C9-5, accepted. A matcher asked for a count, a filename or
        silence does not emit the text it matched, so `grep -c "DROP TABLE"
        dump.sql | sqlite3 db` hands the client a number while being read as
        though it had handed it the statement."""
        for command in (
                'grep -c "DROP TABLE" no-such-file.sql | sqlite3 db',
                'grep -l "DROP TABLE" no-such-file.sql | sqlite3 db',
                'grep -q "DROP TABLE" no-such-file.sql | sqlite3 db',
                'grep --count "DROP TABLE" no-such-file.sql | sqlite3 db',
        ):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNone(block, command)

    def test_a6_still_reads_a_matcher_pattern_as_what_it_emits(self):
        """The other half of C9-5, REJECTED on the merits, pinned so a later
        round does not re-report it as a defect.

        The finding said an upstream argument is not upstream output, which is
        true in general and false for a matcher: `git log | grep 'DROP TABLE' |
        sqlite3 db` feeds the client precisely the lines that contain a DROP,
        and the only place that statement appears in the command text is grep's
        own pattern. Round six pinned that case for exactly this reason.

        `echo SELECT_1 | grep "DROP TABLE" | sqlite3 db` is the finding's own
        specimen and it stays blocked. Deciding it correctly would mean
        modelling what `echo` emits and then whether grep's pattern matches it;
        the hook reads commands, and for a destructive-command rule the fail
        direction is the block. A `-c` on the same command makes it allowed,
        which is the difference the fix above turns on.
        """
        for command in ("git log | grep 'DROP TABLE' | sqlite3 db",
                        'echo SELECT_1 | grep "DROP TABLE" | sqlite3 db'):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A6", command)

    def test_a6_still_reads_what_really_flows_into_a_client(self):
        """The controls for the narrowing. A stage that PRINTS its argument
        text still counts, including the idiomatic drop-every-table one-liner
        where the DROP is written inside an `awk` program, and a client's own
        argument is unaffected."""
        for command in (
                """echo "DROP TABLE users;" | sqlite3 db""",
                """sqlite3 db .tables | awk '{print "DROP TABLE " $1 ";"}' | sqlite3 db""",
                """printf 'DROP TABLE users;' | sqlite3 db""",
                """sqlite3 db "DROP TABLE users;" """,
        ):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A6", command)

    def test_a6_still_allows_a_client_that_is_the_source(self):
        """The pre-existing control: a client at the head of a pipeline is
        producing a dump, not being fed one.

        **Written without `-c`, and that matters.** The first version of this
        test used `grep -c`, which this round's own fix makes the sweep discard
        whether or not the upstream-only logic exists, so it passed under a
        mutation that read the whole pipeline. That is the anti-pattern this
        file records - a control allowed under both behaviours - committed in
        the round that documents it, and found by mutation testing rather than
        by reading. Without the `-c` the command distinguishes the two.
        """
        block, _ = decision_for(
            shell_ctx("sqlite3 db .dump | grep 'DROP TABLE'"))
        self.assertIsNone(block)

    # ---- C9-6: a warning must not turn a block into an allow --------------
    def test_a_rule_does_not_fail_open_when_warnings_are_errors(self):
        """`evaluate` catches a crashing rule and allows the command, which is
        right for one bad rule and wrong as a way to meet a deprecation. A9
        called `re.split(pattern, text, 1)`, whose positional `maxsplit` python
        3.13 deprecates, so `PYTHONWARNINGS=error` turned a document-write block
        into an allow.

        Asserted through the real decision rather than by catching the warning,
        because the fail-open path swallows the exception: a test that only
        watched for the warning would pass while the block silently vanished.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            for command in (
                    """python -c "open('README.md?raw=1','w').write('x')" """,
                    """python -c "open('README.md','w').write('x')" """,
                    """python -c "open('.env#frag','w').write('x')" """,
            ):
                with self.subTest(command=command):
                    block, _ = decision_for(shell_ctx(command))
                    self.assertIsNotNone(
                        block, f"the block became an allow under warnings-as-"
                               f"errors: {command}")
                    self.assertEqual(block.rule, "A9", command)


class TestRoundTen(unittest.TestCase):
    """The review of round nine's repairs, 2026-09-12.

    Two of its findings were regressions introduced by the round-nine fix
    itself, which makes this the eighth consecutive round to find its worst
    defect in the previous round's repair. Both had one cause: the new
    `_arithmetic_substitutions` called `_scan`, and `_scan` calls it back, so
    the recursion had no depth cap where every other recursion in the module
    has one, and each nesting level re-read a body that is nearly the whole
    remaining command. Both directions end in ALLOW - a `RecursionError` is
    caught by the evaluator's per-rule fail-safe, and a hook that exceeds its
    budget has already permitted the command.

    Walking the body directly, instead of handing it back to the command
    lexer, closed a third defect for free: `_scan` honours `#` as a comment and
    arithmetic does not.
    """

    def test_deeply_nested_arithmetic_does_not_crash_the_rules(self):
        """~850 nested `$((` raised RecursionError, which the evaluator logs
        and treats as allow, so five rules were switched off at once by a
        string of punctuation. Bash really runs the shape: the reviewer's
        `touch` stand-in created its file."""
        command = "rm -rf build ; echo " + "$((" * 850 + "1" + "))" * 850
        block, _ = decision_for(shell_ctx(command))
        self.assertIsNotNone(block, "nested arithmetic crashed the rules, and "
                                    "a crashed rule is an allowed command")
        self.assertEqual(block.rule, "A6")

    def test_arithmetic_decisions_cost_at_most_the_budget_plus_linear_work(self):
        """The depth-driven cost defect, measured in work rather than time.

        **This replaces a wall-clock test that could not fail.** Its command
        was 20,000 sibling expansions at depth two, and the defect it named -
        each nesting LEVEL re-scanning nearly the whole remaining command -
        only bites with depth, so reinstating that defect left it passing at
        0.15s against a 2.0s bound. A reviewer found it by mutation. The same
        defect then came back a round later by a different route: deciding
        whether each nested `$((` is arithmetic scans forward to its inner
        close, which at every level of a deep nest is most of the span.

        So this counts the characters `_matching_paren` walks over a
        2,000-level nest and asserts they stay within the arithmetic decision
        budget plus linear work. A character count does not vary with machine
        load, so the bound can be tight without being flaky, and the quadratic
        form walks roughly ten million characters here.
        """
        command = ("rm -rf build ; echo " + "$((" * 2000 + "1" + "))" * 2000)
        walked = []
        real = core._matching_paren

        def counting(text, start):
            result = real(text, start)
            walked.append((result if result != -1 else len(text)) - start)
            return result

        core._scan.cache_clear()
        core._split_heredocs.cache_clear()
        with mock.patch.object(core, "_matching_paren", counting):
            block, _ = decision_for(shell_ctx(command))
        core._scan.cache_clear()
        core._split_heredocs.cache_clear()
        # A FIXED number, deliberately not read from `core`. The first version
        # bounded the walk by the production budget constant plus linear work,
        # and a mutation raising that budget raised the bound with it, so the
        # quadratic walk passed. A test that reads the constant it guards is
        # disarmed by the one change it exists to catch. The budget itself was
        # later removed in favour of a stack walk; the quadratic forms walk
        # roughly ten million characters here, and the stack walk far fewer.
        self.assertLessEqual(
            sum(walked), 250_000,
            "deciding which nested $(( are arithmetic walked far more than the "
            "budget allows, so depth is quadratic again")
        self.assertIsNotNone(block, "the leading rm was not seen")
        self.assertEqual(block.rule, "A6")

    def test_arithmetic_end_is_quote_aware(self):
        """`_arithmetic_end` counted bare parens, so one `(` inside a quoted
        string ran the count off the end of the input. The same
        one-fact-two-walkers shape as three earlier findings: `_matching_paren`
        knew about quotes and this did not.

        **Tested on the helper directly, because through the scanner it cannot
        fail.** Reverting the quote handling and running the command-level test
        below changes no verdict: a run-off count no longer ends in `))`, so
        the scanner falls through to `_matching_paren`, which is quote-aware
        and gets the right answer anyway. That makes the quote fix defence in
        depth rather than the thing carrying that verdict, and a control that
        cannot fail is worth less than no control, so the claim is pinned where
        it is actually observable.
        """
        text = 'echo $(( $(printf "(") )) ; rm -rf build'
        end = core._arithmetic_end(text, text.index("$((") + 1)
        self.assertEqual(text[end - 2:end], "))",
                         "the count ran past the arithmetic, so a quoted paren "
                         "is still being counted")

    def test_an_unbalanced_paren_inside_quotes_does_not_swallow_the_line(self):
        """The behavioural half: whatever carries it, a command written after
        an expansion containing a quoted paren must still be seen. It was not -
        the count ran off the end of the input and the rest of the line was
        consumed as part of the expansion. Today the `))` check is what rescues
        this; `_arithmetic_end`'s quote-awareness is the second line, pinned
        separately above."""
        for command, rule in (
                ('echo $(( $(printf "(") )) ; rm -rf build', "A6"),
                ('echo $(( $(printf "(") )) ; cat README.md', "A3"),
                ('echo $(( $(printf "(") )) ; echo x > .env', "A7"),
        ):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, rule, command)

    def test_a_dollar_paren_paren_that_is_not_arithmetic_is_a_command(self):
        """When the parens close without a second `)` beside the first, bash
        re-reads `$((` as `$( (` and RUNS it. Verified in bash: `$((printf hi)
        )` prints `hi` and `$((touch f) )` creates the file, where `$((printf
        hi))` is a syntax error that runs nothing. One space separated a block
        from an allow."""
        for command in ("echo $((rm -rf build) )",
                        "echo $((rm -rf build); )",
                        'echo "$((rm -rf build) )"'):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A6", command)

    def test_a_hash_in_an_arithmetic_body_is_not_a_comment(self):
        """`#` opens a comment to the command lexer and not to arithmetic:
        bash expands the expression before it fails to evaluate it, so the
        substitution after the `#` has already run by the time the error is
        reported. Verified in bash, where the `touch` stand-in created its
        file and bash then reported an invalid arithmetic operator."""
        for command in ("echo $(( 1 # $(rm -rf build)\n))",
                        'echo "$(( 1 # $(rm -rf build)\n))"'):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNotNone(block, command)
                self.assertEqual(block.rule, "A6", command)

    def test_an_unresolvable_pushd_still_saves_the_directory_it_left(self):
        """The general bypass of this round. A `pushd` whose destination the
        shell expands pushed NOTHING, so the following `popd` found an empty
        modelled stack, was read as a hard failure, and killed its `&&` chain -
        leaving every command after it unexamined. Prefixing any blocked read
        with `pushd "$(pwd)/anything" && popd &&` disabled A3 entirely.

        The directory a `popd` returns to is the one being LEFT, which is known
        even when the destination is not, so it is saved either way. Verified
        in bash: both verbs succeed and the shell ends where it started."""
        abs_readme = str(PROJECT_ROOT / "README.md").replace("\\", "/")
        for command, expected in (
                (f'pushd "$(pwd)/workflows" && popd && cat {abs_readme}', "A3"),
                (f'pushd "$(pwd)/workflows" && popd && cat README.md', "A3"),
                (f'pushd "$(pwd)/workflows" && popd || cat {abs_readme}', None),
        ):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                if expected is None:
                    self.assertIsNone(block, command)
                else:
                    self.assertIsNotNone(block, command)
                    self.assertEqual(block.rule, expected, command)

    def test_a_bare_pushd_swaps_rather_than_blanking_the_directory(self):
        """`pushd x ; pushd ; popd` ends in x, not where it started: bare
        `pushd` swaps the top of the stack with the current directory. Blanking
        the directory instead left the stack holding an entry the shell had
        already returned to, which produced a false negative and a false
        positive off one sequence. Verified in bash."""
        for command, expected in (
                ("pushd workflows ; pushd ; popd ; grep TODO CONTEXT.md", "A3"),
                ("pushd workflows ; pushd ; popd ; grep TODO README.md", None),
        ):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                if expected is None:
                    self.assertIsNone(block, command)
                else:
                    self.assertIsNotNone(block, command)
                    self.assertEqual(block.rule, expected, command)

    def test_pushd_n_adds_to_the_stack_without_moving_the_shell(self):
        """`-n` suppresses the directory change. The operand reader stripped
        every dash-word, so the flag vanished and the move was applied to a shell
        that never went anywhere - blocking a read of a file that is not there
        and naming a tool that would not have helped. Verified in bash: `pushd
        -n workflows` leaves pwd at the root with workflows on the stack, so a
        later bare `popd` lands there."""
        for command, expected in (
                ("pushd -n workflows ; grep TODO CONTEXT.md", None),
                ("pushd -n workflows ; popd ; grep TODO CONTEXT.md", "A3"),
        ):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                if expected is None:
                    self.assertIsNone(block, command)
                else:
                    self.assertIsNotNone(block, command)
                    self.assertEqual(block.rule, expected, command)

    def test_a_directory_verb_mid_pipeline_settles_no_chain(self):
        """A pipeline's exit status is its LAST stage's, so a directory verb in
        the middle of one settles neither `&&` nor `||`. This is the case the
        round-nine restructure fixed without a control, which a reviewer caught
        by mutating the guard away and finding nothing failed.

        Written as a differential rather than as a single assertion, so it
        distinguishes the two cases by itself: the same failing bare `pushd`
        ends the chain when it stands alone and does not when it is the first
        stage of a pipeline.
        """
        block, _ = decision_for(shell_ctx(
            "pushd | > out.txt && grep TODO README.md"))
        self.assertIsNotNone(block, "the failing bare pushd is not the "
                                    "pipeline's status, so the grep runs")
        self.assertEqual(block.rule, "A3")

        alone, _ = decision_for(shell_ctx(
            "pushd > out.txt && grep TODO README.md"))
        self.assertIsNone(alone, "outside a pipeline the same failing pushd "
                                 "does end the chain, so the grep never runs")

    def test_a6_matcher_flags_are_read_per_program(self):
        """In ripgrep `-L` is `--follow` and prints matching LINES;
        `--files-without-match` has no short form there. Applying grep's letters
        to every matcher skipped an rg stage that emits exactly the text the
        sweep exists to read. Verified on this machine with rg 14.1.1: `rg -L`
        printed the matching line where `grep -L` printed nothing."""
        for command, expected in (
                ("rg -L 'DROP TABLE users;' sqldir | sqlite3 db", "A6"),
                ("rg --follow 'DROP TABLE users;' sqldir | sqlite3 db", "A6"),
                ("rg -l 'DROP TABLE users;' sqldir | sqlite3 db", None),
                ("grep -L 'DROP TABLE users;' no-such-file.sql | sqlite3 db",
                 None),
        ):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                if expected is None:
                    self.assertIsNone(block, command)
                else:
                    self.assertIsNotNone(block, command)
                    self.assertEqual(block.rule, expected, command)

    def test_a6_does_not_read_a_flag_that_sits_after_a_double_dash(self):
        """`grep -e 'DROP TABLE x' -- -c dump.sql` names a FILE called `-c`
        and still emits the matched line, so reading it as the count flag
        bought the skip on a command that really does feed the client.
        Confirmed with GNU grep 3.0, which reports `-c: No such file` on stderr
        and prints the matching line on stdout."""
        block, _ = decision_for(shell_ctx(
            "grep -h -e 'DROP TABLE users;' -- -c no-such-file.sql | sqlite3 db"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A6")

    def test_a9_reads_the_ruby_spellings_python_shapes_do_not_reach(self):
        """Splitting ruby's read from its write lost three spellings this rule
        had blocked before: the `mode:` keyword, the `"w:UTF-8"` encoding
        suffix that this project's own encoding rules push an author toward,
        and an integer mode built from `File::` constants."""
        for code in ('File.open("notes.md", mode: "w") { |f| f.puts "x" }',
                     'File.open("notes.md", "w:UTF-8") { |f| f.puts "x" }',
                     'File.open("notes.md", mode: "a", encoding: "UTF-8")',
                     'File.open("notes.md", File::WRONLY|File::CREAT)',
                     'File.open(".env", mode: "w") { |f| f.puts "K=v" }'):
            with self.subTest(code=code):
                block, _ = decision_for(shell_ctx(f"ruby -e '{code}'"))
                self.assertIsNotNone(block, code)
                self.assertEqual(block.rule, "A9", code)

    def test_a9_ruby_read_spellings_are_still_allowed(self):
        """The controls for the widening above: the same three spellings in a
        READ mode stay allowed, which is the C9-3 narrowing this must not
        undo."""
        for code in ('File.open("README.md", mode: "r")',
                     'File.open("README.md", "r:UTF-8")',
                     'File.open("README.md", File::RDONLY)',
                     'File.open("README.md")'):
            with self.subTest(code=code):
                block, _ = decision_for(shell_ctx(f"ruby -e '{code}'"))
                self.assertIsNone(block, code)

    def test_control_ordinary_arithmetic_is_still_not_a_command(self):
        """The controls for all of the above, and they are the half that
        matters: every fix here widens what the reader treats as a command."""
        for command in ('echo "$((rm -rf build))"',
                        "echo $((1<<2)) ; echo done",
                        "echo $((16#ff))",
                        "echo $((1+2))",
                        "git log -n $((5))",
                        "test $(( $(printf 3) % 2 )) -eq 1 && echo odd"):
            with self.subTest(command=command):
                block, _ = decision_for(shell_ctx(command))
                self.assertIsNone(block, command)


class TestRoundEleven(unittest.TestCase):
    """The review of round ten's repairs, 2026-09-12.

    Twelve findings, one of them an accepted trade, and nearly all of them in
    code written the round before
    - the ninth consecutive round in which the newest code was the least
    correct. Three share one cause: whether a `$((` is arithmetic was decided
    by asking whether the span's last two characters were `))`, where bash asks
    a structural question, and every one of the three switched off all six
    shell rules. Three more are the directory-stack verbs taught `-n` in one
    spelling and not its siblings, and one is A6's per-program flag table,
    which knew each program's letters but not which of them carry a value.
    """

    def _assert_rule(self, command, rule):
        block, _ = decision_for(shell_ctx(command))
        if rule is None:
            self.assertIsNone(block, command)
        else:
            self.assertIsNotNone(block, command)
            self.assertEqual(block.rule, rule, command)

    # ---- reader --------------------------------------------------------
    def test_a_dollar_paren_paren_is_arithmetic_only_structurally(self):
        """`$((a) ; (b))` ends in `))` by coincidence and is a command
        substitution holding two subshells, which bash runs. Bash's test is
        whether the `)` matching the SECOND `(` is immediately followed by the
        `)` matching the first; a textual test on the last two characters
        called it arithmetic, and an arithmetic body is only searched for
        `$(`, so both commands vanished. Verified in bash with a `touch`
        stand-in in each operand position and for `;`, `&&`, `||`, `|`."""
        for command in ("echo $((rm -rf build) ; (:))",
                        "echo $((rm -rf build) && (printf X))",
                        'echo "$((rm -rf build) | (printf X))"'):
            with self.subTest(command=command):
                self._assert_rule(command, "A6")
        for command in ("echo $(( (1) )) ; echo done",
                        'echo "$((rm -rf build))"'):
            with self.subTest(control=command):
                self._assert_rule(command, None)

    def test_a_quoted_substitution_keeps_its_outer_command(self):
        """Inside double quotes the arithmetic test was asked of EVERY `$(`,
        and counting from a single `(` made any nested substitution that
        closes against the outer one look arithmetic - so the OUTER command
        was silently dropped. That is an ordinary idiom, not an adversarial
        one: `"$(cat $(ls))"` lost its `cat`."""
        self._assert_rule('echo "$(rm -rf build $(printf x))"', "A6")
        self._assert_rule('echo "$(cat README.md $(printf x))"', "A3")
        self._assert_rule('echo "$(printf hi)" ; echo done', None)

    def test_a_nested_dollar_paren_paren_is_tested_before_stepping_in(self):
        """The walker stepped into every nested `$((` as though it were
        arithmetic, so `$(( $((rm -rf build) ) ))` - which bash runs, printing
        0 and leaving a `touch` stand-in's file behind - hid the subshell."""
        for command in ("echo $(( $((rm -rf build) ) ))",
                        "echo $(( 1 + $((rm -rf build) ) ))"):
            with self.subTest(command=command):
                self._assert_rule(command, "A6")
        self._assert_rule("echo $(( $((1+2)) * 3 ))", None)

    def test_deep_nesting_drops_nothing_that_runs(self):
        """A command buried 2,000 levels deep in arithmetic is still seen.

        Written for round eleven's budget fallback, which dropped a buried
        command at the substitution depth cap; that fallback was replaced in
        round twelve by a stack walk with no budget at all, and the cases
        still hold against the replacement. The positions covered are the ones
        that can hide a word: a plain substitution, bash's `$( (` form, a
        comment, a quoted default, and each rule's own subject."""
        deep = 2000
        for inner, rule in (
                ("$(rm -rf build)", "A6"),
                ("$((rm -rf build) )", "A6"),
                ("$(cat README.md)", "A3"),
                ("$(printf x > .env)", "A7"),
                ("${x:-$(rm -rf build)}", "A6"),
                ("1 # x\n$(rm -rf build)", "A6"),
        ):
            command = "echo " + "$((" * deep + inner + "))" * deep
            with self.subTest(inner=inner):
                self._assert_rule(command, rule)
        pure = "echo " + "$((" * deep + "1" + "))" * deep + " ; echo done"
        self._assert_rule(pure, None)

    # ---- A3: the directory stack -----------------------------------------
    def test_pushd_n_never_fails_and_never_moves(self):
        """`-n` suppresses the directory change, and the directory change is
        both what bare `pushd` has nothing to swap with and what `pushd DIR`
        would have to validate. So bare `pushd -n` is a no-op that exits 0 and
        `pushd -n nosuchdir` exits 0 and pushes the name - measured in bash.
        The model read both as failures that killed an `&&` chain, and bare
        `pushd -n` as a rotation that blanked the directory, each of which let
        a read through."""
        for command in ("pushd -n && cat README.md",
                        "pushd -n nosuchdir && cat README.md",
                        "pushd . ; pushd -n ; cat README.md",
                        "pushd workflows ; pushd -n ; cat CONTEXT.md"):
            with self.subTest(command=command):
                self._assert_rule(command, "A3")

    def test_a_stack_position_is_not_a_directory_name(self):
        """`pushd +0` rotates by nothing and exits 0; `popd +1` removes an
        entry and leaves the shell where it is. Both were read as directory
        names, so the first became a failed `cd` killing its chain and the
        second moved the shell back. Measured in bash."""
        self._assert_rule("pushd +0 && cat README.md", "A3")
        self._assert_rule("pushd workflows ; popd +1 ; cat CONTEXT.md", "A3")
        # `&&` cannot tell a success from an unknown status - both let the
        # read through - so this test passed with `pushd +0` returning
        # UNKNOWN. Only `||` separates them: a success skips the branch.
        self._assert_rule("pushd +0 || cat README.md", None)

    def test_too_many_operands_fail_and_move_nothing(self):
        """`pushd a b` is an error in bash - too many arguments, exit 1, no
        move - and two operands were read as none, sending it down the
        bare-swap path. Written as a differential, because the swap produced a
        false negative and a false positive off the one sequence."""
        self._assert_rule("pushd workflows ; pushd a b ; cat CONTEXT.md", "A3")
        self._assert_rule("pushd workflows ; pushd a b ; cat README.md", None)
        self._assert_rule("cd workflows audit && cat README.md", None)
        # Every specimen above names a first operand that does not exist, so
        # they passed with the too-many check deleted: the move failed anyway.
        # With a first operand that DOES exist, a model that takes it moves
        # into `workflows` and reads its CONTEXT.md; bash moves nowhere.
        self._assert_rule("pushd workflows audit ; cat CONTEXT.md", None)

    def test_control_the_directory_stack_still_behaves(self):
        """The controls for the A3 widening above, including the round-ten
        bypass fix, which the same function carries."""
        self._assert_rule("pushd && cat README.md", None)
        self._assert_rule('pushd "$(pwd)/workflows" && popd && cat README.md',
                          "A3")
        self._assert_rule(
            "pushd workflows ; pushd ; popd ; grep TODO CONTEXT.md", "A3")
        self._assert_rule("pushd workflows ; popd -n ; cat CONTEXT.md", "A3")

    # ---- A6 --------------------------------------------------------------
    def test_a6_an_attached_flag_value_is_not_a_bundle_of_flags(self):
        """`rg -tsql` is `-t sql`, and its letters include `q` and `l`, so
        reading the word as a bundle bought the skip for a stage that prints
        the matched DROP - while `rg --type sql` blocked. A bundle now counts
        only when every letter is one the program takes without a value.
        `grep -e'DROP TABLE t'` had a second, independent cause: the joined
        word `-eDROP` gives `\\bDROP` no word boundary to match."""
        for command in ("rg -tsql 'DROP TABLE users;' sqldir | sqlite3 db",
                        "rg -g'*.sql' 'DROP TABLE users;' sqldir | sqlite3 db",
                        "grep -e'DROP TABLE users;' dump.sql | sqlite3 db"):
            with self.subTest(command=command):
                self._assert_rule(command, "A6")
        self._assert_rule(
            "grep -ci 'DROP TABLE' no-such-file.sql | sqlite3 db", None)
        self._assert_rule("rg -l 'DROP TABLE users;' sqldir | sqlite3 db", None)
        self._assert_rule(
            "grep -C 3 'DROP TABLE users;' dump.sql | sqlite3 db", "A6")

    # ---- A9 --------------------------------------------------------------
    def test_a9_ruby_file_new_io_write_and_both_encodings(self):
        """`File.new` is `File.open`'s constructor sibling and `IO.write` is
        `File.write`'s; and ruby's mode string can name an external AND an
        internal encoding. Ruby is not installed on this machine, so these are
        the documented forms; the verdicts are measured against the hook."""
        for code in ('File.open("notes.md", "w:UTF-8:UTF-8")',
                     'File.new("notes.md", "w")',
                     'File.new("notes.md", "w").write("x")',
                     'IO.write("notes.md", "x")'):
            with self.subTest(code=code):
                self._assert_rule(f"ruby -e '{code}'", "A9")
        for code in ('File.new("README.md", "r").read',
                     'File.open("README.md", "r:UTF-8:UTF-8").read'):
            with self.subTest(control=code):
                self._assert_rule(f"ruby -e '{code}'", None)

    def test_a9_a_call_receiver_is_read_by_its_first_argument(self):
        """The receiver scan took the last quoted string before a `)`, which
        for any two-argument call is the MODE. `io.FileIO("notes.md",
        "w").writelines(x)` recorded `"w"` as a literal, non-document target,
        and with every target literal the computed-target fallback never ran,
        so adding a write made the command more likely to be allowed."""
        self._assert_rule(
            "python -c 'import io; io.FileIO(\"notes.md\", \"w\").writelines(x)'",
            "A9")
        self._assert_rule(
            "python -c 'import io; io.FileIO(\"n.json\", \"w\").writelines(x)'",
            None)
        self._assert_rule(
            "python -c 'from pathlib import Path; "
            "Path(\"notes.md\").write_text(\"x\")'", "A9")


class TestRoundTwelve(unittest.TestCase):
    """The review of round eleven's repairs, 2026-09-12.

    Fifteen findings. The two in the reader were both against round eleven's
    budget and fallback, which had already been wrong once, so that design
    was replaced rather than patched a third time: a stack walk decides each
    nested `$((` as its parens close, in one pass, with no budget. The thirteen
    in the rules were mostly the same reader of option letters written three
    times over, and are fixed by one reader in `core` and one real argument
    parser for the directory builtins. Three were tests that could not fail,
    which are strengthened in place in `TestRoundEleven`.

    Every bash claim below was measured in Git Bash.
    """

    def _assert_rule(self, command, rule):
        block, _ = decision_for(shell_ctx(command))
        if rule is None:
            self.assertIsNone(block, command)
        else:
            self.assertIsNotNone(block, command)
            self.assertEqual(block.rule, rule, command)

    # ---- reader ------------------------------------------------------------
    def test_a_quoted_word_is_not_split_past_any_limit(self):
        """Round eleven's fallback turned quotes into spaces past its budget,
        so `r''m` became a program `r` with an argument `m`, and a quoted
        `sh -c` string fell apart. Bash joins the pieces and runs them. The
        trigger was about 100KB of padding in one arithmetic body."""
        pad = " " * 100_050
        for inner, rule in (("r''m -rf build", "A6"),
                            ('rm "-r""f" build', "A6"),
                            ("sh -c 'rm -rf build'", "A6"),
                            ('bash -lc "rm -rf build"', "A6"),
                            ("git push --fo''rce", "A6"),
                            ("cat READ''ME.md", "A3")):
            command = "echo $(( $((1)) + $(" + inner + ") + 0" + pad + "))"
            with self.subTest(inner=inner):
                self._assert_rule(command, rule)

    def test_nested_arithmetic_siblings_cost_linear_work(self):
        """The budget was per walk, not per command, so 400 siblings each
        nested 150 deep stayed under it one at a time and took 7.06s together
        against a five-second hook limit. Counted in characters walked, so the
        bound does not move with machine load; the bound is the test's own
        command length, never a production value."""
        unit = "$((" * 150 + "1" + "))" * 150
        command = ("echo " + " ".join("$(( " + unit + " ))" for _ in range(400))
                   + " ; rm -rf build")
        walked = []
        real = core._matching_paren

        def counting(text, start):
            result = real(text, start)
            walked.append((result if result != -1 else len(text)) - start)
            return result

        core._scan.cache_clear()
        core._split_heredocs.cache_clear()
        with mock.patch.object(core, "_matching_paren", counting):
            block, _ = decision_for(shell_ctx(command))
        core._scan.cache_clear()
        core._split_heredocs.cache_clear()
        self.assertLessEqual(sum(walked), 10 * len(command),
                             "nested siblings walked super-linearly")
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A6")

    def test_the_arithmetic_walk_follows_bash_quote_nesting(self):
        """Each case removes one idea from the stack walk if it breaks, and
        each was measured in bash with a marker file. Inside an arithmetic
        body a `$(` runs even inside single quotes; a construct opened inside
        quotes starts fresh quoting, so a subshell form in double quotes runs;
        and a paren inside quotes is literal, so a quoted `"))"` must not be
        paired as the construct's own close."""
        for command in (
                "echo $(( '$(rm -rf build)' + 1 ))",
                'echo $(( "$((rm -rf build) )" + 1 ))',
                'echo $(( $((printf %s ")" >/dev/null; rm -rf build) ) ))',
                'echo $(( $((printf %s "))" >/dev/null; rm -rf build) ) ))',
                'echo $(( "(" + $((rm -rf build) ) ))',
        ):
            with self.subTest(command=command):
                self._assert_rule(command, "A6")
        self._assert_rule("echo $(( $(( (1) )) )) ; echo ok", None)

    # ---- A3: the directory builtins' arguments -----------------------------
    def test_options_are_read_only_before_the_first_operand(self):
        """`pushd workflows -n` and `cd workflows -P` are too many arguments
        in bash, not a flag. The model filtered every dash-word out before
        counting operands, so it moved the shell and resolved the read in a
        directory bash never entered."""
        self._assert_rule("pushd workflows -n ; popd ; cat README.md", "A3")
        self._assert_rule("pushd workflows -n || cat README.md", "A3")
        self._assert_rule("cd workflows -P ; cat README.md", "A3")

    def test_an_invalid_option_moves_nothing(self):
        """`pushd -nn` is an invalid number and `cd -x` an invalid option;
        both exit 2 and leave the shell where it was."""
        self._assert_rule("pushd -nn workflows ; cat README.md", "A3")
        self._assert_rule("pushd -nn workflows || cat README.md", "A3")
        self._assert_rule("cd -x workflows ; cat README.md", "A3")
        self._assert_rule("cd -P workflows ; cat CONTEXT.md", "A3")
        self._assert_rule("cd -- workflows ; cat CONTEXT.md", "A3")

    def test_a_lone_dash_is_the_previous_directory(self):
        """`pushd -` goes to the previous directory; it was dropped with the
        dash-words and read as the bare swap. The destination is unknown to
        the model, so the chain continues and an absolute read is seen.

        Joined with `&&` on purpose: with `;` the absolute read is seen however
        `pushd -` is modelled, so the test could not tell an unknown status
        from a failure, and a failure would kill the chain bash really runs."""
        abs_readme = str(PROJECT_ROOT / "README.md").replace("\\", "/")
        self._assert_rule(f"cd workflows ; pushd - && cat {abs_readme}", "A3")

    def test_stack_positions_follow_bash(self):
        """The last position given wins (`popd +1 +0` is `popd +0`); `popd
        +N` shrinks the stack as well as blurring it, so a later `popd` on the
        now-empty stack fails; after `--` a `+1` is a directory name; and a
        position after a directory is too many arguments. The last four lines
        are the surviving mutants the reviewer showed no test could catch."""
        abs_readme = str(PROJECT_ROOT / "README.md").replace("\\", "/")
        self._assert_rule("pushd workflows ; popd +1 +0 ; cat README.md", "A3")
        self._assert_rule(
            "pushd workflows ; pushd audit ; popd +1 ; popd ; "
            f"popd || cat {abs_readme}", "A3")
        self._assert_rule(
            "pushd workflows ; pushd audit ; popd +1 ; popd ; "
            f"popd && cat {abs_readme}", None)
        self._assert_rule("pushd -- +1 ; cat README.md", "A3")
        self._assert_rule("pushd workflows +1 ; cat README.md", "A3")
        self._assert_rule(f"pushd workflows ; popd audit || cat {abs_readme}",
                          "A3")
        self._assert_rule("pushd workflows ; pushd +1 | cat ; cat CONTEXT.md",
                          "A3")
        self._assert_rule("pushd workflows ; pushd -n +1 ; cat CONTEXT.md", "A3")
        self._assert_rule("pushd workflows ; popd +0 ; cat README.md", "A3")

    def test_an_attached_pattern_is_not_a_recursive_flag(self):
        """A3 read `grep -e"Refactor"` as a bundle holding `R`, so a search of
        a pipe looked like a recursive walk of the tree and was blocked. The
        shared option reader ends a bundle at a value-taking letter."""
        self._assert_rule('git log | grep -e"Refactor"', None)
        self._assert_rule('git log --oneline | grep -e"Revert" | wc -l', None)
        self._assert_rule("grep -R TODO", "A3")
        self._assert_rule("grep -rn TODO", "A3")

    # ---- A6 ----------------------------------------------------------------
    def test_a6_a_separate_flag_value_is_not_a_suppressor(self):
        """`grep -e -c -e 'DROP TABLE t'` uses `-c` as the FIRST pattern, and
        prints the DROP; so does the long `--regexp -c` form and ripgrep's.
        Measured with GNU grep 3.0 and rg 14.1.1."""
        for command in (
                'grep -e -c -e "DROP TABLE users;" d.sql | sqlite3 db',
                'grep --regexp -c --regexp "DROP TABLE users;" d.sql | sqlite3 db',
                'rg -e -c -e "DROP TABLE users;" d.sql | sqlite3 db'):
            with self.subTest(command=command):
                self._assert_rule(command, "A6")
        self._assert_rule(
            "grep --count 'DROP TABLE' no-such-file.sql | sqlite3 db", None)
        self._assert_rule("grep -c5 'DROP TABLE' no-such-file.sql | sqlite3 db",
                          "A6")

    def test_a6_a_label_puts_argument_text_in_the_output(self):
        """With `--label`, grep's `-l` and `-c` print the label as the file
        name, so the statement named in it reaches the client."""
        self._assert_rule(
            'echo x | grep -l --label="DROP TABLE users;" x | sqlite3 db', "A6")
        self._assert_rule(
            'echo x | grep -c -H --label="DROP TABLE users;" x | sqlite3 db',
            "A6")

    # ---- A9 ----------------------------------------------------------------
    def test_a9_a_path_built_from_parts_is_computed(self):
        """Round eleven read a call receiver by its first argument, which for
        `Path("docs", "notes.md")` is only the directory: a literal
        non-document, so the fallback never ran. That was a regression. A call
        with several arguments, or none, is now a computed target."""
        for code in ('Path("workflows", "CONTEXT.md").write_text("x")',
                     'Path(".", "notes.md").write_text("x")',
                     'Path("docs", "notes.md").write_bytes(b"x")',
                     'Path("a", "b", "notes.md").open("w").write("x")',
                     'Path("notes.md").resolve().write_text("x")',
                     'Path("notes.md").expanduser().open("w").write("x")'):
            with self.subTest(code=code):
                self._assert_rule(
                    f"python -c 'from pathlib import Path; {code}'", "A9")
        self._assert_rule(
            "python -c 'from pathlib import Path; "
            "Path(\"a\", \"n.json\").write_text(\"x\")'", None)

    def test_a9_ruby_binwrite_parenless_calls_and_kernel_open(self):
        """Ruby writes these without parentheses as a matter of course, and
        `binwrite` and Kernel `open` are the siblings of calls already read.
        Ruby is not installed here; the forms are the documented ones."""
        for code in ('File.binwrite("notes.md", "x")',
                     'IO.binwrite("notes.md", "x")',
                     'File.write "notes.md", "x"',
                     'File.open "notes.md", "w" do |f| f.puts 1 end',
                     'open("notes.md", "w:UTF-8") { |f| f.puts 1 }'):
            with self.subTest(code=code):
                self._assert_rule(f"ruby -e '{code}'", "A9")
        for code in ('File.open "README.md" do |f| puts f.read end',
                     'open("README.md") { |f| puts f.read }'):
            with self.subTest(control=code):
                self._assert_rule(f"ruby -e '{code}'", None)


class TestRoundThirteen(unittest.TestCase):
    """The external Codex review of 2026-09-13, round thirteen.

    Seven findings fixed and two kept as recorded limits at the user's
    decision. Six of the seven were in A9 and most were one question answered
    with one shared list: which words on an interpreter's command line are
    code. `-c`, `-e` and `-r` were read the same way for every interpreter, in
    every position, so node's `--eval` and `-p` were missed, a flag belonging
    to a saved script was read as code, and a warning setting such as `-W
    ignore` was mistaken for a script name. Each interpreter's options are now
    read the way that interpreter reads them.

    Every interpreter claim below was measured in Git Bash with python, node
    and perl; ruby, php and ripgrep are not installed here, so their forms are
    the documented ones.
    """

    WRITE = "require('fs').writeFileSync"

    def _assert_rule(self, command, rule):
        block, _ = decision_for(shell_ctx(command))
        if rule is None:
            self.assertIsNone(block, command)
        else:
            self.assertIsNotNone(block, command)
            self.assertEqual(block.rule, rule, command)

    def _assert_all(self, cases):
        for command, rule in cases:
            with self.subTest(command=command):
                self._assert_rule(command, rule)

    # ---- A9: which words are code, per interpreter -------------------------
    def test_a9_node_long_and_print_spellings_are_inline_code(self):
        """`--eval`, `--eval=`, `-p`, `--print` and `-pe` all run their
        argument, measured with node. Only `-e` was read."""
        w = self.WRITE
        self._assert_all([
            (f'node --eval "{w}(\'notes.md\',\'x\')"', "A9"),
            (f'node --eval="{w}(\'notes.md\',\'x\')"', "A9"),
            (f'node -p "{w}(\'notes.md\',\'x\')"', "A9"),
            (f'node -pe "{w}(\'notes.md\',\'x\')"', "A9"),
            (f'node --print "{w}(\'notes.md\',\'x\')"', "A9"),
            (f'node --require ./x.js --eval "{w}(\'notes.md\',\'x\')"', "A9"),
            (f'node --eval "{w}(\'notes.json\',\'x\')"', None),
            ('node --eval "console.log(1)"', None),
        ])

    def test_a9_short_option_bundles_carry_code(self):
        """A code flag at the end of a bundle of valueless flags still takes
        the next word as code: `python -Sc`, `perl -we`/`-lne`, `ruby -we`."""
        self._assert_all([
            ('python -Sc "open(\'notes.md\',\'w\')"', "A9"),
            ('perl -we \'open(my $fh, ">", "notes.md")\'', "A9"),
            ('perl -lne \'open(FH, ">notes.md")\'', "A9"),
            ('ruby -we \'File.write("notes.md","x")\'', "A9"),
            ('python -Sc "print(1)"', None),
        ])

    def test_a9_an_option_value_is_not_a_script(self):
        """`-W ignore`, `-X utf8`, `node -r module` take the next word as a
        value. Reading `ignore` as a script name made a heredoc look like the
        script's input, so `python -W ignore <<EOF` with a document write in
        the body was ALLOWED; measured, python runs that body as code."""
        w = self.WRITE
        body = "\nfrom pathlib import Path\nPath('notes.md').write_text('x')\nEOF"
        self._assert_all([
            ('python -W ignore -c "open(\'notes.md\',\'w\')"', "A9"),
            ('python -X utf8 -c "open(\'notes.md\',\'w\')"', "A9"),
            ("python -W ignore <<EOF" + body, "A9"),
            ("python -u - <<EOF" + body, "A9"),
            (f'node -r ts-node/register -e "{w}(\'notes.md\',\'x\')"', "A9"),
            (f'node --inspect -e "{w}(\'notes.md\',\'x\')"', "A9"),
        ])

    def test_a9_an_unknown_option_is_not_trusted_to_end_the_options(self):
        """The option rows are not exhaustive, so a word after an option they
        do not know may be that option's value rather than a script. It is
        not trusted to end option reading, which can only read more words as
        options: the code after it is still seen."""
        self._assert_rule('python -Q x -c "open(\'notes.md\',\'w\')"', "A9")
        self._assert_rule(
            'node --no-such-option x -e "require(\'fs\').writeFileSync('
            '\'notes.md\',\'x\')"', "A9")

    def test_a9_node_require_names_a_module_not_code(self):
        """node's `-r` loads a module; measured, `node -r "code"` fails to
        find a module called that. It was read as code for every interpreter,
        and is code only for php."""
        w = self.WRITE
        self._assert_rule(f'node -r "{w}(\'notes.md\',\'x\')" app.js', None)
        self._assert_rule(
            "php -r \"file_put_contents('notes.md','x');\"", "A9")

    def test_a9_flags_after_a_script_or_module_belong_to_it(self):
        """Once python or node has a script path, or python has `-m`, later
        words are that program's arguments; measured, `python render.py -c
        CODE` runs render.py with `-c` in its argv and never runs CODE."""
        w = self.WRITE
        self._assert_all([
            ('python render.py -c "open(\'README.md\',\'w\')"', None),
            ('python -u render.py -c "open(\'README.md\',\'w\')"', None),
            ('python -X utf8 render.py -c "open(\'README.md\',\'w\')"', None),
            ('python -m pytest -c "open(\'README.md\',\'w\')"', None),
            (f'node render.js -e "{w}(\'README.md\',\'x\')"', None),
            ('perl render.pl -e \'open(my $fh, ">", "README.md")\'', None),
            ('python -c "open(\'README.md\',\'w\')" render.py', "A9"),
            ('perl -e \'print 1;\' -e \'open(my $fh, ">", "notes.md")\'',
             "A9"),
            # python's `-c` ends option reading, measured: a second `-c` is
            # an argument to the first program. perl joins several `-e`.
            ('python -c "print(1)" -c "open(\'README.md\',\'w\')"', None),
        ])

    def test_a9_a_heredoc_beside_inline_code_is_data(self):
        """With `-c` or `-e` the program is the argument and stdin is data;
        measured, python and perl both print such a body rather than run it.
        A heredoc with no code flag and no script is still code."""
        self._assert_all([
            ("python -c 'import sys; print(sys.stdin.read())' <<EOF\n"
             "Path(\"notes.md\").write_text(\"x\")\nEOF", None),
            ("perl -e 'print <STDIN>' <<EOF\n"
             "open(my $fh, \">\", \"notes.md\")\nEOF", None),
            ("python - <<EOF\nfrom pathlib import Path\n"
             "Path('notes.md').write_text('x')\nEOF", "A9"),
            ("python <<EOF\nfrom pathlib import Path\n"
             "Path('notes.md').write_text('x')\nEOF", "A9"),
            ("python render.py <<EOF\nPath('notes.md').write_text('x')\nEOF",
             None),
        ])

    # ---- A9: write shapes ---------------------------------------------------
    def test_a9_open_mode_after_other_keywords(self):
        """Python accepts keyword arguments in any order, and this project's
        encoding rules make `encoding=` before `mode=` a likely spelling. Both
        `open(...)` and `Path(...).open(...)` are read. A keyword argument is
        never the path, so a method call's keywords are not read as a target
        of the builtin: that sent a `.json` write to the fallback."""
        self._assert_all([
            ('python -c "open(\'README.md\', encoding=\'utf-8\', mode=\'w\')"',
             "A9"),
            ('python -c "open(\'README.md\', buffering=1, mode=\'w\')"', "A9"),
            ('python -c "open(\'README.md\', newline=\'\\n\', '
             'encoding=\'utf-8\', mode=\'a\')"', "A9"),
            ('python -c "from pathlib import Path; Path(\'README.md\')'
             '.open(encoding=\'utf-8\', mode=\'w\').write(\'x\')"', "A9"),
            ('python -c "open(file=\'README.md\', mode=\'w\')"', "A9"),
            ('python -c "open(file=\'notes.json\', mode=\'w\')'
             '.write(\'README.md\')"', None),
            ('python -c "open(\'README.md\', mode=\'w\', encoding=\'utf-8\')"',
             "A9"),
            ('python -c "print(open(\'README.md\', encoding=\'utf-8\', '
             'mode=\'r\').read())"', None),
            ('python -c "open(\'notes.json\', encoding=\'utf-8\', mode=\'w\')'
             '.write(\'README.md\')"', None),
            ('python -c "from pathlib import Path; Path(\'notes.json\')'
             '.open(encoding=\'utf-8\', mode=\'w\').write(\'README.md\')"',
             None),
        ])

    def test_a9_perl_open_forms(self):
        """Parenless `open`, a lexical handle in the two-argument form, the
        append and read/write modes, and a layer such as `:encoding(UTF-8)`.
        A read mode stays a read."""
        self._assert_all([
            ('perl -e \'open my $fh, ">", "notes.md"\'', "A9"),
            ('perl -e \'open(my $fh, ">notes.md")\'', "A9"),
            ('perl -e \'open my $fh, ">>notes.md"\'', "A9"),
            ('perl -e \'open(my $fh, ">:encoding(UTF-8)", "notes.md")\'',
             "A9"),
            ('perl -e \'open(my $fh, "+<", "notes.md")\'', "A9"),
            ('perl -e \'open(FH, ">notes.md")\'', "A9"),
            ('perl -e \'open(my $fh, "<", "notes.md")\'', None),
            ('perl -e \'open my $fh, "<:encoding(UTF-8)", "README.md"\'', None),
            ('perl -e \'open(my $fh, ">", "notes.json")\'', None),
        ])

    def test_a9_ruby_parenless_kernel_open(self):
        """Kernel `open` without parentheses, with an encoding suffix."""
        self._assert_rule(
            'ruby -e \'open "notes.md", "w:UTF-8" do |f| f.puts 1 end\'', "A9")
        self._assert_rule(
            'ruby -e \'open "README.md" do |f| puts f.read end\'', None)

    def test_a9_python_string_prefixes_are_still_literals(self):
        """`r"..."`, `b"..."` and their combinations are plain literals; an
        f-string is built at runtime and stays a computed target."""
        self._assert_all([
            ('python -c "from pathlib import Path; Path(r\'index.json\')'
             '.write_text(\'README.md\')"', None),
            ('python -c "from pathlib import Path; Path(R\'index.json\')'
             '.write_text(\'README.md\')"', None),
            ('python -c "open(b\'index.json\', \'w\').write(\'README.md\')"',
             None),
            ('python -c "from pathlib import Path; Path(rb\'index.json\')'
             '.write_bytes(b\'README.md\')"', None),
            ('python -c "from pathlib import Path; Path(r\'notes.md\')'
             '.write_text(\'x\')"', "A9"),
            ('python -c "from pathlib import Path; Path(u\'notes.md\')'
             '.write_text(\'x\')"', "A9"),
            ('python -c "from pathlib import Path; n=1; Path(f\'{n}.md\')'
             '.write_text(\'x\')"', "A9"),
            ('python -c "n=1; open(rf\'{n}.md\', \'w\')"', "A9"),
        ])

    # ---- A6 -----------------------------------------------------------------
    def test_a6_a_filter_that_removes_the_statement_is_not_its_source(self):
        """An inverted match, a sed script made only of deletions, and an awk
        program that only prints non-matching lines all emit the lines WITHOUT
        the pattern; measured with GNU grep, sed and awk."""
        self._assert_all([
            ("grep -v 'DROP TABLE' dump.sql | sqlite3 db", None),
            ("grep --invert-match 'DROP TABLE' dump.sql | sqlite3 db", None),
            ("grep -iv 'drop table' dump.sql | sqlite3 db", None),
            ("rg -v 'DROP TABLE' dump.sql | sqlite3 db", None),
            ("sed '/DROP TABLE/d' dump.sql | sqlite3 db", None),
            ("sed -e '/DROP TABLE/d' -e '/TRUNCATE TABLE/d' dump.sql"
             " | sqlite3 db", None),
            ("sed -E '/DROP TABLE/Id' dump.sql | sqlite3 db", None),
            ("awk '!/DROP TABLE/' dump.sql | sqlite3 db", None),
            ("awk '$0 !~ /DROP TABLE/' dump.sql | sqlite3 db", None),
        ])

    def test_a6_controls_for_the_filter_skip(self):
        """What must still block: a filter that keeps or prints the lines, a
        script that does anything besides delete, a later stage or the client
        itself carrying the statement, a sed script read from a file, and
        `--label`, which puts argument text in the output."""
        self._assert_all([
            ("grep 'DROP TABLE' dump.sql | sqlite3 db", "A6"),
            ("sed -n '/DROP TABLE/p' dump.sql | sqlite3 db", "A6"),
            ("sed '/DROP TABLE/!d' dump.sql | sqlite3 db", "A6"),
            ("sed 's/x/DROP TABLE t;/' dump.sql | sqlite3 db", "A6"),
            ("sed '/x/d; s/y/DROP TABLE t;/' dump.sql | sqlite3 db", "A6"),
            ("awk '/DROP TABLE/' dump.sql | sqlite3 db", "A6"),
            ("awk '!/x/ {print \"DROP TABLE t;\"}' dump.sql | sqlite3 db",
             "A6"),
            ("grep -v x dump.sql | grep 'DROP TABLE' | sqlite3 db", "A6"),
            ("grep -v 'DROP TABLE' dump.sql | sqlite3 db 'DROP TABLE t;'",
             "A6"),
            # A script file may add what the `-e` deletes, so `-f` is never
            # read as delete-only, even beside a real deletion.
            ("sed -e '/DROP TABLE/d' -f add.sed dump.sql | sqlite3 db", "A6"),
            ("echo x | grep -v --label='DROP TABLE t;' -H x | sqlite3 db",
             "A6"),
        ])

    # ---- recorded limits, pinned so they are decided rather than rediscovered
    def test_recorded_limit_grep_color_hides_a_count(self):
        """KEPT AT THE USER'S DECISION, 2026-09-13. An unknown long option is
        assumed to take the next word, so `--color -c` hides the count and an
        audit pipeline is falsely blocked; `--color=always -c` is read. Only
        ever a false block."""
        self._assert_rule(
            "grep --color -c 'DROP TABLE' dump.sql | sqlite3 db", "A6")
        self._assert_rule(
            "grep --color=always -c 'DROP TABLE' dump.sql | sqlite3 db", None)

    def test_recorded_limit_stack_positions_leave_the_status_unknown(self):
        """KEPT AT THE USER'S DECISION, 2026-09-13. A stack position other
        than `+0` leaves the directory and exit status unknown, so bash's
        success does not suppress an `||` branch the hook then reads. Only
        ever a false block, and only on an unusual chain."""
        abs_readme = str(PROJECT_ROOT / "README.md").replace("\\", "/")
        self._assert_rule(
            f"pushd workflows ; pushd -n +1 || cat {abs_readme}", "A3")
        self._assert_rule(f"pushd workflows ; popd +1 || cat {abs_readme}",
                          "A3")

    def test_recorded_limit_a_later_filter_does_not_unblock_an_earlier_producer(self):
        """KEPT AT THE USER'S DECISION, 2026-09-18 (R14-4). The filter skip
        works when the DROP text is the filter's OWN pattern, and not when an
        earlier producer emits it and a later filter removes it before output.

        Deferred rather than fixed with the rest of round 14: closing it means
        modelling what a filter removes from text a previous stage produced,
        which is a widening of A6 in the same family as the defects the recent
        rounds kept finding. Only ever a false block, on an unlikely pipeline,
        and pinned here so a later review reads it as decided rather than new.
        """
        for command in (
                "printf 'DROP TABLE t;' | grep -v 'DROP TABLE' | sqlite3 db",
                "printf 'DROP TABLE t;' | sed '/DROP TABLE/d' | sqlite3 db",
                "printf 'DROP TABLE t;' | awk '!/DROP TABLE/' | sqlite3 db",
        ):
            with self.subTest(command=command):
                self._assert_rule(command, "A6")

    def test_recorded_limit_a_negated_pipeline_settling_on_an_ordinary_command(self):
        """KEPT AT THE USER'S DECISION, 2026-09-18 (R15-2). A leading `!` is
        carried across the pipeline, but chain state is only ever settled by a
        DIRECTORY VERB, so a pipeline whose last stage is an ordinary command
        leaves the negation with nothing to invert and the `&&` payload is read.

        Measured in Git Bash with `echo RAN` standing in for the read: `cd`
        fails, `echo hi` runs, the pipeline therefore succeeds, `!` inverts it to
        exit 1, and the payload does not run. The hook reads the payload anyway
        and blocks it.

        Only ever a false block. Deferred rather than fixed with the rest of
        round 15 because closing it means settling chain state from any last
        stage's known status rather than from a directory verb's, which widens
        the directory model that produced this round's blocker; the shape is
        pinned here so a later review reads it as decided rather than new.

        The control is the same command without the `!`, where the pipeline
        really does succeed and bash really does run the payload, so the block
        is correct. The two must not converge: a fix for this limit has to keep
        the second one blocking.
        """
        self._assert_rule("! cd no_such_dir | echo hi && cat README.md", "A3")
        self._assert_rule("cd no_such_dir | echo hi && cat README.md", "A3")


class TestEveryScriptModuleIsTracked(unittest.TestCase):
    """CR8. Every other check in this suite proves the code BEHAVES. None of
    them asked whether the code would still be there after a clone, so an
    untracked module that tracked code imports passed everything and would have
    vanished on commit, leaving a registry importing a file that is not there.

    **Named for what it actually asks, after the name outran it.** It was
    `TestChangeSetIsDeliverable`, which reads as a commit-readiness gate, and it
    is not one: it proves every local module under `scripts/` is in git's index,
    and says nothing about whether the staged commit holds the whole change set.
    The intended state of this change set is one staged file beside many
    unstaged ones, so a reader taking the old name at face value would have read
    a green suite as "ready to commit" while most of the work was unstaged.
    """

    def test_the_import_reader_handles_every_form_it_meets(self):
        """The deliverability check is only as good as its import reader, and
        the hand-rolled one it replaced was wrong six ways.

        Tested here on synthetic source because the check itself cannot test
        it: that check currently fails for an unrelated reason, so mutating its
        reader changes nothing observable and every improvement to it was
        unverifiable. The forms below are the exact ones the previous reader
        got wrong, including `import adapters  # noqa: E402`, which is a real
        line in run.py that it read as a module named
        "adapters  # noqa: E402".
        """
        cases = [
            ("import alpha", {"alpha"}),
            ("import alpha, beta", {"alpha", "beta"}),
            ("import alpha as a", {"alpha"}),
            ("import alpha  # noqa: E402", {"alpha"}),
            ("import alpha.beta", {"alpha"}),
            ("from . import alpha", {"alpha"}),
            ("from . import (\n    alpha,\n    beta,\n)", {"alpha", "beta"}),
            ("from .alpha import thing", {"alpha"}),
            ("from alpha.beta import thing", {"alpha"}),
            ('"""A docstring mentioning import alpha."""', set()),
            ("# import alpha", set()),
            ('x = "import alpha"', set()),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(imported_names(source), expected)

    def test_a_package_import_resolves_to_its_init(self):
        """A local module can be a file OR a package directory. Resolving only
        the file form left `adapters` and `rules` invisible - the packages that
        make the hook fire at all - so both could have been untracked with this
        class reporting clean."""
        found = local_module_for(SCRIPTS, "adapters")
        self.assertIsNotNone(found, "adapters/ is a real local package")
        self.assertEqual(found.name, "__init__.py")
        self.assertEqual(found.parent.name, "adapters")
        self.assertIsNotNone(local_module_for(SCRIPTS, "core"),
                             "the plain-file form must still resolve")
        self.assertIsNone(local_module_for(SCRIPTS, "json"),
                          "a stdlib name is not a local module")

    def test_every_module_the_scripts_import_is_tracked(self):
        """Every local module imported anywhere under `scripts/` must be in
        git's index.

        Three things this asks that an earlier version did not. It reads the
        imports of EVERY file under `scripts/`, not just the rules registry, so
        a module pulled in by `core.py`, `run.py` or an adapter is covered. It
        asks whether a file is in the index (`git ls-files --cached`) rather
        than whether it is untracked-and-unignored, because a *gitignored*
        module is equally missing from a clone and the earlier phrasing
        reported it as fine. And it fails when git cannot answer instead of
        passing quietly, since "not a git repository" is exactly the exported
        tree where this question matters most.
        """
        try:
            result = subprocess.run(
                ["git", "ls-files", "--cached"], capture_output=True,
                encoding="utf-8", cwd=PROJECT_ROOT)
        except (OSError, FileNotFoundError):
            self.skipTest("git binary not available")
        self.assertEqual(result.returncode, 0,
                         "git could not list the index, so deliverability is "
                         "unproven rather than proven")
        tracked = {line.strip() for line in result.stdout.split("\n")
                   if line.strip()}

        missing = []
        for source in sorted(SCRIPTS.rglob("*.py")):
            # Read on the parse tree, not line by line. The hand-rolled version
            # mangled `import adapters  # noqa: E402` into a module named
            # "adapters  # noqa: E402" (a real line in run.py), dropped the
            # second name of `import a, b`, mangled `import a as b`, missed
            # `from . import a` entirely, missed parenthesised multi-line
            # imports, and matched an import written inside a docstring.
            try:
                names = imported_names(source.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - would fail elsewhere
                continue
            for name in names:
                # A local module is a `name.py` OR a `name/__init__.py`
                # package. Resolving only the first left both adapters and the
                # rules registry unreachable, so the code that makes the hook
                # fire at all could have been untracked with this test green.
                candidate = local_module_for(source.parent, name)
                if candidate is None:
                    continue
                rel = candidate.relative_to(PROJECT_ROOT).as_posix()
                if rel not in tracked:
                    missing.append(
                        f"{source.relative_to(PROJECT_ROOT).as_posix()} "
                        f"imports {rel}")
        # Every module under scripts/ must itself be tracked, not only the ones
        # something imports: a rule module reached through the registry's
        # `from .x import` is covered above, but a package's own files are not
        # all imported by name.
        for source in sorted(SCRIPTS.rglob("*.py")):
            rel = source.relative_to(PROJECT_ROOT).as_posix()
            if rel not in tracked:
                missing.append(f"{rel} is not in the index")
        self.assertFalse(sorted(set(missing)),
                         "code under scripts/ imports or contains a module git "
                         "does not have in its index, so a clone or a commit "
                         "would be missing it: " + "; ".join(sorted(set(missing))))


class TestHiddenCommandWrappers(unittest.TestCase):
    """A10: a command hidden inside a wrapper option's value (R19-1).

    `env -S "..."` and `env --split-string="..."` are not configuration. GNU env
    splits the value into words and runs them, so the value is the command - and
    the shared reader, which skips a wrapper's option values, was left with an
    EMPTY argv. That is why this is not an A6 test class: before the repair the
    same four spellings hid a destructive command, a dedicated-tool read, a
    LOG.md redirect and an inline document write alike, because a rule handed no
    argv has nothing to object to. The blocking tests below therefore span four
    rules' worth of payload on purpose.

    Every spelling here was measured in Git Bash before it was written, with
    `printf` standing in for the payload so no destructive command was ever run:
    all four print their marker and exit 0. The `-iS` bundle needed a second
    measurement to read correctly - it first appeared to fail, and `env -i
    printf` fails identically with no `-S` at all, because `-i` empties the
    environment and PATH with it; `env -iS '/usr/bin/printf ...'` prints its
    marker, so the bundle really does split and execute.

    Most of the class is controls, as it is for every widening in this suite: a
    false block is the failure that gets a hook routed around, and this one
    refuses a shape rather than reading it, so the controls are what hold the
    refusal to its intended width.
    """

    # --- the shape is refused, whatever it carries ---

    def test_short_separated_blocks(self):
        block, _ = decision_for(shell_ctx('env -S "find . -delete"'))
        self.assertIsNotNone(block, "env -S hid the command")
        self.assertEqual(block.rule, "A10")

    def test_long_separated_blocks(self):
        block, _ = decision_for(
            shell_ctx('env --split-string "find . -delete"'))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A10")

    def test_long_attached_blocks(self):
        block, _ = decision_for(
            shell_ctx('env --split-string="find . -delete"'))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A10")

    def test_short_attached_blocks(self):
        """The spelling the repair brief did not name.

        `env -S"find . -delete"` reaches the hook as the single token
        `-Sfind . -delete`. Leaving it out would have closed three doors of
        four, which is the "one fact taught in one spelling and not its
        siblings" shape this workflow's history is largely made of.
        """
        block, _ = decision_for(shell_ctx('env -S"find . -delete"'))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A10")

    def test_inside_an_option_bundle_blocks(self):
        block, _ = decision_for(shell_ctx('env -iS "find . -delete"'))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A10")

    # --- it is the reader that was bypassed, not one rule ---

    def test_a_dedicated_tool_read_was_hidden_too(self):
        """Payload for A3 rather than A6, so the class cannot pass as an A6 fix."""
        block, _ = decision_for(shell_ctx('env -S "cat AGENTS.md"'))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A10")

    def test_a_log_redirect_was_hidden_too(self):
        block, _ = decision_for(shell_ctx('env -S "echo hi >> LOG.md"'))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A10")

    def test_reached_through_another_wrapper(self):
        block, _ = decision_for(shell_ctx('sudo env -S "rm -rf build"'))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A10")

    def test_reached_through_a_carried_command(self):
        block, _ = decision_for(shell_ctx("sh -c \"env -S 'rm -rf build'\""))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A10")

    def test_a_harmless_payload_is_refused_too(self):
        """The accepted cost, pinned rather than left implied.

        The shape is refused without reading what it carries, so an innocent
        `env -S "python3 --version"` is refused with the rest. That is the
        deliberate trade - measured at zero uses in 26,985 indexed transcript
        messages before it was accepted - and a real false block on ordinary
        work is the signal to revisit it.
        """
        block, _ = decision_for(shell_ctx('env -S "python3 --version"'))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A10")

    def test_block_message_gives_the_direct_form_and_the_manual_path(self):
        block, _ = decision_for(shell_ctx('env -S "find . -delete"'))
        text = block.reason.lower()
        self.assertIn("directly", text)
        self.assertIn("env", text)
        self.assertIn("terminal", text)

    # --- controls: ordinary env use is untouched ---

    def test_control_an_ordinary_env_wrapper_still_reaches_a6(self):
        """Armed both ways: A10 over-firing shows up as the wrong rule id.

        `env find . -delete` must still block, and must still block as A6. A
        test asserting only "something blocked" would pass if A10 swallowed
        every `env` command, which is exactly the over-width to guard against.
        """
        block, _ = decision_for(shell_ctx("env find . -delete"))
        self.assertIsNotNone(block)
        self.assertEqual(block.rule, "A6")

    def test_control_a_plain_env_wrapper_is_allowed(self):
        block, _ = decision_for(shell_ctx("env python3 --version"))
        self.assertIsNone(block)

    def test_control_env_assignments_are_allowed(self):
        block, _ = decision_for(shell_ctx("env VAR=1 python3 --version"))
        self.assertIsNone(block)

    def test_control_a_real_env_value_flag_is_allowed(self):
        """`-u` takes a value and that value genuinely is configuration."""
        block, _ = decision_for(shell_ctx("env -u PATH python3 --version"))
        self.assertIsNone(block)

    def test_control_a_dash_s_belonging_to_the_wrapped_program(self):
        """The armed control: `-S` after env's options is the program's own.

        Measured in Git Bash - `env ls -S /nonexistent` runs `ls -S` and returns
        ls's own error - so a reading that scanned past env's option region
        would block an ordinary sorted listing. `ls -S` on its own is here for
        the same reason one step further out.
        """
        self.assertIsNone(decision_for(shell_ctx("ls -S"))[0])
        self.assertIsNone(decision_for(shell_ctx("env ls -S"))[0])
        self.assertIsNone(decision_for(shell_ctx("env -u PATH ls -S"))[0])

    def test_control_end_of_options_hands_the_rest_to_the_program(self):
        block, _ = decision_for(shell_ctx("env -- ls -S"))
        self.assertIsNone(block)

    # --- the property the chosen fix preserves ---

    def test_effective_argv_still_returns_a_suffix_of_its_input(self):
        """Why the value is refused rather than spliced into the argv.

        `negation_parity` reads how much was stripped as
        `len(argv) - len(effective_argv(argv))`, which is only meaningful while
        the result is a true suffix of the input. Splitting `env -S`'s value and
        splicing the words in - the alternative fix - can return MORE words than
        it was given, silently breaking that caller and with it A3's reading of
        a negated chain. This pins the property so a later attempt at the
        splice fails here rather than in A3.
        """
        for command in ("find . -delete", "env -S \"find . -delete\"",
                        "! cd workflows", "sudo -u root rm -rf /srv",
                        "env --split-string='rm -rf build'",
                        "timeout 5 env VAR=1 nice ls"):
            for argv in core.split_segments(command) or []:
                effective = core.effective_argv(argv)
                self.assertLessEqual(len(effective), len(argv), command)
                self.assertEqual(effective, argv[len(argv) - len(effective):],
                                 command)


if __name__ == "__main__":
    unittest.main(verbosity=2)
