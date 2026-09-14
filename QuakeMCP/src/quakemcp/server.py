"""QuakeMCP stdio server: FastMCP app with lifecycle tools.

Logs to stderr only — stdout carries MCP traffic. Task 4 registered the
4 lifecycle tools; Task 5 added quake_act, Task 6 quake_state. The rest
land in Tasks 7-8. Unregistered tools must NOT appear in tools/list.
"""
import base64
import json
import sys
import traceback

from mcp.server.fastmcp import FastMCP
from mcp.types import (CallToolResult, ImageContent, TextContent,
                       ToolAnnotations)

from . import lifecycle, vision
from .models import (ActOut, EngineDisconnected, Observation, ObserveOut,
                     QuakeMCPError, STATE_KEYS, StateOut)

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
def quake_state(instance: str) -> StateOut:
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


def _fetch_observation(inst, instance, after_frame=0, timeout_ms=1000):
    """Bridge round trip for one state+pixels snapshot.

    Returns (raw_rgb, bridge_result). The blob is read only after the
    header line, and its length is checked against the declared source
    dimensions.
    """
    client = inst.client()
    try:
        reply = client.send("observe", after_frame=str(int(after_frame)),
                            timeout_ms=str(int(timeout_ms)))
        if reply.get("ok") is not True:
            raise ValueError("%s: %s" % (
                reply.get("error", "ENGINE_DISCONNECTED"),
                reply.get("detail", "")))
        res = reply.get("result", {})
        blob_bytes = int(res.get("blob_bytes", 0))
        raw = client.read_blob(blob_bytes) if blob_bytes else b""
    except EngineDisconnected as e:
        raise ValueError("%s: %s" % (e.code, e.detail))
    finally:
        client.close()

    src_w = int(res.get("src_w", 0))
    src_h = int(res.get("src_h", 0))
    if len(raw) != src_w * src_h * 3:
        raise ValueError("INVALID_CONTEXT: blob size does not match %dx%d"
                         % (src_w, src_h))
    return raw, res


def _encode_observation(inst, instance, after_frame=0, timeout_ms=1000,
                        longest_edge=1280, image_format="png", crop=None,
                        allow_hud_crop=False):
    """Fetch one observation and encode it. Returns (encoded, report,
    structured) with structured ready for telemetry policy filtering."""
    raw, res = _fetch_observation(inst, instance, after_frame=after_frame,
                                  timeout_ms=timeout_ms)
    src_w = int(res["src_w"])
    src_h = int(res["src_h"])
    encoded, report = vision.encode_frame(
        raw, src_w, src_h, longest_edge=longest_edge, fmt=image_format,
        crop=crop or None, hud_rect=res.get("hud_rect") or None,
        allow_hud_crop=allow_hud_crop)

    structured = {k: res[k] for k in STATE_KEYS if k in res}
    structured["instance"] = instance
    for k in ("src_w", "src_h", "out_w", "out_h", "crop", "scale",
              "encoding", "frame_hash"):
        structured[k] = report[k]
    for k in ("viewport", "hud_rect", "capture_age_ms"):
        if k in res:
            structured[k] = res[k]
    return encoded, report, structured


def _image_content(encoded, encoding):
    mime = "image/png" if encoding == "png" else "image/jpeg"
    return ImageContent(type="image",
                        data=base64.b64encode(encoded).decode(),
                        mimeType=mime)


def _caption(structured):
    return TextContent(type="text",
                       text=json.dumps(structured, sort_keys=True))


@mcp.tool(annotations=RO_TRUE)
def quake_observe(
    instance: str,
    after_frame: int = 0,
    timeout_ms: int = 1000,
    longest_edge: int = 1280,
    image_format: str = "png",
    crop: list[int] = [],
    telemetry: str = "hud",
    allow_hud_crop: bool = False,
) -> ObserveOut:
    """Return the rendered frame as an image plus its state snapshot.

    Pixels and state come from one immutable snapshot: the structured
    result carries the same `frame` as the image bytes. after_frame
    waits for a frame newer than that id, bounded by timeout_ms
    (1..10000). Images are PNG by default (jpeg optional), aspect-
    preserving down to longest_edge with no upscaling; crop [x,y,w,h]
    is in source-image coordinates and may not drop the HUD unless
    allow_hud_crop is true. telemetry hud (default) keeps health/ammo;
    pixels_only removes gameplay telemetry and derived flags.
    """
    if not instance:
        raise ValueError("ENGINE_DISCONNECTED: no instance")
    inst = lifecycle.get(instance)
    if inst is None:
        raise ValueError("ENGINE_DISCONNECTED: unknown instance %r"
                         % (instance,))
    if image_format not in vision.ENCODINGS:
        raise ValueError("INVALID_CONTEXT: image_format must be png|jpeg")
    if telemetry not in ("hud", "pixels_only"):
        raise ValueError("INVALID_CONTEXT: telemetry must be hud|pixels_only")
    if not 1 <= longest_edge <= 4096:
        raise ValueError("INVALID_CONTEXT: longest_edge out of range 1..4096")
    if not 1 <= timeout_ms <= 10000:
        raise ValueError("INVALID_CONTEXT: timeout_ms out of range 1..10000")
    if after_frame < 0:
        raise ValueError("INVALID_CONTEXT: after_frame must be >= 0")

    encoded, report, structured = _encode_observation(
        inst, instance, after_frame=after_frame, timeout_ms=timeout_ms,
        longest_edge=longest_edge, image_format=image_format, crop=crop,
        allow_hud_crop=allow_hud_crop)
    structured = vision.apply_telemetry(structured, telemetry)
    return CallToolResult(
        content=[_image_content(encoded, report["encoding"]),
                 _caption(structured)],
        structuredContent=structured,
        isError=False)


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
    telemetry: str = "hud",
) -> ActOut:
    """Run one bounded gameplay action; returns its completion observation.

    Axes: forward/strafe/vertical in [-1, 1], positive = forward/right/up.
    View: positive yaw_delta_deg turns right, positive pitch_delta_deg looks
    up (applied once at action start, clamped to engine view limits).
    jump: none|tap (one simulation step)|hold. weapon_id maps to the Quake
    weapon impulse 1..8 (0 = no switch). Exactly one of ticks (1..72) or
    duration_ms (1..1000) is required; a hard 5 s wall deadline applies.

    The result carries the post-action rendered frame (image block) and
    its matching state snapshot, so pixels and telemetry share one
    `frame`. telemetry hud (default) keeps health/ammo; pixels_only
    removes gameplay telemetry and derived flags.

    Second layer of the lease protocol: the bridge serializes mutations
    through the controller lease (acquired via quake_control in Task 8, or
    lazily here with action_seq defaulting to 1). world_generation and
    control_revision are accepted for schema stability but enforced in
    Task 9.
    """
    inst = lifecycle.get(instance)
    if inst is None:
        raise ValueError("ENGINE_DISCONNECTED: unknown instance %r"
                         % (instance,))
    if telemetry not in ("hud", "pixels_only"):
        raise ValueError("INVALID_CONTEXT: telemetry must be hud|pixels_only")
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
    completed = {
        "action_id": res.get("action_id", action_id),
        "completed_ticks": res.get("completed_ticks"),
        "elapsed_ms": res.get("elapsed_ms"),
        "interrupted": res.get("interrupted"),
    }
    # the action observation must follow the final completed step and its
    # render; a capture failure is surfaced, never fabricated
    encoded, report, structured = _encode_observation(
        inst, instance, after_frame=0, timeout_ms=2000)
    structured.update(completed)
    structured = vision.apply_telemetry(structured, telemetry)
    return CallToolResult(
        content=[_image_content(encoded, report["encoding"]),
                 _caption(structured)],
        structuredContent=structured,
        isError=False)


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
