"""
wiki.py — Phase 6 wiki sub-graph layer for the knowledge graph.

Opt-in and purely additive: this module is only invoked by
``builder.build_graph(include_wiki=True)``. With the flag off the structural
graph is byte-for-byte what it was before Phase 6. It reads the (gitignored)
``wikis/<name>/wiki/`` pages directly — the flag is the deliberate opt-in — so
wiki page titles/paths only ever land in the already-gitignored ``index/``;
nothing here widens what is tracked.

Namespacing is the whole point. Two wikis can each have a page with the same
filename (both have an ``index.md``, for instance). A ``[[stem]]`` link inside
one wiki must resolve to *that* wiki's page, never another's. Two mechanisms
guarantee this:
  - **Path-as-id.** A page's node id is its project-relative path
    ``wikis/<name>/wiki/<page>.md``, so same-named pages across wikis get
    distinct ids for free.
  - **Per-wiki alias map.** Link resolution is keyed on a map built fresh for
    each wiki (page stem / filename / full path -> page id), so a stem only ever
    resolves within its owning wiki. A ``[[stem]]`` that matches only another
    wiki's page is left unresolved (an INFO forward reference), never linked
    across wikis (settled decision: cross-wiki links stay INFO).

Edges produced (mirroring the memory layer):
  - ``links_to`` : an intra-wiki ``[[stem]]`` link, resolved within the owning
    wiki. Unresolved targets are kept with ``resolved=False`` (validation
    classifies these INFO forward-references — wikis are drafted incrementally).
  - ``references`` : a backtick project path inside a page body resolved to a
    structural node (wiki -> structural cross-link), reusing the builder's own
    path resolution. Only added when the resolver callables are supplied.
  - ``contains`` : membership from each ``wikis/<name>`` node to every page in
    that wiki, so a page listed-but-not-cross-linked is not a false orphan. The
    ``wikis/<name>`` node itself is created as a stub by the builder's step 4
    (it is gitignored, so the structural walk does not reach it); this layer runs
    before that step, so the membership edges are added unconditionally rather
    than gated on the node's presence — gating here would wrongly drop every
    membership edge in the real (gitignored) repo.

Pages are plain Markdown with no YAML frontmatter, so the tolerant
``parser.parse_context`` gives the title and the shared ``content_layer`` helpers
give the ``[[links]]`` and backtick tokens (both strip fenced code first, so
``[[ ]]`` inside an example block is ignored). Standard library only.
"""

from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import common
import parser as ctxparser
from content_layer import extract_backticks, extract_links
from graph import Edge, Graph, Node

# The gitignored content directory this layer indexes, and the per-wiki
# subfolder that holds the actual pages.
WIKIS_DIRNAME = "wikis"
WIKI_SUBDIR = "wiki"

# Files inside wikis/<name>/wiki/ that are not themselves wiki pages:
#   - CONTEXT.md / LOG.md     : structural/audit files, never page nodes.
#   - *operations-log.md      : an audit log some wikis keep alongside pages.
_SKIP_FILES = {"CONTEXT.md", "LOG.md"}


def _is_page_file(name: str) -> bool:
    """True if a ``*.md`` filename is a wiki page (not a structural/audit file)."""
    if name in _SKIP_FILES:
        return False
    if name.lower().endswith("operations-log.md"):
        return False
    return True


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def _wiki_dirs(wikis_dir: Path) -> List[Path]:
    """Immediate ``wikis/<name>/`` subdirectories that contain a ``wiki/`` folder.
    Sorted for deterministic output."""
    try:
        names = [
            p for p in wikis_dir.iterdir()
            if p.is_dir() and not p.name.startswith(".") and (p / WIKI_SUBDIR).is_dir()
        ]
    except OSError:
        return []
    return sorted(names, key=lambda p: p.name)


def _wiki_pages(wiki_dir: Path) -> List[Path]:
    """Every ``*.md`` page in a wiki's ``wiki/`` folder (skips CONTEXT.md,
    LOG.md, and *operations-log.md). Sorted for deterministic output."""
    try:
        files = [
            p for p in wiki_dir.iterdir()
            if p.is_file() and p.suffix == ".md" and _is_page_file(p.name)
        ]
    except OSError:
        return []
    return sorted(files, key=lambda p: p.name)


def _normalise_link(token: str, name: str) -> str:
    """Strip a trailing slash and, if present, a fully-qualified
    ``wikis/<name>/wiki/`` prefix from a link token so it can be looked up in the
    owning wiki's alias map (which keys on bare stems / filenames / full ids)."""
    t = token.strip().rstrip("/")
    prefix = f"{WIKIS_DIRNAME}/{name}/{WIKI_SUBDIR}/"
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
def add_wiki_layer(
    graph: Graph,
    root: Path,
    tlds: Optional[set] = None,
    *,
    resolve_target: Optional[Callable[[str, Path], Tuple[Optional[str], bool]]] = None,
    is_project_path: Optional[Callable[[str, set], bool]] = None,
    is_project_link: Optional[Callable[[str, set], bool]] = None,
) -> None:
    """Add wiki-page nodes and edges to ``graph`` in place, one namespaced
    sub-graph per wiki. No-op if wikis/ is absent. The optional resolver
    callables (supplied by builder) enable wiki->structural cross-layer edges;
    without them the layer stays strictly wiki<->wiki."""
    root = Path(root)
    wikis_dir = root / WIKIS_DIRNAME
    if not wikis_dir.is_dir():
        return
    tlds = tlds or set()

    for wiki_name_dir in _wiki_dirs(wikis_dir):
        name = wiki_name_dir.name
        pages = _wiki_pages(wiki_name_dir / WIKI_SUBDIR)
        if not pages:
            continue
        wiki_node_id = f"{WIKIS_DIRNAME}/{name}"

        # 1. Page nodes + a per-wiki alias map (stem, filename, full path).
        alias: Dict[str, str] = {}
        parsed: List[Tuple[str, List[str], List[str]]] = []  # (id, links, backticks)
        for p in pages:
            page_id = f"{wiki_node_id}/{WIKI_SUBDIR}/{p.name}"
            text = common.read_text_safe(p)
            title = ctxparser.parse_context(text).title or p.stem
            graph.add_node(Node(id=page_id, type="wiki-page", title=title))
            alias[p.stem] = page_id          # canonical: page filename stem
            alias[p.name] = page_id          # filename with extension
            alias[page_id] = page_id         # full relative path
            parsed.append((page_id, extract_links(text), extract_backticks(text)))

        # 2. Intra-wiki [[link]] edges, resolved within THIS wiki only.
        for page_id, links, _backticks in parsed:
            for link in links:
                key = _normalise_link(link, name)
                target = alias.get(key) or alias.get(key + ".md")
                if target is not None:
                    if target != page_id:
                        graph.add_edge(
                            Edge(page_id, target, "links_to", page_id, "(wiki link)", True)
                        )
                    continue
                # Not a page in this wiki. Try a structural target (e.g.
                # [[AGENTS]]) before treating it as a forward reference. A bare
                # stem that only matches another wiki's page is not a project
                # link, so it falls through to an unresolved (INFO) edge —
                # cross-wiki links deliberately stay INFO.
                if resolve_target is not None and is_project_link is not None and is_project_link(link, tlds):
                    st_target, resolved = resolve_target(_link_to_path(link), root)
                    if st_target and st_target != page_id:
                        graph.add_edge(
                            Edge(page_id, st_target, "links_to", page_id, "(wiki link)", resolved)
                        )
                        continue
                # Forward reference within the owning wiki: keep an unresolved
                # edge to the namespaced guess id so validation classifies it INFO.
                guess = key if key.endswith(".md") else key + ".md"
                guess_id = f"{wiki_node_id}/{WIKI_SUBDIR}/{guess}"
                if guess_id != page_id:
                    graph.add_edge(
                        Edge(page_id, guess_id, "links_to", page_id, "(wiki link)", False)
                    )

        # 3. Cross-layer references: backtick project paths in a page body
        #    resolve to structural nodes. Only when builder supplied its resolvers.
        if resolve_target is not None and is_project_path is not None:
            for page_id, _links, backticks in parsed:
                for tok in backticks:
                    if not is_project_path(tok, tlds):
                        continue
                    target, resolved = resolve_target(tok, root)
                    if not target or target == page_id:
                        continue
                    graph.add_edge(
                        Edge(page_id, target, "references", page_id, "(wiki body)", resolved)
                    )

        # 4. Membership edges from the wikis/<name> node to each of its pages.
        #    Added unconditionally (see module docstring): the wikis/<name> node
        #    is a step-4 stub created after this layer runs, so it is not yet in
        #    graph.nodes here; gating on its presence would drop every membership
        #    edge in the real gitignored repo.
        found_in = f"{wiki_node_id}/{WIKI_SUBDIR}"
        for page_id, _links, _backticks in parsed:
            graph.add_edge(
                Edge(wiki_node_id, page_id, "contains", found_in, "(wiki pages)", True)
            )
