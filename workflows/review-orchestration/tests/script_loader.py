"""script_loader.py - the one way the review-orchestration suites load a script from
``scripts/`` under its own name.

Not a test file (its name does not begin ``test_``). Each suite used to load the
scripts with a loader of its own that executed the file afresh and put the new module
into ``sys.modules`` under its plain name. Run alone, a suite saw one copy of each
module; run together (``python -m unittest discover``), each suite replaced the copies
the earlier ones held, so one suite's ``stopreasons.ReplyError`` was not the class
another copy of ``loop`` raised, and settings or patches landed on a copy the code did
not use. ``load`` hands back the copy already loaded from ``scripts/`` when there is
one, so every suite in a process shares one copy of each script.

A module of the same name loaded from anywhere else (a frozen folder, a fixture) is
not reused: it is replaced, as the suites' own loaders did.
"""

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _from_scripts(module, path):
    where = getattr(module, "__file__", None)
    if not where:
        return False
    try:
        return Path(where).resolve() == path.resolve()
    except OSError:
        return False


def load(name):
    """The module ``scripts/<name>.py`` under the name ``name``: the copy already in
    ``sys.modules`` when it was loaded from that file, otherwise a new one, registered
    before it runs (as an import is) and removed again if running it fails."""
    path = SCRIPTS / f"{name}.py"
    loaded = sys.modules.get(name)
    if loaded is not None and _from_scripts(loaded, path):
        return loaded
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if sys.modules.get(name) is module:
            del sys.modules[name]
        raise
    return module
