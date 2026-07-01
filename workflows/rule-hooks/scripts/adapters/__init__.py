"""Per-AI adapters - the only AI-specific code in the evaluator.

Each adapter module exposes:
  - ``parse_event(event, project_root) -> Context | None`` - turn that AI's raw
    hook payload into a normalised Context (or None if the firing tool is not
    one we guard);
  - ``emit_block(decision, ctx) -> int`` - render a block in that AI's block
    contract and return the process exit code to use;
  - ``root_from_event(event) -> str | None`` - the AI's project-root channel,
    if it carries one in the payload (used by the runtime root resolver).

Phase 1 ships Claude Code and Codex. Further adapters (Cursor, Gemini,
Windsurf/Devin, Copilot, Cline, OpenCode) are Phase 2+, added on demand.
"""

from . import claude as _claude
from . import codex as _codex

_ADAPTERS = {
    "claude": _claude,
    "codex": _codex,
}


def get(ai_id):
    """Return the adapter module for an AI id, or None if unsupported."""
    return _ADAPTERS.get(ai_id)
