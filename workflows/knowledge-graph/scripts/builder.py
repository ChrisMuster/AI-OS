"""
builder.py — Construct the knowledge graph from the project tree.

Walks the project (gitignore-aware, mirroring the audit workflow), parses each
CONTEXT.md and approved root file with the tolerant parser, then resolves the
extracted tokens into typed nodes and edges.

Edge types produced in Phase 1:
  - child       : filesystem hierarchy (parent dir -> immediate child dir)
  - contains    : project paths in the Contents section
  - depends_on  : project paths in the Dependencies section
  - references  : project paths in any other section (excluding Revision History)
  - links_to    : Obsidian [[links]] anywhere in the file

Only Tier A/B tokens become edges: [[links]] and backtick paths whose first
segment is a top-level directory, plus the approved root files. Bare identifiers
and filenames in backticks (e.g. ``.venv``, ``topic``) are ignored on purpose.
"""

import os
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

import common
import conversation as conversation_layer
import journal as journal_layer
import memory as memory_layer
import parser as ctxparser
import wiki as wiki_layer
from graph import Edge, Graph, Node

# Sections whose backtick paths we never turn into edges.
_SKIP_EDGE_SECTIONS = {"Revision History"}


# ---------------------------------------------------------------------------
# Gitignore-aware tree walk (mirrors workflows/audit/scripts/run.py)
# ---------------------------------------------------------------------------
# Speed note: spawning ``git check-ignore`` is the dominant cost of a rebuild on
# Windows (~0.4s per spawn). Two optimisations keep the ignore semantics exactly
# while cutting that cost:
#   A. _is_git_worktree skips git entirely when the tree is not a repository
#      (e.g. temporary test fixtures) — zero subprocess in that case.
#   B. collect_dirs walks breadth-first and batches one ``git check-ignore
#      --stdin`` call per depth level instead of one per directory, so the call
#      count drops from "directories with children" to "tree depth".
def _is_git_worktree(root: Path) -> bool:
    """True if ``root`` is inside a Git worktree (a ``.git`` exists at root or an
    ancestor). A filesystem check, mirroring Git's own repo discovery, so a
    non-repo tree costs no subprocess at all."""
    try:
        resolved = root.resolve()
    except OSError:
        return False
    for d in (resolved, *resolved.parents):
        if (d / ".git").exists():
            return True
    return False


def _git_check_ignored_batch(root: Path, rel_paths: List[str]) -> set:
    """Return the subset of ``rel_paths`` (project-root-relative, forward slash)
    that git would ignore, in a single ``git check-ignore`` call.

    Uses NUL-separated stdin/stdout (``-z``) and raw bytes so that Windows
    newline translation cannot corrupt the piped paths. Empty set on any git
    failure, matching the previous fall-back behaviour."""
    if not rel_paths:
        return set()
    try:
        result = subprocess.run(
            ["git", "check-ignore", "--stdin", "-z"],
            input="\0".join(rel_paths).encode("utf-8"),
            capture_output=True,
            cwd=str(root),
        )
        out = result.stdout.decode("utf-8", errors="replace")
        return {p.replace("\\", "/") for p in out.split("\0") if p.strip()}
    except Exception:
        return set()


def collect_dirs(root: Path) -> List[Path]:
    """All auditable directories under ``root`` (excluding the root itself), with
    skip/hidden/gitignored/no-recurse pruning applied.

    Breadth-first so each depth level's gitignore check is a single batched
    subprocess; an ignored or no-recurse directory is never descended into, so
    large data trees (e.g. ``collections/``) are pruned before they are walked.
    """
    is_repo = _is_git_worktree(root)
    out: List[Path] = []
    current: List[Path] = [root]

    while current:
        # Gather every candidate child across this whole level, after the cheap
        # skip/hidden pruning, so the gitignore check is one batched call.
        level: List[Tuple[Path, str]] = []
        for parent in current:
            try:
                names = sorted(
                    e.name for e in os.scandir(parent)
                    if e.is_dir()
                    and e.name not in common.SKIP_DIRS
                    and not e.name.startswith(".")
                )
            except OSError:
                continue
            for name in names:
                child = parent / name
                rel = child.relative_to(root).as_posix()
                level.append((child, rel))

        ignored = set()
        if is_repo and level:
            ignored = _git_check_ignored_batch(root, [rel for _, rel in level])

        next_level: List[Path] = []
        for child, rel in level:
            if rel in ignored:
                continue
            out.append(child)
            # No-recurse directories are recorded but never descended into.
            if child.name not in common.NO_RECURSE_DIRS:
                next_level.append(child)
        current = next_level

    return sorted(out)


# ---------------------------------------------------------------------------
# Token classification and resolution
# ---------------------------------------------------------------------------
def _is_placeholder(token: str) -> bool:
    """True for illustrative prose tokens that are never real paths:
    angle-bracket placeholders (``<workflow-name>``), template variables
    (``{{DATE}}``), and date placeholders (``journal/entries/YYYY-MM.md``)."""
    return "<" in token or ">" in token or "{{" in token or "YYYY" in token


def is_project_path(token: str, tlds: set) -> bool:
    """True if a backtick token is a project-root-relative path worth resolving."""
    t = token.strip().rstrip("/")
    if not t or _is_placeholder(t):
        return False
    if t in common.ROOT_LINK_FILES:
        return True
    if "/" in t:
        return t.split("/")[0] in tlds
    # A bare token that exactly names a top-level directory (e.g. `memory/`).
    return t in tlds


def is_project_link(token: str, tlds: set) -> bool:
    """True if a [[link]] target is a project link (vs a wiki-internal one)."""
    t = token.strip().rstrip("/")
    if _is_placeholder(t):
        return False
    if "/" in t:
        return t.split("/")[0] in tlds
    # Bare top-level directory name or a root file stem.
    return t in tlds or t in common.ROOT_LINK_STEMS


def _link_to_path(token: str) -> str:
    """Normalise a [[link]] target into a path-like token for resolution."""
    t = token.strip()
    if t in common.ROOT_LINK_STEMS:
        return t + ".md"
    return t


def resolve_target(token: str, root: Path) -> Tuple[Optional[str], bool]:
    """Resolve a path-like token to a canonical node id and whether it exists.

    Directory/CONTEXT links normalise to the directory node id; root stems and
    files normalise to their path. Returns (node_id, resolved)."""
    t = token.strip().rstrip("/")
    if not t:
        return None, False

    # Root file by name, e.g. "AGENTS.md"
    if t in common.ROOT_LINK_FILES:
        return t, (root / t).exists()

    # Link form "dir/CONTEXT" -> the directory node
    if t.endswith("/CONTEXT"):
        d = t[: -len("/CONTEXT")]
        return d, (root / d / "CONTEXT.md").exists()

    # Explicit "dir/CONTEXT.md" path -> the directory node
    if Path(t).name == "CONTEXT.md":
        d = str(Path(t).parent).replace("\\", "/")
        return d, (root / t).exists()

    full = root / t
    if full.is_dir():
        return t, True
    if full.is_file():
        return t, True

    # Obsidian-style link to a .md file without the extension, e.g.
    # [[skills/web-research/SKILL]] -> skills/web-research/SKILL.md
    md = t + ".md"
    if (root / md).is_file():
        return md, True

    return t, False


def classify(node_id: str, root: Path) -> str:
    """Deterministic node type from id and on-disk shape."""
    if node_id in common.ROOT_LINK_FILES:
        return "root-file"
    if (root / node_id).is_file():
        return "file"
    parts = node_id.split("/")
    if len(parts) == 1:
        return "container" if parts[0] in {"workflows", "wikis", "skills"} else "directory"
    if len(parts) == 2:
        if parts[0] == "workflows":
            return "workflow"
        if parts[0] == "skills":
            return "skill"
        if parts[0] == "wikis":
            return "wiki"
    return "directory"


def _title_for(node_id: str) -> str:
    if node_id in common.ROOT_LINK_FILES:
        return node_id[:-3]
    return Path(node_id).name


# ---------------------------------------------------------------------------
# Edge extraction
# ---------------------------------------------------------------------------
def _add_edges(
    graph: Graph, source_id: str, found_in: str, pc: ctxparser.ParsedContext, root: Path, tlds: set
) -> None:
    """Add contains/depends_on/references/links_to edges for one parsed file."""
    for section, tokens in pc.section_backticks.items():
        if section in _SKIP_EDGE_SECTIONS:
            continue
        if section == "Contents":
            etype = "contains"
        elif section == "Dependencies":
            etype = "depends_on"
        else:
            etype = "references"
        for tok in tokens:
            if not is_project_path(tok, tlds):
                continue
            target, resolved = resolve_target(tok, root)
            if not target or target == source_id:
                continue
            graph.add_edge(Edge(source_id, target, etype, found_in, section, resolved))

    for link in pc.links:
        if not is_project_link(link, tlds):
            continue
        target, resolved = resolve_target(_link_to_path(link), root)
        if not target or target == source_id:
            continue
        graph.add_edge(Edge(source_id, target, "links_to", found_in, "(link)", resolved))


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def build_graph(
    root: Path = common.PROJECT_ROOT,
    *,
    include_memory: bool = False,
    include_wiki: bool = False,
    include_journal: bool = False,
    include_conversation: bool = False,
) -> Graph:
    """Construct the project graph from ``root``.

    With every layer flag False (the default) this is the structural graph of
    tracked content, unchanged from Phases 1-4. ``include_memory`` adds the
    opt-in memory layer (see memory.py); ``include_wiki`` adds the opt-in wiki
    sub-graph layer (see wiki.py): each wiki's pages become nodes, their
    intra-wiki ``[[...]]`` links become edges resolved per-wiki, and each wiki's
    pages are tied to its wiki node by membership. ``include_journal`` adds the
    opt-in journal layer (see journal.py): each month file becomes a node with
    its outbound links, tied to the journal/entries directory by membership.
    ``include_conversation`` adds the opt-in conversation layer (see
    conversation.py): each saved conversation becomes a node — standalone files
    as ``conversation`` nodes and doc-set pages as namespaced ``conversation-doc``
    nodes — with their outbound links, tied to the conversations directory by
    membership. All layers are purely additive — they never alter the structural
    nodes/edges."""
    root = Path(root)
    graph = Graph()
    tlds = common.top_level_dir_names(root)

    # 1. Directory nodes (every walked directory that has a CONTEXT.md).
    for directory in collect_dirs(root):
        ctx = directory / "CONTEXT.md"
        if not ctx.exists():
            continue
        node_id = common.rel(directory, root)
        if node_id in (".", ""):
            continue
        pc = ctxparser.parse_context(common.read_text_safe(ctx))
        graph.add_node(
            Node(
                id=node_id,
                type=classify(node_id, root),
                title=pc.title or _title_for(node_id),
                purpose=pc.purpose,
                last_modified=pc.last_modified,
                sections_present=pc.sections_present,
                parse_warnings=pc.parse_warnings,
            )
        )
        _add_edges(graph, node_id, f"{node_id}/CONTEXT.md", pc, root, tlds)

    # 2. Root file nodes and their outbound edges.
    for rf in sorted(common.ROOT_LINK_FILES):
        path = root / rf
        if not path.exists():
            continue
        pc = ctxparser.parse_context(common.read_text_safe(path))
        graph.add_node(
            Node(
                id=rf,
                type="root-file",
                title=rf[:-3],
                purpose=pc.purpose,
                last_modified=pc.last_modified,
                sections_present=pc.sections_present,
                parse_warnings=pc.parse_warnings,
            )
        )
        _add_edges(graph, rf, rf, pc, root, tlds)

    # 3. Filesystem child edges between directory nodes.
    for node_id in list(graph.nodes):
        if not (root / node_id).is_dir():
            continue
        parent = str(Path(node_id).parent).replace("\\", "/")
        if parent in graph.nodes:
            graph.add_edge(Edge(parent, node_id, "child", parent, "(filesystem)", True))

    # 3b. Opt-in content layers (Phase 5 memory, Phase 6 wiki, Phase 7 journal,
    #     Phase 8 conversation).
    #     Added after the structural graph is complete and before stub creation so
    #     any resolved cross-layer target — and the wikis/<name> source of wiki
    #     membership edges — is stubbed by step 4. No-op unless the flag is set.
    if include_memory:
        memory_layer.add_memory_layer(
            graph, root, tlds,
            resolve_target=resolve_target,
            is_project_path=is_project_path,
            is_project_link=is_project_link,
        )
    if include_wiki:
        wiki_layer.add_wiki_layer(
            graph, root, tlds,
            resolve_target=resolve_target,
            is_project_path=is_project_path,
            is_project_link=is_project_link,
        )
    if include_journal:
        journal_layer.add_journal_layer(
            graph, root, tlds,
            resolve_target=resolve_target,
            is_project_path=is_project_path,
            is_project_link=is_project_link,
        )
    if include_conversation:
        conversation_layer.add_conversation_layer(
            graph, root, tlds,
            resolve_target=resolve_target,
            is_project_path=is_project_path,
            is_project_link=is_project_link,
        )

    # 4. Stub nodes for resolved edge targets that were not otherwise indexed
    #    (e.g. referenced files, or directories pruned as gitignored).
    for edge in list(graph.edges):
        if edge.resolved and edge.target not in graph.nodes:
            graph.add_node(
                Node(
                    id=edge.target,
                    type=classify(edge.target, root),
                    title=_title_for(edge.target),
                )
            )

    return graph
