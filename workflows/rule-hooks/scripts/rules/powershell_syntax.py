#!/usr/bin/env python3
"""Rule A8 - a PowerShell here-string must not be passed to the Bash tool.

Blocks a Bash command carrying a PowerShell here-string (`@'...'@` or
`@"..."@`). It fails at the shell parser before the real program runs, so the
cost is a wasted call and a confusing error rather than a corrupted file - but
it recurs, and that is the reason this is a hook rather than another note.

This rule exists because the prose version of it does not work. The convention
is recorded in a memory, and a session handover named this exact fault, in this
exact context, as "an easy repeat"; it then repeated hours later in the session
that read the handover. The pull is structural rather than careless: the
PowerShell tool's own instructions demonstrate `git commit -m @'...'@` for
passing multi-line strings such as commit messages, so at the moment the mistake
is made - composing a commit message on Windows - the wrong template carries the
strongest and most task-specific cue available. A rule that competes with a
worked example at the point of decision has to be enforced, not remembered.

Detection keys on the *opening delimiter at the end of a line*, which is what
PowerShell's here-string syntax requires (the content must start on the next
line) and what ordinary Bash never produces. `echo @'hi'` is a legitimate, if
unusual, Bash argument on one line and does not fire. Heredoc bodies are
stripped before scanning, so text quoted *into* a command is never matched -
including documentation about this syntax, which exists in this project.

Unlike A2/A3 this ships blocking rather than as a log-only trial. A trial would
record the fault and let it through, which is the behaviour being corrected: the
point of the rule is that the call never runs. The false-positive surface is
small and bounded by the two narrowings above, and a block ships with the exact
correct command, so a wrong fire costs one retry.

Scope is the here-string alone. Other PowerShell-isms (backtick line
continuation, `$env:VAR`, `Test-Path`) are deliberately out of scope: they are
not the recurring fault, and each would need its own false-positive analysis.
"""

import re

from core import Decision, format_block, strip_heredoc_bodies

RULE_ID = "A8"

# A PowerShell here-string OPENS with @' or @" as the last thing on the line.
_OPENER_RE = re.compile(r"(?:^|\s)@(['\"])[ \t]*$", re.MULTILINE)

# The heredoc-body stripper moved to core on 2026-09-02, when A3 needed the
# same behaviour. One copy, per the rule this project keeps relearning: a
# second copy of a rule is the thing that drifts. Behaviour here is unchanged.
_strip_heredoc_bodies = strip_heredoc_bodies


def check(ctx):
    if not ctx.command:
        return None
    match = _OPENER_RE.search(_strip_heredoc_bodies(ctx.command))
    if not match:
        return None
    quote = match.group(1)
    return Decision.block(RULE_ID, format_block(
        f"A PowerShell here-string (`@{quote}...{quote}@`) was passed to the "
        f"Bash tool.",
        "The Bash tool runs POSIX sh, where `@' ... '@` is not here-string "
        "syntax. The shell parses the following lines as separate commands, so "
        "the call fails before the real program runs. This is a recurring "
        "mistake rather than a one-off, which is why it is blocked here instead "
        "of being written down again.",
        "Use a POSIX heredoc instead:\n"
        "  git commit -F - <<'EOF'\n"
        "  Your message here.\n"
        "  EOF\n"
        "PowerShell here-strings belong only in an actual PowerShell tool call.",
    ))
