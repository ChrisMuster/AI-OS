# Knowledge Graph

**Last modified:** 2026-08-23

## Purpose
Deterministic, script-driven indexer that parses the project's `CONTEXT.md` files and approved root files into a rebuildable node/edge graph, then validates and traverses it. It turns existing project structure and relationships - directory hierarchy, Contents references, Dependencies, and Obsidian `[[links]]` - into queryable indexes for traversal, impact analysis, orphan detection, duplicate detection, and broken-reference validation. The default build covers tracked structural content. Opt-in content layers can be added with repeatable `--layer` flags for memory, wiki, journal, and conversation content. The build and read-only query commands are also exposed to MCP-capable AI clients through the Biblio Tools `build_knowledge_graph` and `query_knowledge_graph` tools; the CLI remains the contract.

## Contents
- scripts/ - `workflows/knowledge-graph/scripts/` [[workflows/knowledge-graph/scripts/CONTEXT]] - Parser, graph model with traversal, builder, validation checks, report formatter, content-layer modules, shared helpers, and the `run.py` entry point.
- tests/ - `workflows/knowledge-graph/tests/` [[workflows/knowledge-graph/tests/CONTEXT]] - Standalone unit tests for the parser, graph builder, validation checks, traversal, query commands, session-search cross-reference, and content layers, plus a one-command runner.
- index/ - `workflows/knowledge-graph/index/` [[workflows/knowledge-graph/index/CONTEXT]] - Generated output (`nodes.json`, `edges.json`, `meta.json`). Rebuildable and gitignored.
- archived/ - `workflows/knowledge-graph/archived/` [[workflows/knowledge-graph/archived/CONTEXT]] - Per-phase build/handover documents and completed follow-up handovers kept for history after close-out.
- last-report.md - generated output of `validate --save`. Gitignored; may include names from gitignored directories and may not exist until generated.

## Inputs
- The project's `CONTEXT.md` files and approved root files: `AGENTS.md` [[AGENTS]], `README.md` [[README]], `SOUL.md` [[SOUL]], `AGENT-SETUP.md` [[AGENT-SETUP]], and AI wrapper files. No user input or API keys are required for the structural graph.
- Optional gitignored content layers when explicitly requested: `memory/`, `wikis/` [[wikis/CONTEXT]], `journal/entries/` [[journal/entries/CONTEXT]], and `conversations/`.

## Outputs

**The saved index is an optional cache, not the graph.** Every command rebuilds the graph in memory from the project's current files before answering, because a rebuild is sub-second and can never return a stale answer. `build` is the only command that writes `index/`, it is run by hand, and nothing depends on its output. The only way to read the saved files instead of rebuilding is to pass `--from-index`, which only `stats` accepts. A saved index older than the project is therefore expected and is not a fault.

- `index/nodes.json` - One entry per graph node. Gitignored and rebuildable.
- `index/edges.json` - One entry per relationship. Gitignored and rebuildable.
- `index/meta.json` - Build metadata: timestamp, node/edge counts, layers included, and broken-reference count. These describe the last `build` run, not the current project; read them as a record of that run rather than as the graph's live state.
- Report printed to stdout.
- `last-report.md` - the saved validation report, written only by `validate --save`. Gitignored, since a content-layer run can carry names and titles drawn from gitignored directories, and absent until a `--save` run creates it.
- Exit codes relied on by the MCP wrapper: `build` exits 0 on success and 1 on write failure; `validate` exits 1 only if a FAIL finding exists; read-only query commands exit 0 on success, 2 on an unknown node id, and 1 on a `--from-index` load failure.

## Steps
1. Build or rebuild the graph index:
   `python workflows/knowledge-graph/scripts/run.py build [--dry-run] [--layer memory] [--layer wiki] [--layer journal] [--layer conversation]`
2. Validate the graph:
   `python workflows/knowledge-graph/scripts/run.py validate [--save] [--json] [--no-backrefs]`
3. Query and traverse the graph with read-only commands:
   `node`, `neighbors`, `impact`, `path`, `subtree`, `stats`, `orphans`, `broken`, and `sessions`.
4. Append LOG.md with a completion or failure entry.

## Dependencies
- `AGENTS.md` [[AGENTS]] - Defines the CONTEXT.md schema and project structure parsed by the indexer.
- `templates/` [[templates/CONTEXT]] - Standard CONTEXT.md and LOG.md templates used to scaffold this workflow.
- `workflows/session-search/` [[workflows/session-search/CONTEXT]] - Runtime dependency of the `sessions` command only; absent or unreadable indexes degrade to an empty result.
- `workflows/biblio-tools/` [[workflows/biblio-tools/CONTEXT]] - Exposes the build and query commands as MCP tools.
- Python 3.9+ standard library.
- `git` (optional) - Used for gitignore-aware directory pruning; the workflow falls back if Git is unavailable.

## Known Issues
- The default build covers the structural project graph. The memory, wiki, journal, conversation, audit-hook, and session-search follow-ups are complete, so no functional roadmap follow-ups remain.
- The `sessions` command is best-effort and FTS5 keyword-based, not semantic. An absent or unreadable session index returns an empty result and exit 0.
- Rebuild speed was optimised on 2026-06-24: the directory walk is breadth-first with one batched `git check-ignore` per depth level and skips git entirely outside a worktree, preserving Git ignore semantics and identical graph output. Residual per-command latency is mostly Python process startup, which this workflow does not control.
- When a content layer is built, the generated `index/` output and `last-report.md` may contain personal names or content-derived titles from gitignored directories, so both stay gitignored and must never be committed.
- `index/meta.json` is easy to mistake for a status file for the graph. It carries a `generated_at` timestamp and node and edge counts, which read as though they describe the project now, when they describe the last manual `build`. A reader comparing those figures against a live query will find them different whenever the project has moved on since that run, and will be tempted to report the graph as stale when only the cache is. See the note at the top of Outputs; the live figures come from any command run without `--from-index`.
- Relationship precision depends on Tier A/B conventions: `[[links]]` and backtick paths whose first segment is a top-level directory or root file are treated as edges; bare identifiers and filenames are deliberately ignored to avoid false edges.

## Revision History
Earlier history archived to LOG.md on 2026-06-24.
- 2026-06-22 - Phase 5 completed: added the opt-in memory layer, validation handling, MCP parameters, and tests.
- 2026-06-23 - Phase 6 completed: added the opt-in wiki sub-graph layer, shared content-layer helpers, validation handling, MCP parameters, and tests.
- 2026-06-23 - Phase 7 completed: added the opt-in journal light layer, validation handling, MCP parameters, and tests.
- 2026-06-23 - Phase 8 completed: added the opt-in conversation layer, validation handling, MCP parameters, and tests.
- 2026-06-23 - Wrote the audit-hook integration handover.
- 2026-06-23 - Audit-hook integration completed; the full audit consumes `validate --json` and surfaces structural graph findings.
- 2026-06-23 - Wrote the session-search cross-reference handover.
- 2026-06-24 - Session-search cross-reference completed; added the read-only `sessions` command and MCP dispatcher support.
- 2026-06-24 - Rebuild speed optimised: `collect_dirs` walks breadth-first with one batched `git check-ignore` per depth level and skips git outside a worktree; the directory walk dropped from ~9s to ~1s and the KG test suite from ~410s to ~60s with byte-for-byte identical graph output. Planning/handover/roadmap docs are now treated as personal (gitignored), so ROADMAP.md and the archived plans are no longer tracked or listed in Contents.
- 2026-08-12 - Guard-coverage stage 3c: `last-report.md` added to Outputs. It is written by `validate --save` and was described in Contents and Known Issues but not in the section a reader consults for what the workflow writes. Documentation gap rather than a privacy finding, since the path is gitignored and already carries a tracked inventory row. It matters because stage 3b's unregistered-output detection reads Outputs rather than prose.
- 2026-08-23 - Documented the saved index as an optional cache rather than the graph, in Outputs and Known Issues, after its two-month-old `meta.json` was read as evidence that the graph itself had gone stale. It had not: every command rebuilds in memory from the current files, `--from-index` is opt-in and accepted only by `stats`, and the full audit's graph findings therefore come from a fresh build every run. The saved figures were 185 nodes and 571 edges against a live 317 and 1288, a gap wide enough to look like a fault, which is why the misreading is recorded as a Known Issue rather than only corrected in Outputs. The index was rebuilt in the same pass so the saved files match the current project. No behavioural change.
