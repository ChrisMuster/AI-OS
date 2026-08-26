#!/usr/bin/env python3
"""Verification for the sync architecture plans and their Stage A allowlist.

Read-only. Two checks, run together by default:

  --allowlist    Extract the executable classification block from the architecture
                 plan and prove what it actually selects against the real tree,
                 resolved through allowlist.py in the same git context the Stage A
                 build uses. The selection rule itself lives there, in one copy,
                 so this check and the build cannot drift apart.
  --consistency  Check both plans for the defect classes that have recurred:
                 superseded pathspec spellings, a second copy of the classification,
                 references to artefacts that do not exist, and stale review baselines.

Both are read-only and take no destructive action, so neither carries --dry-run.

The plans are design-time documents and are gitignored, so on a fresh clone they are
absent. That is reported as SKIPPED rather than as a failure: a check that cannot run
must say so rather than pass silently.

No personal filename appears in this file. The allowlist assertions are structural
("some selected file under this directory is not markdown") rather than naming
individual files, both because naming them would put personal content into a tracked
file and because a structural assertion keeps working when the files change.
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import allowlist  # noqa: E402  (the shared selection rule, alongside this file)

sys.stdout.reconfigure(encoding="utf-8")

PLAN = Path("SYNC-ARCHITECTURE-PLAN.md")
SPEC = Path("SHADOW-REPOSITORY-PLAN.md")
BASELINES = [
    (Path("CODEX-SYNC-ARCHITECTURE-PLAN.md"), PLAN),
    (Path("CODEX-SHADOW-REPOSITORY-PLAN.md"), SPEC),
]


# --------------------------------------------------------------------------- utils

def _read(path):
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# The parser lives in the selection module so the check and the build read the
# plan's block through the same code. Re-exported here for check_consistency.
extract_block = allowlist.extract_block


# ----------------------------------------------------------------------- allowlist

def check_allowlist(findings):
    text = _read(PLAN)
    if text is None:
        findings.append(("SKIP", f"{PLAN} not present; allowlist check not run"))
        return
    specs = extract_block(text)
    if specs is None:
        findings.append(("FAIL", f"no usable ```allowlist block in {PLAN}"))
        return

    # Resolved through the shared selection module, in SHADOW context: an empty
    # personal repository looking at this working tree, which is the state the
    # Stage A build resolves the pathspecs in. Resolving them in public context
    # instead was the defect this check was rebuilt to remove. There, "untracked"
    # already excludes every publicly tracked file, so the no-overlap assertion
    # below could not have failed whatever the tree contained.
    out = allowlist.select(specs)
    selected, raw, tracked = out["final"], out["raw"], out["tracked"]
    if selected is None:
        findings.append(("SKIP", "git could not answer; allowlist check not run"))
        return

    def any_sel(pred):
        return any(pred(p) for p in selected)

    # Positive controls. A suite of exclusions alone passes on an empty allowlist,
    # so each of these names a property that must be TRUE of the selection.
    positives = [
        ("an authored personal folder contributes a non-markdown file",
         lambda: any_sel(lambda p: p.startswith("conversations/")
                         and not p.endswith(".md"))),
        ("an authored personal folder contributes a file from a SUBfolder",
         lambda: any_sel(lambda p: p.startswith("conversations/")
                         and p.count("/") > 1)),
        ("journal entries are selected",
         lambda: any_sel(lambda p: p.startswith("journal/entries/"))),
        ("memory files are selected",
         lambda: any_sel(lambda p: p.startswith("memory/") and p.count("/") == 1)),
        ("the backups folder's own documentation is selected",
         lambda: "memory/backlog-backups/CONTEXT.md" in selected
                 and "memory/backlog-backups/LOG.md" in selected),
        ("the user profile is selected", lambda: "USER.md" in selected),
        ("the root audit trail is selected", lambda: "LOG.md" in selected),
        ("an audit trail inside a derived folder is selected",
         lambda: any_sel(lambda p: p.endswith("/LOG.md")
                         and ("session-search/data" in p
                              or "knowledge-graph/index" in p
                              or "web-research/outputs" in p))),
        ("authored wiki content is selected",
         lambda: any_sel(lambda p: "/wiki/" in p and p.startswith("wikis/"))),
        ("a design-time document is selected",
         lambda: any_sel(lambda p: p.endswith("-PLAN.md"))),
    ]
    for label, pred in positives:
        findings.append(("PASS" if pred() else "FAIL", f"allowlist: {label}"))

    # Category sweeps. None of these kinds may appear at all.
    sweeps = [
        ("no file under any raw/ folder, at any depth",
         lambda p: "/raw/" in p),
        ("no Obsidian vault settings, at any depth",
         lambda p: "/.obsidian/" in p or p.startswith(".obsidian/")),
        ("no backlog snapshot",
         lambda p: p.startswith("memory/backlog-backups/")
                   and p.endswith("-backlog.md")),
        ("no virtualenv or bytecode cache",
         lambda p: p.startswith(".venv/") or "__pycache__/" in p),
        ("no bulk scraped collections or run state",
         lambda p: p.startswith("workflows/reddit-collector/collections/")
                   or p.startswith("workflows/reddit-collector/state/")),
        ("no secret", lambda p: p == ".env"),
        ("no machine-local permissions",
         lambda p: p == ".claude/settings.local.json"),
    ]
    for label, pred in sweeps:
        hits = sorted(p for p in selected if pred(p))
        findings.append(("PASS" if not hits else "FAIL",
                         f"allowlist: {label}"
                         + ("" if not hits else f" ({len(hits)} hit(s), e.g. {hits[0]})")))

    # The invariant the design actually rests on. While no file is both tracked
    # and matched by an ignore rule, the two git contexts return the same answer
    # and the seed commit is safe. It is currently held up by the negation ("!")
    # lines in .gitignore and by nothing else, so deleting one breaks it without
    # any other symptom. This is the root-cause check and it names the file.
    both = allowlist.tracked_and_ignored()
    if both is None:
        findings.append(("SKIP", "git could not answer the tracked-and-ignored check"))
    else:
        findings.append(("PASS" if not both else "FAIL",
                         "invariant: no file is both publicly tracked and ignored"
                         + ("" if not both
                            else f" ({len(both)} file(s), e.g. {sorted(both)[0]})")))

    # The consequence of that invariant breaking, measured where the build will
    # meet it. Unlike the old public-context form, this one can fail.
    findings.append(("PASS" if not out["overlap"] else "FAIL",
                     "allowlist: shadow-context selection contains no publicly "
                     "tracked file"
                     + ("" if not out["overlap"]
                        else f" ({len(out['overlap'])} overlap(s), "
                             f"e.g. {sorted(out['overlap'])[0]})")))

    # A regression guard on the subtraction step rather than a property of the
    # tree: it fails if the correction in allowlist.select is weakened or removed.
    findings.append(("PASS" if not (selected & tracked) else "FAIL",
                     "allowlist: the corrected selection excludes tracked files"))

    total = sum(Path(p).stat().st_size for p in selected if Path(p).is_file())
    findings.append(("INFO", f"allowlist selects {len(selected)} files, "
                             f"{total / 1048576:.2f} MiB"))
    if raw is not None and len(raw) != len(selected):
        findings.append(("INFO", f"shadow context offered {len(raw)}; "
                                 f"{len(raw) - len(selected)} removed as publicly tracked"))


# --------------------------------------------------------------------- consistency

def check_consistency(findings):
    for path in (PLAN, SPEC):
        text = _read(path)
        if text is None:
            findings.append(("SKIP", f"{path} not present; consistency check not run"))
            continue
        name = path.name

        # A mention of the superseded one-level spelling is only legitimate as a
        # contrast, which means the corrected spelling appears on the same line.
        bad = [i + 1 for i, line in enumerate(text.splitlines())
               if re.search(r"wikis/\*/raw", line) and "wikis/**/raw" not in line]
        findings.append(("PASS" if not bad else "FAIL",
                         f"{name}: no superseded wikis/*/raw spelling standing alone"
                         + ("" if not bad else f" (line(s) {bad})")))

        # The four authored personal folders take every file type, so a .md-scoped
        # pattern for any of them is the defect that reversed the decision once.
        bad = [i + 1 for i, line in enumerate(text.splitlines())
               if re.search(r"(conversations|reviews|user-inputs|journal/entries)/\*\.md",
                            line)]
        findings.append(("PASS" if not bad else "FAIL",
                         f"{name}: no markdown-scoped authored-folder pattern"
                         + ("" if not bad else f" (line(s) {bad})")))

        # A reference to a measuring script that is not in the tree.
        bad = [i + 1 for i, line in enumerate(text.splitlines())
               if "scope_measure" in line
               and "not reintroduce" not in line and "no such script" not in line]
        findings.append(("PASS" if not bad else "FAIL",
                         f"{name}: no instruction to run a missing measuring script"
                         + ("" if not bad else f" (line(s) {bad})")))

    text = _read(PLAN)
    if text is not None:
        specs = extract_block(text)
        findings.append(("PASS" if specs else "FAIL",
                         f"{PLAN.name}: carries a usable executable classification block"))
        spec_text = _read(SPEC)
        if spec_text is not None:
            findings.append(("PASS" if extract_block(spec_text) is None else "FAIL",
                             f"{SPEC.name}: does NOT carry a second classification block"))

    for baseline, live in BASELINES:
        b, l = _read(baseline), _read(live)
        if b is None or l is None:
            findings.append(("SKIP", f"{baseline.name} or its live plan not present"))
            continue
        same = hashlib.sha256(b.encode()).hexdigest() == \
               hashlib.sha256(l.encode()).hexdigest()
        findings.append(("PASS" if same else "FAIL",
                         f"{baseline.name} is byte-identical to {live.name}"
                         + ("" if same else " (refresh before a review round)")))


# ---------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        description="Verification for the sync architecture plans (read-only).")
    ap.add_argument("--check", action="store_true",
                    help="Run every check (default)")
    ap.add_argument("--allowlist", action="store_true",
                    help="Only prove what the classification block selects")
    ap.add_argument("--consistency", action="store_true",
                    help="Only check the plans for known defect classes")
    args = ap.parse_args()
    run_all = args.check or not (args.allowlist or args.consistency)

    findings = []
    if run_all or args.allowlist:
        check_allowlist(findings)
    if run_all or args.consistency:
        check_consistency(findings)

    print("# Sync Architecture Verification\n")
    for level in ("FAIL", "SKIP", "PASS", "INFO"):
        rows = [f for f in findings if f[0] == level]
        if not rows:
            continue
        print(f"## {level} ({len(rows)})")
        for _, msg in rows:
            print(f"- {msg}")
        print()

    fails = [f for f in findings if f[0] == "FAIL"]
    skips = [f for f in findings if f[0] == "SKIP"]
    if fails:
        print(f"{len(fails)} failure(s).")
    elif skips:
        print(f"No failures, but {len(skips)} check(s) could not run.")
    else:
        print("All checks passed.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
