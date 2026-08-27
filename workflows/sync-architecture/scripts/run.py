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
import difflib
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

PLANNED_MARKER = "```planned-artefacts\n"

# Any script path either plan names. Deliberately anchored on "workflows/" so it
# matches the project's own scripts and not an arbitrary word ending in .py.
SCRIPT_RE = re.compile(r"workflows/[A-Za-z0-9_./-]+\.py")


def extract_planned(text):
    """Paths declared as artefacts a stage will create but which do not exist yet.

    Returns a set, empty when the block is absent. Empty is the correct default:
    it means nothing is exempt, so every script the plans name must be present.
    An absent block must not read as "everything is excused".
    """
    start = text.find(PLANNED_MARKER)
    if start == -1:
        return set()
    start += len(PLANNED_MARKER)
    end = text.find("```", start)
    if end == -1:
        return set()
    planned = set()
    for line in text[start:end].splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            planned.add(line)
    return planned


def compare_to_baseline(baseline_text, live_text):
    """Classify a plan against its review baseline. Returns (level, added, removed).

    The baseline holds the version the reviewing AI last reviewed and is refreshed
    only once a review round has finished, so a plan differing from it is the
    normal state for most of an item's life and the difference is the deliverable
    rather than a defect. Drift is therefore INFO.

    A missing baseline is FAIL, because that is the one state with no diff surface
    at all: the next review would have to re-read the whole document blind.
    """
    if live_text is None:
        return ("SKIP", 0, 0)
    if baseline_text is None:
        return ("FAIL", 0, 0)
    if hashlib.sha256(baseline_text.encode()).hexdigest() == \
            hashlib.sha256(live_text.encode()).hexdigest():
        return ("INFO", 0, 0)
    added = removed = 0
    for row in difflib.unified_diff(baseline_text.splitlines(),
                                    live_text.splitlines(), lineterm="", n=0):
        if row.startswith("+") and not row.startswith("+++"):
            added += 1
        elif row.startswith("-") and not row.startswith("---"):
            removed += 1
    return ("INFO", added, removed)


def unresolved_scripts(text, planned, root=Path(".")):
    """Script paths a document names that neither exist nor are declared planned.

    Separated from check_consistency so it can be tested against a fixture tree
    rather than only against this repository, where every referenced script
    happens to exist and the check would pass without ever being shown able to
    fail.
    """
    return [p for p in sorted(set(SCRIPT_RE.findall(text)))
            if p not in planned and not (root / p).exists()]


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

    # Every script the plans name must be in the tree, or be declared in the
    # architecture plan as something a stage still has to create. This is the
    # general form of the scope_measure defect: three documents carried an
    # instruction to run a script that had only ever lived in a scratchpad, and
    # nothing could tell that from a script not yet written. The declaration makes
    # the difference explicit and therefore checkable.
    plan_text = _read(PLAN)
    planned = extract_planned(plan_text) if plan_text is not None else set()
    for path in (PLAN, SPEC):
        text = _read(path)
        if text is None:
            continue
        missing = unresolved_scripts(text, planned)
        findings.append(("PASS" if not missing else "FAIL",
                         f"{path.name}: every script it names exists or is declared "
                         f"as planned"
                         + ("" if not missing
                            else f" ({len(missing)} missing: {', '.join(missing)})")))

    # A declaration that has come true is a stale exemption, so it is reported.
    # INFO rather than FAIL: the moment to remove the line is just after the build,
    # and failing the suite between those two acts would punish the correct order.
    built = sorted(p for p in planned if Path(p).exists())
    if built:
        findings.append(("INFO", f"{PLAN.name}: planned artefact(s) now built, remove "
                                 f"from the declaration: {', '.join(built)}"))

    text = _read(PLAN)
    if text is not None:
        specs = extract_block(text)
        findings.append(("PASS" if specs else "FAIL",
                         f"{PLAN.name}: carries a usable executable classification block"))
        spec_text = _read(SPEC)
        if spec_text is not None:
            findings.append(("PASS" if extract_block(spec_text) is None else "FAIL",
                             f"{SPEC.name}: does NOT carry a second classification block"))

    # The review baseline holds the version the reviewing AI last reviewed, and it
    # is refreshed only once a review round has FINISHED. So drift between a plan
    # and its baseline is the normal, correct state for most of an item's life: it
    # is the diff the next review reads. It is reported, never failed.
    #
    # This used to be asserted the other way, as "byte-identical or FAIL (refresh
    # before a review round)". That encoded the opposite convention, told the reader
    # to do the one thing that destroys the diff, and was red for the whole duration
    # of any correction pass, which is the state that trains people to ignore a
    # check. What is genuinely a defect is a baseline that is absent, because then
    # there is no diff surface at all.
    for baseline, live in BASELINES:
        level, added, removed = compare_to_baseline(_read(baseline), _read(live))
        if level == "SKIP":
            findings.append(("SKIP", f"{live.name} not present; baseline not compared"))
        elif level == "FAIL":
            findings.append(("FAIL", f"{baseline.name} is missing, so the next review "
                                     f"of {live.name} has no diff surface"))
        elif not added and not removed:
            findings.append(("INFO", f"{live.name} is identical to {baseline.name}: "
                                     f"no changes since the last review round"))
        else:
            findings.append(("INFO", f"{live.name} differs from {baseline.name} "
                                     f"(+{added} / -{removed} lines); that diff is "
                                     f"what the next review reads. Refresh the "
                                     f"baseline only after that review has finished."))


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
