#!/usr/bin/env python3
"""
settings.py - the review-orchestration settings file, read and checked.

``workflows/review-orchestration/config/settings.json`` is the file the user edits.
It holds:

  usage_ceiling_percent  plan section 10.10: before each step, a provider reporting
                         usage at or above it stops the run. Default 80.
  round_caps             plan section 11 item 5: 3 build-review rounds, 2 plan-review
                         rounds.
  models                 plan section 11 item 3: for each provider, the model and
                         effort it uses in each role it can hold.

Which provider builds is not a setting. The user names it for each run
(``run.py --run --builder claude|codex``) and the other provider reviews;
``assign_roles`` gives a run's settings that pairing (the chunk (d) short plan, 11.1).
A file that still holds ``roles`` is refused rather than ignored, so a stale file
cannot quietly decide the pairing. Roles are named by what they do, never by
provider, and the defaults hold a model for every role either provider can hold, so
either pairing runs on the defaults. The synthesiser has no entry of its own: plan
section 11 makes it the builder's provider on the builder's model.

Anything missing takes its default, and a ``models`` entry given overrides only that
provider's role. A settings file that cannot be read, is not a JSON object, or holds a
value it may not raises ``SettingsError`` naming the problem. A bad setting is never
silently replaced by its default.
"""

import copy
import json
from pathlib import Path

_WORKFLOW_DIR = Path(__file__).resolve().parent.parent
SETTINGS_PATH = _WORKFLOW_DIR / "config" / "settings.json"

DEFAULT_USAGE_CEILING = 80

PROVIDERS = ("claude", "codex")
ROLES = ("builder", "planner", "plan_reviewer", "reviewer", "intent_checker")

# The effort values each pinned SDK accepts: claude-agent-sdk's EffortLevel and
# openai-codex's ReasoningEffort. An SDK upgrade may change them (see Known Issues).
EFFORTS = {
    "claude": ("low", "medium", "high", "xhigh", "max"),
    "codex": ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"),
}

DEFAULT_ROUND_CAPS = {"build_review": 3, "plan_review": 2}

# Plan section 11 item 3: the pairs that table names. It gives each provider a builder
# and a reviewer entry, so either pairing has a model for every role it holds.
DEFAULT_MODELS = {
    "claude": {
        "builder": {"model": "claude-opus-5-5", "effort": "medium"},
        "planner": {"model": "claude-opus-5-5", "effort": "medium"},
        "plan_reviewer": {"model": "claude-opus-5-5", "effort": "high"},
        "reviewer": {"model": "claude-opus-5-5", "effort": "high"},
        "intent_checker": {"model": "claude-opus-5-5", "effort": "medium"},
    },
    "codex": {
        "builder": {"model": "gpt-6-sol", "effort": "high"},
        "planner": {"model": "gpt-6-astra", "effort": "medium"},
        "plan_reviewer": {"model": "gpt-6-sol", "effort": "high"},
        "reviewer": {"model": "gpt-6-sol", "effort": "high"},
        "intent_checker": {"model": "gpt-6-sol", "effort": "medium"},
    },
}

# The roles each side of a run needs a model for. The builder's provider also
# synthesises, on its builder entry; every other role falls to the other provider.
_BUILDER_SIDE = ("builder", "planner")
_REVIEWER_SIDE = ("planner", "plan_reviewer", "reviewer", "intent_checker")


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
    if "roles" in data:
        raise SettingsError(f"{path.name} holds roles, which are no longer a setting: "
                            "name the builder for each run with run.py --run "
                            "--builder claude|codex, and remove roles from the file")

    ceiling = data.get("usage_ceiling_percent", DEFAULT_USAGE_CEILING)
    # bool is an int in Python, and true is not a percentage.
    if _not_number(ceiling):
        raise SettingsError("usage_ceiling_percent must be a number")
    if not 0 < ceiling <= 100:
        raise SettingsError("usage_ceiling_percent must be above 0 and at most 100")

    settings = dict(data)
    settings["usage_ceiling_percent"] = ceiling
    settings["round_caps"] = _round_caps(data.get("round_caps", {}))
    settings["models"] = _models(data.get("models", {}))
    return settings


def assign_roles(settings, builder):
    """A copy of ``settings`` with one run's roles: ``builder`` is the provider the user
    named to build, and the other provider reviews. Every role the pairing will use
    must have a model."""
    if builder not in PROVIDERS:
        raise SettingsError(f"the builder must be one of: {', '.join(PROVIDERS)}")
    reviewer = next(provider for provider in PROVIDERS if provider != builder)
    assigned = copy.deepcopy(settings)
    assigned["roles"] = {"builder": builder, "reviewer": reviewer}
    _check_coverage(assigned["roles"], assigned["models"])
    return assigned


def model_for(settings, provider, role):
    """The ``{"model", "effort"}`` entry a provider uses in a role."""
    try:
        return settings["models"][provider][role]
    except KeyError:
        raise SettingsError(f"models.{provider}.{role} is not set") from None


def _not_number(value):
    return isinstance(value, bool) or not isinstance(value, (int, float))


def _object(value, name):
    if not isinstance(value, dict):
        raise SettingsError(f"{name} must be a JSON object")
    return value


def _unknown(value, allowed, name):
    extra = sorted(set(value) - set(allowed))
    if extra:
        raise SettingsError(f"{name} has unknown key(s): {', '.join(extra)} "
                            f"(allowed: {', '.join(allowed)})")


def _round_caps(value):
    value = _object(value, "round_caps")
    _unknown(value, tuple(DEFAULT_ROUND_CAPS), "round_caps")
    caps = {**DEFAULT_ROUND_CAPS, **value}
    for name, cap in caps.items():
        if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
            raise SettingsError(f"round_caps.{name} must be a whole number of at least 1")
    return caps


def _models(value):
    value = _object(value, "models")
    _unknown(value, PROVIDERS, "models")
    models = copy.deepcopy(DEFAULT_MODELS)
    for provider, roles in value.items():
        roles = _object(roles, f"models.{provider}")
        _unknown(roles, ROLES, f"models.{provider}")
        for role, entry in roles.items():
            name = f"models.{provider}.{role}"
            entry = _object(entry, name)
            _unknown(entry, ("model", "effort"), name)
            model = entry.get("model")
            effort = entry.get("effort")
            if not isinstance(model, str) or not model.strip():
                raise SettingsError(f"{name}.model must be a model name")
            if effort not in EFFORTS[provider]:
                raise SettingsError(f"{name}.effort must be one of: "
                                    f"{', '.join(EFFORTS[provider])}")
            models[provider][role] = {"model": model.strip(), "effort": effort}
    return models


def _check_coverage(roles, models):
    """Every role the assigned providers will hold must have a model."""
    builder, reviewer = roles["builder"], roles["reviewer"]
    for provider, needed in ((builder, _BUILDER_SIDE), (reviewer, _REVIEWER_SIDE)):
        for role in needed:
            if role not in models[provider]:
                raise SettingsError(
                    f"models.{provider}.{role} is required when {builder} builds and "
                    f"{reviewer} reviews; add it to the settings file")
