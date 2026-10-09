#!/usr/bin/env python3
"""
tripwire.py - a net under the rule that a run executes no file from the live project
tree once it has started (orchestrator isolation plan 7.8, decision 29).

A run's frozen folder holds a copy of this file as ``tripwire/sitecustomize.py``.
``install`` adds an audit hook (``sys.addaudithook``) that raises ``TripwireError`` on
the ``compile`` and ``exec`` audit events when the code's file name is a ``.py`` file
under the live project root and not under its ``.venv``. Importing such a file, or
running it with ``exec_module``, fires one of the two; reading it as data (``open``)
fires neither, so a guard that scans ``.py`` files still works. The project root is
read from the ``hook.json`` beside the ``tripwire`` folder, written by the orchestrator
out of every builder's reach, unless the caller passes it.

Two users, one file. The trusted ``run.py`` loads it by path and calls ``install`` as
its first statements, and the live ``run.py`` does the same once it has created the
frozen folder (S6a). Loaded as ``sitecustomize`` by a child process, it installs
itself; putting the folder on child processes' ``PYTHONPATH`` is S7 (decision 29).

An audit hook cannot be removed, and the hook stays in force for the life of the
process. Python processes only, and only those that load it. Standard library only.
"""

import json
import os
import sys
from pathlib import Path

HOOK_CONFIG = "hook.json"
_installed = []


class TripwireError(RuntimeError):
    """A process the run started tried to execute a file from the live project."""


def configured_root(folder=None):
    """The live project root named by the frozen folder's ``hook.json``. ``folder`` is
    the frozen folder; by default the parent of the folder this file sits in."""
    folder = Path(folder) if folder is not None else Path(__file__).resolve().parent.parent
    config = json.loads((folder / HOOK_CONFIG).read_text(encoding="utf-8"))
    root = config["project_root"]
    if not isinstance(root, str) or not root:
        raise ValueError(f"{HOOK_CONFIG} names no project root")
    return root


def _base(path):
    return os.path.normcase(os.path.realpath(path))


def tripped(filename, root, venv):
    """True when ``filename`` is a ``.py`` file under ``root`` and not under ``venv``
    (both already normalised with ``_base``)."""
    if not isinstance(filename, str) or not filename.lower().endswith(".py"):
        return False
    path = os.path.normcase(os.path.abspath(filename))
    return path.startswith(root + os.sep) and not path.startswith(venv + os.sep)


def install(root=None):
    """Arm the tripwire for this process. Returns the root it guards. A second call
    is a no-op, since an audit hook cannot be removed or replaced."""
    if _installed:
        return _installed[0]
    root = _base(root if root is not None else configured_root())
    venv = os.path.join(root, ".venv")

    def hook(event, args):
        if event == "compile":
            filename = args[1] if len(args) > 1 else None
        elif event == "exec":
            filename = getattr(args[0], "co_filename", None) if args else None
        else:
            return
        if isinstance(filename, bytes):
            filename = os.fsdecode(filename)
        if tripped(filename, root, venv):
            raise TripwireError(f"the run may not execute a file from the live project "
                                f"tree: {filename}")

    sys.addaudithook(hook)
    _installed.append(root)
    return root


if __name__ == "sitecustomize":
    install()
