#!/usr/bin/env python3
"""Unit tests for landscape mode and its report section.

The web-research call is injected (research_fn), so these tests never touch the
network or the skill itself; they validate the scan logic, config handling,
failure degradation, and report formatting.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import landscape  # noqa: E402
import report  # noqa: E402


def _fake_research(source_list):
    """Return a research_fn that always yields *source_list* as its sources."""
    def _fn(topic, sources=5):
        return {"sources": source_list}
    return _fn


class TestScanProduct(unittest.TestCase):
    def test_detects_keyword(self):
        research_fn = _fake_research([
            {"title": "Gemini CLI is deprecated", "content": "Google will replace it.",
             "url": "http://x", "tier": 1},
        ])
        finding = landscape.scan_product(
            {"name": "Gemini", "query": "q"}, ["deprecated", "replaced by"], research_fn)
        self.assertEqual(finding["status"], "ok")
        self.assertIn("deprecated", finding["signals"])
        self.assertEqual(len(finding["hits"]), 1)
        self.assertEqual(finding["hits"][0]["url"], "http://x")

    def test_no_signal(self):
        research_fn = _fake_research([
            {"title": "Claude Code update", "content": "New features shipped.",
             "url": "http://y", "tier": 1},
        ])
        finding = landscape.scan_product(
            {"name": "Claude Code", "query": "q"}, ["deprecated"], research_fn)
        self.assertEqual(finding["signals"], [])
        self.assertEqual(finding["hits"], [])

    def test_alias_is_scanned(self):
        research_fn = _fake_research([
            {"title": "Antigravity launches", "content": "...", "url": "http://z", "tier": 2},
        ])
        finding = landscape.scan_product(
            {"name": "Gemini", "query": "q", "aliases": ["Antigravity"]},
            ["deprecated"], research_fn)
        self.assertIn("Antigravity", finding["signals"])

    def test_case_insensitive(self):
        research_fn = _fake_research([
            {"title": "Tool SUNSET announced", "content": "", "url": "u", "tier": 3},
        ])
        finding = landscape.scan_product({"name": "Tool", "query": "q"}, ["sunset"], research_fn)
        self.assertIn("sunset", finding["signals"])

    def test_research_failure_degrades(self):
        def boom(topic, sources=5):
            raise RuntimeError("network down")
        finding = landscape.scan_product({"name": "X", "query": "q"}, ["deprecated"], boom)
        self.assertEqual(finding["signals"], [])
        self.assertEqual(finding["hits"], [])
        self.assertTrue(finding["status"].startswith("research failed"))

    def test_offtopic_source_is_filtered(self):
        # Keyword present but the source does not mention the product - must not flag.
        research_fn = _fake_research([
            {"title": "Oval Invincibles will be renamed as MI London",
             "content": "cricket news", "url": "u", "tier": 1},
        ])
        finding = landscape.scan_product(
            {"name": "Codex CLI", "query": "q"}, ["renamed"], research_fn)
        self.assertEqual(finding["signals"], [])
        self.assertEqual(finding["hits"], [])

    def test_ontopic_keyword_is_flagged(self):
        research_fn = _fake_research([
            {"title": "Codex CLI renamed by OpenAI", "content": "...", "url": "u", "tier": 1},
        ])
        finding = landscape.scan_product(
            {"name": "Codex CLI", "query": "q"}, ["renamed"], research_fn)
        self.assertIn("renamed", finding["signals"])

    def test_substring_does_not_match_inside_longer_word(self):
        # "Devin" must not match "Devine" (a cricketer in an unrelated story).
        research_fn = _fake_research([
            {"title": "Devine and Mooney top the auction", "content": "renamed squad",
             "url": "u", "tier": 1},
        ])
        finding = landscape.scan_product(
            {"name": "Windsurf / Devin", "query": "q", "aliases": ["Devin"]},
            ["renamed"], research_fn)
        self.assertEqual(finding["signals"], [])
        self.assertEqual(finding["hits"], [])

    def test_alias_mention_passes_identity_gate(self):
        # A source about the replacement name (alias) alone still counts.
        research_fn = _fake_research([
            {"title": "Windsurf becomes Devin Desktop", "content": "rebranded",
             "url": "u", "tier": 1},
        ])
        finding = landscape.scan_product(
            {"name": "Windsurf / Devin", "query": "q", "aliases": ["Devin"]},
            ["rebranded"], research_fn)
        self.assertIn("rebranded", finding["signals"])
        self.assertIn("Devin", finding["signals"])


class TestCheckLandscape(unittest.TestCase):
    def test_disabled(self):
        findings, status = landscape.check_landscape({"landscape": {"enabled": False}})
        self.assertEqual(status, "disabled")
        self.assertEqual(findings, [])

    def test_no_landscape_key(self):
        findings, status = landscape.check_landscape({})
        self.assertEqual(status, "disabled")

    def test_no_products(self):
        findings, status = landscape.check_landscape(
            {"landscape": {"enabled": True, "watch": []}})
        self.assertEqual(status, "no products configured")

    def test_runs_all_products(self):
        research_fn = _fake_research([
            {"title": "Tool A and Tool B sunset announcement", "content": "",
             "url": "u", "tier": 1},
        ])
        config = {"landscape": {
            "enabled": True, "signal_keywords": ["sunset"],
            "watch": [{"name": "Tool A", "query": "q"}, {"name": "Tool B", "query": "q"}],
        }}
        findings, status = landscape.check_landscape(config, research_fn=research_fn)
        self.assertEqual(status, "ok")
        self.assertEqual(len(findings), 2)
        self.assertIn("sunset", findings[0]["signals"])


class TestLandscapeReport(unittest.TestCase):
    def test_no_signals(self):
        findings = [{"name": "A", "signals": [], "hits": [], "sources_read": 5, "status": "ok"}]
        text = report.build_landscape_report(findings, "ok")
        self.assertIn("No status-change signals", text)

    def test_no_coverage_is_inconclusive(self):
        findings = [{"name": "A", "signals": [], "hits": [], "sources_read": 0, "status": "ok"}]
        text = report.build_landscape_report(findings, "ok")
        self.assertIn("No sources could be read", text)
        self.assertIn("not conclusive", text)

    def test_flagged_lists_sources(self):
        findings = [{
            "name": "Gemini", "signals": ["deprecated"], "status": "ok",
            "hits": [{"title": "t", "url": "http://x", "tier": 1, "signals": ["deprecated"]}],
        }]
        text = report.build_landscape_report(findings, "ok")
        self.assertIn("Gemini", text)
        self.assertIn("deprecated", text)
        self.assertIn("http://x", text)

    def test_disabled(self):
        text = report.build_landscape_report([], "disabled")
        self.assertIn("disabled", text)

    def test_error_status(self):
        text = report.build_landscape_report([], "web-research unavailable (boom)")
        self.assertIn("Could not run", text)

    def test_per_product_error_surfaced(self):
        findings = [{"name": "X", "signals": [], "hits": [],
                     "status": "research failed (timeout)"}]
        text = report.build_landscape_report(findings, "ok")
        self.assertIn("X", text)
        self.assertIn("research failed", text)


if __name__ == "__main__":
    unittest.main()
