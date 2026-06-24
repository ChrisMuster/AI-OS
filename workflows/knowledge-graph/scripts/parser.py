"""
parser.py — Tolerant parser for Book Dragon CONTEXT.md (and root .md) files.

This module is pure: it operates on a content string and never touches the
filesystem, so it can be unit-tested in isolation. It extracts the raw signals
needed to build the graph and leaves all path resolution to builder.py, which
has the project root in hand.

Design guarantee: parse_context never raises. Any unexpected problem is recorded
as a parse warning so the overall build always completes.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------
_TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_SECTION_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
_LAST_MOD_RE = re.compile(r"^\*\*Last modified:\*\*\s*(.+?)\s*$", re.MULTILINE)
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)


@dataclass
class ParsedContext:
    """Raw signals extracted from a single CONTEXT.md / root .md file."""

    title: str = ""
    last_modified: str = ""
    purpose: str = ""
    sections_present: List[str] = field(default_factory=list)
    # section name -> list of backtick tokens found in that section (order-preserving, deduped)
    section_backticks: Dict[str, List[str]] = field(default_factory=dict)
    # all [[link]] targets across the file (display alias stripped, deduped, order-preserving)
    links: List[str] = field(default_factory=list)
    parse_warnings: List[str] = field(default_factory=list)


def _strip_fences(content: str) -> str:
    """Remove fenced code blocks so example code is not mistaken for real
    sections, paths, or links."""
    return _FENCE_RE.sub("", content)


def _dedupe(items):
    """Order-preserving de-duplication."""
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _section_bodies(content: str) -> Dict[str, str]:
    """Map each ``## Section`` name to the text between it and the next header."""
    matches = list(_SECTION_RE.finditer(content))
    bodies: Dict[str, str] = {}
    for i, m in enumerate(matches):
        name = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        # Last writer wins if a section name somehow repeats; harmless for our use.
        bodies[name] = content[start:end]
    return bodies


def _first_line(text: str, limit: int = 240) -> str:
    """First non-empty line of ``text``, trimmed and length-capped."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:limit]
    return ""


def parse_context(content: str) -> ParsedContext:
    """Parse a CONTEXT.md / root .md content string. Never raises."""
    pc = ParsedContext()
    try:
        clean = _strip_fences(content)

        title_match = _TITLE_RE.search(clean)
        if title_match:
            pc.title = title_match.group(1).strip()

        lm_match = _LAST_MOD_RE.search(clean)
        if lm_match:
            pc.last_modified = lm_match.group(1).strip()

        bodies = _section_bodies(clean)
        pc.sections_present = list(bodies.keys())

        if "Purpose" in bodies:
            pc.purpose = _first_line(bodies["Purpose"])

        for name, body in bodies.items():
            tokens = _dedupe(t.strip() for t in _BACKTICK_RE.findall(body) if t.strip())
            if tokens:
                pc.section_backticks[name] = tokens

        raw_links = [m.split("|")[0].strip() for m in _LINK_RE.findall(clean)]
        pc.links = _dedupe(link for link in raw_links if link)
    except Exception as exc:  # never let a malformed file break the build
        pc.parse_warnings.append(f"unexpected parse error: {exc}")

    return pc
