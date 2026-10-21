#!/usr/bin/env python3
"""
fixture_git.py - the one way the review-orchestration suites run git to build a
test's fixture. Not a test file: each suite loads it by path and wraps ``run_git`` in
its own ``git`` helper.

A failure carries git's own error text (code reviews R18-1 and R21-1). One failure is
retried: git refused permission to write one of its own object files in a new
repository, which on this machine is another program (an antivirus scan, most likely)
holding a file git has just written. Seen with that text while building R22-2's test:
"unable to write file .git/objects/...: Permission denied". It is tried up to
``ATTEMPTS`` times with a short pause; every other failure fails at once.

Loading it also switches off git's automatic maintenance for every git the test
process starts (``quiet_maintenance``): after a commit, git can start a detached
background maintenance process, and in the check container, whose first process does
not reap orphans, each one is left as a zombie that counts against the container's
process limit. A whole run of the suites there left over 500, and the next suite could
start neither a thread nor a git (A3 chunk 2).
"""

import os
import subprocess
import time

ATTEMPTS = 3
PAUSE_SECONDS = 0.5
# git configuration given to every git the test process starts, through git's own
# GIT_CONFIG_COUNT variables: no automatic maintenance or garbage collection, and
# neither detached.
QUIET_MAINTENANCE = (("maintenance.auto", "false"), ("maintenance.autoDetach", "false"),
                     ("gc.auto", "0"), ("gc.autoDetach", "false"))


def quiet_maintenance(environ=None):
    """Add ``QUIET_MAINTENANCE`` to ``environ`` (the process's own by default) after
    any git configuration it already gives, once."""
    environ = os.environ if environ is None else environ
    if environ.get("BOOK_DRAGON_FIXTURE_GIT_QUIET") == "1":
        return environ
    try:
        count = int(environ.get("GIT_CONFIG_COUNT") or 0)
    except ValueError:
        count = 0
    for offset, (key, value) in enumerate(QUIET_MAINTENANCE):
        environ[f"GIT_CONFIG_KEY_{count + offset}"] = key
        environ[f"GIT_CONFIG_VALUE_{count + offset}"] = value
    environ["GIT_CONFIG_COUNT"] = str(count + len(QUIET_MAINTENANCE))
    environ["BOOK_DRAGON_FIXTURE_GIT_QUIET"] = "1"
    return environ


quiet_maintenance()


class FixtureGitError(Exception):
    """A git command setting up a test's fixture failed."""


def object_write_refused(stderr):
    """True for git's refusal to write one of its own object files, the one failure
    retried."""
    text = (stderr or "").lower()
    return "permission denied" in text and ("unable to write file" in text
                                            or "failed to insert into database" in text)


def run_git(command, cwd, *, shown=None, runner=subprocess.run, sleep=time.sleep):
    """Run ``command`` (a list starting with ``git``) in ``cwd`` and return the
    finished process; raise FixtureGitError naming the command (as ``shown``, the
    wrapper's short form, when given), exit code, folder and git's own text if it
    fails."""
    for attempt in range(1, ATTEMPTS + 1):
        done = runner(command, cwd=str(cwd), capture_output=True, encoding="utf-8",
                      errors="replace")
        if done.returncode == 0:
            return done
        if attempt < ATTEMPTS and object_write_refused(done.stderr):
            sleep(PAUSE_SECONDS)
            continue
        break
    tries = f" after {attempt} attempts" if attempt > 1 else ""
    raise FixtureGitError(f"`{shown or ' '.join(command)}` exited {done.returncode}{tries} "
                          f"in {cwd}: "
                          f"{(done.stderr or done.stdout).strip() or '(no output)'}")
