#!/usr/bin/env python3
"""
check_server.py - the check helper: a builder's one way to run a check, and its clock
(orchestrator isolation, plan section 6, stage S5).

A stdio MCP server (the ``mcp`` 1.x ``FastMCP`` API) with two tools:

  run_check(command)  Refuses a command that fails ``approver.shell_checks`` or does
                      not fully match a line of the builder's verification list. Any
                      other command runs in the offline container over a fresh clean
                      copy of the project, which is then removed; the reply is
                      ``exit <code>`` and the last characters of stdout and stderr, or
                      ``error: <reason>`` when Docker or git fails.
  get_timestamp()     The host's local time with its offset, as Biblio Tools'
                      ``get_timestamp`` gives it, for a builder's LOG.md entries.

It always runs from a run's frozen folder (``<temp>/book-dragon-orchestration/
<run-id>/``) and reads nothing of the project's own: the run's settings come from the
folder's ``hook.json`` (project root, run id, builder provider, public head, image
tag), the ignore floor from its ``start-ignored.txt``, and the verification list from
the copy beside it. Every ``run_check`` call adds one line to the folder's
``check-calls.jsonl`` (command, allowed, exit code, seconds; never the output); the
helper writes nothing else there. ``get_timestamp`` writes nothing.

Built and tested in S5. The orchestrator writes the folder and its settings from S6a,
the image tag from S6b, and gives the helper to the builders from S7 (the user's
decisions 27 and 28, 2026-10-08). Standard library and this workflow's own modules;
``mcp`` is imported only to serve.
"""

import json
import shlex
import sys
import time
from datetime import datetime
from pathlib import Path

# Run from the frozen folder, where nothing but the two record files may be written
# after setup (plan 7.0 item 3): no bytecode cache for the modules below (code review
# R20-1).
sys.dont_write_bytecode = True

import approver as approver_mod  # noqa: E402
import codex_rules  # noqa: E402
import container  # noqa: E402

FOLDER = Path(__file__).resolve().parent
SERVER_NAME = "checks"
OUTPUT_TAIL = 6000
# The verification list each builder's commands are judged by.
LISTS = {"claude": "verify-commands.txt", "codex": "codex-verify-commands.txt"}
CONFIG_KEYS = ("project_root", "run_id", "builder_provider", "public_head", "image_tag")


class HelperError(Exception):
    """The run's settings in the frozen folder cannot be used."""


def timestamp():
    """The host's current local time with its offset, to the second."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_config(folder):
    """The run's settings from ``hook.json``. Every key must be a non-empty string,
    the run id must be the folder's own name, and the builder a known provider."""
    folder = Path(folder)
    try:
        config = json.loads((folder / codex_rules.HOOK_CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HelperError(f"the run's settings cannot be read ({type(exc).__name__})") from exc
    if not isinstance(config, dict):
        raise HelperError("the run's settings are not a JSON object")
    missing = [key for key in CONFIG_KEYS
               if not isinstance(config.get(key), str) or not config.get(key)]
    if missing:
        raise HelperError("the run's settings lack " + ", ".join(missing))
    if not codex_rules.RUN_ID.match(config["run_id"]) or config["run_id"] != folder.name:
        raise HelperError("the helper was not started for this run")
    if config["builder_provider"] not in LISTS:
        raise HelperError(f"the run's builder `{config['builder_provider']}` is not a "
                          "provider the helper knows")
    return config


def refusal(command, folder, config):
    """Why ``command`` may not run, or None. ``command`` is already trimmed."""
    if not command:
        return "Refused by the orchestrator: no command was given."
    reason = approver_mod.shell_checks(command)
    if reason is not None:
        return reason
    patterns = approver_mod.load_verify_commands(
        Path(folder) / LISTS[config["builder_provider"]])
    if not any(pattern.fullmatch(command) for pattern in patterns):
        return ("Refused by the orchestrator: only the commands on this run's "
                "verification list can be run as checks.")
    try:
        shlex.split(command)
    except ValueError:
        return "Refused by the orchestrator: the command's quoting cannot be read."
    return None


def _tail(name, text):
    text = text or ""
    if len(text) <= OUTPUT_TAIL:
        return f"--- {name} ---\n{text}"
    return f"--- {name} (last {OUTPUT_TAIL} of {len(text)} characters) ---\n{text[-OUTPUT_TAIL:]}"


def _next_copy(run_id):
    """The next unused copy folder for the run: one past the highest number there."""
    parent = container.copy_dir(run_id, 0).parent
    numbers = [int(path.name[5:]) for path in parent.glob("copy-*")
               if path.name[5:].isdigit()] if parent.is_dir() else []
    return container.copy_dir(run_id, max(numbers, default=0) + 1)


def _record(folder, command, allowed, code, seconds):
    """Append the call's line. Non-ASCII is escaped, as every run record file is."""
    line = {"command": command if isinstance(command, str) else repr(command),
            "allowed": allowed, "exit": code, "seconds": round(seconds, 3)}
    with open(Path(folder) / codex_rules.CHECK_CALLS_FILE, "a", encoding="utf-8",
              newline="\n") as handle:
        handle.write(json.dumps(line) + "\n")


def run_check(command, folder=FOLDER, clock=time.monotonic):
    """Judge ``command`` and, if allowed, run it in the container over a fresh clean
    copy. Returns the reply text. Every call is recorded, and a call that cannot be
    recorded is answered with an error instead of its result."""
    started = clock()
    trimmed = command.strip() if isinstance(command, str) else ""
    allowed, code = False, None
    try:
        config = load_config(folder)
        reason = refusal(trimmed, folder, config)
    except (HelperError, approver_mod.ApproverError) as exc:
        reason = f"Refused by the orchestrator: {exc}."
    if reason is not None:
        reply = reason
    else:
        allowed = True
        dest = None
        try:
            floor = container.read_start_ignored(Path(folder) / codex_rules.START_IGNORED)
            dest = _next_copy(config["run_id"])
            container.make_copy(config["project_root"], dest, config["public_head"], floor)
            code, out, err = container.run_in_container(dest, shlex.split(trimmed),
                                                        config["image_tag"])
            reply = f"exit {code}\n{_tail('stdout', out)}\n{_tail('stderr', err)}"
        except (container.ContainerError, OSError, ValueError) as exc:
            reply = f"error: {exc}"
        if dest is not None:
            try:
                container.remove_copy(dest)
            except OSError as exc:
                ran = f"; the check had exited {code}" if code is not None else ""
                reply = (f"error: the copy {Path(dest).as_posix()} could not be removed "
                         f"({exc}){ran}")
    try:
        _record(folder, command, allowed, code, clock() - started)
    except OSError as exc:
        return f"error: the call could not be recorded ({exc})"
    return reply


def build_server(folder=FOLDER):
    """The MCP server, with the two tools bound to ``folder``."""
    from mcp.server.fastmcp import FastMCP  # runtime-guard: launched via the orchestrator's builder session config, under the run's .venv interpreter

    server = FastMCP(SERVER_NAME)

    @server.tool(name="run_check")
    def run_check_tool(command: str) -> str:
        """Run one command from this run's verification list in an offline
        container over a clean copy of the project. Replies `exit <code>` with the
        end of stdout and stderr, or the reason the command was refused."""
        return run_check(command, folder)

    @server.tool(name="get_timestamp")
    def get_timestamp_tool() -> str:
        """The current local time with its offset, for a LOG.md entry. Fetch it
        immediately before writing the entry."""
        return timestamp()

    return server


def main():
    build_server().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
