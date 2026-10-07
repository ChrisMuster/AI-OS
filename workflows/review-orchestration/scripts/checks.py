#!/usr/bin/env python3
"""
checks.py - the mechanical checks a run makes before every review (plan section 10.4).

Four checks, run by the script under the project's Python, never by a model:

  close-out   ``workflows/close-out/scripts/run.py --json`` at its default, affected
              scope. The verifier sees the whole working tree, so a run starts only
              from a tree where it is clean (``close_out_start``): every gate passed and
              nothing skipped. Then every gate that fails during the run, and every
              scope it reports skipped (``degraded``), is a finding: nothing the run
              did not cause can be there to excuse (review finding R5-3, the user's
              re-plan of R2-5; R5-1).
  doc-sync    ``workflows/doc-sync-guard/scripts/run.py --json``. Every WARN about a
              directory inside the run's edit paths is a finding; one about a
              directory outside them is reported but is not a finding, since the
              builder could not act on it except by declining it for scope. A scan
              that did not run in full ("scan skipped", or DEGRADED) is a finding
              whatever the directory (R4-3).
  audit       ``workflows/audit/scripts/run.py --context`` over the directories the
              run changed. Every Failures and Warnings line is a finding, and so is a
              non-zero exit that reported none, since the audit exits non-zero only
              when it did not run to the end.
  acceptance  every command in the brief's Acceptance checks. A command that does not
              exit 0 is a finding. Commands are run without a shell, as the brief
              checker proved them to be single commands, and one naming ``python``
              runs under the project's Python.

A check that cannot run at all is a finding too: a check that did not run has not
passed. Every finding carries the reviewer's fields (title, severity, file, evidence,
fix), so the ledger labels both kinds the same way.

Every process started here runs on the host, so none is given the clean-copy marker
(``BOOK_DRAGON_CLEAN_COPY``) whatever the orchestrator's own environment holds:
with it, the audit and link checks would leave the personal-file checks to "the
host", which is where they are already running (plan 4.3).

Trusted host copies (plan 4.4). ``write_trusted_copies`` writes ``git archive`` of
the four host checks' workflow folders, at the run's start commit, into a folder
outside the project; ``trusted_host_checks`` runs them from there against the live
project (``BOOK_DRAGON_ROOT``), with bytecode off, and reads their results: the
targeted audit and doc-sync as above, ``personal_data`` (the file and the kind of
each hit, never the matched text) and ``link_check`` (each dead link). Built in
stage S3; the loop starts calling them in S6 (the user's decision A, 2026-10-07).
"""

import io
import json
import os
import shlex
import subprocess
import sys
import tarfile
from pathlib import Path

CHECK_TIMEOUT = 1800
EVIDENCE_TAIL = 1500
PYTHON_NAMES = {"python", "python3", "py", "python.exe"}

CLEAN_COPY_ENV = "BOOK_DRAGON_CLEAN_COPY"
ROOT_ENV = "BOOK_DRAGON_ROOT"

# The host checks run from trusted copies, each a whole workflow folder.
TRUSTED_WORKFLOWS = ("workflows/audit", "workflows/doc-sync-guard",
                     "workflows/personal-data-guard", "workflows/link-check")

# How an AI is told what a personal-data hit is: the guard's `kind`, in words. The
# matched text itself never leaves the round's personal-data.json.
PERSONAL_DATA_KINDS = {
    "email": "an email address",
    "home_path": "a personal home path",
    "os_username": "the OS username",
    "personal_name": "a personal name",
    "denylisted_term": "a denylisted term",
}


class TrustedCopyError(Exception):
    """The trusted copies could not be written."""


def _finding(title, severity, file, evidence, fix):
    return {"title": title, "severity": severity, "file": file,
            "evidence": evidence[-EVIDENCE_TAIL:], "fix": fix}


def host_env(extra=None):
    """The orchestrator's environment without the clean-copy marker, plus
    ``extra``. Every process the orchestrator starts on the host gets this."""
    env = {key: value for key, value in os.environ.items()
           if key.upper() != CLEAN_COPY_ENV}
    env.update(extra or {})
    return env


def _run(root, argv, timeout=CHECK_TIMEOUT, env=None):
    try:
        result = subprocess.run(argv, cwd=str(root), capture_output=True,
                                encoding="utf-8", errors="replace", timeout=timeout,
                                env=env if env is not None else host_env())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "", f"{type(exc).__name__}: {exc}"
    return result.returncode, result.stdout, result.stderr


def _inside(directory, edit_paths):
    """Whether a directory named in a finding lies inside one of the edit paths."""
    directory = directory.strip().strip("`").rstrip("/")
    return any(directory == edit.rstrip("/") or directory.startswith(edit.rstrip("/") + "/")
               for edit in edit_paths)


def _close_out_result(root, python):
    """The close-out verifier's JSON result, or (None, why it could not be read)."""
    code, out, err = _run(root, [python, "workflows/close-out/scripts/run.py", "--json"])
    try:
        return json.loads(out), None
    except ValueError:
        return None, (out + err).strip() or f"exit {code}"


def _problems(result):
    """Everything in a close-out result that is not clean: each failed gate and each
    skipped (degraded) scope, as (title, evidence) pairs."""
    problems = []
    for gate in result.get("gates", []):
        if not gate.get("passed"):
            evidence = {key: gate.get(key) for key in ("name", "detail", "blocking")
                        if gate.get(key)}
            failing = [f.get("file") for f in gate.get("files") or [] if not f.get("passed")]
            if failing:
                evidence["failing_files"] = failing
            problems.append((f"close-out gate failed: {gate.get('name')}",
                             json.dumps(evidence)))
    for entry in result.get("degraded") or []:
        # A scope the verifier skipped is a check that did not run, even when every
        # gate is marked passed (review finding R5-1).
        problems.append(("close-out skipped part of a check",
                         entry if isinstance(entry, str) else json.dumps(entry)))
    return problems


def close_out_start(root, python=None):
    """The close-out verifier at the start of a run. A run starts only from a clean
    tree (the user's re-plan of R2-5 after R5-3, 2026-10-01): every gate passed and
    no scope skipped. Returns ``{"clean": bool, "status": ..., "problems": [...]}``;
    a result that cannot be read is not clean."""
    result, why = _close_out_result(root, python or sys.executable)
    if result is None:
        return {"clean": False, "status": "unreadable",
                "problems": [f"the close-out verifier's result could not be read: "
                             f"{why[-300:]}"]}
    problems = [f"{title}: {evidence[:300]}" for title, evidence in _problems(result)]
    return {"clean": not problems, "status": result.get("status"), "problems": problems}


def close_out(root, python):
    """Every failed gate and every skipped scope is a finding. The run started from a
    clean tree, so none of them predates it."""
    result, why = _close_out_result(root, python)
    if result is None:
        return ([_finding("close-out verifier did not run", "blocker", "(close-out)",
                          why, "Make the close-out verifier run and pass.")],
                [f"close-out verifier: could not read its result ({why[-300:]})"])
    lines = [f"close-out verifier: {result.get('status')}"]
    for gate in result.get("gates", []):
        lines.append(f"  {gate.get('name')}: {'PASS' if gate.get('passed') else 'FAIL'}"
                     f" - {gate.get('detail')}")
    findings = [_finding(title, "blocker", "(close-out)", evidence,
                         "Make the check pass, and run in full.")
                for title, evidence in _problems(result)]
    for entry in result.get("degraded") or []:
        lines.append(f"  skipped: {entry}")
    return findings, lines


def doc_sync(root, python, edit_paths, trusted=None):
    """``trusted``: run the trusted copy in that folder rather than the live tree's."""
    if trusted is None:
        code, out, err = _run(root, [python, "workflows/doc-sync-guard/scripts/run.py",
                                     "--json"])
    else:
        code, out, err = _trusted_run(root, python, trusted, "workflows/doc-sync-guard",
                                      ["--json"])
    try:
        # Without --strict the guard exits 0 whenever it finishes, so any other
        # exit is a check that did not finish, whatever it printed (R7-1).
        if code != 0:
            raise ValueError(f"exit {code}")
        reported = json.loads(out).get("findings", [])
    except (ValueError, AttributeError):
        detail = f"exit {code}: " + ((out + err).strip() or "no output")
        return ([_finding("doc-sync did not run", "blocker", "(doc-sync)", detail,
                          "Make doc-sync run.")],
                [f"doc-sync: could not read its result ({detail[-300:]})"])
    findings, lines = [], []
    for item in reported:
        severity, message = item.get("severity"), str(item.get("message", ""))
        where = message.split(":", 1)[0].strip()
        if severity == "DEGRADED" or message.lower().startswith("scan skipped"):
            # The scan, or part of it, did not run: a finding whatever the directory,
            # since a check that did not run has not passed (review finding R4-3).
            lines.append(f"doc-sync did not run in full ({severity}): {message}")
            findings.append(_finding(f"doc-sync did not run in full: {message[:180]}",
                                     "blocker", "(doc-sync)", message,
                                     "Make doc-sync run to the end."))
        elif severity == "WARN" and _inside(where, edit_paths):
            lines.append(f"doc-sync WARN: {message}")
            findings.append(_finding(f"doc-sync: {message[:200]}", "major", where,
                                     message, "Bring CONTEXT.md and LOG.md into line "
                                     "with the change."))
        elif severity == "WARN":
            lines.append(f"doc-sync WARN outside this run's edit paths (not a finding): "
                         f"{message}")
        else:
            lines.append(f"doc-sync {severity}: {message}")
    if not reported:
        lines.append("doc-sync: clean")
    return findings, lines


def audit(root, python, directories, trusted=None):
    """``trusted``: run the trusted copy in that folder, read-only, rather than the
    live tree's."""
    if not directories:
        return [], ["targeted audit: no changed directory to check"]
    if trusted is None:
        code, out, err = _run(root, [python, "workflows/audit/scripts/run.py",
                                     "--context", *directories])
    else:
        code, out, err = _trusted_run(root, python, trusted, "workflows/audit",
                                      ["--context", *directories, "--read-only"])
    if code is None:
        return ([_finding("targeted audit did not run", "blocker", "(audit)", err,
                          "Make the audit run.")], [f"targeted audit: {err}"])
    findings, lines, section = [], [], None
    for line in out.splitlines():
        if line.startswith("## "):
            section = line[3:].strip().lower()
        elif section in ("failures", "warnings") and line.startswith("- "):
            text = line[2:]
            lines.append(f"audit {section}: {text}")
            file = text.split("`")[1] if text.count("`") >= 2 else "(audit)"
            findings.append(_finding(f"audit: {text[:200]}",
                                     "blocker" if section == "failures" else "major",
                                     file, text, "Resolve the audit finding."))
    if code != 0 and not findings:
        # The audit exits non-zero only when it fails to run to the end. A crash
        # before any report section is a check that did not pass (review finding R1-4).
        detail = (err.strip() or out.strip() or "no output")
        lines.append(f"targeted audit failed (exit {code}): {detail[-300:]}")
        return ([_finding(f"targeted audit failed (exit {code})", "blocker", "(audit)",
                          detail, "Make the audit run to the end.")], lines)
    lines.append(f"targeted audit over {len(directories)} changed director"
                 f"{'y' if len(directories) == 1 else 'ies'}: "
                 f"{'clean' if not findings else f'{len(findings)} finding(s)'}")
    return findings, lines


def acceptance(root, python, commands):
    findings, lines = [], []
    for command in commands:
        argv = shlex.split(command)
        if argv and argv[0].lower() in PYTHON_NAMES:
            argv[0] = python
        code, out, err = _run(root, argv)
        ok = code == 0
        lines.append(f"acceptance check {'PASS' if ok else 'FAIL'} "
                     f"(exit {code}): {command}")
        if not ok:
            findings.append(_finding(f"acceptance check failed: {command[:180]}",
                                     "blocker", "(acceptance check)",
                                     (out + err).strip() or f"exit {code}",
                                     "Make the acceptance check exit 0."))
    return findings, lines


# ---------------------------------------------------------------------------
# Trusted host copies (plan 4.4)
# ---------------------------------------------------------------------------
def write_trusted_copies(root, head, dest, workflows=TRUSTED_WORKFLOWS):
    """Write ``git archive <head>`` of each workflow folder into ``dest``, keeping
    project paths (``dest/workflows/audit/...``). Refuses a workflow folder that
    already exists under ``dest`` (a trusted copy is never overwritten) and one
    the commit does not hold. Returns ``dest``."""
    dest = Path(dest)
    existing = [w for w in workflows if (dest / w).exists()]
    if existing:
        raise TrustedCopyError(f"trusted copy already exists: {', '.join(existing)}")
    try:
        result = subprocess.run(["git", "archive", "--format=tar", head, "--", *workflows],
                                cwd=str(root), capture_output=True, timeout=120,
                                env=host_env())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TrustedCopyError(f"git archive could not run: {exc}") from exc
    if result.returncode != 0:
        raise TrustedCopyError("git archive failed: "
                               + result.stderr.decode("utf-8", "replace").strip())
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
        archive.extractall(dest, filter="data")
    missing = [w for w in workflows if not (dest / w / "scripts" / "run.py").is_file()]
    if missing:
        raise TrustedCopyError(f"the commit holds no {', '.join(missing)} run.py")
    return dest


def trusted_env(root):
    """A trusted host check's environment: the project named by BOOK_DRAGON_ROOT,
    no bytecode written (in the copy or the project), and no clean-copy marker."""
    return host_env({ROOT_ENV: str(root), "PYTHONDONTWRITEBYTECODE": "1"})


def _trusted_run(root, python, trusted, workflow, args):
    script = Path(trusted) / workflow / "scripts" / "run.py"
    return _run(root, [python, str(script), *args], env=trusted_env(root))


def personal_data(root, python, trusted, round_dir):
    """The trusted personal-data guard. A FAIL is a blocker and a WARN major, each
    naming the file and the kind of hit from the guard's ``file`` and ``kind``
    fields, never the matched text, and never parsing the message. The guard's
    whole output goes to ``round_dir/personal-data.json`` and nowhere else; a
    result that cannot be read, or a hit missing either field, is a blocker
    naming only the check."""
    code, out, err = _trusted_run(root, python, trusted, "workflows/personal-data-guard",
                                  ["--check", "--json"])
    round_dir = Path(round_dir)
    round_dir.mkdir(parents=True, exist_ok=True)
    with open(round_dir / "personal-data.json", "w", encoding="utf-8",
              newline="\n") as handle:
        handle.write(json.dumps({"exit_code": code, "stdout": out, "stderr": err},
                                indent=2) + "\n")
    where = "The guard's output is in this round's personal-data.json."
    try:
        payload = json.loads(out)
        reported = payload["findings"]
        if not isinstance(reported, list):
            raise TypeError("findings is not a list")
    except (ValueError, KeyError, TypeError):
        return ([_finding("personal-data check did not run", "blocker", "(personal-data)",
                          f"Its result could not be read (exit {code}). {where}",
                          "Make the personal-data check run.")],
                [f"personal-data check: could not read its result (exit {code})"])
    # The guard exits 0 with no FAIL and 1 with at least one; any other pairing is
    # a check that did not finish as it reports (R7-1).
    has_fail = any(isinstance(item, dict) and item.get("severity") == "FAIL"
                   for item in reported)
    if (code, has_fail) not in ((0, False), (1, True)):
        return ([_finding("personal-data check did not run", "blocker", "(personal-data)",
                          f"It exited {code}, which does not match its result. {where}",
                          "Make the personal-data check run.")],
                [f"personal-data check: exit {code} does not match its result"])
    # A pass needs the guard's own statement that it scanned the files (R9-1): a
    # scan git could not list files for exits 0 with no finding but a note.
    if payload.get("scanned") is not True:
        return ([_finding("personal-data check did not run", "blocker", "(personal-data)",
                          f"It does not report a completed scan. {where}",
                          "Make the personal-data check scan the project's files.")],
                ["personal-data check: no completed scan reported"])
    findings, lines, incomplete = [], [], 0
    for item in reported:
        severity = item.get("severity") if isinstance(item, dict) else None
        if severity not in ("FAIL", "WARN"):
            continue
        file, kind = item.get("file"), item.get("kind")
        if not isinstance(file, str) or not file or kind not in PERSONAL_DATA_KINDS:
            incomplete += 1
            continue
        what = PERSONAL_DATA_KINDS[kind]
        lines.append(f"personal-data {severity}: {what} in {file}")
        findings.append(_finding(
            f"personal data: {what} in {file}",
            "blocker" if severity == "FAIL" else "major", file,
            f"The personal-data guard reports {what} in {file} ({severity}). The "
            f"matched text is not shown. {where}",
            f"Remove {what} from {file}, or keep it only in a gitignored file."))
    if incomplete:
        lines.append(f"personal-data check: {incomplete} hit(s) without a file or kind")
        findings.append(_finding(
            "personal-data check reported a hit it did not place", "blocker",
            "(personal-data)",
            f"{incomplete} hit(s) carried no file or no known kind. {where}",
            "Make the personal-data check name the file and kind of every hit."))
    if not findings:
        lines.append("personal-data: clean")
    return findings, lines


def link_check(root, python, trusted):
    """The trusted link check: each dead link is a major finding naming its file
    and target; a result that cannot be read is a blocker."""
    code, out, err = _trusted_run(root, python, trusted, "workflows/link-check",
                                  ["--audit", "--no-log", "--json"])
    try:
        # The link check exits 0 whenever it finishes; dead links are in the JSON.
        # Any other exit is a check that did not finish (R7-1).
        if code != 0:
            raise ValueError(f"exit {code}")
        dead = json.loads(out)["dead_links"]
        if not isinstance(dead, list) or not all(
                isinstance(d, dict) and isinstance(d.get("file"), str)
                and isinstance(d.get("target"), str) for d in dead):
            raise TypeError("dead_links is not a list of file and target")
    except (ValueError, KeyError, TypeError):
        detail = f"exit {code}: " + ((out + err).strip() or "no output")
        return ([_finding("link check did not run", "blocker", "(link check)", detail,
                          "Make the link check run.")],
                [f"link check: could not read its result ({detail[-300:]})"])
    findings = [_finding(f"dead link: [[{d['target']}]] in {d['file']}", "major",
                         d["file"], f"{d['file']} links to [[{d['target']}]], which "
                         "points to no file.",
                         "Point the link at an existing file, or remove it.")
                for d in dead]
    lines = ([f"link check: dead [[{d['target']}]] in {d['file']}" for d in dead]
             or ["link check: clean"])
    return findings, lines


def trusted_host_checks(root, python, trusted, edit_paths, directories, round_dir):
    """The four trusted host checks, in plan 4.4's order: the targeted audit,
    doc-sync, the personal-data guard and the link check. Returns (findings, text)
    as run_all does."""
    findings, lines = [], []
    for part in (audit(root, python, directories, trusted=trusted),
                 doc_sync(root, python, edit_paths, trusted=trusted),
                 personal_data(root, python, trusted, round_dir),
                 link_check(root, python, trusted)):
        findings += part[0]
        lines += part[1]
    return findings, "\n".join(lines)


def run_all(root, edit_paths, directories, commands, python=None):
    """Run the four checks. Returns (findings, text for the prompts and the report)."""
    python = python or sys.executable
    findings, lines = [], []
    for part in (close_out(root, python), doc_sync(root, python, edit_paths),
                 audit(root, python, directories), acceptance(root, python, commands)):
        findings += part[0]
        lines += part[1]
    return findings, "\n".join(lines)
