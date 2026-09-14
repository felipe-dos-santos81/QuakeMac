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
from .models import (ActOut, CAPABILITIES, CONFIG_ACCESS_PREFIX,
                     CONFIG_ACCESS_WRITE, CONFIG_CVARS, CONSOLE_COMMANDS,
                     EngineDisconnected, KEY_CODES, Observation, ObserveOut,
                     QuakeMCPError, STATE_KEYS, StateOut, UI_CONTEXTS,
                     gameplay_ready, validate_map_id, validate_save_slot)

mcp = FastMCP("quakemcp")


RO_TRUE = ToolAnnotations(readOnlyHint=True)
RO_FALSE = ToolAnnotations(readOnlyHint=False)
RO_FALSE_STOP = ToolAnnotations(readOnlyHint=False, destructiveHint=True)


# The console/config/UI allowlists live in models.py: one policy
# definition, shared with the capability manifest. quake_console takes a
# registered command plus validated argument tokens, never a free-form
# string; every other command is POLICY_DENIED before it reaches the
# engine.

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

SAVE_WAIT_SECS = 10.0
WORLD_WAIT_SECS = 30.0

# Forwarded console commands need a running clock (Task 7); 250 ms was
# measured to carry a kill through the client message and the single-
# player level restart it triggers. The respawn tap holds attack across a
# death think (its cadence is 0.1 s) and then waits, bounded.
CONSOLE_FLUSH_SECS = 0.25
RESPAWN_WAIT_SECS = 10.0
RESPAWN_SETTLE_SECS = 0.25
RESPAWN_TAP_SECS = 1.0
RESPAWN_TAP_TICKS = 8

# Bounds of the per-instance act receipt cache (Task 5): evict-oldest by
# entry count and by evictable bytes.
RECEIPT_LIMIT = 16
RECEIPT_BYTES = 8 * 1024 * 1024


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
            inst.clear_lease()
        raise


# The two pinned bridge STALE_STATE details that do not revoke the lease:
# a world/control generation mismatch refuses the mutation, but the
# controller keeps its lease and the engine keeps the lease's sequence
# high-water. Every other STALE_STATE (`no lease`, `lease mismatch`,
# `epoch mismatch`) means the lease itself is gone.
PRECONDITION_MISMATCHES = ("world generation mismatch",
                           "control revision mismatch")


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
        inst.adopt_lease(res.get("lease", ""), res.get("epoch", 0))
        lease, epoch = inst.lease, inst.epoch
    else:
        inst.lease, inst.epoch = lease, epoch
        if not inst._hb_thread:
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
        detail = reply.get("detail", "")
        if code == "STALE_STATE" and detail not in PRECONDITION_MISMATCHES:
            inst.drop_lease_for(lease, epoch)
        raise ValueError("%s: %s" % (code, detail))
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


def _console_line(command, args):
    """Validate one allowlisted command line; returns (line, class)."""
    spec = CONSOLE_COMMANDS.get(command)
    if spec is None:
        raise ValueError("POLICY_DENIED: command %r not allowed" % (command,))
    count, validator, cls = spec
    args = list(args)
    if len(args) != count:
        raise ValueError("INVALID_CONTEXT: %s takes %d argument(s)"
                         % (command, count))
    checked = [validator(str(a)) for a in args] if count else []
    return " ".join([command] + checked), cls


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
async def quake_status(instance: str = "", action_id: str = "") -> dict:
    """Report connection, instance, controller state and capabilities.

    Merges the bridge ping and state snapshot with the static capability
    manifest. `action` is the bridge receipt ledger's answer: the named
    action's latest receipt, or the newest receipt when action_id is
    omitted; a fresh instance with no receipts reports action None (an
    explicit action_id that matches nothing is an error).
    """
    inst = _instance(instance)
    ping = await _offload(_bridge_ok, inst, "ping")
    state = await _offload(_bridge_ok, inst, "state")
    if action_id:
        action = await _offload(_bridge_ok, inst, "status",
                                action_id=action_id)
    else:
        try:
            action = await _offload(_bridge_ok, inst, "status")
        except ValueError as e:
            if not str(e).startswith("INVALID_CONTEXT"):
                raise
            action = None
    out = {"instance": instance, "pid": inst.pid, "owned": inst.owned,
           "bridge_ready": ping.get("ready") is True,
           "gameplay_ready": gameplay_ready(state),
           "lease_held": bool(inst.lease),
           "capabilities": CAPABILITIES, "action": action}
    out.update(state)
    return out


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
    """Launch a preconfigured executable; wait for authenticated ready.

    An owned session starts in stepped mode, so the simulation is frozen
    between actions; the result reports `mode`. If physical attack/jump
    buttons are held at that instant the acquire is refused
    (CONTROL_BUSY) and the session stays realtime with a `reason`.
    """
    if not isinstance(profile, str) or profile not in lifecycle.PROFILES:
        raise ValueError("INVALID_CONTEXT: unknown profile %r" % (profile,))
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("INVALID_CONTEXT: bad port %r" % (port,))
    inst = None
    try:
        inst = await _offload(lifecycle.launch, profile, port=port)
    except QuakeMCPError as e:
        raise ValueError("%s: %s" % (e.code, e.detail))
    assert inst is not None
    out = {"instance": inst.instance_id, "pid": inst.pid,
           "bridge_ready": True}
    async with _cancel_releases(inst):
        try:
            await _offload(_mutate, inst, "control", sub="mode",
                           mode="stepped")
        except ValueError as e:
            code = str(e).split(":", 1)[0]
            if code not in ("CONTROL_BUSY", "STALE_STATE"):
                raise
            out["mode"] = "realtime"
            out["reason"] = str(e)
            return out
    out["mode"] = "stepped"
    return out


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


def _receipt_bytes(entry):
    """Evictable size of one cached observation: the encoded image bytes
    plus the serialized structured payload (the two retained result
    blobs). The report dict and key are bookkeeping."""
    return len(entry["encoded"]) + len(json.dumps(entry["structured"]))


def _receipt_store(inst, action_id, encoded, report, structured):
    """Remember one delivered act observation under action_id.

    LRU bounded by RECEIPT_LIMIT entries and RECEIPT_BYTES evictable
    bytes (oldest first), so a retry replays its recorded frame until the
    entry is gone — then FRAME_EXPIRED, never a re-shoot.
    """
    if not action_id:
        return
    inst.receipts[action_id] = {"encoded": encoded, "report": report,
                                "structured": structured}
    inst.receipts.move_to_end(action_id)
    while inst.receipts:
        total = sum(_receipt_bytes(e) for e in inst.receipts.values())
        if len(inst.receipts) <= RECEIPT_LIMIT and total <= RECEIPT_BYTES:
            break
        inst.receipts.popitem(last=False)


def _receipt_get(inst, action_id):
    """The cached observation for action_id, if retained (touches LRU)."""
    entry = inst.receipts.get(action_id)
    if entry is not None:
        inst.receipts.move_to_end(action_id)
    return entry


def _observation_result(encoded, report, structured):
    """The one CallToolResult shape every observation delivers.

    The image block carries the recorded bytes; structured content stays
    the caller's dictionary (already telemetry-filtered where policy
    applies).
    """
    return CallToolResult(
        content=[_image_content(encoded, report["encoding"]),
                 _caption(structured)],
        structuredContent=structured,
        isError=False)


def _replay(cached):
    """Rebuild the exact CallToolResult the live path delivered.

    The cached structured content was already telemetry-filtered, so a
    replay applies no second policy pass; the image block is re-encoded
    from the recorded bytes, never re-captured.
    """
    structured = dict(cached["structured"])
    return _observation_result(cached["encoded"], cached["report"],
                               structured)


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
    vision.validate_telemetry(telemetry)
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
    return _observation_result(encoded, report, structured)


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
    with different arguments is POLICY_DENIED. The result reports the
    effective post-clamp view deltas and the requested vs active weapon.
    A retried action_id replays the exact recorded frame; once that frame
    is evicted the retry is FRAME_EXPIRED, never a re-shoot.
    """
    inst = _instance(instance)
    vision.validate_telemetry(telemetry)
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
        # a bridge duplicate means this action already ran and delivered
        # a frame: replay the recorded observation, never re-shoot
        if res.get("duplicate") is True and action_id:
            cached = _receipt_get(inst, action_id)
            if cached is None:
                raise ValueError("FRAME_EXPIRED: %s result frame evicted"
                                 % action_id)
            return _replay(cached)
        completed = {
            "action_id": res.get("action_id", action_id),
            "completed_ticks": res.get("completed_ticks"),
            "elapsed_ms": res.get("elapsed_ms"),
            "interrupted": res.get("interrupted"),
            "yaw_applied_deg": res.get("yaw_applied_deg"),
            "pitch_applied_deg": res.get("pitch_applied_deg"),
            "weapon_requested": res.get("weapon_requested"),
            "weapon_active": res.get("weapon_active"),
        }
        # the action observation must follow the final completed step and
        # its render; a capture failure is surfaced, never fabricated
        encoded, report, structured = await _offload(
            _encode_observation, inst, instance, 0, 2000)
    structured.update(completed)
    structured = vision.apply_telemetry(structured, telemetry)
    if action_id:
        _receipt_store(inst, action_id, encoded, report, structured)
    return _observation_result(encoded, report, structured)


@mcp.tool(annotations=RO_FALSE)
async def quake_game(instance: str, operation: str, map_id: str = "",
                     slot: str = "", overwrite: bool = False,
                     action_id: str = "", lease: str = "",
                     action_seq: int = 0, epoch: int = 0,
                     world_generation: int = 0,
                     control_revision: int = 0) -> dict:
    """Run a typed game-lifecycle operation.

    new_game -> map start, restart -> restart, load_map {map_id},
    save {slot} (refuses to replace without overwrite=true), load {slot},
    list_maps / list_saves (gamedir inventory). Completion means the world
    generation advanced and the client is accepting input (new_game,
    restart, load_map, load) or the save file was written. `respawn`
    requires a dead player (NOT_READY otherwise) and returns
    {respawned, waited_ms}: it taps attack, bounded, until the player is
    alive again; in single player respawn restarts the level.

    The mutating operations carry the controller-lease envelope (see
    quake_act); list_maps and list_saves stay reads.
    """
    inst = _instance(instance)
    if operation == "list_maps":
        return {"operation": operation,
                "maps": await _offload(_list_maps, inst)}
    if operation == "list_saves":
        return {"operation": operation,
                "saves": await _offload(_list_saves, inst)}
    env = {"action_id": action_id, "lease": lease,
           "action_seq": action_seq, "epoch": epoch,
           "world_generation": world_generation,
           "control_revision": control_revision}
    if operation == "respawn":
        return await _respawn_op(inst, env)
    if operation == "new_game":
        return await _world_op(inst, operation, "map start", env)
    if operation == "restart":
        return await _world_op(inst, operation, "restart", env)
    if operation == "load_map":
        mid = validate_map_id(map_id)
        if mid not in await _offload(_list_maps, inst):
            raise ValueError("INVALID_CONTEXT: unknown map %r" % (mid,))
        return await _world_op(inst, operation, "map %s" % mid, env)
    if operation == "save":
        name = validate_save_slot(slot)
        path = _save_path(inst, name)
        if os.path.exists(path) and not overwrite:
            raise ValueError("INVALID_CONTEXT: %s exists; pass overwrite=true"
                             % name)
        return await _save_op(inst, name, path, env)
    if operation == "load":
        name = validate_save_slot(slot)
        if not os.path.exists(_save_path(inst, name)):
            raise ValueError("INVALID_CONTEXT: no save named %r" % (name,))
        return await _world_op(inst, operation, "load %s" % name, env)
    raise ValueError("UNSUPPORTED_CAPABILITY: operation %r" % (operation,))


async def _world_op(inst, operation, command, env):
    """Run a world-mutating op with the simulation clock running.

    A stepped session holds the clock while idle, so a map/restart/load
    would never finish sign-on and the op would time out. The session's
    mode is restored afterwards when it was stepped before. The caller's
    envelope rides the primary mutation; the internal mode flips use the
    instance's live lease.
    """
    async with _cancel_releases(inst):
        stepped = (await _offload(_state, inst)).get("mode") == "stepped"
        if stepped:
            await _offload(_mutate, inst, "control", sub="mode",
                           mode="realtime")
        try:
            before = (await _offload(_state, inst))["world_gen"]
            await _offload(_mutate, inst, "exec", text=command, **env)
            st = await _wait_world(inst, before)
        finally:
            if stepped:
                await _offload(_mutate, inst, "control", sub="mode",
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


async def _save_op(inst, slot, path, env):
    async with _cancel_releases(inst):
        await _offload(_mutate, inst, "exec", text="save %s" % slot, **env)
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


async def _console_flush(inst):
    """Run the clock briefly so a forwarded console command can execute.

    Gameplay commands (`kill`, `pause`, `give`) travel through the client
    message, which a stepped session never sends; the bounded realtime
    window delivers them and restores stepped afterwards. A realtime
    session is untouched, and the engine's own mode stays the authority.
    """
    if (await _offload(_state, inst)).get("mode") != "stepped":
        return
    await _offload(_mutate, inst, "control", sub="mode", mode="realtime")
    try:
        await anyio.sleep(CONSOLE_FLUSH_SECS)
    finally:
        await _offload(_mutate, inst, "control", sub="mode", mode="stepped")


async def _respawn_op(inst, env):
    """Attack-tap respawn from a dead player, bounded.

    The progs' death think only accepts a tap after a tick with the
    buttons released (a held button keeps deadflag at DEAD_DEAD), and
    respawn() in single player restarts the level. So the loop resumes
    the clock for a stepped session, gives one buttons-released settle
    window, taps attack across a death think, and repeats until the
    player is alive or the deadline passes.
    """
    st = await _offload(_state, inst)
    if not st.get("dead"):
        raise ValueError("NOT_READY: player is not dead")
    stepped = st.get("mode") == "stepped"
    started = time.time()
    deadline = started + RESPAWN_WAIT_SECS
    respawned = False
    async with _cancel_releases(inst):
        try:
            if stepped:
                await _offload(_mutate, inst, "control", sub="mode",
                               mode="realtime")
            while time.time() < deadline:
                await anyio.sleep(RESPAWN_SETTLE_SECS)
                args = {"forward": "0", "strafe": "0", "vertical": "0",
                        "run": "0", "yaw": "0", "pitch": "0", "attack": "1",
                        "jump": "none", "impulse": "0",
                        "ticks": str(RESPAWN_TAP_TICKS), "respawn": "1"}
                await _offload(_mutate, inst, "act", **dict(args, **env))
                env = {}
                tap_deadline = min(deadline, time.time() + RESPAWN_TAP_SECS)
                while time.time() < tap_deadline:
                    if not (await _offload(_state, inst)).get("dead"):
                        respawned = True
                        break
                    await anyio.sleep(0.1)
                if respawned:
                    break
        finally:
            if stepped:
                await _offload(_mutate, inst, "control", sub="mode",
                               mode="stepped")
    return {"respawned": respawned,
            "waited_ms": int((time.time() - started) * 1000)}


@mcp.tool(annotations=RO_FALSE)
async def quake_config(instance: str, operation: str, name: str,
                       value: str = "", action_id: str = "", lease: str = "",
                       action_seq: int = 0, epoch: int = 0,
                       world_generation: int = 0,
                       control_revision: int = 0) -> dict:
    """Read or write one allowlisted engine setting (effective value).

    Only declared input/view/audio/access settings are reachable; unknown
    names are INVALID_CONTEXT and known-but-read-only ones POLICY_DENIED.
    A set carries the controller-lease envelope (see quake_act); get is a
    read.
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
        res = await _offload(_mutate, inst, "cvar", name=name, value=value,
                             action_id=action_id, lease=lease,
                             action_seq=action_seq, epoch=epoch,
                             world_generation=world_generation,
                             control_revision=control_revision)
    return {"operation": operation, "name": name, "value": res.get("value")}


@mcp.tool(annotations=RO_FALSE)
async def quake_console(instance: str, command: str,
                        args: list[str] = [], action_id: str = "",
                        lease: str = "", action_seq: int = 0, epoch: int = 0,
                        world_generation: int = 0,
                        control_revision: int = 0) -> dict:
    """Run one allowlisted console command with validated arguments.

    Never accepts a raw command line; anything outside the allowlist is
    POLICY_DENIED. Gameplay commands require a ready world (NOT_READY
    otherwise); pause 0 resumes and is exempt. Gameplay commands forward
    through the client message, so a stepped session runs a bounded
    realtime window before restoring the mode. The returned tail reflects
    the console as of the reply and can predate the command's own output;
    poll again to see it.
    """
    inst = _instance(instance)
    line, cls = _console_line(command, args)
    if cls == "gameplay" and not (command == "pause" and list(args) == ["0"]):
        if not gameplay_ready(await _offload(_state, inst)):
            raise ValueError("NOT_READY: gameplay command needs a loaded "
                             "game")
    async with _cancel_releases(inst):
        res = await _offload(_mutate, inst, "exec", text=line,
                             action_id=action_id, lease=lease,
                             action_seq=action_seq, epoch=epoch,
                             world_generation=world_generation,
                             control_revision=control_revision)
        if cls == "gameplay":
            await _console_flush(inst)
    return {"command": command, "args": list(args),
            "output": res.get("output", "")}


@mcp.tool(annotations=RO_FALSE)
async def quake_ui(instance: str, key: str = "", text: str = "",
                   context: str = "", action_id: str = "", lease: str = "",
                   action_seq: int = 0, epoch: int = 0,
                   world_generation: int = 0,
                   control_revision: int = 0) -> dict:
    """Deliver UI keys or type into the active text field.

    key is a name (escape/enter/up/down/...) or a single character; the
    engine receives a key-down/key-up pair. text types only into a live
    console/chat field and never submits it. context (game|console|
    message|menu), when given, must match the engine's current UI state.
    The envelope rides the press (the first key event); the matching
    release is part of the same delivery, not a separate action.

    A key that raises a modal dialog returns `needs_input` with the
    dialog frame and text instead of blocking until it is answered; the
    answer is an ordinary quake_ui call (y, n, or escape), and
    quake_status(action_id) then reports the original action done or
    denied.
    """
    inst = _instance(instance)
    if context:
        want = UI_CONTEXTS.get(context)
        if want is None:
            raise ValueError("INVALID_CONTEXT: context must be game|console|"
                             "message|menu")
        if (await _offload(_state, inst))["ui"] != want:
            raise ValueError("NOT_READY: expected %s UI context" % context)
    env = {"action_id": action_id, "lease": lease,
           "action_seq": action_seq, "epoch": epoch,
           "world_generation": world_generation,
           "control_revision": control_revision}
    if text:
        if (await _offload(_state, inst))["ui"] not in (1, 2):
            raise ValueError("INVALID_CONTEXT: no active text field")
        sent = []
        async with _cancel_releases(inst):
            for ch in text:
                if not 32 <= ord(ch) <= 126:
                    raise ValueError("INVALID_CONTEXT: text must be printable "
                                     "ASCII")
                await _offload(_mutate, inst, "key", key=str(ord(ch)),
                               down="1", **env)
                env = {}
                await _offload(_mutate, inst, "key", key=str(ord(ch)),
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
        res = await _offload(_mutate, inst, "key", key=str(code), down="1",
                             **env)
        # the release rides the same delivery; the dialog wait loop pumps
        # the bridge, so it is processed while the dialog is still up
        await _offload(_mutate, inst, "key", key=str(code), down="0")
        if res.get("needs_input"):
            # a modal the key raised: hand back its live frame and text
            # instead of keeping the call blocked until someone answers
            encoded, report, structured = await _offload(
                _encode_observation, inst, instance, 0, 2000)
            structured["needs_input"] = True
            structured["modal_text"] = res.get("modal_text", "")
            structured["action_id"] = res.get("action_id") or action_id
            return _observation_result(encoded, report, structured)
    return {"key": key, "code": code}


@mcp.tool(annotations=RO_FALSE)
async def quake_control(instance: str, operation: str = "acquire",
                        mode: str = "", action_id: str = "", lease: str = "",
                        action_seq: int = 0, epoch: int = 0,
                        world_generation: int = 0,
                        control_revision: int = 0) -> dict:
    """Acquire/release/detach the controller lease or set the execution mode.

    acquire refuses while physical attack/jump buttons are held
    (CONTROL_BUSY). release and detach are idempotent. mode selects
    stepped (freeze the simulation between actions, advance only by
    ticks) or realtime; stepped sessions need ticks, not duration_ms.
    The envelope applies to mode only; acquire/release/detach are
    lifecycle ops.
    """
    inst = _instance(instance)
    if operation in ("acquire", "release", "detach"):
        res = await _offload(_bridge_ok, inst, "control", sub=operation)
        if operation == "acquire":
            # the server owns the lease while the caller holds it; adopt
            # keeps it beating across tool calls longer than the 2 s
            # expiry (image encoding, world loads)
            inst.adopt_lease(res.get("lease", ""), res.get("epoch", 0))
        else:
            inst.clear_lease()
        out = {"operation": operation}
        for k in ("lease", "epoch", "control_rev", "released"):
            if k in res:
                out[k] = res[k]
        return out
    if operation == "mode":
        if mode not in ("stepped", "realtime"):
            raise ValueError("INVALID_CONTEXT: mode must be stepped|realtime")
        async with _cancel_releases(inst):
            res = await _offload(_mutate, inst, "control", sub="mode",
                                 mode=mode, action_id=action_id,
                                 lease=lease, action_seq=action_seq,
                                 epoch=epoch,
                                 world_generation=world_generation,
                                 control_revision=control_revision)
        return {"operation": "mode", "mode": res.get("mode", mode)}
    raise ValueError("INVALID_CONTEXT: operation must be acquire|release|"
                     "detach|mode")


@mcp.tool(annotations=RO_FALSE)
async def quake_release(instance: str, reason: str = "") -> dict:
    """Priority emergency stop: cancel actions, revoke control, neutralize
    MCP input. Idempotent and safe with no lease held."""
    inst = _instance(instance)
    res = await _offload(_bridge_ok, inst, "release")
    inst.clear_lease()
    return {"released": res.get("released", True), "reason": reason}


@mcp.tool(annotations=RO_FALSE_STOP)
async def quake_stop(instance: str) -> dict:
    """Stop an owned child. Refuses attached user-owned processes."""
    inst = _instance(instance)
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
