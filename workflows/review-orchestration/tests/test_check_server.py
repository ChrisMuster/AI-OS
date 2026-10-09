#!/usr/bin/env python3
"""Tests for check_server.py, the check helper (orchestrator isolation plan section 6,
stage S5).

Docker is never started: ``container.make_copy`` and ``container.run_in_container``
are replaced by fakes that record what they were given, and the copy folder is moved
into the test's own temporary directory. Each test builds a frozen folder of its own
with a ``hook.json``, an ignore floor and two small verification lists, so what is
listed is stated here rather than read from the shipped lists. Two tests run the real
helper as a stdio MCP server from a frozen folder holding the shipped files, as a
builder session will. Expected values are stated by the tests, never computed by the
code under test.

Same three controls as the other suites:

    positive control  a call the plan allows. It must be allowed.
    rejection control a call the plan refuses. It must be refused, the reason named.
    negative control  something that looks like a refused shape but is not one.

    python workflows/review-orchestration/tests/test_check_server.py
"""

import asyncio
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

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
approver = _load("approver")
codex_rules = _load("codex_rules")
container = _load("container")
check_server = _load("check_server")

RUN_ID = "20261008-101500-ab12"
HEAD = "0123456789abcdef0123456789abcdef01234567"
IMAGE = "bookdragon-checks:0123456789ab"
CLAUDE_ONLY = "python workflows/close-out/scripts/run.py --read-only"
CODEX_ONLY = "python workflows/encoding-guard/scripts/run.py --check"
BOTH = "python workflows/x/tests/test_y.py -v"
FLOOR = [".env", "USER.md", "memory/"]
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")


def folder_bytes(folder):
    """A SHA-256 for every file in a frozen folder, bytecode caches included: the
    helper writes none (code review R20-1), as the loop's turn check now requires."""
    folder = Path(folder)
    return {path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(folder.rglob("*")) if path.is_file()}


def changed(before, after):
    return {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}


def write_lists(folder):
    (folder / "verify-commands.txt").write_text(
        "# Claude's list\n"
        r"python workflows/close-out/scripts/run\.py --read-only( --json)?" "\n"
        r"python workflows/x/tests/test_y\.py( -v)?" "\n", encoding="utf-8", newline="\n")
    (folder / "codex-verify-commands.txt").write_text(
        "# Codex's list\n"
        r"python workflows/encoding-guard/scripts/run\.py --check" "\n"
        r"python workflows/x/tests/test_y\.py( -v)?" "\n", encoding="utf-8", newline="\n")


def config(root, provider="claude", **changes):
    value = {"run_id": RUN_ID, "edit_paths": ["notes/"], "project_root": str(root),
             "builder_provider": provider, "public_head": HEAD, "image_tag": IMAGE}
    value.update(changes)
    return value


class HelperCase(unittest.TestCase):
    """A frozen folder named for the run, a project root and a copies folder, all in
    one temporary directory; fakes for the copy and the container."""

    provider = "claude"

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / "project"
        self.root.mkdir()
        self.folder = self.base / "book-dragon-orchestration" / RUN_ID
        self.folder.mkdir(parents=True)
        write_lists(self.folder)
        self.write_config(config(self.root, self.provider))
        (self.folder / "start-ignored.txt").write_text(
            "".join(f"{p}\n" for p in FLOOR), encoding="utf-8", newline="\n")
        self.copies = self.base / "book-dragon-copies" / RUN_ID
        self.made, self.ran = [], []
        self.result = (0, "all passed\n", "")
        self.patch(container, "copy_dir", lambda run_id, n: (
            self.base / "book-dragon-copies" / run_id / f"copy-{n}"))
        self.patch(container, "make_copy", self.fake_make_copy)
        self.patch(container, "run_in_container", self.fake_run)

    def patch(self, target, name, value):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_config(self, value):
        (self.folder / "hook.json").write_text(json.dumps(value), encoding="utf-8",
                                               newline="\n")

    def fake_make_copy(self, root, dest, public_head, floor):
        self.made.append((Path(root), Path(dest), public_head, list(floor)))
        (Path(dest) / ".git").mkdir(parents=True)
        (Path(dest) / "file.txt").write_bytes(b"x\n")
        return dest

    def fake_run(self, copy, argv, image, timeout=None):
        self.ran.append((Path(copy), list(argv), image))
        self.assertTrue(Path(copy).is_dir(), "the copy exists while the check runs")
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    def check(self, command):
        return check_server.run_check(command, self.folder)

    def calls(self):
        path = self.folder / "check-calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def leftover_copies(self):
        return sorted(p.name for p in self.copies.iterdir()) if self.copies.is_dir() else []

    def refused(self, command, reason):
        reply = self.check(command)
        self.assertTrue(reply.startswith("Refused by the orchestrator:"), reply)
        self.assertIn(reason, reply)
        self.assertEqual((self.made, self.ran), ([], []), "nothing was copied or run")
        line = self.calls()[-1]
        self.assertEqual((line["allowed"], line["exit"]), (False, None))
        return reply


# ---------------------------------------------------------------------------
# Judging the command
# ---------------------------------------------------------------------------
class ClaudeListTests(HelperCase):

    def test_positive_a_listed_command_runs_in_the_container(self):
        reply = self.check(CLAUDE_ONLY)
        self.assertEqual(self.made, [(self.root, self.copies / "copy-1", HEAD, FLOOR)])
        self.assertEqual(self.ran, [(self.copies / "copy-1",
                                     ["python", "workflows/close-out/scripts/run.py",
                                      "--read-only"], IMAGE)])
        self.assertEqual(reply, "exit 0\n--- stdout ---\nall passed\n\n--- stderr ---\n")
        self.assertEqual(self.leftover_copies(), [], "the copy is removed after use")

    def test_negative_surrounding_space_is_trimmed_before_matching(self):
        self.check(f"  {BOTH}  ")
        self.assertEqual(self.ran[0][1], ["python", "workflows/x/tests/test_y.py", "-v"])

    def test_rejection_a_command_on_no_list(self):
        self.refused("git push", "verification list")

    def test_rejection_a_command_on_the_codex_list_only(self):
        self.refused(CODEX_ONLY, "verification list")

    def test_rejection_a_listed_command_with_more_after_it(self):
        # The whole command must match a line, not begin with one.
        self.refused(f"{CLAUDE_ONLY} --repair", "verification list")
        self.refused(f"{BOTH}; git push", "verification list")

    def test_rejection_the_shell_checks_come_first(self):
        # Each would be refused whatever the list held; the reason is the shell check's.
        self.refused(f"{BOTH}\n{BOTH}", "one command per call")
        self.refused("python workflows/x/tests/../tests/test_y.py", "`..`")
        self.refused("python workflows/x/tests/test_y.py .env", "`.env`")

    def test_rejection_no_command(self):
        for command in ("", "   ", None, 7, ["python"]):
            with self.subTest(command=command):
                self.refused(command, "no command was given")


class CodexListTests(HelperCase):

    provider = "codex"

    def test_positive_a_command_on_the_codex_list_runs(self):
        reply = self.check(CODEX_ONLY)
        self.assertTrue(reply.startswith("exit 0\n"), reply)
        self.assertEqual(self.ran[0][1], ["python", "workflows/encoding-guard/scripts/run.py",
                                          "--check"])

    def test_rejection_a_command_on_the_claude_list_only(self):
        self.refused(CLAUDE_ONLY, "verification list")


class ConfigTests(HelperCase):

    def test_rejection_settings_that_cannot_be_used(self):
        cases = {
            "missing": None,
            "not json": "{",
            "not an object": "[]",
            "a key missing": config(self.root, image_tag=None),
            "a key empty": config(self.root, public_head=""),
            "a key not text": config(self.root, project_root=7),
            "another run": config(self.root, run_id="20261008-101500-ffff"),
            "a malformed run id": config(self.root, run_id="../x"),
            "an unknown builder": config(self.root, provider="gemini"),
        }
        for label, value in cases.items():
            with self.subTest(label):
                path = self.folder / "hook.json"
                path.unlink(missing_ok=True)
                if isinstance(value, str):
                    path.write_text(value, encoding="utf-8", newline="\n")
                elif value is not None:
                    self.write_config(value)
                reply = self.refused(BOTH, "")
                self.assertNotIn("exit", reply)

    def test_rejection_a_folder_and_run_id_that_agree_but_are_not_a_run_id(self):
        # The run id must have the run-record form as well as name the folder.
        odd = self.folder.parent / "not-a-run"
        shutil.copytree(self.folder, odd)
        (odd / "hook.json").write_text(json.dumps(config(self.root, run_id="not-a-run")),
                                       encoding="utf-8", newline="\n")
        reply = check_server.run_check(BOTH, odd)
        self.assertEqual(reply, "Refused by the orchestrator: the helper was not started "
                                "for this run.")
        self.assertEqual(self.ran, [])

    def test_rejection_a_verification_list_that_cannot_be_read(self):
        (self.folder / "verify-commands.txt").unlink()
        self.refused(BOTH, "cannot read verify-commands.txt")


# ---------------------------------------------------------------------------
# The reply and the copy
# ---------------------------------------------------------------------------
class ReplyTests(HelperCase):

    def test_positive_long_output_keeps_its_last_6000_characters(self):
        out = "a" * 10 + "b" * 6000
        err = "c" * 6001
        self.result = (1, out, err)
        reply = self.check(BOTH)
        self.assertEqual(reply,
                         "exit 1\n"
                         "--- stdout (last 6000 of 6010 characters) ---\n" + "b" * 6000 + "\n"
                         "--- stderr (last 6000 of 6001 characters) ---\n" + "c" * 6000)

    def test_negative_output_of_exactly_6000_characters_is_not_cut(self):
        self.result = (0, "d" * 6000, "")
        self.assertIn("--- stdout ---\n" + "d" * 6000 + "\n", self.check(BOTH))

    def test_positive_a_failing_check_reports_its_exit_code(self):
        self.result = (2, "", "usage error\n")
        self.assertEqual(self.check(BOTH),
                         "exit 2\n--- stdout ---\n\n--- stderr ---\nusage error\n")

    def test_rejection_docker_failing_gives_an_error_and_removes_the_copy(self):
        self.result = container.ContainerError("the Docker engine did not answer")
        reply = self.check(BOTH)
        self.assertEqual(reply, "error: the Docker engine did not answer")
        self.assertEqual(self.leftover_copies(), [])
        line, = self.calls()
        self.assertEqual((line["allowed"], line["exit"]), (True, None))

    def test_rejection_a_copy_that_fails_part_way_is_removed(self):
        def half(root, dest, public_head, floor):
            (Path(dest) / "partial").mkdir(parents=True)
            raise container.ContainerError("git clone failed: no such commit")
        self.patch(container, "make_copy", half)
        self.assertEqual(self.check(BOTH), "error: git clone failed: no such commit")
        self.assertEqual(self.ran, [])
        self.assertEqual(self.leftover_copies(), [])
        self.assertEqual(self.calls()[-1]["exit"], None)

    def test_rejection_a_missing_ignore_floor_is_an_error_and_nothing_is_copied(self):
        (self.folder / "start-ignored.txt").unlink()
        reply = self.check(BOTH)
        self.assertTrue(reply.startswith("error: "), reply)
        self.assertEqual((self.made, self.ran), ([], []))

    def test_rejection_a_copy_that_cannot_be_removed_is_reported(self):
        def stuck(dest):
            raise PermissionError("in use")
        self.patch(container, "remove_copy", stuck)
        reply = self.check(BOTH)
        self.assertTrue(reply.startswith("error: the copy "), reply)
        self.assertIn("could not be removed", reply)
        self.assertIn("the check had exited 0", reply)
        self.assertEqual(self.calls()[-1]["exit"], 0)

    def test_positive_each_call_gets_a_fresh_copy_numbered_after_the_last(self):
        (self.copies / "copy-3").mkdir(parents=True)
        (self.copies / "copy-x").mkdir()
        self.check(BOTH)
        self.check(BOTH)
        self.assertEqual([made[1].name for made in self.made], ["copy-4", "copy-4"])
        self.assertEqual(self.leftover_copies(), ["copy-3", "copy-x"])


# ---------------------------------------------------------------------------
# The calls file
# ---------------------------------------------------------------------------
class RecordTests(HelperCase):

    def test_positive_one_line_per_call_and_never_the_output(self):
        self.result = (0, "SECRET-OUTPUT", "SECRET-ERROR")
        clock = iter([10.0, 12.5, 20.0, 20.25])
        check_server.run_check(BOTH, self.folder, clock=lambda: next(clock))
        check_server.run_check("git push", self.folder, clock=lambda: next(clock))
        self.assertEqual(self.calls(), [
            {"command": BOTH, "allowed": True, "exit": 0, "seconds": 2.5},
            {"command": "git push", "allowed": False, "exit": None, "seconds": 0.25}])
        text = (self.folder / "check-calls.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("SECRET", text)

    def test_positive_non_ascii_is_escaped(self):
        self.check("python workflows/x/tests/café.py")
        raw = (self.folder / "check-calls.jsonl").read_bytes()
        self.assertTrue(raw.isascii(), raw)
        self.assertEqual(self.calls()[-1]["command"], "python workflows/x/tests/café.py")

    def test_positive_a_command_that_is_not_text_is_recorded_as_written(self):
        self.check(["python"])
        self.assertEqual(self.calls()[-1]["command"], "['python']")

    def test_rejection_a_call_that_cannot_be_recorded_gets_no_result(self):
        (self.folder / "check-calls.jsonl").mkdir()
        reply = self.check(BOTH)
        self.assertTrue(reply.startswith("error: the call could not be recorded"), reply)
        self.assertNotIn("exit 0", reply)

    def test_negative_the_helper_writes_nothing_else_in_the_folder(self):
        # Compared byte for byte, so a rewritten hook.json or list is seen too (R16-1).
        before = folder_bytes(self.folder)
        self.check(BOTH)
        self.check("git push")
        self.result = container.ContainerError("Docker failed")
        self.check(BOTH)
        self.assertEqual(changed(before, folder_bytes(self.folder)), {"check-calls.jsonl"})


# ---------------------------------------------------------------------------
# The clock
# ---------------------------------------------------------------------------
class TimestampTests(HelperCase):

    def test_positive_local_time_with_its_offset_to_the_second(self):
        before = datetime.now().astimezone()
        value = check_server.timestamp()
        after = datetime.now().astimezone()
        self.assertRegex(value, TIMESTAMP)
        parsed = datetime.fromisoformat(value)
        self.assertEqual(parsed.utcoffset(), before.utcoffset())
        self.assertLessEqual(before.replace(microsecond=0), parsed)
        self.assertLessEqual(parsed, after)
        self.assertLess((after - parsed).total_seconds(), 1)

    def test_negative_it_writes_nothing(self):
        # The helper's own folder is this test's frozen folder, so a write there, to
        # any file, is seen byte for byte (R16-1).
        self.patch(check_server, "FOLDER", self.folder)
        before = folder_bytes(self.folder)
        check_server.timestamp()
        self.assertEqual(folder_bytes(self.folder), before)


# ---------------------------------------------------------------------------
# The server, run from a frozen folder as a builder session will
# ---------------------------------------------------------------------------
class ServerTests(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        self.cwd = base / "elsewhere"
        self.cwd.mkdir()
        self.folder = base / "book-dragon-orchestration" / RUN_ID
        self.folder.mkdir(parents=True)
        for name in codex_rules.FROZEN_MODULES:
            shutil.copyfile(SCRIPTS / name, self.folder / name)
        for name in codex_rules.FROZEN_LISTS:
            shutil.copyfile(CONFIG / name, self.folder / name)
        (self.folder / "hook.json").write_text(json.dumps(config(base / "project")),
                                               encoding="utf-8", newline="\n")

    def test_positive_the_frozen_helper_needs_nothing_from_the_project(self):
        # Loaded from the frozen folder alone, it judges by the frozen Claude list
        # there: an unlisted command is refused for the list, and a listed one gets
        # past it to the copy step, which fails here on the missing ignore floor
        # before anything is copied or Docker is called. It is run as a file, as a
        # builder session starts it (a script is never cached), not imported, so the
        # bytecode check sees only what the helper's own imports would write.
        code = ("import runpy, sys; sys.path.insert(0, sys.argv[1]); "
                "h = runpy.run_path(sys.argv[1] + '/check_server.py', run_name='helper'); "
                "print(h['run_check']('git push')); "
                "print(h['run_check']('python -m py_compile workflows/x.py')); "
                "h['timestamp']()")
        before = folder_bytes(self.folder)
        # Nothing in the environment asks for no bytecode: the helper does it itself.
        environ = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}
        done = subprocess.run([sys.executable, "-c", code, str(self.folder)],
                              capture_output=True, encoding="utf-8", timeout=60,
                              cwd=str(self.cwd), env=environ)
        self.assertEqual(done.returncode, 0, done.stderr)
        refused, listed = done.stdout.splitlines()[:2]
        self.assertIn("Refused by the orchestrator:", refused)
        self.assertIn("verification list", refused)
        self.assertTrue(listed.startswith("error: "), listed)
        self.assertIn("start-ignored.txt", listed)
        lines = [json.loads(raw) for raw in (self.folder / "check-calls.jsonl")
                 .read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(line["command"], line["allowed"]) for line in lines],
                         [("git push", False),
                          ("python -m py_compile workflows/x.py", True)])
        # The real helper changed nothing in its folder but its calls file (R16-1).
        self.assertEqual(changed(before, folder_bytes(self.folder)), {"check-calls.jsonl"})

    def test_positive_the_stdio_server_offers_exactly_the_two_tools(self):
        async def session():
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            params = StdioServerParameters(command=sys.executable,
                                           args=[str(self.folder / "check_server.py")],
                                           cwd=str(self.cwd))
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    tools = await client.list_tools()
                    stamp = await client.call_tool("get_timestamp", {})
                    refused = await client.call_tool("run_check", {"command": "git push"})
                    return tools, stamp, refused

        before = folder_bytes(self.folder)
        tools, stamp, refused = asyncio.run(asyncio.wait_for(session(), 120))
        # The real server changed nothing in its folder but its calls file (R16-1).
        self.assertEqual(changed(before, folder_bytes(self.folder)), {"check-calls.jsonl"})
        by_name = {tool.name: tool for tool in tools.tools}
        self.assertEqual(set(by_name), {"run_check", "get_timestamp"})
        self.assertEqual(by_name["run_check"].inputSchema["required"], ["command"])
        self.assertEqual(by_name["get_timestamp"].inputSchema.get("properties", {}), {})
        self.assertRegex(stamp.content[0].text, TIMESTAMP)
        self.assertIn("Refused by the orchestrator:", refused.content[0].text)
        line, = [json.loads(raw) for raw in (self.folder / "check-calls.jsonl")
                 .read_text(encoding="utf-8").splitlines()]
        self.assertEqual((line["command"], line["allowed"]), ("git push", False))


if __name__ == "__main__":
    unittest.main()
