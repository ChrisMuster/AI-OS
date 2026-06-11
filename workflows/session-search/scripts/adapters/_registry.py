#!/usr/bin/env python3
"""
_registry.py — Adapter registry for Book Dragon session search.

Maps source labels to adapter instances. When adding a new AI source:
  1. Create an adapter module in this directory (use _base.py as the interface).
  2. Import it here and add an entry to REGISTRY.
  3. Run: python workflows/session-search/scripts/index.py --rebuild
"""

from .claude_code import ClaudeCodeAdapter
from .cline import ClineAdapter
from .codex import CodexAdapter
from .continue_dev import ContinueDevAdapter
from .copilot import CopilotAdapter
from .cowork import CoworkAdapter
from .cursor import CursorAdapter
from .gemini_cli import GeminiCliAdapter
from .opencode import OpenCodeAdapter

# Registry: source label → adapter instance
REGISTRY: dict = {
    'claude-code':  ClaudeCodeAdapter(),
    'cowork':       CoworkAdapter(),
    'codex':        CodexAdapter(),
    'copilot':      CopilotAdapter(),
    'gemini-cli':   GeminiCliAdapter(),
    'continue-dev': ContinueDevAdapter(),
    'opencode':     OpenCodeAdapter(),
    'cursor':       CursorAdapter(),
    'cline':        ClineAdapter(),
}


def get_adapter(source_label: str):
    """Return the adapter for a given source label, or None if not registered."""
    return REGISTRY.get(source_label)


def all_adapters() -> list:
    """Return all registered adapter instances."""
    return list(REGISTRY.values())
