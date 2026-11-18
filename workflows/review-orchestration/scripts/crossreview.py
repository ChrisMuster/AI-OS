#!/usr/bin/env python3
"""One conversation cross-review round, entered through start.py --review."""

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
STARTER = "python workflows/review-orchestration/scripts/start.py --review"
MARKER = "BOOK_DRAGON_REVIEW_ORCHESTRATION_STARTER"
HEADINGS = ("Item", "Kind", "Under review", "Not under review", "Checks",
            "Context directories", "What changed", "Worth challenging")
PACKET_HEADINGS = ("Open - plan readiness", "Open - review process",
                   "Addressed - plan readiness", "Addressed - review process")
LABEL = re.compile(r"R([1-9][0-9]*)(?:-([1-9][0-9]*))?")
FINDING = re.compile(r"^(?:- \*\*|###\s+)(R[1-9][0-9]*(?:-[1-9][0-9]*)?)\b")
REPAIR = re.compile(r"^\s*Repair \((\d{4}-\d{2}-\d{2})\):\s*\S")
ITEM = re.compile(r"^[a-z0-9][a-z0-9_]*$")


class Refused(Exception):
    """A refusal before the first capture, with a corrective instruction."""


class RoundError(Exception):
    """A round step failed after the first capture."""


def _refuse(where, reason, correction):
    raise Refused(f"{where}: {reason}; write {correction}")


def command(argv, *, cwd=ROOT, check=True, binary=False, merge_stderr=True):
    """Run one host command, retaining the last 1,200 characters on a failure.

    With ``merge_stderr`` false, stderr is kept apart so ``stdout`` holds only the
    command's output: git prints warnings such as the line-ending one under
    ``core.autocrlf`` to stderr, and merged they were read as file names."""
    try:
        result = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
                                text=not binary,
                                encoding=None if binary else "utf-8",
                                errors=None if binary else "replace",
                                stdin=subprocess.DEVNULL)
    except OSError as exc:
        raise RoundError(f"{' '.join(map(str, argv))}: {exc}") from exc
    if check and result.returncode:
        shown = result.stdout + (result.stderr or "" if not merge_stderr else "")
        raise RoundError(f"{' '.join(map(str, argv))} exited {result.returncode}: "
                         + shown[-1200:])
    return result


def git(root, *args, check=True):
    """A git command whose stdout is git's output alone, warnings kept out of it."""
    return command(["git", *map(str, args)], cwd=root, check=check, merge_stderr=False)


def personal(root, git_dir, *args, check=True):
    return git(root, f"--git-dir={git_dir}", *args, check=check)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _parts(path, root, line, *, exists=True, kind=None):
    if not path or path.startswith("/") or path.startswith("./") or "\\" in path:
        _refuse(line, f"path {path!r} is not project-relative with forward slashes",
                "the path without a leading ./ and with forward slashes")
    bits = path.split("/")
    if any(bit in ("", ".", "..") for bit in bits):
        _refuse(line, f"path {path!r} has an empty, . or .. component",
                "one project-relative path without those components")
    if any(bit == ".env" or bit.startswith(".env.") for bit in bits):
        _refuse(line, f"path {path!r} names a .env file", "a path with no .env component")
    here = Path(root)
    for index, bit in enumerate(bits):
        if not here.is_dir():
            if exists:
                _refuse(line, f"path {path!r} does not exist", "a path that exists")
            break
        names = {entry.name: entry for entry in here.iterdir()}
        if bit not in names:
            variant = next((name for name in names if name.casefold() == bit.casefold()), None)
            if variant is not None:
                _refuse(line, f"path {path!r} has the wrong capitals", repr(
                    "/".join([*bits[:index], variant, *bits[index + 1:]])))
            if exists:
                _refuse(line, f"path {path!r} does not exist", "a path that exists")
            break
        here = names[bit]
        if here.is_symlink():
            _refuse(line, f"path {path!r} uses a symbolic link", "a real path")
    if exists and not here.exists():
        _refuse(line, f"path {path!r} does not exist", "a path that exists")
    if kind == "file" and not here.is_file():
        _refuse(line, f"path {path!r} is not a file", "one existing file")
    if kind == "dir" and not here.is_dir():
        _refuse(line, f"path {path!r} is not a directory", "one existing directory")
    return here


def read_brief(root, relative):
    """Read the eight sections and reject every ambiguous list or path spelling."""
    path = _parts(relative, root, "review brief", kind="file")
    lines = path.read_text(encoding="utf-8").splitlines()
    sections, section_lines, current, opened, title = {}, {}, None, None, False
    for number, line in enumerate(lines, 1):
        fence = re.match(r"^(`{3,})", line)
        if opened is not None:
            if re.fullmatch(r"`{" + str(opened[1]) + r",}", line):
                opened = None
            elif line.startswith("## "):
                sections[current].append((number, line))
            continue
        if fence:
            opened = (number, len(fence.group(1)))
            if current is not None:
                sections[current].append((number, line))
            continue
        if line.startswith("## "):
            heading = line[3:].strip()
            if heading not in HEADINGS:
                _refuse(f"line {number}", f"unknown section {heading!r}",
                        "one of: " + ", ".join(HEADINGS))
            if heading in sections:
                _refuse(f"line {number}", f"section {heading!r} appears twice",
                        "that heading once")
            sections[heading], section_lines[heading], current = [], number, heading
        elif current is None:
            if line.strip() and not (not title and line.startswith("# ")):
                _refuse(f"line {number}", "text before the first section",
                        "only one # title and blank lines there")
            title |= line.startswith("# ")
        else:
            sections[current].append((number, line))
    if opened is not None:
        _refuse(f"line {opened[0]}", "code fence is not closed",
                "a closing line of at least as many backticks")
    for heading in HEADINGS:
        if heading not in sections:
            _refuse(f"line {len(lines) + 1}", f"section {heading!r} is missing",
                    f"## {heading}")
    data = {}
    for heading, body in sections.items():
        entries = [(number, line.strip()) for number, line in body if line.strip()]
        if not entries:
            _refuse(f"line {section_lines[heading]}", "empty section",
                    "None if it has no entries")
        if heading in ("Item", "Kind"):
            if len(entries) != 1:
                _refuse(f"line {entries[1][0]}", "more than one line", "one line")
            data[heading] = entries[0][1]
        elif heading in ("Under review", "Not under review", "Checks", "Context directories"):
            if entries == [(entries[0][0], "None")]:
                data[heading] = []
                continue
            if any(value in ("none", "None.", "N/A", "None") for _, value in entries):
                _refuse(f"line {next(n for n, value in entries if value in ('none', 'None.', 'N/A', 'None'))}", "invalid empty-list spelling",
                        "only None on a line of its own, or actual entries")
            seen, values = set(), []
            for number, value in entries:
                name, reason = (value.split(" - ", 1) + [None])[:2] if heading == "Not under review" else (value, None)
                if heading == "Not under review" and (reason is None or not reason.strip()):
                    _refuse(f"line {number}", "excluded path has no reason",
                            "<path> - <reason>")
                if name in seen:
                    _refuse(f"line {number}", f"duplicate path {name!r}", "each path once")
                seen.add(name)
                _parts(name, root, f"line {number}", exists=heading != "Not under review",
                       kind="file" if heading == "Checks" else
                            "dir" if heading == "Context directories" else None)
                values.append({"path": name, "reason": reason, "line": number})
            data[heading] = values
        else:
            data[heading] = "\n".join(line for _, line in body).strip()
            if not data[heading]:
                _refuse(f"line {section_lines[heading]}", "empty prose", "text or None")
            if (heading == "What changed" and data[heading] == "None") or data[heading] in ("none", "None.", "N/A"):
                _refuse(f"line {next(n for n, line in body if line.strip())}",
                        "invalid prose or empty-section spelling",
                        "a description of the change" if heading == "What changed" else "None on its own line or prose")
    if not ITEM.fullmatch(data["Item"]):
        _refuse(f"line {next(n for n, line in sections['Item'] if line.strip())}", "invalid item",
                "one lower-case item using a-z, digits or _")
    if data["Kind"] not in ("code", "plan"):
        _refuse(f"line {next(n for n, line in sections['Kind'] if line.strip())}", "invalid kind", "code or plan")
    if not data["Under review"]:
        _refuse(f"line {section_lines['Under review']}", "no path", "at least one path")
    overlap = {e["path"] for e in data["Under review"]} & {e["path"] for e in data["Not under review"]}
    if overlap:
        _refuse(f"line {section_lines['Not under review']}", "path also under review: " + ", ".join(sorted(overlap)),
                "a path in only one of the two lists")
    if data["Kind"] == "plan":
        if len(data["Under review"]) != 1:
            _refuse(f"line {section_lines['Under review']}", "a plan needs exactly one document", "one document")
        document = data["Under review"][0]["path"]
        _parts(document, root, f"line {data['Under review'][0]['line']}", kind="file")
        if git(root, "ls-files", "--error-unmatch", "--", document, check=False).returncode == 0:
            _refuse(f"line {data['Under review'][0]['line']}", "the plan is tracked by the public repository",
                    "a personal document")
        for heading in ("Not under review", "Checks", "Context directories"):
            if data[heading]:
                _refuse(f"line {data[heading][0]['line']}", "a plan takes no entries", "None")
    data["path"], data["text"] = relative, path.read_text(encoding="utf-8")
    return data


def _gather(root):
    source = ROOT / "workflows/handoff/scripts/gather.py"
    spec = importlib.util.spec_from_file_location("crossreview_gather", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def packet_template(item):
    name = item.replace("_", "-")
    return (f"---\nname: {name}-review-packet\n"
            f"description: Live review packet for the {item} item.\n"
            "metadata:\n  type: project\n---\n\n"
            f"# {item.replace('_', ' ')} - Review Packet\n\n"
            "Cross-review mode writes its findings here.\n\n"
            + "\n\n".join(f"## {heading}\n\nNone." for heading in PACKET_HEADINGS) + "\n")


def packet_status(root, item):
    """Check the reader's pooled groups and every addressed finding's own repair."""
    path = Path(root) / "memory" / f"{item}_review_packet.md"
    if not path.exists():
        return path, None, []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _refuse(path, f"packet cannot be read: {exc}", "a readable UTF-8 packet")
    groups = {}
    current, active = None, None
    labels = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            heading = line[3:].strip()
            if heading in PACKET_HEADINGS:
                if heading in groups:
                    _refuse(f"{path}:{number}", f"heading {heading!r} appears twice",
                            "that heading once")
                groups[heading] = {"shapes": set(), "findings": []}
            current, active = heading, None
            continue
        if current not in PACKET_HEADINGS:
            continue
        finding = FINDING.match(line)
        if finding:
            shape = "heading" if line.startswith("###") else "bullet"
            groups[current]["shapes"].add(shape)
            active = {"label": finding.group(1), "repair": False}
            groups[current]["findings"].append(active)
            labels.append(active["label"])
        elif line.lstrip().startswith("Repair"):
            match = REPAIR.match(line)
            if active is None:
                _refuse(f"{path}:{number}", "orphan Repair line",
                        "a Repair line after its own finding")
            if match is None:
                _refuse(f"{path}:{number}", "undated or empty Repair line",
                        "Repair (YYYY-MM-DD): what was done")
            try:
                date.fromisoformat(match.group(1))
            except ValueError:
                _refuse(f"{path}:{number}", "invalid Repair date",
                        "a real YYYY-MM-DD date")
            active["repair"] = True
    for heading in PACKET_HEADINGS:
        if heading not in groups:
            _refuse(path, f"heading {heading!r} is missing", f"## {heading}")
    for pair in (PACKET_HEADINGS[:2], PACKET_HEADINGS[2:]):
        if len(groups[pair[0]]["shapes"] | groups[pair[1]]["shapes"]) > 1:
            _refuse(path, f"mixed bullet and heading findings across {pair[0]} and {pair[1]}",
                    "one finding shape across both sections")
    for heading in PACKET_HEADINGS[2:]:
        for entry in groups[heading]["findings"]:
            if not entry["repair"]:
                _refuse(path, f"addressed finding {entry['label']} lacks its repair",
                        f"Repair (YYYY-MM-DD): after {entry['label']}")
    reader = next((entry for entry in _gather(root).review_packets(root)
                   if entry["path"] == path.relative_to(root).as_posix()), None)
    if reader is None or reader["open"] is None or reader["addressed"] is None:
        _refuse(path, "the handoff reader did not read both packet groups",
                "a readable packet with four recognised headings")
    if reader["open"]:
        _refuse(path, f"{len(reader['open'])} open findings remain",
                "a dated Repair line for each finding moved to Addressed")
    return path, text, labels


def round_records(root):
    """Read all earlier records, including interrupted ones, with strict timestamps."""
    records, times = [], {}
    runs = Path(root) / "workflows/review-orchestration/runs"
    for path in sorted(runs.glob("review-*/round.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or any(not value.get(key) for key in
                                                  ("item", "kind", "started_at")):
                raise ValueError("item, kind or started_at is missing")
            stamp = datetime.fromisoformat(value["started_at"])
            if stamp.tzinfo is None:
                raise ValueError("started_at needs its offset")
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            _refuse(path, f"damaged round record: {exc}", "a valid round.json")
        key = (value["item"], stamp)
        if key in times:
            _refuse(path, f"started_at ties {times[key]}", "a distinct start time")
        times[key] = path
        value["_path"] = path
        records.append(value)
    return records


def choose_round(item, labels, records, given=None):
    highest = max([int(m.group(1)) for label in labels
                   if (m := LABEL.fullmatch(label))] +
                  [int(r.get("round", 0)) for r in records
                   if r["item"] == item and r.get("stop") in ("clean", "findings")
                   and str(r.get("round", "0")).isdigit()] + [0])
    if given is not None and given <= highest:
        _refuse("--round", f"{given} does not exceed R{highest}",
                f"a number above {highest}")
    return given if given is not None else highest + 1


def resolve_base(root, git_dir, base, kind):
    if base is None:
        return None if kind == "plan" else git(root, "rev-parse", "HEAD").stdout.strip()
    args = (f"--git-dir={git_dir}",) if kind == "plan" else ()
    result = git(root, *args, "rev-parse", "--verify", f"{base}^{{commit}}", check=False)
    if result.returncode or not re.fullmatch(r"[0-9a-f]{40,64}", result.stdout.strip()):
        _refuse("--base", f"{base!r} is not one unambiguous commit",
                "a resolvable commit, branch or tag")
    return result.stdout.strip()


def _cover(path, brief):
    matches = []
    for kind in ("Under review", "Not under review"):
        for entry in brief[kind]:
            name = entry["path"]
            if path == name or path.startswith(name.rstrip("/") + "/"):
                matches.append((len(name), kind, name))
    return max(matches, default=None)


def code_diff(root, brief, base, *, allow_empty=False):
    """Assign each changed file before diffing exactly the reviewed files."""
    tracked = [p for p in git(root, "diff", "--name-only", base).stdout.splitlines() if p]
    fresh = [p for p in git(root, "ls-files", "--others", "--exclude-standard").stdout.splitlines() if p]
    changed = sorted(set(tracked + fresh))
    missing = [p for p in changed if _cover(p, brief) is None]
    if missing:
        _refuse("## Under review / ## Not under review",
                "changed paths are uncovered: " + ", ".join(missing),
                "each path under one of the two lists")
    reviewed = [p for p in changed if _cover(p, brief)[1] == "Under review"]
    if not reviewed:
        if allow_empty:
            return "", [], {}
        _refuse("## Under review", "there is no changed file to review",
                "a path covering changed work")
    diff = git(root, "diff", base, "--", *reviewed).stdout
    for path in reviewed:
        if path not in fresh:
            continue
        raw = (Path(root) / path).read_bytes()
        try:
            text = raw.decode("utf-8")
            if b"\0" in raw:
                raise UnicodeError("NUL")
        except UnicodeError:
            diff += f"\nBinary file {path} ({len(raw)} bytes); contents are not shown.\n"
        else:
            diff += f"\n--- /dev/null\n+++ b/{path}\n{text}"
    inventory = {p: sha(Path(root) / p) if (Path(root) / p).is_file() else "deleted"
                 for p in reviewed}
    return f"Diff against public commit {base}\n{diff}", reviewed, inventory


def tree_state(root):
    """The three public-tree views around host checks."""
    status = git(root, "status", "--porcelain", "--untracked-files=all").stdout
    diff = command(["git", "diff", "HEAD"], cwd=root, binary=True).stdout
    names = git(root, "ls-files", "--others", "--exclude-standard").stdout.splitlines()
    return status, hashlib.sha256(diff).hexdigest(), {
        name: sha(Path(root) / name) for name in names}


GUARDS = ("doc-sync-guard", "encoding-guard", "ai-style-guard", "personal-data-guard")
BLOCKING = ("WARN", "FAIL", "DEGRADED")
# Messages a guard reports, at INFO, when part or all of its check did not run:
# doc-sync's and ai-style's git failure, and ai-style's missing or unreadable
# configuration (code review R10-2).
NOT_RUN = ("scan skipped", "config not found", "config unreadable")
AUDIT_COUNTS = ("Failures", "Warnings", "Degraded (did not run)")


def judge(kind, result):
    """Why a step 5 command did not pass, or None when it passed.

    A guard or the targeted audit exits 0 when it reports a WARN (the audit always
    exits 0), so an exit code alone lets drift through. Each is judged as a run's
    checks judge it (checks.py): a guard's ``--json`` findings, where any WARN, FAIL
    or DEGRADED, or a scan that did not run, refuses; the audit's three counts, any
    of them above 0 refusing. A personal-data hit is named by its file and kind,
    never its message, which carries the matched text."""
    if kind == "exit":
        return None if result.returncode == 0 else f"exited {result.returncode}"
    output = result.stdout or ""
    if kind == "audit":
        counts = {name: re.search(rf"^\*\*{re.escape(name)}:\*\* (\d+)\s*$", output, re.M)
                  for name in AUDIT_COUNTS}
        missing = [name for name, found in counts.items() if found is None]
        if missing:
            return (f"exited {result.returncode}; its report has no "
                    + ", ".join(missing) + " count")
        raised = [f"{name} {found.group(1)}" for name, found in counts.items()
                  if found.group(1) != "0"]
        if raised:
            return "reported " + ", ".join(raised)
        return f"exited {result.returncode}" if result.returncode else None
    try:
        payload = json.loads(output)
        findings = payload["findings"]
        if not isinstance(findings, list) or not all(isinstance(f, dict) for f in findings):
            raise TypeError("findings is not a list of objects")
    except (ValueError, KeyError, TypeError):
        return f"exited {result.returncode}; its JSON result could not be read"
    # personal-data states that it scanned; a pass needs that statement, as a run's
    # checks.personal_data requires (code review R10-2).
    if kind == "personal-data" and payload.get("scanned") is not True:
        return "it does not report a completed scan"
    blocking = []
    for finding in findings:
        severity, message = finding.get("severity"), str(finding.get("message", ""))
        if severity in BLOCKING or message.lower().startswith(NOT_RUN):
            blocking.append(f"{severity} {finding.get('file') or '(no file)'} "
                            f"({finding.get('kind') or 'no kind'})"
                            if kind == "personal-data" else f"{severity}: {message}")
    if blocking:
        return "reported " + "; ".join(blocking)
    return f"exited {result.returncode}" if result.returncode else None


def mechanical(root, brief, base, *, dry_run=False, python=sys.executable, run=command):
    commands = []
    if brief["Kind"] == "code":
        commands += [(True, "exit", [python, str(Path(root) / item["path"])])
                     for item in brief["Checks"]]
        if brief["Context directories"]:
            commands.append((False, "audit", [python, "workflows/audit/scripts/run.py",
                                              "--context",
                                              *[e["path"] for e in brief["Context directories"]],
                                              "--read-only"]))
        for guard in GUARDS:
            entry = [python, f"workflows/{guard}/scripts/run.py", "--check", "--json"]
            if base is not None and guard in ("doc-sync-guard", "ai-style-guard"):
                entry += ["--since", base]
            commands.append((False, "personal-data" if guard == "personal-data-guard"
                             else "guard", entry))
    else:
        commands = [(False, "exit", [python, "workflows/doc-verify/scripts/run.py", "--check",
                                     "--strict", brief["Under review"][0]["path"]])]
        packet = Path(root) / "memory" / f"{brief['Item']}_review_packet.md"
        if packet.exists():
            commands.append((False, "exit", [python, "workflows/doc-verify/scripts/run.py",
                                             "--check", "--strict",
                                             packet.relative_to(root).as_posix()]))
    before = tree_state(root)
    results = []
    try:
        for is_check, kind, argv in commands:
            name = " ".join(map(str, argv))
            if dry_run and is_check:
                results.append(f"NOT RUN: {name}")
                continue
            result = run(argv, cwd=root, check=False)
            failure = judge(kind, result)
            results.append(f"{'FAIL' if failure else 'PASS'}: {name}")
            if failure:
                shown = ("its output is not shown, since it carries the matched text"
                         if kind == "personal-data" else (result.stdout or "")[-1200:])
                _refuse(name, f"check {failure}: {shown}",
                        "a passing check before review")
    finally:
        after = tree_state(root)
        if before != after:
            changed = sorted(set(before[2]) ^ set(after[2]))
            changed += sorted(name for name in set(before[2]) & set(after[2])
                              if before[2][name] != after[2][name])
            detail = ("paths changed: " + ", ".join(changed) if changed else
                      "tracked file content or status moved")
            _refuse("mechanical checks", detail,
                    "checks that leave tracked and unignored files untouched")
    return results


def capture(root, git_dir, message, *, python=sys.executable, run=command,
            after=None):
    """The exact gated capture chain, with no extra staging command."""
    chain = ([python, "workflows/sync-architecture/scripts/preseed.py",
              "--compare-git-dir", str(git_dir)],
             [python, "workflows/sync-architecture/scripts/allowlist.py",
              "--stage", "--git-dir", str(git_dir)],
             ["git", f"--git-dir={git_dir}", "--work-tree=.", "add", "-u"])
    for argv in chain:
        run(argv, cwd=root)
    staged = run(["git", f"--git-dir={git_dir}", "diff", "--cached", "--quiet"],
                 cwd=root, check=False)
    if staged.returncode not in (0, 1):
        raise RoundError(f"capture staging check exited {staged.returncode}: " +
                         staged.stdout[-1200:])
    if staged.returncode == 1:
        run(["git", f"--git-dir={git_dir}", "--work-tree=.", "commit", "-m", message],
            cwd=root)
    if after is None:
        import loop
        after = loop.capture_problems
    problems = after(root, git_dir, python)
    if problems:
        raise RoundError(f"capture after-check: {problems}")
    run([python, "workflows/sync-architecture/scripts/allowlist.py",
         "--tracked-by-both", "--git-dir", str(git_dir)], cwd=root)
    return personal(root, git_dir, "rev-parse", "HEAD").stdout.strip()


def build_gate(root, git_dir, brief, records, *, run=command):
    """Five read-only links, in their specified order, returning the first failure."""
    if brief["Kind"] != "plan":
        raise Refused("--build-ready: write a plan review brief")
    document = brief["Under review"][0]["path"]
    matches = [r for r in records if r["item"] == brief["Item"]
               and r["kind"] == "plan" and r.get("document") == document]
    if not matches:
        return "link 1: no round record for this item and document"
    latest = max(matches, key=lambda r: datetime.fromisoformat(r["started_at"]))
    record = latest["_path"]
    relative = record.relative_to(root).as_posix()
    on_disk = git(root, "hash-object", relative).stdout.strip()
    in_history = personal(root, git_dir, "rev-parse", f"HEAD:{relative}",
                          check=False)
    if in_history.returncode or in_history.stdout.strip() != on_disk:
        return "link 2: the round record is not captured as it stands"
    if latest.get("stop") != "clean":
        return f"link 3: the latest round ended {latest.get('stop', 'unfinished')}"
    commit = latest.get("capture_commit")
    if not commit or personal(root, git_dir, "merge-base", "--is-ancestor", commit,
                              "HEAD", check=False).returncode:
        return "link 4: the reviewed capture is not in personal history"
    live = git(root, "hash-object", document).stdout.strip()
    captured = personal(root, git_dir, "rev-parse", f"{commit}:{document}",
                        check=False)
    if captured.returncode or live != captured.stdout.strip() or live != latest.get("document_blob"):
        return "link 5: the live document differs from the reviewed capture"
    return None


def plan_diff(root, git_dir, brief, records, capture_commit, explicit=None):
    document = brief["Under review"][0]["path"]
    base = explicit
    if base is None:
        matches = [r for r in records if r["item"] == brief["Item"]
                   and r["kind"] == "plan" and r.get("document") == document
                   and r.get("reviewer") == brief["reviewer"]
                   and r.get("stop") in ("clean", "findings") and r.get("capture_commit")]
        if matches:
            base = max(matches, key=lambda r: datetime.fromisoformat(r["started_at"]))["capture_commit"]
    if base is None:
        return None, "This is this reviewer's first read of the whole document."
    diff = personal(root, git_dir, "diff", base, capture_commit, "--", document).stdout
    return base, (f"Plan diff from {base} to {capture_commit} for {document}:\n{diff}\n"
                  "Read this diff, then read the whole document cold.")


def review_schema(kind, with_pattern=True):
    import stopreasons
    fields = ("title", "class", "section", "wrong", "consequence", "triage",
              "fix", "reraises")
    item = {"type": "object", "additionalProperties": False,
            "required": list(fields), "properties": {
                key: ({"type": "string", "enum": ["plan-readiness", "review-process"]}
                      if key == "class" else {"type": "string"}) for key in fields}}
    if with_pattern:
        item["properties"]["title"]["pattern"] = stopreasons.TITLE_PATTERN
    keys = ["summary", "findings"] + (["files_reviewed"] if kind == "code" else [])
    properties = {"summary": {"type": "string"},
                  "findings": {"type": "array", "items": item}}
    if kind == "code":
        properties["files_reviewed"] = {"type": "array", "items": {"type": "string"}}
    return {"type": "object", "additionalProperties": False,
            "required": keys, "properties": properties}


def validate_reply(reply, kind, labels, reviewed):
    import loop
    import stopreasons
    if not isinstance(reply, dict) or set(reply) != set(review_schema(kind)["required"]):
        raise RoundError("reviewer reply has missing or extra fields")
    if not isinstance(reply["summary"], str) or not reply["summary"].strip():
        raise RoundError("reviewer summary is blank")
    findings = reply["findings"]
    if not isinstance(findings, list):
        raise RoundError("reviewer findings is not a list")
    if kind == "code":
        try:
            loop.covered(reply, reviewed)
        except stopreasons.ReplyError as exc:
            raise RoundError(str(exc)) from exc
    fields = set(review_schema(kind)["properties"]["findings"]["items"]["required"])
    for number, finding in enumerate(findings, 1):
        if not isinstance(finding, dict) or set(finding) != fields:
            raise RoundError(f"finding {number} has missing or extra fields")
        for key in fields - {"reraises"}:
            if not isinstance(finding[key], str) or not finding[key].strip():
                raise RoundError(f"finding {number} has blank {key}")
        if finding["class"] not in ("plan-readiness", "review-process"):
            raise RoundError(f"finding {number} has unknown class")
        if stopreasons.title_has_label(finding["title"]):
            raise RoundError(f"finding {number} has a label in its title")
        if not isinstance(finding["reraises"], str) or (finding["reraises"] and
                                                        finding["reraises"] not in labels):
            raise RoundError(f"finding {number} re-raises an unknown label")
    return findings


def write_findings(packet_path, item, round_no, findings):
    import packet
    text = packet_path.read_text(encoding="utf-8")
    for kind, heading in (("plan-readiness", PACKET_HEADINGS[0]),
                          ("review-process", PACKET_HEADINGS[1])):
        pieces = []
        for number, finding in enumerate(findings, 1):
            if finding["class"] != kind:
                continue
            title = packet.clean(finding["title"]).rstrip(".")
            lines = [f"- **R{round_no}-{number} - {title}.**"]
            for label, key in (("Section", "section"), ("What is wrong", "wrong"),
                               ("What goes wrong", "consequence"), ("Triage", "triage"),
                               ("Suggested fix", "fix"), ("Re-raises", "reraises")):
                if finding[key]:
                    lines.append(f"  {label}: {packet.clean(finding[key])}")
            pieces.append("\n".join(lines))
        if pieces:
            start = text.index(f"## {heading}\n") + len(f"## {heading}\n")
            end = text.find("\n## ", start)
            if end < 0:
                end = len(text)
            section = text[start:end]
            if section.strip() not in ("None.", ""):
                raise RoundError(f"{heading} is not empty")
            text = text[:start] + "\n" + "\n\n".join(pieces) + "\n" + text[end:]
    packet_path.write_text(text, encoding="utf-8", newline="\n")
    reader = next((p for p in _gather(packet_path.parents[1]).review_packets(packet_path.parents[1])
                   if p["path"] == packet_path.relative_to(packet_path.parents[1]).as_posix()), None)
    if reader is None or len(reader["open"] or []) != len(findings):
        raise RoundError("the handoff reader did not count the written findings")


def consecutive_reraises(packet_path, round_no, findings):
    """Name new findings in a chain also raised in the preceding round."""
    parents = {}
    current = None
    for line in Path(packet_path).read_text(encoding="utf-8").splitlines():
        match = FINDING.match(line)
        if match:
            current = match.group(1)
            parents.setdefault(current, "")
        elif line.startswith("## "):
            current = None
        elif current and (match := re.match(r"^\s*Re-raises:\s*(R[1-9][0-9]*-[1-9][0-9]*)\s*$", line)):
            parents[current] = match.group(1)

    def first(label):
        seen = set()
        while parents.get(label) and label not in seen:
            seen.add(label)
            label = parents[label]
        return label

    previous = {first(label) for label in parents
                if LABEL.fullmatch(label) and int(LABEL.fullmatch(label).group(1)) == round_no - 1}
    return [f"R{round_no}-{number}" for number, finding in enumerate(findings, 1)
            if finding["reraises"] and first(f"R{round_no}-{number}") in previous]


def seed_claude(root, records):
    """The latest saved Claude reading from a round or ordinary run."""
    readings = []
    def stamp(value):
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return datetime.fromisoformat(value).timestamp()
        except (TypeError, ValueError):
            return 0.0
    for value in records:
        reading = (value.get("usage") or {}).get("claude_last_reading")
        when = (reading or {}).get("read_at") or value["started_at"]
        if reading:
            readings.append((stamp(when), reading))
    for path in (Path(root) / "workflows/review-orchestration/runs").glob("*/state.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        reading = value.get("claude_last_reading")
        if reading:
            readings.append((stamp(reading.get("read_at") or value.get("created")), reading))
    return max(readings, key=lambda pair: pair[0])[1] if readings else None


def model_offered(root, model):
    """Request-free Codex model list. A failed list refuses before capture."""
    from openai_codex import Codex, CodexConfig
    import providers
    with tempfile.TemporaryDirectory(prefix="book-dragon-review-models-") as empty:
        base = (providers.PLUGINS_OFF, providers.APPS_OFF)
        first = Codex(CodexConfig(cwd=empty, config_overrides=base))
        try:
            listed = first._client._request_raw("mcpServerStatus/list", {})
        finally:
            first.close()
        off = providers.servers_off(listed)
        client = Codex(CodexConfig(cwd=empty, config_overrides=(*base, *off)))
        try:
            status = client._client._request_raw("mcpServerStatus/list", {})
            if providers.counting_servers(status):
                raise RoundError("Codex model list has an active MCP server")
            offered = providers._model_ids(client.models())
        finally:
            client.close()
    return model in offered


class RoundUsage:
    """Only the state and callbacks the run's three usage functions read."""

    cross_review_round = True

    def __init__(self, state, settings, root, record=None):
        import limits
        self.state, self.settings, self.root = state, settings, Path(root)
        self.usage = limits.RunUsage()
        self.deps = SimpleNamespace(now=time.time)
        self.claude_usage = limits.ClaudeUsage()
        self.record = record
        self.claude_usage.on_event = self.note_claude_event

    def save(self):
        if self.record is not None:
            self.record["usage"] = self.state
            path = self.record["_path"]
            path.write_text(json.dumps({k: v for k, v in self.record.items()
                                        if k != "_path"}, indent=2, default=str) + "\n",
                            encoding="utf-8", newline="\n")

    def note_claude_event(self, reading):
        import loop
        self.state["claude_last_reading"] = loop._reading_record(reading, time.time())
        self.save()

    def _keep_codex_five_hour(self, reading):
        import loop
        return loop.Run._keep_codex_five_hour(self, reading)

    def note_codex(self, reading):
        import loop
        return loop.Run.note_codex(self, reading)

    def grant_paid(self, reading):
        import loop
        loop._grant_paid(self, reading)


def _log(root, action, note):
    import loop
    error = loop._log(action, note,
                      Path(root) / "workflows/review-orchestration/LOG.md")
    if error:
        raise RoundError(error)


def _id():
    # The run-ID form (runrecord.new_run_id), a folder name rather than a log time.
    return f"{datetime.now().astimezone():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"


def _record(root, brief, reviewer, round_no, settings, usage, checks, public_head,
            base, capture_commit, reviewed, inventory, document_blob=None,
            preflight=None):
    import loop
    folder = Path(root) / "workflows/review-orchestration/runs" / f"review-{_id()}"
    folder.mkdir(parents=True, exist_ok=False)
    record = {"item": brief["Item"], "kind": brief["Kind"],
              "round": round_no, "label": f"R{round_no}",
              "started_at": datetime.now().astimezone().isoformat(timespec="microseconds"),
              "under_review": [e["path"] for e in brief["Under review"]],
              "not_under_review": [e["path"] for e in brief["Not under review"]],
              "document": brief["Under review"][0]["path"] if brief["Kind"] == "plan" else None,
              "document_blob": document_blob,
              "reviewer": reviewer, "model": settings["models"][reviewer][
                  "plan_reviewer" if brief["Kind"] == "plan" else "reviewer"]["model"],
              "effort": settings["models"][reviewer][
                  "plan_reviewer" if brief["Kind"] == "plan" else "reviewer"]["effort"],
              "brief": brief["path"], "brief_sha256": hashlib.sha256(
                  brief["text"].encode("utf-8")).hexdigest(),
              "public_head": public_head, "base": base,
              "capture_commit": capture_commit, "checks": checks,
              "reviewed_files": inventory, "usage": usage.state,
              # Step 4's reading, kept so a later reader can compare it with the
              # reading that stopped a round (10.12 step 12; code review R8-2).
              "preflight_reading": preflight,
              "findings": [], "summary": "", "sdk_versions": loop.sdk_versions()}
    record["_path"] = folder / "round.json"
    usage.record = record
    usage.save()
    (folder / "probe-calls.jsonl").write_text("", encoding="utf-8", newline="\n")
    return record, folder


def _prompt(root, brief, round_no, packet, checks, diff, reviewer, reviewed):
    """Render the one cross-review prompt with the correct test claim."""
    from string import Template
    template = (Path(root) / "workflows/review-orchestration/prompts/cross-review.md").read_text(
        encoding="utf-8")
    # A repair round is one whose packet holds earlier findings, not one whose number
    # is above 1: a by-hand finish starts its own empty packet at a round above the
    # run's labels (plan 10.5 "Finishing by hand"; code review R11-1).
    earlier = any(FINDING.match(line) for line in (packet or "").splitlines())
    repair = ("This is a repair round. Check each dated Repair line in Addressed. "
              "Do not reopen an accepted repair unless a later repair broke it. "
              "A repeated finding sets reraises.\n" if earlier else "")
    tests = ([e["path"] for e in brief["Checks"]] if brief["Kind"] == "code" else [])
    test_claim = ("Test files run: " + ", ".join(tests) if tests else
                  "No test file was run. Any claim in the brief that tests pass is "
                  "untested by this round.")
    tool_rules = ("A Grep call carrying the glob option is refused; use path or type."
                  if reviewer == "claude" else
                  "Shell commands are for read-only git and file reads, never project code.")
    return Template(template).substitute(round=f"R{round_no}",
        packet_path=f"memory/{brief['Item']}_review_packet.md", repair=repair,
        test_claim=test_claim, brief=brief["text"], packet=packet or packet_template(brief["Item"]),
        checks="\n".join(checks), diff=diff, reviewer=reviewer,
        tool_rules=tool_rules,
        changed_files="\n".join(reviewed) or "None")


def _review_session(root, brief, reviewer, settings, usage, folder, copy, image,
                    prompt, record):
    import loop
    import providers
    role = "plan_reviewer" if brief["Kind"] == "plan" else "reviewer"
    probe_settings = folder / "probe-settings.json"
    probe_settings.write_text(json.dumps({"copy": str(copy), "image": image,
                                          "record": str(record["_path"].parent / "probe-calls.jsonl")}) + "\n",
                              encoding="utf-8", newline="\n")
    tripwire = folder / "tripwire"
    tripwire.mkdir()
    shutil.copyfile(Path(root) / "workflows/review-orchestration/scripts/tripwire.py",
                    tripwire / "sitecustomize.py")
    (folder / "hook.json").write_text(json.dumps({"project_root": str(root)}) + "\n",
                                      encoding="utf-8", newline="\n")
    session = providers.make_read_only(settings, reviewer, role, cwd=root,
                  usage=usage.claude_usage, frozen_dir=folder,
                  probe_settings=probe_settings)
    state = {"live": {}}
    result = loop.call_reviewer(session, prompt,
                                lambda with_pattern=True: review_schema(brief["Kind"], with_pattern),
                                state, usage.save, note_claude=False)
    record["launch"] = result.launch or result.callbacks
    record["transcript"] = result.thread_id
    (record["_path"].parent / "transcript.json").write_text(
        json.dumps(result.raw, indent=2, default=str) + "\n", encoding="utf-8",
        newline="\n")
    return result.reply


def _inventory_now(root, brief, base):
    return code_diff(root, brief, base, allow_empty=True)[2]


def _inventory_difference(before, after):
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(path for path in set(before) & set(after)
                     if before[path] != after[path])
    return "; ".join(part for part in (
        "added " + ", ".join(added) if added else "",
        "removed " + ", ".join(removed) if removed else "",
        "changed " + ", ".join(changed) if changed else "") if part)


def run_round(args, *, root=ROOT, deps=None):
    """One round in the thirteen-step order; dependencies are replaceable in tests."""
    import container
    import limits
    import loop
    import providers
    import settings as settings_mod
    root = Path(root)
    deps = deps or SimpleNamespace(
        read_codex=loop._read_codex_live, model_offered=model_offered,
        command=command, capture=capture, docker_running=container.docker_running,
        ensure_docker=container.ensure_running, stop_docker=container.stop_docker,
        prepare_image=container.prepare_image, start_ignored=container.start_ignored,
        make_copy=container.make_copy, remove_copy=container.remove_copy,
        review_session=_review_session,
        probe_claude=lambda model, cwd, received: asyncio.run(
            providers.probe_claude(model, cwd=cwd, on_event=received)))
    # Steps 1-3: no round writes.
    brief = read_brief(root, args.review)
    if args.build_ready:
        records = round_records(root)
        failure = build_gate(root, args.git_dir, brief, records)
        print("build-ready: " + (failure or "PASS"))
        return 1 if failure else 0
    packet_path, packet_text, labels = packet_status(root, brief["Item"])
    records = round_records(root)
    round_no = choose_round(brief["Item"], labels, records, args.round)
    reviewer = args.reviewer
    brief["reviewer"] = reviewer
    settings = settings_mod.assign_roles(settings_mod.load_settings(
        root / "workflows/review-orchestration/config/settings.json"),
        "codex" if reviewer == "claude" else "claude")
    role = "plan_reviewer" if brief["Kind"] == "plan" else "reviewer"
    entry = settings_mod.model_for(settings, reviewer, role)
    if args.reset_confirmed and reviewer != "claude":
        _refuse("--reset-confirmed", "only a Claude review can use it",
                "the command without --reset-confirmed")
    explicit_base = resolve_base(root, args.git_dir, args.base, brief["Kind"])
    # Step 4: the run's request-free preflight. The Codex model list is request-free.
    state = {"status": "running"}
    seeded = seed_claude(root, records)
    if seeded is not None:
        state["claude_last_reading"] = seeded
    usage = RoundUsage(state, settings, root)
    try:
        reading, saved, credits = loop.usage_preflight(
            state, settings, "review", read_codex=deps.read_codex,
            note_codex=usage.note_codex, now=time.time, root=root,
            allow_paid=args.allow_paid, user_resume=args.reset_confirmed,
            dry_run=args.dry_run, grant_paid=usage.grant_paid)
    except loop.RunRefused as exc:
        _refuse("usage preflight", str(exc), "a usable reading and permission before review")
    if reviewer == "codex":
        try:
            offered = deps.model_offered(root, entry["model"])
        except Exception as exc:
            _refuse(f"models.codex.{role}",
                    f"model list could not be read: {type(exc).__name__}: {exc}",
                    "a working request-free model list before review")
        if not offered:
            _refuse(f"models.codex.{role}", f"model {entry['model']!r} is not offered",
                    "an offered model in settings.json")
    # Step 5: checks are bracketed by a complete public-tree comparison.
    checks = mechanical(root, brief, explicit_base if args.base else None,
                        dry_run=args.dry_run, run=deps.command)
    # Step 6: resolve the public base and assign the whole change.
    if brief["Kind"] == "code":
        base = explicit_base or resolve_base(root, args.git_dir, None, "code")
        diff, reviewed, inventory = code_diff(root, brief, base)
    else:
        base, reviewed, inventory = explicit_base, [], {}
        diff = "Read the whole document cold."
    for excluded in brief["Not under review"]:
        path = root / excluded["path"]
        if not path.exists() and git(root, "cat-file", "-e",
                                     f"{base}:{excluded['path']}", check=False).returncode:
            _refuse(f"line {excluded['line']}",
                    f"deleted path {excluded['path']!r} is not in the base",
                    "an existing path or one present at the base")
    if args.dry_run:
        with tempfile.TemporaryDirectory(prefix="book-dragon-review-dry-") as tmp:
            image = deps.prepare_image(root, sys.executable, tmp, dry_run=True)
        prompt = _prompt(root, brief, round_no, packet_text, checks, diff,
                         reviewer, reviewed)
        print(f"[DRY RUN] R{round_no}; checks: {'; '.join(checks)}; "
              f"diff {len(diff)} characters; prompt {len(prompt)} characters; "
              f"Docker running: {deps.docker_running()}; image: {image.get('would')}; "
              f"{reading.summary()}; {saved.summary()}; live Claude usage was not read")
        return 0
    # Step 7: the first capture. Nothing between this and the reviewer launch writes
    # reviewed source. A capture failure has no round record and no session.
    _log(root, "started", f"Cross-review R{round_no} of {brief['Item']} started.")
    if packet_text is None:
        packet_path.write_text(packet_template(brief["Item"]), encoding="utf-8",
                               newline="\n")
        packet_text = packet_path.read_text(encoding="utf-8")
    if brief["Kind"] == "code":
        (root / "memory" / f"{brief['Item']}_review_R{round_no}.diff").write_text(
            diff, encoding="utf-8", newline="\n")
    try:
        captured = deps.capture(root, args.git_dir,
            f"Capture before {reviewer} review R{round_no} of {brief['Item']}")
    except Exception as exc:
        print(f"capture-failed: {exc}; tell the user directly")
        _log(root, "failed", f"Cross-review R{round_no} of {brief['Item']} capture-failed.")
        return 1
    public_head = git(root, "rev-parse", "HEAD").stdout.strip()
    preparation_error = None
    if brief["Kind"] == "plan":
        document = brief["Under review"][0]["path"]
        try:
            base, diff = plan_diff(root, args.git_dir, brief, records, captured, explicit_base)
            document_blob = git(root, "hash-object", document).stdout.strip()
            (root / "memory" / f"{brief['Item']}_review_R{round_no}.diff").write_text(
                diff, encoding="utf-8", newline="\n")
        except Exception as exc:
            preparation_error = f"plan diff: {type(exc).__name__}: {exc}"
            base, diff, document_blob = explicit_base, "", None
    else:
        document_blob = None
    # Steps 8-9: record and work folder first, then the shared usage envelope.
    record, record_dir = _record(root, brief, reviewer, round_no, settings, usage,
        checks, public_head, base, captured, reviewed, inventory, document_blob,
        preflight={"codex": reading.summary(), "claude_saved": saved.summary()})
    work = Path(tempfile.gettempdir()) / "book-dragon-orchestration" / record_dir.name
    work_error = preparation_error
    work_created = False
    try:
        work.mkdir(parents=True, exist_ok=False)
        work_created = True
    except OSError as exc:
        work_error = "; ".join(filter(None, (work_error,
                         f"work folder could not be created: {exc}")))
    if work_error:
        record["evidence"] = work_error
    state["frozen_dir"] = str(work)
    usage.save()
    copy = work / "copy"
    started_docker = False
    reply = None
    outcome = ({"stop": "error", "evidence": work_error} if work_error else {})

    def before_step(step, provider):
        stop, probe, codex = loop.Run.usage_before_step(
            state, settings, step, provider, claude_usage=usage.claude_usage,
            read_codex=deps.read_codex, note_codex=usage.note_codex,
            probe_claude=deps.probe_claude, now=time.time, root=root)
        record["usage_probe"] = probe
        record["codex_before_step"] = codex
        usage.save()
        return stop

    async def body():
        nonlocal started_docker, reply
        # ensure_docker says whether this call started Docker (container.ensure_running
        # checks again), so a Docker Desktop that finished starting meanwhile is left
        # running (10.12 step 9; code review R8-1).
        started_docker = bool(deps.ensure_docker())
        image = deps.prepare_image(root, sys.executable, work)["tag"]
        floor = deps.start_ignored(root)
        deps.make_copy(root, copy, public_head, floor)
        if brief["Kind"] == "code":
            difference = _inventory_difference(inventory, _inventory_now(root, brief, base))
            if difference:
                raise RoundError("reviewed files changed before the session: " + difference)
        if brief["Kind"] == "plan" and sha(root / document) != plan_sha:
            raise RoundError("plan document changed before the session")
        prompt = _prompt(root, brief, round_no, packet_text, checks, diff,
                         reviewer, reviewed)
        (record_dir / "prompt.md").write_text(prompt, encoding="utf-8", newline="\n")
        (record_dir / "diff.patch").write_text(diff, encoding="utf-8", newline="\n")
        reply = await asyncio.to_thread(deps.review_session, root, brief, reviewer,
            settings, usage, work, copy, image, prompt, record)
        if brief["Kind"] == "code":
            difference = _inventory_difference(inventory, _inventory_now(root, brief, base))
            if difference:
                raise RoundError("reviewed files changed during the session: " + difference)
        if brief["Kind"] == "plan" and sha(root / document) != plan_sha:
            raise RoundError("plan document changed during the session")
        usage.save()

    plan_sha = None
    if brief["Kind"] == "plan" and not work_error:
        try:
            plan_sha = sha(root / document)
        except OSError as exc:
            work_error = f"plan document could not be read: {exc}"
            record["evidence"] = work_error
            outcome.update(stop="error", evidence=work_error)

    def finish(reason, evidence, interrupted=False):
        outcome["stop"], outcome["evidence"] = reason, evidence
        record["evidence"] = evidence
        state["status"] = reason
        usage.save()

    try:
        if not work_error:
            asyncio.run(loop.usage_envelope(state, settings, "review", reviewer,
                claude_usage=usage.claude_usage, before_step=before_step, body=body,
                finish=finish, read_codex=deps.read_codex,
                note_codex=usage.note_codex, root=root, save=usage.save))
    except Exception as exc:
        outcome.update(stop="error", evidence=f"usage envelope: {type(exc).__name__}: {exc}")
        record["evidence"] = outcome["evidence"]
    finally:
        problems = []
        try:
            if work_created and copy.exists():
                deps.remove_copy(copy)
        except Exception as exc:
            problems.append(f"copy removal: {exc}")
        try:
            if work_created and work.exists():
                shutil.rmtree(work)
        except Exception as exc:
            problems.append(f"work folder removal: {exc}")
        if started_docker:
            try:
                deps.stop_docker()
            except Exception as exc:
                problems.append(f"Docker stop: {exc}")
        if problems:
            record["cleanup_error"] = "; ".join(problems)
            usage.save()
    # Steps 10-11: validate the closed reply before touching packet findings.
    stop = outcome.get("stop")
    if stop is None:
        try:
            findings = validate_reply(reply, brief["Kind"], labels, reviewed)
            if findings:
                write_findings(packet_path, brief["Item"], round_no, findings)
            record["replans"] = consecutive_reraises(packet_path, round_no, findings)
            record["findings"] = [{"label": f"R{round_no}-{n}", **finding}
                                  for n, finding in enumerate(findings, 1)]
            record["summary"] = reply["summary"]
            stop = "findings" if findings else "clean"
        except Exception as exc:
            stop = "error"
            record["evidence"] = f"reply or packet: {exc}"
    # Step 12: finished record and log.
    state["status"] = stop
    record["stop"] = stop
    record["seconds"] = round(time.time() - datetime.fromisoformat(
        record["started_at"]).timestamp(), 1)
    usage.save()
    _log(root, "failed" if stop == "error" else "completed",
         f"Cross-review R{round_no} of {brief['Item']} ended {stop}.")
    # Step 13: every path that reached step 9 gets a closing capture.
    closing_failed = False
    try:
        deps.capture(root, args.git_dir,
            f"Capture after {reviewer} review R{round_no} of {brief['Item']}: {stop}")
    except Exception as exc:
        closing_failed = True
        record["closing_capture_failed"] = str(exc)[-1200:]
        usage.save()
        print(f"closing capture failed: {exc}; result stands; build gate will refuse "
              "until a later capture takes this record; tell the user directly")
    print(f"{stop}: {record.get('summary') or record.get('evidence') or ''}; "
          f"capture {captured}; preflight {reading.summary()}; saved {saved.summary()}; "
          f"step readings {state.get('readings')}")
    for finding in record["findings"]:
        print(f"{finding['label']}: {finding['title']}")
    for label in record.get("replans", []):
        print(f"{label}: raised in two consecutive rounds; take this re-plan to the user")
    if record.get("cleanup_error"):
        print("cleanup error: " + record["cleanup_error"])
    return (1 if closing_failed or record.get("cleanup_error") or stop not in
            ("clean", "findings") else 2 if stop == "findings" else 0)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        # The round runs as the starter's child, so the starter's own reconfigure
        # does not reach it; a reviewer's text must print as UTF-8, not cp1252.
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if os.environ.get(MARKER) != "1":
        print(f"Start through the starter: {STARTER} <review-brief> "
              "--reviewer claude|codex --git-dir <personal repository>")
        return 3
    class ReviewParser(argparse.ArgumentParser):
        def error(self, message):
            raise Refused(f"command: {message}; write the starter's --review form")

    parser = ReviewParser(description="One cross-review round")
    parser.add_argument("--review", required=True)
    parser.add_argument("--reviewer", choices=("claude", "codex"))
    parser.add_argument("--git-dir", required=True)
    parser.add_argument("--round", type=int)
    parser.add_argument("--base")
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--reset-confirmed", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--build-ready", action="store_true")
    try:
        parsed = parser.parse_args(args)
    except Refused as exc:
        print(f"refused: {exc}")
        return 3
    if parsed.build_ready:
        for name in ("reviewer", "round", "base", "allow_paid", "reset_confirmed", "dry_run"):
            if getattr(parsed, name) not in (None, False):
                print(f"--build-ready refuses --{name.replace('_', '-')}")
                return 3
    elif parsed.reviewer is None:
        print("--reviewer is required: write --reviewer claude or codex")
        return 3
    if parsed.round is not None and parsed.round < 1:
        print("--round must be a positive number")
        return 3
    try:
        return run_round(parsed)
    except Refused as exc:
        print(f"refused: {exc}")
        return 3
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
