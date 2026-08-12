#!/usr/bin/env python3
"""Pure CONTEXT.md parsing helpers for the doc-sync guard.

No filesystem or git access: every function takes text (or a list of added
lines) and returns data, so the fiddly parsing is unit-tested with fixtures.

Two questions the guard asks of a CONTEXT.md:
  1. Does the file's `**Last modified:**` date equal the newest date in its
     Revision History section? (final-state check)
  2. Did this change *add* a new Revision History entry? Answered by comparing
     the entry *lines* before vs after (`gained_rh_entry`), which is
     section-aware - a dated bullet added outside the Revision History section
     does not count - and works from full entry lines rather than dates alone, so
     editing an existing entry's date forward is not mistaken for a new entry.
     A brand-new untracked file compares against an empty "before".

The Revision History archiving rule inserts a prose line
"Earlier history archived to LOG.md on YYYY-MM-DD." at the top of the section.
That line is deliberately skipped everywhere a date is collected, so it can
never be mistaken for the newest entry.
"""

import re
from collections import Counter

# A dated Revision History entry bullet. The bullet must be flush-left (column
# 0): real entries and the archive reference line are always top-level, so an
# indented dated sub-bullet under an entry is NOT a separate entry and must not
# be counted. The date may be bare (2026-07-06) or bracketed ([2026-07-06]); the
# separator after it may be a hyphen or a legacy em/en dash. Only the date is
# captured.
RH_ENTRY_RE = re.compile(r"^[-*]\s*\[?(\d{4}-\d{2}-\d{2})\]?")

# The archive reference line at the top of a trimmed Revision History section.
ARCHIVE_LINE_RE = re.compile(r"^\s*Earlier history archived\b", re.IGNORECASE)

# "**Last modified:** 2026-07-06" - capture the whole value; the date is pulled
# out separately so a non-date value simply yields no date (a finding upstream).
_LAST_MODIFIED_RE = re.compile(r"^\*\*Last modified:\*\*\s*(.+?)\s*$", re.MULTILINE)
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.*?)\s*$")


def last_modified_date(text):
    """Return the YYYY-MM-DD date on the `**Last modified:**` line, or None.

    Only an ISO date is returned; a line with no ISO date (or no line at all)
    yields None, which the guard treats as "cannot verify - report it".
    """
    m = _LAST_MODIFIED_RE.search(text)
    if not m:
        return None
    d = _ISO_DATE_RE.search(m.group(1))
    return d.group(0) if d else None


def _revision_history_lines(text):
    """Yield the raw lines inside the `## Revision History` section.

    The section runs from its heading to the next markdown heading (any level)
    or end of file. Returns an empty list if there is no Revision History
    heading.
    """
    lines = text.splitlines()
    out = []
    in_section = False
    for line in lines:
        heading = _HEADING_RE.match(line)
        if heading:
            if in_section:
                break  # next heading ends the section
            if heading.group(1).strip().lower() == "revision history":
                in_section = True
            continue
        if in_section:
            out.append(line)
    return out


def revision_history_dates(text):
    """Return every dated Revision History entry date (YYYY-MM-DD), in order.

    The archive reference line is skipped so its date never counts as an entry.
    """
    dates = []
    for line in _revision_history_lines(text):
        if ARCHIVE_LINE_RE.match(line):
            continue
        m = RH_ENTRY_RE.match(line)
        if m:
            dates.append(m.group(1))
    return dates


def revision_history_entry_lines(text):
    """Return the full, normalised dated Revision History entry lines, in order.

    Like ``revision_history_dates`` but keeps each entry's whole text (stripped)
    rather than only its date, so an entry edited in place (its date or text
    changed) is distinguishable from a genuinely new entry. Section-aware, and
    the archive reference line is skipped so it never counts as an entry.
    """
    lines = []
    for line in _revision_history_lines(text):
        if ARCHIVE_LINE_RE.match(line):
            continue
        if RH_ENTRY_RE.match(line):
            lines.append(line.strip())
    return lines


def archive_reference_lines(text):
    """Return the normalised "Earlier history archived..." reference line(s).

    Records that archiving has happened at some point: when old entries are
    trimmed to LOG.md, this line is added or its date advanced. Its *presence*
    is what `gained_rh_entry` requires before it will consider an archive; its
    presence is not what proves an entry was appended (`_is_archive_plus_append`
    does that), because a second archive on a date the line already carries
    leaves it untouched.
    """
    return [line.strip() for line in _revision_history_lines(text)
            if ARCHIVE_LINE_RE.match(line)]


def newest_archive_reference_date(text):
    """Return the newest date on an archive reference line, or None.

    None means the archive record cannot be read at all: either there is no
    reference line, or the line carries no ISO date. Callers treat both as "no
    archive recorded" rather than guessing, so an unreadable line biases to a
    loud warning.
    """
    dates = []
    for line in archive_reference_lines(text):
        m = _ISO_DATE_RE.search(line)
        if m:
            dates.append(m.group(0))
    return max(dates) if dates else None


def newest_revision_history_date(text):
    """Return the newest (max) Revision History entry date, or None.

    ISO dates sort correctly as strings, so max() gives the newest regardless of
    the newest-at-the-bottom ordering convention.
    """
    dates = revision_history_dates(text)
    return max(dates) if dates else None


def gained_rh_entry(old_text, new_text):
    """True if this change added at least one Revision History entry.

    Section-aware: only dated bullets inside the ``## Revision History`` section
    count (via ``revision_history_entry_lines``), so a dated bullet added
    elsewhere (Contents, Steps, ...) does not satisfy it.

    The comparison works from full entry *lines*, not dates, using a multiset so
    duplicate same-day lines are counted individually:
      * a new line appeared and no old line vanished -> gained (the normal add,
        and a same-day second entry, whose count simply grows).
      * a new line appeared *and* old lines vanished -> gained only when the
        change is a genuine *archive-plus-append*: an archive is recorded by a
        dated reference line that has not moved backwards (a second archive on a
        date the line already carries leaves it unchanged, and that is still an
        archive), a non-empty suffix of the old entries is retained
        unchanged and in order as the prefix of the new entries (the oldest were
        trimmed to LOG.md), and at least one genuinely new entry - dated no
        earlier than the newest retained entry - follows the retained ones. A
        plain in-place edit of an existing entry (its date or
        text changed, no real append) removes and adds a line too, but is NOT a
        gained entry - even if an "Earlier history archived..." line is present,
        because that line alone does not prove an entry was appended. Anything
        that does not fit the archive-plus-append shape biases to *not gained* so
        the guard warns loudly rather than missing the change silently.
      * no new line appeared -> not gained (prose-only or in-place text edit).
    A brand-new untracked file (``old_text=""``) counts as gained if it has any
    entry at all.
    """
    old = revision_history_entry_lines(old_text)
    new = revision_history_entry_lines(new_text)
    if not new:
        return False
    if not old:
        return True
    added = Counter(new) - Counter(old)
    if not added:
        return False
    removed = Counter(old) - Counter(new)
    if not removed:
        return True  # pure addition: every old entry line is retained
    # Lines both appeared and vanished. This is a genuine gain only when it is a
    # true archive-plus-append; otherwise an existing entry was edited in place
    # (possibly dressed up with an archive reference line) and nothing was gained.
    # The question is whether an archive is *recorded*, not whether the reference
    # line changed: a second archive on a date the line already carries leaves it
    # untouched, and reading that as "no archiving happened" reported a genuine
    # archive-plus-append as an in-place edit.
    new_archived_on = newest_archive_reference_date(new_text)
    if new_archived_on is None:
        return False  # no readable archive record - an in-place edit, not a gain
    old_archived_on = newest_archive_reference_date(old_text)
    if old_archived_on is not None and new_archived_on < old_archived_on:
        return False  # the archive record moved backwards; it records nothing new
    return _is_archive_plus_append(old, new)


def _entry_date(entry_line):
    """Return the YYYY-MM-DD date of a Revision History entry line, or None."""
    m = RH_ENTRY_RE.match(entry_line)
    return m.group(1) if m else None


def _is_archive_plus_append(old, new):
    """True when *new* is a valid archive-plus-append of *old* entry lines.

    A genuine archive trims the oldest entries from the top of the section and
    leaves the recent ones untouched, so the retained old entries are a *suffix*
    of ``old`` that appears as the *prefix* of ``new``. A real append then adds at
    least one entry after them. This holds only when:

      * a non-empty suffix of ``old`` is retained unchanged and in order (at least
        one old entry survives - archiving every entry is treated as too
        ambiguous to trust and biases to a loud warning),
      * ``new`` has at least one further entry after that retained prefix, and
      * every such appended entry is dated no earlier than the newest retained
        entry. Appends are newest-at-the-bottom and chronological, so an
        "appended" line older than what it follows is not a genuine new entry -
        it is an old entry that was edited and moved down (with a bogus archive
        line), which must warn, not pass.

    Any other reshuffle (an in-place edit that merely removed and re-added a line)
    fails to match, so the caller warns rather than silently passing.
    """
    for archived in range(1, len(old) + 1):  # trim >=1 oldest from the top
        retained = old[archived:]
        if not retained:
            continue  # all archived - too ambiguous; let the caller warn
        if len(new) <= len(retained):
            continue  # no room for a genuinely new appended entry
        if new[:len(retained)] != retained:
            continue
        appended = new[len(retained):]
        newest_retained = _entry_date(retained[-1])
        if newest_retained is None:
            continue
        if all((_entry_date(a) or "") >= newest_retained for a in appended):
            return True
    return False
