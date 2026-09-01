#!/usr/bin/env python3
"""The pre-seed gate: the last thing standing between the working tree and a
permanent commit into the personal repository.

Three parts behind one entry point, per the architecture plan's Stage A step 3:

  .env excluded   no selected path, lowercased, is `.env` or ends `/.env`   BLOCK
  No overlap      the selection shares nothing with the public tracked set,
                  and no file is both publicly tracked and ignored          BLOCK
  Size flag       no selected file exceeds 25 MiB                           REPORT

It exits non-zero if either blocking part fails and zero otherwise, so the seed
commit can be made conditional on the exit code. The size flag prints and never
changes the exit code.

Why an exit code rather than an instruction: AGENTS.md requires a "checks pass"
claim to be a script's exit code rather than prose. It matters more here than
anywhere else in the project, because the personal repository deliberately has
no hooks at all through Stage A and never a pre-commit hook, so there is no
automatic backstop underneath these two checks, and the commit they gate is both
permanent and bulk.

It is deliberately NOT `run.py --check`. That command answers a different
question, whether the documents and the classification are in good order, and it
can fail on a missing review baseline, which says nothing about whether it is
safe to seed. A gate that blocks a build over a baseline copy gets routed around.
This asserts only what makes the seed commit unsafe.

The selection is not reimplemented here. It comes from allowlist.py, which is the
single executable copy of the rule, so this gate cannot resolve the pathspecs
differently from the build it is gating.

Read-only. It writes nothing at all, which is why it carries no --dry-run: the
project's dry-run requirement applies to scripts that modify files.
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import allowlist  # noqa: E402  (the shared selection rule, alongside this file)

sys.stdout.reconfigure(encoding="utf-8")

# 25 MiB. Written as both spellings because the plan states both and a reader
# checking one against the other should not have to do the arithmetic.
SIZE_LIMIT = 26_214_400  # 25 * 1024 * 1024


# ----------------------------------------------------------------- predicates

def is_env_path(path):
    """True for the secrets file, at the root or in any subdirectory.

    Case-folded, and this is the ONE declared exception to the plan's rule that
    every path predicate compares case-sensitively. The reason is the filesystem
    rather than git: the machines this runs on are case-insensitive, so a file
    named `.ENV` or `.Env` can exist, is the same file to the operating system,
    and would be waved past a case-sensitive test by the one gate that exists to
    stop it. A gate erring wide costs a false positive; erring narrow costs the
    secrets.

    It matches by path rather than by resolving the plan's `category-b` block.
    That block is a bare `.env`, which as a git pathspec means the root file
    alone, so reading the gate off it would leave a `.env` in any subdirectory
    selected and unremarked. The two readings differ only on a file that does
    not exist yet, which is exactly why the assertion states the broad one
    rather than leaving it to be inferred.
    """
    low = path.lower()
    return low == ".env" or low.endswith("/.env")


def env_violations(paths):
    """Every selected path the secrets rule forbids. Empty is the safe answer."""
    return sorted(p for p in paths if is_env_path(p))


def oversized(paths, project=None, limit=SIZE_LIMIT):
    """Selected files larger than the limit, as (path, bytes), largest first.

    `limit` is an argument rather than a constant read directly so the control
    can prove the predicate fires without writing a 25 MiB fixture to disk. The
    real threshold is asserted separately.

    A path that has gone since the selection was computed is skipped rather than
    raising: this is a flag, and it must not be the thing that stops a build.
    """
    root = Path(project or os.getcwd())
    hits = []
    for p in paths:
        target = root / p
        try:
            if target.is_file():
                size = target.stat().st_size
                if size > limit:
                    hits.append((p, size))
        except OSError:
            continue
    return sorted(hits, key=lambda row: row[1], reverse=True)


# --------------------------------------------------------------------- the gate

def run_checks(specs, git_dir=None, project=None, limit=SIZE_LIMIT):
    """Run all three parts. Returns (findings, blocked).

    findings is a list of (level, message) with levels FAIL, FLAG, PASS and INFO.
    blocked is True when the seed commit must not proceed.

    An unanswerable question blocks. If git cannot resolve the selection there is
    no evidence the commit is safe, and a gate that treats "I could not tell" as
    a pass is not a gate. That is the opposite of the read-only checks elsewhere
    in this workflow, which degrade to SKIPPED, and the difference is deliberate:
    those report on documents, this one guards a permanent commit.
    """
    findings = []
    out = allowlist.select(specs, git_dir=git_dir, project=project)
    selected, overlap = out["final"], out["overlap"]

    if selected is None:
        findings.append(("FAIL", "git could not resolve the selection, so the "
                                 "seed commit cannot be shown to be safe"))
        return findings, True

    # --- part 1: .env excluded from the personal repository as well as the public one
    leaks = env_violations(selected)
    if leaks:
        findings.append(("FAIL", f"a secrets file is in the selection: "
                                 f"{', '.join(leaks)}"))
    else:
        findings.append(("PASS", "no secrets file is in the selection"))

    # --- part 2: no overlap, in two halves
    # The first is the consequence, measured in the context the build meets it.
    # The second is the condition that causes it, which is the root-cause check:
    # while no file is both publicly tracked and matched by an ignore rule, the
    # two git contexts return the same answer. It is held up by the negation
    # lines in .gitignore and by nothing else, so removing one breaks it with no
    # other symptom.
    if overlap:
        findings.append(("FAIL", f"{len(overlap)} file(s) in the selection are "
                                 f"already tracked publicly, e.g. "
                                 f"{sorted(overlap)[0]}"))
    else:
        findings.append(("PASS", "the selection contains no publicly tracked file"))

    both = allowlist.tracked_and_ignored()
    if both is None:
        findings.append(("FAIL", "git could not answer the tracked-and-ignored "
                                 "check, so the invariant cannot be confirmed"))
    elif both:
        findings.append(("FAIL", f"{len(both)} file(s) are both publicly tracked "
                                 f"and matched by an ignore rule, e.g. "
                                 f"{sorted(both)[0]}. Fix .gitignore before seeding."))
    else:
        findings.append(("PASS", "no file is both publicly tracked and ignored"))

    # --- part 3: the size flag. Reports, never blocks.
    # The four authored personal folders' include patterns are type-blind, so a
    # large file of any kind dropped into one is selected and would be committed
    # permanently with nobody looking. An oversized file is not necessarily
    # wrong, only worth seeing, which is why this is a flag.
    big = oversized(selected, project=project, limit=limit)
    if big:
        for path, size in big:
            findings.append(("FLAG", f"{path} is {size / 1048576:.2f} MiB, over "
                                     f"the {limit / 1048576:.0f} MiB threshold"))
    else:
        findings.append(("PASS", f"no selected file exceeds "
                                 f"{limit / 1048576:.0f} MiB"))

    total = sum((Path(project or os.getcwd()) / p).stat().st_size
                for p in selected if (Path(project or os.getcwd()) / p).is_file())
    findings.append(("INFO", f"selection is {len(selected)} file(s), "
                             f"{total / 1048576:.2f} MiB"))

    blocked = any(level == "FAIL" for level, _ in findings)
    return findings, blocked


# ------------------------------------------------------------------- self-test

def _git(args, cwd=None):
    subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True,
                   encoding="utf-8")


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def self_test():
    """Prove each blocking part can fail, and that a clean tree passes.

    A gate whose whole purpose is to report nothing is indistinguishable from
    one that cannot report anything until it has been shown to fire. Both of
    this project's zero-results traps say the same thing: an absent finding is a
    measurement and needs its own positive control, and a clean check is not
    evidence unless it could have failed for the reason claimed.

    So this builds two real repositories under tempfile. The dirty one carries
    every fault at once, because each part is asserted individually and running
    them together also proves they do not mask one another. The clean one is the
    negative control: without it, a gate that failed unconditionally would pass
    every positive control here and still be useless.

    The size control uses a small limit against a small file rather than writing
    25 MiB to disk. The real threshold is asserted separately, so nothing rests
    on the fixture's number.
    """
    results = []
    cwd = os.getcwd()

    def check(label, ok):
        results.append((label, bool(ok)))

    with tempfile.TemporaryDirectory(prefix="preseed-selftest-") as tmp:
        # ---------------------------------------------------------- dirty tree
        dirty = Path(tmp) / "dirty"
        _write(dirty / ".gitignore", ".env\nsub/.env\ndeep/.ENV\nnotes/*\n")
        _write(dirty / ".env", "SECRET=1\n")
        _write(dirty / "sub" / ".env", "SECRET=2\n")
        _write(dirty / "deep" / ".ENV", "SECRET=3\n")
        # The bulk sits on private.md, not on oops.md. oops.md is publicly
        # tracked and is therefore subtracted out of the selection before the
        # size check sees it, so a large file there would leave this control
        # passing vacuously. The first run of this self-test did exactly that.
        _write(dirty / "notes" / "private.md", "x" * 40 + "\n")
        _write(dirty / "notes" / "oops.md", "y\n")
        try:
            os.chdir(dirty)
            _git(["init", "-q", "."])
            # oops.md is force-added, so it is tracked publicly AND matched by an
            # ignore rule: the one state where the two git contexts must disagree.
            _git(["add", "-f", ".gitignore", "notes/oops.md"])
            _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed"])

            specs = [".env", ":(glob)sub/.env", ":(glob)deep/.ENV", ":(glob)notes/*"]
            findings, blocked = run_checks(specs, limit=10)
            text = " | ".join(m for _, m in findings)

            check("a root .env in the selection is caught", ".env" in text)
            check("a .env in a subdirectory is caught, not only the root file",
                  "sub/.env" in text)
            check("a case-variant .ENV is caught (the declared case-folded row)",
                  "deep/.ENV" in text or "deep/.env" in text)
            check("a publicly tracked file in the selection is caught",
                  any(l == "FAIL" and "tracked publicly" in m for l, m in findings))
            check("the tracked-and-ignored invariant is caught",
                  any(l == "FAIL" and "matched by an ignore rule" in m
                      for l, m in findings))
            check("an oversized file is flagged",
                  any(l == "FLAG" for l, _ in findings))
            check("the gate blocks when a blocking part fails", blocked)
        finally:
            os.chdir(cwd)

        # ---------------------------------------------------------- clean tree
        clean = Path(tmp) / "clean"
        _write(clean / ".gitignore", "notes/*\n")
        _write(clean / "notes" / "private.md", "x\n")
        try:
            os.chdir(clean)
            _git(["init", "-q", "."])
            _git(["add", "-f", ".gitignore"])
            _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed"])

            findings, blocked = run_checks([":(glob)notes/*"])
            check("a clean tree is not blocked", not blocked)
            check("a clean tree reports no FAIL",
                  not any(l == "FAIL" for l, _ in findings))
            check("a clean tree does not flag a small file",
                  not any(l == "FLAG" for l, _ in findings))
        finally:
            os.chdir(cwd)

        # -------------------------------------------------- flagged but clean
        # The size flag reports and must never change the exit code. Nothing
        # above tests that: the clean tree raises no flag, and the dirty tree is
        # blocked by its failures whatever the flag does. So a mutation making
        # the flag block passed every other control here, which is why this
        # fixture exists. It is the one tree where the flag decides the answer.
        flagged = Path(tmp) / "flagged"
        _write(flagged / ".gitignore", "notes/*\n")
        _write(flagged / "notes" / "big.md", "x" * 400)
        try:
            os.chdir(flagged)
            _git(["init", "-q", "."])
            _git(["add", "-f", ".gitignore"])
            _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed"])

            findings, blocked = run_checks([":(glob)notes/*"], limit=10)
            check("an oversized file raises a flag on an otherwise clean tree",
                  any(l == "FLAG" for l, _ in findings))
            check("the size flag reports and does NOT block", not blocked)
            check("a flagged tree still reports no FAIL",
                  not any(l == "FAIL" for l, _ in findings))
        finally:
            os.chdir(cwd)

    # The fixture deliberately uses a small limit, so the real one is asserted
    # here rather than being taken on trust from a control that never used it.
    check("the real threshold is 25 MiB", SIZE_LIMIT == 25 * 1024 * 1024)

    print("# Pre-seed gate self-test\n")
    for label, ok in results:
        print(f"- {'PASS' if ok else 'FAIL'}: {label}")
    failed = [r for r in results if not r[1]]
    print(f"\n{'All self-tests passed.' if not failed else f'{len(failed)} failure(s).'}")
    return 1 if failed else 0


# ------------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(
        description="Pre-seed gate for the personal repository (read-only).")
    ap.add_argument("--git-dir",
                    help="The personal repository's git directory")
    ap.add_argument("--self-test", action="store_true",
                    help="Prove each blocking part can fail, and that a clean "
                         "tree passes")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    specs = allowlist.read_specs()
    if specs is None:
        print(f"No usable ```{allowlist.BLOCK_LABEL} block in {allowlist.PLAN}; "
              f"the gate cannot run and the seed commit is not cleared.")
        return 1

    if args.git_dir and not Path(args.git_dir).exists():
        print(f"No repository at {args.git_dir}; the gate did not run.")
        return 1

    findings, blocked = run_checks(specs, git_dir=args.git_dir)

    print("# Pre-seed gate\n")
    for level in ("FAIL", "FLAG", "PASS", "INFO"):
        rows = [f for f in findings if f[0] == level]
        if not rows:
            continue
        print(f"## {level} ({len(rows)})")
        for _, msg in rows:
            print(f"- {msg}")
        print()

    if blocked:
        print("BLOCKED: do not seed. Fix the failure(s) above and run again.")
        return 1
    flags = [f for f in findings if f[0] == "FLAG"]
    if flags:
        print(f"Cleared to seed, with {len(flags)} file(s) flagged for review.")
    else:
        print("Cleared to seed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
