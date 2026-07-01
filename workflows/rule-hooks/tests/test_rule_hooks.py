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

import json
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
PROJECT_ROOT = SCRIPTS.parent.parent.parent
RUN_PY = SCRIPTS / "run.py"
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
