#!/usr/bin/env python3
"""
codex_rules.py - the decisions for a Codex builder's tool calls, and the approval
adapter (Stage A2 chunk (d); short plan sections 3.4, 3.6 and 3.7).

A Codex builder is held by a hook Codex runs before every tool call
(``codex_hook.py``). The hook decides each call here, with the run's ``Approver``:

  Bash         allowed when the whole trimmed command is a listed read command
               (``config/codex-read-commands.txt``) that also passes the approver's
               shell checks, or a verification command on the Codex builder's own list
               (``config/codex-verify-commands.txt``). Everything else is refused.
  apply_patch  allowed when every file the patch names, read from its header lines
               only, is one the approver allows as an edit, and none is under this
               workflow's ``runs/`` folder.
  anything     refused. That covers web search, the clock, image generation, goals, MCP
  else         tools, plugin install, the JavaScript REPL and every sub-agent tool.

The approval handler is the second layer behind the hook (``approval_reply``). It
reads no command text: a request is accepted only when the hook recorded an ``allow``
for the same call id, so a shell command or a patch does not run when the hook failed
to judge it.

This module and everything it imports (``approver.py``, ``brief.py``) are copied into a
run's frozen folder and run from there, so nothing here may depend on where the file
sits: the project root always comes from the caller. Standard library only.
"""

import json
import re
from pathlib import Path

import approver as approver_mod
from approver import Decision

_WORKFLOW_DIR = Path(__file__).resolve().parent.parent
READ_COMMANDS_PATH = _WORKFLOW_DIR / "config" / "codex-read-commands.txt"
VERIFY_COMMANDS_PATH = _WORKFLOW_DIR / "config" / "codex-verify-commands.txt"

# What a run's frozen folder holds besides hook.json and the decisions file.
FROZEN_MODULES = ("codex_hook.py", "codex_rules.py", "approver.py", "brief.py")
FROZEN_LISTS = ("codex-verify-commands.txt", "codex-read-commands.txt")
HOOK_CONFIG = "hook.json"
DECISIONS_FILE = "hook-decisions.jsonl"

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
            if listed and approver_mod.shell_checks(trimmed) is None:
                return Decision(True)
        decision = approver.decide("Bash", dict(tool_input))
        # A Get-Content, or half of the accepted form, refused for its form is told the
        # form that is accepted; one in that form refused for another reason (a `.env`
        # file, say) is not.
        if (not decision.allowed and not listed and isinstance(command, str)
                and ("get-content" in command.lower()
                     or "[console]::outputencoding" in command.lower())):
            return Decision(False, decision.reason + GET_CONTENT_NOTE)
        return decision
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
                           "run only the listed verification commands.")


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
