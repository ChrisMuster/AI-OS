#!/usr/bin/env python3
"""Unit tests for the latest-version registry resolvers."""
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import registries  # noqa: E402


class TestRegistries(unittest.TestCase):
    def test_unknown_source(self):
        version, error = registries.resolve_latest("bogus", "x")
        self.assertIsNone(version)
        self.assertIn("unknown source", error)

    def test_missing_identifier(self):
        version, error = registries.resolve_latest("npm", "")
        self.assertIsNone(version)
        self.assertIn("no package id", error)

    def test_resolver_success(self):
        with mock.patch.dict(registries.RESOLVERS, {"npm": lambda identifier: "9.9.9"}):
            version, error = registries.resolve_latest("npm", "@x/x")
        self.assertEqual(version, "9.9.9")
        self.assertIsNone(error)

    def test_resolver_network_error(self):
        def boom(identifier):
            raise urllib.error.URLError("offline")

        with mock.patch.dict(registries.RESOLVERS, {"npm": boom}):
            version, error = registries.resolve_latest("npm", "@x/x")
        self.assertIsNone(version)
        self.assertIn("network error", error)

    def test_resolver_http_error(self):
        def boom(identifier):
            raise urllib.error.HTTPError("url", 404, "Not Found", {}, None)

        with mock.patch.dict(registries.RESOLVERS, {"npm": boom}):
            version, error = registries.resolve_latest("npm", "@x/x")
        self.assertIsNone(version)
        self.assertIn("HTTP 404", error)


if __name__ == "__main__":
    unittest.main()
