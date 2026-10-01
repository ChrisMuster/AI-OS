#!/usr/bin/env python3
"""
loop.py - the live build-and-review loop (plan sections 10.4, 10.5, 10.7 and 10.10).

A run starts from a brief that passes the check. After a read-only preflight it
creates its record, ``runs/<run-id>/``, and then takes steps in this order:

  build        the builder's first pass. An empty diff ends the run
               ``no-change-made``.
  review-R<n>  the script runs its mechanical checks, records the round, and a fresh
               reviewer session reviews the diff. Its findings, and the checks', are
               labelled ``R<n>-<k>`` and written to the review packet.
  fix-R<n>     the builder's session is resumed with the open findings and answers
               each with an action.

After every step the stop-reason rules of ``stopreasons.py`` decide whether the run
goes on. A review round with no findings at all ends the run ``reviewed-clean``: the
intent check that would let it end ``clean`` is a later sub-stage (plan 10.6).

Before every step both providers' usage is read. A provider at or above the ceiling,
reporting its limit, or using paid overage stops the run ``usage-limit`` before the
step starts, and before a step that calls Codex the Codex credit snapshot must be
exactly what it was at preflight. A limit hit mid-step is caught as its own class.
``state.json`` is written after every completed step, so a stop loses at most the step
in progress, and ``--resume`` continues from the last completed step; a builder cut
off mid-step is resumed in the same session.

Nothing here stages, commits, pushes or changes branch (plan section 3). Every entry
point to an SDK or a subprocess that talks to one is a replaceable dependency
(``Deps``), which is how the tests drive a whole run with no model call.
"""

import asyncio
import hashlib
import importlib.metadata
import json
import re
import shutil
import statistics
import string
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import approver as approver_mod
import brief as brief_mod
import checks as checks_mod
import limits
import packet as packet_mod
import providers
import runrecord
import stopreasons as sr
import worktree
from settings import load_settings

PROJECT_ROOT = brief_mod.PROJECT_ROOT
LOG_PATH = brief_mod.LOG_PATH
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
ITEM = re.compile(r"^[a-z0-9][a-z0-9_]*$")
STEP = re.compile(r"^(build|review-R[1-9][0-9]*|fix-R[1-9][0-9]*)$")
SDK_PACKAGES = ("claude-agent-sdk", "openai-codex")
DIFF_LIMIT = 300_000
WAIT_MARGIN_SECONDS = 60

# Plan section 10.5: what the report recommends for each stop reason.
RECOMMEND = {
    sr.CLEAN: "Commit.",
    sr.REVIEWED_CLEAN: "Read the diff against the brief, since nothing has checked it "
                       "for intent, then commit if it is right.",
    sr.INTENT_FAILED: "Read the intent report, then re-plan, amend the brief, or accept "
                      "knowingly.",
    sr.NO_CHANGE_MADE: "Check that the brief still asks for a change, then start a new "
                       "run from a corrected brief.",
    sr.MAX_ROUNDS: "Approve more rounds: run.py --resume <run-id> --rounds N.",
    sr.REOPENED: "Re-plan with that finding as input.",
    sr.OUT_OF_SCOPE: "Widen the edit paths in the brief, or finish by hand.",
    sr.DISAGREEMENT: "Rule on the disputed finding, as under the triage rule.",
    sr.USAGE_LIMIT: "Resume after the reset time given, or let --wait do it.",
    sr.STOPPED_BY_USER: "Nothing; the run record is kept.",
    sr.ERROR: "Fix the cause, then resume: run.py --resume <run-id>.",
}


class RunRefused(Exception):
    """The run cannot start (or resume). Nothing is created."""


# ------------------------------------------------------------------ dependencies


def _read_codex_live(root):
    """One Codex usage reading on a fresh, initialised client. No model call."""
    from openai_codex import Codex, CodexConfig
    with Codex(CodexConfig(cwd=str(root))) as codex:
        return limits.read_codex(codex._client)


def capture_problems(root, git_dir, python=None):
    """Why the personal repository is not captured, as a list (empty when it is).

    Plan 10.4 step 2, both read-only: the personal repository has no uncommitted
    change to a file it tracks, and the capture's staging dry run selects nothing.
    """
    python = python or sys.executable
    problems = []
    try:
        status = worktree.git(root, "status", "--short", "--untracked-files=no",
                              git_dir=git_dir)
    except worktree.GitError as exc:
        return [str(exc)]
    if status.strip():
        problems.append("the personal repository has uncommitted changes: "
                        + status.strip().splitlines()[0])
    result = subprocess.run([python, "workflows/sync-architecture/scripts/allowlist.py",
                             "--stage", "--dry-run", "--git-dir", str(git_dir)],
                            cwd=str(root), capture_output=True, encoding="utf-8",
                            errors="replace")
    text = result.stdout + result.stderr
    if result.returncode != 0 or not re.search(r"(?<![0-9])0 file\(s\)", text):
        problems.append("the capture's staging dry run does not report 0 files: "
                        + (text.strip().splitlines() or ["(no output)"])[-1])
    return problems


@dataclass
class Deps:
    """Every way the loop reaches a provider, a check or the clock."""

    make_builder: Callable = None
    make_reviewer: Callable = None
    read_codex: Callable = None
    run_checks: Callable = None
    baseline_checks: Callable = None
    category_a: Callable = None
    capture_problems: Callable = None
    sleep: Callable = time.sleep
    now: Callable = time.time

    def __post_init__(self):
        if self.make_builder is None:
            self.make_builder = lambda settings, approver, cwd, resume: \
                providers.make_builder(settings, approver, cwd=cwd, resume=resume)
        if self.make_reviewer is None:
            self.make_reviewer = lambda settings, cwd: providers.make_reviewer(settings,
                                                                               cwd=cwd)
        if self.read_codex is None:
            self.read_codex = _read_codex_live
        if self.run_checks is None:
            self.run_checks = checks_mod.run_all
        if self.baseline_checks is None:
            self.baseline_checks = checks_mod.close_out_start
        if self.category_a is None:
            self.category_a = worktree.category_a
        if self.capture_problems is None:
            self.capture_problems = capture_problems


# ------------------------------------------------------------------ small helpers


def render(name, **values):
    """A prompt from ``prompts/<name>.md`` with its ``$name`` fields filled in."""
    template = string.Template((PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8"))
    return template.substitute(**values)


def _hashes(contents):
    """A ``worktree.snapshot`` from a ``worktree.contents`` reading."""
    return {path: hashlib.sha256(data).hexdigest() for path, data in contents.items()}


def credit_snapshot(reading):
    """The Codex credit snapshot a reading carries, as the three values plan 10.10
    item 3 compares, or None when the reading has none."""
    raw = getattr(reading, "raw", None)
    snapshot = raw.get("rateLimits") if isinstance(raw, dict) else None
    credits = snapshot.get("credits") if isinstance(snapshot, dict) else None
    if not isinstance(credits, dict):
        return None
    return {"hasCredits": credits.get("hasCredits"), "unlimited": credits.get("unlimited"),
            "balance": credits.get("balance", "absent")}


def _reached_type(reading):
    raw = getattr(reading, "raw", None)
    snapshot = raw.get("rateLimits") if isinstance(raw, dict) else None
    return snapshot.get("rateLimitReachedType") if isinstance(snapshot, dict) else None


def untrackable(snapshot):
    """Why a preflight credit snapshot cannot be tracked, or None."""
    if snapshot is None:
        return "the Codex reading has no credits snapshot"
    if (snapshot["hasCredits"] or snapshot["unlimited"]) and snapshot["balance"] == "absent":
        return "Codex reports credits or unlimited use but no balance"
    return None


def claude_tokens(usage):
    if not isinstance(usage, dict):
        return None
    keys = ("input_tokens", "output_tokens", "cache_read_input_tokens",
            "cache_creation_input_tokens")
    values = [usage.get(key) for key in keys]
    numbers = [v for v in values if isinstance(v, int) and not isinstance(v, bool)]
    return sum(numbers) if numbers else None


def codex_tokens(usage):
    if not isinstance(usage, dict):
        return None
    for part in ("total", "last"):
        block = usage.get(part)
        if isinstance(block, dict) and isinstance(block.get("total_tokens"), int):
            return block["total_tokens"]
    return None


def _session_id(messages):
    for message in messages or ():
        if isinstance(message, dict):
            value = message.get("session_id")
            if isinstance(value, str) and value:
                return value
            data = message.get("data")
            if isinstance(data, dict) and isinstance(data.get("session_id"), str):
                return data["session_id"]
    return None


def review_schema(with_pattern=True):
    """The reviewer's reply schema for a run: ``stopreasons.REVIEW_SCHEMA`` plus a
    required ``files_reviewed`` list, which the loop checks covers every changed file
    and removes before ``parse_review`` (R4-1). Without the title pattern when Codex
    has refused it."""
    schema = json.loads(json.dumps(sr.REVIEW_SCHEMA))
    schema["required"] = list(schema["required"]) + ["files_reviewed"]
    schema["properties"]["files_reviewed"] = {"type": "array",
                                              "items": {"type": "string"}}
    if not with_pattern:
        schema["properties"]["findings"]["items"]["properties"]["title"].pop("pattern",
                                                                            None)
    return schema


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=repr) + "\n",
                    encoding="utf-8", newline="\n")


def sdk_versions():
    versions = {}
    for package in SDK_PACKAGES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def _log(action, note, log_path):
    """Append one LOG.md entry. Returns the reason it failed, or None."""
    return brief_mod._log(action, note, log_path, dry_run=False)


# ------------------------------------------------------------------------ the run


class Run:
    """One run's state and the steps that move it on."""

    def __init__(self, run_dir, state, settings, *, root=PROJECT_ROOT, deps=None,
                 log_path=LOG_PATH, wait=False, python=None):
        self.run_dir = Path(run_dir)
        self.state = state
        self.settings = settings
        self.root = Path(root)
        self.deps = deps or Deps()
        self.log_path = log_path
        self.wait = wait
        self.python = python or sys.executable
        self.ledger = sr.Ledger(state.get("ledger", []))
        self.approver = approver_mod.Approver(state["edit_paths"],
                                              approver_mod.load_verify_commands(),
                                              root=self.root)
        self.usage = limits.RunUsage()
        # A negative Codex reading holds for the whole run, resumes included.
        source = state.get("usage_source") or {}
        if source.get("codex") == limits.UNAVAILABLE:
            self.usage.codex_negative = limits.Reading(
                limits.CODEX, limits.UNAVAILABLE, reason=source.get("codex_reason"),
                raw=source.get("codex_raw"))
        self.builder = None

    # ----------------------------------------------------------------- state

    def save(self):
        self.state["ledger"] = list(self.ledger.findings.values())
        sr.record_rounds(self.state, self.ledger)
        runrecord.write_state(self.run_dir, self.state)

    def brief_text(self):
        return (self.run_dir / "brief.md").read_text(encoding="utf-8")

    def complete(self, step):
        self.state["step"] = step
        self.state.pop("interrupted", None)
        self.save()

    def next_step(self):
        step = self.state["step"]
        if step == "preflight":
            return "build"
        if step == "build":
            return "review-R1"
        kind, round_no = step.split("-R")
        return f"fix-R{round_no}" if kind == "review" else f"review-R{int(round_no) + 1}"

    # ---------------------------------------------------------------- driving

    async def advance(self):
        """Take steps until the run ends or pauses; with --wait, sleep through a
        usage limit that reported its reset time and carry on."""
        try:
            while True:
                while self.state["status"] == sr.RUNNING:
                    await self.take(self.next_step())
                # A session cut off by a limit is not reused: a resume reconnects to
                # the same session by its ID.
                await self._close_builder()
                if not self._wait_and_resume():
                    break
        finally:
            await self._close_builder()
        return self.state

    def _wait_and_resume(self):
        if not (self.wait and self.state["status"] == sr.ENDED):
            return False
        last = self.state["stop_reasons"][-1]
        resets = (last.get("evidence") or {}).get("resets_at")
        if last["reason"] != sr.USAGE_LIMIT or not isinstance(resets, (int, float)):
            return False
        delay = max(0, resets - self.deps.now()) + WAIT_MARGIN_SECONDS
        print(f"--wait: sleeping {round(delay)} s until the reported reset.",
              file=sys.stderr)
        self.deps.sleep(delay)
        resume(self)
        return True

    async def take(self, step):
        provider = (self.settings["roles"]["reviewer"] if step.startswith("review")
                    else self.settings["roles"]["builder"])
        try:
            stop = self.before_step(step, provider)
        except Exception as exc:  # the reading itself failed: the step never started
            self.end(sr.ERROR, {"step": step, "error": "the usage reading before the "
                                f"step failed: {type(exc).__name__}: {exc}"})
            return
        if stop is not None:
            self.end(sr.USAGE_LIMIT, stop)
            return
        try:
            if step == "build":
                await self.build()
            elif step.startswith("review"):
                await asyncio.to_thread(self.review, int(step.split("-R")[1]))
            else:
                await self.fix(int(step.split("-R")[1]))
        except limits.UsageLimitError as exc:
            self._interrupted(step)
            self.end(sr.USAGE_LIMIT, {"step": step, "provider": exc.provider,
                                      "detail": exc.detail, "resets_at": exc.resets_at})
        except Exception as exc:  # a provider, a check or a reply failed
            self._interrupted(step)
            self.end(sr.ERROR, {"step": step, "error": f"{type(exc).__name__}: {exc}"})

    def _interrupted(self, step):
        """Record what a step left behind when it did not complete.

        Best effort, and never raises: the caller ends the run straight after, and that
        end (the stop reason, the report, the LOG.md entry, a resumable state) must
        happen whatever goes wrong here. A failure in any part, such as a builder having
        broken the sync classification the partial diff reads, is noted in
        ``interrupted_notes`` instead (review finding R7-1).
        """
        self.state["interrupted"] = step
        notes = self.state.setdefault("interrupted_notes", [])
        if self.builder is not None:
            # The session ID first: a resume depends on it, and it reads no files.
            self.state["builder_session"] = (self.builder.session_id
                                             or _session_id(self.builder.last_messages)
                                             or self.state.get("builder_session"))
        for what, action in (("the partial transcript", self._save_partial_transcript),
                             ("the refused calls", lambda s: self._take_refusals(s)),
                             ("the partial diff", self._save_partial_diff)):
            try:
                action(step)
            except Exception as exc:  # noqa: BLE001 - never stop the run's own end
                notes.append(f"{step}: {what} could not be saved: "
                             f"{type(exc).__name__}: {exc}")

    def _save_partial_transcript(self, step):
        if self.builder is not None and self.builder.last_messages:
            _write_json(self.run_dir / "transcripts" / f"{step}-partial.json",
                        self.builder.last_messages)

    def _save_partial_diff(self, step):
        partial = self.run_dir / "partial" / f"{step}.patch"
        partial.parent.mkdir(parents=True, exist_ok=True)
        try:
            diff = self.diff()
        except Exception as exc:  # noqa: BLE001 - say why, and keep the file
            why = f"{type(exc).__name__}: {exc}"
            # Noted in state.json as well as in the placeholder patch, as every failed
            # save is (review finding R8-1).
            self.state.setdefault("interrupted_notes", []).append(
                f"{step}: the partial diff could not be taken: {why}")
            diff = f"(the partial diff could not be taken: {why})\n"
        partial.write_text(diff, encoding="utf-8", newline="\n")

    # --------------------------------------------------------------- usage

    def note_codex(self, reading):
        """Pass a Codex reading through the run's RunUsage and record the run's usage
        source, as plan 10.10 item 1 asks: ``usage_source.codex`` is "unavailable",
        with the reason and the raw response, from the first reading that was, and it
        stays so for the rest of the run (review finding R2-4)."""
        reading = self.usage.codex(reading)
        source = self.state.setdefault("usage_source", {})
        if reading.source == limits.UNAVAILABLE:
            if source.get("codex") != limits.UNAVAILABLE:
                negative = self.usage.codex_negative or reading
                source["codex"] = limits.UNAVAILABLE
                source["codex_reason"] = negative.reason
                source["codex_raw"] = negative.raw
        else:
            source.setdefault("codex", "reported")
        return reading

    def readings(self):
        codex = self.note_codex(self.deps.read_codex(self.root))
        claude = (self.builder.usage.reading() if self.builder is not None
                  else limits.Reading(limits.CLAUDE, limits.UNAVAILABLE,
                                      reason="no builder session open yet"))
        return codex, claude

    def before_step(self, step, provider):
        """None if the step may start, else the evidence for a ``usage-limit`` stop."""
        codex, claude = self.readings()
        self.state.setdefault("readings", []).append(
            {"step": step, "codex": codex.summary(), "claude": claude.summary()})
        stop = limits.check_ceiling([codex, claude], self.settings["usage_ceiling_percent"])
        if stop is not None:
            return {"step": step, "provider": stop.provider, "detail": stop.reason,
                    "reading": stop.reading.summary(), "resets_at": stop.reading.resets_at}
        if provider == limits.CODEX:
            now = credit_snapshot(codex)
            then = self.state.get("credits") or {}
            if now is None:
                return {"step": step, "provider": limits.CODEX, "resets_at": None,
                        "detail": "the Codex credit snapshot is missing from the reading "
                                  f"({codex.summary()})"}
            differs = [key for key in ("hasCredits", "unlimited", "balance")
                       if now.get(key) != then.get(key)]
            if differs:
                return {"step": step, "provider": limits.CODEX, "resets_at": None,
                        "detail": "the Codex credit snapshot changed since preflight ("
                                  + ", ".join(f"{key} {then.get(key)!r} -> {now[key]!r}"
                                              for key in differs) + ")"}
            if _reached_type(codex) and now.get("hasCredits"):
                return {"step": step, "provider": limits.CODEX, "resets_at":
                        codex.resets_at, "detail": "Codex reports its included allowance "
                        f"reached ({_reached_type(codex)}) with credits next"}
        return None

    # -------------------------------------------------------------- builder

    async def _builder(self):
        if self.builder is None:
            self.builder = self.deps.make_builder(self.settings, self.approver, self.root,
                                                  self.state.get("builder_session"))
            await self.builder.start()
        return self.builder

    async def _close_builder(self):
        if self.builder is not None:
            try:
                await self.builder.close()
            finally:
                self.builder = None

    def _inject(self, step, provider):
        """The test-only limit switch (plan 10.4 chunk (c), live check 2): raise
        ``UsageLimitError`` at the provider boundary of one named step, once."""
        inject = self.state.get("inject")
        if inject and inject.get("step") == step and not inject.get("fired"):
            inject["fired"] = True
            self.save()
            raise limits.UsageLimitError(provider, "injected by --inject-limit "
                                                   "(test-only)")

    def _prompt(self, step, prompt):
        if self.state.get("interrupted") == step:
            return ("Your previous turn in this session was interrupted before it "
                    "finished, so the tree may hold part of that work. Carry on and "
                    "complete it. The request was:\n\n" + prompt)
        return prompt

    def _take_refusals(self, step):
        for refusal in self.approver.refusals:
            self.state.setdefault("refusals", []).append({"step": step, **refusal})
        self.approver.refusals.clear()

    def _after_builder(self, step, turn):
        self.state["builder_session"] = turn.session_id
        if turn.model and "claude_init_model" not in self.state.setdefault("live", {}):
            self.state["live"]["claude_init_model"] = turn.model
        self.state.setdefault("steps", []).append(
            {"step": step, "role": "builder", "provider": self.builder.provider,
             "model": turn.model, "tokens": claude_tokens(turn.usage),
             "usage": turn.usage, "cost_usd_equivalent": turn.cost_usd_equivalent,
             "wall_seconds": turn.wall_seconds, "summary": (turn.final_text or "")[-4000:]})
        _write_json(self.run_dir / "transcripts" / f"{step}.json", turn.messages)
        self._take_refusals(step)

    async def build(self):
        builder = await self._builder()
        verify = "\n".join(f"      {pattern.pattern}"
                           for pattern in self.approver.verify_patterns)
        prompt = render("builder", edit_paths=", ".join(self.state["edit_paths"]),
                        verify_commands=verify, brief=self.brief_text())
        self._inject("build", builder.provider)
        turn = await builder.turn("build", self._prompt("build", prompt))
        self._after_builder("build", turn)
        after = worktree.snapshot(self.root, self.state["edit_paths"],
                                  self.deps.category_a(self.root))
        empty = not worktree.changed(self.state["start"]["snapshot"], after)
        self.complete("build")
        stop = sr.after_first_pass(empty)
        if stop is not None:
            self.end(*stop)

    async def fix(self, round_no):
        step = f"fix-R{round_no}"
        builder = await self._builder()
        open_labels = self.ledger.open_labels()
        blocks = []
        for label in open_labels:
            f = self.ledger.findings[label]
            blocks.append(f"{label} ({f['source']}, {f.get('severity')}) {f.get('title')}\n"
                          f"File: {f.get('file')}\nEvidence: {f.get('evidence')}\n"
                          f"Suggested fix: {f.get('fix')}"
                          + (f"\nRe-raises: {f['reraises']}" if f.get("reraises") else ""))
        prompt = render("fix", round=round_no, findings="\n\n".join(blocks))
        self._inject(step, builder.provider)
        turn = await builder.turn(step, self._prompt(step, prompt))
        self._after_builder(step, turn)
        actions = sr.parse_builder_reply(turn.final_text, open_labels)
        self.ledger.apply_actions(actions)
        self.write_packet()
        self.complete(step)
        stop = sr.decide(self.ledger)
        if stop is not None:
            self.end(*stop)

    # ------------------------------------------------------------- reviewer

    def diff(self, category_a=None):
        start = self.state["start"]
        if category_a is None:
            category_a = self.deps.category_a(self.root)
        return worktree.review_diff(self.root, self.state["edit_paths"],
                                    start["public_head"], start["personal_head"],
                                    self.state["git_dir"], category_a=category_a)

    def _review_once(self, reviewer, prompt):
        """One review. If Codex refuses the schema's title pattern, it comes out and
        the script's own title check is the only one (plan 10.4 chunk (c) check 3)."""
        live = self.state.setdefault("live", {})
        if live.get("title_pattern", "").startswith("refused"):
            return reviewer.review(prompt, review_schema(with_pattern=False))
        try:
            record = reviewer.review(prompt, review_schema())
        except providers.ProviderError as exc:
            if "pattern" not in str(exc).lower():
                raise
            live["title_pattern"] = f"refused: {exc}"
            self.save()
            return reviewer.review(prompt, review_schema(with_pattern=False))
        live.setdefault("title_pattern", "accepted")
        return record

    def review(self, round_no):
        step = f"review-R{round_no}"
        edit_paths = self.state["edit_paths"]
        start_snapshot = self.state["start"]["snapshot"]
        # The checks run first; the diff and the archive are then taken together from
        # the state the checks left, so what is reviewed and what is recorded are one
        # state (review finding R4-2). What the checks themselves wrote, such as a
        # verifier's own LOG.md entry, is taken as an exact diff of the contents either
        # side of them, so the reviewer is told which lines are the orchestrator's,
        # never which whole files (R5-2): a file the builder also edited keeps the
        # builder's lines as the builder's.
        category_a = self.deps.category_a(self.root)
        before_checks = worktree.contents(self.root, edit_paths, category_a)
        directories = worktree.changed_dirs(worktree.changed(
            start_snapshot, _hashes(before_checks)))
        mechanical, mechanical_text = self.deps.run_checks(
            self.root, edit_paths, directories, self.state["acceptance"],
            python=self.python)
        category_a = self.deps.category_a(self.root)
        after_checks = worktree.contents(self.root, edit_paths, category_a)
        written = worktree.delta(before_checks, after_checks,
                                 f"the round {round_no} checks")
        diff = self.diff(category_a)
        folder = worktree.record_round(self.run_dir, round_no, self.root, edit_paths,
                                       diff, category_a=category_a)
        if written:
            (folder / "check-writes.patch").write_text(written, encoding="utf-8",
                                                       newline="\n")
            name = (folder / "check-writes.patch").relative_to(self.run_dir).as_posix()
            names = self.state.setdefault("check_writes", [])
            if name not in names:  # a resumed round writes its record again
                names.append(name)
        changed_files = worktree.changed(start_snapshot, _hashes(after_checks))
        check_written = "".join(
            (self.run_dir / name).read_text(encoding="utf-8")
            for name in self.state.get("check_writes", []))
        prior = "\n".join(
            f"- {f['label']} ({f['source']}) {f.get('title')}: builder action "
            f"{f.get('action') or 'none yet'}"
            for f in self.ledger.findings.values()) or "None."
        prompt = render("reviewer", round=round_no, brief=self.brief_text(),
                        mechanical=mechanical_text or "None.", prior=prior,
                        diff=self._shown_diff(diff, folder),
                        changed_files="\n".join(f"- {path}" for path in changed_files)
                        or "None.",
                        check_written=check_written or "None.")
        reviewer = self.deps.make_reviewer(self.settings, self.root)
        self._inject(step, reviewer.provider)
        record = self._review_once(reviewer, prompt)
        reply = self._covered(record.reply, changed_files)
        findings = sr.parse_review(reply, self.ledger)
        new = self.ledger.add_round(round_no, mechanical, findings)
        self._accept_declines(round_no, new)
        self.state.setdefault("steps", []).append(
            {"step": step, "role": "reviewer", "provider": reviewer.provider,
             "model": reviewer.model, "tokens": codex_tokens(record.usage),
             "usage": record.usage, "wall_seconds": record.wall_seconds,
             "thread_id": record.thread_id, "summary": record.reply.get("summary"),
             "mechanical": mechanical_text})
        _write_json(self.run_dir / "transcripts" / f"{step}.json", record.raw)
        self.write_packet()
        self.complete(step)
        stop = sr.decide(self.ledger)
        if stop is None and not new:
            still_open = [f["label"] for f in self.ledger.findings.values()
                          if not packet_mod.is_addressed(f)]
            if still_open:  # cannot happen by the rules above; never end clean on it
                raise RuntimeError("no new findings, yet findings are still open: "
                                   + ", ".join(still_open))
            stop = (sr.REVIEWED_CLEAN, {"round": round_no})
        if stop is None:
            cap = self.state["round_cap"]
            stop = sr.after_review(round_no, cap, new)
        if stop is not None:
            self.end(*stop)

    def _shown_diff(self, diff, folder):
        """The diff for the reviewer's prompt: whole, or, when it is larger than the
        prompt can hold, where to read it in parts. Never a cut-down diff (review
        finding R4-1, the user's choice of a split review with a coverage check)."""
        if not diff:
            return "(empty)"
        if len(diff) <= DIFF_LIMIT:
            return diff
        saved = (folder / "diff.patch").relative_to(self.root).as_posix()
        return (f"(The diff is {len(diff):,} characters, too large for this prompt. It "
                f"is saved whole at `{saved}`: read it there, in parts, and also read "
                "each changed file listed below. Every one of them must be in your "
                "files_reviewed list.)")

    def _covered(self, reply, changed_files):
        """Check the reviewer listed every changed file in ``files_reviewed``, then
        return the reply without that field for ``parse_review``. A missing file is a
        malformed reply, so the round ends ``error`` rather than passing unread
        (R4-1)."""
        if not isinstance(reply, dict):
            return reply
        reply = dict(reply)
        listed = reply.pop("files_reviewed", None)
        if not isinstance(listed, list) or not all(isinstance(p, str) for p in listed):
            raise sr.ReplyError("the reviewer's reply has no files_reviewed list")
        seen = {p.strip().replace("\\", "/").removeprefix("./") for p in listed}
        missing = [path for path in changed_files if path not in seen]
        if missing:
            raise sr.ReplyError("the reviewer did not list these changed files as "
                                "reviewed: " + ", ".join(missing[:20])
                                + (" ..." if len(missing) > 20 else ""))
        return reply

    def _accept_declines(self, round_no, new):
        """A finding the builder declined on its merits that this round's reviewer did
        not raise again is accepted: the reviewer has let the decline stand (the user's
        choice for R1-1, 2026-09-30). One it did raise again ends the run
        ``disagreement`` through the rules instead."""
        raised = {f["reraises"] for f in new if f.get("reraises")}
        for f in self.ledger.findings.values():
            if (f.get("action") == sr.DECLINED_MERITS and f["round"] < round_no
                    and not f.get("decline_accepted") and f["label"] not in raised):
                f["decline_accepted"] = round_no

    # ---------------------------------------------------------------- ending

    def write_packet(self):
        packet_mod.write(self.root, self.state["item"], self.state["run_id"], self.ledger)

    def end(self, reason, evidence=None):
        sr.record_stop(self.state, reason, evidence)
        self.save()
        if self.ledger.findings:
            self.write_packet()
        write_report(self)
        action = "failed" if reason == sr.ERROR else "completed"
        _log(action, f"Run {self.state['run_id']} ended {reason} after step "
                     f"{self.state['step']}.", self.log_path)


# ------------------------------------------------------------------------ report


def _started(state):
    """A run's start as a comparable number: ``started_ns`` (nanoseconds, recorded by
    ``start``) where the record has it, else ``created`` to the second."""
    if isinstance(state.get("started_ns"), int):
        return state["started_ns"]
    try:
        return int(datetime.fromisoformat(state.get("created", "")).timestamp()) \
            * 1_000_000_000
    except (TypeError, ValueError):
        return None


def _earlier_steps(run):
    """Token counts by role from every other run that started strictly before this one.

    Compared by the recorded start time in nanoseconds, not the run ID or the start
    second: two runs started in the same second differ only in the ID's random suffix,
    which says nothing about order (review finding R2-6). A run whose start equals
    this one's, which only a record without ``started_ns`` can give, is left out
    rather than guessed at.
    """
    by_role = {}
    ours = _started(run.state)
    for other in sorted(run.run_dir.parent.glob("*/state.json")):
        if other.parent.name == run.state["run_id"]:
            continue
        try:
            data = json.loads(other.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        theirs = _started(data)
        if ours is None or theirs is None or theirs >= ours:
            continue
        for step in data.get("steps", []):
            if isinstance(step.get("tokens"), int):
                by_role.setdefault(step.get("role"), []).append(step["tokens"])
    return by_role


def price_alarm(run):
    """Plan 10.10 item 6: every step using more than twice the median for its role
    across earlier runs."""
    earlier = _earlier_steps(run)
    flags = []
    for step in run.state.get("steps", []):
        history = earlier.get(step.get("role"))
        if history and isinstance(step.get("tokens"), int):
            median = statistics.median(history)
            if median and step["tokens"] > 2 * median:
                flags.append({"step": step["step"], "role": step["role"],
                              "tokens": step["tokens"], "median": median})
    return flags


def write_report(run):
    state = run.state
    last = state["stop_reasons"][-1]
    evidence = last.get("evidence") or {}
    findings = list(run.ledger.findings.values())
    report = {
        "run_id": state["run_id"], "item": state["item"], "stop_reason": last["reason"],
        "evidence": evidence, "recommendation": RECOMMEND.get(last["reason"]),
        "stop_reasons": state["stop_reasons"], "last_step": state["step"],
        "rounds": state.get("rounds", []),
        "open": [f["label"] for f in findings if not packet_mod.is_addressed(f)],
        "addressed": [f["label"] for f in findings if packet_mod.is_addressed(f)],
        "readings": state.get("readings", []), "steps": state.get("steps", []),
        "refusals": state.get("refusals", []), "live": state.get("live", {}),
        "inject": state.get("inject"), "price_alarm": price_alarm(run),
        "usage_source": {"codex": (state.get("usage_source") or {}).get("codex",
                                                                         "not read"),
                         **({"codex_reason": state["usage_source"].get("codex_reason"),
                             "codex_raw": state["usage_source"].get("codex_raw")}
                            if (state.get("usage_source") or {}).get("codex")
                            == limits.UNAVAILABLE else {})},
        "packet": packet_mod.packet_path(".", state["item"]).as_posix(),
    }
    _write_json(run.run_dir / "report.json", report)
    lines = [f"# Run {state['run_id']}", ""]
    if report["usage_source"]["codex"] == limits.UNAVAILABLE:
        # Plan 10.10 item 1: a negative result goes on the report's first lines.
        lines += ["Codex usage not reported: "
                  + " ".join(str(report["usage_source"].get("codex_reason")).split())
                  + ". The ceiling was not applied to Codex for this run.", ""]
    lines += [f"**Stop reason:** `{last['reason']}`", "",
             f"**Recommended:** {RECOMMEND.get(last['reason'])}", ""]
    if last["reason"] == sr.USAGE_LIMIT:
        resets = evidence.get("resets_at")
        lines += [f"**Provider:** {evidence.get('provider')}. {evidence.get('detail')}",
                  "", f"**Reset time:** {resets if resets is not None else 'not reported'}"
                      + ("" if resets is not None else "; --wait does nothing, so check "
                         "the allowance has reset and resume by hand."), ""]
    elif evidence:
        lines += ["**Evidence:** `" + json.dumps(evidence, ensure_ascii=False)[:1500]
                  .replace("`", "'") + "`", ""]
    lines += [f"**Findings:** {len(report['open'])} open, {len(report['addressed'])} "
              f"addressed, in `{report['packet']}`.", "", "## Steps", ""]
    for step in report["steps"]:
        lines.append(f"- {step['step']} ({step['role']}, {step['provider']}, "
                     f"{step.get('model')}): {step.get('tokens')} tokens, "
                     f"{step.get('wall_seconds')} s")
    lines += ["", "## Usage readings", ""]
    lines += [f"- before {r['step']}: {r['codex']}; {r['claude']}"
              for r in report["readings"]] or ["None."]
    lines += ["", "## Refused builder calls", ""]
    lines += [f"- {r['step']}: {r['tool']} {r.get('detail', '')}: {r['reason']}"
              for r in report["refusals"]] or ["None."]
    lines += ["", "## Price alarm", ""]
    lines += [f"- {a['step']} ({a['role']}): {a['tokens']} tokens, more than twice the "
              f"median {a['median']} of earlier runs" for a in report["price_alarm"]] \
        or ["None."]
    lines += ["", "## Live checks", ""]
    lines += [f"- {key}: {' '.join(str(value).split())}"
              for key, value in report["live"].items()] or ["None."]
    (run.run_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8",
                                           newline="\n")


# ------------------------------------------------------------ start, resume, stop


def preflight(brief_arg, item, git_dir, *, root=PROJECT_ROOT, deps=None, settings=None):
    """Every read-only check a run makes before it creates anything. Returns what the
    run needs; raises RunRefused with every reason it cannot start."""
    deps = deps or Deps()
    problems = []
    if not ITEM.match(item or ""):
        problems.append(f"--item {item!r} must be lower case letters, digits and "
                        "underscores, as in memory/<item>_review_packet.md")
    try:
        settings = settings or load_settings()
    except Exception as exc:
        problems.append(f"settings: {exc}")
    try:
        brief = brief_mod.read_brief(brief_arg, root)
    except ValueError as exc:
        brief = None
        problems.append(str(exc))
    if ITEM.match(item or "") and packet_mod.packet_path(root, item).exists():
        problems.append(f"memory/{item}_review_packet.md already exists; archive the "
                        "closed round first (memory/review_process.md, 'Archiving A "
                        "Closed Round')")
    missing = [name for name, version in sdk_versions().items() if version is None]
    if missing:
        problems.append("SDK not installed: " + ", ".join(missing) + " (pip install -r "
                        "workflows/review-orchestration/requirements.txt)")
    if brief is not None:
        try:
            dirty = worktree.uncommitted(root, brief["edit_paths"])
        except worktree.GitError as exc:
            dirty = [f"(git status failed: {exc})"]
        if dirty:
            problems.append("the edit paths have uncommitted changes, which a review "
                            "would show as the run's: " + ", ".join(dirty[:10])
                            + (" ..." if len(dirty) > 10 else "")
                            + "; commit them first")
    if not Path(git_dir).is_dir():
        problems.append(f"--git-dir {git_dir} is not a folder")
    else:
        problems += deps.capture_problems(root, git_dir)
    try:
        category_a = deps.category_a(root)
    except Exception as exc:  # without it no gitignored file can be judged (R3-1)
        category_a = None
        problems.append(f"the sync classification cannot be read: {exc}")
    if problems:
        raise RunRefused(problems)
    try:
        reading = deps.read_codex(root)
    except Exception as exc:
        raise RunRefused([f"the Codex usage reading failed: {type(exc).__name__}: "
                          f"{exc}"]) from exc
    stop = limits.check_ceiling([reading], settings["usage_ceiling_percent"])
    if stop is not None:
        raise RunRefused([f"usage: {stop.reason} ({reading.summary()})"])
    credits = credit_snapshot(reading)
    reason = untrackable(credits)
    if reason is not None:
        raise RunRefused([f"Codex credits cannot be tracked: {reason}"])
    return {"settings": settings, "brief": brief, "credits": credits,
            "reading": reading, "category_a": category_a}


def start(brief_arg, item, git_dir, *, root=PROJECT_ROOT, deps=None, log_path=LOG_PATH,
          runs_dir=runrecord.RUNS_DIR, inject=None, dry_run=False, wait=False,
          settings=None, python=None):
    """Preflight, then create the run record and take the run as far as it goes.
    Returns the Run (or None on a dry run)."""
    deps = deps or Deps()
    if inject is not None and not STEP.match(inject):
        raise RunRefused([f"--inject-limit {inject!r} is not a step name (build, "
                          "review-R<n>, fix-R<n>)"])
    ready = preflight(brief_arg, item, git_dir, root=root, deps=deps, settings=settings)
    settings, brief = ready["settings"], ready["brief"]
    if dry_run:
        # The clean-start check is not run here: the close-out verifier writes its own
        # log and result file every time it runs, so it cannot run in a dry run. The
        # preview says so rather than claim more than it checked (review finding R6-1).
        print(f"[DRY RUN] the read-only preflight checks passed ({ready['reading'].summary()})."
              " NOT checked: the close-out verifier's clean-start check, which writes its "
              "own log and so cannot run in a dry run; a real start runs it first and "
              "refuses the run if the checks are not clean. If it passes, the run would "
              f"create a run record for item {item} with edit paths "
              f"{', '.join(brief['edit_paths'])}, then build and review up to "
              f"{settings['round_caps']['build_review']} round(s).", file=sys.stderr)
        return None
    # A run starts only from a tree where the close-out checks are clean, so nothing
    # failing during it can predate it (the user's re-plan of R2-5, after R5-3). What
    # the check itself writes under the edit paths (its own LOG.md) is kept as an exact
    # diff and named to every reviewer as the orchestrator's (R5-2).
    before_start_check = worktree.contents(root, brief["edit_paths"], ready["category_a"])
    baseline = deps.baseline_checks(root, python or sys.executable)
    if not baseline.get("clean"):
        raise RunRefused(["the close-out checks are not clean, and a run starts only "
                          "from a tree where they are: "
                          + "; ".join(baseline.get("problems") or ["no detail"])
                          + ". Make them pass, then start the run"])
    start_writes = worktree.delta(before_start_check,
                                  worktree.contents(root, brief["edit_paths"],
                                                    ready["category_a"]),
                                  "the start-of-run close-out check")
    started_ns = time.time_ns()
    run_dir = runrecord.create_run(runs_dir=runs_dir)
    if start_writes:
        (run_dir / "start").mkdir(parents=True, exist_ok=True)
        (run_dir / "start" / "check-writes.patch").write_text(
            start_writes, encoding="utf-8", newline="\n")
    shutil.copyfile(Path(root) / brief_arg, run_dir / "brief.md")
    _write_json(run_dir / "settings.json", {**settings, "sdk_versions": sdk_versions()})
    heads = worktree.record_start(run_dir, root, brief["edit_paths"], git_dir)
    state = runrecord.read_state(run_dir)
    state.update({
        "item": item, "git_dir": str(git_dir), "edit_paths": brief["edit_paths"],
        "acceptance": brief["acceptance"],
        "round_cap": settings["round_caps"]["build_review"],
        "start": {**heads, "snapshot": worktree.snapshot(root, brief["edit_paths"],
                                                         ready["category_a"])},
        "credits": ready["credits"], "builder_session": None, "ledger": [],
        "steps": [], "refusals": [], "readings": [], "live": {},
        "inject": {"step": inject, "fired": False} if inject else None,
        "baseline": baseline, "started_ns": started_ns, "usage_source": {},
        "check_writes": (["start/check-writes.patch"] if start_writes else []),
        "step": "preflight",
    })
    run = Run(run_dir, state, settings, root=root, deps=deps, log_path=log_path,
              wait=wait, python=python)
    run.note_codex(ready["reading"])
    run.save()
    _log("started", f"Run {state['run_id']} started for item {item}.", log_path)
    return run


def resume(run, rounds=None, dry_run=False):
    """Continue an ended or paused run from its last completed step. The Codex credit
    snapshot is taken again, so a harmless change (a top-up) costs only the resume.

    Every check comes before anything about the run changes: a resume refused for a
    reason that is not resumable, a failed reading or untrackable credits leaves
    ``state.json``, the report and LOG.md exactly as the run's last end left them
    (review finding R2-3)."""
    status = run.state.get("status")
    last = run.state["stop_reasons"][-1]["reason"] if run.state["stop_reasons"] else None
    if status == sr.RUNNING:
        raise RunRefused(["the run is already running"])
    if status == sr.ENDED and last not in sr.RESUMABLE:
        raise RunRefused([f"a run that ended {last} cannot be resumed; only "
                          f"{', '.join(sr.RESUMABLE)} can"])
    try:
        reading = run.deps.read_codex(run.root)
    except Exception as exc:
        raise RunRefused([f"the Codex usage reading failed: {type(exc).__name__}: "
                          f"{exc}"]) from exc
    credits = credit_snapshot(reading)
    reason = untrackable(credits)
    if reason is not None:
        raise RunRefused([f"Codex credits cannot be tracked: {reason}; the run is "
                          "unchanged"])
    if dry_run:
        # Every check above has run and nothing has changed (review finding R3-3).
        last_round = max([f["round"] for f in run.ledger.findings.values()] or [0])
        cap = last_round + rounds if rounds is not None else run.state["round_cap"]
        where = f"ended {last}" if status == sr.ENDED else f"paused {run.state.get('pause')}"
        print(f"[DRY RUN] would resume run {run.state['run_id']} ({where}) from its "
              f"last completed step {run.state['step']}; "
              f"next step {run.next_step()}; round cap {cap}; Codex credits {credits}; "
              f"{reading.summary()}. Nothing was changed.", file=sys.stderr)
        return
    sr.continue_run(run.state)
    if rounds is not None:
        last_round = max([f["round"] for f in run.ledger.findings.values()] or [0])
        run.state["round_cap"] = last_round + rounds
    run.state["credits"] = credits
    run.note_codex(reading)
    run.save()
    _log("started", f"Run {run.state['run_id']} resumed from step {run.state['step']}.",
         run.log_path)


def load(run_id, *, root=PROJECT_ROOT, deps=None, log_path=LOG_PATH,
         runs_dir=runrecord.RUNS_DIR, wait=False, settings=None, python=None):
    """The Run for an existing record, with the settings it started under."""
    if not runrecord.RUN_ID.match(run_id or ""):
        raise RunRefused([f"not a run ID: {run_id!r}"])
    run_dir = Path(runs_dir) / run_id
    if not (run_dir / runrecord.STATE_FILE).is_file():
        raise RunRefused([f"no run record for {run_id}"])
    state = runrecord.read_state(run_dir)
    if settings is None:
        saved = json.loads((run_dir / "settings.json").read_text(encoding="utf-8"))
        saved.pop("sdk_versions", None)
        settings = saved
    return Run(run_dir, state, settings, root=root, deps=deps, log_path=log_path,
               wait=wait, python=python)


def stop(run, dry_run=False):
    """End a paused run by the user's choice. A dry run checks and says what it would
    do, changing nothing (review finding R3-3)."""
    if run.state.get("status") != sr.PAUSED:
        raise RunRefused([f"only a paused run can be stopped (this one is "
                          f"{run.state.get('status')})"])
    if dry_run:
        print(f"[DRY RUN] would end run {run.state['run_id']}, paused "
              f"{run.state.get('pause')}, as stopped-by-user, write its report and "
              "append LOG.md. Nothing was changed.", file=sys.stderr)
        return
    run.end(sr.STOPPED_BY_USER, {"pause": run.state.get("pause")})
