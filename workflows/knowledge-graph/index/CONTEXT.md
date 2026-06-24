# Knowledge Graph - Index

**Last modified:** 2026-06-24

## Purpose
Holds the generated output of the knowledge-graph indexer. All files here are rebuildable from the project at any time by running `run.py build`, so they are treated as derived artefacts rather than source. Because the graph may include names drawn from gitignored directories (for example wiki names listed in `wikis/CONTEXT.md` [[wikis/CONTEXT]] and the opt-in memory, wiki, journal, and conversation content layers), the entire directory is gitignored and must never be committed.

## Contents
- Generated index files, written by `workflows/knowledge-graph/scripts/run.py` [[workflows/knowledge-graph/scripts/CONTEXT]]:
  - `nodes.json` - one entry per graph node.
  - `edges.json` - one entry per relationship.
  - `meta.json` - build metadata (timestamp, counts, layers, broken-reference count).
  These files are not committed and are not listed individually beyond this description; the filesystem is the authoritative source. The directory skeleton is kept via `.gitkeep`.

## Inputs
None. Files here are produced by the indexer.

## Outputs
None. This is an output directory; its files are consumed by the query and validation commands when `--from-index` is passed.

## Steps
N/A. This is a generated-output directory, not a workflow.

## Dependencies
- `workflows/knowledge-graph/scripts/` [[workflows/knowledge-graph/scripts/CONTEXT]] - Produces every file in this directory.

## Known Issues
- The index files may contain personal-adjacent names (for example wiki directory names, memory titles, journal entry titles, or conversation titles when content layers are included). This is why the directory is gitignored. Never remove the gitignore rule covering `workflows/knowledge-graph/index/` [[workflows/knowledge-graph/index/CONTEXT]].

## Revision History
- 2026-06-19 - Initial creation. Phase 1 output directory for nodes.json, edges.json, and meta.json.
- 2026-06-21 - Phase 4 staleness sweep: relabelled construction-phase wording (removed "introduced by subsequent phases" and "Phase 2 onwards") to describe the finished workflow; future opt-in layers were referenced as such.
- 2026-06-24 - Updated generated-output notes now that all four opt-in content layers are complete.
