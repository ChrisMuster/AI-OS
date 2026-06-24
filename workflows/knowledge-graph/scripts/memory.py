"""
memory.py — Phase 5 memory-layer extension for the knowledge graph.

Opt-in and purely additive: this module is only invoked by
``builder.build_graph(include_memory=True)``. With the flag off the structural
graph is byte-for-byte what it was before Phase 5. It reads the (gitignored)
``memory/`` files directly — the flag is the deliberate opt-in — so memory
slugs/titles only ever land in the already-gitignored ``index/``; nothing here
widens what is tracked.

Identity (project convention, settled in Phase 5):
  - A memory's canonical link key is its **filename stem**
    (e.g. ``feedback_wait_for_permission``). This matches what every live
    ``[[...]]`` body link and ``MEMORY.md`` entry already uses, and the node id.
  - The frontmatter ``name:`` slug is registered as a **secondary alias** so a
    correctly-slugged link also resolves, but it is descriptive metadata, not
    the link key.
  - The node id is the relative path ``memory/<file>.md``, matching the
    structural "ids are paths" convention and keeping ids collision-proof.

Edges produced:
  - ``links_to`` : a ``[[...]]`` link from one memory to another. Unresolved
    targets are kept with ``resolved=False`` (validation classifies these INFO
    forward-references — the memory rules invite linking to a not-yet-written
    memory).
  - ``contains`` : membership from the ``memory/`` directory node to each memory
    that ``MEMORY.md`` lists, so a listed-but-not-cross-linked memory is not a
    false orphan.
  - ``references`` : a backtick project path inside a memory body resolved to a
    structural node (memory -> structural cross-link), reusing the builder's own
    path resolution. Only added when the resolver callables are supplied.

Standard library only: frontmatter is simple ``key: value`` lines, so a small
line parser suffices — no PyYAML dependency, matching the project's stdlib rule.
"""

import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import common
from content_layer import dedupe, extract_backticks, extract_links
from graph import Edge, Graph, Node

# The gitignored content directory this layer indexes.
MEMORY_DIRNAME = "memory"

# Files inside memory/ that are not themselves memory nodes:
#   - CONTEXT.md  : already a structural node (the directory's context).
#   - MEMORY.md   : the index; parsed separately for membership edges.
#   - LOG.md      : the directory audit trail, never a graph node.
_SKIP_FILES = {"CONTEXT.md", "MEMORY.md", "LOG.md"}

# A Markdown link target in MEMORY.md entries: "- [Title](file.md) -- hook".
# Memory-specific (wikis use Obsidian links), so it stays here rather than in
# content_layer. The shared [[link]]/backtick/fence helpers live in content_layer.
_MD_LINK_RE = re.compile(r"\]\(([^)]+)\)")


# ---------------------------------------------------------------------------
# Parsing helpers (pure; never raise)
# ---------------------------------------------------------------------------
def parse_frontmatter(text: str) -> Tuple[str, str, str, str]:
    """Extract ``name``, ``description``, ``type`` from a leading ``---`` YAML
    block and return them with the remaining body. Handles both a top-level
    ``type:`` and a ``type:`` nested under ``metadata:`` (after stripping the
    indentation both read as ``type: value``). Tolerant: a missing block or
    field yields "" for that field and the whole text as the body. Never raises.
    """
    name = description = mtype = ""
    if not text.startswith("---"):
        return name, description, mtype, text

    lines = text.splitlines()
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        # No closing fence — treat the whole file as body, no frontmatter.
        return name, description, mtype, text

    for raw in lines[1:end]:
        stripped = raw.strip()
        if not stripped or ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip()
        if key == "name" and not name:
            name = val
        elif key == "description" and not description:
            description = val
        elif key == "type" and not mtype:  # top-level or metadata.type
            mtype = val

    body = "\n".join(lines[end + 1:])
    return name, description, mtype, body


# ``extract_links`` and ``extract_backticks`` are imported from content_layer
# (shared with the wiki layer); they are re-exported via the import above so
# existing ``memory.extract_links(...)`` call sites keep working.


# ---------------------------------------------------------------------------
# Layer construction
# ---------------------------------------------------------------------------
def _memory_files(mem_dir: Path) -> List[Path]:
    """Every ``*.md`` file in memory/ that is a memory node (skips CONTEXT.md,
    MEMORY.md, LOG.md). Sorted for deterministic output."""
    try:
        files = [
            p for p in mem_dir.iterdir()
            if p.is_file() and p.suffix == ".md" and p.name not in _SKIP_FILES
        ]
    except OSError:
        return []
    return sorted(files, key=lambda p: p.name)


def _normalise_link(token: str) -> str:
    """Strip a trailing slash and a ``memory/`` prefix from a link/path token so
    it can be looked up in the alias map (which keys on bare stems/filenames)."""
    t = token.strip().rstrip("/")
    if t.startswith(MEMORY_DIRNAME + "/"):
        t = t[len(MEMORY_DIRNAME) + 1:]
    return t


def add_memory_layer(
    graph: Graph,
    root: Path,
    tlds: Optional[set] = None,
    *,
    resolve_target: Optional[Callable[[str, Path], Tuple[Optional[str], bool]]] = None,
    is_project_path: Optional[Callable[[str, set], bool]] = None,
    is_project_link: Optional[Callable[[str, set], bool]] = None,
) -> None:
    """Add memory nodes and edges to ``graph`` in place. No-op if memory/ is
    absent. The optional resolver callables (supplied by builder) enable
    memory->structural cross-layer edges; without them the layer stays strictly
    memory<->memory."""
    root = Path(root)
    mem_dir = root / MEMORY_DIRNAME
    if not mem_dir.is_dir():
        return
    tlds = tlds or set()

    files = _memory_files(mem_dir)

    # 1. Nodes + an alias map (filename stem, filename, full path, name: slug).
    alias: Dict[str, str] = {}
    parsed: List[Tuple[str, str]] = []  # (node_id, body)
    for p in files:
        node_id = f"{MEMORY_DIRNAME}/{p.name}"
        name, description, _mtype, body = parse_frontmatter(common.read_text_safe(p))
        graph.add_node(
            Node(id=node_id, type="memory", title=name or p.stem, purpose=description)
        )
        alias[p.stem] = node_id          # canonical: filename stem
        alias[p.name] = node_id          # filename with extension
        alias[node_id] = node_id         # full relative path
        if name:
            alias.setdefault(name, node_id)  # secondary: frontmatter name: slug
        parsed.append((node_id, body))

    # 2. [[link]] edges (memory->memory; falling back to structural resolution).
    for node_id, body in parsed:
        for link in extract_links(body):
            key = _normalise_link(link)
            target = alias.get(key) or alias.get(key + ".md")
            if target is not None:
                if target != node_id:
                    graph.add_edge(
                        Edge(node_id, target, "links_to", node_id, "(memory link)", True)
                    )
                continue
            # Not a known memory. Try resolving to a structural node (e.g.
            # [[AGENTS]]) before treating it as a forward reference.
            if resolve_target is not None and is_project_link is not None and is_project_link(link, tlds):
                st_target, resolved = resolve_target(_link_to_path(link), root)
                if st_target and st_target != node_id:
                    graph.add_edge(
                        Edge(node_id, st_target, "links_to", node_id, "(memory link)", resolved)
                    )
                    continue
            # Forward reference: keep an unresolved edge to the canonical
            # guess id so validation can classify it INFO.
            guess = key if key.endswith(".md") else key + ".md"
            guess = f"{MEMORY_DIRNAME}/{guess}"
            if guess != node_id:
                graph.add_edge(
                    Edge(node_id, guess, "links_to", node_id, "(memory link)", False)
                )

    # 3. Cross-layer references: backtick project paths in a memory body resolve
    #    to structural nodes. Only when builder supplied its resolvers.
    if resolve_target is not None and is_project_path is not None:
        for node_id, body in parsed:
            for tok in extract_backticks(body):
                if not is_project_path(tok, tlds):
                    continue
                target, resolved = resolve_target(tok, root)
                if not target or target == node_id:
                    continue
                graph.add_edge(
                    Edge(node_id, target, "references", node_id, "(memory body)", resolved)
                )

    # 4. Membership edges from the memory/ directory node to each listed memory,
    #    so a memory that MEMORY.md lists but nothing cross-links is not a false
    #    orphan. Only resolved targets get an edge (a stale MEMORY.md entry for a
    #    deleted memory is silently skipped rather than raised as a broken ref).
    if MEMORY_DIRNAME in graph.nodes:
        for entry in _memory_index_targets(mem_dir):
            target = alias.get(_normalise_link(entry))
            if target is not None and target != MEMORY_DIRNAME:
                graph.add_edge(
                    Edge(
                        MEMORY_DIRNAME, target, "contains",
                        f"{MEMORY_DIRNAME}/MEMORY.md", "(memory index)", True,
                    )
                )


def _memory_index_targets(mem_dir: Path) -> List[str]:
    """The Markdown link targets listed in MEMORY.md, e.g. ``file.md``. Empty if
    MEMORY.md is absent or unreadable."""
    index = mem_dir / "MEMORY.md"
    if not index.is_file():
        return []
    text = common.read_text_safe(index)
    return dedupe(m.strip() for m in _MD_LINK_RE.findall(text) if m.strip())


def _link_to_path(token: str) -> str:
    """Normalise a [[link]] target into a path-like token for structural
    resolution, mirroring builder's handling of root-file stems."""
    t = token.strip()
    if t in common.ROOT_LINK_STEMS:
        return t + ".md"
    return t
