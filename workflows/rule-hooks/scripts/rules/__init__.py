"""Rule registry - maps an event category to its ordered list of checks.

A check is a callable ``check(ctx) -> Decision | None`` (None == allow). The
dispatcher runs them in order, returns the first BLOCK, and collects any WARNs.
Hard (blocking) rules come before trial (warn-only) rules so a real block is
never masked by a warn.

Current scope:
  shell : A10 hidden-command wrapper option, A6 dangerous-bash, A4
          LOG-redirect, A7 .env-redirect, A8 PowerShell-here-string, A9
          inline-interpreter document write, A3 dedicated-tools (block) + A2
          (trial, warn)
  write : A7 .env-protect, B3 personal-data (block)
  read  : none (reads are allowed)

A10 runs FIRST, and the order carries an argument rather than a preference. It
fires on a command none of the others can see: `env -S "rm -rf build"` leaves
the shared reader with an empty argv, so every rule after it would allow the
command for want of anything to read. A rule reporting "this command cannot be
read" has to be asked before the rules that read it, and its message is the
useful one - naming the wrapper rather than whatever the hidden payload turned
out to be this time.

A8 was added after Phase 1 and ships blocking without a trial period, because a
trial records the fault and lets the call through, which is the behaviour it
exists to correct. A5 is a separate, still-unbuilt rule about reaching for the
PowerShell tool at all; A8 is about PowerShell *syntax* inside a Bash call.

A3 was promoted from trial to blocking on 2026-09-02, after its fire-log showed
75 fires in one session. Promotion required narrowing it first: the trial form
could not tell a shell file-read from a filter on a command's output, and
blocking that second case would have refused work with no compliant
alternative. A9 was added in the same pass and ships blocking, because it exists
to close the path that the wrong-tool habit had opened around B3.

A3 keeps one warn: the byte-level allowance, for reads the dedicated tools
cannot perform. It is warned rather than silently allowed so the fire-log can
show whether the allowance is being used as a loophole.

Phase 2+ adds A1 (relative paths), A5 (Claude PowerShell), and flips A2 to
blocking once its fire-log shows it clean.
"""

from .env_protect import check as a7_env
from .hidden_command import check as a10_hidden
from .log_redirect import check as a4_log
from .dangerous_bash import check as a6_bash
from .interpreter_write import check as a9_interpreter
from .personal_data import check as b3_personal
from .powershell_syntax import check as a8_powershell
from .shell_style import check_forward_slashes as a2_slashes
from .shell_style import check_dedicated_tools as a3_tools

_SHELL_RULES = [a10_hidden, a6_bash, a4_log, a7_env, a8_powershell,
                a9_interpreter, a3_tools, a2_slashes]
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
