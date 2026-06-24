#!/usr/bin/env python3
"""
run.py — Command-line entry point for the knowledge-graph indexer.

Commands fall into two groups:

  build                              — construct the index and write it to disk.
  validate / node / neighbors /      — read-only analysis and traversal over the
  impact / path / subtree / stats /    graph. These rebuild the graph in memory
  orphans / broken / sessions          by default (sub-second, always fresh);
                                       pass --from-index to read the saved JSON.

The `sessions` command additionally shells out to the session-search index
(`workflows/session-search/scripts/search.py --json`) to surface the session
transcripts that mention a node — read-only and best-effort: an absent or
unreadable index simply yields no sessions, never an error.

Every read-only command accepts --json so the MCP layer can consume structured
output. Unknown node ids produce a clear error plus closest-match suggestions.

Exit-code contract (relied on by the MCP `_run_json_script` wrapper):
  build              — 0 on success; 1 on write failure.
  validate           — 0 normally; 1 only if a FAIL finding exists (no check
                       emits FAIL today, so this is effectively always 0).
  read-only queries  — 0 on success; 2 on an unknown node id (with difflib
                       suggestions on stderr). 1 is reserved for --from-index
                       load failure.

Usage examples:
    python workflows/knowledge-graph/scripts/run.py build [--dry-run]
    python workflows/knowledge-graph/scripts/run.py validate [--save] [--json]
    python workflows/knowledge-graph/scripts/run.py impact workflows/audit
    python workflows/knowledge-graph/scripts/run.py path AGENTS.md skills/web-research
"""

import argparse
import difflib
import json
import re
import subprocess
import sys
from pathlib import Path

# Allow running both as a script and as part of the package.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402
import validate as validate_mod  # noqa: E402
from builder import build_graph  # noqa: E402
from graph import Graph, atomic_write_json  # noqa: E402
from report import exit_code, format_report  # noqa: E402

# Ensure stdout can render Unicode on Windows terminals.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

INDEX_DIR = common.PROJECT_ROOT / "workflows" / "knowledge-graph" / "index"
WORKFLOW_LOG = common.PROJECT_ROOT / "workflows" / "knowledge-graph" / "LOG.md"
ROOT_LOG = common.PROJECT_ROOT / "LOG.md"
REPORT_PATH = common.PROJECT_ROOT / "workflows" / "knowledge-graph" / "last-report.md"

# The session-search query CLI consumed (as a subprocess, never imported) by the
# read-only `sessions` cross-reference command. See cmd_sessions below.
SESSION_SEARCH_PY = (
    common.PROJECT_ROOT / "workflows" / "session-search" / "scripts" / "search.py"
)


def _append_log(path: Path, ts: str, action: str, note: str) -> None:
    """Append a single project-standard log entry. Best-effort; never fatal."""
    entry = f"[{ts}] | Actor: Biblio | Action: {action} | Note: {note}\n"
    try:
        if not path.exists():
            return
        content = path.read_text(encoding="utf-8")
        sep = "" if content.endswith("\n") else "\n"
        path.write_text(content + sep + entry, encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Shared helpers for the read-only query/validation commands
# ---------------------------------------------------------------------------
def _included_layers(args: argparse.Namespace) -> list:
    """The layers requested via --layer, always including the structural base.
    Order is fixed (structural, then memory, wiki, journal, conversation) so
    meta.json is stable regardless of the order the flags were passed."""
    extra = getattr(args, "layer", None) or []
    return ["structural"] + [
        layer for layer in ("memory", "wiki", "journal", "conversation") if layer in extra
    ]


def _load_graph(args: argparse.Namespace) -> Graph:
    """Rebuild the graph in memory (default) or load it from the saved index
    when --from-index is passed. Rebuild-in-memory is the default because it is
    sub-second and can never return a stale answer. --from-index reads whatever
    layers were built; a rebuild honours --layer."""
    if getattr(args, "from_index", False):
        try:
            nodes = json.loads((INDEX_DIR / "nodes.json").read_text(encoding="utf-8"))
            edges = json.loads((INDEX_DIR / "edges.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(
                f"ERROR: could not read saved index from {common.rel(INDEX_DIR)}/ ({exc}).\n"
                "Run `build` first, or drop --from-index to rebuild in memory.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        return Graph.from_dicts(nodes, edges)
    layers = _included_layers(args)
    return build_graph(
        include_memory="memory" in layers,
        include_wiki="wiki" in layers,
        include_journal="journal" in layers,
        include_conversation="conversation" in layers,
    )


def _resolve_id(graph: Graph, raw: str) -> str:
    """Return ``raw`` if it is a known node id; otherwise exit with a clear
    error and difflib-based closest-match suggestions."""
    if raw in graph.nodes:
        return raw
    suggestions = difflib.get_close_matches(raw, list(graph.nodes), n=5, cutoff=0.4)
    msg = [f"ERROR: unknown node id `{raw}`."]
    if suggestions:
        msg.append("Did you mean one of:")
        msg += [f"  - {s}" for s in suggestions]
    else:
        msg.append("No close matches found. Run `stats` or `build` to list nodes.")
    print("\n".join(msg), file=sys.stderr)
    raise SystemExit(2)


def _emit(args: argparse.Namespace, payload, human_lines) -> int:
    """Print JSON when --json is set, otherwise the human-readable lines."""
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print("\n".join(human_lines))
    return 0


def _edge_dict(e) -> dict:
    return {
        "source": e.source,
        "target": e.target,
        "type": e.type,
        "section": e.section,
        "resolved": e.resolved,
    }


def cmd_build(args: argparse.Namespace) -> int:
    layers = _included_layers(args)
    graph = build_graph(
        include_memory="memory" in layers,
        include_wiki="wiki" in layers,
        include_journal="journal" in layers,
        include_conversation="conversation" in layers,
    )
    nodes, edges = graph.to_dicts()
    broken = [e for e in edges if not e["resolved"]]
    meta = {
        "generated_at": common.now_ts(),
        "layers": layers,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "broken_reference_count": len(broken),
    }

    if args.dry_run:
        print(f"[DRY RUN] would write to {common.rel(INDEX_DIR)}/")
        print(f"[DRY RUN]   layers: {', '.join(layers)}")
        print(f"[DRY RUN]   nodes: {len(nodes)}")
        print(f"[DRY RUN]   edges: {len(edges)}")
        print(f"[DRY RUN]   broken references: {len(broken)}")
        if broken:
            print("[DRY RUN] broken references (up to 10):")
            for e in broken[:10]:
                print(f"[DRY RUN]   {e['source']} --{e['type']}--> {e['target']}")
        return 0

    ts = common.now_ts()
    _append_log(
        WORKFLOW_LOG, ts, "started",
        f"Building knowledge graph (layers: {', '.join(layers)}).",
    )
    try:
        atomic_write_json(INDEX_DIR / "nodes.json", nodes)
        atomic_write_json(INDEX_DIR / "edges.json", edges)
        atomic_write_json(INDEX_DIR / "meta.json", meta)
    except Exception as exc:
        _append_log(WORKFLOW_LOG, ts, "failed", f"Knowledge graph build failed: {exc}")
        _append_log(ROOT_LOG, ts, "failed", f"knowledge-graph build failed. {exc}")
        print(f"ERROR: build failed: {exc}", file=sys.stderr)
        return 1

    print(f"Knowledge graph written to {common.rel(INDEX_DIR)}/")
    print(f"  nodes: {len(nodes)}")
    print(f"  edges: {len(edges)}")
    print(f"  broken references: {len(broken)}")

    note = (
        f"Knowledge graph built. {len(nodes)} nodes, {len(edges)} edges, "
        f"{len(broken)} broken reference(s)."
    )
    _append_log(WORKFLOW_LOG, ts, "completed", note)
    _append_log(ROOT_LOG, ts, "completed", f"knowledge-graph build ran. {note}")
    return 0


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------
def cmd_validate(args: argparse.Namespace) -> int:
    ts = common.now_ts()
    _append_log(WORKFLOW_LOG, ts, "started", "Validating knowledge graph.")
    try:
        graph = _load_graph(args)
        findings = validate_mod.run_checks(
            graph, common.PROJECT_ROOT, include_backrefs=not args.no_backrefs
        )
        nodes, edges = graph.to_dicts()
        report = format_report(
            findings, node_count=len(nodes), edge_count=len(edges), run_at=ts
        )
    except Exception as exc:
        _append_log(WORKFLOW_LOG, ts, "failed", f"Knowledge graph validation failed: {exc}")
        _append_log(ROOT_LOG, ts, "failed", f"knowledge-graph validate failed. {exc}")
        print(f"ERROR: validation failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        payload = {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "findings": [
                {"severity": f.severity, "subject": f.subject, "message": f.message}
                for f in findings
            ],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(report)
        print(
            "Guarantee: node-id collisions are structurally impossible "
            "(the graph keys every node by a unique id)."
        )

    if args.save:
        try:
            REPORT_PATH.write_text(report + "\n", encoding="utf-8")
            if not args.json:
                print(f"\nReport saved to {common.rel(REPORT_PATH)}")
        except OSError as exc:
            print(f"WARNING: could not save report: {exc}", file=sys.stderr)

    warns = sum(1 for f in findings if f.severity == "WARN")
    note = (
        f"Knowledge graph validated. {len(findings)} finding(s), "
        f"{warns} warning(s) across {len(nodes)} nodes."
    )
    _append_log(WORKFLOW_LOG, ts, "completed", note)
    _append_log(ROOT_LOG, ts, "completed", f"knowledge-graph validate ran. {note}")
    return exit_code(findings)


# ---------------------------------------------------------------------------
# node
# ---------------------------------------------------------------------------
def cmd_node(args: argparse.Namespace) -> int:
    graph = _load_graph(args)
    nid = _resolve_id(graph, args.id)
    node = graph.nodes[nid]
    inbound = graph.in_edges(nid)
    outbound = graph.out_edges(nid)

    payload = {
        "id": node.id,
        "type": node.type,
        "title": node.title,
        "purpose": node.purpose,
        "last_modified": node.last_modified,
        "sections_present": node.sections_present,
        "parse_warnings": node.parse_warnings,
        "inbound": [_edge_dict(e) for e in inbound],
        "outbound": [_edge_dict(e) for e in outbound],
    }

    lines = [
        f"{node.id}  [{node.type}]",
        f"  title: {node.title}",
    ]
    if node.purpose:
        lines.append(f"  purpose: {node.purpose}")
    if node.last_modified:
        lines.append(f"  last modified: {node.last_modified}")
    lines += _grouped_edge_lines("inbound", inbound, by="source")
    lines += _grouped_edge_lines("outbound", outbound, by="target")
    return _emit(args, payload, lines)


def _grouped_edge_lines(label: str, edges, *, by: str):
    """Format edges grouped by type for human output."""
    if not edges:
        return [f"  {label}: none"]
    lines = [f"  {label} ({len(edges)}):"]
    by_type = {}
    for e in edges:
        by_type.setdefault(e.type, []).append(getattr(e, by))
    for etype in sorted(by_type):
        for other in sorted(by_type[etype]):
            lines.append(f"    --{etype}--> {other}" if by == "target" else f"    {other} --{etype}-->")
    return lines


# ---------------------------------------------------------------------------
# neighbors
# ---------------------------------------------------------------------------
def cmd_neighbors(args: argparse.Namespace) -> int:
    graph = _load_graph(args)
    nid = _resolve_id(graph, args.id)
    want_in = args.direction in ("in", "both")
    want_out = args.direction in ("out", "both")

    inbound = graph.in_edges(nid, args.type) if want_in else []
    outbound = graph.out_edges(nid, args.type) if want_out else []

    payload = {
        "id": nid,
        "direction": args.direction,
        "type_filter": args.type,
        "inbound": [_edge_dict(e) for e in inbound],
        "outbound": [_edge_dict(e) for e in outbound],
    }
    lines = [f"{nid}  neighbors (direction: {args.direction}"
             + (f", type: {args.type}" if args.type else "") + ")"]
    if want_in:
        lines += _grouped_edge_lines("inbound", inbound, by="source")
    if want_out:
        lines += _grouped_edge_lines("outbound", outbound, by="target")
    return _emit(args, payload, lines)


# ---------------------------------------------------------------------------
# impact
# ---------------------------------------------------------------------------
def cmd_impact(args: argparse.Namespace) -> int:
    graph = _load_graph(args)
    nid = _resolve_id(graph, args.id)
    reachable = graph.reverse_reachable(nid, validate_mod.IMPACT_EDGE_TYPES)

    items = [
        {"id": other, "distance": dist, "first_hop": first_hop}
        for other, (dist, first_hop) in reachable.items()
    ]
    items.sort(key=lambda d: (d["distance"], d["first_hop"], d["id"]))

    payload = {
        "id": nid,
        "edge_types": sorted(validate_mod.IMPACT_EDGE_TYPES),
        "impacted_count": len(items),
        "impacted": items,
    }

    lines = [
        f"Impact of `{nid}` — {len(items)} node(s) would be affected if it were "
        f"renamed or deleted",
        f"  (following inbound {', '.join(sorted(validate_mod.IMPACT_EDGE_TYPES))} edges)",
    ]
    if not items:
        lines.append("  nothing depends on this node.")
    else:
        last_dist = None
        for it in items:
            if it["distance"] != last_dist:
                lines.append(f"  distance {it['distance']}:")
                last_dist = it["distance"]
            lines.append(f"    {it['id']}  (via {it['first_hop']})")
    return _emit(args, payload, lines)


# ---------------------------------------------------------------------------
# path
# ---------------------------------------------------------------------------
def cmd_path(args: argparse.Namespace) -> int:
    graph = _load_graph(args)
    src = _resolve_id(graph, args.source)
    dst = _resolve_id(graph, args.target)
    path = graph.shortest_path(src, dst, undirected=args.undirected)

    payload = {
        "source": src,
        "target": dst,
        "undirected": args.undirected,
        "found": path is not None,
        "path": path or [],
        "length": (len(path) - 1) if path else None,
    }
    if path:
        lines = [
            f"Shortest path ({'undirected' if args.undirected else 'directed'}), "
            f"{len(path) - 1} hop(s):",
            "  " + " -> ".join(path),
        ]
    else:
        lines = [
            f"No {'undirected ' if args.undirected else ''}path from `{src}` to `{dst}`."
        ]
    return _emit(args, payload, lines)


# ---------------------------------------------------------------------------
# subtree
# ---------------------------------------------------------------------------
def cmd_subtree(args: argparse.Namespace) -> int:
    graph = _load_graph(args)
    nid = _resolve_id(graph, args.id)
    descendants = graph.subtree(nid)
    members = [nid] + descendants
    member_set = set(members)
    internal_edges = [
        _edge_dict(e)
        for e in graph.edges
        if e.source in member_set and e.target in member_set
    ]

    payload = {
        "id": nid,
        "descendant_count": len(descendants),
        "descendants": descendants,
        "edges": internal_edges,
    }
    lines = [f"Subtree of `{nid}` — {len(descendants)} descendant(s) via child edges:"]
    if not descendants:
        lines.append("  (leaf — no child directories)")
    else:
        lines += [f"  {d}" for d in descendants]
    return _emit(args, payload, lines)


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------
def cmd_stats(args: argparse.Namespace) -> int:
    # Orphan counting is duplicated here rather than calling validate.check_orphans:
    # stats reports a raw orphan *count* (every node with no inbound edge), whereas
    # check_orphans additionally classifies each orphan's severity (WARN vs INFO).
    # The two answers are intentionally different — keep them separate.
    graph = _load_graph(args)
    node_types = {}
    for node in graph.nodes.values():
        node_types[node.type] = node_types.get(node.type, 0) + 1
    edge_types = {}
    inbound_counts = {}
    broken = 0
    for e in graph.edges:
        edge_types[e.type] = edge_types.get(e.type, 0) + 1
        inbound_counts[e.target] = inbound_counts.get(e.target, 0) + 1
        if not e.resolved:
            broken += 1

    has_inbound = set(inbound_counts)
    orphans = sorted(n for n in graph.nodes if n not in has_inbound)
    most_referenced = sorted(
        inbound_counts.items(), key=lambda kv: (-kv[1], kv[0])
    )[:10]

    payload = {
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "node_types": dict(sorted(node_types.items())),
        "edge_types": dict(sorted(edge_types.items())),
        "broken_reference_count": broken,
        "orphan_count": len(orphans),
        "orphans": orphans,
        "most_referenced": [{"id": nid, "inbound": c} for nid, c in most_referenced],
    }

    lines = [
        f"Knowledge graph — {len(graph.nodes)} nodes, {len(graph.edges)} edges",
        "",
        "Nodes by type:",
    ]
    lines += [f"  {t:<12} {c}" for t, c in sorted(node_types.items())]
    lines += ["", "Edges by type:"]
    lines += [f"  {t:<12} {c}" for t, c in sorted(edge_types.items())]
    lines += ["", f"Broken references: {broken}", f"Orphans: {len(orphans)}", ""]
    lines.append("Most-referenced nodes:")
    lines += [f"  {c:>3}  {nid}" for nid, c in most_referenced]
    return _emit(args, payload, lines)


# ---------------------------------------------------------------------------
# orphans / broken — validation slices
# ---------------------------------------------------------------------------
def cmd_orphans(args: argparse.Namespace) -> int:
    graph = _load_graph(args)
    findings = validate_mod.check_orphans(graph)
    payload = {
        "count": len(findings),
        "orphans": [
            {"severity": f.severity, "id": f.subject, "message": f.message}
            for f in findings
        ],
    }
    lines = [f"Orphan nodes — {len(findings)} found:"]
    if not findings:
        lines.append("  none — every node has at least one inbound edge.")
    else:
        lines += [f"  [{f.severity}] {f.subject} — {f.message}" for f in findings]
    return _emit(args, payload, lines)


def cmd_broken(args: argparse.Namespace) -> int:
    graph = _load_graph(args)
    findings = validate_mod.check_broken_references(graph, common.PROJECT_ROOT)
    payload = {
        "count": len(findings),
        "broken_references": [
            {"severity": f.severity, "target": f.subject, "message": f.message}
            for f in findings
        ],
    }
    warns = sum(1 for f in findings if f.severity == "WARN")
    lines = [f"Broken references — {len(findings)} total, {warns} actionable (WARN):"]
    if not findings:
        lines.append("  none — every edge resolves to an existing target.")
    else:
        lines += [f"  [{f.severity}] {f.subject} — {f.message}" for f in findings]
    return _emit(args, payload, lines)


# ---------------------------------------------------------------------------
# sessions — best-effort cross-reference into the session-search index
# ---------------------------------------------------------------------------
def _fts5_safe(text: str) -> str:
    """Turn arbitrary text into a query that can never be an FTS5 syntax error.

    Extracts word tokens (dropping every operator/punctuation character FTS5
    treats specially) and quotes each as a phrase, joined by spaces — an
    implicit AND. A title like ``Knowledge Graph`` becomes ``"Knowledge"
    "Graph"``; a title with a slash, hyphen, or quote loses those characters
    rather than tripping the parser. Returns "" when there are no word tokens."""
    tokens = re.findall(r"\w+", text or "", flags=re.UNICODE)
    return " ".join(f'"{t}"' for t in tokens)


def session_query_terms(node, override=None) -> str:
    """Derive the FTS5 search terms for a node's session cross-reference.

    Order of preference (Decision: title-first with a --terms override):
      1. an explicit caller override,
      2. the node's title,
      3. the last path segment of the node id.
    The first candidate that yields a non-empty FTS5-safe query wins. Pure
    (node in, query string out) so it is trivially unit-testable."""
    for candidate in (override, getattr(node, "title", None), node.id.rsplit("/", 1)[-1]):
        if candidate:
            terms = _fts5_safe(candidate)
            if terms:
                return terms
    return ""


def run_session_search(
    terms: str,
    limit: int = 10,
    since: str = None,
    ai: str = None,
    source: str = None,
) -> list:
    """Run the session-search CLI (`search.py --json`) as a subprocess and return
    its parsed result list. Best-effort by contract: any failure — the script
    missing, a non-zero exit, or unparseable/non-list output — yields ``[]`` and
    never raises. (An absent index already yields ``[]`` from search() itself.)

    Decoupled by subprocess via sys.executable rather than importing across
    workflow scripts/ dirs, mirroring the audit hook's call into validate --json.
    """
    if not terms:
        return []
    argv = [sys.executable, str(SESSION_SEARCH_PY), terms, "--json", "--limit", str(limit)]
    if since:
        argv += ["--since", since]
    if ai:
        argv += ["--ai", ai]
    if source:
        argv += ["--source", source]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8"
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return []
    return data if isinstance(data, list) else []


def cmd_sessions(args: argparse.Namespace) -> int:
    graph = _load_graph(args)
    nid = _resolve_id(graph, args.id)
    node = graph.nodes[nid]
    terms = session_query_terms(node, getattr(args, "terms", None))
    sessions = run_session_search(
        terms, limit=args.limit, since=args.since, ai=args.ai, source=args.source,
    )

    payload = {
        "id": nid,
        "terms": terms,
        "count": len(sessions),
        "sessions": sessions,
    }

    lines = [f"Sessions mentioning `{nid}` — search terms: {terms or '(none)'}"]
    if not sessions:
        lines.append("  no matching sessions (or the session index is absent).")
    else:
        lines.append(f"  {len(sessions)} session(s):")
        for s in sessions:
            title = s.get("session_title") or s.get("session_id") or "Unknown session"
            date_str = (s.get("timestamp") or "")[:10]
            src = s.get("source", "")
            ai_identity = s.get("ai_identity", "")
            ai_tag = f"  |  {ai_identity}" if ai_identity else ""
            snippet = s.get("snippet", "")
            lines.append(f"    [{date_str}] {title}  ({src}{ai_tag})")
            if snippet:
                lines.append(f"      {snippet}")
    return _emit(args, payload, lines)


def _add_layer_flag(p: argparse.ArgumentParser) -> None:
    """The opt-in content-layer flag. Repeatable so the gitignored content layers
    (memory/wiki/journal/conversation) combine without a redesign. Default off
    keeps the build byte-for-byte the structural graph."""
    p.add_argument(
        "--layer",
        action="append",
        choices=["memory", "wiki", "journal", "conversation"],
        metavar="LAYER",
        help="Additionally include an opt-in content layer (repeatable). "
             "Currently: memory, wiki, journal, conversation. Omit for the "
             "structural graph only.",
    )


def _add_common_query_flags(p: argparse.ArgumentParser) -> None:
    """Flags shared by every read-only command."""
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human-readable text.",
    )
    p.add_argument(
        "--from-index",
        action="store_true",
        help="Read the saved index/*.json instead of rebuilding in memory.",
    )
    _add_layer_flag(p)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic knowledge-graph indexer for Book Dragon."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # build -----------------------------------------------------------------
    p_build = sub.add_parser("build", help="Build the graph index from the project.")
    p_build.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be written without modifying any files or logs.",
    )
    _add_layer_flag(p_build)
    p_build.set_defaults(func=cmd_build)

    # validate --------------------------------------------------------------
    p_validate = sub.add_parser(
        "validate", help="Run all validation checks and report findings."
    )
    _add_common_query_flags(p_validate)
    p_validate.add_argument(
        "--save", action="store_true",
        help="Also write the report to last-report.md (gitignored).",
    )
    p_validate.add_argument(
        "--no-backrefs", action="store_true",
        help="Skip the optional dependency back-reference gap check.",
    )
    p_validate.set_defaults(func=cmd_validate)

    # node ------------------------------------------------------------------
    p_node = sub.add_parser("node", help="Show a node and its inbound/outbound edges.")
    p_node.add_argument("id", help="Node id, e.g. workflows/audit or AGENTS.md.")
    _add_common_query_flags(p_node)
    p_node.set_defaults(func=cmd_node)

    # neighbors -------------------------------------------------------------
    p_neighbors = sub.add_parser("neighbors", help="Show adjacent nodes.")
    p_neighbors.add_argument("id", help="Node id.")
    direction = p_neighbors.add_mutually_exclusive_group()
    direction.add_argument(
        "--in", dest="direction", action="store_const", const="in",
        help="Inbound edges only.",
    )
    direction.add_argument(
        "--out", dest="direction", action="store_const", const="out",
        help="Outbound edges only.",
    )
    p_neighbors.add_argument(
        "--type", default=None,
        help="Restrict to one edge type (child|contains|depends_on|references|links_to).",
    )
    _add_common_query_flags(p_neighbors)
    p_neighbors.set_defaults(func=cmd_neighbors, direction="both")

    # impact ----------------------------------------------------------------
    p_impact = sub.add_parser(
        "impact", help="Reverse-reachability: what breaks if this node is removed."
    )
    p_impact.add_argument("id", help="Node id.")
    _add_common_query_flags(p_impact)
    p_impact.set_defaults(func=cmd_impact)

    # path ------------------------------------------------------------------
    p_path = sub.add_parser("path", help="Shortest path between two nodes.")
    p_path.add_argument("source", help="Source node id.")
    p_path.add_argument("target", help="Target node id.")
    p_path.add_argument(
        "--undirected", action="store_true",
        help="Treat every edge as bidirectional.",
    )
    _add_common_query_flags(p_path)
    p_path.set_defaults(func=cmd_path)

    # subtree ---------------------------------------------------------------
    p_subtree = sub.add_parser(
        "subtree", help="All descendants of a directory via child edges."
    )
    p_subtree.add_argument("id", help="Directory node id.")
    _add_common_query_flags(p_subtree)
    p_subtree.set_defaults(func=cmd_subtree)

    # stats -----------------------------------------------------------------
    p_stats = sub.add_parser("stats", help="Counts and most-referenced nodes.")
    _add_common_query_flags(p_stats)
    p_stats.set_defaults(func=cmd_stats)

    # orphans / broken ------------------------------------------------------
    p_orphans = sub.add_parser("orphans", help="List orphan nodes (no inbound edges).")
    _add_common_query_flags(p_orphans)
    p_orphans.set_defaults(func=cmd_orphans)

    p_broken = sub.add_parser("broken", help="List broken references, classified.")
    _add_common_query_flags(p_broken)
    p_broken.set_defaults(func=cmd_broken)

    # sessions --------------------------------------------------------------
    p_sessions = sub.add_parser(
        "sessions",
        help="Best-effort: session transcripts that mention this node.",
    )
    p_sessions.add_argument("id", help="Node id, e.g. workflows/audit.")
    p_sessions.add_argument(
        "--terms", default=None,
        help="Override the FTS5 search terms (default: derived from the node title).",
    )
    p_sessions.add_argument(
        "--limit", type=int, default=10,
        help="Maximum sessions to return (default: 10).",
    )
    p_sessions.add_argument(
        "--since", metavar="YYYY-MM-DD", default=None,
        help="Only sessions on or after this date.",
    )
    p_sessions.add_argument(
        "--ai", metavar="NAME", default=None,
        help='Only sessions from this AI (e.g. "Claude Code").',
    )
    p_sessions.add_argument(
        "--source", metavar="NAME", default=None,
        help="Only sessions from this source (claude-code|cowork).",
    )
    _add_common_query_flags(p_sessions)
    p_sessions.set_defaults(func=cmd_sessions)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
