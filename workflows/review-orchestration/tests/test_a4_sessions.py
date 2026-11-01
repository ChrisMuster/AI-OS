#!/usr/bin/env python3
"""Tests for A4's session construction in ``providers.py`` (plan 10.6): the intent
checker (process table row 19) is a fresh read-only session on the provider that did
not build, on that provider's ``intent_checker`` entry and with the plan reviewer's
lockdown; and the synthesiser keeps the builder's model at medium effort, corrected in
A4, for each provider as builder.

The registry's session classes are replaced by fakes that record how they were made,
so no session, SDK client or process is started.

    python workflows/review-orchestration/tests/test_a4_sessions.py
"""

import copy
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


settings_mod = _load("settings")
providers = _load("providers")


def fake_class(provider):
    class FakeSession:
        wants = ("usage", "frozen_dir")
        made = []

        def __init__(self, model, effort, **kwargs):
            self.provider, self.model, self.effort, self.kwargs = (provider, model, effort,
                                                                   kwargs)
            FakeSession.made.append(self)
    return FakeSession


class SessionTests(unittest.TestCase):

    def setUp(self):
        self.fakes = {"claude": fake_class("claude"), "codex": fake_class("codex")}
        patcher = mock.patch.dict(providers.REVIEWERS, self.fakes)
        patcher.start()
        self.addCleanup(patcher.stop)

    def settings(self, builder):
        settings = copy.deepcopy(settings_mod.load_settings())
        # A builder entry at high effort, as the Codex default is, for both providers.
        for provider in ("claude", "codex"):
            settings["models"][provider]["builder"]["effort"] = "high"
            settings["models"][provider]["intent_checker"] = {
                "model": f"{provider}-intent-x", "effort": "low"}
        return settings_mod.assign_roles(settings, builder)

    def test_positive_the_synthesiser_keeps_the_builder_model_at_medium(self):
        for builder in ("claude", "codex"):
            with self.subTest(builder=builder):
                settings = self.settings(builder)
                session = providers.make_synthesiser(settings, cwd="x", frozen_dir="f")
                entry = settings["models"][builder]["builder"]
                self.assertEqual(session.provider, builder)
                self.assertEqual((session.model, session.effort), (entry["model"], "medium"))
                self.assertEqual(entry["effort"], "high", "the settings are not changed")
                self.assertEqual(providers.SYNTHESISER_EFFORT, "medium")

    def test_positive_the_intent_checker_is_the_other_providers_read_only_session(self):
        for builder, other in (("claude", "codex"), ("codex", "claude")):
            with self.subTest(builder=builder):
                settings = self.settings(builder)
                session = providers.make_intent_checker(settings, cwd="x", usage="u",
                                                        frozen_dir="f")
                self.assertIsInstance(session, self.fakes[other])
                self.assertEqual((session.model, session.effort),
                                 (f"{other}-intent-x", "low"))

    def test_positive_it_has_the_plan_reviewers_lockdown_arguments(self):
        for builder in ("claude", "codex"):
            with self.subTest(builder=builder):
                settings = self.settings(builder)
                checker = providers.make_intent_checker(settings, cwd="x", usage="u",
                                                        frozen_dir="f")
                reviewer = providers.make_plan_reviewer(settings, cwd="x", usage="u",
                                                        frozen_dir="f")
                self.assertEqual(checker.kwargs, reviewer.kwargs)
                self.assertIs(type(checker), type(reviewer))

    def test_positive_each_call_is_a_fresh_session(self):
        settings = self.settings("claude")
        first = providers.make_intent_checker(settings, cwd="x")
        second = providers.make_intent_checker(settings, cwd="x")
        self.assertIsNot(first, second)

    def test_negative_an_effort_override_keeps_the_model(self):
        settings = self.settings("codex")
        session = providers.make_read_only(settings, "codex", "planner", cwd="x",
                                           effort="xhigh")
        self.assertEqual((session.model, session.effort),
                         (settings["models"]["codex"]["planner"]["model"], "xhigh"))
        plain = providers.make_read_only(settings, "codex", "planner", cwd="x")
        self.assertEqual(plain.effort, settings["models"]["codex"]["planner"]["effort"])


if __name__ == "__main__":
    unittest.main()
