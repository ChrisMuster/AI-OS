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

from core import Context  # noqa: E402
import run as run_mod  # noqa: E402
from adapters import claude as claude_adapter  # noqa: E402
from adapters import codex as codex_adapter  # noqa: E402


def shell_ctx(command, ai="claude"):
    return Context(ai_id=ai, category="shell", tool_name="Bash",
                   command=command, project_root=PROJECT_ROOT)


def write_ctx(file_path, content, ai="claude"):
    return Context(ai_id=ai, category="write", tool_name="Write",
                   file_path=file_path, content=content, project_root=PROJECT_ROOT)


def decision_for(ctx):
    block, warns = run_mod.evaluate(ctx)
    return block, warns


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


class TestTrialRulesWarnOnly(unittest.TestCase):
    def test_a2_backslash_warns_not_blocks(self):
        block, warns = decision_for(shell_ctx("python workflows\\audit\\run.py"))
        self.assertIsNone(block)
        self.assertTrue(any(w.rule == "A2" for w in warns))

    def test_a3_cat_warns_not_blocks(self):
        block, warns = decision_for(shell_ctx("cat workflows/audit/CONTEXT.md"))
        self.assertIsNone(block)
        self.assertTrue(any(w.rule == "A3" for w in warns))

    def test_a3_echo_redirect_warns(self):
        block, warns = decision_for(shell_ctx("echo hi > notes.txt"))
        self.assertIsNone(block)
        self.assertTrue(any(w.rule == "A3" for w in warns))


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

    def test_no_test_writes_to_the_real_fire_log(self):
        # The suite must never append to the real fire-log: it is the store the
        # recording-mode observation period counts, and a junk row with
        # degraded=0 would be indistinguishable from a genuine sample reporting
        # no findings. This was live rather than theoretical - adding the write
        # to _precommit_doc_sync turned three previously side-effect-free tests
        # in TestPrecommitDocSyncSeverity into producers, and nine junk rows
        # reached the real store before it was noticed.
        #
        # Checked at the source rather than at runtime, because a runtime check
        # only catches the test that happens to run.
        source = Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        offenders = []
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            setup_redirects = any(
                "FIRE_LOG" in ast.dump(fn)
                for fn in cls.body
                if isinstance(fn, ast.FunctionDef) and fn.name == "setUp"
            )
            for fn in cls.body:
                if not isinstance(fn, ast.FunctionDef):
                    continue
                dumped = ast.dump(fn)
                if "_precommit_doc_sync" not in dumped and "run_precommit" not in dumped:
                    continue
                if "FIRE_LOG" in dumped or setup_redirects:
                    continue
                offenders.append(f"{cls.name}.{fn.name}")
        # Allow only the two that mock _precommit_doc_sync itself, so the real
        # function - and therefore the write - is never entered.
        allowed = {
            "TestPrecommitDocSync.test_personal_data_block_skips_doc_sync",
            "TestPrecommitDocSync.test_clean_personal_data_runs_doc_sync_and_allows",
        }
        self.assertEqual(
            sorted(set(offenders) - allowed), [],
            "these tests reach the fire-log write without redirecting FIRE_LOG")

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
