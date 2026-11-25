#!/usr/bin/env python3
"""Check process-lifecycle safeguards for Book Dragon helper processes."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from runtime import LAUNCHER_PID_ENV, process_is_alive, venv_python

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent


def _run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )


def check_mcp_smoke() -> tuple[bool, str]:
    try:
        python = str(venv_python())
    except RuntimeError:
        return False, "Project .venv Python not found."
    result = _run([
        python,
        "workflows/biblio-tools/scripts/mcp_smoke.py",
        "--command",
        "python",
        "--arg",
        "workflows/biblio-tools/scripts/launch.py",
        "--arg",
        "workflows/biblio-tools/scripts/server.py",
        "--cwd",
        str(PROJECT_ROOT),
    ])
    if result.returncode != 0:
        return False, result.stderr.strip() or result.stdout.strip()
    return True, "MCP handshake succeeded through launch.py."


def check_parent_death_cleanup() -> tuple[bool, str]:
    code = r"""
import subprocess
import sys

child = subprocess.Popen(
    [
        sys.executable,
        "workflows/biblio-tools/scripts/launch.py",
        "workflows/biblio-tools/scripts/server.py",
    ],
    stdin=subprocess.PIPE,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
print(child.pid, flush=True)
"""
    parent = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    stdout, stderr = parent.communicate(timeout=10)
    if parent.returncode != 0:
        return False, stderr.strip() or "Parent process failed."

    pid = int(stdout.strip().splitlines()[-1])
    time.sleep(1)
    if not process_is_alive(pid):
        return False, "Launcher exited before the watchdog window."
    # Poll with a timeout rather than a fixed sleep — the watchdog polls
    # every 10s, so 30s gives it at least two cycles even on a loaded machine.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if not process_is_alive(pid):
            return True, "Launcher cleaned itself up after parent death."
        time.sleep(2)
    return False, f"Launcher process {pid} survived parent death after 30s."


def check_launcher_death_cleanup() -> tuple[bool, str]:
    """server.py exits when the launcher that named itself to it dies, with its
    stdin still open: the case where launch.py is killed outright and nothing
    closes the pipe."""
    try:
        python = str(venv_python())
    except RuntimeError:
        return False, "Project .venv Python not found."
    stand_in = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    server = subprocess.Popen(
        [python, "workflows/biblio-tools/scripts/server.py"],
        cwd=PROJECT_ROOT,
        env={**os.environ, LAUNCHER_PID_ENV: str(stand_in.pid)},
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(3)
        if server.poll() is not None:
            return False, f"Server exited ({server.returncode}) before the launcher died."
        stand_in.kill()
        # Not waited for until the end: on POSIX the killed stand-in stays a zombie
        # until reaped, as a force-killed launch.py may while its own parent lives,
        # and the server must still see it as dead (cross-review R1-1).
        # The watchdog polls every 10s; 30s gives it at least two cycles.
        try:
            server.wait(timeout=30)
        except subprocess.TimeoutExpired:
            return False, "Server survived its launcher's death after 30s."
        return True, "Server exited after its launcher died."
    finally:
        for proc in (stand_in, server):
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        if server.stdin:
            server.stdin.close()


CODEX_PLUGIN = PROJECT_ROOT / ".codex" / "plugins" / "plugins" / "biblio-tools"


# The environment variables launch_biblio.py reads as a project-root hint. Codex's
# registration sets none of them, so the check removes them all (cross-review R3-1).
CODEX_ROOT_HINTS = ("BIBLIO_PROJECT_ROOT", "CODEX_PROJECT_ROOT", "CODEX_WORKSPACE",
                    "CODEX_WORKSPACE_ROOT", "WORKSPACE_FOLDER", "PWD")


def check_codex_plugin_launcher_death() -> tuple[bool, str]:
    """The Codex plugin's real route, as Codex runs it: a copy of the plugin outside
    the project (Codex runs one from its own cache), the registered command (`python
    ./mcp/launch_biblio.py` from the plugin folder) and no project-root hint, with
    stdin held open. Complete an MCP initialize, kill the process Codex would have
    started (not reaped until the end), and expect the server to exit. The server
    holds the launcher's stdout pipe, so its end-of-file is the sign the server has
    gone; no process listing is needed (cross-review R2-1).

    The copy finds the project only as the cached plugin does, so a checkout outside
    the launcher's home-folder guesses fails here, as it would in Codex (R3-1)."""
    source = CODEX_PLUGIN / "mcp" / "launch_biblio.py"
    if not source.is_file():
        return True, "No Codex plugin launcher in this project; nothing to check."
    # A server that survived still holds its stderr file on Windows, so a failed
    # check must not turn into a clean-up error that hides its own message.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        plugin = Path(folder) / "biblio-tools"
        (plugin / "mcp").mkdir(parents=True)
        shutil.copy2(source, plugin / "mcp" / "launch_biblio.py")
        env = {k: v for k, v in os.environ.items() if k not in CODEX_ROOT_HINTS}
        python = shutil.which("python", path=env.get("PATH")) or sys.executable
        with open(Path(folder) / "stderr.txt", "w+", encoding="utf-8", newline="\n") as err:
            return _plugin_launcher_death(
                [python, "./mcp/launch_biblio.py"], plugin, env, err)


# Bounds for the plugin check, named so a test can shorten them (cross-review R4-1).
PLUGIN_ANSWER_WAIT = 90   # for the server's answer to an MCP initialize
PLUGIN_EXIT_WAIT = 40     # after the kill: the watchdog polls every 10s, so 3+ cycles
PLUGIN_STDIN_WAIT = 15    # after closing the server's stdin, which ends an mcp 2 server


def _plugin_launcher_death(command, cwd, env, err) -> tuple[bool, str]:
    """Run the plugin launcher check on `command`. Every wait is bounded, so the
    check reports a surviving server rather than hanging on it (cross-review R4-1):
    a reader thread still blocked on stdout means a process still holds the pipe,
    and closing a buffered stream another thread is reading blocks until that read
    ends. So stdin is closed first, which ends an `mcp` 2 server, and stdout is closed
    only once no reader is left on it."""
    proc = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=err,
    )
    readers: list[threading.Thread] = []

    def read_in_background(target) -> threading.Thread:
        thread = threading.Thread(target=target, daemon=True)
        readers.append(thread)
        thread.start()
        return thread

    try:
        request = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                              "clientInfo": {"name": "lifecycle-check", "version": "0"}}}
        proc.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
        proc.stdin.flush()
        first: list[bytes] = []
        read_in_background(lambda: first.append(proc.stdout.readline())).join(PLUGIN_ANSWER_WAIT)
        if not first or b'"result"' not in first[0]:
            proc.kill()
            proc.wait()
            err.seek(0)
            said = err.read().strip().splitlines()
            return False, ("The plugin's server did not answer an MCP initialize"
                           + (f": {said[-1]}" if said else "."))
        time.sleep(1)
        if proc.poll() is not None:
            return False, ("The plugin launcher exited once the server started, so "
                           "nothing is left for the server to watch.")
        proc.kill()
        drained = read_in_background(proc.stdout.read)
        drained.join(PLUGIN_EXIT_WAIT)
        if drained.is_alive():
            return False, (f"The server survived the plugin launcher's death after "
                           f"{PLUGIN_EXIT_WAIT}s.")
        return True, "Server exited after the Codex plugin launcher died."
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        try:
            proc.stdin.close()
        except OSError:
            pass
        stuck = [thread for thread in readers if thread.is_alive()]
        for thread in stuck:
            thread.join(PLUGIN_STDIN_WAIT)
        if any(thread.is_alive() for thread in readers):
            # Something still holds the pipe even with its stdin closed. Leave the
            # stream to the daemon reader rather than block on it, and say so.
            print("  [WARN] A process still holds the plugin server's output pipe; "
                  "a Biblio Tools server may be left running.", file=sys.stderr)
        else:
            try:
                proc.stdout.close()
            except OSError:
                pass


def check_reader_stale_pid_safety() -> tuple[bool, str]:
    pid_file = PROJECT_ROOT / "workflows/reddit-collector/.reader_server.pid"
    original = pid_file.read_text(encoding="utf-8") if pid_file.exists() else None
    payload = {
        "pid": os.getpid(),
        "port": 65534,
        "subreddit": "hfy",
        "started_at": "2000-01-01T00:00:00+00:00",
        "token": "not-a-real-reader",
    }
    try:
        with pid_file.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(payload))
        result = _run([
            sys.executable,
            "workflows/reddit-collector/scripts/build_reader.py",
            "--stop",
        ])
        if result.returncode != 0:
            return False, result.stderr.strip() or result.stdout.strip()
        if pid_file.exists():
            return False, "Stale reader PID metadata was not removed."
        return True, "Reader stale-PID safety check passed."
    finally:
        if original is not None:
            with pid_file.open("w", encoding="utf-8", newline="\n") as fh:
                fh.write(original)
        elif pid_file.exists():
            pid_file.unlink()



def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    checks = [
        ("MCP smoke", check_mcp_smoke),
        ("Parent-death cleanup", check_parent_death_cleanup),
        ("Launcher-death cleanup", check_launcher_death_cleanup),
        ("Codex plugin launcher-death cleanup", check_codex_plugin_launcher_death),
        ("Reader stale-PID safety", check_reader_stale_pid_safety),
    ]
    failures = 0
    for label, fn in checks:
        ok, detail = fn()
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {label} - {detail}")
        if not ok:
            failures += 1
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
