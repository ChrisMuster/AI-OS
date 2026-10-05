  - Fetch a real timestamp at the moment you write each LOG.md entry (the Biblio Tools
    `get_timestamp` or `append_log` tool), never before.
  - No git commands. Do not stage or commit anything.
  - You may run these verification commands, one per call, with no chaining. Each is
    a regular expression the whole command must match:
$verify_commands
    Anything else in the shell is refused.
