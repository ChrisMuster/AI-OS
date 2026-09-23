#!/usr/bin/env python3
"""Unit tests for verify.py's Codex hook trust check.

Codex runs a project hook only while its stored approval fingerprint matches the
hook as written; editing a hook silently switches it off. From 2026-07-31 to
2026-09-23 every project hook was switched off that way and nothing reported it.
The check asks Codex itself (its app-server's hooks/list request) and fails when
any project hook would not run.

Covered here:
- the judgement, including the positive control: the statuses Codex actually
  returned on 2026-09-23 must produce a FAIL;
- how the check reacts to a missing Codex, an unanswered question and a
  project with no hooks;
- the search order for the Codex executable and the two path spellings;
- the stdio protocol, against a fake app-server run as a real subprocess;
- two live checks against the installed Codex, skipped visibly when none is
  installed: the protocol works end to end, and a folder whose hook nobody
  approved can never pass.

Standard library only.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import verify  # noqa: E402

UPPER = "C:/project"
LOWER = "c:/project"


def _hook(event, status, matcher=None, source="project", enabled=True):
    return {"eventName": event, "matcher": matcher, "source": source,
            "trustStatus": status, "enabled": enabled,
            "key": f"{event}:{matcher}", "currentHash": "sha256:0"}


def _entry(cwd, hooks, errors=()):
    return {"cwd": cwd, "hooks": list(hooks), "errors": list(errors), "warnings": []}


def _project_hooks(status):
    return [_hook("preToolUse", status, "Bash"), _hook("preToolUse", status, "apply_patch"),
            _hook("sessionStart", status), _hook("stop", status)]


# What this project's .codex/config.toml defines, in the config's own spelling.
DEFINED = [("PreToolUse", "Bash"), ("PreToolUse", "apply_patch"),
           ("SessionStart", None), ("Stop", None)]


def _judge(entries, defined=DEFINED, cwds=None):
    if cwds is None:
        cwds = [entry["cwd"] for entry in entries] or [UPPER]
    return verify.judge_codex_hooks(entries, defined, cwds)


# The statuses Codex returned for this project on 2026-09-23, before the user
# re-approved the hooks: all four project hooks "modified", plus the bundled
# browser plugin's never-approved Stop hook.
OBSERVED_2026_09_23 = [_entry(UPPER, _project_hooks("modified")
                              + [_hook("stop", "untrusted", source="plugin")])]


class TestJudgement(unittest.TestCase):
    def test_positive_control_the_observed_outage_fails(self):
        result = _judge(OBSERVED_2026_09_23)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(result["surface"])
        self.assertIn("4 project hook(s)", result["detail"])

    def test_positive_control_a_partial_list_fails(self):
        # Review finding R1-1: four hooks defined, Codex lists one, trusted.
        # The check before the repair passed this.
        result = _judge([_entry(UPPER, [_hook("stop", "trusted")])])
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(result["surface"])
        self.assertIn("does not list 3 project hook(s)", result["detail"])
        self.assertIn("PreToolUse Bash", result["detail"])

    def test_a_second_command_on_one_event_is_counted(self):
        defined = DEFINED + [("Stop", None)]
        result = _judge([_entry(UPPER, _project_hooks("trusted"))], defined=defined)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("Stop under", result["detail"])

    def test_config_and_codex_event_spellings_match(self):
        self.assertEqual(verify._hook_identity("PreToolUse", "Bash"),
                         verify._hook_identity("preToolUse", "Bash"))
        self.assertEqual(verify._hook_identity("SessionStart", None),
                         verify._hook_identity("sessionStart", ""))

    def test_plugin_hooks_are_not_judged(self):
        entries = [_entry(UPPER, _project_hooks("trusted")
                          + [_hook("stop", "untrusted", source="plugin")])]
        self.assertEqual(_judge(entries)["status"], "PASS")

    def test_a_plugin_hook_does_not_stand_in_for_a_missing_project_hook(self):
        hooks = _project_hooks("trusted")[:3] + [_hook("stop", "trusted", source="plugin")]
        self.assertEqual(_judge([_entry(UPPER, hooks)])["status"], "FAIL")

    def test_all_trusted_under_both_spellings_passes(self):
        entries = [_entry(UPPER, _project_hooks("trusted")), _entry(LOWER, _project_hooks("trusted"))]
        result = _judge(entries)
        self.assertEqual(result["status"], "PASS")
        self.assertIn("all 4 project hooks", result["detail"])
        self.assertIn("2 path spelling(s)", result["detail"])

    def test_one_spelling_unapproved_fails_and_names_it(self):
        entries = [_entry(UPPER, _project_hooks("trusted")), _entry(LOWER, _project_hooks("modified"))]
        result = _judge(entries)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn(LOWER, result["detail"])
        self.assertNotIn(f"under {UPPER}", result["detail"])

    def test_never_approved_fails(self):
        result = _judge([_entry(UPPER, _project_hooks("untrusted"))])
        self.assertEqual(result["status"], "FAIL")

    def test_managed_counts_as_approved(self):
        result = _judge([_entry(UPPER, _project_hooks("managed"))])
        self.assertEqual(result["status"], "PASS")

    def test_disabled_hook_fails(self):
        hooks = _project_hooks("trusted")
        hooks[0]["enabled"] = False
        result = _judge([_entry(UPPER, hooks)])
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("disabled", result["detail"])

    def test_fail_message_warns_against_trust_all(self):
        detail = _judge(OBSERVED_2026_09_23)["detail"]
        self.assertIn("/hooks", detail)
        self.assertIn("trust all", detail)

    def test_no_project_hooks_listed_fails(self):
        # Chosen 2026-09-23: none listed is the whole of "some missing", so it
        # fails rather than warns.
        result = _judge([_entry(UPPER, [_hook("stop", "untrusted", source="plugin")])])
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(result["surface"])
        self.assertIn("does not list 4 project hook(s)", result["detail"])

    def test_one_spelling_with_no_project_hooks_fails(self):
        entries = [_entry(UPPER, _project_hooks("trusted")), _entry(LOWER, [])]
        result = _judge(entries)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn(f"under {LOWER}", result["detail"])

    def test_a_spelling_codex_did_not_answer_for_is_not_a_pass(self):
        result = _judge([_entry(UPPER, _project_hooks("trusted"))], cwds=[UPPER, LOWER])
        self.assertEqual(result["status"], "WARN")
        self.assertTrue(result["surface"])
        self.assertIn(LOWER, result["detail"])

    def test_no_answer_at_all_is_not_a_pass(self):
        self.assertNotEqual(_judge([], cwds=[UPPER])["status"], "PASS")

    def test_codex_errors_are_a_surfaced_warning(self):
        entries = [_entry(UPPER, _project_hooks("trusted"), errors=[{"message": "bad toml", "path": "x"}])]
        result = _judge(entries)
        self.assertEqual(result["status"], "WARN")
        self.assertTrue(result["surface"])
        self.assertIn("bad toml", result["detail"])


class TestCheckOutcomes(unittest.TestCase):
    def test_no_hooks_defined_passes_without_asking_codex(self):
        with mock.patch.object(verify, "_project_codex_hooks", return_value=[]), \
                mock.patch.object(verify, "_codex_binary") as finder:
            result = verify.check_codex_hooks()
        self.assertEqual(result["status"], "PASS")
        finder.assert_not_called()

    def test_codex_not_installed_is_a_quiet_warning(self):
        with mock.patch.object(verify, "_project_codex_hooks", return_value=DEFINED), \
                mock.patch.object(verify, "_codex_binary", return_value=None):
            result = verify.check_codex_hooks()
        self.assertEqual(result["status"], "WARN")
        self.assertFalse(result.get("surface"))

    def test_unanswered_question_is_a_surfaced_warning(self):
        with mock.patch.object(verify, "_project_codex_hooks", return_value=DEFINED), \
                mock.patch.object(verify, "_codex_binary", return_value=Path("codex")), \
                mock.patch.object(verify, "_query_codex_hooks", side_effect=TimeoutError("no answer")):
            result = verify.check_codex_hooks()
        self.assertEqual(result["status"], "WARN")
        self.assertTrue(result["surface"])

    def test_observed_outage_fails_through_the_whole_check(self):
        with mock.patch.object(verify, "_project_codex_hooks", return_value=DEFINED), \
                mock.patch.object(verify, "_codex_binary", return_value=Path("codex")), \
                mock.patch.object(verify, "_query_codex_hooks", return_value=OBSERVED_2026_09_23):
            result = verify.check_codex_hooks()
        self.assertEqual(result["status"], "FAIL")

    def test_partial_list_fails_through_the_whole_check(self):
        # R1-1 end to end: the real config is read, only Codex's answer is faked.
        answer = [_entry(cwd, [_hook("stop", "trusted")]) for cwd in verify._project_root_spellings()]
        with mock.patch.object(verify, "_codex_binary", return_value=Path("codex")), \
                mock.patch.object(verify, "_query_codex_hooks", return_value=answer):
            result = verify.check_codex_hooks()
        self.assertEqual(result["status"], "FAIL")

    def test_missing_tomllib_is_a_surfaced_warning_not_a_crash(self):
        # Review finding R1-2: on a Python without tomllib the check raised
        # ModuleNotFoundError. A None entry in sys.modules makes the import fail.
        with mock.patch.dict(sys.modules, {"tomllib": None}), \
                mock.patch.object(verify, "_codex_binary") as finder:
            result = verify.check_codex_hooks()
        self.assertEqual(result["status"], "WARN")
        self.assertTrue(result["surface"])
        self.assertIn("3.11", result["detail"])
        finder.assert_not_called()

    def test_old_python_still_gets_its_floor_report(self):
        # R1-2 through the runner: an old Python without tomllib must still get
        # a full report carrying the floor FAIL. Antigravity CLI is used because
        # it has no MCP handshake to wait on.
        old = verify.floor_result("Python 3.13+", (3, 10, 0))
        with mock.patch.dict(sys.modules, {"tomllib": None}), \
                mock.patch.object(verify, "check_python_version", return_value=old):
            results = verify.run_checks("Antigravity CLI")
        by_name = {row["check"]: row for row in results}
        self.assertEqual(by_name["Python 3.13+"]["status"], "FAIL")
        self.assertEqual(by_name[verify.CODEX_HOOK_CHECK]["status"], "WARN")

    def test_runs_for_an_ai_that_is_not_codex(self):
        names = [row["check"] for row in verify.run_checks("Claude Code", dry_run=True)]
        self.assertIn(verify.CODEX_HOOK_CHECK, names)


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.exe = "codex.exe" if os.name == "nt" else "codex"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make(self, relative, mtime):
        path = self.tmp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8", newline="\n")
        os.utime(path, (mtime, mtime))
        return path

    def _find(self, which=None):
        with mock.patch.object(verify.shutil, "which", return_value=which), \
                mock.patch.dict(verify.os.environ, {"LOCALAPPDATA": str(self.tmp / "local")}), \
                mock.patch.object(verify.Path, "home", return_value=self.tmp / "home"):
            return verify._codex_binary()

    def test_path_wins(self):
        self._make(f"local/OpenAI/Codex/bin/a/{self.exe}", 100)
        self.assertEqual(self._find(which="/usr/bin/codex"), Path("/usr/bin/codex"))

    def test_newest_desktop_build(self):
        self._make(f"local/OpenAI/Codex/bin/old/{self.exe}", 100)
        newest = self._make(f"local/OpenAI/Codex/bin/new/{self.exe}", 200)
        self.assertEqual(self._find(), newest)

    def test_desktop_before_vscode(self):
        desktop = self._make(f"local/OpenAI/Codex/bin/a/{self.exe}", 100)
        self._make(f"home/.vscode/extensions/openai.chatgpt-1/bin/x/{self.exe}", 999)
        self.assertEqual(self._find(), desktop)

    def test_vscode_fallback(self):
        extension = self._make(f"home/.vscode/extensions/openai.chatgpt-1/bin/x/{self.exe}", 100)
        self.assertEqual(self._find(), extension)

    def test_nothing_installed(self):
        self.assertIsNone(self._find())

    def test_both_drive_spellings_on_windows(self):
        with mock.patch.object(verify.os, "name", "nt"), \
                mock.patch.object(verify, "PROJECT_ROOT", "c:\\proj"):
            self.assertEqual(verify._project_root_spellings(), ["C:\\proj", "c:\\proj"])

    def test_hooks_defined_reads_the_config(self):
        (self.tmp / ".codex").mkdir()
        config = self.tmp / ".codex" / "config.toml"
        with mock.patch.object(verify, "PROJECT_ROOT", self.tmp):
            self.assertEqual(verify._project_codex_hooks(), [])
            config.write_text('[hooks.state."k"]\ntrusted_hash = "x"\n', encoding="utf-8", newline="\n")
            self.assertEqual(verify._project_codex_hooks(), [])
            config.write_text('[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype = "command"\n'
                              'command = "x"\n', encoding="utf-8", newline="\n")
            self.assertEqual(verify._project_codex_hooks(), [("Stop", None)])
            # One pair per command: a group with two commands counts twice.
            config.write_text('[[hooks.PreToolUse]]\nmatcher = "Bash"\n'
                              '[[hooks.PreToolUse.hooks]]\ntype = "command"\ncommand = "a"\n'
                              '[[hooks.PreToolUse.hooks]]\ntype = "command"\ncommand = "b"\n',
                              encoding="utf-8", newline="\n")
            self.assertEqual(verify._project_codex_hooks(),
                             [("PreToolUse", "Bash"), ("PreToolUse", "Bash")])

    def test_this_project_defines_its_four_hooks(self):
        self.assertEqual(sorted(verify._project_codex_hooks(), key=str), sorted(DEFINED, key=str))


FAKE_SERVER = textwrap.dedent('''
    import json, os, sys, time
    mode = os.environ.get("FAKE_MODE", "ok")
    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg:
            continue
        if mode == "exit":
            sys.exit(0)
        if mode == "silent":
            time.sleep(30)
        if msg["method"] == "initialize":
            print(json.dumps({"method": "some/notification", "params": {}}), flush=True)
            print("not json", flush=True)
            print(json.dumps({"id": msg["id"], "result": {}}), flush=True)
        elif mode == "error":
            print(json.dumps({"id": msg["id"], "error": {"code": -1, "message": "boom"}}), flush=True)
        elif mode == "nodata":
            print(json.dumps({"id": msg["id"], "result": {}}), flush=True)
        else:
            data = [{"cwd": c, "hooks": [], "errors": [], "warnings": []} for c in msg["params"]["cwds"]]
            print(json.dumps({"id": msg["id"], "result": {"data": data}}), flush=True)
''')


class TestProtocol(unittest.TestCase):
    """_query_codex_hooks against a fake app-server run as a real subprocess."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.server = cls.tmp / "fake_app_server.py"
        cls.server.write_text(FAKE_SERVER, encoding="utf-8", newline="\n")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _query(self, mode, timeout=10):
        with mock.patch.dict(verify.os.environ, {"FAKE_MODE": mode}):
            return verify._query_codex_hooks([sys.executable, str(self.server)], [UPPER, LOWER],
                                             timeout=timeout)

    def test_answer_is_returned_past_notifications_and_noise(self):
        data = self._query("ok")
        self.assertEqual([entry["cwd"] for entry in data], [UPPER, LOWER])

    def test_error_reply_raises(self):
        with self.assertRaisesRegex(RuntimeError, "boom"):
            self._query("error")

    def test_silence_times_out(self):
        with self.assertRaises(TimeoutError):
            self._query("silent", timeout=1)

    def test_early_exit_raises(self):
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self._query("exit")

    def test_answer_without_data_raises(self):
        with self.assertRaises(ValueError):
            self._query("nodata")


class TestLiveCodex(unittest.TestCase):
    """Against the installed Codex. Skipped, visibly, where Codex is absent."""

    def setUp(self):
        self.codex = verify._codex_binary()
        if self.codex is None:
            self.skipTest("Codex is not installed on this machine")
        self.command = [str(self.codex), "app-server", "--listen", "stdio://"]

    def test_codex_answers_for_this_project(self):
        cwds = verify._project_root_spellings()
        entries = verify._query_codex_hooks(self.command, cwds)
        self.assertEqual(len(entries), len(cwds))
        result = verify.judge_codex_hooks(entries, verify._project_codex_hooks(), cwds)
        self.assertIn(result["status"], ("PASS", "FAIL"))
        # Whatever the approvals, Codex must list every hook the config defines;
        # this is what proves the config and Codex count hooks the same way.
        self.assertNotIn("does not list", result["detail"])

    def test_a_folder_nobody_approved_cannot_pass(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / ".codex").mkdir()
            (tmp / ".codex" / "config.toml").write_text(
                '[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype = "command"\ncommand = "python -c 0"\n',
                encoding="utf-8", newline="\n")
            subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
            entries = verify._query_codex_hooks(self.command, [str(tmp)])
            result = verify.judge_codex_hooks(entries, [("Stop", None)], [str(tmp)])
            self.assertNotEqual(result["status"], "PASS")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
