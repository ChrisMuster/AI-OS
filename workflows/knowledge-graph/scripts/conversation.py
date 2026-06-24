"""
conversation.py — Phase 8 conversation-layer extension for the knowledge graph.

Opt-in and purely additive: this module is only invoked by
``builder.build_graph(include_conversation=True)``. With the flag off the
structural graph is byte-for-byte what it was before Phase 8. It reads the
(gitignored) conversation files directly — the flag is the deliberate opt-in — so
conversation titles/paths only ever land in the already-gitignored ``index/``;
nothing here widens what is tracked.

``conversations/`` holds two distinct shapes, and this layer indexes both:

  - **Standalone conversation files** at the top level
    (``conversations/YYYY-MM-DD-slug.md``) — each a saved, summarised
    conversation. One ``conversation`` node per file. This is the single-file
    case, closest to a journal entry.
  - **Doc-sets** — an immediate subdirectory holding several related Markdown
    files (e.g. a starter pack with several guides). Indexed as a namespaced
    sub-graph of per-file ``conversation-doc`` nodes, closest to a wiki. Two
    mechanisms keep doc-sets from colliding (mirroring wiki.py):
      * **Path-as-id** — a page's node id is its project-relative path, so
        same-named pages across doc-sets get distinct ids for free.
      * **Per-group alias map** — link resolution is keyed on a map built fresh
        for each namespace (the standalone pool, or one doc-set), so a
        ``[[stem]]`` only ever resolves within its owning namespace. A stem that
        matches only another namespace's page is left unresolved (an INFO forward
        reference), never linked across namespaces.

Edges produced (mirroring the memory/wiki/journal layers):
  - ``links_to`` : a ``[[...]]`` link, resolved first within the owning namespace,
    then against the structural graph (e.g. ``[[AGENTS]]``). Unresolved targets
    are kept with ``resolved=False`` (validation classifies these INFO
    forward-references).
  - ``references`` : a backtick project path inside a body resolved to a
    structural node (conversation -> structural cross-link), reusing the
    builder's own path resolution. Only added when the resolver callables are
    supplied.
  - ``contains`` : membership from the ``conversations`` directory node to every
    conversation node — standalone files and doc-set pages alike — so a linkless
    conversation is not a false orphan.

Membership note (why no per-doc-set node): the wiki layer roots membership at a
``wikis/<name>`` node, which exists because ``wikis/CONTEXT.md`` lists each wiki
(stubbed by the builder's step 4). ``conversations/CONTEXT.md`` deliberately does
*not* list individual doc-sets (personal-data isolation), so a
``conversations/<doc-set>`` node would never gain an inbound edge — and, being a
plain ``directory`` type, would surface as a WARN orphan, breaking the
zero-new-WARN guarantee. So membership is rooted at the single tracked
``conversations`` node (the journal pattern) for both shapes, and no phantom
doc-set node is created. Doc-set *resolution* is still per-set namespaced.

Files are plain Markdown with no YAML frontmatter, so the tolerant
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

# The gitignored content this layer indexes lives under conversations/.
CONV_DIRNAME = "conversations"

# Node types: standalone files vs doc-set pages, so queries can tell them apart.
STANDALONE_TYPE = "conversation"
DOCSET_PAGE_TYPE = "conversation-doc"

# Files that are never conversation nodes:
#   - CONTEXT.md : already a structural node (the directory's context).
#   - LOG.md     : the directory audit trail, never a graph node.
# Non-Markdown siblings (a .zip, a .gitkeep) are skipped for free by the
# ``*.md`` filter in discovery.
_SKIP_FILES = {"CONTEXT.md", "LOG.md"}


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def _standalone_files(conv_dir: Path) -> List[Path]:
    """Every top-level ``*.md`` in conversations/ that is a saved conversation
    (skips CONTEXT.md, LOG.md). Sorted for deterministic output."""
    try:
        files = [
            p for p in conv_dir.iterdir()
            if p.is_file() and p.suffix == ".md" and p.name not in _SKIP_FILES
        ]
    except OSError:
        return []
    return sorted(files, key=lambda p: p.name)


def _docset_dirs(conv_dir: Path) -> List[Path]:
    """Immediate ``conversations/<name>/`` subdirectories (doc-sets). Skips
    hidden directories. Sorted for deterministic output."""
    try:
        dirs = [
            p for p in conv_dir.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        ]
    except OSError:
        return []
    return sorted(dirs, key=lambda p: p.name)


def _docset_pages(docset_dir: Path) -> List[Path]:
    """Every ``*.md`` page in a doc-set (skips CONTEXT.md, LOG.md). Sorted for
    deterministic output."""
    try:
        files = [
            p for p in docset_dir.iterdir()
            if p.is_file() and p.suffix == ".md" and p.name not in _SKIP_FILES
        ]
    except OSError:
        return []
    return sorted(files, key=lambda p: p.name)


def _normalise_link(token: str, id_prefix: str) -> str:
    """Strip a trailing slash and, if present, the namespace's fully-qualified
    ``<id_prefix>/`` path from a link token so it can be looked up in the owning
    namespace's alias map (which keys on bare stems / filenames / full ids)."""
    t = token.strip().rstrip("/")
    prefix = f"{id_prefix}/"
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
# One namespace (the standalone pool, or a single doc-set)
# ---------------------------------------------------------------------------
def _index_group(
    graph: Graph,
    root: Path,
    tlds: set,
    files: List[Path],
    *,
    node_type: str,
    id_prefix: str,
    resolve_target: Optional[Callable[[str, Path], Tuple[Optional[str], bool]]],
    is_project_path: Optional[Callable[[str, set], bool]],
    is_project_link: Optional[Callable[[str, set], bool]],
) -> List[str]:
    """Add the nodes and intra-namespace/cross-layer edges for one namespace.

    ``id_prefix`` is the path the namespace's node ids hang off — ``conversations``
    for the standalone pool, ``conversations/<name>`` for a doc-set. Returns the
    list of node ids created, so the caller can root membership edges at them.
    """
    # 1. Nodes + a per-namespace alias map (filename stem, filename, full path).
    alias: Dict[str, str] = {}
    parsed: List[Tuple[str, List[str], List[str]]] = []  # (id, links, backticks)
    node_ids: List[str] = []
    for p in files:
        node_id = f"{id_prefix}/{p.name}"
        text = common.read_text_safe(p)
        title = ctxparser.parse_context(text).title or p.stem
        graph.add_node(Node(id=node_id, type=node_type, title=title))
        alias[p.stem] = node_id          # canonical: filename stem
        alias[p.name] = node_id          # filename with extension
        alias[node_id] = node_id         # full relative path
        parsed.append((node_id, extract_links(text), extract_backticks(text)))
        node_ids.append(node_id)

    # 2. [[link]] edges, resolved within THIS namespace, then structurally.
    for node_id, links, _backticks in parsed:
        for link in links:
            key = _normalise_link(link, id_prefix)
            target = alias.get(key) or alias.get(key + ".md")
            if target is not None:
                if target != node_id:
                    graph.add_edge(
                        Edge(node_id, target, "links_to", node_id, "(conversation link)", True)
                    )
                continue
            # Not a node in this namespace. Try a structural target (e.g.
            # [[AGENTS]]) before treating it as a forward reference. A bare stem
            # that only matches another namespace's page is not a project link,
            # so it falls through to an unresolved (INFO) edge.
            if resolve_target is not None and is_project_link is not None and is_project_link(link, tlds):
                st_target, resolved = resolve_target(_link_to_path(link), root)
                if st_target and st_target != node_id:
                    graph.add_edge(
                        Edge(node_id, st_target, "links_to", node_id, "(conversation link)", resolved)
                    )
                    continue
            # Forward reference within the owning namespace: keep an unresolved
            # edge to the namespaced guess id so validation classifies it INFO.
            guess = key if key.endswith(".md") else key + ".md"
            guess_id = f"{id_prefix}/{guess}"
            if guess_id != node_id:
                graph.add_edge(
                    Edge(node_id, guess_id, "links_to", node_id, "(conversation link)", False)
                )

    # 3. Cross-layer references: backtick project paths in a body resolve to
    #    structural nodes. Only when builder supplied its resolvers.
    if resolve_target is not None and is_project_path is not None:
        for node_id, _links, backticks in parsed:
            for tok in backticks:
                if not is_project_path(tok, tlds):
                    continue
                target, resolved = resolve_target(tok, root)
                if not target or target == node_id:
                    continue
                graph.add_edge(
                    Edge(node_id, target, "references", node_id, "(conversation body)", resolved)
                )

    return node_ids


# ---------------------------------------------------------------------------
# Layer construction
# ---------------------------------------------------------------------------
def add_conversation_layer(
    graph: Graph,
    root: Path,
    tlds: Optional[set] = None,
    *,
    resolve_target: Optional[Callable[[str, Path], Tuple[Optional[str], bool]]] = None,
    is_project_path: Optional[Callable[[str, set], bool]] = None,
    is_project_link: Optional[Callable[[str, set], bool]] = None,
) -> None:
    """Add conversation nodes and edges to ``graph`` in place. No-op if
    conversations/ is absent or empty. The optional resolver callables (supplied
    by builder) enable conversation->structural cross-layer edges; without them
    the layer stays strictly conversation<->conversation."""
    root = Path(root)
    conv_dir = root / CONV_DIRNAME
    if not conv_dir.is_dir():
        return
    tlds = tlds or set()

    all_ids: List[str] = []

    # Standalone pool — one namespace keyed off conversations/.
    standalone = _standalone_files(conv_dir)
    if standalone:
        all_ids += _index_group(
            graph, root, tlds, standalone,
            node_type=STANDALONE_TYPE,
            id_prefix=CONV_DIRNAME,
            resolve_target=resolve_target,
            is_project_path=is_project_path,
            is_project_link=is_project_link,
        )

    # Doc-sets — each subdirectory is its own namespace keyed off
    # conversations/<name>/, resolved per-set so same-named pages never collide.
    for docset in _docset_dirs(conv_dir):
        pages = _docset_pages(docset)
        if not pages:
            continue
        all_ids += _index_group(
            graph, root, tlds, pages,
            node_type=DOCSET_PAGE_TYPE,
            id_prefix=f"{CONV_DIRNAME}/{docset.name}",
            resolve_target=resolve_target,
            is_project_path=is_project_path,
            is_project_link=is_project_link,
        )

    # Membership edges from the conversations directory node to every
    # conversation node (standalone files and doc-set pages alike), so a linkless
    # conversation is not a false orphan. Gated on the node's presence:
    # conversations is a real tracked structural node present at build time, so
    # the gate is safe (see the module docstring on why no per-doc-set node is
    # used). Note the source is the directory, not its CONTEXT — the doc-set
    # pages live under conversations/<name>/, not directly under it, but all
    # conversation content belongs to the one conversations archive.
    if CONV_DIRNAME in graph.nodes:
        found_in = f"{CONV_DIRNAME}/CONTEXT.md"
        for node_id in all_ids:
            graph.add_edge(
                Edge(CONV_DIRNAME, node_id, "contains", found_in, "(conversations)", True)
            )
