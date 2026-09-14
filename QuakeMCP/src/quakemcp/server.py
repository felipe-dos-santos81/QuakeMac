"""QuakeMCP stdio server: FastMCP app with lifecycle tools.

Logs to stderr only — stdout carries MCP traffic. Task 4 registered the
4 lifecycle tools; Task 5 added quake_act, Task 6 quake_state. The rest
land in Tasks 7-8. Unregistered tools must NOT appear in tools/list.
"""
import anyio
import base64
import contextlib
import functools
import json
import os
import re
import struct
import sys
import time
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


# ---- guarded console / config policy (Task 8) -------------------------------
#
# Raw console scripting stays disabled: quake_console takes a registered
# command plus validated argument tokens, never a free-form string. The
# allowlist mirrors what the design's console tool may run; every other
# command is POLICY_DENIED before it reaches the engine.

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_MAP_RE = re.compile(r"^[A-Za-z0-9_*-]{1,64}$")
_SLOT_RE = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
_GIVE_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_CONNECT_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")

# command -> (arg count, validator name or None)
CONSOLE_COMMANDS = {
    "status": (0, None),
    "version": (0, None),
    "skill": (1, "skill"),
    "god": (0, None),
    "noclip": (0, None),
    "give": (1, "give"),
    "impulse": (1, "impulse"),
    "kill": (0, None),
    "pause": (1, "pause"),
    "save": (1, "slot"),
    "load": (1, "slot"),
    "map": (1, "map"),
    "restart": (0, None),
    "changelevel": (1, "map"),
    "connect": (1, "connect"),
    "disconnect": (0, None),
    "quit": (0, None),
    "screenshot": (0, None),
    "toggleconsole": (0, None),
}

# Curated settings from the engine's real cvar set: input, view, audio,
# and the accessibility knobs. `access_*` names are readable but only the
# allowlisted ones writable (several hold command strings).
CONFIG_CVARS = frozenset({
    "sensitivity", "m_pitch", "m_yaw", "m_forward", "m_side", "m_filter",
    "lookspring", "lookstrafe", "in_mouse", "in_dgamouse",
    "cl_forwardspeed", "cl_backspeed", "cl_sidespeed", "cl_upspeed",
    "cl_movespeedkey", "cl_yawspeed", "cl_pitchspeed", "cl_anglespeedkey",
    "cl_autofire", "cl_bob", "cl_bobcycle", "cl_bobup", "cl_rollangle",
    "cl_rollspeed", "cl_pitchdriftspeed",
    "scr_viewsize", "scr_fov", "scr_conspeed", "scr_centertime",
    "scr_showpause", "scr_showram", "scr_showturtle", "crosshair",
    "gl_triplebuffer", "gl_picmip", "gl_ztrick", "gl_finish", "gl_clear",
    "gl_subdivide_size", "gl_max_size", "gl_affinemodels", "gl_smoothmodels",
    "gl_polyblend", "gl_flashblend",
    "host_maxfps", "host_timescale", "v_gamma", "vid_mode",
    "volume", "bgmvolume", "_snd_mixahead", "bgmbuffer", "loadas8bit",
    "ambient_level", "ambient_fade", "snd_noextraupdate",
})
CONFIG_ACCESS_PREFIX = "access_"
CONFIG_ACCESS_WRITE = frozenset({"access_mouseonly"})

KEY_CODES = {
    "escape": 27, "enter": 13, "space": 32, "tab": 9, "backspace": 127,
    "up": 128, "down": 129, "left": 130, "right": 131,
    "ins": 147, "del": 148, "pgdn": 149, "pgup": 150, "home": 151,
    "end": 152, "pause": 255,
}
KEY_CODES.update({"f%d" % n: 134 + n for n in range(1, 13)})

UI_CONTEXTS = {"game": 0, "console": 1, "message": 2, "menu": 3}

SAVE_WAIT_SECS = 10.0
WORLD_WAIT_SECS = 30.0


def _instance(instance):
    if not instance:
        raise ValueError("ENGINE_DISCONNECTED: no instance")
    inst = lifecycle.get(instance)
    if inst is None:
        raise ValueError("ENGINE_DISCONNECTED: unknown instance %r"
                         % (instance,))
    return inst


def _bridge(inst, op, **kw):
    """One authenticated bridge round trip. Raises on transport failure."""
    try:
        client = inst.client()
        try:
            return client.send(op, **kw)
        finally:
            client.close()
    except EngineDisconnected as e:
        raise ValueError("%s: %s" % (e.code, e.detail))


def _bridge_ok(inst, op, **kw):
    reply = _bridge(inst, op, **kw)
    if reply.get("ok") is not True:
        raise ValueError("%s: %s" % (
            reply.get("error", "ENGINE_DISCONNECTED"),
            reply.get("detail", "")))
    return reply.get("result", {})


async def _offload(fn, *args, **kwargs):
    """Run one blocking call on a worker thread.

    abandon_on_cancel stays off: a cancelled wait lets the in-flight
    bridge round trip finish before the cancellation is delivered, so a
    deferred act/observe reply is never abandoned half-read. The
    explicit checkpoint delivers that deferred cancellation now — without
    it a cancelled call whose worker finished would return normally and
    the emergency release would never fire.
    """
    out = await anyio.to_thread.run_sync(functools.partial(fn, *args,
                                                           **kwargs))
    await anyio.lowlevel.checkpoint()
    return out


def _emergency_release(inst):
    """Best-effort priority release; never raises."""
    try:
        _bridge(inst, "release")
    except Exception:
        pass


@contextlib.asynccontextmanager
async def _cancel_releases(inst):
    """Emergency-release the lease when the wrapped body is cancelled."""
    try:
        yield
    except anyio.get_cancelled_exc_class():
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(_emergency_release, inst)
            # the release revoked the engine-side lease: drop the local
            # copy and the beat, exactly like quake_release does, so the
            # next mutation lazily acquires instead of trusting a dead id
            inst.stop_keepalive()
            inst.lease = ""
            inst.epoch = 0
            inst.next_seq = 0
        raise


def _mutate(inst, op, action_id="", lease="", action_seq=0, epoch=0,
            world_generation=0, control_revision=0, **args):
    """Send one mutation under the controller lease envelope.

    Uses the caller's lease/sequence when given, the instance's when the
    caller gave none, and lazily acquires a lease when there is none.
    Retries only with an action_id: the engine's receipt ring is then the
    dedup authority.
    """
    lease = lease or inst.lease
    epoch = epoch or inst.epoch
    if not lease or not epoch:
        res = _bridge_ok(inst, "control", sub="acquire")
        lease = res.get("lease", "")
        epoch = res.get("epoch", 0)
        inst.next_seq = 0
    inst.lease, inst.epoch = lease, epoch
    if not getattr(inst, "_hb_thread", None):
        inst.keepalive(lease, epoch)
    if action_seq <= 0:
        inst.next_seq += 1
        action_seq = inst.next_seq
    else:
        inst.next_seq = max(inst.next_seq, action_seq)
    kw = dict(args, lease=lease, epoch=str(epoch), seq=str(action_seq),
              action_id=action_id)
    if world_generation > 0:
        kw["world_generation"] = str(world_generation)
    if control_revision > 0:
        kw["control_revision"] = str(control_revision)
    try:
        client = inst.client()
        try:
            if action_id:
                reply = client.send_retrying(op, **kw)
            else:
                reply = client.send(op, **kw)
        finally:
            client.close()
    except EngineDisconnected as e:
        raise ValueError("%s: %s" % (e.code, e.detail))
    if reply.get("ok") is not True:
        code = reply.get("error", "ENGINE_DISCONNECTED")
        if code == "STALE_STATE":
            inst.stop_keepalive()
            inst.lease = ""
        raise ValueError("%s: %s" % (code, reply.get("detail", "")))
    return reply.get("result", {})


def _tail(inst):
    """Current console tail (read-only bridge op)."""
    return _bridge_ok(inst, "tail").get("output", "")


def _state(inst):
    return _bridge_ok(inst, "state")


def _gamedir(inst):
    basedir = inst.basedir()
    if basedir is None:
        raise ValueError("UNSUPPORTED_CAPABILITY: gamedir unknown for "
                         "attached instances")
    return os.path.join(basedir, "id1")


def _pak_names(gamedir):
    """Entry names from pak0..pakN directories (no payload reads).

    Quake pak: 12-byte header ('PACK', little-endian dirofs/dirlen) then
    64-byte entries (56-byte name, offset, length). Loose files are not
    included here.
    """
    names = []
    index = 0
    while True:
        path = os.path.join(gamedir, "pak%d.pak" % index)
        if not os.path.exists(path):
            break
        with open(path, "rb") as fh:
            header = fh.read(12)
            if len(header) != 12 or header[:4] != b"PACK":
                raise ValueError("INVALID_CONTEXT: %s is not a pak file"
                                 % os.path.basename(path))
            dirofs, dirlen = struct.unpack("<ii", header[4:12])
            if dirlen < 0 or dirlen % 64 or dirlen > 64 * 1024 * 1024:
                raise ValueError("INVALID_CONTEXT: bad pak directory in %s"
                                 % os.path.basename(path))
            fh.seek(dirofs)
            directory = fh.read(dirlen)
        if len(directory) != dirlen:
            raise ValueError("INVALID_CONTEXT: truncated pak directory in %s"
                             % os.path.basename(path))
        for i in range(0, dirlen, 64):
            raw = directory[i:i + 56].split(b"\x00", 1)[0]
            names.append(raw.decode("ascii", "replace"))
        index += 1
    return names


def _list_maps(inst):
    gamedir = _gamedir(inst)
    names = set()
    for entry in _pak_names(gamedir):
        if entry.lower().startswith("maps/") \
                and entry.lower().endswith(".bsp"):
            names.add(os.path.basename(entry)[:-4])
    for name in os.listdir(gamedir):
        if name.lower().endswith(".bsp"):
            names.add(name[:-4])
    return sorted(names)


def _list_saves(inst):
    gamedir = _gamedir(inst)
    names = []
    for name in sorted(os.listdir(gamedir)):
        if name.lower().endswith(".sav"):
            names.append(name[:-4])
    return names


def _save_path(inst, slot):
    return os.path.join(_gamedir(inst), slot + ".sav")


def _map_id(value):
    if not _MAP_RE.match(value) or ".." in value:
        raise ValueError("POLICY_DENIED: bad map id %r" % (value,))
    return value


def _save_slot(value):
    if not _SLOT_RE.match(value):
        raise ValueError("POLICY_DENIED: bad save slot %r" % (value,))
    return value


def _console_arg(validator, value):
    if validator == "skill":
        if value not in ("0", "1", "2", "3"):
            raise ValueError("INVALID_CONTEXT: skill must be 0..3")
    elif validator == "impulse":
        if not value.isdigit() or not 0 <= int(value) <= 255:
            raise ValueError("INVALID_CONTEXT: impulse must be 0..255")
    elif validator == "pause":
        if value not in ("0", "1"):
            raise ValueError("INVALID_CONTEXT: pause must be 0 or 1")
    elif validator == "give":
        if not _GIVE_RE.match(value):
            raise ValueError("POLICY_DENIED: bad give item %r" % (value,))
    elif validator == "slot":
        _save_slot(value)
    elif validator == "map":
        _map_id(value)
    elif validator == "connect":
        if not _CONNECT_RE.match(value) or ".." in value:
            raise ValueError("POLICY_DENIED: bad connect target %r" % (value,))
    return value


def _console_line(command, args):
    spec = CONSOLE_COMMANDS.get(command)
    if spec is None:
        raise ValueError("POLICY_DENIED: command %r not allowed" % (command,))
    count, validator = spec
    args = list(args)
    if len(args) != count:
        raise ValueError("INVALID_CONTEXT: %s takes %d argument(s)"
                         % (command, count))
    checked = [_console_arg(validator, str(a)) for a in args] if count else []
    return " ".join([command] + checked)


def _config_known(name):
    return name in CONFIG_CVARS or name.startswith(CONFIG_ACCESS_PREFIX)


async def _wait_world(inst, before, timeout=WORLD_WAIT_SECS):
    """Wait for a loaded world that is actually accepting input."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = await _offload(_state, inst)
        if (last["world_gen"] > before and last["signon"] == 4
                and last["movemessages"] > 2):
            return last
        await anyio.sleep(0.25)
    raise ValueError("ACTION_TIMEOUT: world did not become ready: %r" % (last,))


@mcp.tool(annotations=RO_TRUE)
async def quake_status(instance: str = "") -> dict:
    """Report connection, instance and controller state."""
    inst = _instance(instance)
    reply = await _offload(_bridge, inst, "ping")
    ready = reply.get("ok") is True and reply.get("result", {}).get(
        "ready") is True
    return {"instance": instance, "pid": inst.pid, "owned": inst.owned,
            "bridge_ready": ready}


@mcp.tool(annotations=RO_TRUE)
async def quake_state(instance: str) -> StateOut:
    """Return one read-only engine state snapshot.

    Keys: epoch, world_gen, control_rev, frame, time, map, pos, angles,
    health, ammo, ui, loading, dead, intermission, signon, movemessages.
    A state snapshot and the pixels of the same frame share `frame`.
    """
    inst = _instance(instance)
    state = dict(await _offload(_bridge_ok, inst, "state"))
    state["instance"] = instance
    obs = Observation(
        identity={"instance": instance, "epoch": state.get("epoch"),
                  "frame": state.get("frame")},
        state=state)
    return obs.state


@mcp.tool(annotations=RO_FALSE)
async def quake_start(profile: str = "local", port: int = 28900) -> dict:
    """Launch a preconfigured executable; wait for authenticated ready."""
    if not isinstance(profile, str) or profile not in lifecycle.PROFILES:
        raise ValueError("INVALID_CONTEXT: unknown profile %r" % (profile,))
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("INVALID_CONTEXT: bad port %r" % (port,))
    inst = None
    try:
        inst = await _offload(lifecycle.launch, profile, port=port)
    except EngineDisconnected as e:
        raise ValueError("%s: %s" % (e.code, e.detail))
    except QuakeMCPError as e:
        raise ValueError("%s: %s" % (e.code, e.detail))
    assert inst is not None
    return {"instance": inst.instance_id, "pid": inst.pid,
            "bridge_ready": True}


@mcp.tool(annotations=RO_FALSE)
async def quake_attach(instance: str, port: int, token: str) -> dict:
    """Attach to an explicitly authorized instrumented instance."""
    if not instance or not token:
        raise ValueError("INVALID_CONTEXT: instance and token required")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("INVALID_CONTEXT: bad port %r" % (port,))
    inst = None
    try:
        inst = await _offload(lifecycle.attach, instance, port, token)
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
async def quake_observe(
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
    inst = _instance(instance)
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

    encoded, report, structured = await _offload(
        _encode_observation, inst, instance, after_frame=after_frame,
        timeout_ms=timeout_ms, longest_edge=longest_edge,
        image_format=image_format, crop=crop,
        allow_hud_crop=allow_hud_crop)
    structured = vision.apply_telemetry(structured, telemetry)
    return CallToolResult(
        content=[_image_content(encoded, report["encoding"]),
                 _caption(structured)],
        structuredContent=structured,
        isError=False)


@mcp.tool(annotations=RO_FALSE)
async def quake_act(
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
    through the controller lease (acquired via quake_control, or lazily
    here with action_seq defaulting to 1). world_generation and
    control_revision, when non-zero, are checked against the engine and
    a mismatch is STALE_STATE. With an action_id the engine records a
    receipt, so a dropped connection is retried once and either returns
    that receipt or runs the action exactly once; reusing an action_id
    with different arguments is POLICY_DENIED.
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

    async with _cancel_releases(inst):
        # arguments must reach the bridge as strings, like the envelope
        args = {
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
            args["ticks"] = str(ticks)
        else:
            args["duration_ms"] = str(duration_ms)
        res = await _offload(_mutate, inst, "act", action_id=action_id,
                             lease=lease, action_seq=action_seq,
                             epoch=epoch,
                             world_generation=world_generation,
                             control_revision=control_revision, **args)
        completed = {
            "action_id": res.get("action_id", action_id),
            "completed_ticks": res.get("completed_ticks"),
            "elapsed_ms": res.get("elapsed_ms"),
            "interrupted": res.get("interrupted"),
        }
        # the action observation must follow the final completed step and
        # its render; a capture failure is surfaced, never fabricated
        encoded, report, structured = await _offload(
            _encode_observation, inst, instance, 0, 2000)
    structured.update(completed)
    structured = vision.apply_telemetry(structured, telemetry)
    return CallToolResult(
        content=[_image_content(encoded, report["encoding"]),
                 _caption(structured)],
        structuredContent=structured,
        isError=False)


@mcp.tool(annotations=RO_FALSE)
async def quake_game(instance: str, operation: str, map_id: str = "",
                     slot: str = "", overwrite: bool = False) -> dict:
    """Run a typed game-lifecycle operation.

    new_game -> map start, restart -> restart, load_map {map_id},
    save {slot} (refuses to replace without overwrite=true), load {slot},
    list_maps / list_saves (gamedir inventory). Completion means the world
    generation advanced and the client is accepting input (new_game,
    restart, load_map, load) or the save file was written. `respawn` is
    unsupported until its death-flow semantics are verified.
    """
    inst = _instance(instance)
    if operation == "list_maps":
        return {"operation": operation,
                "maps": await _offload(_list_maps, inst)}
    if operation == "list_saves":
        return {"operation": operation,
                "saves": await _offload(_list_saves, inst)}
    if operation == "respawn":
        raise ValueError("UNSUPPORTED_CAPABILITY: respawn semantics "
                         "unverified")
    if operation == "new_game":
        return await _world_op(inst, operation, "map start")
    if operation == "restart":
        return await _world_op(inst, operation, "restart")
    if operation == "load_map":
        mid = _map_id(map_id)
        if mid not in await _offload(_list_maps, inst):
            raise ValueError("INVALID_CONTEXT: unknown map %r" % (mid,))
        return await _world_op(inst, operation, "map %s" % mid)
    if operation == "save":
        name = _save_slot(slot)
        path = _save_path(inst, name)
        if os.path.exists(path) and not overwrite:
            raise ValueError("INVALID_CONTEXT: %s exists; pass overwrite=true"
                             % name)
        return await _save_op(inst, name, path)
    if operation == "load":
        name = _save_slot(slot)
        if not os.path.exists(_save_path(inst, name)):
            raise ValueError("INVALID_CONTEXT: no save named %r" % (name,))
        return await _world_op(inst, operation, "load %s" % name)
    raise ValueError("UNSUPPORTED_CAPABILITY: operation %r" % (operation,))


async def _world_op(inst, operation, command):
    """Run a world-mutating op with the simulation clock running.

    A stepped session holds the clock while idle, so a map/restart/load
    would never finish sign-on and the op would time out. The session's
    mode is restored afterwards when it was stepped before.
    """
    async with _cancel_releases(inst):
        stepped = (await _offload(_state, inst)).get("mode") == "stepped"
        if stepped:
            await _offload(_bridge_ok, inst, "control", sub="mode",
                           mode="realtime")
        try:
            before = (await _offload(_state, inst))["world_gen"]
            await _offload(_bridge_ok, inst, "exec", text=command)
            st = await _wait_world(inst, before)
        finally:
            if stepped:
                await _offload(_bridge_ok, inst, "control", sub="mode",
                               mode="stepped")
    return {"operation": operation, "world_gen": st["world_gen"],
            "frame": st["frame"], "map": st["map"], "gameplay_ready": True}


SAVE_FAILURES = (
    "Not playing a local game.",
    "Can't save in intermission.",
    "Can't save multiplayer games.",
    "Can't savegame with a dead player",
    "Relative pathnames are not allowed.",
    "ERROR: couldn't open.",
)


async def _save_op(inst, slot, path):
    async with _cancel_releases(inst):
        await _offload(_bridge_ok, inst, "exec", text="save %s" % slot)
        deadline = time.time() + SAVE_WAIT_SECS
        tail = ""
        while time.time() < deadline:
            tail = await _offload(_tail, inst)
            if "done." in tail:
                return {"operation": "save", "slot": slot, "saved": True,
                        "file": path}
            for bad in SAVE_FAILURES:
                if bad in tail:
                    raise ValueError("NOT_READY: %s" % (bad,))
            await anyio.sleep(0.25)
        raise ValueError("ACTION_TIMEOUT: save %s did not complete: %r"
                         % (slot, tail[-200:]))


@mcp.tool(annotations=RO_FALSE)
async def quake_config(instance: str, operation: str, name: str,
                       value: str = "") -> dict:
    """Read or write one allowlisted engine setting (effective value).

    Only declared input/view/audio/access settings are reachable; unknown
    names are INVALID_CONTEXT and known-but-read-only ones POLICY_DENIED.
    """
    inst = _instance(instance)
    if operation not in ("get", "set"):
        raise ValueError("INVALID_CONTEXT: operation must be get|set")
    if not name or not _TOKEN_RE.match(name):
        raise ValueError("INVALID_CONTEXT: bad setting name %r" % (name,))
    if operation == "get":
        if not _config_known(name):
            raise ValueError("INVALID_CONTEXT: unknown setting %r" % (name,))
        res = await _offload(_bridge_ok, inst, "cvar", name=name)
        return {"operation": operation, "name": name,
                "value": res.get("value")}
    writable = (name in CONFIG_CVARS
                or name in CONFIG_ACCESS_WRITE)
    if not writable:
        if _config_known(name):
            raise ValueError("POLICY_DENIED: %s is read-only" % (name,))
        raise ValueError("INVALID_CONTEXT: unknown setting %r" % (name,))
    if len(value) > 128 or any(ord(ch) < 32 for ch in value):
        raise ValueError("INVALID_CONTEXT: bad setting value")
    async with _cancel_releases(inst):
        res = await _offload(_bridge_ok, inst, "cvar", name=name, value=value)
    return {"operation": operation, "name": name, "value": res.get("value")}


@mcp.tool(annotations=RO_FALSE)
async def quake_console(instance: str, command: str,
                        args: list[str] = []) -> dict:
    """Run one allowlisted console command with validated arguments.

    Never accepts a raw command line; anything outside the allowlist is
    POLICY_DENIED. The returned tail reflects the console as of the
    reply and can predate the command's own output; poll again to see it.
    """
    inst = _instance(instance)
    line = _console_line(command, args)
    async with _cancel_releases(inst):
        res = await _offload(_bridge_ok, inst, "exec", text=line)
    return {"command": command, "args": list(args),
            "output": res.get("output", "")}


@mcp.tool(annotations=RO_FALSE)
async def quake_ui(instance: str, key: str = "", text: str = "",
                   context: str = "") -> dict:
    """Deliver UI keys or type into the active text field.

    key is a name (escape/enter/up/down/...) or a single character; the
    engine receives a key-down/key-up pair. text types only into a live
    console/chat field and never submits it. context (game|console|
    message|menu), when given, must match the engine's current UI state.
    """
    inst = _instance(instance)
    if context:
        want = UI_CONTEXTS.get(context)
        if want is None:
            raise ValueError("INVALID_CONTEXT: context must be game|console|"
                             "message|menu")
        if (await _offload(_state, inst))["ui"] != want:
            raise ValueError("NOT_READY: expected %s UI context" % context)
    if text:
        if (await _offload(_state, inst))["ui"] not in (1, 2):
            raise ValueError("INVALID_CONTEXT: no active text field")
        sent = []
        async with _cancel_releases(inst):
            for ch in text:
                if not 32 <= ord(ch) <= 126:
                    raise ValueError("INVALID_CONTEXT: text must be printable "
                                     "ASCII")
                await _offload(_bridge_ok, inst, "key", key=str(ord(ch)),
                               down="1")
                await _offload(_bridge_ok, inst, "key", key=str(ord(ch)),
                               down="0")
                sent.append(ch)
        return {"typed": len(sent)}
    if not key:
        raise ValueError("INVALID_CONTEXT: key or text required")
    code = KEY_CODES.get(key.lower())
    if code is None:
        if len(key) == 1 and 32 <= ord(key) <= 126:
            code = ord(key)
        else:
            raise ValueError("INVALID_CONTEXT: unknown key %r" % (key,))
    async with _cancel_releases(inst):
        await _offload(_bridge_ok, inst, "key", key=str(code), down="1")
        await _offload(_bridge_ok, inst, "key", key=str(code), down="0")
    return {"key": key, "code": code}


@mcp.tool(annotations=RO_FALSE)
async def quake_control(instance: str, operation: str = "acquire",
                        mode: str = "") -> dict:
    """Acquire/release/detach the controller lease or set the execution mode.

    acquire refuses while physical attack/jump buttons are held
    (CONTROL_BUSY). release and detach are idempotent. mode selects
    stepped (freeze the simulation between actions, advance only by
    ticks) or realtime; stepped sessions need ticks, not duration_ms.
    """
    inst = _instance(instance)
    if operation in ("acquire", "release", "detach"):
        res = await _offload(_bridge_ok, inst, "control", sub=operation)
        if operation == "acquire":
            # the server owns the lease while the caller holds it: beat
            # every 500 ms so tool calls longer than the 2 s expiry
            # (image encoding, world loads) do not lose control
            inst.lease = res.get("lease", "")
            inst.epoch = res.get("epoch", 0)
            inst.next_seq = 0
            inst.keepalive(inst.lease, inst.epoch)
        else:
            inst.stop_keepalive()
            inst.lease = ""
            inst.epoch = 0
            inst.next_seq = 0
        out = {"operation": operation}
        for k in ("lease", "epoch", "control_rev", "released"):
            if k in res:
                out[k] = res[k]
        return out
    if operation == "mode":
        if mode not in ("stepped", "realtime"):
            raise ValueError("INVALID_CONTEXT: mode must be stepped|realtime")
        async with _cancel_releases(inst):
            res = await _offload(_bridge_ok, inst, "control", sub="mode",
                                 mode=mode)
        return {"operation": "mode", "mode": res.get("mode", mode)}
    raise ValueError("INVALID_CONTEXT: operation must be acquire|release|"
                     "detach|mode")


@mcp.tool(annotations=RO_FALSE)
async def quake_release(instance: str, reason: str = "") -> dict:
    """Priority emergency stop: cancel actions, revoke control, neutralize
    MCP input. Idempotent and safe with no lease held."""
    inst = _instance(instance)
    res = await _offload(_bridge_ok, inst, "release")
    inst.stop_keepalive()
    inst.lease = ""
    inst.epoch = 0
    inst.next_seq = 0
    return {"released": res.get("released", True), "reason": reason}


@mcp.tool(annotations=RO_FALSE_STOP)
async def quake_stop(instance: str) -> dict:
    """Stop an owned child. Refuses attached user-owned processes."""
    inst = lifecycle.get(instance)
    if inst is None:
        raise ValueError("ENGINE_DISCONNECTED: unknown instance %r"
                         % (instance,))
    result = None
    try:
        result = await _offload(inst.stop)
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
