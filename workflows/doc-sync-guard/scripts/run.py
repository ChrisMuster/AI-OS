#!/usr/bin/env python3
"""Doc-sync guard - catch CONTEXT.md / LOG.md drift when a directory changes.

Read-only check. There is deliberately no fix mode: the AI that made the change
knows *why* it changed each directory and writes a proper Revision History / LOG
entry from that knowledge, whereas a context-free script could only lay down
hollow filler. The guard only reports; the AI (or human) fixes.

    python workflows/doc-sync-guard/scripts/run.py --check [--json]
        [--since REF | --base BRANCH | --staged] [--strict]

What it catches, for every committable directory whose *real* content changed
(a file added/removed/edited that is not the directory's own CONTEXT.md/LOG.md):
  * CONTEXT.md was not updated in the same change.
  * CONTEXT.md was updated but gained no new Revision History entry, or its
    `**Last modified:**` date disagrees with the newest Revision History date.
  * a child directory was added or removed without the parent CONTEXT.md moving
    (the one mechanical parent-propagation case; subjective prose stays manual).
  * LOG.md gained no entry for the change (verified by mtime; see below).

Scope - the working tree by default (R3-1). Two collection modes:
  * default / --since / --base : `git diff <base>` (base=HEAD) plus untracked
    committable files. HEAD keeps the check on the current uncommitted task and
    keeps file mtimes honest for the LOG check. `--base BRANCH` diffs the
    merge-base of BRANCH and HEAD for a whole-branch scan; no stopping point
    uses it by default.
  * --staged : `git diff --cached` - the pre-commit view (what is about to be
    committed).
Gitignored paths never appear in any of these lists, so the personal-file
carve-out (LOG.md, memory/, journal/entries/, wikis/, ...) is automatic.

The LOG.md check is by mtime because LOG.md is gitignored and cannot be
content-diffed. It is reliable in the build-once-at-the-end flow, where the whole
body of work sits uncommitted in the working tree with real edit-time mtimes.
See DOC-SYNC-GUARD-PLAN.md section 4.2 for the full argument and the documented
residual (a deliberate mid-task git op that rewrites a file's mtime can raise a
loud false positive, never a silent miss).

Reports drift as WARN under a `doc-sync` label: the audit merges those advisory,
and the close-out verifier turns a WARN under that label into a hard fail (a
DEGRADED finding under the same label is non-blocking).
Exit 0 by default; with --strict, exit 1 when any WARN exists.

A third severity, DEGRADED, is reserved for "a part of this check could not run"
- currently only the output-inventory probe, which needs PyYAML while the drift
scan itself is standard-library only. It is deliberately NOT a WARN: a `doc-sync`
WARN means drift to every consumer, so reporting a missing package that way makes
close-out hard-fail and the pre-commit advisory announce drift that does not
exist. DEGRADED carries its own repair hint through the audit instead, matching
how every other guard reports an unavailable runtime, and never trips --strict.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import context_parse  # noqa: E402
import logtime  # noqa: E402

Finding = tuple  # (severity, label, message)
LABEL = "doc-sync"
WARN = "WARN"
INFO = "INFO"
# "A part of this check could not run." Never drift, so consumers that gate on
# drift must not gate on this. See the module docstring.
DEGRADED = "DEGRADED"
SEVERITIES = (WARN, INFO, DEGRADED)

CONTEXT_NAME = "CONTEXT.md"
LOG_NAME = "LOG.md"
MTIME_TOLERANCE = 2.0  # seconds


class GitError(Exception):
    """A git invocation failed; the caller reports a WARN and scans nothing."""


# ---------------------------------------------------------------------------
# Git I/O (the only impure layer; the parsing helpers are pure)
# ---------------------------------------------------------------------------
def _git(root, args):
    try:
        result = subprocess.run(
            ["git", *args], capture_output=True, encoding="utf-8", cwd=str(root),
        )
    except Exception as exc:  # git missing
        raise GitError(f"could not run git ({exc})") from exc
    if result.returncode != 0:
        reason = (result.stderr or "").strip().splitlines()
        raise GitError(reason[-1] if reason else f"git exited {result.returncode}")
    return result.stdout


def merge_base(root, base):
    """Merge-base sha of *base* and HEAD, or None when unavailable."""
    try:
        out = _git(root, ["merge-base", base, "HEAD"]).strip()
    except GitError:
        return None
    return out or None


def changed_name_status(root, base, staged):
    """Return [(status, path), ...] for tracked changes.

    status is git's single-letter code (A/M/D/...); path is project-relative
    posix. Working-tree mode diffs against *base*; staged mode uses --cached.
    """
    # --no-renames splits a move into a delete (old path) + add (new path), so
    # the source directory that *lost* content is checked too, not only the
    # destination. Without it git reports `R### old new` and the guard would only
    # see the new path, silently skipping the old directory's CONTEXT/LOG update.
    args = ["diff", "--name-status", "--no-renames", "-z"]
    if staged:
        args.append("--cached")
    else:
        args.append(base)
    out = _git(root, args)
    return _parse_name_status(out)


def _parse_name_status(out):
    """Parse `git diff --name-status -z` output into [(status, path), ...].

    The -z form is NUL-separated. A rename/copy (R###/C###) is followed by two
    path fields (old, new); we key the change on the new path. In practice the
    callers pass --no-renames so a move arrives as D+A and this branch is not
    hit, but it is kept defensively for any caller that does not.
    """
    fields = [f for f in out.split("\0") if f != ""]
    pairs = []
    i = 0
    while i < len(fields):
        code = fields[i][0]
        if code in ("R", "C"):
            # Rename/copy: status, old_path, new_path. Key on the new path.
            new_path = fields[i + 2] if (i + 2) < len(fields) else ""
            pairs.append((code, new_path))
            i += 3
        else:
            pairs.append((code, fields[i + 1] if (i + 1) < len(fields) else ""))
            i += 2
    return [(s, p) for s, p in pairs if p]


def untracked_files(root):
    """New committable (not gitignored) files, project-relative posix."""
    out = _git(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    return [p for p in out.split("\0") if p]


def committable_paths(root):
    """Tracked + new-committable files (posix). Excludes gitignored paths."""
    out = _git(root, ["ls-files", "--cached", "--others", "--exclude-standard", "-z"])
    return [p for p in out.split("\0") if p]


def file_at_ref(root, ref, path):
    """Text of *path* at git *ref*, or '' if it does not exist there.

    Used to load the pre-change CONTEXT.md so its Revision History entries can be
    compared against the current file's (a section-aware "gained an entry" test).
    A path that did not exist at *ref* - a brand-new file - yields ''.
    """
    try:
        return _git(root, ["show", f"{ref}:{path}"])
    except GitError:
        return ""


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------
def _dir_of(path):
    """Project-relative posix directory of *path* ('' for a root-level file)."""
    return path.rsplit("/", 1)[0] if "/" in path else ""


def committable_context_dirs(paths):
    """Set of directories that own a committable CONTEXT.md (from *paths*)."""
    dirs = set()
    for p in paths:
        if p.rsplit("/", 1)[-1] == CONTEXT_NAME:
            dirs.add(_dir_of(p))
    return dirs


def deleted_context_dirs(changes):
    """Directories whose tracked CONTEXT.md is being deleted."""
    return {
        _dir_of(path) for status, path, _ in changes
        if status == "D" and path.rsplit("/", 1)[-1] == CONTEXT_NAME
    }


def path_under_dir(path, directory):
    """True when *path* is inside *directory* (or is the directory itself)."""
    if directory == "":
        return True
    return path == directory or path.startswith(f"{directory}/")


def owner_dir(path, ctx_dirs):
    """Nearest ancestor directory (incl. the file's own) owning a CONTEXT.md.

    Returns the posix directory, or None if no ancestor owns one.
    """
    d = _dir_of(path)
    while True:
        if d in ctx_dirs:
            return d
        if d == "":
            return None
        d = _dir_of(d)


# ---------------------------------------------------------------------------
# Core check
# ---------------------------------------------------------------------------
def _missing_package(exc):
    """Return the top-level package an ImportError names, or "" if it names none.

    `import inventory` fails with the missing module recorded on the exception:
    an interpreter with no PyYAML reports `yaml`, while a broken import inside
    the loader reports whatever that import asked for. The top level is what
    matters, so a failure to find `yaml.cyaml` still reads as PyYAML.
    """
    name = getattr(exc, "name", None) or ""
    return name.split(".")[0]


def _inventory_findings():
    """Probe the classified output inventory; report when it cannot be read.

    The guard does not consume doc-sync exceptions yet - that is the scope
    extension, and until then every changed directory is checked the same way.
    What the probe buys now is fail-closed behaviour at the point the answer
    starts mattering: an inventory that cannot be parsed is reported and exempts
    nothing, rather than being read as an empty exception set that silently
    excuses whatever it was meant to classify.

    It is reported as DEGRADED, not WARN. The distinction is the whole point: a
    `doc-sync` WARN means "a directory changed and its documentation did not",
    which close-out hard-fails on and the pre-commit hook announces as drift.
    "PyYAML is not installed on this interpreter" is a different claim, and the
    project already has a severity for it. The drift scan below is unaffected and
    still runs in full, which is why this cannot be reported as a check that did
    not run either.

    The import is deliberately lazy. `run.py` must stay importable and runnable
    on an interpreter with no PyYAML, because the guard itself has no
    third-party dependency and several consumers launch it directly.

    Only the two expected failures degrade: PyYAML missing, and a config that is
    absent or does not satisfy the schema. Anything else propagates, since a
    guard that swallows an unexpected error is the failure one frame up from the
    one this probe exists to prevent. That is why the import branch below tests
    which module was missing rather than catching `ImportError` broadly: a
    broken import inside `inventory.py` is a defect in this repository, and
    reporting it as "output inventory unavailable" would file a code bug under
    the same heading as an unconfigured runtime.
    """
    try:
        import inventory
    except ImportError as exc:
        if _missing_package(exc) != "yaml":
            raise
        return [(DEGRADED, LABEL,
                 f"output inventory unavailable - no doc-sync exceptions "
                 f"applied ({exc})")]
    try:
        inventory.default_inventory()
    except inventory.InventoryError as exc:
        return [(DEGRADED, LABEL,
                 f"output inventory unavailable - no doc-sync exceptions "
                 f"applied ({exc})")]
    return []


def run_check(root, base="HEAD", staged=False):
    """Return doc-sync findings for the current change set. Read-only."""
    root = Path(root)
    findings = _inventory_findings()
    try:
        tracked = changed_name_status(root, base, staged)
        untracked = [] if staged else untracked_files(root)
        all_committable = committable_paths(root)
    except GitError as exc:
        return findings + [(WARN, LABEL, f"scan skipped - {exc}")]

    # Build the change list: (status, path, is_untracked).
    changes = [(s, p, False) for s, p in tracked]
    changes += [("A", p, True) for p in untracked]
    removed_ctx_dirs = deleted_context_dirs(changes)
    ctx_dirs = committable_context_dirs(all_committable) - removed_ctx_dirs

    # Group real content changes by owning directory, and note which owners had
    # their own CONTEXT.md among the changes.
    real_changes = {}          # owner -> [(status, path, is_untracked), ...]
    context_changed = {}       # owner -> is_untracked (the owner's CONTEXT.md)
    for status, path, is_unt in changes:
        name = path.rsplit("/", 1)[-1]
        if name == LOG_NAME:
            continue  # gitignored; never a tracked change, but be defensive
        if any(path_under_dir(path, d) for d in removed_ctx_dirs):
            # A documented child directory being deleted should not be checked as
            # an active owner, and its internal deleted files should not be
            # reassigned upward to the parent. The parent-propagation check below
            # handles the real obligation: the parent Contents must move.
            continue
        owner = owner_dir(path, ctx_dirs)
        if owner is None:
            continue  # no documented owner (e.g. a root-level file)
        if name == CONTEXT_NAME and _dir_of(path) == owner:
            context_changed[owner] = is_unt
            continue  # the owner's own CONTEXT.md is not "real content"
        real_changes.setdefault(owner, []).append((status, path, is_unt))

    for owner in sorted(real_changes):
        findings.extend(
            _check_directory(root, owner, real_changes[owner],
                             context_changed, base, staged)
        )
    findings.extend(
        _check_parent_propagation(changes, ctx_dirs, context_changed)
    )
    return _dedupe(findings)


def _check_directory(root, owner, changed_here, context_changed, base, staged):
    """CONTEXT.md and LOG.md checks for one in-scope directory."""
    findings = []
    label_dir = owner or "(root)"

    # --- CONTEXT.md ---
    if owner not in context_changed:
        findings.append((WARN, LABEL,
                         f"{label_dir}: CONTEXT.md not updated for changes in "
                         f"this directory"))
    else:
        ctx_path = f"{owner}/{CONTEXT_NAME}" if owner else CONTEXT_NAME
        if staged:
            # Judge the STAGED blob (git show :path) - what is actually being
            # committed - not the working tree, which can carry unstaged edits
            # under partial staging. The old side (HEAD, below) is already the
            # committed baseline, so both sides are the commit's view.
            text = file_at_ref(root, "", ctx_path)
        else:
            try:
                text = (root / ctx_path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                text = ""
        # Section-aware "gained a Revision History entry": compare the entry
        # count before vs after, so a dated bullet added elsewhere (Contents,
        # Steps, ...) cannot satisfy it. An untracked CONTEXT.md has no diff
        # base, so its "before" is empty (the whole file is new content).
        if context_changed[owner]:  # untracked - no diff base
            old_text = ""
        else:
            old_ref = "HEAD" if staged else base
            old_text = file_at_ref(root, old_ref, ctx_path)
        if not context_parse.gained_rh_entry(old_text, text):
            findings.append((WARN, LABEL,
                             f"{label_dir}: CONTEXT.md changed but Revision "
                             f"History gained no entry"))
        lm = context_parse.last_modified_date(text)
        newest = context_parse.newest_revision_history_date(text)
        if lm != newest:
            findings.append((WARN, LABEL,
                             f"{label_dir}: CONTEXT.md Last modified ({lm}) does "
                             f"not match the newest Revision History date "
                             f"({newest})"))

    # --- LOG.md (mtime) ---
    findings.extend(_check_log(root, owner, changed_here, label_dir))
    return findings


def _check_log(root, owner, changed_here, label_dir):
    """Verify LOG.md has an entry no older than the batch reference time T."""
    mtimes = []
    for status, path, _is_unt in changed_here:
        if status == "D":
            continue  # a deleted file has no mtime on disk
        fp = root / path
        try:
            mtimes.append(fp.stat().st_mtime)
        except OSError:
            continue
    ctx_path = root / (f"{owner}/{CONTEXT_NAME}" if owner else CONTEXT_NAME)
    try:
        mtimes.append(ctx_path.stat().st_mtime)
    except OSError:
        pass
    if not mtimes:
        return []  # nothing on disk to reference (e.g. deletion-only, no CONTEXT)
    reference = max(mtimes)

    log_path = root / (f"{owner}/{LOG_NAME}" if owner else LOG_NAME)
    if not log_path.exists():
        return [(WARN, LABEL,
                 f"{label_dir}: no LOG.md for a directory whose content changed")]
    try:
        newest = logtime.newest_log_datetime(log_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        newest = None
    if newest is None:
        return [(WARN, LABEL,
                 f"{label_dir}: LOG.md has no parseable entry for this change")]
    if logtime.log_is_behind(newest.timestamp(), reference, MTIME_TOLERANCE):
        return [(WARN, LABEL,
                 f"{label_dir}: LOG.md has no entry for this change "
                 f"(newest entry predates the changed files)")]
    return []


def _check_parent_propagation(changes, ctx_dirs, context_changed):
    """Warn when a child directory was added/removed but the parent didn't move.

    The one mechanical propagation case: a committable CONTEXT.md appearing
    (added child dir) or disappearing (removed child dir) one level down should
    move the parent's CONTEXT.md Contents. Subjective prose stays manual.
    """
    findings = []
    seen = set()
    for status, path, is_unt in changes:
        if path.rsplit("/", 1)[-1] != CONTEXT_NAME:
            continue
        if not (is_unt or status in ("A", "D")):
            continue  # a plain modify is not an add/remove of a child dir
        child_dir = _dir_of(path)
        parent = _dir_of(child_dir)
        if parent not in ctx_dirs:
            continue  # parent is not a documented directory; nothing to require
        if parent in seen:
            continue
        seen.add(parent)
        if parent not in context_changed:
            verb = "removed" if status == "D" else "added"
            article = "a" if verb == "removed" else "an"
            findings.append((WARN, LABEL,
                             f"{parent or '(root)'}: CONTEXT.md Contents not "
                             f"updated for {article} {verb} child directory "
                             f"({child_dir})"))
    return findings


def _dedupe(findings):
    seen, out = set(), []
    for f in findings:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def print_report(findings):
    counts = {sev: 0 for sev in SEVERITIES}
    for sev, _, _ in findings:
        counts[sev] = counts.get(sev, 0) + 1
    print("# Doc-Sync Guard Report\n")
    print(f"**Warnings:** {counts[WARN]}  **Info:** {counts[INFO]}  "
          f"**Degraded (did not run):** {counts[DEGRADED]}\n")
    # The clean line is about drift, so a degraded probe does not suppress it:
    # the scan really did find no drift, and the degrade is reported separately
    # below rather than leaving the reader to infer it from a missing sentence.
    if not any(f[0] in (WARN, INFO) for f in findings):
        print("No CONTEXT.md / LOG.md drift found in changed directories.\n")
    for sev in SEVERITIES:
        group = [f for f in findings if f[0] == sev]
        if not group:
            continue
        heading = "DEGRADED (did not run)" if sev == DEGRADED else sev
        print(f"## {heading}")
        for _, _, msg in sorted(group, key=lambda f: f[2]):
            print(f"- {msg}")
        print()


def strict_failed(findings):
    """True when --strict should exit 1.

    Only drift counts. A DEGRADED finding is deliberately excluded: it says a
    component could not run, which is a runtime to repair rather than a
    documentation change to make, and gating on it turns a missing package into
    a failed close-out. Extracted from main() so the rule is testable without
    depending on whatever the real working tree happens to contain.
    """
    return any(sev == WARN for sev, _, _ in findings)


def findings_json(findings):
    return json.dumps({
        "findings": [
            {"severity": s, "label": lab, "message": m} for s, lab, m in findings
        ]
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Scan changed directories for CONTEXT.md / LOG.md drift "
                    "(read-only)")
    parser.add_argument("--check", action="store_true",
                        help="Read-only scan (default; the only mode)")
    parser.add_argument("--json", action="store_true",
                        help="Emit findings as JSON on stdout (for the audit hook)")
    parser.add_argument("--since", metavar="REF", default=None,
                        help="Diff base ref (default: HEAD - the working tree)")
    parser.add_argument("--base", metavar="BRANCH", default=None,
                        help="Diff against the merge-base of BRANCH and HEAD "
                             "(a whole-branch scan; not used by default)")
    parser.add_argument("--staged", action="store_true",
                        help="Scan staged changes (git diff --cached) - the "
                             "pre-commit view")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 if any WARN finding exists (for CI/pre-commit). "
                             "A DEGRADED finding does not trip it: the runtime, "
                             "not the documentation, is what needs fixing")
    args = parser.parse_args()

    if args.since is not None:
        base = args.since
    elif args.base is not None:
        base = merge_base(PROJECT_ROOT, args.base) or "HEAD"
    else:
        base = "HEAD"

    findings = run_check(PROJECT_ROOT, base=base, staged=args.staged)
    if args.json:
        print(findings_json(findings))
    else:
        print_report(findings)
    if args.strict and strict_failed(findings):
        sys.exit(1)


if __name__ == "__main__":
    main()
