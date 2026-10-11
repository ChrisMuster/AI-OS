#!/usr/bin/env python3
"""
codex_rules.py - the decisions for a Codex builder's tool calls, and the approval
adapter (Stage A2 chunk (d); short plan sections 3.4, 3.6 and 3.7).

A Codex builder is held by a hook Codex runs before every tool call
(``codex_hook.py``). The hook decides each call here, with the run's ``Approver``:

  Bash         allowed when the whole trimmed command is a listed read command
               (``config/codex-read-commands.txt``) that also passes the approver's
               shell checks. Everything else is refused: since S7 a verification
               command runs only through the check helper (orchestrator isolation
               plan 7.5).
  apply_patch  allowed when every file the patch names, read from its header lines
               only, is one the approver allows as an edit, and none is under this
               workflow's ``runs/`` folder.
  the check    the helper's ``run_check``, when its command is on the Codex builder's
  helper       own verification list (``config/codex-verify-commands.txt``) and passes
               the helper's test, and ``get_timestamp`` with no arguments; both judged
               by the approver.
  anything     refused. That covers web search, the clock, image generation, goals,
  else         every other MCP tool, plugin install, the JavaScript REPL and every
               sub-agent tool.

After this module allows a ``Bash`` or ``apply_patch`` call, the hook runs the run's
trusted rule hooks on it (``run_rule_hook``, plan 7.6 item 3), from the frozen
folder's ``trusted/`` copy, and a block from them is the call's deny.

The approval handler is the second layer behind the hook (``approval_reply``). It
reads no command text: a request is accepted only when the hook recorded an ``allow``
for the same call id, so a shell command or a patch does not run when the hook failed
to judge it.

This module and everything it imports (``approver.py``, ``brief.py``) are copied into a
run's frozen folder and run from there, so nothing here may depend on where the file
sits: the project root always comes from the caller. Standard library only.
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import approver as approver_mod
from approver import Decision

_WORKFLOW_DIR = Path(__file__).resolve().parent.parent
READ_COMMANDS_PATH = _WORKFLOW_DIR / "config" / "codex-read-commands.txt"
VERIFY_COMMANDS_PATH = _WORKFLOW_DIR / "config" / "codex-verify-commands.txt"

# What a run's frozen folder holds besides hook.json and the two record files. The
# check helper (check_server.py) and the container module it calls run from there too,
# with the Claude builder's verification list, since the helper judges either builder
# (orchestrator isolation plan 6, item 1). The first two lists keep their places:
# callers index them.
FROZEN_MODULES = ("codex_hook.py", "codex_rules.py", "approver.py", "brief.py",
                  "check_server.py", "container.py")
FROZEN_LISTS = ("codex-verify-commands.txt", "codex-read-commands.txt",
                "verify-commands.txt")
HOOK_CONFIG = "hook.json"
DECISIONS_FILE = "hook-decisions.jsonl"
# The check helper's one line per call, and the ignore floor it reads (plan 5.3, 6).
CHECK_CALLS_FILE = "check-calls.jsonl"
START_IGNORED = "start-ignored.txt"

# The form runrecord.py issues; a test checks the two agree.
RUN_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")

# A project-relative path, bare or single-quoted, that cannot start with `/`, `~` or
# `-` and cannot hold a drive letter.
PATH_TOKEN = r"(?:(?![/~\-])[\w./\-]+|'(?![/~\-])[\w./\-]+')"
# A quoted search pattern. Not starting with `-`: the shell removes the quotes, and rg
# would read "--pre=python" as its option to run a program on every file searched. A
# double-quoted one holds no backtick or `$`, which the shell expands inside them.
QUOTED_TOKEN = r"""(?:"(?!-)[^"`$]*"|'(?!-)[^']*')"""

SHELL_TOOL = "Bash"
PATCH_TOOL = "apply_patch"
CHECK_TOOLS = (approver_mod.RUN_CHECK_TOOL, approver_mod.TIMESTAMP_TOOL)

# The run's trusted rule hooks (plan 7.6 item 3): the frozen folder's archive of the
# start commit's `workflows/rule-hooks/` (checks.TRUSTED_DIR, restated: this module is
# standard library only). Each process gets the project root and a fire log of its own
# run, outside the project and the frozen folder.
TRUSTED_RULE_HOOKS = ("trusted", "workflows", "rule-hooks", "scripts", "run.py")
RULE_HOOK_TIMEOUT_SECONDS = 20
RUNLOGS_PARENT = "book-dragon-runlogs"
FIRE_LOG_NAME = "fire-log.jsonl"
ROOT_ENV = "BOOK_DRAGON_ROOT"
FIRE_LOG_ENV = "BOOK_DRAGON_FIRE_LOG"
# The tools the project's rule hooks are registered for in `.codex/config.toml`; the
# Codex hook runs them for these and no other.
RULE_HOOK_TOOLS = (SHELL_TOOL, PATCH_TOOL)
_PATCH_BEGIN = "*** Begin Patch"
_PATCH_FILE_HEADERS = ("*** Add File: ", "*** Update File: ", "*** Delete File: ",
                       "*** Move to: ")
_PATCH_OTHER_LINES = (_PATCH_BEGIN, "*** End Patch", "*** End of File")
RUNS_PREFIX = "workflows/review-orchestration/runs/"

APPROVAL_METHODS = ("item/commandExecution/requestApproval",
                    "item/fileChange/requestApproval")
NO_HOOK_DECISION = "no hook decision for this call"

# Shell-call fields that would ask to run a command with more than the sandbox allows,
# or to add a lasting rule (code review finding R1-2). Whether Codex offers any of them
# under the run's approval policy is not established; a call carrying one with any
# value but the default is refused rather than judged, since the approval handler
# accepts by call id and would otherwise accept the override with the command.
SHELL_OVERRIDE_FIELDS = ("sandbox_permissions", "additional_permissions",
                         "with_escalated_permissions", "prefix_rule",
                         "dangerouslyDisableSandbox")
_OVERRIDE_DEFAULTS = (None, False, "", "use_default", [], {})

# Shell-call fields that name the folder a command runs in (code review finding R2-1).
# Every listed command is judged as if run from the project root, so a call naming any
# other folder is refused: a project-relative read path, or a listed test script, would
# otherwise resolve somewhere else.
SHELL_FOLDER_FIELDS = ("workdir", "cwd")


def _folder_problem(root, tool_input):
    """Why a shell call's working-folder field is refused, or None."""
    for field in SHELL_FOLDER_FIELDS:
        value = tool_input.get(field)
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            return f"`{field}` is not a folder path"
        path = Path(value)
        if not path.is_absolute():
            path = Path(root) / path
        try:
            same = path.resolve() == Path(root).resolve()
        except (OSError, ValueError):
            same = False
        if not same:
            return f"`{field}` names a folder other than the project root"
    return None

# The one form a Codex builder may read a file with Get-Content in (short plan 3.7 and
# 11.5), named in the refusal of any other Get-Content so it is not retried blind.
GET_CONTENT_FORM = ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                    "Get-Content -LiteralPath <path> -Encoding UTF8")
GET_CONTENT_NOTE = (f" Read a file with Get-Content only as `{GET_CONTENT_FORM}`, "
                    "optionally with -TotalCount or -Tail, or piped to Select-Object: "
                    "without both encodings Windows PowerShell returns non-ASCII text "
                    "wrongly.")


def read_command_lines(path=READ_COMMANDS_PATH):
    """The lines of the read-commands file as written, comments and blanks apart."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise approver_mod.ApproverError(
            f"cannot read {path.name}: {exc.strerror or exc}") from exc
    lines = [line.strip() for line in text.splitlines()]
    return [line for line in lines if line and not line.startswith("#")]


def expand(line):
    """One read-commands line with its PATH and QUOTED tokens replaced."""
    return line.replace("PATH", PATH_TOKEN).replace("QUOTED", QUOTED_TOKEN)


def load_read_pattern(path=READ_COMMANDS_PATH):
    """The read pattern: one compiled expression matching any listed line, then any
    number of further listed lines each joined by ``; ``."""
    lines = read_command_lines(path)
    if not lines:
        raise approver_mod.ApproverError(f"{Path(path).name} lists no commands")
    expanded = []
    for number, line in enumerate(lines, 1):
        try:
            re.compile(expand(line))
        except re.error as exc:
            raise approver_mod.ApproverError(
                f"{Path(path).name} command {number} is not a valid regular "
                f"expression: {exc}") from exc
        expanded.append(f"(?:{expand(line)})")
    one = "(?:" + "|".join(expanded) + ")"
    return re.compile(f"{one}(?:; {one})*")


def load_verify_commands(path=VERIFY_COMMANDS_PATH):
    """The Codex builder's verification list, read as ``verify-commands.txt`` is."""
    return approver_mod.load_verify_commands(path)


class PatchRefused(Exception):
    """A patch whose files cannot be read from its header lines."""


def patch_files(text):
    """The files a patch names, from its header lines only, in order.

    Raises PatchRefused for a patch that does not start with ``*** Begin Patch``,
    names no file, or has a line starting ``*** `` that is not a known header.
    """
    if not isinstance(text, str):
        raise PatchRefused("the patch is not text")
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines or lines[0].rstrip() != _PATCH_BEGIN:
        raise PatchRefused("the patch does not start with `*** Begin Patch`")
    files = []
    for line in lines:
        if not line.startswith("*** "):
            continue
        for header in _PATCH_FILE_HEADERS:
            if line.startswith(header):
                name = line[len(header):].strip()
                if not name:
                    raise PatchRefused(f"the patch line `{line.strip()}` names no file")
                files.append(name)
                break
        else:
            if line.rstrip() not in _PATCH_OTHER_LINES:
                raise PatchRefused("the patch has a line the orchestrator does not "
                                   f"recognise: `{line.strip()[:80]}`")
    if not files:
        raise PatchRefused("the patch names no file")
    return files


def _under_runs(root, raw):
    """True when ``raw`` resolves to a file under this workflow's ``runs/`` folder."""
    path = Path(raw)
    if not path.is_absolute():
        path = Path(root) / path
    try:
        relative = path.resolve().relative_to(Path(root).resolve())
    except (ValueError, OSError):
        return False
    return (relative.as_posix().lower() + "/").startswith(RUNS_PREFIX)


def decide(tool_name, tool_input, approver, read_pattern):
    """Allow or refuse one Codex tool call. Returns an ``approver.Decision``."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    if tool_name == SHELL_TOOL:
        override = next((field for field in SHELL_OVERRIDE_FIELDS
                         if tool_input.get(field) not in _OVERRIDE_DEFAULTS), None)
        if override is not None:
            return Decision(False, f"Refused by the orchestrator: `{override}` asks for "
                                   "more than the sandbox allows, and commands in this "
                                   "run may not leave the sandbox.")
        folder = _folder_problem(approver.root, tool_input)
        if folder is not None:
            return Decision(False, f"Refused by the orchestrator: {folder}; every command "
                                   "in this run runs from the project root.")
        command = tool_input.get("command")
        listed = False
        if isinstance(command, str):
            trimmed = command.strip()
            listed = bool(trimmed and read_pattern.fullmatch(trimmed))
            if listed:
                reason = approver_mod.shell_checks(trimmed)
                return Decision(True) if reason is None else Decision(False, reason)
        reason = ("Refused by the orchestrator: the shell runs only the listed read "
                  "commands in this run. Run a check with the check helper's run_check "
                  "tool, which runs it in an offline container over a clean copy.")
        # A Get-Content, or half of the accepted form, refused for its form is told the
        # form that is accepted.
        if isinstance(command, str) and ("get-content" in command.lower()
                                         or "[console]::outputencoding" in command.lower()):
            reason += GET_CONTENT_NOTE
        return Decision(False, reason)
    if tool_name in CHECK_TOOLS:
        return approver.decide(tool_name, dict(tool_input))
    if tool_name == PATCH_TOOL:
        try:
            files = patch_files(tool_input.get("command"))
        except PatchRefused as exc:
            return Decision(False, f"Refused by the orchestrator: {exc}. A patch must "
                                   "start with `*** Begin Patch` and name each file in "
                                   "an Add File, Update File, Delete File or Move to "
                                   "line.")
        for name in files:
            if _under_runs(approver.root, name):
                return Decision(False, f"Refused by the orchestrator: {name} is in a "
                                       "run record, which is never a builder's work. "
                                       "This run may change only: "
                                       f"{', '.join(approver.edit_paths) or '(nothing)'}.")
            decision = approver.decide("Write", {"file_path": name})
            if not decision.allowed:
                return decision
        return Decision(True)
    return Decision(False, f"Refused by the orchestrator: {tool_name} is not permitted "
                           "in this run. Read with the listed read commands, change "
                           "files with apply_patch inside the run's edit paths, and "
                           "run the listed verification commands with the check "
                           "helper's run_check.")


# ------------------------------------------------------------- the rule hooks


def fire_log_path(run_id, temp_root=None):
    """The run's rule-hook fire log, ``<temp>/book-dragon-runlogs/<run-id>/
    fire-log.jsonl``: outside the project and the frozen folder (plan 7.6 item 3)."""
    return (Path(temp_root or tempfile.gettempdir()) / RUNLOGS_PARENT / run_id
            / FIRE_LOG_NAME)


def rule_hook_env(environ, root, run_id, temp_root=None):
    """The environment for one trusted rule-hook process: the caller's, with the
    project root and the run's fire log set. The fire log's folder is made here."""
    log = fire_log_path(run_id, temp_root)
    log.parent.mkdir(parents=True, exist_ok=True)
    env = dict(environ)
    env.update({ROOT_ENV: str(root), FIRE_LOG_ENV: str(log),
                "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def run_rule_hook(python, frozen, ai, event, root, run_id, *, temp_root=None,
                  environ=None, runner=subprocess.run,
                  timeout=RULE_HOOK_TIMEOUT_SECONDS):
    """Run the trusted rule hooks once on one call, as the project hook would have.

    Returns None when they let the call through, else the reason it is refused: the
    rule's own message for a block, or what failed when they could not judge it (a
    timeout, a crash, output that cannot be read), since a hook that cannot judge a
    call never lets it through. ``ai`` is ``claude`` or ``codex``: Claude's rule
    hooks block with exit 2 and the reason on stderr, Codex's with a deny on stdout."""
    script = Path(frozen).joinpath(*TRUSTED_RULE_HOOKS)
    if not script.is_file():
        return f"Refused by the orchestrator: the run's rule hooks ({script.as_posix()}) are missing."
    environ = os.environ if environ is None else environ
    try:
        done = runner([str(python), str(script), "--ai", ai], input=json.dumps(event),
                      capture_output=True, encoding="utf-8", errors="replace",
                      timeout=timeout, env=rule_hook_env(environ, root, run_id, temp_root),
                      cwd=str(root))
    except subprocess.TimeoutExpired:
        return (f"Refused by the orchestrator: the rule hooks did not answer within "
                f"{timeout} s.")
    except OSError as exc:
        return f"Refused by the orchestrator: the rule hooks could not be started ({exc})."
    out, err = (done.stdout or "").strip(), (done.stderr or "").strip()
    if ai == "claude":
        if done.returncode == 0 and not out:
            return None
        if done.returncode == 2 and err:
            return err
    else:
        if done.returncode == 0 and not out:
            return None
        try:
            reply = json.loads(out)["hookSpecificOutput"]
            if reply.get("permissionDecision") == "deny" and reply.get("permissionDecisionReason"):
                return str(reply["permissionDecisionReason"])
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
    tail = " ".join((err or out).split())[-300:]
    return (f"Refused by the orchestrator: the rule hooks could not judge this call "
            f"(exit {done.returncode}{'; ' + tail if tail else ''}).")


def detail(tool_name, tool_input):
    """What a decision was about, short enough for a report line."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    value = tool_input.get("command")
    if tool_name == PATCH_TOOL:
        try:
            value = ", ".join(patch_files(value))
        except PatchRefused:
            pass
    text = "" if value is None else " ".join(str(value).split())
    return text if len(text) <= 300 else text[:297] + "..."


# ------------------------------------------------------------ the decisions file


def read_decisions(path):
    """Every line of a decisions file, as dictionaries. A missing file is no lines; a
    line that is not a JSON object is skipped, since it can vouch for nothing."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    lines = []
    for raw in text.splitlines():
        try:
            record = json.loads(raw)
        except ValueError:
            continue
        if isinstance(record, dict):
            lines.append(record)
    return lines


def decision_for(lines, call_id):
    """The decision recorded for one call id, ``allow`` or ``deny``, or None. A call
    with any ``deny`` line is denied."""
    found = [line.get("decision") for line in lines
             if call_id and line.get("tool_use_id") == call_id]
    if not found:
        return None
    return "deny" if "deny" in found else found[-1]


def approval_reply(method, params, lines):
    """The approval handler's answer to one server request.

    Returns ``(reply, refusal, failed)``. ``refusal`` is a record for the run report
    when a request was declined; ``failed`` is true for a request of any other kind,
    which ends the turn.
    """
    params = params if isinstance(params, dict) else {}
    if method not in APPROVAL_METHODS:
        return {}, None, True
    call_id = params.get("itemId")
    if decision_for(lines, call_id) == "allow":
        return {"decision": "accept"}, None, False
    tool = SHELL_TOOL if method == APPROVAL_METHODS[0] else PATCH_TOOL
    text = " ".join(str(params.get("command") or call_id or "").split())
    refusal = {"tool": tool, "detail": text if len(text) <= 300 else text[:297] + "...",
               "reason": f"Refused by the orchestrator: {NO_HOOK_DECISION}."}
    return {"decision": "decline"}, refusal, False
