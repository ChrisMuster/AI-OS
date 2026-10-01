#!/usr/bin/env python3
"""
packet.py - writes a run's review packet (plan section 3 and 10.7).

The packet is ``memory/<item>_review_packet.md``, where the handoff reader
(``workflows/handoff/scripts/gather.py``) counts its findings. It is written in the
one layout that reader takes (``memory/review_process.md``): an ``## Open`` and an
``## Addressed`` section, one ``###`` sub-heading per finding, the label first in the
one label form ``R<round>-<n>``.

A finding is **addressed** when the builder marked it ``fixed``, or when the builder
declined it on its merits and the next review round did not raise it again, so the
reviewer let the decline stand (the user's choice, 2026-09-30, review finding R1-1).
Everything else is open: a finding the builder has not acted on yet, one it declined
for scope, and a merits decline no review round has passed yet.

Every piece of text from a provider or a check goes through ``clean``, so a finding
can never break the packet's own structure: no line break, no heading, no character
the project's style guard refuses (the review ledger, "Tool output written into a
packet can break the packet's own structure").
"""

from pathlib import Path

import stopreasons

# Characters the ai-style guard refuses, and what a packet writes instead. Built by
# code point so this source stays plain ASCII: em dash, en dash, the four smart
# quotes, the ellipsis, the non-breaking space and the right arrow.
_REPLACE = {chr(point): text for point, text in (
    (0x2014, " - "), (0x2013, "-"), (0x2018, "'"), (0x2019, "'"), (0x201C, '"'),
    (0x201D, '"'), (0x2026, "..."), (0x00A0, " "), (0x2192, "->"))}
MAX_FIELD = 4000


def clean(text, limit=MAX_FIELD):
    """One line of packet-safe text."""
    text = "" if text is None else str(text)
    for bad, good in _REPLACE.items():
        text = text.replace(bad, good)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit - 3] + "..."
    # A line starting with # would be read as a heading, and a leading - or * as a
    # top-level bullet (a finding of its own); escape both.
    if text[:1] in "#-*+":
        text = "\\" + text
    return text


def is_addressed(finding):
    """Whether a finding goes under Addressed (see the module docstring)."""
    return (finding.get("action") == stopreasons.FIXED
            or (finding.get("action") == stopreasons.DECLINED_MERITS
                and bool(finding.get("decline_accepted"))))


def packet_path(root, item):
    return Path(root) / "memory" / f"{item}_review_packet.md"


def render(item, run_id, ledger, *, header_note=""):
    """The packet's full text for the ledger's current state."""
    stem = f"{item}_review_packet"
    title = stem.replace("_", " ").title()
    lines = ["---", f"name: {stem.replace('_', '-')}",
             f"description: Review packet for {item.replace('_', ' ')}, written by "
             "review-orchestration run " + run_id + "; the Open and Addressed sections "
             "say what remains.",
             "metadata:", "  type: project", "---", "",
             f"# {title}", "",
             f"Written by review-orchestration run `{run_id}`. Findings are labelled by "
             "the run in the one label form; mechanical findings come from the checks "
             "the run makes itself, the rest from the reviewer. A finding the builder "
             "fixed moves to Addressed, and so does one it declined on its merits that "
             "the next review did not raise again; anything else stays open for the "
             "user."]
    if header_note:
        lines += ["", clean(header_note)]
    lines.append("")
    findings = list(ledger.findings.values())
    for heading, wanted in (("## Open", False), ("## Addressed", True)):
        lines += [heading, ""]
        items = [f for f in findings if is_addressed(f) == wanted]
        if not items:
            lines += ["None.", ""]
        for f in items:
            lines += [f"### {f['label']} - {clean(f.get('title'), 300)}", ""]
            detail = f"Source: {f['source']}. Severity: {clean(f.get('severity'))}."
            if f.get("file"):
                detail += f" File: `{clean(f['file'], 300).replace('`', '')}`."
            lines += [detail, ""]
            lines += [f"Evidence: {clean(f.get('evidence'))}", ""]
            lines += [f"Suggested fix: {clean(f.get('fix'))}", ""]
            if f.get("reraises"):
                lines += [f"Re-raises: {f['reraises']}.", ""]
            if f.get("action"):
                lines += [f"Builder: {f['action']}. {clean(f.get('note'))}".rstrip(), ""]
            if f.get("decline_accepted"):
                lines += [f"Reviewer: not raised again in round {f['decline_accepted']}, "
                          "so the decline stands.", ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def write(root, item, run_id, ledger, *, header_note=""):
    """Write the packet. Returns its path."""
    path = packet_path(root, item)
    path.write_text(render(item, run_id, ledger, header_note=header_note),
                    encoding="utf-8", newline="\n")
    return path
