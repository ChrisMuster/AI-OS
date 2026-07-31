# Rule Hooks

**Last modified:** 2026-07-31

## Purpose
Deterministic rule enforcement: moves Book Dragon's load-bearing always/never rules out of prose and into hooks that fire when an AI acts, so a violation is blocked at the moment it would happen rather than relied on by discipline. Phase 1 wires the two daily-use AIs (Claude Code and Codex) plus an AI-agnostic git pre-commit net that protects every AI and manual commits. The benchmark principle: "hooks guarantee execution; prompts do not."

## Contents
- scripts/ - `workflows/rule-hooks/scripts/` [[workflows/rule-hooks/scripts/CONTEXT]] - The `run.py` entry point, the shared `core.py` contract, and the `rules/` and `adapters/` packages.
- tests/ - `workflows/rule-hooks/tests/` [[workflows/rule-hooks/tests/CONTEXT]] - Synthetic-event tests for every rule and both adapters, plus subprocess exit-code, config wiring, and cwd-drift contract tests.
- git-hooks/ - `workflows/rule-hooks/git-hooks/` [[workflows/rule-hooks/git-hooks/CONTEXT]] - The universal git pre-commit shim, activated via `core.hooksPath`.
- fire-log.jsonl (gitignored) - runtime, machine-specific append-only record of trial-rule fires and all blocks; never committed.
- archived/ - `workflows/rule-hooks/archived/` [[workflows/rule-hooks/archived/CONTEXT]] - Holds the gitignored HOOKS-PLAN.md design document from the build; local-only.

## Inputs
- A firing tool event on stdin (PreToolUse JSON) from an AI's native hook, selected by `--ai <id>`.
- The project root, resolved at runtime from the AI's channel (`CLAUDE_PROJECT_DIR` for Claude; stdin `cwd` for Codex), falling back to `git rev-parse` then script-relative, adjudicated by a read-only AGENTS.md+.git signature test on disagreement. Codex hook launch commands also resolve the git root before starting `run.py`, because they have no project-root environment variable and may fire from a drifted session cwd.
- For B3 (personal data): the personal-data-guard workflow, reused as the single detection source.

## Outputs
- A block decision rendered in the firing AI's contract (Claude: exit 2 + stderr; Codex: `hookSpecificOutput.permissionDecision="deny"` JSON + exit 0), carrying the three-part block-and-explain message (Blocked / Why / To do it manually).
- The git pre-commit gate blocks a commit that would add personal data to tracked files (exit 1) and prints the same block-and-explain; it then prints a warn-not-block doc-sync CONTEXT/LOG drift advisory (exit 0, commit proceeds).
- The SessionStart reminder text (`--reinject`) re-injecting the permission gate and self-correction rules.
- Trial-rule warns and all blocks appended to the gitignored fire-log.

## Steps
1. An AI's PreToolUse hook launches `workflows/rule-hooks/scripts/run.py --ai <id>` with the event on stdin, anchored through the AI's project-root mechanism (Claude: `$CLAUDE_PROJECT_DIR`; Codex: `git rev-parse --show-toplevel` in `.codex/config.toml`).
2. `run.py` resolves the project root and routes the event through that AI's adapter into a normalised `Context`.
3. The dispatcher runs the rules for the event category (shell / write); the first block wins, warns are collected.
4. On a block, the adapter emits the AI's block contract; on a warn or allow, the action proceeds (warns are fire-logged).
5. Any hook or rule failure degrades to allow + fire-log, so a tooling bug never freezes the agent.
6. Separately, `--precommit` runs the commit-time gates (the personal-data hard block, then the doc-sync drift advisory), and `--reinject` prints the SessionStart reminder.
7. Append LOG.md with a completion or failure entry. (Per-fire activity is recorded in the gitignored fire-log, never LOG.md.)

## Dependencies
- `workflows/personal-data-guard/` [[workflows/personal-data-guard/CONTEXT]] - Reused as the B3 detection source by both the per-write rule and the git pre-commit gate (one detection source, two triggers). If its marker functions change, the B3 rule follows.
- `workflows/doc-sync-guard/` [[workflows/doc-sync-guard/CONTEXT]] - Reused (in `--staged` mode) as the CONTEXT/LOG drift detection source for the pre-commit warn-not-block advisory. Loaded lazily by the `--precommit` gate; a guard crash is swallowed so it never disrupts a commit.
- `AGENTS.md` [[AGENTS]] (root) - The source of the rules being enforced (A4, A6, A7, B3, and the trial A2/A3) and the project-root signature marker.
- `.claude/settings.json` and `.codex/config.toml` - Per-AI hook wiring that invokes `run.py` through project-root-anchored commands.
- `workflows/settings-check/` [[workflows/settings-check/CONTEXT]] - Validates the new hook commands are allowlisted so they never prompt.
- `workflows/biblio-tools/scripts/verify.py` [[workflows/biblio-tools/scripts/CONTEXT]] - Checks the git pre-commit hook is active.
- Python 3.9+ standard library only; the `git` CLI for root resolution and the personal-data gate.

## Known Issues
- The Codex live smoke test initially failed on 2026-07-01 because the adapter used the unsupported `continue:false` PreToolUse output and did not read apply_patch bodies from `tool_input.command`. After correction, Codex live blocking is confirmed for A7 on shell redirect, apply_patch, and cp writes to `.env.hooktest`.
- The Claude PreToolUse live block is confirmed live: both A7 paths (the Edit/Write file path and the Bash `> .env` redirect) and the git pre-commit block. It requires the `.claude/settings.json` hooks applied and a new session started, since Claude loads hooks at startup.
- **Shell-write coverage boundary (deliberate, not a gap).** A4/A7 detect shell writes to a protected file via output redirections (`>`, `>>`, `>|`, `&>`, `&>>`), `cp`/`mv` destinations (including the `-t` / `--target-directory` forms), `tee`, and `sed -i` - all through the shared `core.shell_write_targets`. This is intentionally NOT exhaustive: arbitrary-code interpreters (`python -c`, `node -e`, `perl -e`, `bash -c`) write through code that cannot be statically parsed, and rarer verbs (`dd of=`, `truncate`, `rsync`, `install`, `ed`/`ex`) are out of scope by design. The threat model is a cooperative AI reaching for the wrong tool, not an adversary - an AI with a shell cannot be fully contained at the verb-parsing layer, and chasing completeness only breeds false-positive fatigue on legitimate tooling. The fully-covered Edit/Write tool path, `.env` being gitignored, and the git pre-commit gate are the backstop for anything the shell parser does not catch.
- A2 and A3 are trial rules: they fire to the fire-log only and never block in Phase 1. A2 scans the raw command (POSIX shlex would consume backslashes), so it is intentionally coarse pending fire-log tuning.
- Phase 1 ships Claude + Codex only. Cursor, Gemini/Antigravity, Windsurf/Devin, Copilot, Cline, and OpenCode are Phase 2+, covered meanwhile by the git pre-commit + audit + prose fallback. A1 and A5 are also Phase 2.

## Revision History
- 2026-06-30 - Initial creation. Phase 1 of the deterministic rule-enforcement hooks: `run.py` evaluator with per-AI adapters (Claude, Codex), blocking rules A7/.env, A4/LOG-redirect, A6/dangerous-bash, B3/personal-data (reusing personal-data-guard), trial rules A2/A3 log-only, a universal git pre-commit gate, SessionStart re-injection, runtime root resolution, a gitignored fire-log, and a 33-test synthetic-event suite.
- 2026-06-30 - Closed an A7 gap found during the Claude live test: A7 checked only the Edit/Write file path, so a shell redirect (`> .env`) bypassed it. Added a shell-redirect branch to A7 (mirroring A4's `redirect_targets` parsing) and registered A7 in the shell rule set. Both A7 paths now block live on Claude Code. +4 tests (37 total).
- 2026-06-30 - Broadened the shell write detection beyond redirects: new shared `core.shell_write_targets` helper also catches cp/mv destinations and tee file operands. A4 and A7 both use it (so LOG.md and .env are protected against `cp`/`mv`/`tee`, not just `>`/`>>`), and A4's block wording generalised from "redirecting" to "writing from the shell". Shared rule layer means Codex is covered too (added a Codex cp-into-.env subprocess test). All verbs confirmed blocking live on Claude Code. +7 tests (44 total).
- 2026-06-30 - Bounded hardening of the shell-write detection (deliberately final, not open-ended): added the `>|` / `&>` / `&>>` redirect operators, the `cp -t` / `mv --target-directory` forms, and `sed -i` to `core.shell_write_targets`. Documented the coverage boundary in Known Issues as a deliberate threat-model decision (arbitrary-code interpreters and rare verbs out of scope by design; tool path + gitignore + git pre-commit are the backstop). The full "enumerate every vector" idea was considered and rejected as over-engineering. +7 tests (51 total); all forms confirmed blocking live on Claude Code.
- 2026-07-01 - Corrected the Codex hook adapter after live verification failed: blocks now use the supported `hookSpecificOutput.permissionDecision="deny"` contract, and apply_patch payloads are parsed from `tool_input.command`. Added regression tests for both failure modes and confirmed the Codex live block for shell redirect, apply_patch, and cp writes to `.env.hooktest`.
- 2026-07-01 - Phase 1 close-out: committed the build (c4b690f58), corrected two CONTEXT staleness gaps (scripts/ and rules/ shell-write descriptions), and archived HOOKS-PLAN.md into the new `archived/` subdirectory.
- 2026-07-07 - The git pre-commit gate now runs a second, warn-not-block step after the personal-data block: a doc-sync CONTEXT/LOG drift advisory (doc-sync-guard build part 4). It reuses the doc-sync guard (`--staged`) as detection, prints a loud advisory naming each drift directory, and always allows the commit (close-out is the deterministic gate). Added `TestPrecommitDocSync` (53 -> 57 tests); live-confirmed on a staged drift.
- 2026-07-25 - Hardened the Claude hook wiring against a drifted shell working directory. The `.claude/settings.json` hook commands invoked `run.py` by a relative path, so once the Bash tool's working directory moved off the project root the OS could not find the script and every subsequent Bash and Edit call was blocked (the PreToolUse hook failing to launch). Changed the three Claude hook commands (both PreToolUse plus the SessionStart `--reinject`) to anchor the script to `$CLAUDE_PROJECT_DIR` - which Claude Code sets in the hook environment and the Claude adapter already reads for root resolution - and pinned `shell: bash` so the expansion is deterministic. The session-search archive hooks in the same file got the identical treatment. `settings-check` was taught to resolve the `$CLAUDE_PROJECT_DIR/` prefix so it still validates script existence. Added `TestCwdIndependence` (57 -> 60 tests). Codex (`.codex/config.toml`) was left unchanged: it has no equivalent variable and that file is tracked, so an absolute path there would breach the no-absolute-paths rule; its own fix is deferred to a Codex session. Not yet live-confirmed - Claude loads hooks at startup, so the change takes effect on the next session; verified this session via the from-a-subdirectory test and a green settings-check.
- 2026-07-31 - Hardened Codex hook wiring against cwd drift. `.codex/config.toml` now resolves the git project root before execing `run.py` for both PreToolUse hooks, `run.py --reinject` for SessionStart, and the session-search indexer for Stop. Added Codex config and from-subdirectory regression coverage in `TestCwdIndependence` (60 -> 62 tests).
