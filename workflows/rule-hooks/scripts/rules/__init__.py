"""Rule registry - maps an event category to its ordered list of checks.

A check is a callable ``check(ctx) -> Decision | None`` (None == allow). The
dispatcher runs them in order, returns the first BLOCK, and collects any WARNs.
Hard (blocking) rules come before trial (warn-only) rules so a real block is
never masked by a warn.

Phase 1 scope (see HOOKS-PLAN.md 4E / rollout Task 9):
  shell : A6 dangerous-bash, A4 LOG-redirect, A7 .env-redirect (block) + A2, A3 (trial, warn)
  write : A7 .env-protect, B3 personal-data (block)
  read  : none (reads are allowed in Phase 1)

Phase 2+ adds A1 (relative paths), A5 (Claude PowerShell), and flips A2/A3 to
blocking once the fire-log shows them clean.
"""

from .env_protect import check as a7_env
from .log_redirect import check as a4_log
from .dangerous_bash import check as a6_bash
from .personal_data import check as b3_personal
from .shell_style import check_forward_slashes as a2_slashes
from .shell_style import check_dedicated_tools as a3_tools

_SHELL_RULES = [a6_bash, a4_log, a7_env, a2_slashes, a3_tools]
_WRITE_RULES = [a7_env, b3_personal]
_READ_RULES = []

_BY_CATEGORY = {
    "shell": _SHELL_RULES,
    "write": _WRITE_RULES,
    "read": _READ_RULES,
}


def rules_for(category):
    """Return the ordered check list for an event category (or [])."""
    return _BY_CATEGORY.get(category, [])
