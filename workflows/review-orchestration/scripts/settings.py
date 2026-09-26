#!/usr/bin/env python3
"""
settings.py - the review-orchestration settings file, read and checked.

``workflows/review-orchestration/config/settings.json`` is the file the user edits.
It holds:

  usage_ceiling_percent  plan section 10.10: before each step, a provider reporting
                         usage at or above it stops the run. Default 80.
  roles                  which provider builds and which reviews (plan section 3: the
                         user assigns them). Default Claude builds, Codex reviews.
  round_caps             plan section 11 item 5: 3 build-review rounds, 2 plan-review
                         rounds.
  models                 plan section 11 item 3: for each provider, the model and
                         effort it uses in each role it can hold.

Roles are named by what they do, never by provider, so swapping who builds and who
reviews is a change to ``roles`` plus a model entry for each new pairing, not a code
change. The synthesiser has no entry of its own: plan section 11 makes it the builder's
provider on the builder's model.

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

DEFAULT_ROLES = {"builder": "claude", "reviewer": "codex"}
DEFAULT_ROUND_CAPS = {"build_review": 3, "plan_review": 2}

# Plan section 11 item 3, as settled on 2026-09-24. Only the pairs that table names:
# Codex has no builder entry and Claude no reviewer entry, so swapping the roles means
# adding those two to the settings file first.
DEFAULT_MODELS = {
    "claude": {
        "builder": {"model": "claude-opus-5-5", "effort": "medium"},
        "planner": {"model": "claude-opus-5-5", "effort": "medium"},
        "plan_reviewer": {"model": "claude-opus-5-5", "effort": "high"},
        "intent_checker": {"model": "claude-opus-5-5", "effort": "medium"},
    },
    "codex": {
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

    ceiling = data.get("usage_ceiling_percent", DEFAULT_USAGE_CEILING)
    # bool is an int in Python, and true is not a percentage.
    if _not_number(ceiling):
        raise SettingsError("usage_ceiling_percent must be a number")
    if not 0 < ceiling <= 100:
        raise SettingsError("usage_ceiling_percent must be above 0 and at most 100")

    settings = dict(data)
    settings["usage_ceiling_percent"] = ceiling
    settings["roles"] = _roles(data.get("roles", {}))
    settings["round_caps"] = _round_caps(data.get("round_caps", {}))
    settings["models"] = _models(data.get("models", {}))
    _check_coverage(settings["roles"], settings["models"])
    return settings


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


def _roles(value):
    value = _object(value, "roles")
    _unknown(value, tuple(DEFAULT_ROLES), "roles")
    roles = {**DEFAULT_ROLES, **value}
    for role, provider in roles.items():
        if provider not in PROVIDERS:
            raise SettingsError(f"roles.{role} must be one of: {', '.join(PROVIDERS)}")
    if roles["builder"] == roles["reviewer"]:
        raise SettingsError("roles.builder and roles.reviewer must be different "
                            "providers: one AI builds while the other reviews")
    return roles


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
