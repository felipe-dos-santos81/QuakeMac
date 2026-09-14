"""Contract tests: tools/list surface + quake_status error path."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "src"))

from mcp.shared.memory import create_connected_server_and_client_session

from quakemcp.server import mcp


def _run(coro):
    return asyncio.run(coro)


def _text(result):
    return "".join(getattr(c, "text", "") for c in result.content)


async def _session():
    session_cm = create_connected_server_and_client_session(mcp)
    return session_cm


def test_tools_list_has_exactly_seven():
    async def main():
        async with create_connected_server_and_client_session(mcp) as s:
            await s.initialize()
            tools = await s.list_tools()
            return sorted(t.name for t in tools.tools)
    assert _run(main()) == ["quake_act", "quake_attach", "quake_observe",
                            "quake_start", "quake_state", "quake_status",
                            "quake_stop"]


def test_status_no_instance_is_tool_error():
    async def main():
        async with create_connected_server_and_client_session(mcp) as s:
            await s.initialize()
            return await s.call_tool("quake_status", {})
    result = _run(main())
    assert result.isError, result
    assert "ENGINE_DISCONNECTED" in _text(result)


def test_status_extra_fields_rejected():
    # FastMCP 1.29.0 declares a strict inputSchema (no catch-all), which
    # the MCP client enforces. The SDK itself drops unknown fields at
    # dispatch, so "rejection" is the schema contract, not a runtime raise.
    async def main():
        async with create_connected_server_and_client_session(mcp) as s:
            await s.initialize()
            tools = await s.list_tools()
            status = next(t for t in tools.tools
                          if t.name == "quake_status")
            return status

    tool = _run(main())
    props = tool.inputSchema.get("properties", {})
    assert set(props) == {"instance"}  # no catch-all field in schema
