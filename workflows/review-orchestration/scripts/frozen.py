#!/usr/bin/env python3
"""
frozen.py - a run's frozen folder, made by the orchestrator for every run, whichever
provider builds (orchestrator isolation plan 6 item 1, 7.0 and 7.1 item 5; stage S6a).

``<temp>/book-dragon-orchestration/<run-id>/`` is outside the project and out of every
builder's reach. It holds:

  trusted/          ``git archive`` of the run's starting commit for the four host
                    checks' workflows, the orchestrator's own workflow, the sync
                    classification's scripts and the rule hooks. The run re-executes
                    from ``trusted/workflows/review-orchestration/scripts/run.py``, so
                    a builder's edits to any of them take effect only on a later run.
  the frozen files  ``codex_rules.FROZEN_MODULES`` and ``FROZEN_LISTS``, copied from
                    ``trusted/``, so they are the commit's too: the Codex hook and the
                    check helper run from here.
  hook.json         the run id, project root, builder provider and public head. Since
                    A3 it holds neither the edit paths nor the image tag (plan 10.3
                    step 6): a resume during planning reads only ``project_root``.
  approval.json     the approved edit paths and the image tag, written by
                    ``write_approval`` at approval, never at setup.
  start-ignored.txt the ignore floor (plan 5.3), recorded once, at the start.
  tripwire/         ``sitecustomize.py``, a copy of ``tripwire.py`` (plan 7.8).

``setup`` refuses a folder that already exists and removes what it made if any part
fails, so a start that is refused here leaves nothing behind. Nothing writes in the
folder after ``setup`` except three named writes: ``approval.json``, created once and
exclusively by ``write_approval`` and never rewritten, and the two record files, the
Codex hook's decisions file and the check helper's calls file. The run removes the
folder at a stop reason a resume cannot continue (plan 7.3).
"""

import json
import shutil
import tempfile
from pathlib import Path

import checks as checks_mod
import codex_rules
import container

FROZEN_PARENT = "book-dragon-orchestration"
TRUSTED_DIR = checks_mod.TRUSTED_DIR  # one name: the checks after each turn read it
TRIPWIRE_DIR = "tripwire"
TRIPWIRE_FILE = "sitecustomize.py"
ORCHESTRATOR = "workflows/review-orchestration"
# What trusted/ holds: the host checks (plan 4.4), and the code 7.0 runs from there.
TRUSTED_CODE = (*checks_mod.TRUSTED_WORKFLOWS, ORCHESTRATOR,
                "workflows/sync-architecture/scripts", "workflows/rule-hooks")


class FrozenError(Exception):
    """The frozen folder cannot be made. The run is refused; nothing is left behind."""


def folder(run_id, temp_root=None):
    """Where a run's frozen folder is: under the system temp folder."""
    return Path(temp_root or tempfile.gettempdir()) / FROZEN_PARENT / run_id


def trusted_run_py(frozen):
    """The trusted copy of ``run.py`` inside a frozen folder."""
    return Path(frozen) / TRUSTED_DIR / ORCHESTRATOR / "scripts" / "run.py"


def hook_config(run_id, root, builder, public_head):
    """``hook.json``: everything the frozen folder's programs need except the edit paths
    and the image tag, which ``approval.json`` alone holds (plan 10.3 step 6)."""
    return {"run_id": run_id, "project_root": str(root), "builder_provider": builder,
            "public_head": public_head}


def write_approval(dest, edit_paths, image_tag):
    """Write ``<frozen folder>/approval.json`` (plan 10.3 step 6): exactly the approved
    edit paths and the image tag, created exclusively, so a second write fails rather
    than replacing the first. Returns its path; raises FrozenError naming the cause,
    including a file that already exists."""
    paths = list(edit_paths or ())
    if not paths or not all(isinstance(item, str) and item.strip() for item in paths):
        raise FrozenError("approval.json needs at least one edit path")
    if not isinstance(image_tag, str) or not image_tag.strip():
        raise FrozenError("approval.json needs the check image's tag")
    path = Path(dest) / codex_rules.APPROVAL_FILE
    text = json.dumps({"edit_paths": paths, "image_tag": image_tag}, indent=1) + "\n"
    try:
        with open(path, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except FileExistsError as exc:
        raise FrozenError(f"{path.as_posix()} already exists; it is written once and "
                          "never rewritten") from exc
    except OSError as exc:
        raise FrozenError(f"{path.as_posix()} could not be written: {exc}") from exc
    return path


def populate(dest, scripts_dir, config_dir, config):
    """Write the frozen files, the tripwire and ``hook.json`` into ``dest``, from
    ``scripts_dir`` and ``config_dir``. ``setup`` passes the trusted copy's folders;
    the tests pass the workflow's own."""
    dest, scripts_dir, config_dir = Path(dest), Path(scripts_dir), Path(config_dir)
    for name in codex_rules.FROZEN_MODULES:
        shutil.copyfile(scripts_dir / name, dest / name)
    for name in codex_rules.FROZEN_LISTS:
        shutil.copyfile(config_dir / name, dest / name)
    (dest / TRIPWIRE_DIR).mkdir(exist_ok=True)
    shutil.copyfile(scripts_dir / "tripwire.py", dest / TRIPWIRE_DIR / TRIPWIRE_FILE)
    with open(dest / codex_rules.HOOK_CONFIG, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(config, indent=1) + "\n")
    return dest


def setup(root, run_id, *, public_head, builder, temp_root=None):
    """Make the run's frozen folder. Returns its path; raises FrozenError, having
    removed whatever it made, if any part cannot be done."""
    if not codex_rules.RUN_ID.match(run_id or ""):
        raise FrozenError(f"not a run id: {run_id!r}")
    dest = folder(run_id, temp_root)
    # Any failure to make either folder is a refusal naming it, never a traceback
    # (code review R19-2). Neither failure leaves a folder of this call's behind.
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FrozenError(f"the frozen folders' parent {dest.parent.as_posix()} cannot "
                          f"be made: {exc}") from exc
    try:
        dest.mkdir()
    except FileExistsError as exc:
        raise FrozenError(f"the run's frozen folder {dest.as_posix()} already exists "
                          "before the run starts") from exc
    except OSError as exc:
        raise FrozenError(f"the run's frozen folder {dest.as_posix()} cannot be made: "
                          f"{exc}") from exc
    try:
        trusted = checks_mod.write_trusted_copies(root, public_head, dest / TRUSTED_DIR,
                                                  workflows=TRUSTED_CODE)
        code = trusted / ORCHESTRATOR
        populate(dest, code / "scripts", code / "config",
                 hook_config(run_id, root, builder, public_head))
        container.write_start_ignored(container.start_ignored(root),
                                      dest / codex_rules.START_IGNORED)
    except (checks_mod.TrustedCopyError, container.ContainerError, OSError,
            ValueError) as exc:
        remove(dest)
        raise FrozenError(f"the run's frozen folder could not be made: {exc}") from exc
    return dest


def remove(dest):
    """Remove a frozen folder, read-only files included."""
    container.remove_copy(dest)
