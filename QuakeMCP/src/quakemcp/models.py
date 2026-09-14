"""QuakeMCP shared types and policy: error codes, leases, observations,
the capability manifest and the console/config allowlists.

Pure validation only — no engine I/O, no sockets. The C bridge mirrors
the wire semantics; this module is what the Python server enforces.
"""
import re
from collections import deque
from dataclasses import dataclass, field
from typing import NotRequired, TypedDict

ERROR_CODES = (
    "NOT_READY",
    "CONTROL_BUSY",
    "STALE_STATE",
    "UNSUPPORTED_CAPABILITY",
    "INVALID_CONTEXT",
    "ACTION_TIMEOUT",
    "ACTION_INTERRUPTED",
    "ENGINE_DISCONNECTED",
    "FRAME_TIMEOUT",
    "FRAME_EXPIRED",
    "RENDER_UNAVAILABLE",
    "RESULT_EXPIRED",
    "POLICY_DENIED",
)

REQUIRED_STATE_KEYS = (
    "instance",
    "epoch",
    "world_gen",
    "control_rev",
    "frame",
    "time",
    "map",
    "mode",
    "pos",
    "health",
    "ammo",
    "ui",
    "loading",
    "dead",
    "intermission",
    "signon",
    "movemessages",
)

# Identity keys are the minimum an Observation must carry; the full
# state group is frozen in Task 6 (state op).
REQUIRED_IDENTITY_KEYS = ("instance", "epoch", "frame")

# Bridge-side state snapshot keys (everything the `state` op returns).
STATE_KEYS = (
    "epoch",
    "world_gen",
    "control_rev",
    "frame",
    "time",
    "map",
    "mode",
    "pos",
    "angles",
    "health",
    "ammo",
    "ui",
    "loading",
    "dead",
    "intermission",
    "signon",
    "movemessages",
)


class StateRequired(TypedDict):
    """Observation fields that survive every telemetry policy."""

    instance: str
    epoch: int
    world_gen: int
    control_rev: int
    frame: int
    time: float
    map: str
    mode: str
    pos: list
    angles: list
    ui: int
    loading: bool
    intermission: bool
    signon: int
    movemessages: int


class StateOut(StateRequired):
    """quake_state output: authoritative HUD telemetry (hud policy)."""

    health: int
    ammo: int
    dead: bool


class ObserveOut(StateRequired):
    """Observation output shared by quake_observe and quake_act.

    Telemetry keys are optional: pixels_only removes them by policy.
    The image group is declared and satisfied for every successful
    observation (Task 7).
    """

    health: NotRequired[int]
    ammo: NotRequired[int]
    dead: NotRequired[bool]
    src_w: int
    src_h: int
    out_w: int
    out_h: int
    viewport: list
    hud_rect: list
    crop: list
    scale: float
    encoding: str
    frame_hash: str
    capture_age_ms: int


class ActOut(ObserveOut):
    """quake_act output: completion facts plus the post-action frame."""

    action_id: str
    completed_ticks: int
    elapsed_ms: int
    interrupted: bool

KNOWN_OPS = frozenset({
    "ping",
    "exec",
    "cvar",
    "act",
    "ui",
    "control",
    "release",
    "state",
    "observe",
    "key",
})

MAX_LINE_BYTES = 65536


# ---- console / config policy (moved from server.py, Task 4) -----------------
#
# Raw console scripting stays disabled: quake_console takes a registered
# command plus validated argument tokens, never a free-form string. Each
# entry declares its arity, validator and class once; a validator is a
# pure function raising ValueError with the frozen error code. Gameplay
# commands require a ready world (except pause 0, which resumes one).

_MAP_RE = re.compile(r"^[A-Za-z0-9_*-]{1,64}$")
_SLOT_RE = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
_GIVE_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")


def _validate_skill(value):
    if value not in ("0", "1", "2", "3"):
        raise ValueError("INVALID_CONTEXT: skill must be 0..3")
    return value


def _validate_impulse(value):
    if not value.isdigit() or not 0 <= int(value) <= 255:
        raise ValueError("INVALID_CONTEXT: impulse must be 0..255")
    return value


def _validate_pause(value):
    if value not in ("0", "1"):
        raise ValueError("INVALID_CONTEXT: pause must be 0 or 1")
    return value


def _validate_give(value):
    if not _GIVE_RE.match(value):
        raise ValueError("POLICY_DENIED: bad give item %r" % (value,))
    return value


def validate_map_id(value):
    if not _MAP_RE.match(value) or ".." in value:
        raise ValueError("POLICY_DENIED: bad map id %r" % (value,))
    return value


def validate_save_slot(value):
    if not _SLOT_RE.match(value):
        raise ValueError("POLICY_DENIED: bad save slot %r" % (value,))
    return value


# command -> (arg count, validator or None, class)
CONSOLE_COMMANDS = {
    "status": (0, None, "client"),
    "version": (0, None, "client"),
    "skill": (1, _validate_skill, "gameplay"),
    "god": (0, None, "gameplay"),
    "noclip": (0, None, "gameplay"),
    "give": (1, _validate_give, "gameplay"),
    "impulse": (1, _validate_impulse, "gameplay"),
    "kill": (0, None, "gameplay"),
    "pause": (1, _validate_pause, "gameplay"),
    "map": (1, validate_map_id, "client"),
    "restart": (0, None, "client"),
    "changelevel": (1, validate_map_id, "client"),
    "quit": (0, None, "client"),
    "screenshot": (0, None, "client"),
    "toggleconsole": (0, None, "client"),
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

# Static capability manifest: what this server and bridge actually
# support. `tools` mirrors the registered tool surface; keep it in step
# with server.py. `respawn` is deliberately absent: its semantics are
# unverified (UNSUPPORTED_CAPABILITY).
CAPABILITIES = {
    "tools": [
        "quake_act", "quake_attach", "quake_config", "quake_console",
        "quake_control", "quake_game", "quake_observe", "quake_release",
        "quake_start", "quake_state", "quake_status", "quake_stop",
        "quake_ui"],
    "actions": {"axes": ["forward", "strafe", "vertical"],
                "run": True, "attack": True, "jump": ["none", "tap", "hold"],
                "weapons": list(range(1, 9))},
    "ui": {"contexts": ["game", "console", "message", "menu"]},
    "settings": sorted(CONFIG_CVARS),
    "frames": {"formats": ["png", "jpeg"], "longest_edge": 1280,
               "crop": True},
    "modes": ["stepped", "realtime"],
    "telemetry": ["hud", "pixels_only"],
    "console": sorted(CONSOLE_COMMANDS),
    "game": ["new_game", "restart", "load_map", "save", "load",
             "list_maps", "list_saves"],
}


def gameplay_ready(state):
    """A loaded local world that is past sign-on and accepting input.

    Mirrors the bridge's act gate minus the engine's paused flag and
    health check: those live in MCP_GameplayReady (q_mcp.c). The fork
    dumps its first two movement messages, so movemessages > 2.
    """
    return bool(state.get("map")) and state.get("signon") == 4 \
        and state.get("movemessages", 0) > 2 \
        and not state.get("dead") and not state.get("intermission")


class QuakeMCPError(Exception):
    """Tool-level failure: carries a frozen error code."""

    def __init__(self, code, detail=""):
        if code not in ERROR_CODES:
            raise ValueError("unknown error code: %r" % (code,))
        super().__init__("%s: %s" % (code, detail))
        self.code = code
        self.detail = detail


class EngineDisconnected(QuakeMCPError):
    def __init__(self, detail=""):
        super().__init__("ENGINE_DISCONNECTED", detail)


@dataclass(frozen=True)
class Lease:
    id: str
    seq: int
    epoch: int


@dataclass
class Observation:
    """Structured observation payload (Task 6 froze the state group).

    Identity and state groups are required at construction; the timing,
    image and telemetry groups are validated for presence of required
    keys as Tasks 7 freezes them.
    """

    identity: dict = field(default_factory=dict)
    timing: dict = field(default_factory=dict)
    image: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)
    telemetry: dict = field(default_factory=dict)

    def __post_init__(self):
        missing = [k for k in REQUIRED_IDENTITY_KEYS
                   if k not in self.identity]
        if missing:
            raise ValueError("identity missing keys: %s" % (missing,))
        missing = [k for k in REQUIRED_STATE_KEYS if k not in self.state]
        if missing:
            raise ValueError("state missing keys: %s" % (missing,))


@dataclass(frozen=True)
class LedgerEntry:
    lease: str
    action_id: str
    epoch: int
    payload_hash: int
    receipt: str


class Ledger:
    """Reference model of the bridge's receipt ring (Task 9).

    The engine's C copy is authoritative; this model pins the semantics
    the contract tests exercise. A repeated (lease, action_id, epoch) with
    the same argument hash returns its recorded receipt; the same id with
    different arguments is a conflict, never a second execution. Acts
    without an action_id carry no identity and are never deduplicated.
    Lookup never consults the sequence or lease liveness, so a retry still
    finds its receipt after the lease or world moved on. Once a consumed
    sequence falls out of the ring, the original result can no longer be
    returned: the call is expired, not re-executed.
    """

    CAPACITY = 64

    def __init__(self, capacity=CAPACITY):
        self.capacity = capacity
        self._entries = deque(maxlen=capacity)
        self.seq = 0

    def check(self, lease, epoch, action_id, payload_hash):
        """-> ("duplicate", receipt) | ("conflict", None) | ("new", None)."""
        if not action_id:
            return ("new", None)
        for e in self._entries:
            if (e.lease, e.action_id, e.epoch) == (lease, action_id, epoch):
                if e.payload_hash == payload_hash:
                    return ("duplicate", e.receipt)
                return ("conflict", None)
        return ("new", None)

    def record(self, lease, epoch, action_id, payload_hash, receipt):
        if not action_id:
            return
        self._entries.append(
            LedgerEntry(lease, action_id, epoch, payload_hash, receipt))

    def expired(self, seq):
        return seq <= self.seq

    def consume(self, seq):
        self.seq = max(self.seq, seq)


def validate_line(obj):
    """Validate a decoded control-protocol object.

    Returns the object unchanged. Raises ValueError on unknown op,
    missing auth, or oversize payload.
    """
    if not isinstance(obj, dict):
        raise ValueError("line must be a JSON object")
    op = obj.get("op")
    if op not in KNOWN_OPS:
        raise ValueError("unknown op: %r" % (op,))
    if not obj.get("auth"):
        raise ValueError("missing auth")
    raw_len = len(str(obj))
    if raw_len > MAX_LINE_BYTES:
        raise ValueError("payload over %d bytes" % MAX_LINE_BYTES)
    return obj
