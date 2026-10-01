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
"""

import json
import shlex
import subprocess
import sys

CHECK_TIMEOUT = 1800
EVIDENCE_TAIL = 1500
PYTHON_NAMES = {"python", "python3", "py", "python.exe"}


def _finding(title, severity, file, evidence, fix):
    return {"title": title, "severity": severity, "file": file,
            "evidence": evidence[-EVIDENCE_TAIL:], "fix": fix}


def _run(root, argv, timeout=CHECK_TIMEOUT):
    try:
        result = subprocess.run(argv, cwd=str(root), capture_output=True,
                                encoding="utf-8", errors="replace", timeout=timeout)
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


def doc_sync(root, python, edit_paths):
    code, out, err = _run(root, [python, "workflows/doc-sync-guard/scripts/run.py",
                                 "--json"])
    try:
        reported = json.loads(out).get("findings", [])
    except (ValueError, AttributeError):
        detail = (out + err).strip() or f"exit {code}"
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


def audit(root, python, directories):
    if not directories:
        return [], ["targeted audit: no changed directory to check"]
    code, out, err = _run(root, [python, "workflows/audit/scripts/run.py", "--context",
                                 *directories])
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


def run_all(root, edit_paths, directories, commands, python=None):
    """Run the four checks. Returns (findings, text for the prompts and the report)."""
    python = python or sys.executable
    findings, lines = [], []
    for part in (close_out(root, python), doc_sync(root, python, edit_paths),
                 audit(root, python, directories), acceptance(root, python, commands)):
        findings += part[0]
        lines += part[1]
    return findings, "\n".join(lines)
