"""
content_layer.py — Shared parsing helpers for the opt-in content layers.

The opt-in gitignored content layers (Phase 5's ``memory.py``, Phase 6's
``wiki.py``, and the journal/conversation layers to come) all read free-form
Markdown bodies and pull the same three signals out of them: Obsidian
``[[links]]``, backtick tokens, and fenced code blocks to ignore. Those helpers
were first written for the memory layer; this module lifts them out so neither
content layer owns the other's parsing code, and a future layer can import them
without reaching into a sibling module.

Standard library only, matching the rest of the indexer. Every function here is
pure — it operates on a content string and never touches the filesystem or
raises.
"""

import re
from typing import Iterable, List

# A whole fenced code block, so example ``[[ ]]`` / backticks inside it are not
# mistaken for real links or paths.
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
# An Obsidian-style ``[[target]]`` (optionally ``[[target|alias]]``) link.
_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
# A single inline backtick token.
_BACKTICK_RE = re.compile(r"`([^`]+)`")


def strip_fences(text: str) -> str:
    """Remove fenced code blocks so example code is not parsed for links/paths."""
    return _FENCE_RE.sub("", text)


def dedupe(items: Iterable[str]) -> List[str]:
    """Order-preserving de-duplication."""
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def extract_links(body: str) -> List[str]:
    """All ``[[link]]`` targets in a body, display alias stripped, fenced code
    removed, order-preserving and deduped."""
    clean = strip_fences(body)
    raw = [m.split("|")[0].strip() for m in _LINK_RE.findall(clean)]
    return dedupe(t for t in raw if t)


def extract_backticks(body: str) -> List[str]:
    """All backtick tokens in a body (fenced code removed), trimmed and deduped."""
    clean = strip_fences(body)
    return dedupe(t.strip() for t in _BACKTICK_RE.findall(clean) if t.strip())
