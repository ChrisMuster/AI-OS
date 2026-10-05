  - Every tool call you make is judged before it runs, and anything not named here is
    refused. A refused call is not retried in another form.
  - Read files only with these shell commands, in these forms. Each line is a regular
    expression the whole command must match; several may be joined with `; ` and
    nothing else. PATH is a project-relative path, bare or in single quotes; QUOTED
    is a quoted search pattern that does not begin with `-`, and in double quotes
    holds no backtick or dollar sign:
$read_commands
  - Read a whole file with Get-Content exactly like this, both encodings set:
    `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; Get-Content -LiteralPath notes/example.md -Encoding UTF8`
    Windows PowerShell otherwise returns non-ASCII text wrongly, and a patch whose
    context lines hold that text would then not match the file. Any other Get-Content
    form is refused. `rg -n "^" PATH` also reads a whole file correctly.
  - Change files only with apply_patch, and only inside the edit paths.
  - Write a LOG.md entry with apply_patch. Take its timestamp from the listed
    `Get-Date` command immediately before you write the entry, never earlier.
  - No git commands other than the read forms listed above. Do not stage or commit
    anything.
  - You may run these verification commands, one per call, with no chaining. Each is
    a regular expression the whole command must match:
$verify_commands
    The targeted audit and the close-out verifier are not among them: the
    orchestrator runs both itself before every review and reports what they find, so
    do not try to run either.
  - No web search, no sub-agents, no MCP tools and no other tool.
