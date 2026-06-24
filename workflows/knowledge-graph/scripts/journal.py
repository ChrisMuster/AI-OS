"""
journal.py — Phase 7 journal-layer extension for the knowledge graph.

Opt-in and purely additive: this module is only invoked by
``builder.build_graph(include_journal=True)``. With the flag off the structural
graph is byte-for-byte what it was before Phase 7. It reads the (gitignored)
``journal/entries/YYYY-MM.md`` month files directly — the flag is the deliberate
opt-in — so journal titles/paths only ever land in the already-gitignored
``index/``; nothing here widens what is tracked.

Light by design. Journal entries are plain daily prose; in practice a month file
carries no ``[[links]]`` and no backtick project paths at all, so the common case
is a near-leaf node (one node per month, few or zero outbound edges). That is not
a bug — the machinery still handles the rich case (a user who does add links or
backtick paths) but most months produce just a node tied to its directory by
membership.

Identity:
  - One node per **month file**: ``type="journal-entry"``, id is the relative
    path ``journal/entries/<file>.md`` (matching the structural "ids are paths"
    convention and keeping ids collision-proof), title from the ``# Journal — …``
    heading (falling back to the filename stem).
  - A flat per-layer alias map (filename stem / filename / full path) lets one
    entry ``[[link]]`` to another, though entries rarely cross-link.

Edges produced (mirroring the memory layer):
  - ``links_to`` : a ``[[...]]`` link from an entry, resolved first within the
    journal layer, then against the structural graph (e.g. ``[[AGENTS]]``).
    Unresolved targets are kept with ``resolved=False`` (validation classifies
    these INFO forward-references).
  - ``references`` : a backtick project path inside an entry body resolved to a
    structural node (journal -> structural cross-link), reusing the builder's own
    path resolution. Only added when the resolver callables are supplied.
  - ``contains`` : membership from the ``journal/entries`` directory node to each
    entry, so a linkless entry is not a false orphan. Unlike the wiki layer's
    ``wikis/<name>`` source, ``journal/entries`` is a real tracked structural node
    present at build time, so membership is gated on its presence (a safe gate
    here — it never drops every edge the way gating the wiki source would).

Entries are plain Markdown with no YAML frontmatter, so the tolerant
``parser.parse_context`` gives the title and the shared ``content_layer`` helpers
give the ``[[links]]`` and backtick tokens (both strip fenced code first).
Standard library only.
"""

from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import common
import parser as ctxparser
from content_layer import extract_backticks, extract_links
from graph import Edge, Graph, Node

# The gitignored content this layer indexes: month files under journal/entries/.
JOURNAL_DIRNAME = "journal"
ENTRIES_SUBDIR = "entries"
JOURNAL_ENTRIES_ID = f"{JOURNAL_DIRNAME}/{ENTRIES_SUBDIR}"

# Files inside journal/entries/ that are not themselves journal entries:
#   - CONTEXT.md : already a structural node (the directory's context).
#   - LOG.md     : the directory audit trail, never a graph node.
_SKIP_FILES = {"CONTEXT.md", "LOG.md"}


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def _entry_files(entries_dir: Path) -> List[Path]:
    """Every ``*.md`` month file in journal/entries/ that is a journal entry
    (skips CONTEXT.md, LOG.md). Sorted for deterministic output."""
    try:
        files = [
            p for p in entries_dir.iterdir()
            if p.is_file() and p.suffix == ".md" and p.name not in _SKIP_FILES
        ]
    except OSError:
        return []
    return sorted(files, key=lambda p: p.name)


def _normalise_link(token: str) -> str:
    """Strip a trailing slash and a ``journal/entries/`` prefix from a link/path
    token so it can be looked up in the alias map (which keys on bare
    stems/filenames)."""
    t = token.strip().rstrip("/")
    prefix = f"{JOURNAL_ENTRIES_ID}/"
    if t.startswith(prefix):
        t = t[len(prefix):]
    return t


def _link_to_path(token: str) -> str:
    """Normalise a [[link]] target into a path-like token for structural
    resolution, mirroring builder's handling of root-file stems."""
    t = token.strip()
    if t in common.ROOT_LINK_STEMS:
        return t + ".md"
    return t


# ---------------------------------------------------------------------------
# Layer construction
# ---------------------------------------------------------------------------
def add_journal_layer(
    graph: Graph,
    root: Path,
    tlds: Optional[set] = None,
    *,
    resolve_target: Optional[Callable[[str, Path], Tuple[Optional[str], bool]]] = None,
    is_project_path: Optional[Callable[[str, set], bool]] = None,
    is_project_link: Optional[Callable[[str, set], bool]] = None,
) -> None:
    """Add journal-entry nodes and edges to ``graph`` in place. No-op if
    journal/entries/ is absent. The optional resolver callables (supplied by
    builder) enable journal->structural cross-layer edges; without them the layer
    stays strictly journal<->journal."""
    root = Path(root)
    entries_dir = root / JOURNAL_DIRNAME / ENTRIES_SUBDIR
    if not entries_dir.is_dir():
        return
    tlds = tlds or set()

    files = _entry_files(entries_dir)
    if not files:
        return

    # 1. Nodes + an alias map (filename stem, filename, full path).
    alias: Dict[str, str] = {}
    parsed: List[Tuple[str, List[str], List[str]]] = []  # (id, links, backticks)
    for p in files:
        node_id = f"{JOURNAL_ENTRIES_ID}/{p.name}"
        text = common.read_text_safe(p)
        title = ctxparser.parse_context(text).title or p.stem
        graph.add_node(Node(id=node_id, type="journal-entry", title=title))
        alias[p.stem] = node_id          # canonical: filename stem
        alias[p.name] = node_id          # filename with extension
        alias[node_id] = node_id         # full relative path
        parsed.append((node_id, extract_links(text), extract_backticks(text)))

    # 2. [[link]] edges (journal->journal; falling back to structural resolution).
    for node_id, links, _backticks in parsed:
        for link in links:
            key = _normalise_link(link)
            target = alias.get(key) or alias.get(key + ".md")
            if target is not None:
                if target != node_id:
                    graph.add_edge(
                        Edge(node_id, target, "links_to", node_id, "(journal link)", True)
                    )
                continue
            # Not a known entry. Try resolving to a structural node (e.g.
            # [[AGENTS]]) before treating it as a forward reference.
            if resolve_target is not None and is_project_link is not None and is_project_link(link, tlds):
                st_target, resolved = resolve_target(_link_to_path(link), root)
                if st_target and st_target != node_id:
                    graph.add_edge(
                        Edge(node_id, st_target, "links_to", node_id, "(journal link)", resolved)
                    )
                    continue
            # Forward reference: keep an unresolved edge to the canonical guess id
            # so validation can classify it INFO.
            guess = key if key.endswith(".md") else key + ".md"
            guess_id = f"{JOURNAL_ENTRIES_ID}/{guess}"
            if guess_id != node_id:
                graph.add_edge(
                    Edge(node_id, guess_id, "links_to", node_id, "(journal link)", False)
                )

    # 3. Cross-layer references: backtick project paths in an entry body resolve
    #    to structural nodes. Only when builder supplied its resolvers.
    if resolve_target is not None and is_project_path is not None:
        for node_id, _links, backticks in parsed:
            for tok in backticks:
                if not is_project_path(tok, tlds):
                    continue
                target, resolved = resolve_target(tok, root)
                if not target or target == node_id:
                    continue
                graph.add_edge(
                    Edge(node_id, target, "references", node_id, "(journal body)", resolved)
                )

    # 4. Membership edges from the journal/entries directory node to each entry,
    #    so a linkless entry is not a false orphan. Gated on the node's presence:
    #    journal/entries is a real tracked structural node present at build time,
    #    so the gate is safe (unlike the wiki layer's gitignored source node).
    if JOURNAL_ENTRIES_ID in graph.nodes:
        found_in = f"{JOURNAL_ENTRIES_ID}/CONTEXT.md"
        for node_id, _links, _backticks in parsed:
            graph.add_edge(
                Edge(
                    JOURNAL_ENTRIES_ID, node_id, "contains",
                    found_in, "(journal entries)", True,
                )
            )
