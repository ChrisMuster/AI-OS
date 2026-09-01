#!/usr/bin/env python3
"""Boundary completeness: every gitignored path has exactly one owner.

A file in neither repository and not classified as Category B or Category C is
silently unprotected. It exists on one machine only, with no history and no
backup. That is the failure that destroyed the backlog on 2026-08-04, and it is
what this check exists to make visible.

It is the only instrument in the design that a broken allowlist does not blind.
Three mechanisms read the allowlist to decide what to look at: layer 1
registration, the layer 3 scan, and the allowlist test. If a pattern silently
stops matching a file type, all three go blind in the same instant and in the
same way, because a scan cannot report a file it was never told to look for.
**This check starts from the disk and treats the allowlist as the thing under
test.** Preserve that direction if it is ever rewritten.

Six conditions, per the architecture plan's section 3 exit-code table:

  a path claimed by two categories                                      FAIL
  a path under any raw/ claimed by Category A                           FAIL
  a path in an authored folder that is not Category A, and that no
      line of the declared-exclusions block carves out                  FAIL
  an exclusion in the allowlist block that reaches an authored folder
      and is not declared                                               FAIL
  a path the personal repository tracks that Category B or C claims     FAIL
  a path relying on the Category C default because no block names it    REPORT

The last one reports rather than fails on purpose. Under the Category C default
an unnamed path is classified, not unclassified, so the finding is a prompt to
decide whether it belongs in A or in C. Making it fail would turn an ordinary
new file into a build blocker.

**Which git context each question is asked in is part of the specification, not
an implementation detail.** Measured on a fixture, walking the ignored set in
public-repository context returned 53 paths and one relying on the default,
while the same walk in the personal repository's context returned 33 and
fifteen. In that context the walk omits every file already seeded, which makes
the authored-folder invariant vacuous, and it adds every publicly tracked file,
because the personal repository's own info/exclude marks them ignored there. So
the first five questions are asked in PUBLIC context and only the post-seed
sweep uses --git-dir.

Read-only. It writes nothing, so it carries no --dry-run.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import allowlist  # noqa: E402  (the shared block parser and git helpers)

sys.stdout.reconfigure(encoding="utf-8")

LABELS = (allowlist.BLOCK_LABEL, allowlist.CATEGORY_B_LABEL,
          allowlist.CATEGORY_C_LABEL, allowlist.AUTHORED_FOLDERS_LABEL,
          allowlist.DECLARED_EXCLUSIONS_LABEL)


# ------------------------------------------------------------------ predicates

def under_raw(path):
    """True when any DIRECTORY component of the path is named `raw`.

    A segment test, deliberately, and not a `wikis/` prefix test. The plan is
    explicit that this invariant is wider than the `wikis/**/raw/**` pathspec
    that carves raw material out of the allowlist block: that pathspec says
    which files the personal repository declines to capture today, while this is
    an invariant about where bulk material may EVER be claimed from, and it has
    to hold for a raw/ folder created somewhere the pathspec does not reach.

    Both readings return the same answer on the tree as it stands, because every
    raw/ directory in it sits under wikis/. Testing cannot tell them apart, so
    the choice comes from the specification rather than from a green run, and
    the self-test plants a raw/ folder outside wikis/ to keep it that way.

    The final component is excluded because a FILE named `raw` is not a folder
    of bulk material.
    """
    return "raw" in path.split("/")[:-1]


def block_specs(plan, label):
    """One labelled block's pathspecs, or None when absent.

    Read through allowlist.extract_block rather than a second parser, for the
    same reason the pathspecs are read in place rather than copied: a second
    copy of the classification is the defect the whole of section 3 removes.
    """
    return allowlist.read_specs(plan=plan, label=label)


# ------------------------------------------------------------------ the analysis

def classify(plan, project=None):
    """Resolve every block in PUBLIC context and return the sets.

    Returns a dict, or None under `error` if a block is missing or git could not
    answer. An unanswerable question is never returned as an empty set: a zero
    is the one result that looks identical whether the instrument works or not.
    """
    specs = {label: block_specs(plan, label) for label in LABELS}
    missing = [label for label, value in specs.items() if value is None]
    if missing:
        return {"error": f"the plan carries no usable block(s): {', '.join(missing)}"}

    everything = allowlist.public_selection([])
    cat_a = allowlist.public_selection(specs[allowlist.BLOCK_LABEL])
    cat_b = allowlist.public_selection(specs[allowlist.CATEGORY_B_LABEL])
    cat_c = allowlist.public_selection(specs[allowlist.CATEGORY_C_LABEL])
    authored = allowlist.public_selection(specs[allowlist.AUTHORED_FOLDERS_LABEL])
    if any(s is None for s in (everything, cat_a, cat_b, cat_c, authored)):
        return {"error": "git could not resolve one or more blocks"}

    # An exclusion resolved as written selects the COMPLEMENT, so each one is
    # inverted before being asked what it reaches. Measured on this tree, the
    # raw-material exclusion resolved as written returns 178,544 paths, meaning
    # every ignored file except the ones it means.
    exclusions = [s for s in specs[allowlist.BLOCK_LABEL] if s.startswith(":(exclude")]
    reach = {}
    for spec in exclusions:
        carved = allowlist.public_selection([allowlist.positive(spec)])
        if carved is None:
            return {"error": f"git could not resolve the exclusion {spec}"}
        reach[spec] = carved

    declared = specs[allowlist.DECLARED_EXCLUSIONS_LABEL]
    covered_by_declared = set()
    for spec in declared:
        carved = reach.get(spec)
        if carved is None:
            carved = allowlist.public_selection([allowlist.positive(spec)])
            if carved is None:
                return {"error": f"git could not resolve the declared exclusion {spec}"}
        covered_by_declared |= carved

    return {
        "error": None,
        "specs": specs,
        "everything": everything,
        "A": cat_a,
        "B": cat_b,
        "C": cat_c,
        "authored": authored,
        "reach": reach,
        "declared": declared,
        "covered_by_declared": covered_by_declared,
    }


def personal_tracked(git_dir, project=None):
    """What the personal repository actually holds. The one --git-dir question."""
    project = os.path.abspath(project or os.getcwd())
    out = allowlist._run(["--git-dir", os.path.abspath(git_dir),
                          "--work-tree", project, "ls-files", "-z"])
    return None if out is None else set(allowlist._paths(out))


def check(plan, git_dir, project=None, sample=5):
    """Run all six conditions. Returns (findings, failed).

    findings is a list of (level, message) with levels FAIL, REPORT, PASS, INFO.
    """
    findings = []
    state = classify(plan, project=project)
    if state["error"]:
        return [("FAIL", state["error"])], True

    A, B, C = state["A"], state["B"], state["C"]
    everything, authored = state["everything"], state["authored"]

    def show(paths):
        ordered = sorted(paths)
        head = ", ".join(ordered[:sample])
        return head + (f", and {len(ordered) - sample} more"
                       if len(ordered) > sample else "")

    # --- 1. a path claimed by two categories
    collisions = (A & B) | (A & C) | (B & C)
    if collisions:
        findings.append(("FAIL", f"{len(collisions)} path(s) claimed by two "
                                 f"categories: {show(collisions)}"))
    else:
        findings.append(("PASS", "no path is claimed by two categories"))

    # --- 2. invariant one: nothing under any raw/ is Category A
    # A collision check cannot catch this. The archived wiki's raw/ folder was
    # claimed by exactly one category, the wrong one, and read as clean for two
    # days.
    raw_in_a = {p for p in A if under_raw(p)}
    if raw_in_a:
        findings.append(("FAIL", f"{len(raw_in_a)} path(s) under a raw/ segment "
                                 f"are Category A: {show(raw_in_a)}"))
    else:
        findings.append(("PASS", "nothing under any raw/ segment is Category A"))

    # --- 3. invariant two: nothing in an authored folder is anything but
    #        Category A, except where a DECLARED exclusion carves it out.
    # The exception is membership of the declared set, never the mere existence
    # of an exclusion line: phrased that way, any exclusion added later would
    # satisfy it by existing, and a line written a shade too wide could carve
    # authored files out of Category A and be reported as correct.
    authored_not_a = authored - A - state["covered_by_declared"]
    if authored_not_a:
        findings.append(("FAIL", f"{len(authored_not_a)} authored path(s) are not "
                                 f"Category A and no declared exclusion covers "
                                 f"them: {show(authored_not_a)}"))
    else:
        findings.append(("PASS", "every authored path is Category A, or is "
                                 "carved out by a declared exclusion"))

    # --- 4. every exclusion reaching an authored folder is declared
    undeclared = sorted(spec for spec, carved in state["reach"].items()
                        if (carved & authored) and spec not in state["declared"])
    if undeclared:
        findings.append(("FAIL", f"{len(undeclared)} exclusion(s) reach an authored "
                                 f"folder without being declared: "
                                 f"{', '.join(undeclared)}"))
    else:
        findings.append(("PASS", "every exclusion reaching an authored folder is "
                                 "declared"))

    # --- 5. the post-seed tracked sweep, the only question asked in the
    #        personal repository's own context.
    tracked = personal_tracked(git_dir, project=project)
    if tracked is None:
        findings.append(("FAIL", "git could not read the personal repository's "
                                 "tracked paths, so the post-seed sweep did not run"))
    elif not tracked:
        # Refusing here is a deliberate extension of the plan's rule that
        # --git-dir is required rather than optional. Run before the seed commit
        # the index is empty and this sweep passes without being able to fail,
        # which is the shape of a check that cannot fail, and the Stage A
        # acceptance row it discharges would be falsely green.
        findings.append(("FAIL", "the personal repository tracks nothing, so the "
                                 "post-seed sweep cannot fail and its acceptance "
                                 "row is not discharged. Run this after the seed "
                                 "commit."))
    else:
        bad = {p for p in tracked if p in B or p in C}
        if bad:
            findings.append(("FAIL", f"{len(bad)} path(s) tracked by the personal "
                                     f"repository are claimed by Category B or C: "
                                     f"{show(bad)}"))
        else:
            findings.append(("PASS", f"none of the {len(tracked)} path(s) the "
                                     f"personal repository tracks is Category B or C"))

    # --- 6. relying on the Category C default. REPORT ONLY.
    orphans = everything - A - B - C
    if orphans:
        findings.append(("REPORT", f"{len(orphans)} path(s) rely on the Category C "
                                   f"default because no block names them: "
                                   f"{show(orphans)}"))
    else:
        findings.append(("PASS", "no path relies on the Category C default"))

    findings.append(("INFO", f"ignored set {len(everything)} path(s): "
                             f"A={len(A)} B={len(B)} C={len(C)}"))

    failed = any(level == "FAIL" for level, _ in findings)
    return findings, failed


# -------------------------------------------------------------------- self-test

def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _plan(blocks):
    """A minimal document carrying the five labelled blocks, as the real one does."""
    out = ["# Fixture plan", ""]
    for label, lines in blocks.items():
        out.append("```" + label)
        out.extend(lines)
        out.append("```")
        out.append("")
    return "\n".join(out) + "\n"


def _seed(root, git_dir, paths):
    """Build a personal repository over the fixture and commit the given paths."""
    allowlist._run(["init", "--bare", "-q", str(git_dir)])
    if paths:
        allowlist._run(["--git-dir", str(git_dir), "--work-tree", str(root),
                        "add", "-f", "--"] + list(paths))
        allowlist._run(["--git-dir", str(git_dir), "--work-tree", str(root),
                        "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-qm", "seed"])


def self_test():
    """Prove every failing condition can fire, and that a clean tree passes.

    Real fixtures rather than stubs. This module's whole subject is what git
    reports about a working tree, so a stubbed git would be testing the stub.

    The dirty fixture carries every fault at once, which also proves they do not
    mask one another. Two of its faults are the ones a green run on the real
    tree can never exercise:

      - the raw/ folder sits OUTSIDE wikis/, so a prefix test passes and only a
        segment test fails. On the real tree both readings agree, so nothing
        there can tell them apart.
      - an exclusion carves into an authored folder without being declared,
        which is the accident the declared-set rule exists to catch and which
        the real tree, being correct, never presents.
    """
    import tempfile
    results = []
    cwd = os.getcwd()

    def want(label, ok):
        results.append((label, bool(ok)))

    with tempfile.TemporaryDirectory(prefix="boundary-selftest-") as tmp:
        tmp = Path(tmp)

        # ------------------------------------------------------- dirty fixture
        dirty = tmp / "dirty"
        _write(dirty / ".gitignore",
               ".env\nnotes/*\nbulk/*\nauthored/*\nextra/*\nlost/*\n")
        _write(dirty / ".env", "SECRET=1\n")
        _write(dirty / "notes" / "a.md", "x\n")
        _write(dirty / "bulk" / "raw" / "big.bin", "x\n")
        _write(dirty / "authored" / "doc.md", "x\n")
        _write(dirty / "authored" / "state" / "x.json", "{}\n")
        _write(dirty / "extra" / "dup.md", "x\n")
        _write(dirty / "lost" / "orphan.md", "x\n")
        dirty_plan = dirty / "PLAN.md"
        _write(dirty_plan, _plan({
            "allowlist": [":(glob)notes/**", ":(glob)authored/**",
                          ":(glob)bulk/**", ":(glob)extra/**",
                          ":(exclude,glob)authored/state/**"],
            "category-b": [".env"],
            "category-c": [":(glob)extra/**"],
            "authored-folders": [":(glob)authored/**"],
            # Present but irrelevant, so the block exists while the exclusion
            # above stays undeclared. An absent block would fail for a different
            # reason and prove nothing about this one.
            "declared-exclusions": [":(exclude,glob)**/.nothing/**"],
        }))
        try:
            os.chdir(dirty)
            allowlist._run(["init", "-q", "."])
            allowlist._run(["add", "-f", ".gitignore", "PLAN.md"])
            allowlist._run(["-c", "user.email=t@t", "-c", "user.name=t",
                            "commit", "-qm", "public"])
            gd = tmp / "dirty-personal.git"
            _seed(dirty, gd, [".env", "extra/dup.md"])

            findings, failed = check(Path("PLAN.md"), str(gd))
            text = " | ".join(f"{lvl}:{msg}" for lvl, msg in findings)
            fails = [m for lvl, m in findings if lvl == "FAIL"]

            want("a path claimed by two categories is caught",
                 any("claimed by two categories" in m for m in fails))
            want("a raw/ path claimed by Category A is caught",
                 any("raw/ segment" in m for m in fails))
            want("the raw/ test is a segment test, not a wikis/ prefix test",
                 any("bulk/raw/big.bin" in m for m in fails))
            want("an authored path that is not Category A is caught",
                 any("authored path(s) are not" in m for m in fails))
            want("an undeclared exclusion reaching an authored folder is caught",
                 any("without being declared" in m for m in fails))
            want("a Category B path tracked by the personal repo is caught",
                 any("Category B or C" in m and ".env" in m for m in fails))
            want("a Category C path tracked by the personal repo is caught",
                 any("Category B or C" in m and "extra/dup.md" in m for m in fails))
            want("a path named by no block is REPORTED, not failed",
                 any(lvl == "REPORT" and "orphan.md" in msg
                     for lvl, msg in findings)
                 and not any("orphan.md" in m for m in fails))
            want("the check fails overall", failed)
        finally:
            os.chdir(cwd)

        # ------------------------------------------------------- clean fixture
        clean = tmp / "clean"
        _write(clean / ".gitignore", ".env\nnotes/*\nauthored/*\n")
        _write(clean / ".env", "SECRET=1\n")
        _write(clean / "notes" / "a.md", "x\n")
        _write(clean / "authored" / "doc.md", "x\n")
        _write(clean / "authored" / ".obsidian" / "workspace.json", "{}\n")
        clean_plan = clean / "PLAN.md"
        _write(clean_plan, _plan({
            "allowlist": [":(glob)notes/**", ":(glob)authored/**",
                          ":(exclude,glob)**/.obsidian/**"],
            "category-b": [".env"],
            "category-c": [":(glob)**/.obsidian/**"],
            "authored-folders": [":(glob)authored/**"],
            "declared-exclusions": [":(exclude,glob)**/.obsidian/**"],
        }))
        try:
            os.chdir(clean)
            allowlist._run(["init", "-q", "."])
            allowlist._run(["add", "-f", ".gitignore", "PLAN.md"])
            allowlist._run(["-c", "user.email=t@t", "-c", "user.name=t",
                            "commit", "-qm", "public"])
            gd = tmp / "clean-personal.git"
            _seed(clean, gd, ["notes/a.md", "authored/doc.md"])

            findings, failed = check(Path("PLAN.md"), str(gd))
            want("a correct tree does not fail", not failed)
            want("a declared exclusion carving an authored folder is allowed",
                 not any("authored path(s) are not" in m
                         for lvl, m in findings if lvl == "FAIL"))
            want("a correct tree reports no orphan",
                 not any(lvl == "REPORT" for lvl, _ in findings))

            # The vacuous-sweep control. Same correct tree, personal repository
            # never seeded: the sweep would pass without being able to fail, so
            # the check must refuse rather than report a green acceptance row.
            empty = tmp / "empty-personal.git"
            allowlist._run(["init", "--bare", "-q", str(empty)])
            findings, failed = check(Path("PLAN.md"), str(empty))
            want("an unseeded personal repo makes the sweep refuse, not pass",
                 failed and any("cannot fail" in m
                                for lvl, m in findings if lvl == "FAIL"))
        finally:
            os.chdir(cwd)

    print("# Boundary completeness self-test\n")
    for label, ok in results:
        print(f"- {'PASS' if ok else 'FAIL'}: {label}")
    failed_rows = [r for r in results if not r[1]]
    print(f"\n{'All self-tests passed.' if not failed_rows else f'{len(failed_rows)} failure(s).'}")
    return 1 if failed_rows else 0


# ------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        description="Boundary completeness for the three categories (read-only).")
    ap.add_argument("--git-dir", help="The personal repository's git directory. "
                                      "Required: the post-seed sweep asks it what "
                                      "it holds, and a run without it does five "
                                      "sixths of the check while appearing to do "
                                      "all of it.")
    ap.add_argument("--plan", default=str(allowlist.PLAN),
                    help="The document carrying the labelled blocks")
    ap.add_argument("--self-test", action="store_true",
                    help="Prove every failing condition can fire")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if not args.git_dir:
        print("--git-dir is required. Five of the six conditions are asked in "
              "public-repository context, but the post-seed tracked sweep has to "
              "ask the personal repository what it holds, so a run without it "
              "would do five sixths of the check while appearing to do all of it.")
        return 1
    if not Path(args.git_dir).exists():
        print(f"No repository at {args.git_dir}; the check did not run.")
        return 1

    findings, failed = check(Path(args.plan), args.git_dir)

    print("# Boundary completeness\n")
    for level in ("FAIL", "REPORT", "PASS", "INFO"):
        rows = [f for f in findings if f[0] == level]
        if not rows:
            continue
        print(f"## {level} ({len(rows)})")
        for _, msg in rows:
            print(f"- {msg}")
        print()

    print("Boundary incomplete." if failed else "Every ignored path has exactly one owner.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
