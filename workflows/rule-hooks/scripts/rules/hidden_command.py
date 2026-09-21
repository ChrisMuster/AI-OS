#!/usr/bin/env python3
"""Rule A10 - a command must not be hidden inside a wrapper option's value.

`env -S "find . -delete"` and `env --split-string="find . -delete"` are not
configuration. GNU env splits that option's value into words and executes them,
so the value IS the command. The shared reader skips a wrapper's option values
as inert configuration, which for this one option throws away the only part any
rule was going to read.

**The consequence is not a mis-read program, it is no program at all.** All four
spellings leave `effective_argv` returning an empty argv, and an empty argv is
read by every shell rule as "nothing here to judge". Measured against the tree
before this rule existed, `env -S` hid a command from A3, A4, A6 and A9 alike,
including the flat `rm -rf` verb block that has no adjudication to get wrong. It
is a bypass of the reader the rules sit on, rather than a hole in any one of
them, which is why it is refused here rather than patched into A6.

**Why this refuses the shape instead of reading it.** Two other fixes were
available: split the value the way GNU env does and splice the words back into
the argv, or hand the value to the carried-command path that already reads a
`sh -c` string. Both commit the project to reproducing env's own splitting -
quotes, backslash escapes, `\\c` and `\\t`, `${VAR}` substitution, a leading `#`
comment, options inside the string - and every point where our version differs
from the real one is a new bypass of exactly the shape being closed. That is the
same trade round 18 settled for `find -delete`: a question that cannot be
answered reliably from the argv is better refused than answered badly.

**The cost was measured before it was accepted, not assumed.** Across 26,985
indexed transcript messages spanning 2026-05-29 to 2026-09-20, every occurrence
of `env -S` or `env --split-string` dates from the single day the bypass was
reported, and all of them are the review that reported it or the documents it
produced. No script in the tree uses either spelling, and `env` is not used as a
command wrapper in real work at all. The fire-log was deliberately NOT consulted
for this: A6 never fired on `env -S`, that being the bug, so a zero there would
have proved nothing.

So a legitimate `env -S` is refused along with a destructive one, and the block
message carries the manual path. A real false block on ordinary work is the
signal to revisit this, exactly as it is for `find -delete`.
"""

from core import (
    Decision,
    format_block,
    hidden_command_wrappers,
    split_segments,
)

RULE_ID = "A10"


def check(ctx):
    if not ctx.command:
        return None
    # Every segment, because `split_segments` already appends the commands
    # carried by a `sh -c` string, an `eval` and a substitution as segments of
    # their own: `sh -c "env -S 'rm -rf build'"` arrives here as its own argv
    # without this rule needing to know how it got there.
    for argv in split_segments(ctx.command) or []:
        for wrapper, spelling in hidden_command_wrappers(argv):
            return Decision.block(RULE_ID, format_block(
                f"`{wrapper} {spelling}` hides the real command inside an "
                f"option value.",
                f"`{wrapper} {spelling}` does not configure anything: it takes "
                f"that string, splits it into words and runs them as the "
                f"command. The hook reads a wrapper's option values as "
                f"configuration and skips them, so the command inside is not "
                f"merely mis-read, it is not seen at all - which switches off "
                f"every rule at once, the destructive-command block included. "
                f"The shape is refused rather than parsed, because matching "
                f"how `{wrapper}` splits that string in every case would leave "
                f"a gap of the same kind wherever it did not match.",
                f"Write the command directly, without the wrapper: "
                f"`env -S \"find . -delete\"` is just `find . -delete`, and "
                f"the ordinary wrapper forms (`env find . -delete`, "
                f"`env VAR=1 cmd`, `env -u PATH cmd`) are all still read "
                f"normally.\n"
                f"Nothing in this project has ever needed `{wrapper} "
                f"{spelling}`. If you genuinely do, it is your call rather "
                f"than the hook's: run it yourself in the terminal, or tell "
                f"Biblio to lift this block.",
            ))
    return None
