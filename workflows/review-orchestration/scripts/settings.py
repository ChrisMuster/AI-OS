#!/usr/bin/env python3
"""
settings.py - the review-orchestration settings file, read and checked.

``workflows/review-orchestration/config/settings.json`` is the file the user edits.
So far it holds one setting, ``usage_ceiling_percent`` (plan section 10.10): before
each step, a provider reporting usage at or above it stops the run. Missing, it
defaults to 80. The roles, models and efforts of plan section 11 join it in a later
chunk of the engine.

A settings file that cannot be read, is not a JSON object, or holds a value outside
its allowed range raises ``SettingsError`` naming the problem. A bad setting is
never silently replaced by its default.
"""

import json
from pathlib import Path

_WORKFLOW_DIR = Path(__file__).resolve().parent.parent
SETTINGS_PATH = _WORKFLOW_DIR / "config" / "settings.json"

DEFAULT_USAGE_CEILING = 80


class SettingsError(Exception):
    """The settings file is missing, unreadable, or holds a value it may not."""


def load_settings(path=SETTINGS_PATH):
    """Read and check the settings file. Returns a dict with every default filled in."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SettingsError(f"cannot read {path.name}: {exc.strerror or exc}") from exc
    except UnicodeDecodeError as exc:
        raise SettingsError(f"{path.name} is not valid UTF-8") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SettingsError(f"{path.name} is not valid JSON: {exc.msg} at line "
                            f"{exc.lineno}") from exc
    if not isinstance(data, dict):
        raise SettingsError(f"{path.name} must hold a JSON object")

    ceiling = data.get("usage_ceiling_percent", DEFAULT_USAGE_CEILING)
    # bool is an int in Python, and true is not a percentage.
    if isinstance(ceiling, bool) or not isinstance(ceiling, (int, float)):
        raise SettingsError("usage_ceiling_percent must be a number")
    if not 0 < ceiling <= 100:
        raise SettingsError("usage_ceiling_percent must be above 0 and at most 100")

    settings = dict(data)
    settings["usage_ceiling_percent"] = ceiling
    return settings
