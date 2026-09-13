"""QuakeMCP stdio server: FastMCP app with lifecycle tools.

Logs to stderr only — stdout carries MCP traffic. Task 4 registers 4
tools (quake_status/start/attach/stop); the other 8 land in Tasks 5-8.
Unregistered tools must NOT appear in tools/list.
"""
import sys
import traceback

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import lifecycle
from .models import EngineDisconnected, QuakeMCPError

mcp = FastMCP("quakemcp")


RO_TRUE = ToolAnnotations(readOnlyHint=True)
RO_FALSE = ToolAnnotations(readOnlyHint=False)
RO_FALSE_STOP = ToolAnnotations(readOnlyHint=False, destructiveHint=True)


@mcp.tool(annotations=RO_TRUE)
def quake_status(instance: str = "") -> dict:
    """Report connection, instance and controller state."""
    if not instance:
        raise ValueError("ENGINE_DISCONNECTED: no instance")
    inst = lifecycle.get(instance)
    if inst is None:
        raise ValueError("ENGINE_DISCONNECTED: unknown instance %r"
                         % (instance,))
    reply = None
    try:
        client = inst.client()
        try:
            reply = client.send("ping")
        finally:
            client.close()
    except EngineDisconnected as e:
        raise ValueError("%s: %s" % (e.code, e.detail))
    assert reply is not None
    ready = reply.get("ok") is True and reply.get("result", {}).get(
        "ready") is True
    return {"instance": instance, "pid": inst.pid, "owned": inst.owned,
            "bridge_ready": ready}


@mcp.tool(annotations=RO_FALSE)
def quake_start(profile: str = "local", port: int = 28900) -> dict:
    """Launch a preconfigured executable; wait for authenticated ready."""
    if not isinstance(profile, str) or profile not in lifecycle.PROFILES:
        raise ValueError("INVALID_CONTEXT: unknown profile %r" % (profile,))
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("INVALID_CONTEXT: bad port %r" % (port,))
    inst = None
    try:
        inst = lifecycle.launch(profile, port=port)
    except EngineDisconnected as e:
        raise ValueError("%s: %s" % (e.code, e.detail))
    except QuakeMCPError as e:
        raise ValueError("%s: %s" % (e.code, e.detail))
    assert inst is not None
    return {"instance": inst.instance_id, "pid": inst.pid,
            "bridge_ready": True}


@mcp.tool(annotations=RO_FALSE)
def quake_attach(instance: str, port: int, token: str) -> dict:
    """Attach to an explicitly authorized instrumented instance."""
    if not instance or not token:
        raise ValueError("INVALID_CONTEXT: instance and token required")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("INVALID_CONTEXT: bad port %r" % (port,))
    inst = None
    try:
        inst = lifecycle.attach(instance, port, token)
    except EngineDisconnected as e:
        raise ValueError("%s: %s" % (e.code, e.detail))
    except QuakeMCPError as e:
        raise ValueError("%s: %s" % (e.code, e.detail))
    assert inst is not None
    return {"instance": inst.instance_id, "bridge_ready": True}


@mcp.tool(annotations=RO_FALSE_STOP)
def quake_stop(instance: str) -> dict:
    """Stop an owned child. Refuses attached user-owned processes."""
    inst = lifecycle.get(instance)
    if inst is None:
        raise ValueError("ENGINE_DISCONNECTED: unknown instance %r"
                         % (instance,))
    result = None
    try:
        result = inst.stop()
    except QuakeMCPError as e:
        raise ValueError("%s: %s" % (e.code, e.detail))
    assert result is not None
    lifecycle.forget(instance)
    return result


def main():
    try:
        mcp.run()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
