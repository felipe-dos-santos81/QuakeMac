"""QuakeMCP shared types: error codes, leases, observations.

Pure validation only — no engine I/O, no sockets. The C bridge mirrors
the wire semantics; this module is what the Python server enforces.
"""
from dataclasses import dataclass, field

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
