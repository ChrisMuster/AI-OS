#!/usr/bin/env python3
"""One isolated Python probe for a cross-review session (plan 10.12, step 9)."""

import json
import sys
import time
from pathlib import Path

import container

MAX_CODE = 20_000
TIMEOUT = 120
TAIL = 6_000


def load_settings(path):
    """Read and validate the server's immutable launch settings once."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or any(
            not isinstance(data.get(key), str) or not data[key]
            for key in ("copy", "image", "record")):
        raise ValueError("probe settings need copy, image and record paths")
    copy, record = Path(data["copy"]), Path(data["record"])
    if not copy.is_dir() or not record.is_file():
        raise ValueError("the probe copy or call record is missing")
    return data


def run_probe(code, settings, *, clock=time.monotonic, run=container.run_in_container):
    """Run one call in the offline copy and record its input without its output."""
    started = clock()
    exit_code = None
    if not isinstance(code, str) or len(code) > MAX_CODE:
        answer = f"error: code must be a string of at most {MAX_CODE} characters"
    else:
        try:
            exit_code, stdout, stderr = run(
                settings["copy"], ["python", "-B", "-c", code], settings["image"],
                timeout=TIMEOUT, merged_stream=True)
            if container.docker_failed(exit_code, stdout):
                answer = "error: " + stdout[-TAIL:]
            else:
                answer = f"exit {exit_code}\n{stdout[-TAIL:]}"
        except (container.ContainerError, OSError) as exc:
            answer = f"error: {exc}"
    duration = round(clock() - started, 3)
    try:
        with Path(settings["record"]).open("a", encoding="utf-8", newline="\n") as log:
            log.write(json.dumps({"code": code, "exit_code": exit_code,
                                  "seconds": duration}, ensure_ascii=False) + "\n")
    except OSError as exc:
        return f"error: the probe call could not be recorded ({exc})"
    return answer


def build_server(settings):
    from mcp.server.mcpserver import MCPServer  # runtime-guard: launched via the cross-review round's reviewer session config, under the round's .venv interpreter
    server = MCPServer("probes")

    @server.tool(name="run_probe")
    def run_probe_tool(code: str) -> str:
        """Run up to 20,000 characters of Python in the offline review copy."""
        return run_probe(code, settings)

    return server


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("probe_server.py needs one settings file", file=sys.stderr)
        return 1
    try:
        settings = load_settings(args[0])
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"probe settings could not be read: {exc}", file=sys.stderr)
        return 1
    build_server(settings).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
