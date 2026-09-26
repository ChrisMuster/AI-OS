# Review Orchestration - Scripts

**Last modified:** 2026-09-26

## Purpose
Holds the review-orchestration entry point, the brief checker, and the first parts of the engine. `brief.py` reads a run brief, classifies every line the way a CommonMark renderer would (so a heading inside a comment or a fenced block does not count), splits the brief into its seven sections and applies each section's rules, reporting every problem rather than the first. It never runs a command the brief contains. `limits.py` reads each provider's usage and decides whether a run may start its next step; `runrecord.py` creates a run's record folder and keeps its state file; `settings.py` reads the settings file. `run.py` is the workflow's entry point; later chunks add its run modes.

## Contents
- run.py - `workflows/review-orchestration/scripts/run.py` - The entry point. One mode so far: `--check-brief <brief>` calls the checker and returns its result unchanged (the same stdout, exit code and LOG.md entries as `brief.py --check`). Takes `--dry-run`.
- brief.py - `workflows/review-orchestration/scripts/brief.py` - The checker: `check_brief` (path in, output lines and exit code out), `check_text`, `scan`, `check_command`, `check_edit_path`, `check_source_line`, and the command line (`--check <brief>`, `--dry-run`).
- limits.py - `workflows/review-orchestration/scripts/limits.py` - Usage readings (plan section 10.10): `read_codex` and `classify_codex` (the `account/rateLimits/read` request, no model call), `RunUsage` (holds a failed Codex reading for the rest of its run), `ClaudeUsage` (built up from a session's rate-limit events), `check_ceiling` (whether the next step may start), and `codex_turn_error` / `claude_message_error`, which turn a limit hit mid-session into a `UsageLimitError`. Library only; no command line.
- runrecord.py - `workflows/review-orchestration/scripts/runrecord.py` - `new_run_id`, `create_run` (makes `runs/<run-id>/` with its first `state.json`; takes `dry_run`), `write_state` (atomic) and `read_state`. Library only.
- settings.py - `workflows/review-orchestration/scripts/settings.py` - `load_settings`: reads `config/settings.json`, fills in defaults, and raises `SettingsError` on a value it may not hold. Library only.

## Inputs
- A project-relative path to a brief, from the command line.
- The project tree, for resolving the Source section's paths.
- For `limits.py`: an initialised Codex client, and the Claude SDK's rate-limit events and assistant messages, both passed in by the caller.
- For `settings.py`: `workflows/review-orchestration/config/settings.json` [[workflows/review-orchestration/config/CONTEXT]].

## Outputs
- `PASS: <path>` or one `FAIL: <heading>: <reason>` line per problem on stdout. `Brief` lines (the file itself) come first, then `Headings` lines, then section lines in document order. Exit code 0 on PASS, 1 on any FAIL, 2 on a usage error.
- `started`, then `completed` (PASS) or `failed` (refused, or an unexpected error), appended to `workflows/review-orchestration/LOG.md`, with a generic note that never includes the brief's path. `--dry-run` prints each entry to stderr as a `[DRY RUN]` line instead, so stdout still carries only the PASS or FAIL lines.
- `runrecord.py` writes `workflows/review-orchestration/runs/<run-id>/state.json` (and a `state.json.tmp` beside it for the moment of each write). `limits.py` and `settings.py` write nothing.

## Steps
1. Read the brief: refuse an absolute path, one resolving outside the project, a folder, a missing or unreadable file, or invalid UTF-8.
2. Classify each line (blank, text, fence, heading) with HTML comments removed.
3. Check the H2 headings: the seven required ones, each once, spelled as shown, in order.
4. Check each recognised section against its own rules.
5. Print the result and append LOG.md with a completion or failure entry.

## Choices where the specification is silent
The specification leaves these to the builder. Each is covered by a test in `workflows/review-orchestration/tests/` [[workflows/review-orchestration/tests/CONTEXT]]: the brief checker's in `test_brief.py`, the usage readings' and settings' in `test_limits.py`, and the run record's in `test_runrecord.py`.

**Reading the file**
- The path is resolved against the project root, not the working directory, so the result does not depend on where the command is run. Backslashes in the argument are accepted as separators; a path that resolves outside the project is refused, and so is a folder.
- A UTF-8 byte-order mark is refused and the rest of the brief is still checked. Invalid UTF-8 is refused and checking stops, since nothing after it can be read reliably.
- CRLF and a lone CR are accepted as line endings.

**Headings**
- Headings follow CommonMark: up to three leading spaces, and `##` must be followed by a space, so `##Goal` is text. The title is compared after trimming, but a closing `##` is kept as part of it, so `## Goal ##` is an unexpected heading.
- An underlined (setext) level-2 heading is refused anywhere: headings must be written with `## `. A dash line under a list item or under indented code is not a heading, as in CommonMark.
- A level-1 heading is allowed before the first section and refused after it.
- Content under an unexpected or repeated heading is not checked, and a missing section gets only its `Headings` line, not an "empty" line as well.
- Each heading that appears after a later one is reported as out of order, naming the latest heading seen so far. Its section is still checked.
- A heading that differs from a required one only in case is reported with the correct spelling.

**Content**
- Deeper headings (`###` and below) are not content: a section holding only them is empty, and they are skipped when reading bullets.
- The `...` and angle-bracket placeholders may be bulleted with `-`, `*` or `+`. The two placeholder words the specification names (the upper-case to-do and to-be-decided markers) are matched with case and as a prefix, so a plural of either is a placeholder and the lower-case word is text. A placeholder line is ignored wherever it appears; it is not refused when the section has other content.
- A fenced block is reported once, at its opening line, and its lines are not content.
- Section reasons carry their line number.

**Bullets**
- Only `- ` starts a bullet; `*` and `+` bullets are refused. Leading indentation is allowed. A line that is not a bullet, including a wrapped continuation line, is refused.
- Open questions: `None` must be written with that case and alone. The question and answer split at the first `| Answer:`; an empty question is refused.
- Edit paths: the path and command split at the first `| Measure:` (case-sensitive). `.git`, `.env` and duplicates are compared the way Windows compares names: without case, ignoring trailing dots and trailing spaces. `docs` and `docs/` are duplicates; a folder and a file beneath it are not. An empty or `.` component and a backslash are refused.
- Source: a name ending `.md` is matched without case. Brackets, quotes and trailing punctuation are stripped from a word outside backticks before it is tested, so `plan.md.` and `(docs/plan.md)` are caught. A backtick-quoted path with a backslash, a leading `/` or `~`, a drive letter or a `..` component is refused; `.` and empty components are ignored. Existence is checked with exact spelling and case, so a brief that passes here passes on a case-sensitive file system too. A trailing `/` requires a folder. An unbalanced backtick is refused, and the text after it is treated as unquoted.

**One command**
- Command substitution is refused even inside single quotes.
- An unquoted operator is reported as the whole run of operator characters, so `2>&1` reports `>&`.
- A wrapper is looked for as the program, `argv[0]`, and nowhere else, so a shell or launcher name later in the arguments, as in `rg bash -c README.md`, is data. A launcher that runs another program (`env`, `sudo`, `doas`, `xargs`, `nohup`, `nice`, `timeout`, `time`, `command`, `exec`, `stdbuf`, `runas`, `start`) is refused outright as the program, with `` `<launcher>` launches another program; name that program directly ``. Reading past it would mean modelling each launcher's options (`timeout 10`, `sudo -u x`), and `env -S` / `--split-string` hide a whole command in one argument; a brief can always name the program itself. Chosen in code review round R2 over Codex's suggested per-launcher parsing, which the round's R1-3 repair had already shown to be a source of both false refusals and bypasses.
- Only a shell's own options count, read up to its script operand (or `--`), so `bash scripts/check.sh -c` passes: that `-c` belongs to the script. Options that take a value (`-o`, `+o`, `-O`, `+O`, `--rcfile`, `--init-file`) skip it, so `bash -o pipefail -c ...` is still caught. `-c` combined with other single-letter flags (`-lc`, `-ec`) and `--command` count; for fish, `-C` and `--init-command` too, which run a string there and mean noclobber elsewhere.
- For `cmd`, any argument beginning `/c` or `/k`. For PowerShell, parameters are matched as PowerShell matches them, by prefix, with `-`, `--` or `/`: any prefix of `-Command`, `-EncodedCommand` or `-CommandWithArgs`, plus the `-ec` and `-cwa` aliases, read up to `-File`, after which the arguments are the script's. Windows PowerShell (`powershell`, not `pwsh`) treats a bare argument as a command string, so a bare argument with no `-File` before it is refused.
- The wrapper check reads the command twice: once with POSIX quoting, and once with quotes honoured but backslashes kept, because POSIX reading turns an unquoted `C:\Git\bin\bash.exe` into a name that is no longer `bash`. Either reading finding a wrapper refuses the command; quoted text is data in both.

**Command line**
- A usage error exits 2 (the argparse default), distinct from a refused brief.
- A refused brief logs `failed`; an unexpected error logs `failed` and is then raised. A LOG.md that cannot be written fails the check: the audit trail is part of it, so a brief that would pass prints `FAIL: Brief: could not append to the workflow LOG.md (<reason>)` instead of PASS and exits 1, and a refused brief gets that line first.

**Usage readings (`limits.py`)**
- A Codex JSON-RPC error, a response that is not a JSON object, and a response that fails validation are each an `unavailable` reading carrying the error text, as the plan says; a validation error is kept whole, flattened to one line, since its first line names only the model and the failing field is on the lines after it. A closed transport (`TransportClosedError`) is not: it is raised, because it is a provider failure rather than an answer about usage.
- A negative Codex result holds for the rest of its run, as the plan says, through `RunUsage`: one object per run, every Codex reading passed through it. After the first `unavailable` reading, a later `reported` one comes back `unavailable`, naming the first failure and keeping its own figures for the report, so the ceiling is not applied. A later `limit-reported` reading is passed through unchanged and still stops the run. A new run starts from a new object.
- Where two windows report the same percentage, the one with the later reset is taken, since waiting for the earlier reset would not free the other window. An unknown reset counts as earlier than a known one.
- When Codex says usage is not allowed and windows are present, their highest reading is still recorded beside the stop, so the report shows the numbers the plan asks for.
- Claude's percentages come from each event's raw `unifiedWindows` map first and its top-level `utilization` only as a fallback: in the event recorded by the B1 probe the top-level field was empty and the real figures were only in the map. Windows accumulate across events, and the latest event's `status` is the current status, so a later `allowed` clears an earlier `rejected`. A `rejected` status stops the run whatever the percentages say; its reset time is the event's own, or the highest window's where the event gives none.
- Paid overage stops the run (the user's rule, now in plan section 10.10 item 3): an event with raw `isUsingOverage` true, or an event for the `overage` window, makes Claude's reading `limit-reported` with the reason `using paid overage`. The `overage` window is never counted as an included window, so it never sets the percentage or the reset time; the reset reported is the fullest included window's, which is when the run can resume. The latest event decides, so overage that has ended no longer stops a run, and overage that is available but unused never did. Overage is checked before a `rejected` status, so an event that is both (overage itself refused) still reports `using paid overage` with the included window's reset, since that is when the run can resume. Where the fullest included window carries no reset of its own, the event's top-level reset is used only if the event names that same window; a different window's reset would not free it, so the reset is reported as unknown instead. A stop with an unknown reset does not resume on its own: the user checks whether the allowance has reset and says when to start again (the user's ruling, 2026-09-26).
- Mid-session, Codex's `usageLimitExceeded` turn error and Claude's `rate_limit` assistant-message error are usage limits. Codex's `rateLimitExceeded` is not: it is short-term request throttling, not the plan allowance, and is left to the caller as an ordinary error.
- `check_ceiling` checks every `limit-reported` reading before any percentage, so a provider that reports its limit reached stops the run even when another provider is above the ceiling too. A reading exactly at the ceiling stops.

**Run record (`runrecord.py`)**
- A run ID is `YYYYMMDD-HHMMSS-xxxx`: the local start time, so IDs sort by start, plus four random hex digits, so two runs started in the same second do not collide. Any other form is refused, which also rules out a path separator or `..` in an ID.
- Creating a run whose folder exists is refused. A run record is never reused or overwritten.
- The first `state.json` holds `status: running`, `step: created`, no pause, and empty `stop_reasons` and `rounds` lists: the fields plan section 10.7 names, present from the start.

**Settings (`settings.py`)**
- A missing `usage_ceiling_percent` takes the default, 80. A present one outside `0 < value <= 100`, or not a number (a JSON `true` included, although Python counts `bool` as `int`), is refused rather than replaced by the default.

## Dependencies
- Python 3.13+ standard library (`argparse`, `json`, `os`, `re`, `secrets`, `shlex`, `sys`, `datetime`, `pathlib`, `dataclasses`).
- `openai-codex` and `claude-agent-sdk`, from `workflows/review-orchestration/requirements.txt` [[workflows/review-orchestration/CONTEXT]], for `limits.py`'s Codex request and its error classes. They are imported inside the functions that need them, so the other modules, and `limits.py`'s pure parts, load without them.
- `workflows/review-orchestration/tests/` [[workflows/review-orchestration/tests/CONTEXT]] - the suite that proves each rule and each choice above.
- `workflows/review-orchestration/prompts/brief.md` [[workflows/review-orchestration/prompts/brief]] [[workflows/review-orchestration/prompts/CONTEXT]] - the template, whose comments restate these rules.

## Known Issues
- The one-command rule refuses the listed shell wrappers only. An interpreter given code as a string (`python -c`, `node -e`) passes, because it is one command with quoted arguments. The checker never runs anything; whether an acceptance check does what it claims is the intent check's job in a later sub-stage.
- The launcher list is fixed. A shell wrapper behind a launcher not on it (for example `parallel sh -c ...`) is read as that launcher's data and passes; adding a launcher is a one-line change, which the every-launcher test then covers.
- A valid command that happens to go through a launcher, such as `env rg bash -c README.md`, is refused. That is deliberate: the refusal names the launcher, and dropping it gives an equivalent command that passes.
- The line classifier models the CommonMark cases a brief uses (ATX and setext headings, fences, HTML comments, list items, quotes, indented code). It is not a full CommonMark parser: an HTML block other than a comment, or a lazy continuation line inside a quote, is read as plain text.
- The Windows name folding for `.git`, `.env` and duplicates covers case, trailing dots and trailing spaces. It does not cover short 8.3 names or alternate data streams.
- `limits.py` covers reading a limit, the ceiling and recognising a mid-session limit. Stopping cleanly, resuming, `--wait` and the token-per-step price alarm (plan section 10.10, items 4 to 6) belong to the loop and come with it.
- Claude reports no usage until its session emits a rate-limit event, so at a run's preflight Claude's reading is always `unavailable` and the ceiling cannot be applied to Claude before its first step.
- Paid overage is detected for Claude only. Codex's response has no equivalent field this chunk reads: its `ordinaryUsageAllowed` false already stops a run, and whether Codex credits count as overage is not established.

## Revision History
- 2026-09-24 - Initial creation with `run.py` (`--check-brief`) and `brief.py`, the brief checker, and the record of every choice made where the specification is silent.
- 2026-09-24 - Repairs from Codex's code review round R1. The wrapper check now reads the program and, after a launcher, later arguments, and only a shell's own options up to its script, so a shell name used as data or a `-c` passed to a script no longer refuses a valid command (R1-3). A LOG.md that cannot be written now fails the check instead of warning (R1-4). The lone-CR line-ending choice gained its test (R1-5).
- 2026-09-25 - Repairs from code review round R2. The wrapper check examines only the program, `argv[0]`, and refuses a launcher there outright instead of reading every later argument, so a shell name after a launcher's program is no longer mistaken for one (R2-1) and `env -S` can no longer hide a shell wrapper (R2-2). Recorded choice and Known Issues updated.
- 2026-09-25 - Stage A2, chunk (a): added `limits.py` (usage readings, ceiling, mid-session limit recognition), `runrecord.py` (run IDs, the run folder and its atomic `state.json`) and `settings.py` (the settings reader), with the choices made where the plan is silent.
- 2026-09-25 - Repairs from code review round R1 of chunk (a): `RunUsage` holds a failed Codex reading for the rest of its run, so the ceiling is not applied to Codex after it (R1-1); a validation failure keeps pydantic's whole error rather than its first line (R1-2). Then, at the user's ruling, Claude's paid overage stops a run, with the included window's reset time.
- 2026-09-25 - Repair from code review round R2 of chunk (a): `ClaudeUsage.reading` checks paid overage before a `rejected` status, so a rejected overage event reports `using paid overage` and the included window's reset rather than the overage window's (R2-1). Recorded choice updated.
- 2026-09-26 - Repair from code review round R3 of chunk (a): in the overage branch, the event's own reset stands in for the fullest included window's only when the event names that window; otherwise the reset is reported as unknown rather than borrowed from an emptier window (R3-1). Recorded choice updated, with the user's ruling that an unknown reset means he restarts the run.
