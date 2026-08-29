#!/usr/bin/env python3
"""Selection of the files that belong in the personal repository.

This module is the single executable copy of the selection rule. The
verification suite (run.py) imports it and the Stage A build calls it, so a
check and the build it is meant to be checking cannot disagree about which
files would be staged. That was the defect this module was written to remove:
the check resolved the pathspecs in one git context and the build resolved
them in another, so the check's safety assertion could not fail for the reason
it claimed to be testing.

It is also pure Python over the git CLI, with no shell pipeline, so every AI
that can run a script can run the build step. The previous form was a shell
pipeline through `xargs`, which is unavailable in a PowerShell session and
therefore not portable across this project's AI roster.

Two git contexts matter and they are not interchangeable:

  public context   `git ls-files ...` with no override. "Untracked" means
                   untracked by the public repository.
  shadow context   `git --git-dir=<personal> --work-tree=<project> ls-files ...`
                   "Untracked" means untracked by the personal repository,
                   which before the seed commit is everything.

A file that the public repository tracks *and* an ignore rule still matches is
returned by the second and not by the first. Selecting in shadow context and
subtracting the public repository's tracked set is what makes the two agree.

Read-only unless `--stage` is passed. `--stage` writes to the personal
repository's index only, never to the working tree, and honours `--dry-run`.
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

PLAN = Path("SYNC-ARCHITECTURE-PLAN.md")
BLOCK_LABEL = "allowlist"
BLOCK_MARKER = "```" + BLOCK_LABEL + "\n"

# The other labelled blocks the plan carries. Named here so a caller asks for a
# block by a constant rather than by a string literal that can drift from the
# document.
CATEGORY_B_LABEL = "category-b"
CATEGORY_C_LABEL = "category-c"
AUTHORED_FOLDERS_LABEL = "authored-folders"
DECLARED_EXCLUSIONS_LABEL = "declared-exclusions"

# Pathspecs are passed on the command line, which is length-limited on Windows.
# Staging is chunked rather than relying on a shell to split it.
_CHUNK_CHARS = 6000


# --------------------------------------------------------------------------- git

LAST_ERROR = ""


def _run(args, stdin_data=None, ok_codes=(0,)):
    """Run a git command, returning stdout, or None if it failed.

    ok_codes exists because some git commands use a non-zero exit to mean "no
    matches" rather than "error". check-ignore returns 1 when nothing matched,
    which is a legitimate and common answer here.

    A failure records git's own stderr in LAST_ERROR. An earlier version
    discarded it, and a staging run that git had refused outright printed
    nothing at all and looked like a no-op.
    """
    global LAST_ERROR
    proc = subprocess.run(
        ["git"] + args, capture_output=True, text=True,
        encoding="utf-8", input=stdin_data,
    )
    if proc.returncode not in ok_codes:
        LAST_ERROR = (proc.stderr or "").strip()
        return None
    return proc.stdout


def _paths(out):
    return [] if out is None else [p for p in out.split("\0") if p]


# ---------------------------------------------------------------- the allowlist

def extract_block(text, label=BLOCK_LABEL):
    """Pull the pathspecs out of one of the plan's labelled fenced blocks.

    The plan is the authority, so this reads it rather than a copy. A copy is
    what let a settled decision fail to reach the patterns that implement it.

    `label` selects which block. It defaulted to nothing at all until
    2026-08-29, when the plan gained four blocks rather than one and the
    single fixed label became the reason boundary.py could not be built from
    the document: two of its failure conditions are stated against the
    authored-folder list and the declared-exclusion set, and neither had any
    machine-readable form because this function could not have returned one.

    Comment lines are dropped, which is worth knowing rather than discovering.
    A grouping comment inside a block is invisible here, so a set identified
    only by a comment is not readable through this function and needs a block
    of its own. That is exactly why `authored-folders` exists.
    """
    marker = "```" + label + "\n"
    start = text.find(marker)
    if start == -1:
        return None
    start += len(marker)
    end = text.find("```", start)
    if end == -1:
        return None
    specs = []
    for line in text[start:end].splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            specs.append(line)
    return specs or None


def positive(spec):
    """Turn an exclusion pathspec into the positive one naming what it carves out.

    An `:(exclude,glob)X` spec resolved on its own selects the *complement*:
    measured on this tree, the raw-material exclusion alone returns 178,544
    paths, meaning every ignored file except the ones it means. So a check
    asking what an exclusion reaches cannot resolve it as written. Removing
    `exclude,` from the magic prefix is the inversion, and the plan states the
    rule alongside the blocks.

    A spec that is not an exclusion comes back unchanged.
    """
    if spec.startswith(":(exclude,"):
        return ":(" + spec[len(":(exclude,"):]
    if spec.startswith(":(exclude)"):
        return spec[len(":(exclude)"):]
    return spec


def read_specs(plan=PLAN, label=BLOCK_LABEL):
    """Return one block's pathspecs, or None if the plan or that block is absent."""
    if not Path(plan).exists():
        return None
    with open(plan, encoding="utf-8") as fh:
        return extract_block(fh.read(), label)


# ------------------------------------------------------------------ the contexts

def public_tracked():
    """Every path the public repository tracks. None if git is unavailable."""
    out = _run(["ls-files", "-z"])
    return None if out is None else set(_paths(out))


def public_selection(specs):
    """Resolve pathspecs against the ignored set in PUBLIC-repository context.

    This is the context the plan's own table assigns to the question "which
    category claims a path", and it is not interchangeable with the shadow one.
    In shadow context the same walk omits every already-seeded file and adds
    every publicly tracked file, because the personal repository's info/exclude
    marks those ignored there: measured on a fixture, 53 paths against 33, with
    the default-reliance count going from 1 to 15.

    None if git could not answer, so an unanswered question is never read as an
    empty selection.
    """
    out = _run(["ls-files", "--others", "--ignored", "--exclude-standard",
                "-z", "--"] + list(specs))
    return None if out is None else set(_paths(out))


def shadow_selection(specs, git_dir=None, project=None):
    """Resolve the pathspecs the way the Stage A build will resolve them.

    This is the selection in shadow context. Passing git_dir=None builds a
    throwaway empty repository, which is what a verification run wants: an
    empty index is exactly the state the personal repository is in immediately
    before its seed commit, so it is the honest thing to test against.
    """
    project = os.path.abspath(project or os.getcwd())

    def _select(gd):
        out = _run(["--git-dir", gd, "--work-tree", project,
                    "ls-files", "--others", "--ignored", "--exclude-standard",
                    "-z", "--"] + list(specs))
        return None if out is None else set(_paths(out))

    if git_dir is not None:
        return _select(os.path.abspath(git_dir))
    with tempfile.TemporaryDirectory(prefix="sync-arch-") as tmp:
        gd = os.path.join(tmp, "probe.git")
        if _run(["init", "--bare", "-q", gd]) is None:
            return None
        return _select(gd)


def tracked_and_ignored():
    """Paths that are both tracked publicly and matched by an ignore rule.

    This is the invariant the whole design rests on, and until it was written
    down nothing watched it. While it holds, the two contexts return the same
    answer and the build is safe. It is currently held up by the negation
    ("!") lines in .gitignore, so deleting one breaks it silently.

    --no-index is load-bearing. Without it git check-ignore refuses to report a
    tracked path as ignored, so the check would return an empty list whatever
    the truth was: an instrument that cannot produce the finding it exists to
    produce.
    """
    tracked = _run(["ls-files", "-z"])
    if tracked is None:
        return None
    paths = _paths(tracked)
    if not paths:
        return set()
    out = _run(["check-ignore", "--stdin", "-z", "--no-index"],
               stdin_data="\0".join(paths) + "\0", ok_codes=(0, 1))
    return None if out is None else set(_paths(out))


def tracked_by_both(git_dir, project=None):
    """Paths carried in BOTH repositories' indexes, once the personal one has some.

    This is the post-seed form of the no-overlap question and it is a different
    question from the one select() answers. select() asks whether the pending
    *selection* contains a publicly tracked file, which is answerable before
    anything is committed. This asks whether the two *indexes* share a path,
    which is what the Stage A verify line needs and which only becomes
    answerable once the seed commit exists.

    It is here rather than in a shell pipeline because the comparison is a set
    intersection, and the obvious shell spelling of it needs `comm`, which is
    present in Git Bash and absent from PowerShell. That made the verify step
    runnable on only some of this project's AIs, which is the same portability
    defect the selection itself was rewritten to remove.

    Returns None if git could not answer, so an unanswered question is never
    read as an empty answer.
    """
    project = os.path.abspath(project or os.getcwd())
    mine = _run(["--git-dir", os.path.abspath(git_dir), "--work-tree", project,
                 "ls-files", "--cached", "-z"])
    theirs = _run(["ls-files", "-z"])
    if mine is None or theirs is None:
        return None
    return set(_paths(mine)) & set(_paths(theirs))


# ------------------------------------------------------------------- the rule

def select(specs, git_dir=None, project=None):
    """The selection rule, in one place.

    Returns a dict:
      raw       what shadow context hands back, before any correction
      tracked   what the public repository tracks
      overlap   raw & tracked, the files that must never reach the seed commit
      final     raw - tracked, the set the build stages
    Any value is None if git could not answer, and the caller degrades rather
    than treating an unanswered question as a pass.
    """
    raw = shadow_selection(specs, git_dir=git_dir, project=project)
    tracked = public_tracked()
    if raw is None or tracked is None:
        return {"raw": raw, "tracked": tracked, "overlap": None, "final": None}
    return {
        "raw": raw,
        "tracked": tracked,
        "overlap": raw & tracked,
        "final": raw - tracked,
    }


# ---------------------------------------------------------------------- staging

def stage(paths, git_dir, project=None, dry_run=False):
    """Force-add the selected paths into the personal repository's index.

    Force-add is required: both repositories read the same worktree .gitignore,
    which outranks a repository's own info/exclude, so the personal repository
    cannot un-ignore by configuration. It is a one-time cost per file, because
    ignore rules stop applying once a file is tracked.
    """
    project = os.path.abspath(project or os.getcwd())
    git_dir = os.path.abspath(git_dir)
    ordered = sorted(paths)
    if dry_run:
        for p in ordered:
            print(f"[DRY RUN] would force-add {p}")
        print(f"[DRY RUN] {len(ordered)} file(s), 0 written")
        return True

    staged = 0
    batch, size = [], 0

    def flush(batch):
        if not batch:
            return True
        ok = _run(["--git-dir", git_dir, "--work-tree", project,
                   "add", "-f", "--"] + batch) is not None
        if not ok:
            print(f"git refused the force-add: {LAST_ERROR}", file=sys.stderr)
        return ok

    for p in ordered:
        if size + len(p) > _CHUNK_CHARS and batch:
            if not flush(batch):
                return False
            staged += len(batch)
            batch, size = [], 0
        batch.append(p)
        size += len(p) + 1
    if not flush(batch):
        return False
    staged += len(batch)
    print(f"Force-added {staged} file(s).")
    return True


# -------------------------------------------------------------------- self-test

def self_test():
    """Prove the module can tell the two contexts apart.

    A suite that only ever runs against a clean tree cannot distinguish "the
    contexts agree" from "I am not really asking two different questions". This
    builds a tree where a file is both tracked and ignored, which is the one
    state where a correct implementation must return different answers, and
    asserts all three properties that matter.
    """
    results = []
    with tempfile.TemporaryDirectory(prefix="sync-arch-selftest-") as tmp:
        proj = os.path.join(tmp, "proj")
        os.makedirs(os.path.join(proj, "notes"))
        cwd = os.getcwd()
        try:
            os.chdir(proj)
            with open(".gitignore", "w", encoding="utf-8", newline="\n") as fh:
                fh.write("notes/*\n")
            for name in ("private.md", "oops.md"):
                with open(os.path.join("notes", name), "w",
                          encoding="utf-8", newline="\n") as fh:
                    fh.write("x\n")
            _run(["init", "-q", "."])
            _run(["add", "-f", "notes/oops.md"])
            _run(["-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "-qm", "seed"])

            specs = ["notes/*"]
            pub = _run(["ls-files", "--others", "--ignored",
                        "--exclude-standard", "-z", "--"] + specs)
            pub = set(_paths(pub))
            out = select(specs)

            results.append((
                "public context hides the tracked-and-ignored file",
                "notes/oops.md" not in pub))
            results.append((
                "shadow context exposes it (the contexts really do differ)",
                out["raw"] is not None and "notes/oops.md" in out["raw"]))
            results.append((
                "the overlap is detected rather than silently passed",
                out["overlap"] == {"notes/oops.md"}))
            results.append((
                "the final selection excludes it",
                out["final"] == {"notes/private.md"}))
            results.append((
                "the invariant check names the offending file",
                tracked_and_ignored() == {"notes/oops.md"}))
        finally:
            os.chdir(cwd)

    print("# Selection self-test\n")
    for label, ok in results:
        print(f"- {'PASS' if ok else 'FAIL'}: {label}")
    failed = [r for r in results if not r[1]]
    print(f"\n{'All self-tests passed.' if not failed else f'{len(failed)} failure(s).'}")
    return 1 if failed else 0


# ------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        description="Select the files that belong in the personal repository.")
    ap.add_argument("--list", action="store_true",
                    help="Print the selection (default; read-only)")
    ap.add_argument("--stage", action="store_true",
                    help="Force-add the selection into the personal repository")
    ap.add_argument("--git-dir",
                    help="The personal repository's git directory")
    ap.add_argument("--dry-run", action="store_true",
                    help="With --stage, print what would be added and write nothing")
    ap.add_argument("--self-test", action="store_true",
                    help="Prove the module can distinguish the two git contexts")
    ap.add_argument("--tracked-by-both", action="store_true",
                    help="Report paths carried in both repositories' indexes "
                         "(the post-seed no-overlap check; needs --git-dir)")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if args.tracked_by_both:
        if not args.git_dir:
            print("--tracked-by-both requires --git-dir.")
            return 1
        if not Path(args.git_dir).exists():
            print(f"No repository at {args.git_dir}; no-overlap check not run.")
            return 1
        both = tracked_by_both(args.git_dir)
        if both is None:
            print("git could not answer; no-overlap check not run.")
            return 1
        if both:
            print(f"FAIL: {len(both)} file(s) tracked by both repositories:")
            for p in sorted(both):
                print(f"  {p}")
            return 1
        print("PASS: no file is tracked by both repositories.")
        return 0

    specs = read_specs()
    if specs is None:
        print(f"No usable ```allowlist block in {PLAN}; nothing to select.")
        return 1

    if args.git_dir and not Path(args.git_dir).exists():
        print(f"No repository at {args.git_dir}; selection not computed.")
        return 1

    out = select(specs, git_dir=args.git_dir)
    if out["final"] is None:
        print("git could not answer; selection not computed.")
        return 1

    both = tracked_and_ignored()
    if both:
        print(f"REFUSING: {len(both)} file(s) are both publicly tracked and "
              f"matched by an ignore rule, e.g. {sorted(both)[0]}")
        print("The personal repository must not take a file the public "
              "repository already tracks. Fix .gitignore before staging.")
        return 1

    if args.stage:
        if not args.git_dir:
            print("--stage requires --git-dir.")
            return 1
        return 0 if stage(out["final"], args.git_dir,
                          dry_run=args.dry_run) else 1

    for p in sorted(out["final"]):
        print(p)
    total = sum(Path(p).stat().st_size for p in out["final"] if Path(p).is_file())
    print(f"\n{len(out['final'])} file(s), {total / 1048576:.2f} MiB", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
