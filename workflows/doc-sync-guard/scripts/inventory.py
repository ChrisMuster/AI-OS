#!/usr/bin/env python3
"""Loader for the classified output inventory.

The inventory (``workflows/doc-sync-guard/config/output-inventory.yaml``) is the
single tracked record of every gitignored path in this project plus the guard
output destinations named before they exist. This module is the only reader of
that file: every consumer imports these functions rather than parsing the YAML,
so the same path cannot be answered two different ways in two workflows.

Four questions are answered here:

  * ``assertions()``      - which concrete paths must be ignored, and which must
                            stay tracked. Patterned rows contribute their
                            example; concrete rows contribute themselves.
  * ``seed_paths()``      - the paths that seed the unregistered-output check.
                            This is the only view that includes out-of-scope
                            rows.
  * ``doc_sync_exception(path)`` - the documentation answer for a path: an
                            exception id, ``checked``, or ``not-policed``.
  * ``signature_excluded(path)`` - whether the path is excluded from evidence
                            signatures. Derived, never stored: two
                            hand-maintained columns that must agree is another
                            way to encode drift.

Failure is loud in both directions. A missing or unparseable config raises
rather than returning empty answers: an empty inventory read permissively would
exempt the whole project from doc-sync while every test still passed. An
out-of-scope row reaching either classifier raises too, rather than defaulting,
because a total function whose totality depends on an unenforced precondition is
a partial function with better documentation.

Ownership is deliberately absent. Whether a documented child directory inside an
exempt data store answers for itself is decided by the caller that knows the
filesystem, not here (locked decision 19). What this module guarantees is that a
``*`` never crosses a directory separator, so a documented child does not
inherit its container's row by accident; it falls through to the unmatched-path
default of ``checked``.
"""

import re
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
WORKFLOW_DIR = SCRIPT_DIR.parent
DEFAULT_CONFIG = WORKFLOW_DIR / "config" / "output-inventory.yaml"

SCHEMA_VERSION = 1

ASSERT_IGNORED = "assert-ignored"
ASSERT_TRACKED = "assert-tracked"
OUT_OF_SCOPE = "out-of-scope"
ASSERTIONS = (ASSERT_IGNORED, ASSERT_TRACKED, OUT_OF_SCOPE)

DOC_SYNC_VALUES = ("E10", "E11", "E12", "E13", "checked", "not-policed")
PROVENANCE_VALUES = ("sweep-1", "sweep-2", "sweep-3", "decision-13a")

# The default answer for a path no row names. It is `checked` rather than an
# exemption so that an unregistered path is policed, not quietly excused.
DEFAULT_DOC_SYNC = "checked"

# Signature exclusion is derived from these three facts about a row.
SIGNATURE_EXCLUDED_DOC_SYNC = ("E10", "E11", "E12", "E13", "not-policed")
SIGNATURE_EXCLUDED_BASENAMES = ("CONTEXT.md", "LOG.md")

GLOB_CHARS = "*?["


class InventoryError(Exception):
    """The inventory could not be read, or a row does not satisfy the schema."""


# ---------------------------------------------------------------------------
# Path matching
# ---------------------------------------------------------------------------
def is_patterned(path):
    """True when *path* is a pattern rather than one concrete file path.

    Three shapes count, and each needs an example because none of them can be
    handed to `git check-ignore` as it stands:

      * a wildcard or character class;
      * a trailing slash, standing for everything inside a directory;
      * a leading slash, the .gitignore root anchor. A project-relative path
        never starts with one, so `/LOG.md` is a pattern whose example is the
        real path `LOG.md`.
    """
    return (path.endswith("/") or path.startswith("/")
            or any(ch in path for ch in GLOB_CHARS))


def _glob_regex(pattern):
    """Translate a .gitignore-style *pattern* into an anchored regex.

    The subset implemented here is the one the inventory uses:

      * a leading ``/`` anchors to the project root;
      * a pattern containing a slash anywhere else is anchored too, matching
        git's own rule; a pattern with no slash matches at any depth;
      * ``**`` crosses directory separators, ``*`` and ``?`` never do;
      * a character class passes through;
      * a trailing ``/`` means the directory and everything beneath it.

    Deliberately *not* implemented: extending an ordinary match to a matched
    directory's descendants. `wikis/*` answers for `wikis/<item>` and not for
    `wikis/<item>/page.md`, which is what keeps a documented child from
    inheriting its container's row.
    """
    p = pattern
    dir_only = p.endswith("/")
    if dir_only:
        p = p[:-1]
    anchored = p.startswith("/") or "/" in p
    if p.startswith("/"):
        p = p[1:]

    out = []
    i = 0
    while i < len(p):
        ch = p[i]
        if ch == "*":
            if p[i:i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
                continue
            if p[i:i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
            i += 1
            continue
        if ch == "?":
            out.append("[^/]")
            i += 1
            continue
        if ch == "[":
            close = p.find("]", i + 1)
            if close == -1:  # an unclosed bracket is a literal
                out.append(re.escape(ch))
                i += 1
                continue
            body = p[i + 1:close]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body + "]")
            i = close + 1
            continue
        out.append(re.escape(ch))
        i += 1

    body = "".join(out)
    if not anchored:
        body = "(?:.*/)?" + body
    tail = "(?:/.*)?" if dir_only else ""
    return re.compile("^" + body + tail + "$")


def matches(pattern, path):
    """True when *path* is matched by inventory *pattern*."""
    if pattern == path:
        return True
    return _glob_regex(pattern).search(path) is not None


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------
class Row:
    """One inventory row, validated at construction."""

    __slots__ = ("path", "assertion", "example", "seed", "doc_sync",
                 "reason", "provenance")

    def __init__(self, path, assertion, example, seed, doc_sync, reason,
                 provenance):
        self.path = path
        self.assertion = assertion
        self.example = example
        self.seed = seed
        self.doc_sync = doc_sync
        self.reason = reason
        self.provenance = provenance

    @property
    def is_patterned(self):
        return is_patterned(self.path)

    @property
    def assertion_path(self):
        """The concrete path this row asserts: its example, or itself."""
        return self.example if self.example is not None else self.path

    def __repr__(self):  # pragma: no cover - diagnostics only
        return f"Row({self.path!r}, {self.assertion!r})"


class Assertion:
    """One assertable path and what must be true of it."""

    __slots__ = ("path", "kind", "row_path")

    def __init__(self, path, kind, row_path):
        self.path = path        # the concrete path to test
        self.kind = kind        # "ignored" or "tracked"
        self.row_path = row_path  # the row that produced it, for messages

    def __repr__(self):  # pragma: no cover - diagnostics only
        return f"Assertion({self.path!r}, {self.kind!r})"


def _require(condition, message):
    if not condition:
        raise InventoryError(message)


def _parse_row(raw, index):
    where = f"row {index}"
    _require(isinstance(raw, dict), f"{where}: expected a mapping, got {type(raw).__name__}")

    path = raw.get("path")
    _require(isinstance(path, str) and path.strip(),
             f"{where}: 'path' is required and must be a non-empty string")
    where = f"row {index} ({path})"

    unknown = set(raw) - {"path", "assertion", "example", "seed", "doc_sync",
                          "reason", "provenance"}
    _require(not unknown, f"{where}: unknown key(s): {', '.join(sorted(unknown))}")

    assertion = raw.get("assertion")
    _require(assertion in ASSERTIONS,
             f"{where}: 'assertion' must be one of {', '.join(ASSERTIONS)}, "
             f"got {assertion!r}")

    seed = raw.get("seed")
    _require(isinstance(seed, bool), f"{where}: 'seed' is required and must be a boolean")

    # Refusal 7: provenance is required and non-empty, so a row no derivation
    # supports is visible rather than inferred.
    provenance = raw.get("provenance")
    _require(isinstance(provenance, list) and provenance,
             f"{where}: 'provenance' is required and must be a non-empty list")
    for value in provenance:
        _require(value in PROVENANCE_VALUES,
                 f"{where}: unknown provenance {value!r}; expected one of "
                 f"{', '.join(PROVENANCE_VALUES)}")

    example = raw.get("example")
    doc_sync = raw.get("doc_sync")
    reason = raw.get("reason")
    is_assertion_row = assertion in (ASSERT_IGNORED, ASSERT_TRACKED)

    # Refusal 2 and refusal 6 first: they are the direction nobody checks. A
    # loader written from the requirement halves alone accepts both.
    if not (is_assertion_row and is_patterned(path)):
        # Refusal 2: a concrete row carrying an example would simply repeat
        # 'path', and two hand-maintained values that must agree is the defect
        # the derived signature column exists to remove. An out-of-scope row
        # carries no example at all: it is never asserted, so an example there
        # is a value no consumer reads.
        _require(example is None,
                 f"{where}: 'example' is only for patterned assertion rows")
    if is_assertion_row:
        # Refusal 6: a key no consumer reads is a value nobody can be wrong
        # about, so nothing will ever catch it drifting.
        _require(reason is None,
                 f"{where}: 'reason' is only for out-of-scope rows")
        # Refusal 1: a patterned row with no example asserts nothing.
        if is_patterned(path):
            _require(isinstance(example, str) and example.strip(),
                     f"{where}: a patterned row needs an 'example' path")
        # Refusal 3: an assertion row needs a documentation answer.
        _require(doc_sync is not None, f"{where}: 'doc_sync' is required")
        _require(doc_sync in DOC_SYNC_VALUES,
                 f"{where}: unknown doc_sync {doc_sync!r}; expected one of "
                 f"{', '.join(DOC_SYNC_VALUES)}")
    else:
        # Refusal 4: an out-of-scope row has no documentation answer to give.
        _require(doc_sync is None,
                 f"{where}: 'doc_sync' is only for assertion rows")
        # Refusal 5: out of scope without a stated reason is an unexplained hole.
        _require(isinstance(reason, str) and reason.strip(),
                 f"{where}: 'reason' is required on an out-of-scope row")

    return Row(path=path, assertion=assertion, example=example, seed=seed,
               doc_sync=doc_sync, reason=reason, provenance=list(provenance))


class Inventory:
    """The parsed inventory. Constructed by :func:`load`."""

    def __init__(self, version, rows):
        self.version = version
        self.rows = rows

    # -- views --------------------------------------------------------------
    def assertions(self):
        """Concrete paths to assert, one per assertion row. Read-only view."""
        out = []
        for row in self.rows:
            if row.assertion == ASSERT_IGNORED:
                out.append(Assertion(row.assertion_path, "ignored", row.path))
            elif row.assertion == ASSERT_TRACKED:
                out.append(Assertion(row.assertion_path, "tracked", row.path))
        return out

    def seed_paths(self):
        """Paths seeding the unregistered-output check, out-of-scope included."""
        return [row.path for row in self.rows if row.seed]

    # -- classification ------------------------------------------------------
    def row_for(self, path):
        """The row answering for *path*, or None when no row names it.

        Exact equality wins, so a concrete row always beats a pattern that also
        covers it (and so a test may ask about a pattern by writing it out).
        Otherwise the longest matching pattern wins, ties going to declaration
        order, which keeps the answer deterministic rather than file-order
        dependent.
        """
        best = None
        for row in self.rows:
            if row.path == path:
                return row
            if matches(row.path, path):
                if best is None or len(row.path) > len(best.path):
                    best = row
        return best

    def doc_sync_exception(self, path):
        """The documentation answer for *path*.

        Returns an exception id, ``checked``, or ``not-policed``. A path no row
        names defaults to ``checked``: an unregistered path is policed, never
        quietly excused. Raises when the answering row is out of scope.
        """
        row = self.row_for(path)
        if row is None:
            return DEFAULT_DOC_SYNC
        self._refuse_out_of_scope(row, path, "doc_sync_exception")
        return row.doc_sync

    def signature_excluded(self, path):
        """True when *path* is excluded from evidence signatures.

        Derived from three facts, in this order: the row is ``assert-tracked``;
        the doc-sync answer is an exemption or ``not-policed``; or the basename
        is a documentation stream (``CONTEXT.md`` / ``LOG.md``), which is
        excluded because logging a certification run would otherwise invalidate
        the run that wrote the log entry.
        """
        row = self.row_for(path)
        if row is not None:
            self._refuse_out_of_scope(row, path, "signature_excluded")
            if row.assertion == ASSERT_TRACKED:
                return True
            if row.doc_sync in SIGNATURE_EXCLUDED_DOC_SYNC:
                return True
        return path.rsplit("/", 1)[-1] in SIGNATURE_EXCLUDED_BASENAMES

    @staticmethod
    def _refuse_out_of_scope(row, path, caller):
        if row.assertion == OUT_OF_SCOPE:
            raise InventoryError(
                f"{caller}({path!r}) resolves to the out-of-scope row "
                f"{row.path!r}; out-of-scope rows are visible to seed_paths() "
                f"only")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load(config_path=None):
    """Parse and validate the inventory. Raises InventoryError on any problem.

    Fails closed on purpose: a missing or unparseable config raises rather than
    yielding an empty inventory, which every caller would read as "nothing is
    exempt and nothing is asserted" while its tests still passed.
    """
    path = Path(config_path) if config_path is not None else DEFAULT_CONFIG
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InventoryError(f"inventory config unreadable at {path}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise InventoryError(f"inventory config is not valid YAML: {exc}") from exc

    _require(isinstance(data, dict), "inventory config must be a mapping")
    version = data.get("version")
    _require(version == SCHEMA_VERSION,
             f"inventory config version must be {SCHEMA_VERSION}, got {version!r}")
    raw_rows = data.get("rows")
    _require(isinstance(raw_rows, list) and raw_rows,
             "inventory config must carry a non-empty 'rows' list")

    rows = []
    seen = set()
    for index, raw in enumerate(raw_rows, start=1):
        row = _parse_row(raw, index)
        _require(row.path not in seen,
                 f"row {index} ({row.path}): duplicate path; one row per path")
        seen.add(row.path)
        rows.append(row)
    return Inventory(version=version, rows=rows)


_CACHE = {}


def default_inventory():
    """The project inventory, parsed once per process."""
    key = str(DEFAULT_CONFIG)
    if key not in _CACHE:
        _CACHE[key] = load()
    return _CACHE[key]


def clear_cache():
    """Drop the cached project inventory (tests reload after editing config)."""
    _CACHE.clear()


def assertions():
    return default_inventory().assertions()


def seed_paths():
    return default_inventory().seed_paths()


def doc_sync_exception(path):
    return default_inventory().doc_sync_exception(path)


def signature_excluded(path):
    return default_inventory().signature_excluded(path)
