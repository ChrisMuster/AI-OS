#!/usr/bin/env python3
"""
run.py - Close-out verifier for Book Dragon.

Bundles the mechanical close-out checks into ONE pass/fail gate, so that a
"the checks pass" claim is this script's exit code rather than prose. It is the
executable backing for the Verification discipline rule in AGENTS.md.

Gates:
  - structural audit   (workflows/audit/scripts/run.py, called in-process)
  - link audit         (workflows/link-check/scripts/run.py, called in-process)
  - workflow test suites (workflows/*/tests/test_*.py and skills/*, as subprocesses)

The audit and link checks always run over the whole project. Only the TEST
breadth is scoped:

  default        run the tests for the workflows/skills changed vs git (affected)
  --scope all    run every discovered test suite (used by a full close-out)
  --scope NAME   run the test suite(s) for the workflow or skill called NAME

If git is unavailable or the changed set cannot be determined, the affected
scope falls back to running ALL tests - err toward more, never under-claim a pass.
Affected scope also escalates to ALL tests when the change touches project-wide
files no single suite owns (root-level *.md governance docs or templates/).

To keep the gate independent of whichever `python` is first on PATH, the script
re-execs under the project .venv interpreter when one exists (guarded against
recursion), so the documented `python ...` command stays simple and does not
depend on which interpreter is first on PATH.

A check that could not run at all (a degraded advisory hook - e.g. a guard whose
runtime is broken) is reported as DEGRADED: shown distinctly, non-blocking, and
never counted as a pass. --repair runs setup.py to fix the runtime and re-runs
the gates once; without it, the report prints the exact fix to run by hand.

This verifier does NOT replace the judgement steps of close-out (plan complete,
logs current, CONTEXT accurate) and never fires automatically; it is run by hand.

Exit code: 0 if every gate passed, 1 otherwise.

Usage:
  python workflows/close-out/scripts/run.py               # affected scope
  python workflows/close-out/scripts/run.py --scope all    # full close-out
  python workflows/close-out/scripts/run.py --scope audit  # one workflow
  python workflows/close-out/scripts/run.py --json
  python workflows/close-out/scripts/run.py --repair       # auto-fix DEGRADED checks
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SCRIPT_DIR = Path(__file__).resolve().parent
WORKFLOW_DIR = SCRIPT_DIR.parent                     # workflows/close-out
PROJECT_ROOT = WORKFLOW_DIR.parent.parent            # AI-OS
LOG_FILE = WORKFLOW_DIR / "LOG.md"
RESULT_FILE = WORKFLOW_DIR / "last-result.json"

AUDIT_RUN = PROJECT_ROOT / "workflows" / "audit" / "scripts" / "run.py"
LINK_RUN = PROJECT_ROOT / "workflows" / "link-check" / "scripts" / "run.py"
SETUP_PY = PROJECT_ROOT / "workflows" / "biblio-tools" / "scripts" / "setup.py"

TEST_ROOTS = ("workflows", "skills")
TEST_TIMEOUT = 600  # seconds per test file

# Re-exec guard: set on the child so the venv interpreter never loops back.
REEXEC_MARKER = "BOOK_DRAGON_CLOSE_OUT_REEXEC"


# ---------------------------------------------------------------------------
# Interpreter
# ---------------------------------------------------------------------------
def venv_python() -> Path:
    """Path to the project .venv interpreter for this platform (may not exist)."""
    if os.name == "nt":
        return PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    return PROJECT_ROOT / ".venv" / "bin" / "python"


def reexec_under_venv() -> None:
    """Re-run this script under the project .venv interpreter when needed.

    The verifier is the executable backing for the Verification discipline rule,
    so it must not depend on whichever `python` happens to be first on PATH (a
    system interpreter may lack PyYAML and other declared packages). When a
    canonical .venv interpreter exists and differs from the current one, restart
    under it. A one-shot env marker prevents an infinite re-exec loop, and any
    failure falls through to the current interpreter - running is better than
    crashing the gate over interpreter selection.
    """
    if os.environ.get(REEXEC_MARKER) == "1":
        return
    try:
        target = venv_python()
        if target.exists() and target.resolve() != Path(sys.executable).resolve():
            env = dict(os.environ, **{REEXEC_MARKER: "1"})
            completed = subprocess.run(
                [str(target), str(Path(__file__).resolve()), *sys.argv[1:]], env=env
            )
            sys.exit(completed.returncode)
    except SystemExit:
        raise
    except Exception:
        return  # fall through to the current interpreter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def now_ts() -> str:
    """ISO 8601 timestamp with local timezone offset."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def append_log(action: str, note: str) -> None:
    line = f"[{now_ts()}] | Actor: Biblio | Action: {action} | Note: {note}\n"
    with open(LOG_FILE, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line)


def load_module(path: Path, name: str):
    """Import a project script from its file path without running its main()."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Test discovery and scope selection
# ---------------------------------------------------------------------------
def discover_suites():
    """Return [(owner_rel, [test_file, ...]), ...] for every project test suite.

    owner_rel is the project-relative posix path of the workflow/skill directory
    that owns the tests/ folder (e.g. "workflows/audit").
    """
    suites = []
    for base in TEST_ROOTS:
        base_dir = PROJECT_ROOT / base
        if not base_dir.exists():
            continue
        for tests_dir in sorted(base_dir.rglob("tests")):
            if not tests_dir.is_dir():
                continue
            files = sorted(tests_dir.glob("test_*.py"))
            if not files:
                continue
            owner_rel = tests_dir.parent.relative_to(PROJECT_ROOT).as_posix()
            suites.append((owner_rel, files))
    return suites


def changed_paths():
    """Project-relative posix paths changed vs HEAD (staged + unstaged + untracked).

    Returns a set, or None if git is unavailable / errors (caller falls back to all).
    """
    try:
        paths = set()
        diff = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "diff", "--name-only", "HEAD"],
            capture_output=True, encoding="utf-8", timeout=30,
        )
        if diff.returncode != 0:
            return None
        paths.update(l.strip() for l in diff.stdout.splitlines() if l.strip())
        untracked = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "ls-files", "--others", "--exclude-standard"],
            capture_output=True, encoding="utf-8", timeout=30,
        )
        if untracked.returncode == 0:
            paths.update(l.strip() for l in untracked.stdout.splitlines() if l.strip())
        return paths
    except Exception:
        return None


def is_cross_cutting(changed):
    """True if the changed set touches project-wide files that no single suite owns.

    Root-level governance docs (any top-level `.md` such as AGENTS.md, README.md,
    SOUL.md, or a wrapper file) and the shared `templates/` set are consumed
    across the whole project, so a change to them is not covered by any workflow's
    own test suite. When one of these changes, affected scope escalates to all
    suites rather than reporting a pass over 0 test files.
    """
    for c in changed:
        if c.startswith("templates/"):
            return True
        if "/" not in c and c.endswith(".md"):
            return True
    return False


def select_suites(scope):
    """Return (selected_suites, scope_label)."""
    suites = discover_suites()
    if scope == "all":
        return suites, "all"
    if scope and scope != "affected":
        selected = [s for s in suites if s[0].rsplit("/", 1)[-1] == scope]
        return selected, f"name={scope}"
    # affected (default)
    changed = changed_paths()
    if changed is None:
        return suites, "affected (git unavailable, running all)"
    if is_cross_cutting(changed):
        return suites, "affected (cross-cutting change, running all)"
    selected = [
        (owner_rel, files)
        for owner_rel, files in suites
        if any(c == owner_rel or c.startswith(owner_rel + "/") for c in changed)
    ]
    return selected, "affected"


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------
# Labels that are advisory WARN inside the audit (they never change the audit's
# own exit code) but hard-fail close-out - the deterministic "done means done"
# gate (plan R2-3, Option B). Each maps to the noun used in the detail line. Only
# a WARN blocks: a DEGRADED finding for one of these labels means the guard could
# not run, which is non-blocking and surfaces via the DEGRADED path instead. Add
# a third blocking label by adding one entry here - the gate, detail line, and
# report all derive from this map, so there is nothing else to wire.
BLOCKING_LABELS = {"doc-sync": "drift", "skill-hardening": "gap"}


def gate_audit():
    try:
        mod = load_module(AUDIT_RUN, "closeout_audit")
        findings, dir_count = mod.run_audit(with_graph=True)
        fails = sum(1 for f in findings if f[0] == "FAIL")
        # The single in-process audit call is reused: its findings are just
        # inspected for the blocking labels. Each label's blocking messages are
        # its WARNs (counted separately from other WARNs so the detail line does
        # not double-count them).
        blocking = {
            label: [f[2] for f in findings
                    if f[0] == "WARN" and f[1] == label]
            for label in BLOCKING_LABELS
        }
        warns = sum(1 for f in findings
                    if f[0] == "WARN" and f[1] not in BLOCKING_LABELS)
        degraded = [f[2] for f in findings if f[0] == "DEGRADED"]
        detail = f"{dir_count} dirs checked, {fails} FAIL, {warns} WARN"
        for label, noun in BLOCKING_LABELS.items():
            if blocking[label]:
                detail += f", {len(blocking[label])} {label} {noun}"
        if degraded:
            detail += f", {len(degraded)} DEGRADED"
        return {
            "name": "structural audit",
            "passed": fails == 0 and not any(blocking.values()),
            "detail": detail,
            "degraded": degraded,
            "blocking": blocking,
        }
    except Exception as exc:  # a gate that cannot run has not passed
        return {"name": "structural audit", "passed": False,
                "detail": f"could not run audit: {exc}"}


def gate_link():
    try:
        mod = load_module(LINK_RUN, "closeout_link")
        _report, dead_count, _fixed = mod.run_audit_mode(fix=False, dry_run=False)
        return {
            "name": "link audit",
            "passed": dead_count == 0,
            "detail": f"{dead_count} dead link(s)",
        }
    except Exception as exc:
        return {"name": "link audit", "passed": False,
                "detail": f"could not run link audit: {exc}"}


def run_test_file(path: Path):
    try:
        result = subprocess.run(
            [sys.executable, str(path)],
            capture_output=True, encoding="utf-8",
            timeout=TEST_TIMEOUT, cwd=str(PROJECT_ROOT),
        )
        ok = result.returncode == 0
        tail = ""
        if not ok:
            combined = (result.stdout + result.stderr).strip().splitlines()
            tail = "\n        ".join(combined[-4:])
        return ok, tail
    except Exception as exc:
        return False, f"could not run: {exc}"


def gate_tests(selected):
    file_results = []
    passed_all = True
    file_count = 0
    for owner_rel, files in selected:
        for test_file in files:
            file_count += 1
            ok, tail = run_test_file(test_file)
            try:
                rel = test_file.relative_to(PROJECT_ROOT).as_posix()
            except ValueError:
                rel = test_file.as_posix()
            file_results.append({"file": rel, "passed": ok, "tail": tail})
            if not ok:
                passed_all = False
    return {
        "name": "tests",
        "passed": passed_all,
        "detail": f"{file_count} test file(s) across {len(selected)} suite(s)",
        "files": file_results,
    }


# ---------------------------------------------------------------------------
# Repair
# ---------------------------------------------------------------------------
def collect_degraded(gates):
    """Every DEGRADED message across all gates (checks that could not run)."""
    return [d for g in gates for d in g.get("degraded", [])]


def overall_status(overall_pass: bool, degraded: list) -> str:
    """Machine-readable verdict that never lets a degraded run read as a clean pass.

    - ``"fail"``     a gate failed.
    - ``"degraded"`` every gate passed, but at least one check could not run;
      non-blocking (exit 0), yet distinct from a clean pass so automation or a
      tired human reading only the verdict cannot mistake it for one.
    - ``"pass"``     every check ran and passed.
    """
    if not overall_pass:
        return "fail"
    if degraded:
        return "degraded"
    return "pass"


def run_repair():
    """Repair the project runtime by running setup.py. Returns (ok, note).

    A DEGRADED check almost always means the .venv is broken or incomplete, and
    setup.py is the canonical fix. It is only invoked on --repair (opt-in), never
    silently, so there is no surprise network install.
    """
    if not SETUP_PY.exists():
        return False, "repair skipped - setup.py not found"
    try:
        completed = subprocess.run(
            [sys.executable, str(SETUP_PY)], cwd=str(PROJECT_ROOT), timeout=1200,
        )
    except Exception as exc:
        return False, f"repair could not run setup.py ({exc})"
    return completed.returncode == 0, f"repair ran setup.py (exit {completed.returncode})"


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def build_report(scope_label, gates, repair_note=None):
    lines = [
        "Book Dragon - Close-out Verifier",
        f"Scope: {scope_label}",
        "-" * 60,
    ]
    for gate in gates:
        tag = "PASS" if gate["passed"] else "FAIL"
        lines.append(f"[{tag}] {gate['name']} - {gate['detail']}")
        for label, msgs in gate.get("blocking", {}).items():
            for msg in msgs:
                lines.append(f"    [{label}] {msg}")
        for fr in gate.get("files", []):
            if not fr["passed"]:
                lines.append(f"    [FAIL] {fr['file']}")
                if fr["tail"]:
                    lines.append(f"        {fr['tail']}")
    degraded = collect_degraded(gates)
    if degraded:
        lines.append("-" * 60)
        lines.append(f"DEGRADED - {len(degraded)} check(s) did not run:")
        for d in degraded:
            lines.append(f"  - {d}")
    if repair_note:
        lines.append("-" * 60)
        lines.append(repair_note)
    lines.append("-" * 60)
    failed = [g["name"] for g in gates if not g["passed"]]
    if failed:
        lines.append(f"RESULT: FAIL ({len(failed)} gate(s) failed: {', '.join(failed)})")
    elif degraded:
        lines.append(
            f"RESULT: DEGRADED (gates passed, but {len(degraded)} check(s) did not "
            "run); see above. Not a clean pass."
        )
    else:
        lines.append("RESULT: PASS (all gates green)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Close-out verifier: run the mechanical close-out checks as one pass/fail gate.",
    )
    parser.add_argument(
        "--scope", default="affected", metavar="all|NAME",
        help="Test breadth: 'affected' (default, tests for changed workflows), "
             "'all' (every suite, used by a full close-out), or a workflow/skill NAME.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the result as JSON.")
    parser.add_argument(
        "--repair", action="store_true",
        help="If a check DEGRADED (could not run), run setup.py to repair the "
             "project runtime, then re-run the gates once.",
    )
    args = parser.parse_args()

    reexec_under_venv()

    append_log("started", f"Close-out verifier started (scope: {args.scope}).")

    selected, scope_label = select_suites(args.scope)
    gates = [gate_audit(), gate_link(), gate_tests(selected)]
    degraded = collect_degraded(gates)

    # A DEGRADED check did not run. --repair fixes the runtime (setup.py) and
    # re-runs once; without --repair, point at the fix so it can be run by hand.
    repair_note = None
    if degraded and args.repair:
        _ok, note = run_repair()
        gates = [gate_audit(), gate_link(), gate_tests(selected)]
        degraded = collect_degraded(gates)
        if degraded:
            repair_note = (f"--repair: {note}; {len(degraded)} check(s) still "
                           "DEGRADED - fix the runtime manually, then re-run.")
        else:
            repair_note = f"--repair: {note}; all checks now run."
    elif degraded:
        repair_note = ("Some checks DEGRADED (did not run). Re-run with --repair "
                       "to auto-fix the runtime (runs setup.py), or run the fix "
                       "shown above and re-run.")

    overall_pass = all(g["passed"] for g in gates)
    status = overall_status(overall_pass, degraded)

    result = {
        "generated_at": now_ts(),
        "scope": args.scope,
        "scope_label": scope_label,
        "passed": overall_pass,
        # A degraded run keeps passed=true (non-blocking) but is not clean: these
        # two fields let a consumer of the JSON tell a clean pass from a degraded
        # one without parsing the human report.
        "clean": status == "pass",
        "status": status,
        "degraded": degraded,
        "repair_note": repair_note,
        "gates": gates,
    }
    try:
        with RESULT_FILE.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(result, indent=2))
    except Exception:
        pass  # durable result is a convenience, not a gate

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(build_report(scope_label, gates, repair_note))

    verdict = status.upper()
    failed = [g["name"] for g in gates if not g["passed"]]
    degrade_suffix = f" {len(degraded)} check(s) DEGRADED." if degraded else ""
    if not overall_pass:
        body = f"Failed: {', '.join(failed)}."
    elif degraded:
        body = "All gates passed, but some checks did not run."
    else:
        body = "All gates green."
    note = f"Close-out verifier {verdict} (scope: {scope_label}). {body}{degrade_suffix}"
    append_log("completed" if overall_pass else "failed", note)

    sys.exit(0 if overall_pass else 1)


if __name__ == "__main__":
    main()
