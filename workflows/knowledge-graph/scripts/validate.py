"""
validate.py — Graph validation checks producing audit-style findings.

Every check is pure analysis over an in-memory Graph. Checks emit Finding
objects (severity, subject, message); the run.py validate command formats and,
optionally, saves them.

Severity philosophy: only genuinely actionable problems are WARN. Everything
that is expected-by-design (gitignored artifacts, illustrative prose in root
files, intentional structural duplicates) is downgraded to INFO so the warning
list stays a true to-do list.
"""

import subprocess
from pathlib import Path

import common
from graph import Graph
from report import Finding

# Opt-in content-layer node types (gitignored layers: memory, wiki pages, journal
# entries, conversations). Their unresolved [[links]] are legitimate forward
# references, never actionable WARNs, and an unlinked content node is INFO at most
# — so these types are excluded from the orphan-WARN set below and routed to INFO
# in check_broken_references.
_CONTENT_LAYER_TYPES = {
    "memory", "wiki-page", "journal-entry", "conversation", "conversation-doc",
}

# Human-readable noun per content-layer node type, for broken-reference messages.
_CONTENT_LAYER_NOUNS = {
    "memory": "memory",
    "wiki-page": "wiki page",
    "journal-entry": "journal entry",
    "conversation": "conversation",
    "conversation-doc": "conversation doc",
}

# Orphan severity: structural directories the user maintains are WARN; root
# files and pure containers having no inbound edge is unremarkable -> INFO.
# Content-layer nodes (see _CONTENT_LAYER_TYPES) are deliberately excluded — a
# memory or wiki page with no inbound link is INFO at most, never actionable.
_ORPHAN_WARN_TYPES = {"workflow", "skill", "wiki", "directory"}

# Back-reference gaps are only interesting between user-maintained directories;
# depending on a root file or a referenced data file needs no reciprocal link.
_BACKREF_TYPES = {"workflow", "skill", "wiki", "directory"}

# Edge types over which "what breaks if I delete this" propagates.
IMPACT_EDGE_TYPES = {"depends_on", "contains", "links_to"}


# ---------------------------------------------------------------------------
# git helper
# ---------------------------------------------------------------------------
def _git_ignored(root: Path, targets) -> set:
    """Subset of ``targets`` that git would ignore. Works on paths whether or
    not they exist (it matches gitignore patterns). Empty on any git failure or
    when the tree is not a repository — callers then treat the ref as genuine."""
    targets = [t for t in targets if t]
    if not targets:
        return set()
    try:
        result = subprocess.run(
            ["git", "check-ignore", *targets],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(root),
        )
        return {
            line.strip().replace("\\", "/")
            for line in result.stdout.splitlines()
            if line.strip()
        }
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def check_broken_references(graph: Graph, root: Path):
    """Check 1 — three-way classification of every unresolved edge."""
    broken = [e for e in graph.edges if not e.resolved]
    ignored = _git_ignored(root, sorted({e.target for e in broken}))
    findings = []
    for e in sorted(broken, key=lambda e: (e.source, e.type, e.target)):
        src = graph.nodes.get(e.source)
        if src is not None and src.type in _CONTENT_LAYER_TYPES:
            # Content-layer rule (memory, wiki pages, journal entries): an
            # unresolved [[link]] is a legitimate forward reference (these layers
            # are drafted incrementally and invite linking to a not-yet-written
            # target); any other unresolved content-layer reference is advisory
            # too. These edges never raise WARN.
            noun = _CONTENT_LAYER_NOUNS.get(src.type, "content node")
            detail = (
                f"forward reference ({noun} not yet written)"
                if e.type == "links_to"
                else f"unresolved reference ({e.type})"
            )
            findings.append(Finding(
                "INFO", e.target,
                f"{detail}; from {noun} `{e.source}`.",
            ))
        elif e.target in ignored:
            findings.append(Finding(
                "INFO", e.target,
                f"expected-absent (gitignored/generated artifact); referenced by `{e.source}` ({e.type}).",
            ))
        elif src is not None and src.type == "root-file" and e.type == "references":
            findings.append(Finding(
                "INFO", e.target,
                f"illustrative prose reference in root file `{e.source}`.",
            ))
        else:
            findings.append(Finding(
                "WARN", e.target,
                f"broken reference — target does not exist (from `{e.source}`, {e.type}).",
            ))
    return findings


def check_orphans(graph: Graph):
    """Check 2 — nodes with no inbound edges."""
    has_inbound = {e.target for e in graph.edges}
    findings = []
    for nid in sorted(graph.nodes):
        if nid in has_inbound:
            continue
        node = graph.nodes[nid]
        sev = "WARN" if node.type in _ORPHAN_WARN_TYPES else "INFO"
        findings.append(Finding(sev, nid, f"orphan — no inbound edges (type: {node.type})."))
    return findings


def check_uncontained(graph: Graph):
    """Check 3 — descendant-aware: a child directory is uncontained only when
    neither it nor any descendant appears in the parent's Contents."""
    findings = []
    child_edges = sorted(
        (e for e in graph.edges if e.type == "child"), key=lambda e: e.target
    )
    for e in child_edges:
        parent, child = e.source, e.target
        contains_targets = {ce.target for ce in graph.out_edges(parent, "contains")}
        contained = any(
            t == child or t.startswith(child + "/") for t in contains_targets
        )
        if not contained:
            findings.append(Finding(
                "WARN", child,
                f"uncontained — neither `{child}` nor any descendant is listed in `{parent}` Contents.",
            ))
    return findings


def check_duplicate_titles(graph: Graph):
    """Check 4 — duplicate ``# Title`` across parsed files. Node-id collisions
    are structurally impossible (the graph keys nodes by id), so they are a
    guarantee, not a finding. File stub nodes (basename fallback titles) are
    excluded to avoid the naive-basename noise the plan warns against."""
    by_title = {}
    for nid, node in graph.nodes.items():
        if node.type == "file":
            continue
        title = (node.title or "").strip()
        if not title:
            continue
        by_title.setdefault(title, []).append(nid)
    findings = []
    for title, ids in sorted(by_title.items()):
        if len(ids) > 1:
            findings.append(Finding(
                "INFO", title,
                f"duplicate title `# {title}` across {len(ids)} files: {', '.join(sorted(ids))}.",
            ))
    return findings


def check_parse_warnings(graph: Graph):
    """Check 5 — surface any parser warnings carried on nodes."""
    findings = []
    for nid in sorted(graph.nodes):
        for warning in graph.nodes[nid].parse_warnings:
            findings.append(Finding("WARN", nid, f"parse warning: {warning}"))
    return findings


def check_backref_gaps(graph: Graph):
    """Check 6 (optional) — A depends_on B, but B never references A back.
    Restricted to user-maintained directory targets so the universal
    dependency on root files does not flood the report."""
    findings = []
    deps = sorted(
        (e for e in graph.edges if e.type == "depends_on"),
        key=lambda e: (e.source, e.target),
    )
    for e in deps:
        target = graph.nodes.get(e.target)
        if target is None or target.type not in _BACKREF_TYPES:
            continue
        back = any(be.target == e.source for be in graph.out_edges(e.target))
        if not back:
            findings.append(Finding(
                "INFO", e.source,
                f"depends on `{e.target}`, but `{e.target}` does not reference it back.",
            ))
    return findings


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run_checks(graph: Graph, root: Path = common.PROJECT_ROOT, *, include_backrefs: bool = True):
    """Run every validation check and return the combined finding list."""
    findings = []
    findings += check_broken_references(graph, root)
    findings += check_orphans(graph)
    findings += check_uncontained(graph)
    findings += check_duplicate_titles(graph)
    findings += check_parse_warnings(graph)
    if include_backrefs:
        findings += check_backref_gaps(graph)
    return findings
