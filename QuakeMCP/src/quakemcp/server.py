"""QuakeMCP stdio server: FastMCP app with lifecycle tools.

Logs to stderr only — stdout carries MCP traffic. Task 4 registered the
4 lifecycle tools; Task 5 added quake_act, Task 6 quake_state. The rest
land in Tasks 7-8. Unregistered tools must NOT appear in tools/list.
"""
import sys
import traceback

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import lifecycle
from .models import EngineDisconnected, Observation, QuakeMCPError

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


@mcp.tool(annotations=RO_TRUE)
def quake_state(instance: str) -> dict:
    """Return one read-only engine state snapshot.

    Keys: epoch, world_gen, control_rev, frame, time, map, pos, angles,
    health, ammo, ui, loading, dead, intermission, signon, movemessages.
    A state snapshot and the pixels of the same frame share `frame`.
    """
    if not instance:
        raise ValueError("ENGINE_DISCONNECTED: no instance")
    inst = lifecycle.get(instance)
    if inst is None:
        raise ValueError("ENGINE_DISCONNECTED: unknown instance %r"
                         % (instance,))
    try:
        client = inst.client()
        try:
            reply = client.send("state")
        finally:
            client.close()
    except EngineDisconnected as e:
        raise ValueError("%s: %s" % (e.code, e.detail))
    if reply.get("ok") is not True:
        raise ValueError("%s: %s" % (
            reply.get("error", "ENGINE_DISCONNECTED"),
            reply.get("detail", "")))
    state = dict(reply.get("result", {}))
    state["instance"] = instance
    obs = Observation(
        identity={"instance": instance, "epoch": state.get("epoch"),
                  "frame": state.get("frame")},
        state=state)
    return obs.state


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


@mcp.tool(annotations=RO_FALSE)
def quake_act(
    instance: str,
    forward: float = 0.0,
    strafe: float = 0.0,
    vertical: float = 0.0,
    run: bool = False,
    yaw_delta_deg: float = 0.0,
    pitch_delta_deg: float = 0.0,
    attack: bool = False,
    jump: str = "none",
    weapon_id: int = 0,
    ticks: int = 0,
    duration_ms: int = 0,
    lease: str = "",
    action_id: str = "",
    action_seq: int = 0,
    epoch: int = 0,
    world_generation: int = 0,
    control_revision: int = 0,
) -> dict:
    """Run one bounded gameplay action; returns its completion observation.

    Axes: forward/strafe/vertical in [-1, 1], positive = forward/right/up.
    View: positive yaw_delta_deg turns right, positive pitch_delta_deg looks
    up (applied once at action start, clamped to engine view limits).
    jump: none|tap (one simulation step)|hold. weapon_id maps to the Quake
    weapon impulse 1..8 (0 = no switch). Exactly one of ticks (1..72) or
    duration_ms (1..1000) is required; a hard 5 s wall deadline applies.

    Second layer of the lease protocol: the bridge serializes mutations
    through the controller lease (acquired via quake_control in Task 8, or
    lazily here with action_seq defaulting to 1). world_generation and
    control_revision are accepted for schema stability but enforced in
    Task 9. Image block lands in Task 7.
    """
    inst = lifecycle.get(instance)
    if inst is None:
        raise ValueError("ENGINE_DISCONNECTED: unknown instance %r"
                         % (instance,))
    if forward < -1.0 or forward > 1.0 or strafe < -1.0 or strafe > 1.0 \
            or vertical < -1.0 or vertical > 1.0:
        raise ValueError("INVALID_CONTEXT: axes must be within [-1, 1]")
    if jump not in ("none", "tap", "hold"):
        raise ValueError("INVALID_CONTEXT: jump must be none|tap|hold")
    if not 0 <= weapon_id <= 8:
        raise ValueError("INVALID_CONTEXT: weapon_id out of range 0..8")
    if (ticks > 0) == (duration_ms > 0):
        raise ValueError(
            "INVALID_CONTEXT: exactly one of ticks / duration_ms required")
    if ticks and not 1 <= ticks <= 72:
        raise ValueError("INVALID_CONTEXT: ticks out of range 1..72")
    if duration_ms and not 1 <= duration_ms <= 1000:
        raise ValueError("INVALID_CONTEXT: duration_ms out of range 1..1000")

    client = inst.client()
    try:
        # lazy acquire keeps the vertical slice self-contained until
        # quake_control lands in Task 8
        if not lease or not epoch:
            reply = client.send("control", sub="acquire")
            if reply.get("ok") is not True:
                raise ValueError("%s: %s" % (
                    reply.get("error", "ENGINE_DISCONNECTED"),
                    reply.get("detail", "")))
            res = reply.get("result", {})
            lease = res.get("lease", "")
            epoch = res.get("epoch", 0)
            if action_seq <= 0:
                action_seq = 1  # first action on the fresh lease
        kw = {
            "lease": lease,
            "epoch": str(epoch),
            "seq": str(action_seq),
            "action_id": action_id,
            "forward": str(forward),
            "strafe": str(strafe),
            "vertical": str(vertical),
            "run": "1" if run else "0",
            "yaw": str(yaw_delta_deg),
            "pitch": str(pitch_delta_deg),
            "attack": "1" if attack else "0",
            "jump": jump,
            "impulse": str(weapon_id),
        }
        if ticks:
            kw["ticks"] = str(ticks)
        else:
            kw["duration_ms"] = str(duration_ms)
        reply = client.send("act", **kw)
    except EngineDisconnected as e:
        raise ValueError("%s: %s" % (e.code, e.detail))
    finally:
        client.close()

    if reply.get("ok") is not True:
        raise ValueError("%s: %s" % (
            reply.get("error", "ENGINE_DISCONNECTED"),
            reply.get("detail", "")))
    res = reply.get("result", {})
    return {
        "action_id": res.get("action_id", action_id),
        "completed_ticks": res.get("completed_ticks"),
        "elapsed_ms": res.get("elapsed_ms"),
        "interrupted": res.get("interrupted"),
        "instance": instance,
        "lease": lease,
    }


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
