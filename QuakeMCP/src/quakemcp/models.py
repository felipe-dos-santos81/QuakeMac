"""QuakeMCP shared types: error codes, leases, observations.

Pure validation only — no engine I/O, no sockets. The C bridge mirrors
the wire semantics; this module is what the Python server enforces.
"""
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
