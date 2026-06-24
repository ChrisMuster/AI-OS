"""
graph.py — Node/edge data model, deterministic serialisation, and atomic IO.

The graph is intentionally simple: a dict of nodes keyed by id and a list of
edges de-duplicated on (source, target, type). Serialisation is sorted so the
JSON index produces stable, reviewable diffs. Writes are atomic (temp file +
os.replace) so a crash can never leave a half-written index behind.
"""

import json
import os
import tempfile
from collections import deque
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class Node:
    id: str
    type: str
    title: str = ""
    purpose: str = ""
    last_modified: str = ""
    sections_present: List[str] = field(default_factory=list)
    parse_warnings: List[str] = field(default_factory=list)


@dataclass
class Edge:
    source: str
    target: str
    type: str  # child | contains | depends_on | references | links_to
    found_in: str = ""
    section: str = ""
    resolved: bool = True


class Graph:
    """Container for nodes and edges with de-duplication and stable output."""

    def __init__(self):
        self.nodes: Dict[str, Node] = {}
        self.edges: List[Edge] = []
        self._edge_keys: set = set()
        # Adjacency indexes, built lazily on first traversal (see _ensure_adjacency).
        self._out: Optional[Dict[str, List[Edge]]] = None
        self._in: Optional[Dict[str, List[Edge]]] = None
        self._dirty: bool = True  # adjacency indexes need (re)building

    # -- nodes --------------------------------------------------------------
    def add_node(self, node: Node) -> None:
        """Add a node. First write wins, so a rich node is never overwritten by
        a later stub for the same id."""
        if node.id not in self.nodes:
            self.nodes[node.id] = node

    def has_node(self, node_id: str) -> bool:
        return node_id in self.nodes

    # -- edges --------------------------------------------------------------
    def add_edge(self, edge: Edge) -> None:
        key = (edge.source, edge.target, edge.type)
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)
        self.edges.append(edge)
        self._dirty = True  # invalidate adjacency caches

    # -- serialisation ------------------------------------------------------
    def to_dicts(self) -> Tuple[List[dict], List[dict]]:
        """Return (nodes, edges) as sorted lists of plain dicts."""
        nodes = [asdict(self.nodes[k]) for k in sorted(self.nodes)]
        edges = [
            asdict(e)
            for e in sorted(self.edges, key=lambda e: (e.source, e.type, e.target))
        ]
        return nodes, edges

    @classmethod
    def from_dicts(cls, nodes: List[dict], edges: List[dict]) -> "Graph":
        """Rebuild a Graph from serialised node/edge dicts (the inverse of
        ``to_dicts``). Used by the ``--from-index`` fast path; the default path
        rebuilds from the live tree instead."""
        g = cls()
        node_fields = {f for f in Node.__dataclass_fields__}
        edge_fields = {f for f in Edge.__dataclass_fields__}
        for nd in nodes:
            g.add_node(Node(**{k: v for k, v in nd.items() if k in node_fields}))
        for ed in edges:
            g.add_edge(Edge(**{k: v for k, v in ed.items() if k in edge_fields}))
        return g

    # -- adjacency & traversal ---------------------------------------------
    def _ensure_adjacency(self) -> None:
        """Lazily (re)build outbound/inbound adjacency indexes keyed by node id.
        ``add_edge`` sets ``self._dirty`` whenever an edge is added, so the
        indexes are rebuilt on the next traversal after any change. In normal use
        every edge is added during build, before any traversal is requested, so
        this rebuilds exactly once; the dirty flag keeps the cache correct if an
        edge is ever added afterwards."""
        if self._out is not None and not self._dirty:
            return
        out: Dict[str, List[Edge]] = {}
        inb: Dict[str, List[Edge]] = {}
        for e in self.edges:
            out.setdefault(e.source, []).append(e)
            inb.setdefault(e.target, []).append(e)
        self._out = out
        self._in = inb
        self._dirty = False

    def out_edges(self, node_id: str, etype: Optional[str] = None) -> List[Edge]:
        self._ensure_adjacency()
        edges = self._out.get(node_id, [])
        return [e for e in edges if etype is None or e.type == etype]

    def in_edges(self, node_id: str, etype: Optional[str] = None) -> List[Edge]:
        self._ensure_adjacency()
        edges = self._in.get(node_id, [])
        return [e for e in edges if etype is None or e.type == etype]

    def reverse_reachable(
        self, node_id: str, edge_types: Optional[Set[str]] = None
    ) -> Dict[str, Tuple[int, str]]:
        """Reverse-reachability BFS over inbound edges: every node that can reach
        ``node_id`` by following edges *towards* it. Returns
        ``{node_id: (distance, first_hop_edge_type)}`` for each reachable node
        (excluding the start). Cycle-safe via a visited set."""
        self._ensure_adjacency()
        result: Dict[str, Tuple[int, str]] = {}
        queue: deque = deque()
        for e in self._in.get(node_id, []):
            if edge_types is not None and e.type not in edge_types:
                continue
            if e.source not in result and e.source != node_id:
                result[e.source] = (1, e.type)
                queue.append((e.source, 1, e.type))
        while queue:
            current, dist, first_hop = queue.popleft()
            for e in self._in.get(current, []):
                if edge_types is not None and e.type not in edge_types:
                    continue
                if e.source == node_id or e.source in result:
                    continue
                result[e.source] = (dist + 1, first_hop)
                queue.append((e.source, dist + 1, first_hop))
        return result

    def shortest_path(
        self, source: str, target: str, undirected: bool = False
    ) -> Optional[List[str]]:
        """Shortest path from ``source`` to ``target`` via BFS, or None if no
        path exists. Follows outbound edges; ``undirected`` also follows inbound
        edges (treats every edge as bidirectional)."""
        self._ensure_adjacency()
        if source == target:
            return [source]
        prev: Dict[str, str] = {source: source}
        queue: deque = deque([source])
        while queue:
            current = queue.popleft()
            neighbours = [e.target for e in self._out.get(current, [])]
            if undirected:
                neighbours += [e.source for e in self._in.get(current, [])]
            for nxt in neighbours:
                if nxt in prev:
                    continue
                prev[nxt] = current
                if nxt == target:
                    return self._reconstruct(prev, source, target)
                queue.append(nxt)
        return None

    @staticmethod
    def _reconstruct(prev: Dict[str, str], source: str, target: str) -> List[str]:
        path = [target]
        while path[-1] != source:
            path.append(prev[path[-1]])
        path.reverse()
        return path

    def subtree(self, node_id: str) -> List[str]:
        """All descendant node ids reachable from ``node_id`` via ``child``
        edges (the filesystem hierarchy), excluding the start. Cycle-safe."""
        self._ensure_adjacency()
        out: List[str] = []
        seen: Set[str] = {node_id}
        queue: deque = deque([node_id])
        while queue:
            current = queue.popleft()
            for e in self._out.get(current, []):
                if e.type != "child" or e.target in seen:
                    continue
                seen.add(e.target)
                out.append(e.target)
                queue.append(e.target)
        return sorted(out)


def atomic_write_json(path: Path, data) -> None:
    """Write ``data`` as pretty UTF-8 JSON atomically (temp file + os.replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
