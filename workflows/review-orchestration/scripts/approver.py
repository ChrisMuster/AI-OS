#!/usr/bin/env python3
"""
approver.py - decides every tool call an orchestrated builder makes (Stage A2).

Plan section 10.4: the builder is confined to the brief's edit paths and may run the
verification commands in ``config/verify-commands.txt``; every refusal goes into the
run report. The decision is a plain function of the tool's name and input, with no
SDK inside it, so the provider that talks to an SDK only has to translate the answer
(``providers.py``) and every rule here is tested without a session.

What is allowed:

  read tools      Read, Grep, Glob, TodoWrite and ToolSearch, which change nothing.
                  Read, Grep and Glob are subject to the read rules (``read_rules``):
                  none may name a ``.env`` file, and a Grep may not carry a ``glob``.
  edit tools     Edit, Write, MultiEdit and NotebookEdit, when the file resolves
                  inside the project, inside one of the brief's edit paths, and is not
                  under ``.git`` or the project ``.venv``, a ``.env`` file, or the
                  orchestrator's starter (``brief.STARTER_PATH``).
  the shell       Bash, when the whole command matches one line of
                  ``verify-commands.txt``, is one line, has no ``..`` path component,
                  names no ``.env`` file and does not ask to leave the sandbox.
  Biblio Tools    get_timestamp; run_audit and run_link_check in their read-only
                  forms; append_log for a directory inside the edit paths.

Everything else is refused, with a message the builder can act on. Refusal is the
default, so a tool nobody listed is never allowed by accident.

Edit paths are compared the way the brief checker compares them (``brief.py``):
without case, ignoring trailing dots and spaces, since that is how Windows compares
names.

Two functions are shared with the other sessions of a run, so each rule has one
implementation: ``read_rules`` (also called by the Claude reviewer's hook) and
``shell_checks`` (also applied to a Codex builder's read commands by
``codex_rules.py``).
"""

import re
from dataclasses import dataclass
from pathlib import Path

from brief import PROJECT_ROOT, STARTER_PATH, _components, _folded, check_edit_path

_WORKFLOW_DIR = Path(__file__).resolve().parent.parent
VERIFY_COMMANDS_PATH = _WORKFLOW_DIR / "config" / "verify-commands.txt"

READ_TOOLS = frozenset({"Read", "Grep", "Glob", "TodoWrite", "ToolSearch"})
# The read tools that take a file, a folder or a search: the read rules bind these.
FILE_READ_TOOLS = frozenset({"Read", "Grep", "Glob"})
EDIT_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path",
              "NotebookEdit": "notebook_path"}
SHELL_TOOL = "Bash"
BIBLIO_PREFIX = "mcp__biblio-tools__"

_PARENT_COMPONENT = re.compile(r"(?:^|[\s/\\=\"'])\.\.(?:[/\\\s\"']|$)")
_ENV_FILE = re.compile(r"(?i)(?:^|[\s/\\=\"'])\.env(?:\.[\w.-]*)?(?:[\s/\\\"']|$)")


class ApproverError(Exception):
    """The approver cannot be set up: a bad edit path or verification pattern."""


@dataclass(frozen=True)
class Decision:
    """One answer. ``reason`` is shown to the builder when ``allowed`` is false."""

    allowed: bool
    reason: str = ""


def shell_checks(command):
    """Why a one-string shell command is refused whatever list it matches, or None.

    The checks every shell command gets: one line, no ``..`` path component, no
    ``.env`` file named. ``command`` is already trimmed.
    """
    if "\n" in command or "\r" in command:
        return "Refused by the orchestrator: one command per call, on one line."
    if _PARENT_COMPONENT.search(command):
        return ("Refused by the orchestrator: a `..` path component is not allowed; "
                "name the path from the project root.")
    if _ENV_FILE.search(command):
        return "Refused by the orchestrator: a command may not name a `.env` file."
    return None


def _strings(value):
    """Every string inside a tool input, however nested."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def read_rules(tool_name, tool_input):
    """Why a Read, Grep or Glob call is refused, or None.

    Rule 1: no string value in the input names a ``.env`` file, by the test the shell
    checks use. A Grep call's ``pattern`` is exempt: it is the text searched for, not a
    file. Rule 2: a Grep call may not carry a ``glob`` key, whatever its value, since
    an explicit file wildcard overrides the ignore rules that keep a ``.env`` file out
    of a folder search, and no list of unsafe wildcards can be complete.
    """
    if tool_name not in FILE_READ_TOOLS:
        return None
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    if tool_name == "Grep" and "glob" in tool_input:
        return ("Refused by the orchestrator: a search may not carry a file wildcard "
                "(`glob`). Search by folder, file or file type instead.")
    for key, value in tool_input.items():
        if tool_name == "Grep" and key == "pattern":
            continue
        if any(_ENV_FILE.search(text) for text in _strings(value)):
            return ("Refused by the orchestrator: a read or search may not name a "
                    "`.env` file.")
    return None


def load_verify_commands(path=VERIFY_COMMANDS_PATH):
    """Read ``verify-commands.txt``: one regular expression per line, ``#`` comments
    and blank lines ignored. Returns the compiled patterns in file order."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ApproverError(f"cannot read {path.name}: {exc.strerror or exc}") from exc
    patterns = []
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            patterns.append(re.compile(line))
        except re.error as exc:
            raise ApproverError(f"{path.name} line {number} is not a valid regular "
                                f"expression: {exc}") from exc
    if not patterns:
        raise ApproverError(f"{path.name} lists no commands")
    return patterns


class Approver:
    """The decision for one run. Records every refusal in ``refusals``."""

    def __init__(self, edit_paths, verify_patterns, *, root=PROJECT_ROOT):
        self.root = Path(root).resolve()
        self.edit_paths = list(edit_paths)
        problems = [reason for path in self.edit_paths for reason in check_edit_path(path)]
        if problems:
            raise ApproverError("bad edit path: " + "; ".join(problems))
        self._edit_keys = [self._key(_components(path)) for path in self.edit_paths]
        self.verify_patterns = list(verify_patterns)
        self.refusals = []

    # ---------------------------------------------------------------- public

    def decide(self, tool_name, tool_input):
        """Allow or refuse one tool call. A refusal is also appended to ``refusals``."""
        tool_input = tool_input if isinstance(tool_input, dict) else {}
        decision = self._decide(tool_name, tool_input)
        if not decision.allowed:
            self.refusals.append({"tool": tool_name, "detail": _detail(tool_name, tool_input),
                                  "reason": decision.reason})
        return decision

    # ---------------------------------------------------------------- rules

    def _decide(self, tool_name, tool_input):
        if tool_name in READ_TOOLS:
            reason = read_rules(tool_name, tool_input)
            return Decision(True) if reason is None else Decision(False, reason)
        if tool_name in EDIT_TOOLS:
            return self._edit(tool_input.get(EDIT_TOOLS[tool_name]))
        if tool_name == SHELL_TOOL:
            return self._shell(tool_input)
        if isinstance(tool_name, str) and tool_name.startswith(BIBLIO_PREFIX):
            return self._biblio(tool_name[len(BIBLIO_PREFIX):], tool_input)
        return Decision(False, f"Refused by the orchestrator: {tool_name} is not "
                               "permitted in this run. Use Read, Grep and Glob to read, "
                               "and edit only inside the run's edit paths.")

    def _edit(self, raw):
        where = self._inside_edit_paths(raw)
        if where is None:
            return Decision(True)
        return Decision(False, f"Refused by the orchestrator: {where} This run may "
                               f"change only: {', '.join(self.edit_paths) or '(nothing)'}.")

    def _shell(self, tool_input):
        command = tool_input.get("command")
        if not isinstance(command, str) or not command.strip():
            return Decision(False, "Refused by the orchestrator: no command given.")
        command = command.strip()
        if tool_input.get("dangerouslyDisableSandbox"):
            return Decision(False, "Refused by the orchestrator: commands in this run "
                                   "may not leave the sandbox.")
        reason = shell_checks(command)
        if reason is not None:
            return Decision(False, reason)
        if any(pattern.fullmatch(command) for pattern in self.verify_patterns):
            return Decision(True)
        allowed = "; ".join(pattern.pattern for pattern in self.verify_patterns)
        return Decision(False, "Refused by the orchestrator: only these verification "
                               "commands may run, one per call with no chaining "
                               f"(each a regular expression the whole command must "
                               f"match): {allowed}")

    def _biblio(self, name, tool_input):
        if name == "get_timestamp":
            return Decision(True)
        if name == "run_audit":
            if tool_input.get("save"):
                return Decision(False, "Refused by the orchestrator: run_audit may not "
                                       "save its report in this run; leave save off.")
            return Decision(True)
        if name == "run_link_check":
            if tool_input.get("mode", "audit") != "audit" or tool_input.get("save"):
                return Decision(False, "Refused by the orchestrator: run_link_check may "
                                       "only audit in this run (mode audit, save off).")
            return Decision(True)
        if name == "append_log":
            directory = tool_input.get("directory")
            if not isinstance(directory, str) or not directory.strip():
                return Decision(False, "Refused by the orchestrator: append_log needs a "
                                       "directory.")
            where = self._inside_edit_paths(directory.strip().rstrip("/") + "/LOG.md")
            if where is None:
                return Decision(True)
            return Decision(False, f"Refused by the orchestrator: append_log: {where} "
                                   f"This run may log only inside: "
                                   f"{', '.join(self.edit_paths) or '(nothing)'}.")
        return Decision(False, f"Refused by the orchestrator: the Biblio Tools tool "
                               f"{name} is not permitted in this run.")

    # ---------------------------------------------------------------- paths

    def _inside_edit_paths(self, raw):
        """None if ``raw`` is a file this run may change, else the reason it may not."""
        if not isinstance(raw, str) or not raw.strip():
            return "no file path given."
        path = Path(raw.strip())
        if not path.is_absolute():
            path = self.root / path
        try:
            relative = path.resolve().relative_to(self.root)
        except (ValueError, OSError):
            return f"{raw} is outside the project."
        text = relative.as_posix()
        if text in ("", "."):
            return f"{raw} is the project root, not a file."
        if ":" in text:
            return f"{raw} names an alternate data stream."
        parts = [_folded(part) for part in text.split("/")]
        if ".git" in parts:
            return f"{raw} is under `.git/`."
        if any(part == ".env" or part.startswith(".env.") for part in parts):
            return f"{raw} is a `.env` file."
        # Whatever the edit paths say (orchestrator isolation plan 7.0 item 2 and 7.7):
        # the project .venv runs every check, and the starter enters every run.
        if parts[0] == ".venv":
            return f"{raw} is in the project `.venv`, which no run may change."
        key = "/".join(parts)
        if key == self._key(STARTER_PATH.split("/")):
            return f"{raw} is the orchestrator's starter, which no run may change."
        if any(key == edit or key.startswith(edit + "/") for edit in self._edit_keys):
            return None
        return f"{raw} is outside this run's edit paths."

    @staticmethod
    def _key(parts):
        return "/".join(_folded(part) for part in parts)


def _detail(tool_name, tool_input):
    """What the refusal was about, short enough for a report line."""
    if tool_name in EDIT_TOOLS:
        value = tool_input.get(EDIT_TOOLS[tool_name])
    elif tool_name in FILE_READ_TOOLS:
        value = {key: tool_input[key] for key in ("file_path", "path", "pattern", "glob")
                 if key in tool_input} or None
    elif tool_name == SHELL_TOOL:
        value = tool_input.get("command")
    elif isinstance(tool_name, str) and tool_name.startswith(BIBLIO_PREFIX):
        value = {key: tool_input[key] for key in ("directory", "mode", "save")
                 if key in tool_input} or None
    else:
        value = None
    text = "" if value is None else str(value)
    return text if len(text) <= 300 else text[:297] + "..."
