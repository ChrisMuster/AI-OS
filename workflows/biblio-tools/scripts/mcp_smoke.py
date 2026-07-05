#!/usr/bin/env python3
"""Run a protocol-level smoke test against a configured stdio MCP server."""

import argparse
import json
from pathlib import Path

# runtime-guard: launched via verify.py/lifecycle_check.py under the project .venv
import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


EXPECTED_TOOLS = {
    "append_log",
    "build_knowledge_graph",
    "get_timestamp",
    "query_knowledge_graph",
    "run_audit",
    "run_link_check",
    "run_new_month",
    "run_session_search_index",
    "run_settings_check",
    "verify_setup",
}


def _has_saved_knowledge_graph_index(cwd: Path) -> bool:
    """True when the saved graph index exists and can be used for a fast smoke."""
    index_dir = cwd / "workflows" / "knowledge-graph" / "index"
    return (index_dir / "nodes.json").is_file() and (index_dir / "edges.json").is_file()


async def run_smoke(command: str, args: list[str], cwd: Path) -> dict:
    """Connect, initialise, list tools, and call a read-only utility tool."""
    parameters = StdioServerParameters(command=command, args=args, cwd=cwd)
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialised = await session.initialize()
            listed = await session.list_tools()
            tool_names = {tool.name for tool in listed.tools}
            missing = sorted(EXPECTED_TOOLS - tool_names)
            unexpected = sorted(tool_names - EXPECTED_TOOLS)

            timestamp_result = await session.call_tool("get_timestamp", {})
            if timestamp_result.isError:
                raise RuntimeError("get_timestamp returned an MCP tool error")

            invalid_month = await session.call_tool(
                "run_new_month", {"month": "invalid"}
            )
            invalid_month_payload = json.loads(invalid_month.content[0].text)
            if invalid_month_payload.get("success") is not False:
                raise RuntimeError("run_new_month accepted an invalid month")

            traversal = await session.call_tool(
                "append_log",
                {
                    "directory": "../outside-project",
                    "actor": "Biblio",
                    "action": "completed",
                    "note": "MCP smoke test",
                },
            )
            traversal_payload = json.loads(traversal.content[0].text)
            if "Path traversal detected" not in traversal_payload.get("error", ""):
                raise RuntimeError("append_log did not reject path traversal")

            if _has_saved_knowledge_graph_index(cwd):
                kg_stats = await session.call_tool(
                    "query_knowledge_graph",
                    {"command": "stats", "from_index": True},
                )
                kg_stats_payload = json.loads(kg_stats.content[0].text)
                if not kg_stats_payload.get("success"):
                    raise RuntimeError(
                        f"query_knowledge_graph stats failed: {kg_stats_payload}"
                    )
                if "node_count" not in kg_stats_payload.get("result", {}):
                    raise RuntimeError(
                        "query_knowledge_graph stats result missing node_count"
                    )
                kg_status = "PASS"
            else:
                kg_arg_check = await session.call_tool(
                    "query_knowledge_graph", {"command": "node"}
                )
                kg_arg_payload = json.loads(kg_arg_check.content[0].text)
                if kg_arg_payload.get("success") is not False:
                    raise RuntimeError(
                        "query_knowledge_graph accepted a node query without an id"
                    )
                kg_status = "PASS_NO_INDEX"

            return {
                "success": not missing,
                "protocol_version": initialised.protocolVersion,
                "tools": sorted(tool_names),
                "missing_tools": missing,
                "unexpected_tools": unexpected,
                "get_timestamp": "PASS",
                "invalid_month_rejected": "PASS",
                "path_traversal_rejected": "PASS",
                "knowledge_graph_stats": kg_status,
            }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", required=True)
    parser.add_argument("--arg", action="append", default=[])
    parser.add_argument("--cwd", type=Path, required=True)
    args = parser.parse_args()

    try:
        result = anyio.run(run_smoke, args.command, args.arg, args.cwd)
    except BaseException as exc:
        result = {"success": False, "error": str(exc)}

    print(json.dumps(result))
    raise SystemExit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
