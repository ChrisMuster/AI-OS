  - Run checks only with the check helper's `run_check` tool. It runs each command in
    an offline container over a clean copy of the project, with no personal file and
    no network, and replies with the exit code and the end of the output. The shell
    is not available.
  - You may run these verification commands through `run_check`, one per call, with
    no chaining. Each is a regular expression the whole command must match:
$verify_commands
    Anything else is refused.
  - Write a LOG.md entry with the edit tools. Take its timestamp from the check
    helper's `get_timestamp` tool immediately before you write the entry, never
    earlier.
  - A Grep call carrying the `glob` option is refused whatever the pattern, so leave
    that option out and narrow a search with `path` (a folder or a file) or `type`
    instead.
  - No git commands. Do not stage or commit anything. The Biblio Tools tools are not
    available.
