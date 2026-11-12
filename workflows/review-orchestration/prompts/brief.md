<!--
Run brief for the review-orchestration workflow. Copy this file to
workflows/review-orchestration/runs/<run-id>/brief.md, fill in every section, then
check it with:
  python workflows/review-orchestration/scripts/start.py --check-brief <path>
Keep the seven headings exactly as written and in this order. Comments are ignored.
A line that is only "...", only an angle-bracket placeholder, or that begins TODO or
TBD counts as empty. No fenced code blocks anywhere in a section.
Size the work so its build is expected to fit within one five-hour usage window where
the work allows; split a larger item into chunks, each its own brief and run ending
tested and committable. The plan report's size estimate is the check; the checker is
not.
-->

## Goal
<!-- What done looks like, written as statements a reader can check. -->

## Constraints
<!-- Musts and must-nots the run obeys. -->

## Out of scope
<!-- Named things the run must not do. Write None if nothing is excluded. A path
written in backticks here is checked by the script at both intent checks, the one on
the merged plan and the one on the final diff, provided it names a file or folder that
exists when the run starts; one that names nothing is listed in the plan report as not
checked. The rest of the section is read by the intent checker only. -->

## Acceptance checks
<!-- One bullet per check, written "- <command>". Each is a single command that
exits 0 when the work is done: no shell operators outside quotes, no command
substitution, no shell wrapper such as "bash -c", and no launcher such as "env" or
"sudo" in front of the program. Each must also be on both builders' verification lists
(config/verify-commands.txt and config/codex-verify-commands.txt), so whichever AI
builds can run it: put a one-off check such as a test count in a test file. -->

## Edit paths
<!-- One bullet per path a builder may change, written
"- <path> | Measure: <command>", where the command is how the path was measured
(a grep, a checker's output). Project-relative, forward slashes, no wildcards and no
"..". A path ending in "/" is a folder and covers everything beneath it. Nothing under
.git/ and no .env file. The path need not exist yet. -->

## Open questions
<!-- Questions for the user. Leave blank, write None, or answer each in place as
"- <question> | Answer: <answer>". A run does not start with an unanswered one. -->

## Source
<!-- One bullet per source: the backlog item, plan sections and memory files the
brief was drawn from. Put every file or folder in backticks, relative to the project
root; each must exist. A path written outside backticks is refused. -->
